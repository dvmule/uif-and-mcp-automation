# test_lambdas.py
# The Step Functions executors and the scheduled starter, tested with fake
# clients — no AWS.

import json
import os
import unittest
from pathlib import Path
from datetime import datetime, timezone

from beacon_export.lambdas import (deliver, emit_event, record_run,
                                   scheduled_starter)

EXPORT_CONFIG = {
    "version": "1.0",
    "job_id": "product-export",
    "mapping_config_s3_path": "s3://data/configs/mapping.json",
    "canonical_s3_prefix": "s3://data/staging/{batch_id}/canonical/",
    "quality_gates": {"min_rows": 1, "max_rejection_pct": 2.0},
    "renderings": [{"format": "csv", "options": {}}],
    "packaging": {"filename_pattern": "e_{batch_id}.{ext}",
                  "pickup_s3_prefix": "s3://data/pickup/{batch_id}/"},
    "delivery": {"channel": "s3", "adapter": "s3_copy",
                 "destination_s3_prefix": "s3://partner/inbound"},
}

FILES = ["e_b-1.csv", "e_b-1.manifest.json"]


class FakeS3:
    def __init__(self):
        self.objects = {"data/configs/export.json":
                        json.dumps(EXPORT_CONFIG).encode()}
        for i, name in enumerate(FILES):
            self.objects[f"data/pickup/b-1/{name}"] = b"x" * (10 + i)

    def get_object(self, Bucket, Key):
        import io
        return {"Body": io.BytesIO(self.objects[f"{Bucket}/{Key}"])}

    def list_objects_v2(self, Bucket, Prefix):
        keys = [k.split("/", 1)[1] for k in self.objects
                if k.startswith(f"{Bucket}/{Prefix}")]
        return {"Contents": [{"Key": k} for k in keys]}

    def copy_object(self, Bucket, Key, CopySource):
        src = f"{CopySource['Bucket']}/{CopySource['Key']}"
        self.objects[f"{Bucket}/{Key}"] = self.objects[src]

    def head_object(self, Bucket, Key):
        return {"ContentLength": len(self.objects[f"{Bucket}/{Key}"])}


class FakeTransfer:
    """UIFPE-339: StartFileTransfer + ListFileTransferResults, faked. Every
    file completes immediately unless `fail_file` names one to fail."""

    def __init__(self, fail_file=None):
        self.fail_file = fail_file
        self.transfers = []  # (connector_id, send_paths, remote_dir)
        self._results = {}
        self._n = 0

    def start_file_transfer(self, ConnectorId, SendFilePaths,
                            RemoteDirectoryPath):
        self._n += 1
        transfer_id = f"t-{self._n:04d}"
        self.transfers.append((ConnectorId, list(SendFilePaths),
                               RemoteDirectoryPath))
        self._results[transfer_id] = [
            {"FilePath": p,
             **({"StatusCode": "FAILED", "FailureCode": "PERMISSION_DENIED",
                 "FailureMessage": "denied"}
                if self.fail_file and p.endswith(self.fail_file)
                else {"StatusCode": "COMPLETED"})}
            for p in SendFilePaths]
        return {"TransferId": transfer_id}

    def list_file_transfer_results(self, ConnectorId, TransferId, **kwargs):
        return {"FileTransferResults": self._results[TransferId]}


class TestDeliver(unittest.TestCase):
    def test_lists_pickup_and_copies_manifest_last(self):
        s3 = FakeS3()
        result = deliver.handler(
            {"export_config_s3_path": "s3://data/configs/export.json",
             "batch_id": "b-1"}, s3_client=s3)
        self.assertEqual(result["delivered"], 2)
        self.assertTrue(result["delivery"][-1]["file"].endswith(".manifest.json"))
        self.assertIn("partner/inbound/e_b-1.csv", s3.objects)

    def test_empty_pickup_fails_loudly(self):
        s3 = FakeS3()
        with self.assertRaises(RuntimeError):
            deliver.handler(
                {"export_config_s3_path": "s3://data/configs/export.json",
                 "batch_id": "b-GHOST"}, s3_client=s3)

    def test_retired_paramiko_adapter_fails_loudly_with_republish_hint(self):
        # UIFPE-438: the in-Lambda Paramiko adapter is gone. An immutable
        # pre-connector version that still says paramiko must fail with the
        # fix in the message (republish), never deliver silently wrong.
        s3 = FakeS3()
        cfg = dict(EXPORT_CONFIG,
                   delivery={"channel": "sftp", "adapter": "paramiko",
                             "secret_ref": "uif-beacon/dev/destinations/acme",
                             "remote_dir": "/inbound"})
        s3.objects["data/configs/export.json"] = json.dumps(cfg).encode()
        with self.assertRaises(RuntimeError) as ctx:
            deliver.handler(
                {"export_config_s3_path": "s3://data/configs/export.json",
                 "batch_id": "b-1"}, s3_client=s3)
        # per-target outcome names the real cause (UIFPE-170 fan-out)
        self.assertIn("retired", str(ctx.exception))
        self.assertIn("republish", str(ctx.exception))

    def test_retired_connection_test_action_fails_loudly(self):
        # the API stopped invoking this at UIFPE-438; a stray legacy caller
        # gets a loud pointer at the service-side test, not a silent no-op
        with self.assertRaises(NotImplementedError) as ctx:
            deliver.handler({"action": "connection_test", "secret_ref": "r",
                             "remote_dir": "/in"})
        self.assertIn("TestConnection", str(ctx.exception))

    def test_sftp_transfer_family_delivers_via_connector(self):
        # UIFPE-339: a connector destination hands the batch's S3 paths to
        # StartFileTransfer (remote dir included) and polls the results — the
        # Lambda never opens an SFTP session or downloads the files
        s3 = FakeS3()
        cfg = dict(EXPORT_CONFIG,
                   delivery={"channel": "sftp", "adapter": "transfer_family",
                             "connector_id": "c-123", "remote_dir": "/inbound"})
        s3.objects["data/configs/export.json"] = json.dumps(cfg).encode()
        transfer = FakeTransfer()

        result = deliver.handler(
            {"export_config_s3_path": "s3://data/configs/export.json",
             "batch_id": "b-1"}, s3_client=s3, transfer_client=transfer)

        self.assertEqual(result["delivered"], 2)
        (connector_id, send_paths, remote_dir), = transfer.transfers
        self.assertEqual(connector_id, "c-123")
        self.assertEqual(remote_dir, "/inbound")
        # S3 paths in Transfer's /bucket/key form, manifest LAST
        self.assertEqual(send_paths, ["/data/pickup/b-1/e_b-1.csv",
                                      "/data/pickup/b-1/e_b-1.manifest.json"])
        self.assertEqual(result["delivery"][-1]["via"], "transfer_family")

    def test_sftp_transfer_family_failure_names_the_file(self):
        s3 = FakeS3()
        cfg = dict(EXPORT_CONFIG,
                   delivery={"channel": "sftp", "adapter": "transfer_family",
                             "connector_id": "c-123", "remote_dir": "/inbound"})
        s3.objects["data/configs/export.json"] = json.dumps(cfg).encode()
        transfer = FakeTransfer(fail_file="e_b-1.csv")
        with self.assertRaises(RuntimeError) as ctx:
            deliver.handler(
                {"export_config_s3_path": "s3://data/configs/export.json",
                 "batch_id": "b-1"}, s3_client=s3, transfer_client=transfer)
        self.assertIn("e_b-1.csv", str(ctx.exception))
        self.assertIn("PERMISSION_DENIED", str(ctx.exception))

    # (an unknown sftp adapter never reaches this Lambda — export_config.load
    # refuses it at validation; see test_beacon_config's adapter tests)


def _multi_target_cfg(*targets):
    cfg = {k: v for k, v in EXPORT_CONFIG.items() if k != "delivery"}
    cfg["deliveries"] = list(targets)
    return cfg


class TestDeliverMultiTarget(unittest.TestCase):
    # UIFPE-170: one batch fans out to every target; every target is
    # attempted; any failure fails the batch loudly with per-target detail.

    S3_A = {"channel": "s3", "adapter": "s3_copy",
            "destination_s3_prefix": "s3://partner-a/inbound"}
    S3_B = {"channel": "s3", "adapter": "s3_copy",
            "destination_s3_prefix": "s3://partner-b/drop"}

    def test_delivers_the_batch_to_every_target(self):
        s3 = FakeS3()
        s3.objects["data/configs/export.json"] = json.dumps(
            _multi_target_cfg(self.S3_A, self.S3_B)).encode()
        result = deliver.handler(
            {"export_config_s3_path": "s3://data/configs/export.json",
             "batch_id": "b-1"}, s3_client=s3)
        self.assertIn("partner-a/inbound/e_b-1.csv", s3.objects)
        self.assertIn("partner-b/drop/e_b-1.csv", s3.objects)
        self.assertEqual(result["delivered"], 4)  # 2 files x 2 targets
        self.assertEqual([t["status"] for t in result["targets"]],
                         ["delivered", "delivered"])

    def test_a_failing_target_does_not_stop_the_others(self):
        # the FAILING target comes first — the s3 target after it must still
        # deliver (one partner's outage never hides the other's feed), and
        # the batch still fails loudly with the per-target outcomes
        s3 = FakeS3()
        bad_sftp = {"channel": "sftp", "adapter": "paramiko",
                    "secret_ref": "uif/beacon/dev/destinations/down",
                    "remote_dir": "/inbound", "destination_id": "dest-down"}
        s3.objects["data/configs/export.json"] = json.dumps(
            _multi_target_cfg(bad_sftp, self.S3_A)).encode()

        class BrokenSecrets:
            def get_secret_value(self, SecretId):
                raise ConnectionError("secretsmanager unreachable")

        with self.assertRaises(RuntimeError) as ctx:
            deliver.handler(
                {"export_config_s3_path": "s3://data/configs/export.json",
                 "batch_id": "b-1"}, s3_client=s3,
                secrets_client=BrokenSecrets())
        self.assertIn("1 of 2", str(ctx.exception))
        self.assertIn("dest-down", str(ctx.exception))
        # the second target was still attempted and delivered
        self.assertIn("partner-a/inbound/e_b-1.csv", s3.objects)

    def test_each_sftp_target_uses_its_own_connector(self):
        # UIFPE-339/438: every sftp target rides ITS destination's connector
        s3 = FakeS3()
        t1 = {"channel": "sftp", "adapter": "transfer_family",
              "connector_id": "c-one", "remote_dir": "/a"}
        t2 = {"channel": "sftp", "adapter": "transfer_family",
              "connector_id": "c-two", "remote_dir": "/b"}
        s3.objects["data/configs/export.json"] = json.dumps(
            _multi_target_cfg(t1, t2)).encode()
        transfer = FakeTransfer()
        result = deliver.handler(
            {"export_config_s3_path": "s3://data/configs/export.json",
             "batch_id": "b-1"}, s3_client=s3, transfer_client=transfer)
        self.assertEqual([(c, d) for c, _, d in transfer.transfers],
                         [("c-one", "/a"), ("c-two", "/b")])
        self.assertEqual(len(result["targets"]), 2)
        self.assertEqual({r["via"] for r in result["delivery"]},
                         {"transfer_family"})

    def test_single_delivery_object_reports_one_target(self):
        # the pre-170 shape flows through the same fan-out as a list of one
        s3 = FakeS3()
        result = deliver.handler(
            {"export_config_s3_path": "s3://data/configs/export.json",
             "batch_id": "b-1"}, s3_client=s3)
        self.assertEqual(len(result["targets"]), 1)
        self.assertEqual(result["targets"][0]["status"], "delivered")


class FakeDdb:
    def __init__(self):
        self.items = []

    def put_item(self, TableName, Item):
        self.items.append((TableName, Item))


class TestRecordRun(unittest.TestCase):
    def setUp(self):
        os.environ["BEACON_TABLE_RUNS"] = "runs-table"

    def tearDown(self):
        os.environ.pop("BEACON_TABLE_RUNS", None)

    def test_records_success_with_summary(self):
        ddb = FakeDdb()
        result = record_run.handler(
            {"job_id": "product-export", "batch_id": "b-1",
             "status": "succeeded",
             "summary": {"output_rows": 98, "rejected_rows": 2,
                         "duration_seconds": 81.5}},  # float survives as JSON string
            ddb_client=ddb)
        self.assertTrue(result["recorded"])
        table, item = ddb.items[0]
        self.assertEqual(table, "runs-table")
        self.assertEqual(item["batch_id"]["S"], "b-1")
        self.assertEqual(json.loads(item["summary"]["S"])["output_rows"], 98)

    def test_records_failure_with_error(self):
        ddb = FakeDdb()
        record_run.handler(
            {"job_id": "product-export", "batch_id": "b-2",
             "status": "failed", "error": "quality gate breached"},
            ddb_client=ddb)
        _, item = ddb.items[0]
        self.assertEqual(item["status"]["S"], "failed")
        self.assertIn("gate", item["error"]["S"])

    def test_failure_without_job_id_derives_it_from_config_path(self):
        # UIFPE-200: the failure branch passes the config PATH, not a job_id —
        # the run must land under the real export, not "unknown"
        ddb = FakeDdb()
        result = record_run.handler(
            {"export_config_s3_path":
                 "s3://uif-beacon-dev-data/configs/100001/v1/export.json",
             "batch_id": "b-3", "status": "failed", "error": "boom"},
            ddb_client=ddb)
        _, item = ddb.items[0]
        self.assertEqual(item["job_id"]["S"], "100001")
        self.assertEqual(result["job_id"], "100001")

    def test_unparseable_path_still_records_as_unknown(self):
        ddb = FakeDdb()
        record_run.handler(
            {"export_config_s3_path": "s3://bucket/not-a-config.json",
             "batch_id": "b-4", "status": "failed", "error": "boom"},
            ddb_client=ddb)
        _, item = ddb.items[0]
        self.assertEqual(item["job_id"]["S"], "unknown")

    def test_explicit_job_id_wins_over_path(self):
        ddb = FakeDdb()
        record_run.handler(
            {"job_id": "100007", "batch_id": "b-5", "status": "succeeded",
             "export_config_s3_path":
                 "s3://uif-beacon-dev-data/configs/999999/v1/export.json"},
            ddb_client=ddb)
        _, item = ddb.items[0]
        self.assertEqual(item["job_id"]["S"], "100007")

    def test_trigger_is_persisted_when_present(self):
        # UIFPE-206: the run history shows who started the batch
        ddb = FakeDdb()
        record_run.handler(
            {"job_id": "100007", "batch_id": "b-6", "status": "succeeded",
             "trigger": "scheduled"}, ddb_client=ddb)
        _, item = ddb.items[0]
        self.assertEqual(item["trigger"]["S"], "scheduled")

    def test_no_trigger_writes_no_trigger_attribute(self):
        ddb = FakeDdb()
        record_run.handler(
            {"job_id": "100007", "batch_id": "b-7", "status": "succeeded"},
            ddb_client=ddb)
        _, item = ddb.items[0]
        self.assertNotIn("trigger", item)


class TestRecordRunStartedAt(unittest.TestCase):
    """UIFPE-720: the run row carries the execution's start time, in
    recorded_at's own format, on both branches of the state machine."""

    def setUp(self):
        os.environ["BEACON_TABLE_RUNS"] = "runs-table"

    def tearDown(self):
        os.environ.pop("BEACON_TABLE_RUNS", None)

    def test_success_branch_stores_started_at_in_recorded_at_format(self):
        ddb = FakeDdb()
        record_run.handler(
            {"job_id": "100007", "batch_id": "b-s1", "status": "succeeded",
             "started_at": "2026-09-30T09:12:00.123Z"}, ddb_client=ddb)
        _, item = ddb.items[0]
        self.assertEqual(item["started_at"]["S"], "2026-09-30T09:12:00Z")

    def test_failure_branch_stores_started_at_too(self):
        ddb = FakeDdb()
        record_run.handler(
            {"export_config_s3_path":
                 "s3://uif-beacon-dev-data/configs/100001/v1/export.json",
             "batch_id": "b-s2", "status": "failed", "error": "boom",
             "started_at": "2026-09-30T14:42:00.5+05:30"}, ddb_client=ddb)
        _, item = ddb.items[0]
        self.assertEqual(item["started_at"]["S"], "2026-09-30T09:12:00Z")

    def test_missing_or_unreadable_start_writes_no_attribute(self):
        ddb = FakeDdb()
        record_run.handler({"job_id": "100007", "batch_id": "b-s3", "status": "succeeded"}, ddb_client=ddb)
        record_run.handler({"job_id": "100007", "batch_id": "b-s4", "status": "succeeded",
                            "started_at": "not a time"}, ddb_client=ddb)
        self.assertNotIn("started_at", ddb.items[0][1])
        self.assertNotIn("started_at", ddb.items[1][1])

    def test_utc_stamp(self):
        self.assertEqual(record_run.utc_stamp("2026-09-30T09:12:00Z"), "2026-09-30T09:12:00Z")
        self.assertEqual(record_run.utc_stamp("2026-09-30T09:12:00"), "2026-09-30T09:12:00Z")
        self.assertIsNone(record_run.utc_stamp(""))
        self.assertIsNone(record_run.utc_stamp(None))

    def test_state_machine_passes_the_start_to_both_record_states(self):
        # the Lambda can only store what the state machine hands it
        tf = (Path(__file__).resolve().parents[2]
              / "infrastructure" / "terraform" / "stepfunctions.tf").read_text(encoding="utf-8")
        for state in ("RecordRun", "RecordFailure"):
            block = tf.split(f"      {state} = {{", 1)[1].split("ResultPath", 1)[0]
            self.assertIn('"started_at.$"', block, state)
            self.assertIn("$$.Execution.StartTime", block, state)


class FakeStateDdb(FakeDdb):
    """Runs-table put_item plus the extraction-state conditional update."""

    def __init__(self, existing_watermark=None):
        super().__init__()
        self.existing_watermark = existing_watermark
        self.updates = []

    def update_item(self, TableName, Key, UpdateExpression,
                    ConditionExpression, ExpressionAttributeValues):
        new = ExpressionAttributeValues[":w"]["S"]
        if self.existing_watermark is not None and \
                not self.existing_watermark < new:
            raise Exception("ConditionalCheckFailedException")
        self.updates.append((TableName, Key["job_id"]["S"], new))


class FakeReportS3:
    """Serves the export config and the batch's report.json."""

    def __init__(self, report=None, config=None):
        self.objects = {}
        cfg = config or EXPORT_CONFIG
        self.objects["data/configs/export.json"] = json.dumps(cfg).encode()
        if report is not None:
            canonical = cfg["canonical_s3_prefix"].replace("{batch_id}", "b-1")
            self.objects[canonical.removeprefix("s3://").rstrip("/")
                         + "/_report/report.json"] = json.dumps(report).encode()

    def get_object(self, Bucket, Key):
        import io
        return {"Body": io.BytesIO(self.objects[f"{Bucket}/{Key}"])}


REPORT = {
    "job_id": "product-export", "batch_id": "b-1", "status": "succeeded",
    "transform": {"status": "succeeded", "output_rows": 42,
                  "rejected_rows": 1, "filtered_rows": 3},
    "extraction": {"mode": "incremental", "applied": "incremental",
                   "watermark_in": "2026-08-26 06:00:00.000000",
                   "high_water_mark": "2026-08-27 06:30:00.000001"},
}


class TestRecordRunReportHandoff(unittest.TestCase):
    # UIFPE-168: the success branch re-reads the transform's report.json —
    # row counts land flat on the run summary (the shape the panel reads) and
    # the incremental watermark advances, monotonically, here and only here.

    def setUp(self):
        os.environ["BEACON_TABLE_RUNS"] = "runs-table"
        os.environ["BEACON_TABLE_EXTRACTION_STATE"] = "state-table"

    def tearDown(self):
        os.environ.pop("BEACON_TABLE_RUNS", None)
        os.environ.pop("BEACON_TABLE_EXTRACTION_STATE", None)

    EVENT = {"job_id": "product-export", "batch_id": "b-1",
             "status": "succeeded", "trigger": "scheduled",
             "summary": {"job_id": "product-export", "delivered": 2},
             "export_config_s3_path": "s3://data/configs/export.json"}

    def test_row_counts_merged_flat_into_the_summary(self):
        ddb, s3 = FakeStateDdb(), FakeReportS3(report=REPORT)
        record_run.handler(dict(self.EVENT), ddb_client=ddb, s3_client=s3)
        _, item = ddb.items[0]
        summary = json.loads(item["summary"]["S"])
        self.assertEqual(summary["output_rows"], 42)
        self.assertEqual(summary["rejected_rows"], 1)
        self.assertEqual(summary["filtered_rows"], 3)
        self.assertEqual(summary["delivered"], 2)  # deliver output survives
        self.assertEqual(summary["extraction"]["applied"], "incremental")

    def test_watermark_advances_on_success(self):
        ddb, s3 = FakeStateDdb(), FakeReportS3(report=REPORT)
        record_run.handler(dict(self.EVENT), ddb_client=ddb, s3_client=s3)
        self.assertEqual(ddb.updates,
                         [("state-table", "product-export",
                           "2026-08-27 06:30:00.000001")])

    def test_watermark_never_regresses(self):
        # a concurrent batch already advanced past us: the conditional update
        # fails and the run row is still recorded
        ddb = FakeStateDdb(existing_watermark="2026-08-28 00:00:00.000000")
        s3 = FakeReportS3(report=REPORT)
        result = record_run.handler(dict(self.EVENT), ddb_client=ddb, s3_client=s3)
        self.assertTrue(result["recorded"])
        self.assertEqual(ddb.updates, [])
        self.assertEqual(len(ddb.items), 1)

    def test_no_high_water_mark_means_no_advance(self):
        # an empty increment covered the window but observed no rows — the
        # stored watermark stays put (strictly-greater predicates make the
        # re-covered window idempotent)
        report = dict(REPORT, extraction=dict(REPORT["extraction"],
                                              high_water_mark=None))
        ddb, s3 = FakeStateDdb(), FakeReportS3(report=report)
        record_run.handler(dict(self.EVENT), ddb_client=ddb, s3_client=s3)
        self.assertEqual(ddb.updates, [])

    def test_missing_report_still_records_the_run(self):
        # pre-UIFPE-168 engine, or a report write that failed: enrichment is
        # best-effort, the run row is not
        ddb, s3 = FakeStateDdb(), FakeReportS3(report=None)
        result = record_run.handler(dict(self.EVENT), ddb_client=ddb, s3_client=s3)
        self.assertTrue(result["recorded"])
        _, item = ddb.items[0]
        summary = json.loads(item["summary"]["S"])
        self.assertNotIn("output_rows", summary)
        self.assertEqual(ddb.updates, [])

    def test_failure_branch_never_reads_the_report_or_advances(self):
        class MustNotBeCalled:
            def get_object(self, Bucket, Key):
                raise AssertionError("the failure branch must not read S3")

        ddb = FakeStateDdb()
        record_run.handler(
            {"export_config_s3_path": "s3://data/configs/export.json",
             "batch_id": "b-9", "status": "failed", "error": "boom"},
            ddb_client=ddb, s3_client=MustNotBeCalled())
        self.assertEqual(ddb.updates, [])
        self.assertEqual(len(ddb.items), 1)


class FakeSchedulerDdb:
    """Catalog + runs + glue-config + schedules tables behind one fake client."""

    def __init__(self, versions=None, sizing=None, schedule=None):
        self.versions = versions if versions is not None else []
        self.sizing = sizing
        self.schedule = schedule  # the export's schedule record item (UIFPE-231)
        self.put_items = []

    def query(self, TableName, KeyConditionExpression, ExpressionAttributeValues):
        job_id = ExpressionAttributeValues[":j"]["S"]
        items = [{"job_id": {"S": job_id}, "version": {"N": str(v["version"])},
                  "data": {"S": json.dumps(v)}}
                 for v in self.versions if v["job_id"] == job_id]
        # the reserved counter item has no data attribute — must be skipped
        items.append({"job_id": {"S": job_id}, "version": {"N": "0"}})
        return {"Items": items}

    def get_item(self, TableName, Key):
        if TableName == os.environ.get("BEACON_TABLE_SCHEDULES"):
            return {"Item": self.schedule} if self.schedule else {}
        if self.sizing is None:
            return {}
        return {"Item": {
            "config_key": {"S": "default"},
            "worker_type": {"S": self.sizing["worker_type"]},
            "workers": {"N": str(self.sizing["workers"])},
            "timeout_minutes": {"N": str(self.sizing["timeout_minutes"])},
        }}

    def put_item(self, TableName, Item):
        self.put_items.append((TableName, Item))


class FakeStarterSfn:
    def __init__(self, running_inputs=None):
        # each entry: the input JSON of a RUNNING execution
        self.running = running_inputs or []
        self.started = []

    def list_executions(self, stateMachineArn, statusFilter):
        return {"executions": [
            {"executionArn": f"arn:exec:{i}", "name": f"b-run-{i}"}
            for i in range(len(self.running))]}

    def describe_execution(self, executionArn):
        i = int(executionArn.rsplit(":", 1)[-1])
        return {"executionArn": executionArn, "name": f"b-run-{i}",
                "input": json.dumps(self.running[i])}

    def start_execution(self, stateMachineArn, name, input):
        self.started.append({"name": name, "input": json.loads(input)})
        return {"executionArn": f"arn:exec:new:{name}"}


VERSION_100001 = {
    "job_id": "100001", "version": 2,
    "s3": {"export_config_uri":
           "s3://uif-beacon-dev-data/configs/100001/v2/export.json"},
}


class TestScheduledStarter(unittest.TestCase):
    def setUp(self):
        os.environ["BEACON_STATE_MACHINE_ARN"] = "arn:sm"
        os.environ["BEACON_TABLE_CATALOG"] = "catalog-table"
        os.environ["BEACON_TABLE_RUNS"] = "runs-table"
        os.environ["BEACON_TABLE_GLUE_CONFIG"] = "glue-config-table"

    def tearDown(self):
        for k in ("BEACON_STATE_MACHINE_ARN", "BEACON_TABLE_CATALOG",
                  "BEACON_TABLE_RUNS", "BEACON_TABLE_GLUE_CONFIG"):
            os.environ.pop(k, None)

    def test_starts_latest_version_with_scheduled_trigger(self):
        v1 = {"job_id": "100001", "version": 1,
              "s3": {"export_config_uri": "s3://old/v1/export.json"}}
        ddb = FakeSchedulerDdb(versions=[v1, VERSION_100001],
                               sizing={"worker_type": "G.2X", "workers": 4,
                                       "timeout_minutes": 90})
        sfn = FakeStarterSfn()
        result = scheduled_starter.handler({"job_id": "100001"},
                                           ddb_client=ddb, sfn_client=sfn)
        self.assertTrue(result["started"])
        started = sfn.started[0]
        self.assertTrue(started["name"].startswith("b-sched-"))
        # LATEST version resolved at fire time, not v1
        self.assertEqual(started["input"]["export_config_s3_path"],
                         VERSION_100001["s3"]["export_config_uri"])
        self.assertEqual(started["input"]["trigger"], "scheduled")
        self.assertEqual(started["input"]["job_config"]["workers"], 4)

    def test_sizing_falls_back_to_defaults(self):
        ddb = FakeSchedulerDdb(versions=[VERSION_100001], sizing=None)
        sfn = FakeStarterSfn()
        scheduled_starter.handler({"job_id": "100001"},
                                  ddb_client=ddb, sfn_client=sfn)
        self.assertEqual(sfn.started[0]["input"]["job_config"],
                         scheduled_starter.DEFAULT_JOB_CONFIG)

    def test_skips_and_records_when_run_in_flight(self):
        # owner decision (27 Aug 2026): never overlap a running batch
        running = {"export_config_s3_path":
                   "s3://uif-beacon-dev-data/configs/100001/v2/export.json"}
        ddb = FakeSchedulerDdb(versions=[VERSION_100001])
        sfn = FakeStarterSfn(running_inputs=[running])
        result = scheduled_starter.handler({"job_id": "100001"},
                                           ddb_client=ddb, sfn_client=sfn)
        self.assertFalse(result["started"])
        self.assertTrue(result["skipped"])
        self.assertEqual(sfn.started, [])
        table, item = ddb.put_items[0]
        self.assertEqual(table, "runs-table")
        self.assertEqual(item["status"]["S"], "skipped")
        self.assertEqual(item["trigger"]["S"], "scheduled")
        self.assertIn("still running", item["error"]["S"])

    def test_other_exports_running_do_not_block(self):
        running = {"export_config_s3_path":
                   "s3://uif-beacon-dev-data/configs/999999/v1/export.json"}
        ddb = FakeSchedulerDdb(versions=[VERSION_100001])
        sfn = FakeStarterSfn(running_inputs=[running])
        result = scheduled_starter.handler({"job_id": "100001"},
                                           ddb_client=ddb, sfn_client=sfn)
        self.assertTrue(result["started"])

    def test_paused_window_skips_and_records(self):
        # UIFPE-231: a fire inside the pause window starts nothing, records a
        # neutral row naming the resume date — the AWS schedule stays enabled
        os.environ["BEACON_TABLE_SCHEDULES"] = "schedules-table"
        try:
            today = datetime.now(timezone.utc).date().isoformat()
            ddb = FakeSchedulerDdb(versions=[VERSION_100001], schedule={
                "job_id": {"S": "100001"},
                "timezone": {"S": "UTC"},
                "paused_from": {"S": today},
                "paused_until": {"S": "2099-12-31"},
            })
            sfn = FakeStarterSfn()
            result = scheduled_starter.handler({"job_id": "100001"},
                                               ddb_client=ddb, sfn_client=sfn)
            self.assertFalse(result["started"])
            self.assertTrue(result["skipped"])
            self.assertEqual(sfn.started, [])
            _, item = ddb.put_items[0]
            self.assertEqual(item["status"]["S"], "skipped")
            self.assertIn("paused until 2099-12-31", item["error"]["S"])
        finally:
            os.environ.pop("BEACON_TABLE_SCHEDULES", None)

    def test_expired_pause_window_runs_normally(self):
        # auto-resume is the window PASSING — no re-enable step exists to fail
        os.environ["BEACON_TABLE_SCHEDULES"] = "schedules-table"
        try:
            ddb = FakeSchedulerDdb(versions=[VERSION_100001], schedule={
                "job_id": {"S": "100001"},
                "timezone": {"S": "UTC"},
                "paused_from": {"S": "2020-01-01"},
                "paused_until": {"S": "2020-01-05"},
            })
            sfn = FakeStarterSfn()
            result = scheduled_starter.handler({"job_id": "100001"},
                                               ddb_client=ddb, sfn_client=sfn)
            self.assertTrue(result["started"])
        finally:
            os.environ.pop("BEACON_TABLE_SCHEDULES", None)

    def test_no_published_version_records_skip(self):
        ddb = FakeSchedulerDdb(versions=[])
        sfn = FakeStarterSfn()
        result = scheduled_starter.handler({"job_id": "100001"},
                                           ddb_client=ddb, sfn_client=sfn)
        self.assertFalse(result["started"])
        self.assertEqual(sfn.started, [])
        _, item = ddb.put_items[0]
        self.assertEqual(item["status"]["S"], "skipped")
        self.assertIn("no published version", item["error"]["S"])

    def test_full_mode_feed_sends_empty_watermark(self):
        # UIFPE-168: the watermark key is part of the input contract — always
        # supplied, "" for full-mode feeds
        ddb = FakeSchedulerDdb(versions=[VERSION_100001])
        sfn = FakeStarterSfn()
        scheduled_starter.handler({"job_id": "100001"},
                                  ddb_client=ddb, sfn_client=sfn)
        self.assertEqual(sfn.started[0]["input"]["watermark"], "")


class FakeIncrementalDdb(FakeSchedulerDdb):
    """Adds the extraction-state table behind the same get_item fake."""

    def __init__(self, versions=None, watermark=None):
        super().__init__(versions=versions)
        self.watermark = watermark

    def get_item(self, TableName, Key):
        if TableName == "state-table":
            if self.watermark is None:
                return {}
            return {"Item": {"job_id": Key["job_id"],
                             "watermark": {"S": self.watermark}}}
        return super().get_item(TableName, Key)


VERSION_INCREMENTAL = {
    "job_id": "100002", "version": 1,
    "settings": {"extraction": {"mode": "incremental"}},
    "s3": {"export_config_uri":
           "s3://uif-beacon-dev-data/configs/100002/v1/export.json"},
}


class TestScheduledStarterWatermark(unittest.TestCase):
    # UIFPE-168: an incremental feed's stored watermark rides the execution
    # input; missing/unreadable state degrades to "" (baseline full extract —
    # over-delivery, never a gap).

    def setUp(self):
        os.environ["BEACON_STATE_MACHINE_ARN"] = "arn:sm"
        os.environ["BEACON_TABLE_CATALOG"] = "catalog-table"
        os.environ["BEACON_TABLE_RUNS"] = "runs-table"
        os.environ["BEACON_TABLE_EXTRACTION_STATE"] = "state-table"

    def tearDown(self):
        for k in ("BEACON_STATE_MACHINE_ARN", "BEACON_TABLE_CATALOG",
                  "BEACON_TABLE_RUNS", "BEACON_TABLE_EXTRACTION_STATE"):
            os.environ.pop(k, None)

    def test_stored_watermark_rides_the_execution_input(self):
        ddb = FakeIncrementalDdb(versions=[VERSION_INCREMENTAL],
                                 watermark="2026-08-27 06:30:00.000001")
        sfn = FakeStarterSfn()
        result = scheduled_starter.handler({"job_id": "100002"},
                                           ddb_client=ddb, sfn_client=sfn)
        self.assertTrue(result["started"])
        self.assertEqual(sfn.started[0]["input"]["watermark"],
                         "2026-08-27 06:30:00.000001")

    def test_no_stored_watermark_means_baseline(self):
        ddb = FakeIncrementalDdb(versions=[VERSION_INCREMENTAL], watermark=None)
        sfn = FakeStarterSfn()
        scheduled_starter.handler({"job_id": "100002"},
                                  ddb_client=ddb, sfn_client=sfn)
        self.assertEqual(sfn.started[0]["input"]["watermark"], "")

    def test_state_table_unset_degrades_to_baseline(self):
        os.environ.pop("BEACON_TABLE_EXTRACTION_STATE", None)
        ddb = FakeIncrementalDdb(versions=[VERSION_INCREMENTAL],
                                 watermark="2026-08-27 06:30:00.000001")
        sfn = FakeStarterSfn()
        scheduled_starter.handler({"job_id": "100002"},
                                  ddb_client=ddb, sfn_client=sfn)
        self.assertEqual(sfn.started[0]["input"]["watermark"], "")


class FakeEvents:
    def __init__(self, fail=False):
        self.entries = []
        self.fail = fail

    def put_events(self, Entries):
        self.entries.extend(Entries)
        if self.fail:
            return {"FailedEntryCount": 1, "Entries": [{"ErrorCode": "X"}]}
        return {"FailedEntryCount": 0, "Entries": [{"EventId": "ev-1"}]}


class TestEmitEvent(unittest.TestCase):
    def test_success_event(self):
        events = FakeEvents()
        result = emit_event.handler(
            {"job_id": "product-export", "batch_id": "b-1",
             "status": "succeeded", "delivered": 3}, events_client=events)
        self.assertEqual(result["emitted"], "beacon.export.completed")
        entry = events.entries[0]
        self.assertEqual(entry["Source"], "uif.beacon")
        self.assertEqual(json.loads(entry["Detail"])["delivered"], 3)

    def test_failure_event_type(self):
        events = FakeEvents()
        result = emit_event.handler(
            {"job_id": "product-export", "batch_id": "b-2",
             "status": "failed", "error": "gate"}, events_client=events)
        self.assertEqual(result["emitted"], "beacon.export.failed")

    def test_failure_event_derives_job_id_from_config_path(self):
        # UIFPE-200: same derivation as record_run, for event consumers
        events = FakeEvents()
        emit_event.handler(
            {"export_config_s3_path":
                 "s3://uif-beacon-dev-data/configs/100001/v2/export.json",
             "batch_id": "b-6", "status": "failed", "error": "boom"},
            events_client=events)
        detail = json.loads(events.entries[0]["Detail"])
        self.assertEqual(detail["job_id"], "100001")

    def test_rejected_entry_raises(self):
        with self.assertRaises(RuntimeError):
            emit_event.handler({"job_id": "j", "batch_id": "b",
                                "status": "succeeded"},
                               events_client=FakeEvents(fail=True))
