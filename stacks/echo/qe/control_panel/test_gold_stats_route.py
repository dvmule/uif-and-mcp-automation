# test_gold_stats_route.py
# UIFPE-544 PR 2: GET /gold-stats, the gold fleet Overview.
#
# The route has two halves with two sources, and the tests hold both honest:
# the live half is computed from the rows this Lambda already reads, and the
# 30-day half is READ from one file the nightly Glue job writes. The arithmetic
# itself is tested in qe/gold/test_gold_stats.py; what is pinned here is the
# plumbing - that a missing file degrades loudly instead of rendering as zeros,
# that `days` cannot ask for more than the file holds, that the metrics read is
# a query and not a table scan, and that the route needs no Athena grant.

import importlib
import io
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import pytest

STACK = Path(__file__).resolve().parents[2]
CONTROLLER_SRC = (STACK / "src/apps/control_panel/pipeline_controller.py").read_text(encoding="utf-8")
LAMBDA_TF = (STACK / "infrastructure/terraform/lambda.tf").read_text(encoding="utf-8")
APIGW_TF = (STACK / "infrastructure/terraform/apigateway.tf").read_text(encoding="utf-8")
STAGE_SH = (STACK / "scripts/stage-artifacts.sh").read_text(encoding="utf-8")

ENV = {
    "GOLD_CONFIG_TABLE": "uif-echo-dev-gold-pipeline-config",
    "GOLD_ARTIFACTS_BUCKET": "uif-echo-dev-gold-artifacts",
    "GOLD_JOB_NAME": "uif-echo-dev-gold-transform",
    "METRICS_TABLE": "uif-echo-dev-glue-job-metrics",
}

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)


class FrozenDatetime(datetime):
    """The route stamps its own now(); without freezing it, every freshness and
    age assertion here would drift with the wall clock."""

    @classmethod
    def now(cls, tz=None):
        return NOW if tz else NOW.replace(tzinfo=None)


def config_row(gold_id, frequency="gold-24h", transform_at=None, enabled=True):
    return {"gold_id": gold_id, "version": "latest", "enabled": enabled,
            "schedule": {"frequency": frequency},
            "metadata": {"last_transform_at": transform_at} if transform_at else {}}


def metrics_row(gold_id, started_at, status="SUCCEEDED", rows_written=0):
    return {"job_name": ENV["GOLD_JOB_NAME"], "run_id": f"jr_{gold_id}_{started_at}",
            "gold_id": gold_id, "status": status, "started_at": started_at,
            "rows_written": rows_written}


def summary_file(days=("2026-09-22", "2026-09-23"), generated_at=None):
    return {
        "schema_version": 1,
        "generated_at": (generated_at or NOW).isoformat(),
        "days": 30,
        "fleet": [{"day": d, "entities": 2, "runs": 2, "failed": 0,
                   "rows_written": 10, "fresh": 2} for d in days],
        "entities": {"product": [{"day": d, "runs": 1, "failed": 0,
                                  "rows_written": 5, "fresh": True} for d in days]},
    }


@pytest.fixture
def pc():
    with mock.patch("boto3.resource"), mock.patch("boto3.client"), mock.patch.dict(os.environ, ENV):
        sys.modules.pop("pipeline_controller", None)
        module = importlib.import_module("pipeline_controller")
    with mock.patch.dict(os.environ, ENV), \
            mock.patch.object(module, "datetime", FrozenDatetime):
        yield module
    sys.modules.pop("pipeline_controller", None)


class FakeMetrics:
    """Records how it was read: the route must QUERY the job partition, never
    scan a table that also holds every bronze and silver run."""

    def __init__(self, rows):
        self.rows = rows
        self.queries, self.scans = [], 0

    def query(self, **kwargs):
        self.queries.append(kwargs)
        return {"Items": [dict(r) for r in self.rows]}

    def scan(self, **kwargs):
        self.scans += 1
        return {"Items": [dict(r) for r in self.rows]}


@pytest.fixture
def store(pc):
    state = {"configs": [], "metrics": FakeMetrics([]), "summary": summary_file(),
             "s3_error": None, "s3_calls": []}

    config = mock.MagicMock()
    config.scan.side_effect = lambda **_: {"Items": [dict(r) for r in state["configs"]]}

    def table(name):
        return state["metrics"] if name == ENV["METRICS_TABLE"] else config
    pc.dynamo = mock.MagicMock()
    pc.dynamo.Table.side_effect = table

    def get_object(Bucket, Key):
        state["s3_calls"].append((Bucket, Key))
        if state["s3_error"]:
            raise state["s3_error"]
        return {"Body": io.BytesIO(json.dumps(state["summary"]).encode("utf-8"))}
    pc.s3_client = mock.MagicMock()
    pc.s3_client.get_object.side_effect = get_object
    return state


def call(pc, params=None):
    event = {"requestContext": {"http": {"method": "GET", "path": "/gold-stats"}},
             "rawPath": "/gold-stats", "queryStringParameters": params or {}}
    resp = pc.handler(event, None)
    return resp["statusCode"], json.loads(resp["body"])


# ── the live half ───────────────────────────────────────────────────────────

def test_it_answers_with_both_halves(pc, store):
    store["configs"] = [config_row("product", transform_at=(NOW - timedelta(hours=2)).isoformat())]
    store["metrics"] = FakeMetrics([metrics_row("product", (NOW - timedelta(hours=2)).isoformat(),
                                                rows_written=7)])
    status, body = call(pc)
    assert status == 200, body
    assert body["live"]["entities"] == 1 and body["live"]["succeeded"] == 1
    assert body["live"]["rows_written"] == 7
    assert body["trend_available"] is True
    assert [p["day"] for p in body["trend"]["fleet"]] == ["2026-09-22", "2026-09-23"]


def test_the_live_half_counts_every_entity_including_the_disabled_ones(pc, store):
    """The fleet's denominator must not move when someone pauses a table."""
    store["configs"] = [config_row("product"), config_row("price", enabled=False)]
    _status, body = call(pc)
    assert body["live"]["entities"] == 2
    assert body["live"]["enabled"] == 1


def test_both_reads_follow_their_pages(pc, store):
    """A truncated read here is a wrong fleet COUNT, which is exactly the kind
    of silent wrongness this Story exists to remove."""
    pages = {"configs": [[config_row("product")], [config_row("price")]],
             "metrics": [[metrics_row("product", NOW.isoformat())],
                         [metrics_row("price", NOW.isoformat())]]}

    def paged(kind):
        def scan_or_query(**kwargs):
            n = 1 if kwargs.get("ExclusiveStartKey") else 0
            resp = {"Items": [dict(r) for r in pages[kind][n]]}
            if n == 0:
                resp["LastEvaluatedKey"] = {"page": 1}
            return resp
        return scan_or_query

    config = mock.MagicMock()
    config.scan.side_effect = paged("configs")
    metrics = mock.MagicMock()
    metrics.query.side_effect = paged("metrics")
    pc.dynamo.Table.side_effect = lambda name: (
        metrics if name == ENV["METRICS_TABLE"] else config)

    _status, body = call(pc)
    assert body["live"]["entities"] == 2, "the second page of configs was dropped"
    assert body["live"]["runs"] == 2, "the second page of runs was dropped"


def test_it_queries_the_job_partition_and_never_scans_the_metrics_table(pc, store):
    """The metrics table holds every bronze and silver run too; a scan here is
    the 20-second page load UIFPE-565 was raised for."""
    store["metrics"] = FakeMetrics([metrics_row("product", NOW.isoformat())])
    call(pc)
    assert store["metrics"].scans == 0
    assert len(store["metrics"].queries) == 1


def test_the_cadence_reaches_the_freshness_judgement(pc, store):
    """An hourly table two hours stale is behind; a daily one is not. Without
    the cadence resolved to minutes both would read the same."""
    two_hours_ago = (NOW - timedelta(hours=2)).isoformat()
    store["configs"] = [config_row("price", frequency="gold-1h", transform_at=two_hours_ago),
                        config_row("product", frequency="gold-24h", transform_at=two_hours_ago)]
    _status, body = call(pc)
    assert body["live"]["stale_entities"] == ["price"]
    assert body["live"]["fresh"] == 1


# ── the pre-computed half ───────────────────────────────────────────────────

def test_it_reads_the_summary_from_the_artifacts_bucket_at_the_library_key(pc, store):
    import gold_stats
    call(pc)
    assert store["s3_calls"] == [(ENV["GOLD_ARTIFACTS_BUCKET"], gold_stats.SUMMARY_KEY)]


def test_a_missing_summary_is_said_out_loud_not_rendered_as_zeros(pc, store):
    """On the day this ships the nightly job has not run. A flat line at zero
    would read as an outage; `trend_available: false` reads as what it is."""
    store["s3_error"] = RuntimeError("NoSuchKey")
    status, body = call(pc)
    assert status == 200, body
    assert body["trend"] is None
    assert body["trend_available"] is False
    assert "NoSuchKey" in body["trend_unavailable_reason"]
    # The live half still answers: one missing file is not an outage.
    assert body["live"]["entities"] == 0


def test_an_unreadable_summary_does_not_take_the_page_down(pc, store):
    store["s3_error"] = ValueError("not json")
    status, body = call(pc)
    assert status == 200
    assert body["trend_available"] is False


def test_it_says_how_old_the_pre_computed_half_is(pc, store):
    store["summary"] = summary_file(generated_at=NOW - timedelta(hours=40))
    _status, body = call(pc)
    assert body["trend_age_minutes"] is not None
    assert body["trend_age_minutes"] >= 40 * 60 - 1
    assert body["trend_generated_at"] == (NOW - timedelta(hours=40)).isoformat()


def test_days_trims_to_the_newest_days_asked_for(pc, store):
    store["summary"] = summary_file(days=("2026-09-20", "2026-09-21",
                                          "2026-09-22", "2026-09-23"))
    _status, body = call(pc, {"days": "2"})
    assert [p["day"] for p in body["trend"]["fleet"]] == ["2026-09-22", "2026-09-23"]
    assert body["trend"]["days"] == 2
    # The entity series is cut by the same days, so the totals agree.
    assert [p["day"] for p in body["trend"]["entities"]["product"]] == \
        ["2026-09-22", "2026-09-23"]


def test_days_cannot_ask_for_more_than_the_file_holds(pc, store):
    import gold_stats
    _status, body = call(pc, {"days": "9999"})
    assert body["trend"]["days"] == gold_stats.SUMMARY_DAYS


def test_a_nonsense_days_value_falls_back_instead_of_failing(pc, store):
    import gold_stats
    for value in ("", "abc", "-4", None):
        status, body = call(pc, {"days": value} if value is not None else {})
        assert status == 200, (value, body)
        assert body["trend"]["days"] in (1, gold_stats.SUMMARY_DAYS)


# ── the wiring ──────────────────────────────────────────────────────────────

def test_the_route_is_reachable_through_the_api():
    block = re.search(r'resource "aws_apigatewayv2_route" "gold_stats_overview" \{.*?\n\}',
                      APIGW_TF, re.S)
    assert block, "no API Gateway route: the handler would never be reached"
    assert 'route_key          = "GET /gold-stats"' in block.group(0)
    assert "authorization_type = local.authorizer_id != null" in block.group(0)


def test_the_controller_ships_the_libraries_it_now_imports():
    """gold_stats imports its freshness rule from gold_telemetry, so both go in
    the zip - one without the other is an ImportError at INIT.

    BOTH assembly paths, because there are two: lambda.tf's archive_file is the
    Terraform one and stage-artifacts.sh is the apps deploy one. I updated only
    the first, and qe/pipeline's artifact-set gate caught it in CI."""
    zip_block = re.search(r'data "archive_file" "pipeline_controller" \{.*?\n\}\n',
                          LAMBDA_TF, re.S).group(0)
    staged = re.search(r"lambda_zip pipeline_controller \\\n(.*?)\nlambda_zip ",
                       STAGE_SH, re.S).group(1)
    for library in ("gold_stats.py", "gold_telemetry.py"):
        assert f'filename = "{library}"' in zip_block, f"{library} is not in the zip"
        assert f"src/libs/{library}" in zip_block
        assert f"/{library}" in staged, f"{library} is not staged by the deploy script"


def test_the_route_needs_no_athena_grant():
    """The design choice, pinned: the pre-computed file exists so this Lambda
    never queries Iceberg. An athena client appearing here is a widening of the
    role's blast radius, not a refactor."""
    assert not re.search(r"""boto3\.client\(\s*['"]athena""", CONTROLLER_SRC)
    assert "start_query_execution" not in CONTROLLER_SRC
    assert "get_query_results" not in CONTROLLER_SRC


def test_the_summary_is_read_with_one_get_object():
    body = re.search(r"def _gold_stats_summary\(.*?\n\ndef ", CONTROLLER_SRC, re.S).group(0)
    assert "s3_client.get_object" in body
    assert "gold_stats.SUMMARY_KEY" in body
    assert "GOLD_ARTIFACTS_BUCKET" in body
