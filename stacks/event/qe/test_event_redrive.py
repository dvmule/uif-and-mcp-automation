import gzip
import json
import os
import sys
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("DELIVERY_METRICS_TABLE", "uif-event-dev-delivery-metrics")
os.environ.setdefault("SUBSCRIPTION_CONFIGS_TABLE", "uif-event-dev-subscription-configs")
os.environ.setdefault("PIPELINE_METRICS_TABLE", "uif-event-dev-pipeline-metrics")

import event_redrive as app  # noqa: E402

DELIVERY_TABLE = os.environ["DELIVERY_METRICS_TABLE"]
SUBS_TABLE = os.environ["SUBSCRIPTION_CONFIGS_TABLE"]
PIPELINE_TABLE = os.environ["PIPELINE_METRICS_TABLE"]

QUEUE_ARN = "arn:aws:sqs:eu-west-1:283556840805:uif-event-dev-pmc-product-product-updated-v1"
QUEUE_URL = "https://sqs.eu-west-1.amazonaws.com/283556840805/uif-event-dev-pmc-product-product-updated-v1"

ENVELOPE = {"specversion": "1.0", "id": "orig-id", "channel": "event",
            "type": "com.pearson.product.product.updated.v1",
            "subject": "product/A1", "data": {"productId": "A1"}}

TRIGGER = {"domain": "product", "customer_id": "pmc",
           "redriven_by": "ops@pearson.com"}


def _row(delivery_id="2026-09-18T11:19:15Z#ev-1", detail_type="product.updated.v1",
         payload=True):
    row = {
        "domain_customer": {"S": "product#pmc"},
        "delivery_id": {"S": delivery_id},
        "detail_type": {"S": detail_type},
        "status": {"S": "FAILED"},
    }
    if payload:
        row["payload"] = {"B": gzip.compress(json.dumps(ENVELOPE).encode())}
    return row


def _subscription(status="ACTIVE", target_type="SQS", target_arn=QUEUE_ARN):
    return {
        "customer_id": {"S": "pmc"},
        "domain_detail_type": {"S": "product#product.updated.v1"},
        "status": {"S": status},
        "target": {"M": {
            "target_type": {"S": target_type},
            "target_arn": {"S": target_arn},
        }},
    }


@pytest.fixture()
def mocks(monkeypatch):
    ddb, q = MagicMock(), MagicMock()
    ddb.query.return_value = {"Items": [_row()]}
    ddb.get_item.return_value = {"Item": _subscription()}
    q.get_queue_url.return_value = {"QueueUrl": QUEUE_URL}
    monkeypatch.setattr(app, "dynamodb", ddb)
    monkeypatch.setattr(app, "sqs", q)
    return ddb, q


def _update_for(ddb, table=DELIVERY_TABLE):
    return [c.kwargs for c in ddb.update_item.call_args_list
            if c.kwargs["TableName"] == table]


def test_replay_success_settles_row_and_run(mocks):
    ddb, q = mocks

    result = app.lambda_handler(dict(TRIGGER), None)

    assert result["status"] == "SUCCEEDED"
    assert result["replayed"] == 1 and result["failed"] == 0
    assert result["redriveRunId"].startswith("rd_")

    # The message: the STORED envelope, fresh id, straight to the queue.
    body = json.loads(q.send_message.call_args.kwargs["MessageBody"])
    assert body["id"] != "orig-id" and body["data"] == ENVELOPE["data"]
    assert body["channel"] == "event"  # untouched stored bytes otherwise
    assert q.send_message.call_args.kwargs["QueueUrl"] == QUEUE_URL
    q.get_queue_url.assert_called_once_with(
        QueueName="uif-event-dev-pmc-product-product-updated-v1",
        QueueOwnerAWSAccountId="283556840805")

    # Rule 2: REPLAYED, new event_id, ttl set, optimistic on FAILED.
    upd = _update_for(ddb)[0]
    assert upd["ConditionExpression"] == "#s = :failed"
    v = upd["ExpressionAttributeValues"]
    assert v[":replayed"] == {"S": "REPLAYED"}
    assert v[":eid"]["S"] == body["id"]  # ledger follows the new envelope id
    assert ":ttl" in v and "ADD redrive_count :one" in upd["UpdateExpression"]

    # Run row: mode REDRIVE, LAMBDA engine, customer as started_by, no gold_run_id.
    run = ddb.put_item.call_args.kwargs
    assert run["TableName"] == PIPELINE_TABLE
    item = run["Item"]
    assert item["mode"] == {"S": "REDRIVE"}
    assert item["worker_type"] == {"S": "LAMBDA"}
    assert item["started_by"] == {"S": "pmc"}
    assert item["published_row_count"] == {"N": "1"}
    assert "gold_run_id" not in item  # sparse gsi_gold_run never matches


def test_inactive_subscription_fails_rows_without_sending(mocks):
    ddb, q = mocks
    ddb.get_item.return_value = {"Item": _subscription(status="DELETED")}

    result = app.lambda_handler(dict(TRIGGER), None)

    q.send_message.assert_not_called()
    assert result["status"] == "FAILED" and result["failed"] == 1
    upd = _update_for(ddb)[0]
    assert upd["ExpressionAttributeValues"][":why"] == {"S": "SUBSCRIPTION_NOT_ACTIVE"}
    assert ":ttl" not in upd["ExpressionAttributeValues"]  # stays queued
    # Run row records the failure reason.
    assert ddb.put_item.call_args.kwargs["Item"]["reason"] == {"S": "TARGET_REJECTED"}


def test_send_failure_keeps_row_queued(mocks):
    ddb, q = mocks
    q.send_message.side_effect = ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": "no grant"}},
        "SendMessage")

    result = app.lambda_handler(dict(TRIGGER), None)

    assert result["status"] == "FAILED"
    upd = _update_for(ddb)[0]
    assert upd["ExpressionAttributeValues"][":why"] == {"S": "AccessDeniedException"}


def test_partial_failure_mixes_outcomes(mocks):
    ddb, q = mocks
    ddb.query.return_value = {"Items": [_row("t1#e1"), _row("t2#e2")]}
    q.send_message.side_effect = [
        ClientError({"Error": {"Code": "AccessDeniedException", "Message": "x"}},
                    "SendMessage"),
        {},
    ]

    result = app.lambda_handler(dict(TRIGGER), None)

    assert result["status"] == "PARTIAL_FAILURE"
    assert result["replayed"] == 1 and result["failed"] == 1
    # One subscription lookup for the shared detail-type, not one per row.
    ddb.get_item.assert_called_once()


def test_race_lost_row_counts_skipped_not_replayed(mocks):
    """A concurrent redrive settled the row between query and update - the
    message went out, but the OTHER run owns the bookkeeping."""
    ddb, _ = mocks
    ddb.update_item.side_effect = ClientError(
        {"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")

    result = app.lambda_handler(dict(TRIGGER), None)

    assert result["skipped"] == 1
    assert result["replayed"] == 0 and result["failed"] == 0


def test_unsupported_target_type_fails_visibly(mocks):
    ddb, q = mocks
    ddb.get_item.return_value = {"Item": _subscription(target_type="KINESIS")}

    result = app.lambda_handler(dict(TRIGGER), None)

    q.send_message.assert_not_called()
    upd = _update_for(ddb)[0]
    assert upd["ExpressionAttributeValues"][":why"] == {"S": "TARGET_TYPE_UNSUPPORTED"}
    assert result["failed"] == 1


def test_nothing_outstanding_writes_no_run_row(mocks):
    ddb, _ = mocks
    ddb.query.return_value = {"Items": []}

    result = app.lambda_handler(dict(TRIGGER), None)

    assert result["status"] == "NOTHING_TO_REDRIVE"
    ddb.put_item.assert_not_called()
    ddb.update_item.assert_not_called()


def test_detail_type_filter_and_pagination(mocks):
    ddb, _ = mocks
    ddb.query.side_effect = [
        {"Items": [_row("t1#e1")], "LastEvaluatedKey": {"k": {"S": "v"}}},
        {"Items": [_row("t2#e2")]},
    ]

    result = app.lambda_handler({**TRIGGER, "detail_type": "product.updated.v1"},
                                None)

    assert result["outstanding"] == 2
    first = ddb.query.call_args_list[0].kwargs
    assert ":dt" in first["ExpressionAttributeValues"]
    assert "ExclusiveStartKey" in ddb.query.call_args_list[1].kwargs


def test_unattributed_redrive_is_refused(mocks):
    with pytest.raises(ValueError, match="redriven_by"):
        app.lambda_handler({"domain": "product", "customer_id": "pmc"}, None)
