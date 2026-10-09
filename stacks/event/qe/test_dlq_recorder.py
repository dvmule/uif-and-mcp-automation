import gzip
import json
import os
import sys
from unittest.mock import MagicMock

from botocore.exceptions import ClientError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("DELIVERY_METRICS_TABLE", "uif-event-dev-delivery-metrics")
os.environ.setdefault("PUBLISH_METRICS_TABLE", "uif-event-dev-publish-metrics")
os.environ.setdefault("SUBSCRIPTION_CONFIGS_TABLE", "uif-event-dev-subscription-configs")

import dlq_recorder as app  # noqa: E402

DELIVERY_TABLE = os.environ["DELIVERY_METRICS_TABLE"]
PUBLISH_TABLE = os.environ["PUBLISH_METRICS_TABLE"]
SUBS_TABLE = os.environ["SUBSCRIPTION_CONFIGS_TABLE"]

EVENT_ID = "a1b2c3d4-5e6f-4a7b-8c9d-0e1f2a3b4c5d"
RULE_ARN = ("arn:aws:events:eu-west-1:283556840805:rule/uif-event-dev-product/"
            "uif-event-dev-on-product-product-updated-v1-abc")
TARGET_ARN = "arn:aws:sqs:eu-west-1:283556840805:uif-event-dev-abc-product-product-updated-v1"
PUBLISH_ID = "product#jr_gold1#A101703297180#updated#v1"
STORED_PAYLOAD = gzip.compress(json.dumps({"specversion": "1.0", "id": "env-1"}).encode())

ENVELOPE = {"specversion": "1.0", "id": "env-1",
            "subject": "product/A101703297180", "data": {"productId": "A101703297180"}}


def _sqs_record(message_id="m-1", attrs=None, body=None):
    """An SQS record carrying an EventBridge dead-lettered delivery."""
    message_attrs = {
        "ERROR_CODE": {"stringValue": "SQS_QUEUE_ACCESS_DENIED"},
        "ERROR_MESSAGE": {"stringValue": "Access to the resource is denied."},
        "RULE_ARN": {"stringValue": RULE_ARN},
        "TARGET_ARN": {"stringValue": TARGET_ARN},
        "EXHAUSTED_RETRY_CONDITION": {"stringValue": "MaximumRetryAttempts"},
    }
    if attrs is not None:
        message_attrs = attrs
    return {
        "messageId": message_id,
        "messageAttributes": message_attrs,
        "body": json.dumps(body if body is not None else {
            "version": "0", "id": EVENT_ID,
            "detail-type": "product.updated.v1",
            "source": "urn:pearson:svc:product-change-pipeline",
            "time": "2026-09-18T06:03:12Z",
            "detail": ENVELOPE,
        }),
    }


def _mock_dynamo(monkeypatch, *, delivery_exists=False, lineage=True,
                 subscription=True, payload=True):
    ddb = MagicMock()

    def query(**kwargs):
        table, index = kwargs["TableName"], kwargs.get("IndexName")
        if table == DELIVERY_TABLE and index == "gsi_event_id":
            return {"Items": [{"event_id": {"S": EVENT_ID}}]} if delivery_exists else {"Items": []}
        if table == PUBLISH_TABLE and index == "gsi_event_id":
            if not lineage:
                return {"Items": []}
            return {"Items": [{
                "publish_id": {"S": PUBLISH_ID},
                "domain": {"S": "product"},
                "gold_run_id": {"S": "jr_gold1"},
                "event_run_id": {"S": "jr_evt1"},
                "primary_key": {"S": "A101703297180"},
                "operation": {"S": "updated"},
                "version": {"S": "v1"},
            }]}
        if table == SUBS_TABLE:
            if not subscription:
                return {"Items": []}
            return {"Items": [{"customer_id": {"S": "abc"}}]}
        raise AssertionError(f"unexpected query {table}/{index}")

    def get_item(**kwargs):
        assert kwargs["TableName"] == PUBLISH_TABLE
        if not payload:
            return {}
        return {"Item": {"publish_id": {"S": PUBLISH_ID},
                         "payload": {"B": STORED_PAYLOAD}}}

    ddb.query.side_effect = query
    ddb.get_item.side_effect = get_item
    monkeypatch.setattr(app, "dynamodb", ddb)
    return ddb


def test_failed_delivery_recorded_with_lineage_and_payload(monkeypatch):
    ddb = _mock_dynamo(monkeypatch)

    result = app.lambda_handler({"Records": [_sqs_record()]}, None)

    assert result == {"batchItemFailures": []}
    item = ddb.put_item.call_args.kwargs["Item"]
    assert ddb.put_item.call_args.kwargs["TableName"] == DELIVERY_TABLE
    assert ddb.put_item.call_args.kwargs["ConditionExpression"] == \
        "attribute_not_exists(domain_customer)"
    assert item["domain_customer"] == {"S": "product#abc"}
    # The correlator is the ENVELOPE id (detail.id) in both dead-letter
    # shapes - the wrapper's bus-assigned id never survives InputPath.
    assert item["delivery_id"] == {"S": "2026-09-18T06:03:12Z#env-1"}
    assert item["customer_id"] == {"S": "abc"}
    assert item["status"] == {"S": "FAILED"}
    assert item["reason"] == {"S": "SQS_QUEUE_ACCESS_DENIED"}
    assert item["exhausted_by"] == {"S": "MaximumRetryAttempts"}
    assert item["target_arn"] == {"S": TARGET_ARN}
    assert item["rule_arn"] == {"S": RULE_ARN}
    assert item["primary_key"] == {"S": "A101703297180"}
    assert item["operation"] == {"S": "updated"}
    assert item["detail_type"] == {"S": "product.updated.v1"}
    assert item["publish_id"] == {"S": PUBLISH_ID}
    assert item["gold_run_id"] == {"S": "jr_gold1"}
    assert item["event_run_id"] == {"S": "jr_evt1"}
    assert item["payload"] == {"B": STORED_PAYLOAD}  # Table 3's stored bytes
    assert item["redrive_count"] == {"N": "0"}
    assert item["redriven_by"] == {"NULL": True}
    assert "ttl" not in item  # no TTL while FAILED - never silently expires


def test_bare_envelope_dead_letter_recorded(monkeypatch):
    """EventBridge applies the target's InputPath BEFORE dead-lettering
    (found live 2026-09-21), so the normal DLQ body is the bare CloudEvents
    envelope - no wrapper, no bus-assigned id, no detail-type field. The
    recorder derives everything from the envelope itself."""
    ddb = _mock_dynamo(monkeypatch, lineage=False)
    envelope = {"specversion": "1.0", "id": "env-bare-1",
                "type": "com.pearson.product.product.updated.v1",
                "time": "2026-09-21T13:20:03Z", "channel": "event",
                "subject": "product/A101703297180",
                "data": {"productId": "A101703297180"}}
    record = {"messageId": "m-bare",
              "messageAttributes": {
                  "ERROR_CODE": {"stringValue": "NO_RESOURCE"},
                  "ERROR_MESSAGE": {"stringValue": "Queue gone."},
                  "RULE_ARN": {"stringValue": RULE_ARN},
                  "TARGET_ARN": {"stringValue": TARGET_ARN},
                  "EXHAUSTED_RETRY_CONDITION": {"stringValue": "MaximumRetryAttempts"},
              },
              "body": json.dumps(envelope)}

    result = app.lambda_handler({"Records": [record]}, None)

    assert result == {"batchItemFailures": []}
    item = ddb.put_item.call_args.kwargs["Item"]
    assert item["domain_customer"] == {"S": "product#abc"}   # from the subject
    assert item["event_id"] == {"S": "env-bare-1"}           # the envelope id
    assert item["detail_type"] == {"S": "product.updated.v1"}  # from the type
    assert item["operation"] == {"S": "updated"}
    assert item["primary_key"] == {"S": "A101703297180"}
    assert item["failed_at"] == {"S": "2026-09-21T13:20:03Z"}  # envelope time
    # Degraded payload = the whole envelope, redrive-ready.
    assert json.loads(gzip.decompress(item["payload"]["B"])) == envelope


def test_duplicate_event_id_is_skipped(monkeypatch):
    ddb = _mock_dynamo(monkeypatch, delivery_exists=True)
    result = app.lambda_handler({"Records": [_sqs_record()]}, None)
    assert result == {"batchItemFailures": []}
    ddb.put_item.assert_not_called()


def test_missing_lineage_degrades_to_event_detail_payload(monkeypatch):
    """No Table 3 row (e.g. pre-migration event): the row is still written,
    self-contained, with the DLQ'd event's own detail gzip'd as payload."""
    ddb = _mock_dynamo(monkeypatch, lineage=False)

    app.lambda_handler({"Records": [_sqs_record()]}, None)

    item = ddb.put_item.call_args.kwargs["Item"]
    # Absent lineage keys are OMITTED, never NULL: gold_run_id keys
    # gsi_gold_run and DynamoDB rejects NULL-typed index keys (the whole
    # PutItem fails) - proven live 2026-09-21.
    assert "publish_id" not in item
    assert "gold_run_id" not in item
    assert "event_run_id" not in item
    assert item["primary_key"] == {"S": "A101703297180"}  # from the subject
    assert json.loads(gzip.decompress(item["payload"]["B"])) == ENVELOPE


def test_unknown_subscription_falls_back_to_target_name(monkeypatch):
    """Seeds are PENDING with future rule ARNs, so subscription resolution
    misses today - the customer comes from the managed target's name."""
    ddb = _mock_dynamo(monkeypatch, subscription=False)

    app.lambda_handler({"Records": [_sqs_record()]}, None)

    item = ddb.put_item.call_args.kwargs["Item"]
    assert item["customer_id"] == {"S": "abc"}  # uif-event-dev-ABC-...
    assert item["domain_customer"] == {"S": "product#abc"}


def test_non_delivery_message_logged_and_dropped(monkeypatch):
    """The DLQ is shared with the trigger Lambda's async dead letters - no
    RULE_ARN means it is not a subscriber delivery failure. Logged, dropped,
    never written to Table 4."""
    ddb = _mock_dynamo(monkeypatch)
    record = _sqs_record(attrs={}, body={"run_id": "jr_x", "domain": "product"})

    result = app.lambda_handler({"Records": [record]}, None)

    assert result == {"batchItemFailures": []}  # deleted from the queue
    ddb.put_item.assert_not_called()
    ddb.query.assert_not_called()


def test_conditional_check_failure_treated_as_duplicate(monkeypatch):
    ddb = _mock_dynamo(monkeypatch)
    ddb.put_item.side_effect = ClientError(
        {"Error": {"Code": "ConditionalCheckFailedException"}}, "PutItem")

    result = app.lambda_handler({"Records": [_sqs_record()]}, None)

    assert result == {"batchItemFailures": []}


def test_failing_record_returned_to_queue_others_succeed(monkeypatch):
    """Partial-batch response: only the record that errored is redelivered."""
    ddb = _mock_dynamo(monkeypatch)
    ddb.put_item.side_effect = [RuntimeError("throttled"), None]

    result = app.lambda_handler({"Records": [
        _sqs_record(message_id="m-bad"),
        _sqs_record(message_id="m-good"),
    ]}, None)

    assert result == {"batchItemFailures": [{"itemIdentifier": "m-bad"}]}
    assert ddb.put_item.call_count == 2


def test_string_detail_and_operation_from_detail_type(monkeypatch):
    """detail may arrive as a JSON string; operation derives from the
    detail-type when lineage is absent."""
    ddb = _mock_dynamo(monkeypatch, lineage=False)
    body = {
        "version": "0", "id": EVENT_ID,
        "detail-type": "product.created.v2",
        "time": "2026-09-18T07:00:00Z",
        "detail": json.dumps(ENVELOPE),
    }

    app.lambda_handler({"Records": [_sqs_record(body=body)]}, None)

    item = ddb.put_item.call_args.kwargs["Item"]
    assert item["operation"] == {"S": "created"}
    assert item["detail_type"] == {"S": "product.created.v2"}
    assert item["domain"] == {"S": "product"}
