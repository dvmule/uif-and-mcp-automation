import gzip
import json
import os
import sys
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "glue"))

os.environ.setdefault("AWS_DEFAULT_REGION", "eu-west-1")

import event_fabric_recovery as rec  # noqa: E402

TABLE = "uif-event-dev-publish-metrics"
PIPELINE = "uif-event-dev-pipeline-metrics"
GOLD_RUN = "jr_gold1"
SCOPE = f"product#{GOLD_RUN}"

ENVELOPE = {"specversion": "1.0", "id": "env-1", "channel": "event",
            "source": "urn:pearson:svc:product-change-pipeline",
            "type": "com.pearson.product.product.updated.v1",
            "subject": "product/A1", "partitionkey": "A1|MDM",
            "data": {"productId": "A1"}}


def _argv(**extra):
    pairs = {"domain": "product", "gold_run_id": GOLD_RUN,
             "started_by": "ops@pearson.com",
             "publish_metrics_table": TABLE,
             "pipeline_metrics_table": PIPELINE,
             "event_stack_prefix": "uif-event-dev", **extra}
    argv = []
    for k, v in pairs.items():
        argv += [f"--{k}", str(v)]
    return argv


def _gsi_row(publish_id, operation="updated", version="v1"):
    return {"publish_id": {"S": publish_id},
            "recovery_scope": {"S": SCOPE},
            "primary_key": {"S": publish_id.split("#")[2]},
            "operation": {"S": operation}, "version": {"S": version},
            "event_run_id": {"S": "jr_orig"}, "attempt_count": {"N": "1"}}


PID1 = f"product#{GOLD_RUN}#A1#updated#v1"
PID2 = f"product#{GOLD_RUN}#A2#created#v1"


@pytest.fixture()
def mocks(monkeypatch):
    ddb, ev = MagicMock(), MagicMock()

    def query(**kwargs):
        if kwargs.get("IndexName") == "gsi_recovery":
            return {"Items": [_gsi_row(PID1), _gsi_row(PID2, "created")]}
        if kwargs.get("IndexName") == "gsi_gold_run":
            return {"Items": [{"event_run_id": {"S": "jr_orig"},
                               "mode": {"S": "INCREMENTAL"},
                               "started_at": {"S": "2026-09-24T00:00:00Z"}}]}
        raise AssertionError(f"unexpected query {kwargs}")

    def get_item(**kwargs):
        env = dict(ENVELOPE)
        env["id"] = "env-" + kwargs["Key"]["publish_id"]["S"].split("#")[2]
        return {"Item": {"payload": {"B": gzip.compress(json.dumps(env).encode())}}}

    ddb.query.side_effect = query
    ddb.get_item.side_effect = get_item
    ev.put_events.side_effect = lambda Entries: {
        "FailedEntryCount": 0,
        "Entries": [{"EventId": f"eb-{i}"} for i in range(len(Entries))]}
    monkeypatch.setattr(rec, "dynamodb", ddb)
    monkeypatch.setattr(rec, "events", ev)
    return ddb, ev


def _updates(ddb, table=TABLE):
    return [c.kwargs for c in ddb.update_item.call_args_list
            if c.kwargs["TableName"] == table]


def test_recovery_republishes_byte_identical_and_settles_rows(mocks):
    ddb, ev = mocks

    summary = rec.main(_argv())

    assert summary["status"] == "SUCCEEDED"
    assert summary["outstanding"] == 2 and summary["republished"] == 2
    assert summary["recoveryRunId"].startswith("rp_")

    # Entries: the STORED envelope byte-identical, per-row DetailType, the
    # domain bus by convention, Source from the envelope itself.
    entries = ev.put_events.call_args.kwargs["Entries"]
    assert [e["EventBusName"] for e in entries] == ["uif-event-dev-product"] * 2
    assert {e["DetailType"] for e in entries} == {"product.updated.v1",
                                                  "product.created.v1"}
    env1 = json.loads(entries[0]["Detail"])
    assert env1["id"] == "env-A1"  # unchanged - byte-identical replay
    assert entries[0]["Source"] == ENVELOPE["source"]

    # Rule 2 on the SAME rows: PUBLISHED, recovery_scope REMOVEd (leaves
    # gsi_recovery), event_id stays the envelope id, optimistic condition.
    upds = _updates(ddb)
    assert len(upds) == 2
    for upd in upds:
        assert upd["ConditionExpression"] == "#s = :failed"
        assert "REMOVE recovery_scope, reason, error_msg" in upd["UpdateExpression"]
        assert "ADD attempt_count :one" in upd["UpdateExpression"]
        v = upd["ExpressionAttributeValues"]
        assert v[":published"] == {"S": "PUBLISHED"}
    eids = {u["ExpressionAttributeValues"][":eid"]["S"] for u in upds}
    assert eids == {"env-A1", "env-A2"}

    # Run accounting: replay_count bump on the ORIGINAL processor row, plus
    # this run's own REPLAY row carrying the SAME gold_run_id.
    bump = _updates(ddb, PIPELINE)[0]
    assert bump["Key"]["event_run_id"] == {"S": "jr_orig"}
    assert "ADD replay_count :one" in bump["UpdateExpression"]
    run = ddb.put_item.call_args.kwargs
    assert run["TableName"] == PIPELINE
    item = run["Item"]
    assert item["mode"] == {"S": "REPLAY"}
    assert item["gold_run_id"] == {"S": GOLD_RUN}  # spans gsi_gold_run
    assert item["worker_type"] == {"S": "PYTHON_SHELL"}
    assert item["started_by"] == {"S": "ops@pearson.com"}
    assert item["published_row_count"] == {"N": "2"}


def test_still_failing_entry_stays_queued(mocks):
    ddb, ev = mocks
    ev.put_events.side_effect = lambda Entries: {
        "FailedEntryCount": 1,
        "Entries": [{"ErrorCode": "ThrottlingException", "ErrorMessage": "slow"},
                    {"EventId": "eb-2"}]}

    summary = rec.main(_argv())

    assert summary["status"] == "PARTIAL_FAILURE"
    assert summary["republished"] == 1 and summary["failed"] == 1
    upds = _updates(ddb)
    rule3 = next(u for u in upds if ":why" in u["ExpressionAttributeValues"])
    assert rule3["ExpressionAttributeValues"][":why"] == {"S": "ThrottlingException"}
    assert "REMOVE" not in rule3["UpdateExpression"]  # stays in gsi_recovery
    assert rule3["ConditionExpression"] == "#s = :failed"


def test_whole_call_failure_marks_batch_call_failed(mocks):
    ddb, ev = mocks
    ev.put_events.side_effect = RuntimeError("network")

    summary = rec.main(_argv())

    assert summary["status"] == "FAILED"
    assert summary["failed"] == 2
    for u in _updates(ddb):
        assert u["ExpressionAttributeValues"][":why"] == {"S": "BATCH_CALL_FAILED"}


def test_race_lost_settlement_counts_skipped(mocks):
    """A concurrent recovery settled the row between query and update: the
    bus accepted this run's publish too, but the OTHER run owns the row."""
    ddb, _ = mocks
    def upd(**kwargs):
        if kwargs["TableName"] == TABLE:
            raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}},
                              "UpdateItem")
        return {}
    ddb.update_item.side_effect = upd

    summary = rec.main(_argv())

    assert summary["skipped"] == 2
    assert summary["republished"] == 0 and summary["failed"] == 0


def test_nothing_outstanding_short_circuits(mocks):
    ddb, ev = mocks
    ddb.query.side_effect = lambda **k: {"Items": []}

    summary = rec.main(_argv())

    assert summary["status"] == "NOTHING_TO_RECOVER"
    ev.put_events.assert_not_called()
    ddb.put_item.assert_not_called()   # no run row
    ddb.update_item.assert_not_called()  # no replay bump either


def test_missing_payload_fails_row_visibly(mocks):
    ddb, ev = mocks
    ddb.get_item.side_effect = lambda **k: {"Item": {}}

    summary = rec.main(_argv())

    ev.put_events.assert_not_called()
    assert summary["failed"] == 2
    for u in _updates(ddb):
        assert u["ExpressionAttributeValues"][":why"] == {"S": "PAYLOAD_MISSING"}


def test_attribution_is_mandatory():
    with pytest.raises(ValueError, match="started_by"):
        rec.main(["--domain", "product", "--gold_run_id", GOLD_RUN,
                  "--publish_metrics_table", TABLE,
                  "--event_stack_prefix", "uif-event-dev"])
