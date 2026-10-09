import json
import os
import sys
from unittest.mock import MagicMock

import jsonschema
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "glue"))

import event_fabric_processor as efp  # noqa: E402

BUCKET = "product-change-pipeline-test-061206535585"
MAPPING_KEY = "config/product.mapping.json"
DYNAMO_TABLE = "product-change-pipeline-test-event-runs"
METRICS_TABLE = "product-change-pipeline-test-publish-metrics"
PIPELINE_TABLE = "product-change-pipeline-test-pipeline-metrics"
SUBS_TABLE = "product-change-pipeline-test-subscription-configs"
EVENT_RUN_ID = "jr-evt-1"  # the Table 2 row the mocked gsi_gold_run resolves

# Trimmed mapping: enough channel diversity to prove per-channel projection.
#   organizationCode -> ALL channels (the routing-attribute invariant)
#   description  -> event+message+api        (all three channels)
#   ppid         -> message+api only         (so a NULL-description row has
#                                             no event-enabled attrs at all)
#   longDescription -> message+api only
#   itemType     -> message+api only
#   unitLength   -> api only
MAPPING = {
    "domain": "product",
    "columns": [
        {"goldColumnName": "organizationCode", "channels": {"event": True, "message": True, "api": True}},
        {"goldColumnName": "description", "channels": {"event": True, "message": True, "api": True}},
        {"goldColumnName": "ppid", "channels": {"event": False, "message": True, "api": True}},
        {"goldColumnName": "longDescription", "channels": {"event": False, "message": True, "api": True}},
        {"goldColumnName": "itemType", "channels": {"event": False, "message": True, "api": True}},
        {"goldColumnName": "unitLength", "channels": {"event": False, "message": False, "api": True}},
    ],
}

# Result shape of the change-log LEFT JOIN: c-side columns aliased
# _change_op/_change_key, then the master row's columns (all NULL for a
# DELETE verdict, whose master row is gone).
COLUMNS = [
    ("_change_op", "varchar"),
    ("_change_key", "varchar"),
    ("ppid", "varchar"),
    ("description", "varchar"),
    ("long_description", "varchar"),
    ("item_type", "varchar"),
    ("unit_length", "integer"),
    ("_gold_run_id", "varchar"),
]

RUN_A_ROWS = [
    {"_change_op": "UPDATE", "_change_key": "SKU-1",
     "ppid": "SKU-1", "description": "Algebra Textbook",
     "long_description": "Long description of the algebra textbook.",
     "item_type": "FINISHED_GOOD", "unit_length": "27", "_gold_run_id": "RUN-A"},
    {"_change_op": "INSERT", "_change_key": "SKU-2",
     "ppid": "SKU-2", "description": None, "long_description": None,
     "item_type": "FINISHED_GOOD", "unit_length": "30", "_gold_run_id": "RUN-A"},
]

# Shared uif/pearson-event-envelope v1 (Apicurio) - embedded as offline test
# fixture data only, per generate-event-schema SKILL.md's reference carve-out.
ENVELOPE_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
        "specversion": {"const": "1.0"},
        "id": {"type": "string", "minLength": 1},
        "source": {"type": "string", "pattern": "^urn:pearson:(svc|domain):[a-z0-9-]+$"},
        "type": {"type": "string", "pattern": r"^com\.pearson\.[a-z0-9]+\.[a-z0-9-]+\.[a-z0-9]+\.v[0-9]+$"},
        "time": {"type": "string"},
        "datacontenttype": {"const": "application/json"},
        "dataschema": {"type": "string"},
        "subject": {"type": "string", "pattern": "^[a-z-]+/[A-Za-z0-9._-]+$"},
        "correlationid": {"type": "string", "format": "uuid"},
        "partitionkey": {"type": "string", "minLength": 1, "maxLength": 256},
        "dataclassification": {"enum": ["public", "internal", "confidential", "restricted"]},
        "piipresent": {"type": "boolean"},
        "channel": {"enum": ["event", "message", "api"]},
        "data": {"type": "object"},
    },
    "required": [
        "specversion", "id", "source", "type", "time",
        "datacontenttype", "dataschema", "subject",
        "correlationid", "partitionkey", "channel", "data",
    ],
}


def _argv(run_id, changed_by="test-user", join_key="ppid", business_key_columns=None):
    pairs = {
        "run_id": run_id,
        "changed_by": changed_by,
        "join_key": join_key,
        **({"business_key_columns": business_key_columns} if business_key_columns else {}),
        "data_bucket": BUCKET,
        "mapping_key": MAPPING_KEY,
        "dynamo_table": DYNAMO_TABLE,
        "pipeline_metrics_table": PIPELINE_TABLE,
        "publish_metrics_table": METRICS_TABLE,
        "subscription_configs_table": SUBS_TABLE,
        "event_bus_name": "product-change-pipeline-test-product-change-bus",
        "schema_name": "product",
        "glue_database": "gold_product",
        "glue_table": "product",
        "changes_table": "product_changes",
        "run_id_column": "run_id",
        "athena_workgroup": "primary",
        "athena_output": f"s3://{BUCKET}/athena-results/",
        "event_type": "com.pearson.product.product.{operation}.v1",
        "event_source": "urn:pearson:svc:product-change-pipeline",
        "event_dataschema": "https://schemas.pearson.com/product/product-{operation}-data/v1",
        "data_classification": "internal",
        "pii_present": "false",
    }
    argv = []
    for k, v in pairs.items():
        argv += [f"--{k}", v]
    return argv


def _json_bytes(obj):
    return json.dumps(obj).encode("utf-8")


class _FakeBody:
    def __init__(self, data: bytes):
        self._data = data

    def read(self):
        return self._data


SEED_KEY = "config/product.subscriptions.seed.json"
SEED_SUBSCRIPTIONS = [
    {"customer_id": {"S": "pmc"},
     "domain_detail_type": {"S": "product#product.created.v1"},
     "status": {"S": "ACTIVE"}},
    {"customer_id": {"S": "platinum"},
     "domain_detail_type": {"S": "product#product.created.v1"},
     "status": {"S": "ACTIVE"}},
]


def _fake_s3(mapping=None):
    def fake_get_object(Bucket, Key):
        assert Bucket == BUCKET
        if Key == MAPPING_KEY:
            return {"Body": _FakeBody(_json_bytes(mapping if mapping is not None else MAPPING))}
        if Key == SEED_KEY:
            return {"Body": _FakeBody(_json_bytes(SEED_SUBSCRIPTIONS))}
        raise efp.ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
    return fake_get_object


def _athena_page(columns, rows):
    def datum(value):
        return {} if value is None else {"VarCharValue": str(value)}

    header = {"Data": [{"VarCharValue": name} for name, _ in columns]}
    data_rows = [{"Data": [datum(r.get(name)) for name, _ in columns]} for r in rows]
    return {
        "ResultSet": {
            "ResultSetMetadata": {
                "ColumnInfo": [{"Name": n, "Type": t} for n, t in columns]
            },
            "Rows": [header] + data_rows,
        }
    }


def _mock_athena(monkeypatch, rows, columns=None, state="SUCCEEDED"):
    monkeypatch.setattr(
        efp.athena, "start_query_execution",
        MagicMock(return_value={"QueryExecutionId": "q-1"}),
    )
    monkeypatch.setattr(
        efp.athena, "get_query_execution",
        MagicMock(return_value={"QueryExecution": {"Status": {
            "State": state, "StateChangeReason": "boom" if state == "FAILED" else "",
        }}}),
    )
    monkeypatch.setattr(
        efp.athena, "get_query_results",
        MagicMock(return_value=_athena_page(columns or COLUMNS, rows)),
    )
    monkeypatch.setattr(efp.athena, "stop_query_execution", MagicMock())


def _entries():
    """Flattened (DetailType, channel, subject, envelope) across every
    batched PutEvents call - DetailType is per-operation, so the channel
    discriminator lives in the envelope."""
    out = []
    for c in efp.events.put_events.call_args_list:
        for entry in c.kwargs["Entries"]:
            env = json.loads(entry["Detail"])
            out.append((entry["DetailType"], env["channel"],
                        env["subject"].split("/", 1)[1], env))
    return out


def _dynamo_statuses():
    """status values written to the RUN REGISTRY (not metrics), in call order."""
    statuses = []
    for c in efp.dynamodb.update_item.call_args_list:
        if c.kwargs.get("TableName") != DYNAMO_TABLE:
            continue
        values = c.kwargs["ExpressionAttributeValues"]
        for v in values.values():
            if isinstance(v, dict) and v.get("S") in {"STARTED", "COMPLETED", "FAILED"}:
                statuses.append(v["S"])
    return statuses


def _metrics_updates():
    """(status, kwargs) of every update to the pipeline-metrics (Table 2) row."""
    out = []
    for c in efp.dynamodb.update_item.call_args_list:
        if c.kwargs.get("TableName") != PIPELINE_TABLE:
            continue
        out.append((c.kwargs["ExpressionAttributeValues"][":s"]["S"], c.kwargs))
    return out


def _publish_rows():
    """Every Table 3 item written across all BatchWriteItem calls."""
    out = []
    for c in efp.dynamodb.batch_write_item.call_args_list:
        for req in c.kwargs["RequestItems"].get(METRICS_TABLE, []):
            out.append(req["PutRequest"]["Item"])
    return out


@pytest.fixture(autouse=True)
def _fresh_mocks(monkeypatch):
    monkeypatch.setattr(efp.s3, "get_object", MagicMock(side_effect=_fake_s3()))
    monkeypatch.setattr(efp.dynamodb, "update_item", MagicMock())
    # Emit validation reads the BUS (rules + targets), not the table
    # (2026-09-24): every operation routable by default so existing
    # behaviour tests stand.
    monkeypatch.setattr(efp.events, "list_rules",
                        MagicMock(side_effect=_bus_rules()))
    monkeypatch.setattr(efp.events, "list_targets_by_rule",
                        MagicMock(return_value={"Targets": [{"Id": "t1"}]}))
    # gsi_gold_run resolves this run's Table 2 row (written by the Lambda).
    def _query(**kwargs):
        if kwargs.get("TableName") == SUBS_TABLE:
            return {"Items": [{"status": {"S": "ACTIVE"},
                               "customer_id": {"S": "abc"}}]}
        return {"Items": [{
            "event_run_id": {"S": EVENT_RUN_ID},
            "mode": {"S": "INCREMENTAL"},
            "status": {"S": "IN_PROGRESS"},
            "started_at": {"S": "2026-09-17T00:00:00Z"},
        }]}
    monkeypatch.setattr(efp.dynamodb, "query", MagicMock(side_effect=_query))
    monkeypatch.setattr(efp.dynamodb, "batch_write_item", MagicMock(return_value={}))
    monkeypatch.setattr(
        efp.events, "put_events",
        MagicMock(side_effect=lambda Entries: {
            "FailedEntryCount": 0,
            "Entries": [{"EventId": f"eb-{i}"} for i in range(len(Entries))],
        }),
    )


def test_run_registers_processes_and_completes(monkeypatch):
    _mock_athena(monkeypatch, RUN_A_ROWS)

    summary = efp.main(_argv("RUN-A"))

    # DynamoDB: STARTED before any query, COMPLETED at the end.
    assert _dynamo_statuses() == ["STARTED", "COMPLETED"]
    first_call_order = efp.dynamodb.update_item.call_args_list[0]
    assert first_call_order.kwargs["Key"] == {"run_id": {"S": "RUN-A"}}

    # Events are driven from the change log, joined to the master table, with
    # run filtering in the query as an escaped literal (ExecutionParameters
    # is unknown to the Glue runtime's old botocore).
    _, kwargs = efp.athena.start_query_execution.call_args
    q = kwargs["QueryString"]
    assert '"gold_product"."product_changes" c' in q
    assert 'LEFT JOIN "gold_product"."product" m' in q
    assert 'ON m."ppid" = c."ppid"' in q
    assert 'CAST(c."ppid" AS varchar) AS "_change_key"' in q
    assert "c.\"run_id\" = 'RUN-A'" in q
    # The per-channel projection. contains() is the Trino function;
    # array_contains() is Spark and raises FUNCTION_NOT_FOUND on Athena, so the
    # whole query fails, _load_run_changes raises, and EVERY processor run for
    # every domain dies. Assert the function NAME, not a substring of it:
    # 'array_contains(...)' literally contains 'contains(...)', so the three
    # plain `in q` checks this replaces passed with the bug present and proved
    # nothing.
    assert "array_contains" not in q
    sq = "'"
    for ch in ("event", "message", "api"):
        assert f', contains(c."changed_channels", {sq}{ch}{sq}) AS "_ch_{ch}"' in q
    assert "ExecutionParameters" not in kwargs

    assert summary["changes"] == 2
    assert summary["published"] == {"event": 1, "message": 2, "api": 2}
    assert summary["skipped"] == {"event": 1, "message": 0, "api": 0}
    assert summary["deletes"] == 0
    assert summary["missingMaster"] == 0
    assert summary["status"] == "SUCCEEDED"

    by_key = {(dt, ch, subj): env for dt, ch, subj, env in _entries()}
    # DetailType is per-operation; the channel discriminator is in the
    # envelope, so one record's copies share a DetailType across channels.
    assert set(by_key) == {
        ("product.updated.v1", "event", "SKU-1"),
        ("product.updated.v1", "message", "SKU-1"),
        ("product.updated.v1", "api", "SKU-1"),
        ("product.created.v1", "message", "SKU-2"),
        ("product.created.v1", "api", "SKU-2"),
    }

    event_env = by_key[("product.updated.v1", "event", "SKU-1")]
    jsonschema.validate(event_env, ENVELOPE_SCHEMA)
    # One contract per verdict: SKU-1 is an UPDATE, SKU-2 an INSERT.
    assert event_env["type"] == "com.pearson.product.product.updated.v1"
    assert event_env["dataschema"] == "https://schemas.pearson.com/product/product-updated-data/v1"
    created_env = by_key[("product.created.v1", "message", "SKU-2")]
    assert created_env["type"] == "com.pearson.product.product.created.v1"
    assert created_env["dataschema"] == "https://schemas.pearson.com/product/product-created-data/v1"
    assert event_env["data"] == {"description": "Algebra Textbook"}
    assert by_key[("product.updated.v1", "api", "SKU-1")]["data"] == {
        "ppid": "SKU-1", "description": "Algebra Textbook",
        "longDescription": "Long description of the algebra textbook.",
        "itemType": "FINISHED_GOOD", "unitLength": 27,
    }

    # A row with no routing-attribute VALUE degrades to the bare business
    # key (the composite form is pinned in the organizationCode test).
    assert event_env["partitionkey"] == "SKU-1"

    # same occurrence -> same id across channels; one correlationid per run
    assert event_env["id"] == by_key[("product.updated.v1", "message", "SKU-1")]["id"]
    assert event_env["correlationid"] == summary["correlationId"]


def test_organizationcode_in_data_not_envelope(monkeypatch):
    columns = COLUMNS + [("organizationcode", "varchar")]
    rows = [{"_change_op": "UPDATE", "_change_key": "P-1",
             "ppid": "P-1", "description": "With org", "organizationcode": "01D",
             "_gold_run_id": "jr_org"}]
    _mock_athena(monkeypatch, rows, columns=columns)

    efp.main(_argv("jr_org"))

    for dt, ch, _, env in _entries():
        assert "organizationcode" not in env, (dt, ch)
        assert env["data"]["organizationCode"] == "01D", (dt, ch)
        # partitionkey = {business key}|{routing attribute}: per-(product,
        # org) partition ordering (2026-09-22 contract change).
        assert env["partitionkey"] == "P-1|01D", (dt, ch)


def test_delete_verdicts_counted_not_published(monkeypatch):
    """A DELETE verdict's master row is gone - no attributes, no routing
    attribute, so no client rule could match. Counted visibly (summary +
    registry), never published, never silently lost."""
    rows = RUN_A_ROWS + [
        {"_change_op": "DELETE", "_change_key": "SKU-GONE"},  # master columns all NULL
    ]
    _mock_athena(monkeypatch, rows)

    summary = efp.main(_argv("RUN-A"))

    assert summary["changes"] == 3
    assert summary["deletes"] == 1
    assert summary["published"] == {"event": 1, "message": 2, "api": 2}  # unchanged
    assert not any(subj == "SKU-GONE" for _, _, subj, _ in _entries())
    # the registry record carries the delete count
    done = efp.dynamodb.update_item.call_args_list[-1]
    assert done.kwargs["ExpressionAttributeValues"][":deletes"] == {"N": "1"}


def test_channel_whose_hash_did_not_move_is_not_published(monkeypatch):
    """A row that moved only message+api must NOT publish an event.

    This is what keeps "an event" meaning "the event payload changed". Fields
    tagged message+api only - price's priceLastModifiedTimestamp, product's
    affiliation2/3 - write changed_channels without 'event' when they are the
    only thing that moved. Before this gate the processor published every row
    on all three channels regardless of which hash actually moved, so a source
    touch that changed no event-visible attribute still emitted an event.
    """
    columns = COLUMNS + [("_ch_event", "boolean"), ("_ch_message", "boolean"),
                         ("_ch_api", "boolean")]
    rows = [{"_change_op": "UPDATE", "_change_key": "SKU-1",
             "ppid": "SKU-1", "description": "Algebra Textbook",
             "long_description": "Long description of the algebra textbook.",
             "item_type": "FINISHED_GOOD", "unit_length": "27",
             "_gold_run_id": "RUN-A",
             "_ch_event": "false", "_ch_message": "true", "_ch_api": "true"}]
    _mock_athena(monkeypatch, rows, columns=columns)

    summary = efp.main(_argv("RUN-A"))

    # the event channel is suppressed, and visibly so
    assert summary["published"] == {"event": 0, "message": 1, "api": 1}
    assert summary["skipped"]["event"] == 1
    # the other two still deliver - the point of gating per channel rather
    # than skipping the run, which would have dropped these as well
    assert {ch for _, ch, _, _ in _entries()} == {"message", "api"}


def test_absent_channel_flags_publish_everywhere(monkeypatch):
    """No _ch_* columns (an older change log, or a fixture) must behave exactly
    as before: publish on every channel. The gate narrows, it never widens."""
    _mock_athena(monkeypatch, RUN_A_ROWS)

    summary = efp.main(_argv("RUN-A"))

    assert summary["published"] == {"event": 1, "message": 2, "api": 2}


def test_channel_flags_never_reach_the_payload(monkeypatch):
    """The _ch_* columns are plumbing: they must be popped before the row is
    treated as the master record, or they surface as product attributes."""
    columns = COLUMNS + [("_ch_event", "boolean"), ("_ch_message", "boolean"),
                         ("_ch_api", "boolean")]
    rows = [{"_change_op": "UPDATE", "_change_key": "SKU-1",
             "ppid": "SKU-1", "description": "Algebra Textbook",
             "_gold_run_id": "RUN-A",
             "_ch_event": "true", "_ch_message": "true", "_ch_api": "true"}]
    _mock_athena(monkeypatch, rows, columns=columns)

    efp.main(_argv("RUN-A"))

    entries = _entries()
    assert entries, "the row must publish somewhere, or this proves nothing"
    for dt, ch, _, env in entries:
        for leaked in ("_ch_event", "_ch_message", "_ch_api",
                       "chEvent", "chMessage", "chApi"):
            assert leaked not in env["data"], (dt, ch, leaked)


def test_non_delete_without_master_row_counted_skipped(monkeypatch):
    rows = RUN_A_ROWS + [
        {"_change_op": "UPDATE", "_change_key": "SKU-VANISHED"},  # master gone
    ]
    _mock_athena(monkeypatch, rows)

    summary = efp.main(_argv("RUN-A"))

    assert summary["missingMaster"] == 1
    assert summary["published"] == {"event": 1, "message": 2, "api": 2}
    assert not any(subj == "SKU-VANISHED" for _, _, subj, _ in _entries())


def test_routing_attribute_missing_from_a_channel_fails_closed(monkeypatch):
    """Client rules filter on data.organizationCode; a mapping that stops
    enabling it on any channel would silently match no rule - the run must
    fail instead."""
    broken = {
        "domain": "product",
        "columns": [
            {"goldColumnName": "organizationCode",
             "channels": {"event": False, "message": True, "api": True}},  # event flipped off
            {"goldColumnName": "description",
             "channels": {"event": True, "message": True, "api": True}},
        ],
    }
    monkeypatch.setattr(efp.s3, "get_object", MagicMock(side_effect=_fake_s3(mapping=broken)))
    _mock_athena(monkeypatch, RUN_A_ROWS)

    with pytest.raises(ValueError, match="organizationCode.*not mapping-enabled.*event"):
        efp.main(_argv("RUN-A"))
    assert _dynamo_statuses() == ["STARTED", "FAILED"]
    efp.events.put_events.assert_not_called()


def test_putevents_batched_at_ten_entries(monkeypatch):
    # 4 fully-populated products x 3 channels = 12 entries -> batches of 10 + 2.
    rows = [
        {"_change_op": "UPDATE", "_change_key": f"SKU-{i}",
         "ppid": f"SKU-{i}", "description": f"Product {i}",
         "long_description": "L", "item_type": "FG", "unit_length": "1",
         "_gold_run_id": "RUN-B"}
        for i in range(4)
    ]
    _mock_athena(monkeypatch, rows)

    summary = efp.main(_argv("RUN-B"))

    assert summary["published"] == {"event": 4, "message": 4, "api": 4}
    sizes = [len(c.kwargs["Entries"]) for c in efp.events.put_events.call_args_list]
    assert sizes == [10, 2]
    assert len(_entries()) == 12


def test_zero_products_fails_run_and_marks_failed(monkeypatch):
    _mock_athena(monkeypatch, [])
    with pytest.raises(ValueError, match="No change rows with run_id=RUN-NOPE"):
        efp.main(_argv("RUN-NOPE"))
    assert _dynamo_statuses() == ["STARTED", "FAILED"]
    efp.events.put_events.assert_not_called()


def test_athena_failure_marks_run_failed_and_raises(monkeypatch):
    _mock_athena(monkeypatch, RUN_A_ROWS, state="FAILED")
    with pytest.raises(RuntimeError, match="FAILED"):
        efp.main(_argv("RUN-A"))
    assert _dynamo_statuses() == ["STARTED", "FAILED"]
    # the error message lands in the registry record...
    fail_call = [c for c in efp.dynamodb.update_item.call_args_list
                 if c.kwargs.get("TableName") == DYNAMO_TABLE][-1]
    assert "error_message = :err" in fail_call.kwargs["UpdateExpression"]
    # ...and the Table 2 row goes STARTED then terminal FAILED with the error.
    assert [s for s, _ in _metrics_updates()] == ["STARTED", "FAILED"]
    assert ":err" in _metrics_updates()[-1][1]["ExpressionAttributeValues"]
    efp.events.put_events.assert_not_called()


def test_missing_mapping_fails_closed(monkeypatch):
    _mock_athena(monkeypatch, RUN_A_ROWS)
    monkeypatch.setattr(
        efp.s3, "get_object",
        MagicMock(side_effect=efp.ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")),
    )
    with pytest.raises(ValueError, match="Required object missing"):
        efp.main(_argv("RUN-A"))
    assert _dynamo_statuses() == ["STARTED", "FAILED"]
    efp.events.put_events.assert_not_called()


def test_partial_publish_failure_marks_partial_failure(monkeypatch):
    """RUN-A publishes 5 entries (U-event, U-message, U-api, I-message, I-api);
    the first is rejected -> the SKU-1 record fails as a whole (recovery
    re-sends whole records), the run completes PARTIAL_FAILURE, and Table 3
    holds one PUBLISH_FAILED and one PUBLISHED row."""
    _mock_athena(monkeypatch, RUN_A_ROWS)
    monkeypatch.setattr(
        efp.events, "put_events",
        MagicMock(return_value={
            "FailedEntryCount": 1,
            "Entries": [{"ErrorCode": "InternalFailure", "ErrorMessage": "boom"},
                        {"EventId": "e2"}, {"EventId": "e3"},
                        {"EventId": "e4"}, {"EventId": "e5"}],
        }),
    )

    summary = efp.main(_argv("RUN-A"))

    assert summary["status"] == "PARTIAL_FAILURE"
    assert sum(summary["published"].values()) == 4
    assert sum(summary["failed"].values()) == 1
    assert summary["recordsPublished"] == 1 and summary["recordsFailed"] == 1
    # The run itself completes; the failed record is queued for recovery.
    assert _dynamo_statuses() == ["STARTED", "COMPLETED"]
    status, kwargs = _metrics_updates()[-1]
    assert status == "PARTIAL_FAILURE"
    v = kwargs["ExpressionAttributeValues"]
    assert v[":ok"] == {"N": "1"} and v[":bad"] == {"N": "1"}
    assert v[":vs"] == {"M": {"product.product.created": {"N": "1"}}}
    assert v[":vf"] == {"M": {"product.product.updated": {"N": "1"}}}
    assert kwargs["Key"] == {"event_run_id": {"S": EVENT_RUN_ID},
                             "mode": {"S": "INCREMENTAL"}}

    rows = {r["publish_id"]["S"]: r for r in _publish_rows()}
    failed_row = rows["product#RUN-A#SKU-1#updated#v1"]
    assert failed_row["status"] == {"S": "PUBLISH_FAILED"}
    assert failed_row["recovery_scope"] == {"S": "product#RUN-A"}
    assert failed_row["reason"] == {"S": "InternalFailure"}
    assert "event_id" not in failed_row  # sparse gsi_event_id
    ok_row = rows["product#RUN-A#SKU-2#created#v1"]
    assert ok_row["status"] == {"S": "PUBLISHED"}
    assert "recovery_scope" not in ok_row  # sparse gsi_recovery
    # The ledgered event_id is the ENVELOPE id (what an InputPath dead
    # letter carries and what consumers dedupe on), not the bus EventId.
    sku2_env = next(env for _, _, subj, env in _entries() if subj == "SKU-2")
    assert ok_row["event_id"] == {"S": sku2_env["id"]}


def test_all_publishes_failing_marks_run_and_metrics_failed(monkeypatch):
    _mock_athena(monkeypatch, RUN_A_ROWS)
    monkeypatch.setattr(
        efp.events, "put_events",
        MagicMock(return_value={
            "FailedEntryCount": 5,
            "Entries": [{"ErrorCode": "InternalFailure", "ErrorMessage": "boom"}] * 5,
        }),
    )

    with pytest.raises(RuntimeError, match="5 of 5 bus entries failed"):
        efp.main(_argv("RUN-A"))

    assert _dynamo_statuses() == ["STARTED", "FAILED"]
    status, kwargs = _metrics_updates()[-1]
    assert status == "FAILED"
    v = kwargs["ExpressionAttributeValues"]
    assert v[":ok"] == {"N": "0"} and v[":bad"] == {"N": "2"}  # records, not entries
    assert ":err" in v
    # Every Table 3 row is queued for recovery.
    assert all(r["status"] == {"S": "PUBLISH_FAILED"} for r in _publish_rows())


def test_putevents_call_failure_writes_batch_call_failed_rows(monkeypatch):
    """A PutEvents CALL failure (exception after client retries) fails every
    entry of the batch as BATCH_CALL_FAILED - recorded in Table 3 for
    recovery, never silently dropped."""
    _mock_athena(monkeypatch, RUN_A_ROWS)
    monkeypatch.setattr(
        efp.events, "put_events",
        MagicMock(side_effect=RuntimeError("connection reset")),
    )

    with pytest.raises(RuntimeError, match="bus entries failed"):
        efp.main(_argv("RUN-A"))

    rows = _publish_rows()
    assert len(rows) == 2
    assert all(r["reason"] == {"S": "BATCH_CALL_FAILED"} for r in rows)
    assert all(r["recovery_scope"] == {"S": "product#RUN-A"} for r in rows)


def test_succeeded_metrics_update_carries_counts_and_rows(monkeypatch):
    _mock_athena(monkeypatch, RUN_A_ROWS)

    efp.main(_argv("RUN-A"))

    assert [s for s, _ in _metrics_updates()] == ["STARTED", "SUCCEEDED"]
    _, kwargs = _metrics_updates()[-1]
    v = kwargs["ExpressionAttributeValues"]
    assert v[":ok"] == {"N": "2"} and v[":bad"] == {"N": "0"}  # records
    assert v[":vs"] == {"M": {"product.product.created": {"N": "1"},
                              "product.product.updated": {"N": "1"}}}
    assert v[":vf"] == {"NULL": True}
    assert v[":vt"] == {"L": [{"S": "product.product.created"},
                              {"S": "product.product.updated"}]}
    assert ":dur" in v and ":err" not in v
    assert kwargs["ConditionExpression"] == "attribute_exists(event_run_id)"

    # Table 3: one PUBLISHED row per record, payload gzip-round-trippable.
    rows = {r["publish_id"]["S"]: r for r in _publish_rows()}
    assert set(rows) == {"product#RUN-A#SKU-1#updated#v1",
                         "product#RUN-A#SKU-2#created#v1"}
    row = rows["product#RUN-A#SKU-1#updated#v1"]
    assert row["event_run_id"] == {"S": EVENT_RUN_ID}
    assert row["attempt_count"] == {"N": "1"}
    envelope = json.loads(efp.gzip.decompress(row["payload"]["B"]))
    assert envelope["data"]["description"] == "Algebra Textbook"
    assert int(row["payload_size_bytes"]["N"]) == len(json.dumps(envelope))


def test_no_metrics_tables_skips_metrics_writes(monkeypatch):
    _mock_athena(monkeypatch, RUN_A_ROWS)
    argv = _argv("RUN-A")
    for arg in ("--pipeline_metrics_table", "--publish_metrics_table"):
        idx = argv.index(arg)
        del argv[idx:idx + 2]

    summary = efp.main(argv)

    assert summary["status"] == "SUCCEEDED"
    assert _metrics_updates() == []
    efp.dynamodb.batch_write_item.assert_not_called()


def test_missing_metrics_record_is_tolerated(monkeypatch):
    """Manual reprocess: the Lambda never wrote an INPROGRESS record, the
    conditional update fails, and the run still completes."""
    _mock_athena(monkeypatch, RUN_A_ROWS)
    real = efp.dynamodb.update_item

    def update_item(**kwargs):
        if kwargs.get("TableName") == METRICS_TABLE:
            raise efp.ClientError(
                {"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")
        return real(**kwargs)

    monkeypatch.setattr(efp.dynamodb, "update_item", MagicMock(side_effect=update_item))

    summary = efp.main(_argv("RUN-A"))

    assert summary["status"] == "SUCCEEDED"
    assert _dynamo_statuses() == ["STARTED", "COMPLETED"]


def test_run_id_single_quotes_escaped(monkeypatch):
    _mock_athena(monkeypatch, [])
    with pytest.raises(ValueError, match="No change rows"):
        efp.main(_argv("it's"))
    _, kwargs = efp.athena.start_query_execution.call_args
    assert "= 'it''s'" in kwargs["QueryString"]


def test_missing_required_argument_raises():
    argv = _argv("RUN-A")
    idx = argv.index("--dynamo_table")
    del argv[idx:idx + 2]
    with pytest.raises(ValueError, match="dynamo_table"):
        efp.parse_args(argv)


def test_composite_join_key_builds_and_joined_query(monkeypatch):
    """The dev gold change log keys on inventoryitemid,organizationid - a
    comma-separated --join_key must AND the ON clause and concatenate the
    key values into _change_key."""
    _mock_athena(monkeypatch, RUN_A_ROWS)

    efp.main(_argv("RUN-A", join_key="inventoryitemid,organizationid"))

    q = efp.athena.start_query_execution.call_args.kwargs["QueryString"]
    assert ('ON m."inventoryitemid" = c."inventoryitemid" '
            'AND m."organizationid" = c."organizationid"') in q
    assert ("CAST(c.\"inventoryitemid\" AS varchar) || '|' || "
            "CAST(c.\"organizationid\" AS varchar) AS \"_change_key\"") in q


def test_primary_key_prefers_master_product_id_over_change_key(monkeypatch):
    """Regression (2026-09-18 dev finding): the gold master is snake_case, so
    a productId/ppid lookup fell through to the composite change_key -
    publish_ids gained extra '#' segments. product_id wins now, and a
    composite change_key fallback is '|'-joined, never '#'."""
    columns = [
        ("_change_op", "varchar"), ("_change_key", "varchar"),
        ("product_id", "varchar"), ("organizationCode", "varchar"),
        ("description", "varchar"), ("_gold_run_id", "varchar"),
    ]
    rows = [{"_change_op": "UPDATE", "_change_key": "1051754|82",
             "product_id": "A101708520216", "organizationCode": "01D",
             "description": "Snake-case master row", "_gold_run_id": "RUN-A"}]
    _mock_athena(monkeypatch, rows, columns=columns)

    summary = efp.main(_argv("RUN-A"))

    assert summary["status"] == "SUCCEEDED"
    (row,) = _publish_rows()
    assert row["primary_key"] == {"S": "A101708520216"}
    assert row["publish_id"]["S"] == "product#RUN-A#A101708520216#updated#v1"
    assert "#" not in row["primary_key"]["S"]


def test_composite_business_key_keeps_per_org_rows_distinct(monkeypatch):
    """Stock's grain is one row per (product, organisation). With a scalar
    business key, two organisations' rows for one product shared a publish_id
    and DynamoDB rejected the whole BatchWriteItem chunk ("Provided list of
    item keys contains duplicates", 2026-09-30 - stock had zero Table 3 rows).
    A '|'-composite candidate renders '|'-joined in primary_key/publish_id and
    partitionkey, and '.'-joined in the subject (envelope charset)."""
    columns = [
        ("_change_op", "varchar"), ("_change_key", "varchar"),
        ("product_id", "varchar"), ("organization_code", "varchar"),
        ("organizationCode", "varchar"), ("description", "varchar"),
        ("_gold_run_id", "varchar"),
    ]
    rows = [
        {"_change_op": "UPDATE", "_change_key": "A1|07B", "product_id": "A1",
         "organization_code": "07B", "organizationCode": "07B",
         "description": "Stock in 07B", "_gold_run_id": "RUN-S"},
        {"_change_op": "UPDATE", "_change_key": "A1|05B", "product_id": "A1",
         "organization_code": "05B", "organizationCode": "05B",
         "description": "Stock in 05B", "_gold_run_id": "RUN-S"},
    ]
    _mock_athena(monkeypatch, rows, columns=columns)

    summary = efp.main(_argv("RUN-S", join_key="product_id,organization_code",
                             business_key_columns="product_id|organization_code"))

    assert summary["status"] == "SUCCEEDED"
    metrics = _publish_rows()
    assert sorted(r["primary_key"]["S"] for r in metrics) == ["A1|05B", "A1|07B"]
    assert len({r["publish_id"]["S"] for r in metrics}) == 2  # no duplicate keys
    assert all("#" not in r["primary_key"]["S"] for r in metrics)
    for dt, _, subj, env in _entries():
        assert subj in ("A1.07B", "A1.05B"), (dt, subj)
        jsonschema.validate(env, ENVELOPE_SCHEMA)
        org = env["data"]["organizationCode"]
        # {business key}|{routing attribute}, the routing attribute being
        # organizationCode here - so the org appears in both halves.
        assert env["partitionkey"] == f"A1|{org}|{org}", (dt, env["partitionkey"])


def test_composite_business_key_falls_through_when_a_column_is_null(monkeypatch):
    """A composite candidate hits only when EVERY column is populated;
    otherwise the next candidate (or the change_key fallback) applies."""
    columns = [
        ("_change_op", "varchar"), ("_change_key", "varchar"),
        ("product_id", "varchar"), ("organization_code", "varchar"),
        ("description", "varchar"), ("_gold_run_id", "varchar"),
    ]
    rows = [{"_change_op": "UPDATE", "_change_key": "A1|",
             "product_id": "A1", "organization_code": None,
             "description": "No org on master", "_gold_run_id": "RUN-S"}]
    _mock_athena(monkeypatch, rows, columns=columns)

    efp.main(_argv("RUN-S", business_key_columns="product_id|organization_code,product_id"))

    (row,) = _publish_rows()
    assert row["primary_key"] == {"S": "A1"}


def _bus_rules(subscribed_ops=("created", "updated", "deleted"), empty=False,
               state="ENABLED"):
    """list_rules side_effect: one rule per op on the domain bus, patterned
    on the real provisioner output ({"detail-type": [dt], "detail":
    {"channel": [ch]}})."""
    def _list_rules(**kwargs):
        if empty:
            return {"Rules": []}
        return {"Rules": [
            {"Name": f"uif-event-dev-on-product-product-{op}-v1-abc",
             "State": state,
             "EventPattern": json.dumps({
                 "detail-type": [f"product.{op}.v1"],
                 "detail": {"channel": ["event"]},
             })}
            for op in subscribed_ops
        ]}
    return _list_rules


def test_unsubscribed_operation_not_emitted(monkeypatch):
    """Emit validation from the DEPLOYED bus rules: RUN-A's UPDATE record
    has no rule on the domain bus -> skipped and counted; the INSERT record
    (whose rule exists with a target) still publishes."""
    _mock_athena(monkeypatch, RUN_A_ROWS)
    monkeypatch.setattr(efp.events, "list_rules",
                        MagicMock(side_effect=_bus_rules(("created",))))

    summary = efp.main(_argv("RUN-A"))

    assert summary["noSubscribers"] == 1
    assert summary["recordsPublished"] == 1
    sent = {(e[0], e[1]) for e in _entries()}
    assert sent == {("product.created.v1", "message"),
                    ("product.created.v1", "api")}  # SKU-2 (created) only


def test_empty_bus_bootstraps_seed_rows(monkeypatch):
    """No matching rules on the bus -> the DOMAIN-OWNED seed file (S3,
    --bootstrap_subscriptions_key) is conditionally inserted (unchanged),
    but the PROVISIONER materialises rows into rules asynchronously - so
    the bootstrap run itself counts no_subscribers and the next run emits."""
    _mock_athena(monkeypatch, RUN_A_ROWS)
    monkeypatch.setattr(efp.events, "list_rules",
                        MagicMock(side_effect=_bus_rules(empty=True)))
    real_put = MagicMock()

    def _put(**kwargs):
        if kwargs.get("TableName") == SUBS_TABLE:
            assert kwargs["ConditionExpression"] == "attribute_not_exists(customer_id)"
        return real_put(**kwargs)

    monkeypatch.setattr(efp.dynamodb, "put_item", MagicMock(side_effect=_put))

    summary = efp.main(_argv("RUN-A")
                       + ["--bootstrap_subscriptions_key", SEED_KEY])

    puts = [c for c in efp.dynamodb.put_item.call_args_list
            if c.kwargs.get("TableName") == SUBS_TABLE]
    assert len(puts) == 2  # pmc + platinum, from the domain's seed file
    assert {c.kwargs["Item"]["customer_id"]["S"] for c in puts} == {"pmc", "platinum"}
    assert summary["recordsPublished"] == 0  # rules not provisioned yet
    assert summary["noSubscribers"] == 2
    assert efp.events.list_rules.call_count >= 2  # re-checked post-bootstrap


def test_disabled_rule_still_counts_as_subscribed(monkeypatch):
    """A DISABLED rule is a SUSPENDED subscription - previous semantics
    emitted for SUSPENDED, and the bus check preserves that."""
    _mock_athena(monkeypatch, RUN_A_ROWS)
    monkeypatch.setattr(efp.events, "list_rules",
                        MagicMock(side_effect=_bus_rules(state="DISABLED")))

    summary = efp.main(_argv("RUN-A"))

    assert summary["noSubscribers"] == 0
    assert summary["recordsPublished"] == 2


def test_rule_without_target_does_not_count(monkeypatch):
    """bus name + rule + TARGET: a rule with no target delivers nothing."""
    _mock_athena(monkeypatch, RUN_A_ROWS)
    monkeypatch.setattr(efp.events, "list_targets_by_rule",
                        MagicMock(return_value={"Targets": []}))

    summary = efp.main(_argv("RUN-A"))

    assert summary["recordsPublished"] == 0
    assert summary["noSubscribers"] == 2


def test_no_subscription_table_disables_validation(monkeypatch):
    """Sandbox path: without --subscription_configs_table everything emits."""
    _mock_athena(monkeypatch, RUN_A_ROWS)
    argv = _argv("RUN-A")
    idx = argv.index("--subscription_configs_table")
    del argv[idx:idx + 2]

    summary = efp.main(argv)

    assert summary["noSubscribers"] == 0
    assert summary["recordsPublished"] == 2


def test_chunked_ledgering_releases_records_per_chunk(monkeypatch):
    """The OOM fix (2026-09-25): with --delta_chunk_size 1, RUN-A's two
    records are ledgered to Table 3 in TWO separate writes and the records
    list is released between them - memory stays flat at one chunk. Totals
    and the terminal metrics are unchanged from the unchunked path."""
    _mock_athena(monkeypatch, RUN_A_ROWS)

    summary = efp.main(_argv("RUN-A") + ["--delta_chunk_size", "1"])

    assert summary["changes"] == 2
    assert summary["recordsPublished"] == 2
    # One batch_write_item per chunk (2 records / chunk size 1), instead of
    # one big end-of-run write.
    writes = [c for c in efp.dynamodb.batch_write_item.call_args_list]
    assert len(writes) == 2
    per_write = [len(c.kwargs["RequestItems"][METRICS_TABLE]) for c in writes]
    assert per_write == [1, 1]
    # Terminal metrics still carry the merged totals across chunks.
    status, kwargs = _metrics_updates()[-1]
    assert status == "SUCCEEDED"
    v = kwargs["ExpressionAttributeValues"]
    assert v[":ok"] == {"N": "2"}
    assert v[":vs"]["M"] == {"product.product.updated": {"N": "1"},
                             "product.product.created": {"N": "1"}}


def test_zero_rows_still_fails_closed_with_streaming(monkeypatch):
    """The empty-run data error survives the move to streamed rows."""
    _mock_athena(monkeypatch, [])

    with pytest.raises(ValueError, match="No change rows"):
        efp.main(_argv("RUN-EMPTY"))

    assert _dynamo_statuses() == ["STARTED", "FAILED"]


# ---------- Nested columns as JSON (2026-09-25) ----------
# The first price deliveries shipped priceSpecifications as Hive text
# ("[{price_list_id=..., ...}]", snake_case) instead of the JSON array the
# mapping contract declares. Nested mapping columns are now CAST(... AS JSON)
# in the Athena query and their elements renamed to the mapping's canonical
# field names, declared timestamps ISO-8601.

MAPPING_NESTED = {
    "domain": "product",
    "columns": MAPPING["columns"] + [
        {"goldColumnName": "priceSpecifications", "kind": "nested",
         "channels": {"event": True, "message": True, "api": True},
         "fields": [
             {"name": "priceListId", "type": "integer"},
             {"name": "currencyCode", "type": "string"},
             {"name": "priceStartTimestamp", "type": "timestamp"},
         ]},
        # Dotted goldColumnName = struct sub-path: never a flat column,
        # deliberately skipped (no CAST, no crash).
        {"goldColumnName": "fulfillmentOrgDetails.marketingInitiative",
         "kind": "nested", "channels": {"event": True}, "fields": []},
    ],
}

HIVE_BLOB = "[{price_list_id=565335, currency_code=GBP}]"
JSON_CAST = json.dumps([{
    "price_list_id": 565335,
    "currency_code": "GBP",
    "price_start_timestamp": "2017-07-10 00:00:00.000",
    "vat_country_code": None,  # field the mapping does not list -> generic camel
}])

COLUMNS_NESTED = COLUMNS + [
    ("price_specifications", "array(row(price_list_id bigint))"),
    ("_json_price_specifications", "json"),
]


CATALOG_TYPE = ("array<struct<price_list_id:bigint,currency_code:string,"
                "price_start_timestamp:timestamp>>")


def _nested_setup(monkeypatch, *, catalog_type=CATALOG_TYPE,
                  get_table_error=None):
    monkeypatch.setattr(efp.s3, "get_object",
                        MagicMock(side_effect=_fake_s3(MAPPING_NESTED)))
    if get_table_error:
        get_table = MagicMock(side_effect=get_table_error)
    else:
        columns = [{"Name": "ppid", "Type": "string"}]
        if catalog_type:
            columns.append({"Name": "price_specifications", "Type": catalog_type})
        get_table = MagicMock(return_value={
            "Table": {"StorageDescriptor": {"Columns": columns}}})
    monkeypatch.setattr(efp.glue, "get_table", get_table)
    rows = [dict(RUN_A_ROWS[0], price_specifications=HIVE_BLOB,
                 _json_price_specifications=JSON_CAST)]
    _mock_athena(monkeypatch, rows, columns=COLUMNS_NESTED)


def test_nested_column_cast_to_json_and_canonicalized(monkeypatch):
    _nested_setup(monkeypatch)

    efp.main(_argv("RUN-NESTED"))

    # The query selects the JSON companion for exactly the flat nested
    # column that exists on the table; the dotted sub-path never appears.
    # The cast is a TYPED transform: Iceberg timestamps surface as timestamp
    # with time zone, which Trino cannot cast to JSON (TYPE_MISMATCH, live
    # failure jr_04b4401c 2026-09-25) - timestamp/date fields are
    # stringified BEFORE the JSON cast.
    q = efp.athena.start_query_execution.call_args.kwargs["QueryString"]
    assert 'transform(m."price_specifications", x ->' in q
    assert 'CAST(x."price_start_timestamp" AS varchar)' in q
    assert '"price_start_timestamp" varchar' in q
    assert '"currency_code" varchar' in q
    assert 'AS "_json_price_specifications"' in q
    assert "marketing_initiative" not in q
    # The catalog read went to the master table.
    assert efp.glue.get_table.call_args.kwargs == {
        "DatabaseName": "gold_product", "Name": "product"}

    # Payload: a REAL array (not the Hive blob), canonical camelCase names,
    # the declared timestamp ISO-8601, unmapped fields generically camelled.
    payloads = [env["data"].get("priceSpecifications")
                for _, _, _, env in _entries()]
    assert payloads and all(p == [{
        "priceListId": 565335,
        "currencyCode": "GBP",
        "priceStartTimestamp": "2017-07-10T00:00:00.000Z",
        "vatCountryCode": None,
    }] for p in payloads)


def test_nested_cast_skipped_when_column_not_on_table(monkeypatch):
    """A mapping column the master table does not carry gets no CAST - the
    query can never die of COLUMN_NOT_FOUND for contract-only columns."""
    _nested_setup(monkeypatch, catalog_type=None)

    efp.main(_argv("RUN-NESTED"))

    q = efp.athena.start_query_execution.call_args.kwargs["QueryString"]
    assert "_json_price_specifications" not in q


def test_nested_cast_fails_open_on_catalog_error(monkeypatch):
    """Catalog unreadable -> no casts, run proceeds (text blobs beat a dead
    run); the untouched blob passes through the payload as before."""
    _nested_setup(monkeypatch, get_table_error=RuntimeError("denied"))

    efp.main(_argv("RUN-NESTED"))

    q = efp.athena.start_query_execution.call_args.kwargs["QueryString"]
    assert "_json_price_specifications" not in q
    assert _dynamo_statuses() == ["STARTED", "COMPLETED"]


def test_trino_json_select_shapes():
    """The SQL generator: typed transform for array<struct<scalars>> with
    timestamps stringified; direct cast for primitive arrays; None (blob
    passthrough) for anything it cannot serialize safely."""
    sql = efp._trino_json_select("specs", CATALOG_TYPE)
    assert sql.startswith('CAST(transform(m."specs", x -> CAST(ROW(')
    assert 'CAST(x."price_start_timestamp" AS varchar)' in sql
    assert '"price_list_id" bigint' in sql
    assert sql.endswith("AS JSON)")
    # decimal keeps its parameters; date stringifies like timestamp.
    sql2 = efp._trino_json_select(
        "c", "array<struct<amount:decimal(38,2),from_date:date>>")
    assert '"amount" decimal(38,2)' in sql2
    assert 'CAST(x."from_date" AS varchar)' in sql2
    # Primitive arrays cast directly; deeper nesting and maps are skipped.
    assert efp._trino_json_select("tags", "array<string>") == 'CAST(m."tags" AS JSON)'
    assert efp._trino_json_select("t", "array<timestamp>") is None
    assert efp._trino_json_select("n", "array<struct<inner:array<string>>>") is None
    assert efp._trino_json_select("m1", "map<string,string>") is None


# ---------- Published data schemas as the payload gate (2026-09-26) ----------
# priceListId and priceLastModifiedTimestamp shipped in live price events
# although the published schemas declare neither and set
# additionalProperties: false. With --event_schema_key configured, payloads
# are strictly projected to each operation's schema before publish.

EVENT_SCHEMA_KEY = "event-pipeline/domains/product/product-{operation}-data.json"
EVENT_SCHEMA = {
    "$id": "https://schemas.pearson.com/product/product-updated-data/v1",
    "type": "object",
    "properties": {
        "organizationCode": {"type": "string"},
        "description": {"type": ["string", "null"]},
        "priceSpecifications": {
            "type": ["array", "null"],
            "items": {
                "type": "object",
                "properties": {
                    "currencyCode": {"type": ["string", "null"]},
                    "priceStartTimestamp": {"type": ["string", "null"]},
                },
                "additionalProperties": False,
            },
        },
    },
    "additionalProperties": False,
}


def _fake_s3_with_schema(mapping, schema):
    inner = _fake_s3(mapping)

    def fake_get_object(Bucket, Key):
        if Key.startswith("event-pipeline/domains/product/product-") and Key.endswith("-data.json"):
            return {"Body": _FakeBody(_json_bytes(schema))}
        return inner(Bucket=Bucket, Key=Key)
    return fake_get_object


def test_apply_event_schema_strict_projection():
    payload = {
        "productId": "A1", "priceListId": 7,  # undeclared at a strict level
        "specs": [{"currencyCode": "GBP", "priceListId": 7}],
        "loose": {"anything": True},
    }
    schema = {
        "type": "object",
        "properties": {
            "productId": {"type": "string"},
            "specs": {"items": {
                "type": "object",
                "properties": {"currencyCode": {}},
                "additionalProperties": False,
            }},
            # 'loose' declared, its own level NOT strict -> untouched.
            "loose": {"type": "object", "properties": {}},
        },
        "additionalProperties": False,
    }
    assert efp._apply_event_schema(payload, schema) == {
        "productId": "A1",
        "specs": [{"currencyCode": "GBP"}],
        "loose": {"anything": True},
    }
    # A schema level without additionalProperties: false drops nothing.
    assert efp._apply_event_schema({"a": 1, "b": 2},
                                   {"properties": {"a": {}}}) == {"a": 1, "b": 2}


def test_event_schema_filters_published_payloads(monkeypatch):
    """End to end: with --event_schema_key set, undeclared fields (top-level
    AND nested-element) never leave, while declared attributes and the
    envelope are untouched."""
    _nested_setup(monkeypatch)
    monkeypatch.setattr(efp.s3, "get_object", MagicMock(
        side_effect=_fake_s3_with_schema(MAPPING_NESTED, EVENT_SCHEMA)))

    efp.main(_argv("RUN-SCHEMA") + ["--event_schema_key", EVENT_SCHEMA_KEY])

    assert _entries()
    for _, _, _, env in _entries():
        data = env["data"]
        # ppid/longDescription/itemType/unitLength are mapping-enabled on
        # some channels but NOT declared in the published schema - gone.
        assert set(data) <= {"organizationCode", "description",
                             "priceSpecifications"}
        for element in data.get("priceSpecifications") or []:
            assert set(element) <= {"currencyCode", "priceStartTimestamp"}
            assert "priceListId" not in element


def test_missing_event_schema_ships_unfiltered(monkeypatch):
    """A configured but unreadable schema logs and ships unfiltered -
    degraded and visible, never a dead run."""
    _nested_setup(monkeypatch)  # _fake_s3 raises NoSuchKey for unknown keys

    efp.main(_argv("RUN-NOSCHEMA") + ["--event_schema_key", EVENT_SCHEMA_KEY])

    assert _dynamo_statuses() == ["STARTED", "COMPLETED"]
    assert any("priceSpecifications" in env["data"]
               for _, _, _, env in _entries())
    # Unfiltered: the undeclared nested field still present.
    element = next(env["data"]["priceSpecifications"][0]
                   for _, _, _, env in _entries()
                   if env["data"].get("priceSpecifications"))
    assert "priceListId" in element


def test_iso8601_and_camel_helpers():
    assert efp._iso8601("2017-07-10 00:00:00.000") == "2017-07-10T00:00:00.000Z"
    assert efp._iso8601("2017-07-10 00:00:00.000000 UTC") == "2017-07-10T00:00:00.000000Z"
    assert efp._iso8601("not a timestamp") == "not a timestamp"
    assert efp._camel("price_list_id") == "priceListId"
    assert efp._camel("vat_country_code") == "vatCountryCode"
