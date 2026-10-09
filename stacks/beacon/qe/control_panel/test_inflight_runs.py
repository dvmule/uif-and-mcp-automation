# test_inflight_runs.py (UIFPE-561)
# Runs still executing come from Step Functions (the only place that knows)
# and are merged into GET /runs so a refreshed page keeps showing them.
# Covered: the merge and its ordering, the recorded-row-wins dedupe, the
# job_id filter, the no-state-machine skip, the fail-open posture on AWS
# errors, and the short cache that shields ListExecutions from refresh storms.

import json
import unittest
from datetime import datetime, timezone
from unittest import mock

from fastapi.testclient import TestClient

from control_panel import publisher, runs
from control_panel.api import app

client = TestClient(app)

ARN = "arn:aws:states:eu-west-1:000000000000:stateMachine:uif-beacon-dev-export"
ENV = {"BEACON_STATE_MACHINE_ARN": ARN, "BEACON_TABLE_RUNS": "beacon-dev-runs"}
STARTED = datetime(2026, 9, 24, 10, 0, 0, tzinfo=timezone.utc)


class FakeSfn:
    def __init__(self, executions=(), fail_list=False):
        self.executions = list(executions)
        self.fail_list = fail_list
        self.list_calls = 0

    def list_executions(self, stateMachineArn, statusFilter, maxResults):
        self.list_calls += 1
        assert statusFilter == "RUNNING"
        if self.fail_list:
            raise RuntimeError("throttled")
        return {"executions": [
            {"name": e["batch_id"], "executionArn": f"arn:exec:{e['batch_id']}",
             "startDate": STARTED} for e in self.executions]}

    def describe_execution(self, executionArn):
        batch = executionArn.split(":")[-1]
        e = next(x for x in self.executions if x["batch_id"] == batch)
        # UIFPE-577: the input publisher.start_run ACTUALLY sends. `job_config`
        # is the Glue sizing row and carries no export id — the id is in the
        # config path (D22 layout). The old fake asserted the implementation's
        # assumption instead of this contract, so it blessed the bug.
        return {"input": json.dumps(
            {"export_config_s3_path":
                f"s3://uif-beacon-dev-data/configs/{e['job_id']}/v2/export.json",
             "job_config": {"worker_type": "G.1X", "workers": 2,
                            "timeout_minutes": 60},
             "trigger": e.get("trigger", "manual"),
             "watermark": ""})}


class FakeRunsDdb:
    """The runs table with a few recorded rows."""

    def __init__(self, rows=()):
        self.rows = list(rows)

    def _item(self, r):
        return {"job_id": {"S": r["job_id"]}, "batch_id": {"S": r["batch_id"]},
                "status": {"S": r["status"]}, "trigger": {"S": "manual"},
                "recorded_at": {"S": r.get("recorded_at", "2026-09-24T09:00:00Z")}}

    def query(self, TableName, KeyConditionExpression, ExpressionAttributeValues):
        job = ExpressionAttributeValues[":j"]["S"]
        return {"Items": [self._item(r) for r in self.rows if r["job_id"] == job]}

    def scan(self, TableName):
        return {"Items": [self._item(r) for r in self.rows]}


class InFlightRuns(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict("os.environ", ENV)
        self.env.start()
        publisher.reset_inflight_cache_for_testing()

    def tearDown(self):
        self.env.stop()
        publisher.set_clients_for_testing(None, None, None)
        runs.set_client_for_testing(None)
        publisher.reset_inflight_cache_for_testing()

    def _wire(self, sfn, table_rows=()):
        publisher.set_clients_for_testing(object(), sfn)
        runs.set_client_for_testing(FakeRunsDdb(table_rows))

    def test_running_execution_appears_in_the_list_first(self):
        self._wire(FakeSfn([{"batch_id": "b-live", "job_id": "100001"}]),
                   [{"job_id": "100001", "batch_id": "b-old", "status": "succeeded"}])
        body = client.get("/runs").json()
        self.assertEqual([r["batch_id"] for r in body["runs"]], ["b-live", "b-old"])
        live = body["runs"][0]
        self.assertEqual(live["status"], "running")
        self.assertEqual(live["job_id"], "100001")
        self.assertEqual(live["recorded_at"], STARTED.isoformat())

    def test_recorded_row_wins_over_the_same_batch_in_flight(self):
        self._wire(FakeSfn([{"batch_id": "b-1", "job_id": "100001"}]),
                   [{"job_id": "100001", "batch_id": "b-1", "status": "succeeded"}])
        body = client.get("/runs").json()
        rows = [r for r in body["runs"] if r["batch_id"] == "b-1"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "succeeded")

    def test_job_filter_applies_to_in_flight_rows_too(self):
        self._wire(FakeSfn([{"batch_id": "b-a", "job_id": "100001"},
                            {"batch_id": "b-b", "job_id": "100002"}]))
        body = client.get("/runs", params={"job_id": "100002"}).json()
        self.assertEqual([r["batch_id"] for r in body["runs"]], ["b-b"])

    def test_job_id_comes_from_the_config_path_not_the_sizing_block(self):
        """UIFPE-577's regression guard. The execution input's `job_config` is
        Glue sizing; the export id lives only in export_config_s3_path. Read
        from the wrong place and the per-export list shows nothing while a
        batch runs — which is how this shipped broken."""
        sfn = FakeSfn([{"batch_id": "b-live", "job_id": "100031"}])
        self._wire(sfn)
        payload = json.loads(sfn.describe_execution("arn:exec:b-live")["input"])
        self.assertNotIn("job_id", payload["job_config"])  # sizing only
        rows = publisher.in_flight_runs("100031")
        self.assertEqual([r["batch_id"] for r in rows], ["b-live"])
        self.assertEqual(rows[0]["job_id"], "100031")

    def test_the_row_carries_the_execution_arn_for_stop_and_poll(self):
        """UIFPE-583: the panel re-attaches to a batch it did not start, and
        run_status/stop_run accept nothing but the execution ARN."""
        self._wire(FakeSfn([{"batch_id": "b-live", "job_id": "100001"}]))
        rows = publisher.in_flight_runs("100001")
        self.assertEqual(rows[0]["run_id"], "arn:exec:b-live")

    def test_an_unattributable_execution_is_kept_but_never_misfiled(self):
        """No derivable id (an input shape we do not know): the row still
        appears unfiltered, and belongs to no export."""
        class NoPath(FakeSfn):
            def describe_execution(self, executionArn):
                return {"input": json.dumps({"trigger": "manual"})}

        self._wire(NoPath([{"batch_id": "b-odd", "job_id": "100001"}]))
        self.assertEqual([r["job_id"] for r in publisher.in_flight_runs()], [None])
        publisher.reset_inflight_cache_for_testing()
        self.assertEqual(publisher.in_flight_runs("100001"), [])

    def test_scheduled_trigger_carried_from_the_execution_input(self):
        self._wire(FakeSfn([{"batch_id": "b-s", "job_id": "100001",
                             "trigger": "scheduled"}]))
        body = client.get("/runs").json()
        self.assertEqual(body["runs"][0]["trigger"], "scheduled")

    def test_no_state_machine_means_no_lookup(self):
        with mock.patch.dict("os.environ", {"BEACON_STATE_MACHINE_ARN": ""}):
            self.assertEqual(publisher.in_flight_runs(), [])

    def test_aws_failure_fails_open(self):
        self._wire(FakeSfn(fail_list=True),
                   [{"job_id": "100001", "batch_id": "b-old", "status": "failed"}])
        body = client.get("/runs").json()
        self.assertEqual([r["batch_id"] for r in body["runs"]], ["b-old"])

    def test_short_cache_shields_list_executions(self):
        sfn = FakeSfn([{"batch_id": "b-live", "job_id": "100001"}])
        self._wire(sfn)
        client.get("/runs")
        client.get("/runs")
        self.assertEqual(sfn.list_calls, 1)


if __name__ == "__main__":
    unittest.main()
