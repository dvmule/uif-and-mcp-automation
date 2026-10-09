# test_home_summary.py
# Unit tests for /home/summary and /home/recent-activity in pipeline_controller.
# moto stubs DynamoDB so we can exercise the aggregation logic without AWS.

import json
import os
import sys
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import boto3
from moto import mock_aws

# Stub heavy/optional deps before importing the controller
sys.modules.setdefault("oracledb", MagicMock())

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "lambda"))

# Fix region BEFORE moto's mock_aws decorator runs so the module-level boto3
# clients in pipeline_controller bind to the moto-mocked endpoint.
os.environ["AWS_DEFAULT_REGION"] = "eu-west-1"
os.environ["REGION"] = "eu-west-1"
os.environ["METRICS_TABLE"] = "echo-test-glue-job-metrics"
os.environ["EXPORT_METRICS_TABLE"] = "partner_export_metrics_test"
os.environ["DQ_METRICS_TABLE"] = "dq_check_metrics_test"


def _seed_glue_metrics(table_name: str, rows: list[dict]) -> None:
    """Create the metrics table with a pipeline_id-index GSI and seed rows."""
    ddb = boto3.resource("dynamodb", region_name="eu-west-1")
    ddb.create_table(
        TableName=table_name,
        KeySchema=[
            {"AttributeName": "job_name", "KeyType": "HASH"},
            {"AttributeName": "run_id",   "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "job_name",    "AttributeType": "S"},
            {"AttributeName": "run_id",      "AttributeType": "S"},
            {"AttributeName": "pipeline_id", "AttributeType": "S"},
            {"AttributeName": "start_time",  "AttributeType": "S"},
        ],
        GlobalSecondaryIndexes=[{
            "IndexName": "pipeline_id-index",
            "KeySchema": [
                {"AttributeName": "pipeline_id", "KeyType": "HASH"},
                {"AttributeName": "start_time",  "KeyType": "RANGE"},
            ],
            "Projection": {"ProjectionType": "ALL"},
        }],
        BillingMode="PAY_PER_REQUEST",
    )
    table = ddb.Table(table_name)
    for r in rows:
        table.put_item(Item=r)


# UIFPE-355: these env vars MUST be re-applied per test, not once at import.
#
# The controller reads METRICS_TABLE et al from os.environ at module scope
# (pipeline_controller.py:45-47), and each test below reloads that module inside
# its moto context to rebind it. But os.environ is process-global and survives
# across test FILES: test_dq_check_controller.py and test_lineage_dq.py both set
# METRICS_TABLE = "eds-test-glue-job-metrics" in their setUp, which runs before
# every one of their tests. pytest imports all modules first and runs tests
# after, so a module-level assignment here is overwritten long before these
# tests execute - the reload then picks up "eds-test-..." while the test seeded
# "echo-test-...", and the handler queries an empty table.
#
# That is why these tests passed alone and failed in a directory run. Setting
# them in setUp makes each test independent of file order.
class _HomeSummaryEnv(unittest.TestCase):
    def setUp(self):
        os.environ["AWS_DEFAULT_REGION"]   = "eu-west-1"
        os.environ["REGION"]               = "eu-west-1"
        os.environ["METRICS_TABLE"]        = "echo-test-glue-job-metrics"
        os.environ["EXPORT_METRICS_TABLE"] = "partner_export_metrics_test"
        os.environ["DQ_METRICS_TABLE"]     = "dq_check_metrics_test"


class TestHomeSummaryHappyPath(_HomeSummaryEnv):

    @mock_aws
    def test_summary_pipeline_level_24h_sync_metrics(self):
        """Resolved decision #2: count DISTINCT pipelines, not runs.

        Test data (all within 24h window):
          - pipeline p1: 2 SUCCEEDED runs       -> ok bucket (1 distinct)
          - pipeline p2: 1 FAILED run           -> fail bucket (1 distinct)
          - pipeline p3: 1 SUCCEEDED + 1 FAILED -> fail bucket (has at least one failure)

        Plus one row outside the window:
          - pipeline p4: 1 FAILED at t=48h -> dropped by window filter

        Expected: ok=1 (only p1 is purely successful), fail=2 (p2, p3), in_24h=3 distinct pipelines.
        """
        now = datetime.now(timezone.utc)
        recent = (now - timedelta(hours=2)).isoformat()
        recent2 = (now - timedelta(hours=4)).isoformat()
        old    = (now - timedelta(hours=48)).isoformat()
        _seed_glue_metrics("echo-test-glue-job-metrics", [
            {"job_name": "echo-dev-bronze-ingest", "run_id": "r1",
             "pipeline_id": "p1", "start_time": recent, "status": "SUCCEEDED"},
            {"job_name": "echo-dev-bronze-ingest", "run_id": "r2",
             "pipeline_id": "p1", "start_time": recent2, "status": "SUCCEEDED"},
            {"job_name": "echo-dev-bronze-ingest", "run_id": "r3",
             "pipeline_id": "p2", "start_time": recent, "status": "FAILED"},
            {"job_name": "echo-dev-bronze-ingest", "run_id": "r4",
             "pipeline_id": "p3", "start_time": recent, "status": "SUCCEEDED"},
            {"job_name": "echo-dev-bronze-ingest", "run_id": "r5",
             "pipeline_id": "p3", "start_time": recent2, "status": "FAILED"},
            {"job_name": "echo-dev-bronze-ingest", "run_id": "r6",
             "pipeline_id": "p4", "start_time": old,    "status": "FAILED"},
        ])

        # Import inside the moto context so module-level boto3 clients bind
        # to the mocked endpoint.
        import importlib
        import pipeline_controller
        importlib.reload(pipeline_controller)

        event = {
            "requestContext": {"http": {"method": "GET"}},
            "rawPath": "/home/summary",
            "pathParameters": {},
            "queryStringParameters": None,
        }
        resp = pipeline_controller.handler(event, None)
        self.assertEqual(resp["statusCode"], 200)
        body = json.loads(resp["body"])

        # Pipeline-level: p1 is all-ok; p2 and p3 each had at least one failure.
        self.assertEqual(body["syncs"]["ok"],     1, "Only p1 had zero failures")
        self.assertEqual(body["syncs"]["fail"],   2, "p2 and p3 each had a failure")
        self.assertEqual(body["syncs"]["in_24h"], 3, "3 distinct pipelines ran in window")
        # Exports + DQ tables don't exist yet — safe zeros.
        self.assertEqual(body["exports"], {"ok": 0, "fail": 0, "in_24h": 0})
        self.assertEqual(body["dq"],      {"ok": 0, "fail": 0, "in_24h": 0})


class TestHomeSummarySafeZero(_HomeSummaryEnv):
    """Exports + DQ DynamoDB tables don't exist until Plans 6/7 ship. The
    home page must still load on day-1 with zeros for those modules."""

    @mock_aws
    def test_missing_exports_and_dq_tables_return_zeros(self):
        # Only create the syncs metrics table — exports and dq tables are absent.
        _seed_glue_metrics("echo-test-glue-job-metrics", [])

        import importlib
        import pipeline_controller
        importlib.reload(pipeline_controller)

        event = {
            "requestContext": {"http": {"method": "GET"}},
            "rawPath": "/home/summary",
            "pathParameters": {},
            "queryStringParameters": None,
        }
        resp = pipeline_controller.handler(event, None)
        self.assertEqual(resp["statusCode"], 200)
        body = json.loads(resp["body"])

        self.assertEqual(body["syncs"],   {"ok": 0, "fail": 0, "in_24h": 0})
        self.assertEqual(body["exports"], {"ok": 0, "fail": 0, "in_24h": 0})
        self.assertEqual(body["dq"],      {"ok": 0, "fail": 0, "in_24h": 0})

    @mock_aws
    def test_all_three_tables_missing_returns_zeros_not_500(self):
        # No tables created at all. Endpoint must NOT 500.
        import importlib
        import pipeline_controller
        importlib.reload(pipeline_controller)

        event = {
            "requestContext": {"http": {"method": "GET"}},
            "rawPath": "/home/summary",
            "pathParameters": {},
            "queryStringParameters": None,
        }
        resp = pipeline_controller.handler(event, None)
        self.assertEqual(resp["statusCode"], 200)
        body = json.loads(resp["body"])
        self.assertEqual(body["syncs"]["in_24h"], 0)
        self.assertEqual(body["exports"]["in_24h"], 0)
        self.assertEqual(body["dq"]["in_24h"], 0)


class TestHomeRecentActivity(_HomeSummaryEnv):

    @mock_aws
    def test_recent_activity_sorted_desc_with_link_path(self):
        now = datetime.now(timezone.utc)
        t1 = (now - timedelta(hours=1)).isoformat()
        t2 = (now - timedelta(hours=3)).isoformat()
        t3 = (now - timedelta(hours=6)).isoformat()
        _seed_glue_metrics("echo-test-glue-job-metrics", [
            {"job_name": "echo-dev-bronze-ingest", "run_id": "rA",
             "pipeline_id": "mtl_system_items_b", "start_time": t2,
             "status": "SUCCEEDED", "rows_written": 1234},
            {"job_name": "echo-dev-bronze-ingest", "run_id": "rB",
             "pipeline_id": "ego_mtl_sy_items_ext_b", "start_time": t1,
             "status": "FAILED", "error_message": "ORA-12541: TNS no listener"},
            {"job_name": "echo-dev-bronze-ingest", "run_id": "rC",
             "pipeline_id": "bom_components_b", "start_time": t3,
             "status": "SUCCEEDED", "rows_written": 42},
        ])

        import importlib
        import pipeline_controller
        importlib.reload(pipeline_controller)

        event = {
            "requestContext": {"http": {"method": "GET"}},
            "rawPath": "/home/recent-activity",
            "pathParameters": {},
            "queryStringParameters": None,
        }
        resp = pipeline_controller.handler(event, None)
        self.assertEqual(resp["statusCode"], 200)
        body = json.loads(resp["body"])

        items = body["items"]
        self.assertEqual(len(items), 3)
        # Descending by ts
        self.assertEqual(items[0]["subject"], "ego_mtl_sy_items_ext_b")
        self.assertEqual(items[1]["subject"], "mtl_system_items_b")
        self.assertEqual(items[2]["subject"], "bom_components_b")
        # Shape contract
        for it in items:
            self.assertIn("ts", it)
            self.assertEqual(it["module"], "sync")
            self.assertIn(it["status"], ("SUCCEEDED", "FAILED"))
            self.assertTrue(it["link_path"].startswith("/m/data-sync/syncs/"))
        # Deep-link payload
        self.assertEqual(items[0]["link_path"], "/m/data-sync/syncs/ego_mtl_sy_items_ext_b")
        # Failure carries a message
        self.assertIn("ORA-12541", items[0]["message"])


class TestHomeRecentActivityGold(_HomeSummaryEnv):
    """UIFPE-504. Gold runs reach this feed (unlike /home/summary, which filters
    them with _is_ingest_job), but they carry gold_id and no pipeline_id, so
    they used to render with an empty subject and a dead /m/data-sync/syncs/
    link. Live proof before the fix: 3 of 100 items at limit=100."""

    @mock_aws
    def test_a_gold_run_is_named_and_linked_to_the_runs_list(self):
        now = datetime.now(timezone.utc)
        _seed_glue_metrics("echo-test-glue-job-metrics", [
            {"job_name": "uif-echo-dev-bronze-ingest", "run_id": "rB",
             "pipeline_id": "mtl_system_items_b",
             "start_time": (now - timedelta(hours=3)).isoformat(),
             "status": "SUCCEEDED", "rows_written": 7},
            {"job_name": "uif-echo-dev-gold-transform", "run_id": "rG",
             "gold_id": "price",
             "start_time": (now - timedelta(hours=1)).isoformat(),
             "status": "SUCCEEDED", "rows_written": 1492904},
        ])

        items = self._feed()
        self.assertEqual(len(items), 2, items)

        gold, bronze = items[0], items[1]
        # named by its ENTITY, not blank
        self.assertEqual(gold["subject"], "price")
        # and pointed somewhere that exists — /syncs/{gold_id} would 404
        self.assertEqual(gold["link_path"], "/m/data-sync/runs")
        # the bronze row is untouched
        self.assertEqual(bronze["subject"], "mtl_system_items_b")
        self.assertEqual(bronze["link_path"], "/m/data-sync/syncs/mtl_system_items_b")

    @mock_aws
    def test_no_feed_item_ever_has_an_empty_subject(self):
        """The acceptance criterion, asserted over the whole feed rather than a
        single row — a metrics row nothing can name is dropped instead of
        rendering as a bare status badge."""
        now = datetime.now(timezone.utc)
        _seed_glue_metrics("echo-test-glue-job-metrics", [
            {"job_name": "uif-echo-dev-gold-transform", "run_id": "rG",
             "gold_id": "product",
             "start_time": (now - timedelta(hours=1)).isoformat(),
             "status": "SUCCEEDED"},
            # neither a pipeline nor an entity: unnameable, must not appear
            {"job_name": "uif-echo-dev-silver-transform", "run_id": "rX",
             "start_time": (now - timedelta(hours=2)).isoformat(),
             "status": "FAILED"},
        ])

        items = self._feed()
        self.assertEqual([i["subject"] for i in items], ["product"], items)
        for it in items:
            self.assertTrue(it["subject"].strip(), it)
            self.assertFalse(it["link_path"].endswith("/syncs/"), it)

    def _feed(self):
        import importlib
        import pipeline_controller
        importlib.reload(pipeline_controller)
        resp = pipeline_controller.handler({
            "requestContext": {"http": {"method": "GET"}},
            "rawPath": "/home/recent-activity",
            "pathParameters": {},
            "queryStringParameters": None,
        }, None)
        self.assertEqual(resp["statusCode"], 200, resp)
        return json.loads(resp["body"])["items"]


class TestHomeRecentActivityLimit(_HomeSummaryEnv):

    @mock_aws
    def test_default_limit_is_20(self):
        now = datetime.now(timezone.utc)
        rows = [
            {"job_name": "echo-dev-bronze-ingest", "run_id": f"r{i:02d}",
             "pipeline_id": f"sync_{i:02d}",
             "start_time": (now - timedelta(minutes=i)).isoformat(),
             "status": "SUCCEEDED"}
            for i in range(30)
        ]
        _seed_glue_metrics("echo-test-glue-job-metrics", rows)

        import importlib
        import pipeline_controller
        importlib.reload(pipeline_controller)

        event = {
            "requestContext": {"http": {"method": "GET"}},
            "rawPath": "/home/recent-activity",
            "pathParameters": {},
            "queryStringParameters": None,
        }
        resp = pipeline_controller.handler(event, None)
        self.assertEqual(resp["statusCode"], 200)
        body = json.loads(resp["body"])
        self.assertEqual(len(body["items"]), 20)
        # First item is most recent → "sync_00"
        self.assertEqual(body["items"][0]["subject"], "sync_00")

    @mock_aws
    def test_custom_limit_5(self):
        now = datetime.now(timezone.utc)
        rows = [
            {"job_name": "echo-dev-bronze-ingest", "run_id": f"r{i:02d}",
             "pipeline_id": f"sync_{i:02d}",
             "start_time": (now - timedelta(minutes=i)).isoformat(),
             "status": "SUCCEEDED"}
            for i in range(10)
        ]
        _seed_glue_metrics("echo-test-glue-job-metrics", rows)

        import importlib
        import pipeline_controller
        importlib.reload(pipeline_controller)

        event = {
            "requestContext": {"http": {"method": "GET"}},
            "rawPath": "/home/recent-activity",
            "pathParameters": {},
            "queryStringParameters": {"limit": "5"},
        }
        resp = pipeline_controller.handler(event, None)
        self.assertEqual(resp["statusCode"], 200)
        body = json.loads(resp["body"])
        self.assertEqual(len(body["items"]), 5)

    @mock_aws
    def test_invalid_limit_falls_back_to_20(self):
        _seed_glue_metrics("echo-test-glue-job-metrics", [])

        import importlib
        import pipeline_controller
        importlib.reload(pipeline_controller)

        event = {
            "requestContext": {"http": {"method": "GET"}},
            "rawPath": "/home/recent-activity",
            "pathParameters": {},
            "queryStringParameters": {"limit": "not-a-number"},
        }
        resp = pipeline_controller.handler(event, None)
        self.assertEqual(resp["statusCode"], 200)
        # No items because we seeded zero, but the request must succeed (200).
        body = json.loads(resp["body"])
        self.assertEqual(body["items"], [])


if __name__ == "__main__":
    unittest.main()
