# test_runs.py
# Run-history reads from the runs table, with a fake DynamoDB client — and
# the honest disabled mode when the table isn't configured.

import json
import os
import unittest

from fastapi.testclient import TestClient

from control_panel import runs
from control_panel.api import app

client = TestClient(app)


def item(job_id, batch_id, status, at, summary=None, error=None):
    it = {"job_id": {"S": job_id}, "batch_id": {"S": batch_id},
          "status": {"S": status}, "recorded_at": {"S": at}}
    if summary is not None:
        it["summary"] = {"S": json.dumps(summary)}
    if error:
        it["error"] = {"S": error}
    return it


ITEMS = [
    item("product-export", "b-0141", "succeeded", "2026-08-06T07:02:00Z",
         {"output_rows": 48090, "rejected_rows": 41, "duration_seconds": 81.5}),
    item("product-export", "b-0142", "succeeded", "2026-08-07T07:02:00Z",
         {"output_rows": 48112, "rejected_rows": 37}),
    item("product-export", "b-0143", "failed", "2026-08-07T09:00:00Z",
         error="quality gate breached: rejection ceiling"),
]


class FakeDdb:
    def __init__(self):
        self.put = []

    def query(self, TableName, KeyConditionExpression, ExpressionAttributeValues):
        job = ExpressionAttributeValues[":j"]["S"]
        return {"Items": [i for i in ITEMS if i["job_id"]["S"] == job]}

    def scan(self, TableName):
        return {"Items": list(ITEMS)}

    def put_item(self, TableName, Item):
        self.put.append((TableName, Item))


class TestRuns(unittest.TestCase):
    def setUp(self):
        os.environ["BEACON_TABLE_RUNS"] = "runs-table"
        self.ddb = FakeDdb()
        runs.set_client_for_testing(self.ddb)

    def tearDown(self):
        os.environ.pop("BEACON_TABLE_RUNS", None)
        runs.set_client_for_testing(None)

    def test_list_newest_first_with_parsed_summary(self):
        result = client.get("/runs").json()
        self.assertTrue(result["enabled"])
        batches = [r["batch_id"] for r in result["runs"]]
        self.assertEqual(batches, ["b-0143", "b-0142", "b-0141"])
        self.assertEqual(result["runs"][1]["summary"]["output_rows"], 48112)
        self.assertEqual(result["runs"][2]["summary"]["duration_seconds"], 81.5)

    def test_failed_run_carries_error(self):
        result = client.get("/runs").json()
        failed = result["runs"][0]
        self.assertEqual(failed["status"], "failed")
        self.assertIn("gate", failed["error"])

    def test_filter_by_job_id_uses_query(self):
        result = client.get("/runs", params={"job_id": "product-export"}).json()
        self.assertEqual(len(result["runs"]), 3)

    def test_pre_206_rows_default_to_manual_trigger(self):
        # every run before UIFPE-206 was a person pressing Run now
        result = client.get("/runs").json()
        self.assertTrue(all(r["trigger"] == "manual" for r in result["runs"]))

    def test_scheduled_trigger_is_surfaced(self):
        row = dict(ITEMS[0])
        row["trigger"] = {"S": "scheduled"}
        parsed = runs._parse_item(row)
        self.assertEqual(parsed["trigger"], "scheduled")

    def test_disabled_mode_is_honest(self):
        os.environ.pop("BEACON_TABLE_RUNS", None)
        result = client.get("/runs").json()
        self.assertFalse(result["enabled"])
        self.assertEqual(result["runs"], [])
        self.assertIn("not configured", result["reason"])

    def test_health_reports_history_flag(self):
        self.assertTrue(client.get("/health").json()["runs_history_enabled"])

    def test_record_stop_writes_stopped_row(self):
        # UIFPE-203: a stopped run's terminal row is written by the API —
        # the record_run Lambda never fires on an aborted execution
        self.assertTrue(runs.record_stop("100000", "b-9", "stopped from the control panel"))
        table, item = self.ddb.put[0]
        self.assertEqual(table, "runs-table")
        self.assertEqual(item["job_id"]["S"], "100000")
        self.assertEqual(item["status"]["S"], "stopped")
        self.assertIn("control panel", item["error"]["S"])

    def test_started_at_is_surfaced_when_the_row_has_one(self):
        # UIFPE-720: new rows carry the execution's start; older rows do not
        row = dict(ITEMS[0])
        row["started_at"] = {"S": "2026-09-30T09:12:00Z"}
        self.assertEqual(runs._parse_item(row)["started_at"], "2026-09-30T09:12:00Z")
        self.assertNotIn("started_at", runs._parse_item(dict(ITEMS[0])))

    def test_record_stop_keeps_the_execution_start(self):
        self.assertTrue(runs.record_stop("100000", "b-10", "stopped from the control panel",
                                         started_at="2026-09-30T09:12:00.250000+00:00"))
        _, item = self.ddb.put[-1]
        self.assertEqual(item["started_at"]["S"], "2026-09-30T09:12:00Z")

    def test_record_stop_disabled_mode_is_a_noop(self):
        os.environ.pop("BEACON_TABLE_RUNS", None)
        self.assertFalse(runs.record_stop("100000", "b-9", "note"))
        self.assertEqual(self.ddb.put, [])
