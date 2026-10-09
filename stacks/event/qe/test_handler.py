import json
import os
import sys
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("EVENT_FABRIC_GLUE_JOB", "uif-event-dev-publish")
os.environ.setdefault("PIPELINE_CONFIGS_TABLE", "uif-event-dev-pipeline-configs")
os.environ.setdefault("PIPELINE_METRICS_TABLE", "uif-event-dev-pipeline-metrics")
os.environ.setdefault("PIPELINE_ID_PREFIX", "event-dev")
os.environ.setdefault("GLUE_DATABASE", "uif_echo_dev_gold_product")
os.environ.setdefault("ATHENA_WORKGROUP", "primary")
os.environ.setdefault("ATHENA_OUTPUT", "s3://uif-echo-dev-gold-artifacts/product-events/athena-results/")

import handler as app  # noqa: E402

# Trigger contract (2026-09-03): the caller announces only
# {"run_id", "changedBy", "domain"}; the pipeline config comes from
# uif-event-<env>-pipeline-configs and the run's op counts are DERIVED from
# the change log via Athena. GoldMergeCompleted funnels into the same flow;
# a bare {"run_id"} without domain stays the raw operator tool.

TRIGGER = {"run_id": "jr_12345", "changedBy": "ops", "domain": "product"}

# Mirrors the seeded event-dev-product config (product_events.tf).
PIPELINE_CONFIG = {
    "pipelineid": "event-dev-product",
    "domain": "product",
    "version": "latest",
    "enabled": True,
    "processor": {
        "glue_job_name": "uif-event-dev-publish",
        "mode_default": "INCREMENTAL",
    },
    "delta": {"table": "PRODUCT_CHANGES", "run_id_col": "run_id", "op_col": "OP"},
    "publish_ops": ["INSERT", "UPDATE", "DELETE"],
    "skip_run_when": {"total_zero": True, "no_publishable_ops": True},
    # Ordered tiers, smallest first; the run takes the FIRST tier whose
    # max_rows covers the derived change-log count (null = catch-all).
    "glue": {
        "max_retries": 0,
        # max_capacity is each tier's python-shell translation (the only
        # memory knob that job type has: 0.0625 = 1GB, 1 = 16GB).
        "sizing_tiers": [
            {"name": "small", "max_rows": 100000, "max_capacity": 0.0625,
             "worker_type": "G.1X", "num_workers": 2, "timeout_minutes": 120},
            {"name": "medium", "max_rows": 500000, "max_capacity": 1,
             "worker_type": "G.2X", "num_workers": 4, "timeout_minutes": 240},
            {"name": "large", "max_rows": None, "max_capacity": 1,
             "worker_type": "G.4X", "num_workers": 6, "timeout_minutes": 600},
        ],
    },
}


def _gold_merge_event(detail=None):
    # Source is the publishing gold Glue job's name. The base detail is
    # DELIBERATELY the legacy shape without goldId: it pins the UIFPE-474
    # fallback (domain doubles as the entity key for pre-474 events). The
    # live announcer sends goldId too - the routed tests below carry it.
    base_detail = {
        "goldRunId": "jr_12345",
        "domain": "product",
        "status": "Success",
    }
    return {
        "version": "0",
        "id": "6a7e8feb-b491-4cf7-a9f1-bf3703467718",
        "detail-type": "GoldMergeCompleted",
        "source": "uif-echo-dev-gold-transform",
        "detail": base_detail if detail is None else detail,
    }


def _athena_counts(counts):
    """get_query_results payload: header row + one (op, n) row per op."""
    rows = [{"Data": [{"VarCharValue": "op"}, {"VarCharValue": "n"}]}]
    for op, n in counts.items():
        rows.append({"Data": [{"VarCharValue": op}, {"VarCharValue": str(n)}]})
    return {"ResultSet": {"Rows": rows}}


@pytest.fixture()
def aws(monkeypatch):
    """Mocked Glue, Athena (25-row run: 6 INSERT + 19 UPDATE), config and
    metrics tables; config seeded, no prior metrics."""
    import copy

    glue = MagicMock()
    glue.start_job_run.return_value = {"JobRunId": "jr-run-1"}
    monkeypatch.setattr(app, "glue", glue)

    athena = MagicMock()
    athena.start_query_execution.return_value = {"QueryExecutionId": "q-1"}
    athena.get_query_execution.return_value = {
        "QueryExecution": {"Status": {"State": "SUCCEEDED"}}
    }
    athena.get_query_results.return_value = _athena_counts({"INSERT": 6, "UPDATE": 19})
    monkeypatch.setattr(app, "athena", athena)

    configs = MagicMock()
    configs.get_item.return_value = {"Item": copy.deepcopy(PIPELINE_CONFIG)}
    monkeypatch.setattr(app, "configs_table", configs)

    metrics = MagicMock()  # pipeline-metrics (Table 2)
    metrics.query.return_value = {"Items": []}  # no prior runs for the gold run
    monkeypatch.setattr(app, "metrics_table", metrics)

    return glue, athena, configs, metrics


def test_config_supplies_domain_args_and_gold_settings(aws):
    """The stack is shared: everything per-domain rides the config record -
    processor.arguments is passed through to StartJobRun (--{key}, run keys
    win) and glue.database / eventing.athena_output steer the Athena delta
    count. Nothing per-domain may come from the Lambda's environment."""
    import copy
    glue, athena, configs, _ = aws
    config = copy.deepcopy(PIPELINE_CONFIG)
    # 2026-09-24 shape: database under glue, athena_output under eventing.
    config["glue"]["database"] = "uif_echo_dev_gold_price"
    config.setdefault("eventing", {})["athena_output"] =         "s3://bkt/event-pipeline/athena-results/"
    config["processor"]["arguments"] = {
        "schema_name": "price",
        "event_bus_name": "uif-event-dev-price",
        "--mapping_key": "event-pipeline/domains/price/price.mapping.json",
        "run_id": "must-not-override",  # run-specific keys always win
    }
    configs.get_item.return_value = {"Item": config}

    app.lambda_handler({"run_id": "jr_p1", "changedBy": "ops",
                        "domain": "price"}, None)

    q = athena.start_query_execution.call_args.kwargs
    assert '"uif_echo_dev_gold_price"' in q["QueryString"]
    assert q["ResultConfiguration"]["OutputLocation"] == \
        "s3://bkt/event-pipeline/athena-results/"
    args = glue.start_job_run.call_args.kwargs["Arguments"]
    assert args["--schema_name"] == "price"
    assert args["--event_bus_name"] == "uif-event-dev-price"
    assert args["--mapping_key"] == "event-pipeline/domains/price/price.mapping.json"
    assert args["--run_id"] == "jr_p1"  # config could not override it


def test_retired_gold_section_still_resolves(aws):
    """Config rows and the handler can deploy in either order: a row still
    carrying the retired top-level `gold` section keeps working until its
    domain re-applies with the reshaped seed."""
    import copy
    _, athena, configs, _ = aws
    config = copy.deepcopy(PIPELINE_CONFIG)
    config.pop("glue", None) or None
    config["glue"] = {"sizing_tiers": PIPELINE_CONFIG["glue"]["sizing_tiers"]}
    config["gold"] = {"glue_database": "uif_echo_dev_gold_legacy",
                      "athena_output": "s3://bkt/legacy-results/"}
    configs.get_item.return_value = {"Item": config}

    app.lambda_handler(dict(TRIGGER), None)

    q = athena.start_query_execution.call_args.kwargs
    assert '"uif_echo_dev_gold_legacy"' in q["QueryString"]
    assert q["ResultConfiguration"]["OutputLocation"] == "s3://bkt/legacy-results/"


def test_run_announcement_derives_summary_and_starts(aws):
    glue, athena, configs, metrics = aws

    result = app.lambda_handler(dict(TRIGGER), None)

    configs.get_item.assert_called_once_with(
        Key={"pipelineid": "event-dev-product", "version": "latest"}
    )
    q = athena.start_query_execution.call_args.kwargs["QueryString"]
    assert 'FROM "uif_echo_dev_gold_product"."product_changes"' in q
    assert '"op" AS op' in q
    assert "\"run_id\" = 'jr_12345'" in q
    # 25 rows <= small's max_rows (100k) -> the first tier wins.
    glue.start_job_run.assert_called_once_with(
        JobName="uif-event-dev-publish",
        Arguments={
            "--run_id": "jr_12345",
            "--changed_by": "ops",
            "--changes_table": "product_changes",
        },
        Timeout=120,
        WorkerType="G.1X",
        NumberOfWorkers=2,
    )
    assert result["status"] == "started"
    assert result["sizingTier"] == "small"
    assert result["deltaSummary"] == {"total": 25, "insert": 6, "update": 19,
                                      "delete": 0, "refresh": 0}
    assert result["metricsRecorded"] is True

    item = metrics.put_item.call_args.kwargs["Item"]
    assert item["event_run_id"] == "jr-run-1"  # Table 2 keys on the JobRunId
    assert item["mode"] == "INCREMENTAL"
    assert item["gold_run_id"] == "jr_12345"
    assert item["operation"] == "OVERALL"
    assert item["status"] == "IN_PROGRESS"
    assert item["source_row_count"] == 25
    assert item["worker_type"] == "G.1X"
    assert item["replay_count"] == 0
    assert item["started_by"] == "ops"  # a manual announcement carries the caller


def test_mid_range_count_uses_medium_tier(aws):
    glue, athena, _, _ = aws
    athena.get_query_results.return_value = _athena_counts(
        {"INSERT": 12000, "UPDATE": 235000, "DELETE": 3000})

    result = app.lambda_handler(dict(TRIGGER), None)

    kwargs = glue.start_job_run.call_args.kwargs
    assert kwargs["WorkerType"] == "G.2X" and kwargs["NumberOfWorkers"] == 4
    assert kwargs["Timeout"] == 240
    assert result["sizingTier"] == "medium"
    assert result["deltaSummary"]["total"] == 250000


def test_count_beyond_every_bound_uses_the_null_catch_all(aws):
    glue, athena, _, _ = aws
    athena.get_query_results.return_value = _athena_counts({"UPDATE": 750000})

    result = app.lambda_handler(dict(TRIGGER), None)

    kwargs = glue.start_job_run.call_args.kwargs
    assert kwargs["WorkerType"] == "G.4X" and kwargs["NumberOfWorkers"] == 6
    assert kwargs["Timeout"] == 600
    assert result["sizingTier"] == "large"


def test_tier_boundary_is_inclusive(aws):
    """Exactly max_rows rows still fit the tier (<=, not <)."""
    glue, athena, _, _ = aws
    athena.get_query_results.return_value = _athena_counts({"UPDATE": 100000})

    result = app.lambda_handler(dict(TRIGGER), None)

    assert result["sizingTier"] == "small"
    assert glue.start_job_run.call_args.kwargs["WorkerType"] == "G.1X"


def test_legacy_two_tier_config_still_resolves(aws):
    """Domains migrate their config rows on their own schedule - the old
    incremental/large_run shape keeps working until they do."""
    import copy
    glue, athena, configs, _ = aws
    config = copy.deepcopy(PIPELINE_CONFIG)
    config["glue"] = {
        "incremental": {"worker_type": "G.2X", "num_workers": 4, "timeout_minutes": 240},
        "large_run": {"worker_type": "G.4X", "num_workers": 6, "timeout_minutes": 600},
        "large_run_threshold_rows": 200000,
    }
    configs.get_item.return_value = {"Item": config}
    athena.get_query_results.return_value = _athena_counts({"UPDATE": 250000})

    result = app.lambda_handler(dict(TRIGGER), None)

    assert result["sizingTier"] == "large_run"
    assert glue.start_job_run.call_args.kwargs["WorkerType"] == "G.4X"


def test_zero_rows_in_change_log_is_skipped(aws):
    glue, athena, _, metrics = aws
    athena.get_query_results.return_value = _athena_counts({})

    result = app.lambda_handler(dict(TRIGGER), None)

    glue.start_job_run.assert_not_called()
    metrics.put_item.assert_not_called()
    assert result["status"] == "skipped"
    assert "total_zero" in result["reason"]


def test_refresh_only_run_is_skipped_when_the_config_does_not_opt_in(aws):
    """REFRESH is publishable but OPT-IN: this fixture's publish_ops is
    [INSERT, UPDATE, DELETE], so a refresh-only run still skips. What changed
    is that a domain can now opt in at all - see the test below."""
    glue, athena, _, _ = aws
    athena.get_query_results.return_value = _athena_counts({"REFRESH": 500})

    result = app.lambda_handler(dict(TRIGGER), None)

    glue.start_job_run.assert_not_called()
    assert result["status"] == "skipped"
    assert "no publishable operations" in result["reason"]


def test_refresh_only_run_publishes_when_the_config_opts_in(aws):
    """A targeted refresh in emit mode appends real op=REFRESH change-log
    rows, and for a column that is only detectable that way it is the ONLY
    source of change. With REFRESH in publish_ops the run must start, not
    skip - before this, no_publishable_ops starved all three channels."""
    glue, athena, configs, metrics = aws
    configs.get_item.return_value = {"Item": dict(
        PIPELINE_CONFIG,
        publish_ops=["INSERT", "UPDATE", "DELETE", "REFRESH"])}
    athena.get_query_results.return_value = _athena_counts({"REFRESH": 500})

    result = app.lambda_handler(dict(TRIGGER), None)

    glue.start_job_run.assert_called_once()
    metrics.put_item.assert_called_once()
    assert result["status"] == "started"
    assert result["deltaSummary"]["refresh"] == 500


def test_refresh_rows_are_counted_under_their_own_summary_key(aws):
    """OP_SUMMARY_KEYS must carry REFRESH, or the count lands in `total`
    only and no_publishable_ops can never see it."""
    assert app.OP_SUMMARY_KEYS.get("REFRESH") == "refresh"
    glue, athena, configs, _ = aws
    configs.get_item.return_value = {"Item": dict(
        PIPELINE_CONFIG, publish_ops=["REFRESH"])}
    athena.get_query_results.return_value = _athena_counts(
        {"REFRESH": 7, "UPDATE": 3})

    result = app.lambda_handler(dict(TRIGGER), None)

    assert result["deltaSummary"]["refresh"] == 7
    assert result["deltaSummary"]["total"] == 10
    # opts in on REFRESH alone, so the run starts on refresh rows only
    assert result["status"] == "started"
    glue.start_job_run.assert_called_once()


def test_disabled_pipeline_skips_before_athena(aws):
    glue, athena, configs, _ = aws
    configs.get_item.return_value = {"Item": dict(PIPELINE_CONFIG, enabled=False)}

    result = app.lambda_handler(dict(TRIGGER), None)

    athena.start_query_execution.assert_not_called()
    glue.start_job_run.assert_not_called()
    assert result["status"] == "skipped"
    assert "disabled" in result["reason"]


def test_missing_pipeline_config_raises(aws):
    glue, _, configs, _ = aws
    configs.get_item.return_value = {}
    with pytest.raises(ValueError, match="No pipeline config event-dev-product/latest"):
        app.lambda_handler(dict(TRIGGER), None)
    glue.start_job_run.assert_not_called()


def test_athena_failure_raises(aws):
    glue, athena, _, _ = aws
    athena.get_query_execution.return_value = {
        "QueryExecution": {"Status": {"State": "FAILED", "StateChangeReason": "boom"}}
    }
    with pytest.raises(RuntimeError, match="FAILED: boom"):
        app.lambda_handler(dict(TRIGGER), None)
    glue.start_job_run.assert_not_called()


def test_gold_merge_event_funnels_into_same_flow(aws):
    glue, athena, configs, metrics = aws

    result = app.lambda_handler(_gold_merge_event(), None)

    configs.get_item.assert_called_once()
    athena.start_query_execution.assert_called_once()
    args = glue.start_job_run.call_args.kwargs["Arguments"]
    assert args["--changed_by"] == "uif-echo-dev-gold-transform"
    assert args["--changes_table"] == "product_changes"
    assert result["status"] == "started"
    assert metrics.put_item.called


def test_non_success_merge_skipped_before_config(aws):
    glue, _, configs, _ = aws
    detail = {"goldRunId": "jr_12345", "domain": "product", "status": "Failed"}

    result = app.lambda_handler(_gold_merge_event(detail), None)

    configs.get_item.assert_not_called()
    glue.start_job_run.assert_not_called()
    assert result["status"] == "skipped"
    assert "Failed" in result["reason"]


def test_string_detail_is_parsed(aws):
    event = _gold_merge_event()
    event["detail"] = json.dumps(event["detail"])
    result = app.lambda_handler(event, None)
    assert result["status"] == "started"
    assert result["runId"] == "jr_12345"


def test_manual_reannouncement_counts_prior_attempts_as_replays(aws):
    """Manual announcements bypass the idempotency check (they ARE the
    operator replay tool) and carry the prior-attempt count."""
    glue, _, _, metrics = aws
    glue.start_job_run.return_value = {"JobRunId": "jr-run-9"}
    metrics.query.return_value = {"Items": [
        {"event_run_id": "jr-run-1", "mode": "INCREMENTAL", "status": "SUCCEEDED"},
    ]}

    result = app.lambda_handler(dict(TRIGGER), None)

    assert result["status"] == "started"
    item = metrics.put_item.call_args.kwargs["Item"]
    assert item["event_run_id"] == "jr-run-9"
    assert item["replay_count"] == 1


def test_duplicate_gold_merge_delivery_is_skipped(aws):
    """Event deliveries are idempotency-checked on Table 2's gsi_gold_run:
    an IN_PROGRESS / STARTED / SUCCEEDED processor row means a duplicate
    GoldMergeCompleted must not double-process the gold run."""
    glue, athena, _, metrics = aws
    metrics.query.return_value = {"Items": [
        {"event_run_id": "jr-run-1", "mode": "INCREMENTAL", "status": "SUCCEEDED"},
    ]}

    result = app.lambda_handler(_gold_merge_event(), None)

    glue.start_job_run.assert_not_called()
    athena.start_query_execution.assert_not_called()
    assert result["status"] == "skipped"
    assert "duplicate GoldMergeCompleted" in result["reason"]


def test_gold_merge_after_failed_attempt_runs_again(aws):
    """A FAILED prior attempt does not block the event path - only
    active/succeeded rows do."""
    glue, _, _, metrics = aws
    metrics.query.return_value = {"Items": [
        {"event_run_id": "jr-run-1", "mode": "INCREMENTAL", "status": "FAILED"},
    ]}

    result = app.lambda_handler(_gold_merge_event(), None)

    assert result["status"] == "started"
    item = metrics.put_item.call_args.kwargs["Item"]
    assert item["replay_count"] == 1
    assert item["started_by"] == "SYSTEM"  # event-driven runs are the system's


def test_python_shell_job_falls_back_without_worker_sizing(aws):
    glue, _, _, metrics = aws
    glue.start_job_run.side_effect = [
        ClientError({"Error": {"Code": "InvalidInputException",
                               "Message": "WorkerType is not supported"}}, "StartJobRun"),
        {"JobRunId": "jr-run-2"},
    ]

    result = app.lambda_handler(dict(TRIGGER), None)

    assert result["jobRunId"] == "jr-run-2"
    retry_kwargs = glue.start_job_run.call_args.kwargs
    assert "WorkerType" not in retry_kwargs
    assert retry_kwargs["Timeout"] == 120  # the small tier's timeout
    # Option A (2026-09-25): the tier's python-shell translation rides the
    # retry, so the run gets REAL memory - the 511k-row OOM ran at the 1GB
    # job default because the old fallback dropped sizing entirely.
    assert retry_kwargs["MaxCapacity"] == 0.0625
    # The sizing INTENT still lands in metrics even when Glue rejected it.
    assert metrics.put_item.call_args.kwargs["Item"]["worker_type"] == "G.1X"


def test_metrics_failure_does_not_fail_started_run(aws):
    _, _, _, metrics = aws
    metrics.put_item.side_effect = RuntimeError("throttled")
    result = app.lambda_handler(dict(TRIGGER), None)
    assert result["status"] == "started"
    assert result["metricsRecorded"] is False


def test_raw_payload_without_domain_stays_configless(aws):
    glue, athena, configs, metrics = aws
    glue.start_job_run.return_value = {"JobRunId": "jr-run-3"}

    result = app.lambda_handler({"run_id": "jr_manual", "changedBy": "ops"}, None)

    glue.start_job_run.assert_called_once_with(
        JobName=os.environ["EVENT_FABRIC_GLUE_JOB"],
        Arguments={"--run_id": "jr_manual", "--changed_by": "ops"},
    )
    configs.get_item.assert_not_called()
    athena.start_query_execution.assert_not_called()
    metrics.put_item.assert_not_called()
    assert result == {
        "runId": "jr_manual",
        "glueJob": os.environ["EVENT_FABRIC_GLUE_JOB"],
        "jobRunId": "jr-run-3",
        "status": "started",
    }


def test_gold_merge_for_unseeded_domain_skips_cleanly(aws):
    """The bus rule matches planned domains (price/customer) before their
    pipeline configs exist - an event-path trigger must skip visibly, never
    raise (raising burns EventBridge's retries and dead-letters the trigger
    as junk). Manual announcements for the same domain still raise."""
    glue, _, configs, _ = aws
    configs.get_item.return_value = {}  # no config item for this domain
    detail = {"goldRunId": "jr_12345", "goldId": "price",
              "domain": "price", "status": "Success"}

    result = app.lambda_handler(_gold_merge_event(detail), None)

    assert result["status"] == "skipped"
    assert "no pipeline config for gold entity 'price'" in result["reason"]
    glue.start_job_run.assert_not_called()

    with pytest.raises(ValueError, match="No pipeline config"):
        app.lambda_handler({"run_id": "jr_12345", "domain": "price",
                            "changedBy": "ops@pearson.com"}, None)
    glue.start_job_run.assert_not_called()


def test_gold_merge_without_domain_raises(aws):
    glue, _, _, _ = aws
    detail = {"goldRunId": "jr_12345", "status": "Success"}
    with pytest.raises(ValueError, match="domain missing"):
        app.lambda_handler(_gold_merge_event(detail), None)
    glue.start_job_run.assert_not_called()


def test_missing_run_id_raises(aws):
    glue, _, _, _ = aws
    with pytest.raises(ValueError, match="neither a GoldMergeCompleted"):
        app.lambda_handler({"domain": "product"}, None)
    glue.start_job_run.assert_not_called()


def test_non_object_payload_raises(aws):
    glue, _, _, _ = aws
    with pytest.raises(ValueError, match="JSON object payload"):
        app.lambda_handler(["run_id"], None)
    glue.start_job_run.assert_not_called()


def test_run_id_quotes_escaped_in_athena_query(aws):
    _, athena, _, _ = aws
    app.lambda_handler(dict(TRIGGER, run_id="it's"), None)
    q = athena.start_query_execution.call_args.kwargs["QueryString"]
    assert "= 'it''s'" in q


def test_gold_merge_legacy_run_id_still_accepted(aws):
    """Older publishers (and replays of old events) send detail.run_id."""
    detail = {"run_id": "jr_legacy", "domain": "product", "status": "Success",
              "glue_job_name": "event-dev-gold-transform"}
    result = app.lambda_handler(_gold_merge_event(detail), None)
    assert result["status"] == "started"
    assert result["runId"] == "jr_legacy"


def test_gold_merge_without_goldrunid_raises(aws):
    glue, _, _, _ = aws
    with pytest.raises(ValueError, match="goldRunId missing"):
        app.lambda_handler(_gold_merge_event({"domain": "product", "status": "Success"}), None)
    glue.start_job_run.assert_not_called()


def test_gold_merge_routes_by_gold_id_not_domain(aws):
    """UIFPE-474's whole point: marketing_copy lives in product's DOMAIN but
    must load its OWN config row. A domain-keyed lookup would silently hand it
    product's tier, job and skip rules."""
    glue, athena, configs, metrics = aws
    detail = {"goldRunId": "jr_12345", "goldId": "marketing_copy",
              "domain": "product", "status": "Success"}

    result = app.lambda_handler(_gold_merge_event(detail), None)

    key = configs.get_item.call_args.kwargs["Key"]
    assert key["pipelineid"] == "event-dev-marketing_copy", key
    assert result["goldId"] == "marketing_copy"
    assert result["domain"] == "product"


def test_legacy_event_without_gold_id_falls_back_to_domain(aws):
    """A pre-474 event carries no goldId; domain named the same entity for
    every publisher that existed then, so it keys the lookup - loudly."""
    glue, athena, configs, metrics = aws

    result = app.lambda_handler(_gold_merge_event(), None)  # base = no goldId

    key = configs.get_item.call_args.kwargs["Key"]
    assert key["pipelineid"] == "event-dev-product", key
    assert result["status"] == "started"


def test_manual_announcement_accepts_gold_id(aws):
    glue, athena, configs, metrics = aws

    result = app.lambda_handler(
        {"run_id": "jr_12345", "changedBy": "ops", "goldId": "marketing_copy"}, None)

    key = configs.get_item.call_args.kwargs["Key"]
    assert key["pipelineid"] == "event-dev-marketing_copy", key
    assert result["goldId"] == "marketing_copy"


def test_manual_domain_only_payload_still_works(aws):
    """The historical operator payload - {"run_id", "domain"} - keeps working:
    domain keys the lookup exactly as before the re-key."""
    glue, athena, configs, metrics = aws

    result = app.lambda_handler(
        {"run_id": "jr_12345", "changedBy": "ops", "domain": "product"}, None)

    key = configs.get_item.call_args.kwargs["Key"]
    assert key["pipelineid"] == "event-dev-product", key
    assert result["status"] == "started"


def test_python_shell_fallback_without_tier_capacity_stays_timeout_only(aws):
    """A tier that declares no max_capacity keeps the old behaviour - the
    retry carries Timeout only (config rows migrate on their own schedule)."""
    import copy
    glue, _, configs, _ = aws
    config = copy.deepcopy(PIPELINE_CONFIG)
    for tier in config["glue"]["sizing_tiers"]:
        tier.pop("max_capacity", None)
    configs.get_item.return_value = {"Item": config}
    glue.start_job_run.side_effect = [
        ClientError({"Error": {"Code": "InvalidInputException",
                               "Message": "WorkerType is not supported"}}, "StartJobRun"),
        {"JobRunId": "jr-run-2"},
    ]

    app.lambda_handler(dict(TRIGGER), None)

    retry_kwargs = glue.start_job_run.call_args.kwargs
    assert "MaxCapacity" not in retry_kwargs
    assert "WorkerType" not in retry_kwargs


def test_large_tier_translates_to_full_capacity(aws):
    """The 2026-09-24 OOM scenario: >500k rows picks large, and the
    python-shell retry runs it at MaxCapacity 1 (16GB)."""
    glue, athena, _, metrics = aws
    athena.get_query_results.return_value = _athena_counts({"UPDATE": 511734})
    glue.start_job_run.side_effect = [
        ClientError({"Error": {"Code": "InvalidInputException",
                               "Message": "WorkerType is not supported"}}, "StartJobRun"),
        {"JobRunId": "jr-run-2"},
    ]

    result = app.lambda_handler(dict(TRIGGER), None)

    assert result["sizingTier"] == "large"
    retry_kwargs = glue.start_job_run.call_args.kwargs
    assert retry_kwargs["MaxCapacity"] == 1
    assert retry_kwargs["Timeout"] == 600
    # The intent (G.4X) still lands in metrics for the ops view.
    assert metrics.put_item.call_args.kwargs["Item"]["worker_type"] == "G.4X"
