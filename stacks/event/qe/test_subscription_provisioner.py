import json
import os
import sys
from unittest.mock import MagicMock

from botocore.exceptions import ClientError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

os.environ.setdefault("AWS_DEFAULT_REGION", "eu-west-1")
os.environ.setdefault("SUBSCRIPTION_CONFIGS_TABLE", "uif-event-dev-subscription-configs")
os.environ.setdefault("DOMAIN_BUS", "uif-event-dev-product")
os.environ.setdefault("EVENT_STACK_PREFIX", "uif-event-dev")
os.environ.setdefault("ACCOUNT_ID", "283556840805")
os.environ.setdefault("KINESIS_DELIVERY_ROLE_ARN",
                      "arn:aws:iam::283556840805:role/uif-event-dev-events-to-kinesis-role")

import subscription_provisioner as app  # noqa: E402

TABLE = os.environ["SUBSCRIPTION_CONFIGS_TABLE"]
BUS = os.environ["DOMAIN_BUS"]
QUEUE_URL = "https://sqs.eu-west-1.amazonaws.com/283556840805/uif-event-dev-pmc-product-product-created-v1"
QUEUE_ARN = "arn:aws:sqs:eu-west-1:283556840805:uif-event-dev-pmc-product-product-created-v1"
DLQ_ARN = QUEUE_ARN + "-dlq"
RULE_NAME = "uif-event-dev-on-product-product-created-v1-pmc"
RULE_ARN = f"arn:aws:events:eu-west-1:283556840805:rule/{BUS}/{RULE_NAME}"


def _row(status="PENDING", customer="pmc", detail_type="product.created.v1",
         target_type="SQS", principal="arn:aws:iam::111122223333:role/pmc-event-consumer"):
    return {
        "customer_id": {"S": customer},
        "domain_detail_type": {"S": f"product#{detail_type}"},
        "subscription_id": {"S": "8f3c1e2a-4b7d-4c19-9a55-2e6f0d1b7c34"},
        "policy_sid": {"S": "uif8f3c1e2a4b7d4c199a552e6f0d1b7c34"},
        "domain": {"S": "product"},
        "operation": {"S": detail_type.split(".")[1]},
        "version": {"S": "v1"},
        "detail_type": {"S": detail_type},
        "target": {"M": {
            "target_type": {"S": target_type},
            "target_arn": {"S": "arn:aws:sqs:eu-west-1:444455556666:example"},
            "target_config": {"M": {
                "consumer_principal_arn": {"S": principal},
                "retry_policy": {"M": {"max_attempts": {"N": "3"},
                                       "max_age_seconds": {"N": "900"}}},
            }},
        }},
        "target_arn": {"S": "arn:aws:sqs:eu-west-1:444455556666:example"},
        "status": {"S": status},
        "record_version": {"N": "1"},
    }


def _stream_event(new, old=None, name="MODIFY", event_id="ev-1"):
    return {"Records": [{
        "eventID": event_id, "eventName": name,
        "dynamodb": {"NewImage": new, **({"OldImage": old} if old else {})},
    }]}


def _mock_aws(monkeypatch, *, existing_policy=None, principal_rejected=False,
              pipeline_config_bus=None):
    ddb, q, ev = MagicMock(), MagicMock(), MagicMock()
    # Bus resolution reads the domain's pipeline-configs row; default: no
    # row, so the conventional bus applies (memo cleared per test).
    app._bus_cache.clear()
    if pipeline_config_bus:
        ddb.get_item.return_value = {"Item": {"processor": {"M": {
            "arguments": {"M": {"event_bus_name": {"S": pipeline_config_bus}}},
        }}}}
    else:
        ddb.get_item.return_value = {}

    def create_queue(QueueName, Attributes):
        return {"QueueUrl": f"https://sqs.eu-west-1.amazonaws.com/283556840805/{QueueName}"}

    def get_queue_attributes(QueueUrl, AttributeNames):
        name = QueueUrl.rsplit("/", 1)[-1]
        attrs = {"QueueArn": f"arn:aws:sqs:eu-west-1:283556840805:{name}"}
        if "Policy" in AttributeNames:
            attrs = {"Policy": json.dumps(existing_policy)} if existing_policy else {}
            return {"Attributes": attrs}
        return {"Attributes": attrs}

    calls = {"policy_writes": 0}

    def set_queue_attributes(QueueUrl, Attributes):
        calls["policy_writes"] += 1
        if principal_rejected and calls["policy_writes"] == 1 and \
                "111122223333" in Attributes.get("Policy", ""):
            raise ClientError({"Error": {"Code": "InvalidAttributeValue",
                                         "Message": "Invalid principal"}},
                              "SetQueueAttributes")
        return {}

    q.create_queue.side_effect = create_queue
    q.get_queue_attributes.side_effect = get_queue_attributes
    q.set_queue_attributes.side_effect = set_queue_attributes
    kin = MagicMock()
    kin.describe_stream_summary.side_effect = lambda StreamName: {
        "StreamDescriptionSummary": {
            "StreamStatus": "ACTIVE",
            "StreamARN": f"arn:aws:kinesis:eu-west-1:283556840805:stream/{StreamName}",
        }}
    monkeypatch.setattr(app, "dynamodb", ddb)
    monkeypatch.setattr(app, "sqs", q)
    monkeypatch.setattr(app, "events", ev)
    monkeypatch.setattr(app, "kinesis", kin)
    return ddb, q, ev, kin


def test_pending_row_provisioned_to_active(monkeypatch):
    ddb, q, ev, _ = _mock_aws(monkeypatch)

    result = app.lambda_handler(_stream_event(_row(), name="INSERT"), None)

    assert result == {"batchItemFailures": []}
    # Queues: DLQ first, then the main queue with RedrivePolicy -> DLQ.
    names = [c.kwargs["QueueName"] for c in q.create_queue.call_args_list]
    assert names == ["uif-event-dev-pmc-product-product-created-v1-dlq",
                     "uif-event-dev-pmc-product-product-created-v1"]
    redrive = json.loads(q.create_queue.call_args_list[1]
                         .kwargs["Attributes"]["RedrivePolicy"])
    assert redrive["deadLetterTargetArn"] == DLQ_ARN

    # Policy: delivery Sid conditioned on the rule ARN + consume Sid; no
    # redrive Sid while REDRIVE_ROLE_ARN is unset.
    policy = json.loads(q.set_queue_attributes.call_args.kwargs["Attributes"]["Policy"])
    sids = {s["Sid"]: s for s in policy["Statement"]}
    assert set(sids) == {"uif8f3c1e2a4b7d4c199a552e6f0d1b7c34",
                         "uif8f3c1e2a4b7d4c199a552e6f0d1b7c34c"}
    delivery = sids["uif8f3c1e2a4b7d4c199a552e6f0d1b7c34"]
    assert delivery["Condition"] == {"ArnEquals": {"aws:SourceArn": RULE_ARN}}
    consume = sids["uif8f3c1e2a4b7d4c199a552e6f0d1b7c34c"]
    assert "sqs:SendMessage" not in consume["Action"]  # never SendMessage

    # Rule + target on the domain bus.
    ev.put_rule.assert_called_once_with(
        Name=RULE_NAME, EventBusName=BUS,
        EventPattern=json.dumps({"detail-type": ["product.created.v1"],
                                 "detail": {"channel": ["event"]}}),
        State="ENABLED")
    target = ev.put_targets.call_args.kwargs["Targets"][0]
    assert target["Id"] == "8f3c1e2a-4b7d-4c19-9a55-2e6f0d1b7c34"
    assert target["Arn"] == QUEUE_ARN
    # Exhausted deliveries dead-letter to the DOMAIN DLQ (the recorder's
    # queue) - NOT the per-subscription DLQ, which is the subscription
    # queue's own RedrivePolicy target (fixed after the 2026-09-21 live test
    # showed failed deliveries were invisible).
    assert target["DeadLetterConfig"] == {
        "Arn": "arn:aws:sqs:eu-west-1:283556840805:uif-event-dev-product-dlq"}
    assert target["RetryPolicy"] == {"MaximumRetryAttempts": 3,
                                     "MaximumEventAgeInSeconds": 900}
    # Consumers get ONLY the CloudEvents envelope - EventBridge's wrapper
    # (version/id/account/region/...) is stripped at delivery.
    assert target["InputPath"] == "$.detail"

    # ACTIVE with the REAL ARNs, optimistic-locked, flat + nested together.
    upd = ddb.update_item.call_args.kwargs
    assert upd["ConditionExpression"] == "record_version = :expected"
    v = upd["ExpressionAttributeValues"]
    assert v[":expected"] == {"N": "1"}
    assert v[":active"] == {"S": "ACTIVE"}
    assert v[":rule"] == {"S": RULE_ARN}
    assert v[":qarn"] == {"S": QUEUE_ARN}
    assert "#t.target_arn = :qarn" in upd["UpdateExpression"]


def test_invalid_consumer_principal_degrades_not_fails(monkeypatch):
    """Documented deviation: a rejected principal drops the consume Sid and
    the row still goes ACTIVE with status_reason CONSUMER_PRINCIPAL_INVALID."""
    ddb, q, ev, _ = _mock_aws(monkeypatch, principal_rejected=True)

    app.lambda_handler(_stream_event(_row(), name="INSERT"), None)

    assert q.set_queue_attributes.call_count == 2  # full write, then retry
    retry_policy = json.loads(
        q.set_queue_attributes.call_args.kwargs["Attributes"]["Policy"])
    assert all(not s["Sid"].endswith("c") for s in retry_policy["Statement"])
    v = ddb.update_item.call_args.kwargs["ExpressionAttributeValues"]
    assert v[":active"] == {"S": "ACTIVE"}
    assert v[":reason"] == {"S": "CONSUMER_PRINCIPAL_INVALID"}


def test_policy_merge_keeps_foreign_statements(monkeypatch):
    """Only Sids with this subscription's prefix are replaced - the
    customer's own statements and other subscriptions' grants survive."""
    existing = {"Version": "2012-10-17", "Statement": [
        {"Sid": "CustomerOwn", "Effect": "Allow"},
        {"Sid": "uif8f3c1e2a4b7d4c199a552e6f0d1b7c34c", "Effect": "Allow"},
        {"Sid": "uifOTHERSUBSCRIPTION", "Effect": "Allow"},
    ]}
    _, q, _, _ = _mock_aws(monkeypatch, existing_policy=existing)

    app.lambda_handler(_stream_event(_row(), name="INSERT"), None)

    policy = json.loads(q.set_queue_attributes.call_args.kwargs["Attributes"]["Policy"])
    sids = [s["Sid"] for s in policy["Statement"]]
    assert "CustomerOwn" in sids
    assert "uifOTHERSUBSCRIPTION" in sids
    assert sids.count("uif8f3c1e2a4b7d4c199a552e6f0d1b7c34c") == 1  # replaced, not duplicated


def test_unsupported_target_type_fails_row(monkeypatch):
    ddb, q, ev, _ = _mock_aws(monkeypatch)

    app.lambda_handler(_stream_event(_row(target_type="API"), name="INSERT"), None)

    q.create_queue.assert_not_called()
    ev.put_rule.assert_not_called()
    v = ddb.update_item.call_args.kwargs["ExpressionAttributeValues"]
    assert v[":failed"] == {"S": "FAILED"}
    assert v[":reason"] == {"S": "TARGET_TYPE_UNSUPPORTED"}


def test_name_too_long_fails_before_aws(monkeypatch):
    ddb, q, ev, _ = _mock_aws(monkeypatch)
    row = _row(customer="a-very-long-customer-code-that-overflows-limits",
               detail_type="product.created.v1")

    app.lambda_handler(_stream_event(row, name="INSERT"), None)

    q.create_queue.assert_not_called()
    v = ddb.update_item.call_args.kwargs["ExpressionAttributeValues"]
    assert v[":reason"] == {"S": "NAME_TOO_LONG"}


def test_rule_quota_fails_row(monkeypatch):
    ddb, _, ev, _ = _mock_aws(monkeypatch)
    ev.put_rule.side_effect = ClientError(
        {"Error": {"Code": "LimitExceededException"}}, "PutRule")

    app.lambda_handler(_stream_event(_row(), name="INSERT"), None)

    v = ddb.update_item.call_args.kwargs["ExpressionAttributeValues"]
    assert v[":reason"] == {"S": "RULE_QUOTA"}


def test_deleting_row_torn_down_to_deleted(monkeypatch):
    ddb, q, ev, _ = _mock_aws(monkeypatch, existing_policy={
        "Version": "2012-10-17", "Statement": [
            {"Sid": "uif8f3c1e2a4b7d4c199a552e6f0d1b7c34", "Effect": "Allow"},
            {"Sid": "CustomerOwn", "Effect": "Allow"},
        ]})

    app.lambda_handler(_stream_event(_row(status="DELETING")), None)

    ev.remove_targets.assert_called_once_with(
        Rule=RULE_NAME, EventBusName=BUS,
        Ids=["8f3c1e2a-4b7d-4c19-9a55-2e6f0d1b7c34"])
    ev.delete_rule.assert_called_once_with(Name=RULE_NAME, EventBusName=BUS)
    # Our Sids stripped, the customer's kept; both queues tagged for sweep.
    policy = json.loads(
        q.set_queue_attributes.call_args_list[0].kwargs["Attributes"]["Policy"])
    assert [s["Sid"] for s in policy["Statement"]] == ["CustomerOwn"]
    assert q.tag_queue.call_count == 2
    assert "delete_after" in q.tag_queue.call_args.kwargs["Tags"]
    v = ddb.update_item.call_args.kwargs["ExpressionAttributeValues"]
    assert v[":deleted"] == {"S": "DELETED"}


def test_suspend_disables_and_resume_enables_rule(monkeypatch):
    _, _, ev, _ = _mock_aws(monkeypatch)

    app.lambda_handler(_stream_event(_row(status="SUSPENDED"),
                                     old=_row(status="ACTIVE")), None)
    ev.disable_rule.assert_called_once_with(Name=RULE_NAME, EventBusName=BUS)

    app.lambda_handler(_stream_event(_row(status="ACTIVE"),
                                     old=_row(status="SUSPENDED")), None)
    ev.enable_rule.assert_called_once_with(Name=RULE_NAME, EventBusName=BUS)


def test_active_without_transition_is_ignored(monkeypatch):
    ddb, q, ev, _ = _mock_aws(monkeypatch)
    app.lambda_handler(_stream_event(_row(status="ACTIVE"),
                                     old=_row(status="ACTIVE")), None)
    ev.enable_rule.assert_not_called()
    q.create_queue.assert_not_called()
    ddb.update_item.assert_not_called()


def test_concurrent_edit_skips_activation(monkeypatch):
    ddb, _, _, _ = _mock_aws(monkeypatch)
    ddb.update_item.side_effect = ClientError(
        {"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")

    result = app.lambda_handler(_stream_event(_row(), name="INSERT"), None)

    assert result == {"batchItemFailures": []}  # stale view skipped, no retry loop


def test_failing_record_reported_others_succeed(monkeypatch):
    ddb, q, ev, _ = _mock_aws(monkeypatch)
    ev.put_targets.side_effect = [RuntimeError("boom"), {}]
    event = {"Records": [
        {"eventID": "ev-bad", "eventName": "INSERT",
         "dynamodb": {"NewImage": _row(customer="pmc")}},
        {"eventID": "ev-good", "eventName": "INSERT",
         "dynamodb": {"NewImage": _row(customer="platinum")}},
    ]}

    result = app.lambda_handler(event, None)

    assert result == {"batchItemFailures": [{"itemIdentifier": "ev-bad"}]}


def test_failing_record_identified_by_stream_sequence_number(monkeypatch):
    """A DynamoDB-stream partial-batch response must name the record by its
    SequenceNumber; with the eventID Lambda logged "Sequence number in
    function response is invalid" and retried the WHOLE batch (live
    2026-10-01 - with infinite retries, the shard stalled)."""
    ddb, q, ev, _ = _mock_aws(monkeypatch)
    ev.put_targets.side_effect = RuntimeError("boom")
    event = {"Records": [{
        "eventID": "d043f5d24130dc236e9b75f06ffc6763", "eventName": "INSERT",
        "dynamodb": {"SequenceNumber": "4950000000012345678901",
                     "NewImage": _row(customer="pmc")},
    }]}

    result = app.lambda_handler(event, None)

    assert result == {"batchItemFailures": [
        {"itemIdentifier": "4950000000012345678901"}]}


def test_teardown_removes_policy_when_only_our_sids_remain(monkeypatch):
    """A managed queue only the provisioner ever wrote to: stripping our Sids
    leaves no statements, and SQS rejects {"Statement": []} with
    InvalidAttributeValue (live 2026-10-01, the price bus flip's pmc
    teardowns). The policy is REMOVED (empty attribute) instead."""
    ddb, q, ev, _ = _mock_aws(monkeypatch, existing_policy={
        "Version": "2012-10-17", "Statement": [
            {"Sid": "uif8f3c1e2a4b7d4c199a552e6f0d1b7c34", "Effect": "Allow"},
            {"Sid": "uif8f3c1e2a4b7d4c199a552e6f0d1b7c34r", "Effect": "Allow"},
            {"Sid": "uif8f3c1e2a4b7d4c199a552e6f0d1b7c34c", "Effect": "Allow"},
        ]})

    result = app.lambda_handler(_stream_event(_row(status="DELETING")), None)

    assert result == {"batchItemFailures": []}
    for call in q.set_queue_attributes.call_args_list:
        assert call.kwargs["Attributes"]["Policy"] == ""
    v = ddb.update_item.call_args.kwargs["ExpressionAttributeValues"]
    assert v[":deleted"] == {"S": "DELETED"}


def test_sweep_reconciles_pending_and_deleting(monkeypatch):
    ddb, q, ev, _ = _mock_aws(monkeypatch)
    ddb.scan.return_value = {"Items": [_row(), _row(status="DELETING",
                                                    customer="platinum")]}

    result = app.lambda_handler({}, None)

    assert result == {"handled": 2}
    scan = ddb.scan.call_args.kwargs
    assert scan["ExpressionAttributeValues"][":p"] == {"S": "PENDING"}
    assert ev.put_rule.called      # pmc provisioned
    assert ev.delete_rule.called   # platinum torn down


def test_bus_resolved_from_pipeline_configs(monkeypatch):
    """A domain riding another domain's bus (price on the product bus while
    its own is pending, 2026-09-25): the rule, its ARN and the delivery DLQ
    all follow the CONFIGURED bus, not the naming convention."""
    ddb, q, ev, _ = _mock_aws(monkeypatch,
                              pipeline_config_bus="uif-event-dev-product")
    row = _row(customer="pmc", detail_type="price.updated.v1")
    row["domain"] = {"S": "price"}
    row["domain_detail_type"] = {"S": "price#price.updated.v1"}

    app.lambda_handler(_stream_event(row, name="INSERT"), None)

    # The config row it read: price's, latest.
    key = ddb.get_item.call_args.kwargs["Key"]
    assert key == {"pipelineid": {"S": "event-dev-price"},
                   "version": {"S": "latest"}}
    # Rule on the CONFIGURED (product) bus; names still carry the true domain.
    put_rule = ev.put_rule.call_args.kwargs
    assert put_rule["EventBusName"] == "uif-event-dev-product"
    assert put_rule["Name"] == "uif-event-dev-on-price-price-updated-v1-pmc"
    # Delivery DLQ follows the bus's OWNER (product's recorder queue).
    target = ev.put_targets.call_args.kwargs["Targets"][0]
    assert target["DeadLetterConfig"] == {
        "Arn": "arn:aws:sqs:eu-west-1:283556840805:uif-event-dev-product-dlq"}
    # Activation records the rule ARN on the configured bus.
    v = ddb.update_item.call_args.kwargs["ExpressionAttributeValues"]
    assert v[":rule"] == {"S": ("arn:aws:events:eu-west-1:283556840805:rule/"
                                "uif-event-dev-product/"
                                "uif-event-dev-on-price-price-updated-v1-pmc")}


def test_bus_lookup_failure_falls_back_to_convention(monkeypatch):
    """An unreadable pipeline-configs table (e.g. the IAM grant not yet
    deployed) never blocks provisioning - the conventional bus applies."""
    ddb, _, ev, _ = _mock_aws(monkeypatch)
    ddb.get_item.side_effect = ClientError(
        {"Error": {"Code": "AccessDeniedException"}}, "GetItem")

    app.lambda_handler(_stream_event(_row(), name="INSERT"), None)

    assert ev.put_rule.call_args.kwargs["EventBusName"] == BUS


def test_teardown_uses_bus_from_stored_rule_arn(monkeypatch):
    """After a domain's configured bus flips, DELETING rows still tear down
    on the bus their rule was CREATED on - parsed from the stored rule_arn,
    with no config lookup at all."""
    ddb, _, ev, _ = _mock_aws(monkeypatch,
                              pipeline_config_bus="uif-event-dev-price")
    row = _row(status="DELETING")
    row["domain"] = {"S": "price"}
    row["rule_arn"] = {"S": ("arn:aws:events:eu-west-1:283556840805:rule/"
                             "uif-event-dev-product/"
                             "uif-event-dev-on-price-price-updated-v1-pmc")}

    app.lambda_handler(_stream_event(row), None)

    assert ev.remove_targets.call_args.kwargs["EventBusName"] == "uif-event-dev-product"
    assert ev.delete_rule.call_args.kwargs["EventBusName"] == "uif-event-dev-product"
    ddb.get_item.assert_not_called()


STREAM_NAME = "uif-event-dev-pdz-product-product-updated-v1"
STREAM_ARN = f"arn:aws:kinesis:eu-west-1:283556840805:stream/{STREAM_NAME}"


def _kinesis_row(**kw):
    row = _row(customer="pdz", detail_type="product.updated.v1",
               target_type="KINESIS",
               principal="arn:aws:iam::283556840805:root", **kw)
    row["target"]["M"]["target_config"]["M"]["partition_key_path"] = \
        {"S": "$.detail.partitionkey"}
    return row


def test_kinesis_pending_row_provisioned_to_active(monkeypatch):
    """Managed KINESIS (2026-09-24, first client pdz): ON_DEMAND stream
    created if missing, consumer grant as the stream resource policy, rule
    on the message channel, target with the shared delivery RoleArn and
    PartitionKeyPath, activation with the STREAM ARN."""
    ddb, q, ev, kin = _mock_aws(monkeypatch)

    result = app.lambda_handler(_stream_event(_kinesis_row(), name="INSERT"), None)

    assert result == {"batchItemFailures": []}
    q.create_queue.assert_not_called()  # no queues on this path
    kin.create_stream.assert_called_once_with(
        StreamName=STREAM_NAME, StreamModeDetails={"StreamMode": "ON_DEMAND"})

    # Consumer grant: read-side only, on the stream's resource policy.
    pol = json.loads(kin.put_resource_policy.call_args.kwargs["Policy"])
    (stmt,) = pol["Statement"]
    assert stmt["Sid"].endswith("c")
    assert "kinesis:PutRecord" not in stmt["Action"]
    assert "kinesis:GetRecords" in stmt["Action"]

    # Rule: the message channel (KINESIS), per TARGET_TYPE_CHANNEL.
    pattern = json.loads(ev.put_rule.call_args.kwargs["EventPattern"])
    assert pattern == {"detail-type": ["product.updated.v1"],
                       "detail": {"channel": ["message"]}}

    target = ev.put_targets.call_args.kwargs["Targets"][0]
    assert target["Arn"] == STREAM_ARN
    assert target["RoleArn"] == os.environ["KINESIS_DELIVERY_ROLE_ARN"]
    assert target["KinesisParameters"] == {"PartitionKeyPath": "$.detail.partitionkey"}
    assert target["InputPath"] == "$.detail"
    assert target["DeadLetterConfig"] == {
        "Arn": "arn:aws:sqs:eu-west-1:283556840805:uif-event-dev-product-dlq"}

    v = ddb.update_item.call_args.kwargs["ExpressionAttributeValues"]
    assert v[":active"] == {"S": "ACTIVE"}
    assert v[":qarn"] == {"S": STREAM_ARN}


def test_kinesis_existing_stream_is_reused(monkeypatch):
    ddb, _, _, kin = _mock_aws(monkeypatch)
    kin.create_stream.side_effect = ClientError(
        {"Error": {"Code": "ResourceInUseException"}}, "CreateStream")

    app.lambda_handler(_stream_event(_kinesis_row(), name="INSERT"), None)

    v = ddb.update_item.call_args.kwargs["ExpressionAttributeValues"]
    assert v[":active"] == {"S": "ACTIVE"}  # idempotent reuse, no failure
    # A reused stream is live again: the teardown's delete_after mark goes.
    kin.remove_tags_from_stream.assert_called_with(
        StreamName=STREAM_NAME, TagKeys=["delete_after"])


def test_reprovisioned_queues_lose_their_delete_after_mark(monkeypatch):
    """Re-seeding a subscription after DELETING (the bus-flip path) reuses
    the queue and DLQ the teardown tagged delete_after. They are live again,
    so provisioning clears the tag - otherwise a sweeper would delete a
    queue a subscription depends on (seen on price, 2026-10-01)."""
    ddb, q, ev, _ = _mock_aws(monkeypatch)

    app.lambda_handler(_stream_event(_row(), name="INSERT"), None)

    untagged = {c.kwargs["QueueUrl"].rsplit("/", 1)[-1]
                for c in q.untag_queue.call_args_list}
    assert untagged == {"uif-event-dev-pmc-product-product-created-v1",
                        "uif-event-dev-pmc-product-product-created-v1-dlq"}
    assert all(c.kwargs["TagKeys"] == ["delete_after"]
               for c in q.untag_queue.call_args_list)


def test_kinesis_invalid_principal_degrades_not_fails(monkeypatch):
    ddb, _, _, kin = _mock_aws(monkeypatch)
    kin.put_resource_policy.side_effect = ClientError(
        {"Error": {"Code": "ValidationException", "Message": "bad principal"}},
        "PutResourcePolicy")

    app.lambda_handler(_stream_event(_kinesis_row(), name="INSERT"), None)

    v = ddb.update_item.call_args.kwargs["ExpressionAttributeValues"]
    assert v[":active"] == {"S": "ACTIVE"}
    assert v[":reason"] == {"S": "CONSUMER_PRINCIPAL_INVALID"}


def test_kinesis_without_delivery_role_fails_visibly(monkeypatch):
    ddb, _, ev, _ = _mock_aws(monkeypatch)
    monkeypatch.setattr(app, "KINESIS_DELIVERY_ROLE_ARN", "")

    app.lambda_handler(_stream_event(_kinesis_row(), name="INSERT"), None)

    ev.put_rule.assert_not_called()
    v = ddb.update_item.call_args.kwargs["ExpressionAttributeValues"]
    assert v[":failed"] == {"S": "FAILED"}
    assert v[":reason"] == {"S": "KINESIS_DELIVERY_ROLE_MISSING"}


def test_kinesis_teardown_tags_stream_and_strips_policy(monkeypatch):
    ddb, q, ev, kin = _mock_aws(monkeypatch)

    app.lambda_handler(
        _stream_event(_kinesis_row(status="DELETING"), name="MODIFY"), None)

    ev.remove_targets.assert_called_once()
    ev.delete_rule.assert_called_once()
    kin.delete_resource_policy.assert_called_once_with(ResourceARN=STREAM_ARN)
    tags = kin.add_tags_to_stream.call_args.kwargs
    assert tags["StreamName"] == STREAM_NAME and "delete_after" in tags["Tags"]
    q.tag_queue.assert_not_called()  # no queues on this path
    v = ddb.update_item.call_args.kwargs["ExpressionAttributeValues"]
    assert v[":deleted"] == {"S": "DELETED"}
