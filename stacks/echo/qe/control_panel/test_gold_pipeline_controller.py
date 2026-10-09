# test_gold_pipeline_controller.py
# Unit tests for the /gold-pipelines API surface in pipeline_controller.
# Focus on validation logic (no Spark/Glue dependencies).

import importlib
import json
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

LAMBDA_DIR = Path(__file__).resolve().parents[1] / "lambda"
sys.path.insert(0, str(LAMBDA_DIR))


@pytest.fixture
def pc():
    """Import pipeline_controller with AWS clients mocked at import time."""
    with mock.patch("boto3.resource"), mock.patch("boto3.client"):
        if "pipeline_controller" in sys.modules:
            del sys.modules["pipeline_controller"]
        module = importlib.import_module("pipeline_controller")
        return module


def _valid_gold_body():
    return {
        "gold_id": "product",
        "domain":  "product",
        "schema": {
            "master_s3_path": "s3://uif-echo-dev-gold-artifacts/schemas/gold/product/v1/master.json",
            "version":        "v1",
        },
        "mapping": {
            "manifest_s3_path": "s3://uif-echo-dev-gold-artifacts/mappings/product/v1/manifest.yaml",
            "sql_s3_path":      "s3://uif-echo-dev-gold-artifacts/mappings/product/v1/sql/product.sql",
        },
        "sources": [
            {"alias": "items", "silver_table": "uif_echo_dev_silver_mdm.mtl_system_items_b"},
        ],
        "target": {
            "database":         "uif_echo_dev_gold_product",
            "table":            "product",
            "s3_path":          "s3://uif-echo-dev-gold/product/",
            "primary_key_cols": ["product_id", "org_id"],
        },
    }


def test_validate_accepts_minimal_valid(pc):
    assert pc._validate_gold_config(_valid_gold_body()) is None


def test_validate_rejects_missing_top_field(pc):
    body = _valid_gold_body()
    del body["target"]
    err = pc._validate_gold_config(body)
    assert err is not None and "target" in err


def test_validate_rejects_empty_pk(pc):
    body = _valid_gold_body()
    body["target"]["primary_key_cols"] = []
    err = pc._validate_gold_config(body)
    assert err is not None and "primary_key_cols" in err


def test_validate_rejects_non_s3_master(pc):
    body = _valid_gold_body()
    body["schema"]["master_s3_path"] = "/local/path.json"
    err = pc._validate_gold_config(body)
    assert err is not None and "s3://" in err


def test_validate_rejects_empty_sources(pc):
    body = _valid_gold_body()
    body["sources"] = []
    err = pc._validate_gold_config(body)
    assert err is not None and "sources" in err


def test_validate_rejects_source_without_silver_table(pc):
    body = _valid_gold_body()
    body["sources"] = [{"alias": "x"}]
    err = pc._validate_gold_config(body)
    assert err is not None and "silver_table" in err


def test_delete_without_cascade_rejected(pc):
    resp = pc.delete_gold_pipeline("product", cascade=False)
    assert resp["statusCode"] == 400
    assert "cascade" in json.loads(resp["body"])["error"]


# ---------------------------------------------------------------------------
# GET /gold-runs (UIFPE-502). Gold rows carry gold_id and never pipeline_id, so
# they are invisible to the pipeline_id-index list_runs queries; these pin the
# separate read path, and the ordering the Lambda has to do itself because the
# metrics range key is a random run_id.


def _gold_row(gold_id, run_id, started_at, status="SUCCEEDED"):
    return {
        "job_name": "uif-echo-dev-gold-transform",
        "run_id": run_id,
        "gold_id": gold_id,
        "status": status,
        "started_at": started_at,
        "rows_read": 10,
        "rows_written": 10,
    }


GOLD_JOB = "uif-echo-dev-gold-transform"


def _wire_metrics(pc, rows, pages=None):
    """Point the metrics table at `rows`, or at a sequence of paged responses.

    Also pins GOLD_JOB_NAME: the module resolves it to "" with no env var set,
    and an empty partition key is a ValidationException on real DynamoDB, so
    leaving it empty would make every assertion below vacuous.
    """
    pc.GOLD_JOB_NAME = GOLD_JOB
    table = mock.MagicMock()
    if pages is None:
        table.query.return_value = {"Items": rows}
    else:
        table.query.side_effect = pages
    pc.dynamo.Table.return_value = table
    # no in-flight Glue runs unless a test says otherwise
    pc.glue.get_paginator.return_value.paginate.return_value = [{"JobRuns": []}]
    return table


def test_gold_runs_returns_every_entity_newest_first(pc):
    _wire_metrics(pc, [
        _gold_row("price", "jr_a", "2026-09-20T08:45:00"),
        _gold_row("product", "jr_b", "2026-09-21T06:30:00"),
        _gold_row("stock", "jr_c", "2026-09-19T08:15:00"),
    ])
    resp = pc.list_gold_runs({})
    body = json.loads(resp["body"])
    assert resp["statusCode"] == 200
    # ordered by started_at DESC, which DynamoDB cannot do here
    assert [r["run_id"] for r in body["runs"]] == ["jr_b", "jr_a", "jr_c"]
    assert body["truncated"] is False


def test_gold_runs_queries_the_gold_job_partition_not_the_pipeline_index(pc):
    """The whole reason this endpoint exists: gold rows are not in that GSI."""
    table = _wire_metrics(pc, [])
    pc.list_gold_runs({})
    kwargs = table.query.call_args.kwargs
    assert "IndexName" not in kwargs, kwargs
    # the real job name, on the base table's partition key
    expr = kwargs["KeyConditionExpression"].get_expression()
    assert expr["operator"] == "="
    assert expr["values"][0].name == "job_name", expr
    assert expr["values"][1] == GOLD_JOB, expr


def test_gold_runs_filters_to_one_entity(pc):
    _wire_metrics(pc, [
        _gold_row("price", "jr_a", "2026-09-20T08:45:00"),
        _gold_row("product", "jr_b", "2026-09-21T06:30:00"),
    ])
    body = json.loads(pc.list_gold_runs({"gold_id": "price"})["body"])
    assert [r["run_id"] for r in body["runs"]] == ["jr_a"]


def test_gold_runs_honours_limit_and_reports_truncation(pc):
    _wire_metrics(pc, [
        _gold_row("price", "jr_a", "2026-09-20T08:45:00"),
        _gold_row("product", "jr_b", "2026-09-21T06:30:00"),
    ])
    body = json.loads(pc.list_gold_runs({"limit": "1"})["body"])
    assert [r["run_id"] for r in body["runs"]] == ["jr_b"]
    assert body["truncated"] is True, "limit cut a run but truncated was False"


def test_gold_runs_pages_the_partition(pc):
    _wire_metrics(pc, None, pages=[
        {"Items": [_gold_row("price", "jr_a", "2026-09-20T08:45:00")],
         "LastEvaluatedKey": {"job_name": "j", "run_id": "jr_a"}},
        {"Items": [_gold_row("product", "jr_b", "2026-09-21T06:30:00")]},
    ])
    body = json.loads(pc.list_gold_runs({})["body"])
    assert [r["run_id"] for r in body["runs"]] == ["jr_b", "jr_a"]


def test_gold_runs_surfaces_an_in_flight_run_ahead_of_history(pc):
    _wire_metrics(pc, [_gold_row("price", "jr_done", "2026-09-20T08:45:00")])
    pc.glue.get_paginator.return_value.paginate.return_value = [{
        "JobRuns": [{
            "Id": "jr_running",
            "JobRunState": "RUNNING",
            "Arguments": {"--gold_id": "product"},
            "StartedOn": "2026-09-21T06:30:00",
        }],
    }]
    body = json.loads(pc.list_gold_runs({})["body"])
    assert body["runs"][0]["run_id"] == "jr_running"
    # keyed by gold_id, not pipeline_id — the UI distinguishes layers on it
    assert body["runs"][0]["gold_id"] == "product"
    assert "pipeline_id" not in body["runs"][0]


def test_in_flight_helper_still_keys_bronze_runs_by_pipeline_id(pc):
    """Control for the generalisation: the default arguments must leave the
    bronze/silver caller behaving exactly as before."""
    pc.glue.get_paginator.return_value.paginate.return_value = [{
        "JobRuns": [
            {"Id": "jr_1", "JobRunState": "RUNNING",
             "Arguments": {"--pipeline_id": "mdm-items"}},
            {"Id": "jr_2", "JobRunState": "RUNNING",
             "Arguments": {"--pipeline_id": "someone-else"}},
        ],
    }]
    rows = pc._in_flight_glue_runs("uif-echo-dev-bronze-ingest", "mdm-items")
    assert [r["run_id"] for r in rows] == ["jr_1"]
    assert rows[0]["pipeline_id"] == "mdm-items"
    assert "gold_id" not in rows[0]


def test_gold_runs_route_is_reachable_and_writes_to_it_are_not(pc):
    """The route must not sit under /gold-pipelines/{gold_id}: gold_id comes
    from pathParameters, and the PUT/DELETE branches carry no /runs exclusion,
    so a write to such a path would land in update_gold_pipeline."""
    _wire_metrics(pc, [])
    with mock.patch.dict(os.environ, {}, clear=False):
        get = pc.handler(
            {"requestContext": {"http": {"method": "GET", "path": "/gold-runs"}},
             "rawPath": "/gold-runs", "pathParameters": {}}, None)
    assert get["statusCode"] == 200, get

    put = pc.handler(
        {"requestContext": {"http": {"method": "PUT", "path": "/gold-runs"}},
         "rawPath": "/gold-runs", "pathParameters": {}, "body": "{}"}, None)
    assert put["statusCode"] == 400, put
    assert "Unknown route" in json.loads(put["body"])["error"]


def test_gold_runs_names_an_unresolved_job_name(pc):
    """The env fallback resolves to "" (16af1f0e), which DynamoDB rejects as a
    partition key — the cause has to be legible, not a ValidationException."""
    _wire_metrics(pc, [])
    with mock.patch.object(pc, "GOLD_JOB_NAME", ""):
        resp = pc.list_gold_runs({})
    assert resp["statusCode"] == 500
    assert "GOLD_JOB_NAME" in json.loads(resp["body"])["error"]


# ---------------------------------------------------------------------------
# get_run across layers (UIFPE-505). job_name is the metrics partition key and
# one pipeline's runs span several jobs, so a GetItem pinned to bronze could
# only ever find bronze rows — 2,894 of the 7,885 live rows were unreachable.


def _metrics_table(pc):
    table = mock.MagicMock()
    pc.dynamo.Table.return_value = table
    return table


def test_get_run_finds_a_silver_run_without_a_job_name_hint(pc):
    """The defect: silver rows live in a different partition from bronze."""
    table = _metrics_table(pc)
    table.query.return_value = {"Items": [
        {"run_id": "jr_s", "pipeline_id": "mdm-items",
         "job_name": "uif-echo-dev-silver-transform", "status": "SUCCEEDED"},
    ]}
    resp = pc.get_run("mdm-items", "jr_s")
    body = json.loads(resp["body"])
    assert resp["statusCode"] == 200
    assert body["job_name"] == "uif-echo-dev-silver-transform"
    # resolved through the index on the path's pipeline_id, not a pinned GetItem
    table.get_item.assert_not_called()
    kwargs = table.query.call_args.kwargs
    assert kwargs["IndexName"] == "pipeline_id-index"


def test_get_run_filters_by_run_id_within_the_pipeline(pc):
    table = _metrics_table(pc)
    table.query.return_value = {"Items": []}
    pc.get_run("mdm-items", "jr_missing")
    kwargs = table.query.call_args.kwargs
    # run_id is not a key on that index, so it must be a FilterExpression
    assert "FilterExpression" in kwargs, kwargs
    # and Limit must NOT be set: DynamoDB applies it before the filter
    assert "Limit" not in kwargs, kwargs


def test_get_run_pages_past_a_filtered_empty_page(pc):
    """A filtered query can return an empty page with more to come; stopping
    there would report a run that exists as not found."""
    table = _metrics_table(pc)
    table.query.side_effect = [
        {"Items": [], "LastEvaluatedKey": {"pipeline_id": "mdm-items"}},
        {"Items": [{"run_id": "jr_late", "pipeline_id": "mdm-items",
                    "job_name": "uif-echo-dev-replicate-bq"}]},
    ]
    body = json.loads(pc.get_run("mdm-items", "jr_late")["body"])
    assert body["run_id"] == "jr_late"


def test_get_run_uses_an_exact_lookup_when_given_a_job_name(pc):
    table = _metrics_table(pc)
    table.get_item.return_value = {"Item": {
        "run_id": "jr_g", "job_name": "uif-echo-dev-gold-transform",
        "gold_id": "price", "status": "SUCCEEDED"}}
    body = json.loads(pc.get_run("price", "jr_g",
                                 job_name="uif-echo-dev-gold-transform")["body"])
    assert body["gold_id"] == "price"
    key = table.get_item.call_args.kwargs["Key"]
    assert key == {"job_name": "uif-echo-dev-gold-transform", "run_id": "jr_g"}
    table.query.assert_not_called()


def test_get_run_glue_fallback_honours_the_job_name_hint(pc):
    """An in-flight run is not in metrics yet, and no API can infer its JobName
    from a run id — so the hint is the only way to reach a non-bronze one."""
    table = _metrics_table(pc)
    table.get_item.return_value = {}
    pc.glue.get_job_run.return_value = {"JobRun": {
        "JobRunState": "RUNNING", "StartedOn": "2026-09-22T10:00:00"}}
    body = json.loads(pc.get_run("mdm-items", "jr_live",
                                 job_name="uif-echo-dev-silver-transform")["body"])
    assert body["status"] == "RUNNING"
    assert pc.glue.get_job_run.call_args.kwargs["JobName"] ==         "uif-echo-dev-silver-transform"


# ---------------------------------------------------------------------------
# PATCH /gold-pipelines/{gold_id}/schedule. The console owns frequency
# (UIFPE-564), start_time (UIFPE-513) and weekly_days (UIFPE-532). These pin the
# narrow UpdateExpression, the strict parsers shared with the dispatcher, and
# that an unknown entity is a 404 rather than toggle's silent upsert.

def _gold_cfg_table(pc, item):
    table = pc.dynamo.Table.return_value
    table.get_item.return_value = {"Item": item} if item is not None else {}
    table.update_item.reset_mock()
    return table


def test_set_start_time_updates_only_that_field(pc):
    table = _gold_cfg_table(pc, {
        "gold_id": "product", "version": "latest", "enabled": True,
        "schedule": {"frequency": "gold-24h", "start_time": "06:30",
                     "failure_cooldown_minutes": 30},
    })
    resp = pc.set_gold_schedule("product", {"start_time": "07:00"})
    assert resp["statusCode"] == 200, resp
    body = json.loads(resp["body"])
    assert body["schedule"]["start_time"] == "07:00"
    assert body["schedule"]["frequency"] == "gold-24h"      # cadence untouched
    kw = table.update_item.call_args.kwargs
    assert kw["UpdateExpression"] == "SET #s.start_time = :t"
    assert kw["ExpressionAttributeNames"] == {"#s": "schedule"}
    assert kw["ExpressionAttributeValues"] == {":t": "07:00"}
    assert "attribute_exists(gold_id)" in kw["ConditionExpression"]


@pytest.mark.parametrize("bad", [":30", "24:00", "6e1:00", "0630", "", None, 630])
def test_set_start_time_is_as_strict_as_the_dispatcher(pc, bad):
    table = _gold_cfg_table(pc, {"gold_id": "product", "version": "latest",
                                 "schedule": {"frequency": "gold-24h"}})
    resp = pc.set_gold_schedule("product", {"start_time": bad})
    assert resp["statusCode"] == 400, resp
    assert "start_time" in json.loads(resp["body"])["error"]
    table.update_item.assert_not_called()


def test_set_start_time_unknown_entity_is_404_not_an_upsert(pc):
    table = _gold_cfg_table(pc, None)
    resp = pc.set_gold_schedule("nope", {"start_time": "07:00"})
    assert resp["statusCode"] == 404, resp
    table.update_item.assert_not_called()


def test_set_start_time_creates_the_schedule_map_when_absent(pc):
    table = _gold_cfg_table(pc, {"gold_id": "product", "version": "latest"})
    resp = pc.set_gold_schedule("product", {"start_time": "07:00"})
    assert resp["statusCode"] == 200, resp
    kw = table.update_item.call_args.kwargs
    assert kw["UpdateExpression"] == "SET #s = :m"
    assert kw["ExpressionAttributeValues"] == {":m": {"start_time": "07:00"}}


def test_set_start_time_route_is_reachable(pc):
    _gold_cfg_table(pc, {"gold_id": "product", "version": "latest",
                         "schedule": {"frequency": "gold-24h"}})
    resp = pc.handler(
        {"requestContext": {"http": {"method": "PATCH",
                                     "path": "/gold-pipelines/product/schedule"}},
         "rawPath": "/gold-pipelines/product/schedule",
         "pathParameters": {"gold_id": "product"},
         "body": json.dumps({"start_time": "07:00"})}, None)
    assert resp["statusCode"] == 200, resp
    assert json.loads(resp["body"])["schedule"]["start_time"] == "07:00"


# UIFPE-532: the weekly rung's day set rides the same console route.

def test_set_weekly_days_on_a_weekly_entity(pc):
    table = _gold_cfg_table(pc, {
        "gold_id": "product", "version": "latest",
        "schedule": {"frequency": "gold-weekly", "start_time": "06:30",
                     "weekly_days": ["MON"]},
    })
    resp = pc.set_gold_schedule("product", {"weekly_days": ["thu", "TUE"]})
    assert resp["statusCode"] == 200, resp
    body = json.loads(resp["body"])
    assert body["schedule"]["weekly_days"] == ["TUE", "THU"]     # ISO order, upper
    assert body["schedule"]["start_time"] == "06:30"            # untouched
    kw = table.update_item.call_args.kwargs
    assert kw["UpdateExpression"] == "SET #s.weekly_days = :d"
    assert kw["ExpressionAttributeValues"] == {":d": ["TUE", "THU"]}


def test_set_start_time_and_weekly_days_together(pc):
    table = _gold_cfg_table(pc, {
        "gold_id": "product", "version": "latest",
        "schedule": {"frequency": "gold-weekly"},
    })
    resp = pc.set_gold_schedule(
        "product", {"start_time": "03:00", "weekly_days": ["TUE", "THU"]})
    assert resp["statusCode"] == 200, resp
    kw = table.update_item.call_args.kwargs
    assert kw["UpdateExpression"] == "SET #s.start_time = :t, #s.weekly_days = :d"
    assert kw["ExpressionAttributeValues"] == {":t": "03:00", ":d": ["TUE", "THU"]}


def test_weekly_days_are_refused_on_a_non_weekly_entity(pc):
    """The cadence is Git-owned; a day set on gold-24h would be a config that
    says two things, and the dispatcher would ignore it silently."""
    table = _gold_cfg_table(pc, {"gold_id": "product", "version": "latest",
                                 "schedule": {"frequency": "gold-24h"}})
    resp = pc.set_gold_schedule("product", {"weekly_days": ["TUE"]})
    assert resp["statusCode"] == 400, resp
    assert "gold-weekly" in json.loads(resp["body"])["error"]
    table.update_item.assert_not_called()


@pytest.mark.parametrize("bad", [[], ["FUNDAY"], "TUE", ["TUE", 3], 5, None])
def test_set_weekly_days_is_as_strict_as_the_validator(pc, bad):
    table = _gold_cfg_table(pc, {"gold_id": "product", "version": "latest",
                                 "schedule": {"frequency": "gold-weekly"}})
    resp = pc.set_gold_schedule("product", {"weekly_days": bad})
    assert resp["statusCode"] == 400, resp
    assert "weekly_days" in json.loads(resp["body"])["error"]
    table.update_item.assert_not_called()


def test_schedule_patch_needs_at_least_one_field(pc):
    table = _gold_cfg_table(pc, {"gold_id": "product", "version": "latest",
                                 "schedule": {"frequency": "gold-weekly"}})
    resp = pc.set_gold_schedule("product", {})
    assert resp["statusCode"] == 400, resp
    table.update_item.assert_not_called()


def test_validate_mirrors_weekly_days_rules(pc):
    body = _valid_gold_body()
    body["schedule"] = {"frequency": "gold-24h", "weekly_days": ["TUE"]}
    assert "weekly_days" in pc._validate_gold_config(body)
    body["schedule"] = {"frequency": "gold-weekly", "weekly_days": []}
    assert "weekly_days" in pc._validate_gold_config(body)
    body["schedule"] = {"frequency": "gold-weekly", "weekly_days": ["TUE", "THU"],
                        "start_time": "03:00"}
    assert pc._validate_gold_config(body) is None


def test_validate_rejects_a_bad_schedule_like_gold_config_does(pc):
    body = _valid_gold_body()
    body["schedule"] = {"frequency": "gold-24h", "start_time": ":30"}
    assert "start_time" in pc._validate_gold_config(body)
    body["schedule"] = {"frequency": "fortnightly"}
    assert "frequency" in pc._validate_gold_config(body)
    body["schedule"] = {"frequency": "gold", "start_time": "07:00"}   # legacy alias
    assert pc._validate_gold_config(body) is None


# ---------------------------------------------------------------------------
# POST /gold-pipelines/{gold_id}/run guards (UIFPE-516). Silver's trigger_run
# refuses a disabled pipeline and an in-flight run; gold did neither, and with
# max_concurrent_runs = 10 a second click started a second writer on the same
# Iceberg table. These pin both guards, and that the in-flight check spans BOTH
# gold jobs while keying on --gold_id rather than on the job.

GOLD_RECONCILE_JOB = "uif-echo-dev-gold-reconcile"


def _gold_run_ready(pc, enabled=True):
    pc.GOLD_JOB_NAME = GOLD_JOB
    pc.GOLD_RECONCILE_JOB_NAME = GOLD_RECONCILE_JOB
    table = pc.dynamo.Table.return_value
    table.get_item.return_value = {"Item": {
        "gold_id": "product", "version": "latest", "enabled": enabled, "glue": {},
    }}
    pc.glue.start_job_run.reset_mock()
    pc.glue.start_job_run.return_value = {"JobRunId": "jr_new"}
    pag = pc.glue.get_paginator.return_value.paginate
    pag.side_effect = None
    pag.return_value = [{"JobRuns": []}]
    return table


def _in_flight_on(pc, job_name, gold_id, run_id="jr_running", state="RUNNING"):
    """One in-flight run, on one job, for one entity; every other job is idle."""
    def paginate(JobName, **_):
        if JobName == job_name:
            return [{"JobRuns": [{"Id": run_id, "JobRunState": state,
                                  "Arguments": {"--gold_id": gold_id}}]}]
        return [{"JobRuns": []}]
    pc.glue.get_paginator.return_value.paginate.side_effect = paginate


def test_trigger_gold_run_refuses_a_disabled_entity(pc):
    _gold_run_ready(pc, enabled=False)
    resp = pc.trigger_gold_run("product", {})
    assert resp["statusCode"] == 400, resp
    assert "disabled" in json.loads(resp["body"])["error"]
    pc.glue.start_job_run.assert_not_called()


def test_trigger_gold_run_409_while_a_transform_is_in_flight(pc):
    _gold_run_ready(pc)
    _in_flight_on(pc, GOLD_JOB, "product", run_id="jr_t1")
    resp = pc.trigger_gold_run("product", {})
    assert resp["statusCode"] == 409, resp
    err = json.loads(resp["body"])["error"]
    assert "jr_t1" in err and GOLD_JOB in err          # names the run so it can be found
    pc.glue.start_job_run.assert_not_called()


def test_trigger_gold_run_409_while_a_reconcile_is_in_flight(pc):
    """A reconcile writes the same table: a TRANSFORM must refuse over it."""
    _gold_run_ready(pc)
    _in_flight_on(pc, GOLD_RECONCILE_JOB, "product", run_id="jr_r1")
    resp = pc.trigger_gold_run("product", {})
    assert resp["statusCode"] == 409, resp
    assert "jr_r1" in json.loads(resp["body"])["error"]
    pc.glue.start_job_run.assert_not_called()


def test_trigger_gold_reconcile_409_while_a_transform_is_in_flight(pc):
    """...and the other way round: a RECONCILE refuses over a running transform."""
    _gold_run_ready(pc)
    _in_flight_on(pc, GOLD_JOB, "product", run_id="jr_t2")
    resp = pc.trigger_gold_run("product", {"kind": "reconcile"})
    assert resp["statusCode"] == 409, resp
    pc.glue.start_job_run.assert_not_called()


def test_trigger_gold_run_ignores_another_entitys_run(pc):
    """The check keys on --gold_id, not on the job: price running must not block product."""
    _gold_run_ready(pc)
    _in_flight_on(pc, GOLD_JOB, "price", run_id="jr_price")
    resp = pc.trigger_gold_run("product", {})
    assert resp["statusCode"] == 200, resp
    pc.glue.start_job_run.assert_called_once()
    assert pc.glue.start_job_run.call_args.kwargs["Arguments"]["--gold_id"] == "product"


def test_trigger_gold_run_still_starts_exactly_one_job_when_clear(pc):
    _gold_run_ready(pc)
    resp = pc.trigger_gold_run("product", {})
    assert resp["statusCode"] == 200, resp
    assert json.loads(resp["body"])["run_id"] == "jr_new"
    pc.glue.start_job_run.assert_called_once()


# ── UIFPE-564: the cadence is console-owned ─────────────────────────────────
# Owner decision 2026-09-24. Until now `frequency` lived only in the domain
# YAML; these pin the move, the weekly transitions in both directions, and that
# the response echoes the stored schedule (how the console proves a deployment
# understood the field rather than ignoring it).

def _sched(pc, **schedule):
    return _gold_cfg_table(pc, {"gold_id": "product", "version": "latest",
                                "enabled": True, "schedule": schedule})


def test_set_frequency_alone_updates_only_that_field(pc):
    table = _sched(pc, frequency="gold-24h", start_time="06:30",
                   failure_cooldown_minutes=30)
    resp = pc.set_gold_schedule("product", {"frequency": "gold-4h"})
    assert resp["statusCode"] == 200, resp
    body = json.loads(resp["body"])
    assert body["schedule"]["frequency"] == "gold-4h"
    assert body["schedule"]["start_time"] == "06:30"          # phase anchor kept
    assert body["schedule"]["failure_cooldown_minutes"] == 30
    kw = table.update_item.call_args.kwargs
    assert kw["UpdateExpression"] == "SET #s.frequency = :f"
    assert kw["ExpressionAttributeValues"] == {":f": "gold-4h"}


def test_set_frequency_and_start_time_together(pc):
    table = _sched(pc, frequency="gold-24h", start_time="06:30")
    resp = pc.set_gold_schedule("product", {"frequency": "gold-1h",
                                            "start_time": "00:15"})
    assert resp["statusCode"] == 200, resp
    kw = table.update_item.call_args.kwargs
    # frequency first, then start_time: one fixed field order, so the shape is stable
    assert kw["UpdateExpression"] == "SET #s.frequency = :f, #s.start_time = :t"
    assert kw["ExpressionAttributeValues"] == {":f": "gold-1h", ":t": "00:15"}


@pytest.mark.parametrize("rung", [
    "gold-5m", "gold-10m", "gold-15m", "gold-30m", "gold-1h", "gold-2h",
    "gold-4h", "gold-6h", "gold-8h", "gold-12h", "gold-24h", "gold-weekly"])
def test_every_rung_the_vocabulary_offers_is_accepted(pc, rung):
    """The console's picker offers exactly TRANSFORM_FREQUENCIES; each must be
    storable, including the sub-hourly ones the dialog confirms twice."""
    _sched(pc, frequency="gold-24h", start_time="06:30")
    resp = pc.set_gold_schedule("product", {"frequency": rung})
    assert resp["statusCode"] == 200, resp
    assert json.loads(resp["body"])["schedule"]["frequency"] == rung


def test_the_picker_and_the_handler_agree_on_the_rungs(pc):
    from gold_frequencies import TRANSFORM_FREQUENCIES
    for rung in TRANSFORM_FREQUENCIES:
        _sched(pc, frequency="gold-24h")
        assert pc.set_gold_schedule("product", {"frequency": rung})["statusCode"] == 200


@pytest.mark.parametrize("bad", ["daily", "gold-3h", "hourly", "", None, 5, ["gold-1h"]])
def test_an_unknown_cadence_is_refused_and_names_the_rungs(pc, bad):
    table = _sched(pc, frequency="gold-24h")
    resp = pc.set_gold_schedule("product", {"frequency": bad})
    assert resp["statusCode"] == 400, resp
    err = json.loads(resp["body"])["error"]
    assert "frequency" in err and "gold-24h" in err          # the message lists them
    table.update_item.assert_not_called()


@pytest.mark.parametrize("given,stored", [
    ("gold", "gold-24h"),          # legacy alias
    ("gold-hourly", "gold-1h"),
    ("GOLD-4H", "gold-4h"),        # case
    ("  gold-2h  ", "gold-2h"),    # padding
])
def test_the_stored_cadence_is_canonical(pc, given, stored):
    """Tolerant at the door, canonical in the row: the dispatcher's own
    normalize() must be a no-op on what we write."""
    _sched(pc, frequency="gold-24h")
    resp = pc.set_gold_schedule("product", {"frequency": given})
    assert resp["statusCode"] == 200, resp
    assert json.loads(resp["body"])["schedule"]["frequency"] == stored


def test_moving_to_weekly_seeds_monday_when_there_is_no_day_set(pc):
    """A weekly row with no weekly_days is a config the dispatcher has to guess
    at; the move carries the same default the YAML scaffold uses."""
    table = _sched(pc, frequency="gold-24h", start_time="06:30")
    resp = pc.set_gold_schedule("product", {"frequency": "gold-weekly"})
    assert resp["statusCode"] == 200, resp
    body = json.loads(resp["body"])
    assert body["schedule"]["weekly_days"] == ["MON"]
    kw = table.update_item.call_args.kwargs
    assert kw["UpdateExpression"] == "SET #s.frequency = :f, #s.weekly_days = :d"
    assert kw["ExpressionAttributeValues"] == {":f": "gold-weekly", ":d": ["MON"]}


def test_moving_to_weekly_keeps_a_day_set_the_row_already_had(pc):
    table = _sched(pc, frequency="gold-24h", weekly_days=["TUE", "THU"])
    resp = pc.set_gold_schedule("product", {"frequency": "gold-weekly"})
    assert resp["statusCode"] == 200, resp
    assert json.loads(resp["body"])["schedule"]["weekly_days"] == ["TUE", "THU"]
    kw = table.update_item.call_args.kwargs
    assert kw["UpdateExpression"] == "SET #s.frequency = :f"   # nothing to write


def test_moving_to_weekly_with_days_in_the_same_request(pc):
    table = _sched(pc, frequency="gold-24h", start_time="06:30")
    resp = pc.set_gold_schedule("product", {"frequency": "gold-weekly",
                                            "weekly_days": ["thu", "TUE"],
                                            "start_time": "03:00"})
    assert resp["statusCode"] == 200, resp
    body = json.loads(resp["body"])
    assert body["schedule"]["weekly_days"] == ["TUE", "THU"]   # ISO order, upper
    assert body["schedule"]["start_time"] == "03:00"
    kw = table.update_item.call_args.kwargs
    assert kw["UpdateExpression"] == (
        "SET #s.frequency = :f, #s.start_time = :t, #s.weekly_days = :d")


def test_leaving_weekly_removes_the_stale_day_set(pc):
    """A day set beside a non-weekly cadence fails gold_config.validate at the
    next sync, so it goes with the move rather than being left to rot."""
    table = _sched(pc, frequency="gold-weekly", start_time="06:30",
                   weekly_days=["TUE", "THU"])
    resp = pc.set_gold_schedule("product", {"frequency": "gold-12h"})
    assert resp["statusCode"] == 200, resp
    body = json.loads(resp["body"])
    assert body["schedule"]["frequency"] == "gold-12h"
    assert "weekly_days" not in body["schedule"]
    kw = table.update_item.call_args.kwargs
    assert kw["UpdateExpression"] == "SET #s.frequency = :f REMOVE #s.weekly_days"
    assert kw["ExpressionAttributeValues"] == {":f": "gold-12h"}


def test_leaving_weekly_without_a_day_set_removes_nothing(pc):
    table = _sched(pc, frequency="gold-weekly", start_time="06:30")
    resp = pc.set_gold_schedule("product", {"frequency": "gold-12h"})
    assert resp["statusCode"] == 200, resp
    kw = table.update_item.call_args.kwargs
    assert "REMOVE" not in kw["UpdateExpression"]


def test_days_for_a_row_being_moved_off_weekly_in_the_same_request_are_refused(pc):
    table = _sched(pc, frequency="gold-weekly", weekly_days=["TUE"])
    resp = pc.set_gold_schedule("product", {"frequency": "gold-1h",
                                            "weekly_days": ["WED"]})
    assert resp["statusCode"] == 400, resp
    assert "gold-weekly" in json.loads(resp["body"])["error"]
    table.update_item.assert_not_called()


def test_days_alone_are_accepted_when_the_row_is_already_weekly(pc):
    """The UIFPE-532 path still works: no frequency in the body means the
    stored cadence decides, and it is weekly."""
    _sched(pc, frequency="gold-weekly", weekly_days=["MON"])
    resp = pc.set_gold_schedule("product", {"weekly_days": ["FRI"]})
    assert resp["statusCode"] == 200, resp
    assert json.loads(resp["body"])["schedule"]["weekly_days"] == ["FRI"]


def test_a_row_with_no_schedule_map_can_be_given_a_cadence(pc):
    table = _gold_cfg_table(pc, {"gold_id": "product", "version": "latest"})
    resp = pc.set_gold_schedule("product", {"frequency": "gold-weekly"})
    assert resp["statusCode"] == 200, resp
    kw = table.update_item.call_args.kwargs
    assert kw["UpdateExpression"] == "SET #s = :m"
    assert kw["ExpressionAttributeValues"] == {
        ":m": {"frequency": "gold-weekly", "weekly_days": ["MON"]}}


def test_an_unknown_schedule_field_is_refused(pc):
    """`cadence`, `interval` and `schedule` are the words people reach for; a
    silent ignore would look like a saved change that never happened."""
    table = _sched(pc, frequency="gold-24h")
    for bad in ("cadence", "interval", "frequency_minutes", "enabled"):
        resp = pc.set_gold_schedule("product", {bad: "gold-1h"})
        assert resp["statusCode"] == 400, (bad, resp)
        assert bad in json.loads(resp["body"])["error"]
    table.update_item.assert_not_called()


def test_the_response_echoes_the_whole_stored_schedule(pc):
    """The console compares this echo with what it asked for: a deployment that
    predates UIFPE-564 accepts the request, ignores frequency, and would
    otherwise report a cadence change it never made."""
    _sched(pc, frequency="gold-24h", start_time="06:30",
           failure_cooldown_minutes=30, recompute={"every_n_runs": 8})
    resp = pc.set_gold_schedule("product", {"frequency": "gold-6h"})
    schedule = json.loads(resp["body"])["schedule"]
    assert schedule == {"frequency": "gold-6h", "start_time": "06:30",
                        "failure_cooldown_minutes": 30,
                        "recompute": {"every_n_runs": 8}}


def test_the_cadence_move_is_logged(pc, caplog):
    _sched(pc, frequency="gold-24h", start_time="06:30")
    with caplog.at_level("INFO"):
        pc.set_gold_schedule("product", {"frequency": "gold-2h"})
    assert any("[GOLD SCHEDULE]" in r.message and "frequency" in r.message
               and "gold-24h" in r.message and "gold-2h" in r.message
               for r in caplog.records)


def test_leaving_weekly_logs_the_dropped_day_set(pc, caplog):
    _sched(pc, frequency="gold-weekly", weekly_days=["TUE"])
    with caplog.at_level("INFO"):
        pc.set_gold_schedule("product", {"frequency": "gold-24h"})
    assert any("weekly_days" in r.message and "removed" in r.message
               for r in caplog.records)


def test_a_cadence_change_survives_a_sync(pc):
    """The whole point of the ticket: schedule.frequency is runtime-owned, so
    sync_pipelines carries the console's value over the YAML's."""
    import sync_pipelines as sp
    assert "schedule.frequency" in sp.RUNTIME_PATHS
    merged = sp.preserve_runtime_fields(
        {"gold_id": "product", "schedule": {"frequency": "gold-24h",
                                            "start_time": "06:30"}},
        {"gold_id": "product", "schedule": {"frequency": "gold-2h",
                                            "start_time": "07:00"}})
    assert merged["schedule"]["frequency"] == "gold-2h"
