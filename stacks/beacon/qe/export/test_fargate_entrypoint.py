# test_fargate_entrypoint.py
# The shared batch entrypoint's contract: argument parsing, the run summary
# as the last stdout line, and the return code as the branch signal. The
# session factory is injected by the runtime (glue_entrypoint since
# UIFPE-215 deleted the Fargate one). Factories are injected — no Spark,
# no AWS.

import io
import json
import unittest
from contextlib import redirect_stdout

from beacon_export import fargate_entrypoint

EXPORT_CONFIG = {
    "version": "1.0",
    "job_id": "t",
    "mapping_config_s3_path": "s3://b/m.json",
    "canonical_s3_prefix": "s3://b/s/{batch_id}/c/",
    "quality_gates": {"min_rows": 1, "max_rejection_pct": 2.0},
    "renderings": [{"format": "csv", "options": {}}],
    "packaging": {"filename_pattern": "e_{batch_id}.{ext}",
                  "pickup_s3_prefix": "s3://b/p/{batch_id}/"},
    "delivery": {"channel": "s3", "adapter": "s3_copy",
                 "destination_s3_prefix": "s3://dest/inbound"},
}


class FakeS3:
    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(json.dumps(EXPORT_CONFIG).encode())}


def fake_clients():
    return FakeS3(), object()


class TestParseArgs(unittest.TestCase):
    def test_required_args(self):
        args = fargate_entrypoint.parse_args(
            ["--export-config-s3-path", "s3://b/e.json", "--batch-id", "b-1"])
        self.assertEqual(args.batch_id, "b-1")
        self.assertIsNone(args.previous_output_rows)

    def test_previous_rows_optional_int(self):
        args = fargate_entrypoint.parse_args(
            ["--export-config-s3-path", "s3://b/e.json", "--batch-id", "b-1",
             "--previous-output-rows", "48112"])
        self.assertEqual(args.previous_output_rows, 48112)

    def test_watermark_optional(self):
        # UIFPE-168: absent = full extract (or an incremental feed's baseline)
        args = fargate_entrypoint.parse_args(
            ["--export-config-s3-path", "s3://b/e.json", "--batch-id", "b-1"])
        self.assertIsNone(args.watermark)
        args = fargate_entrypoint.parse_args(
            ["--export-config-s3-path", "s3://b/e.json", "--batch-id", "b-1",
             "--watermark", "2026-08-27 06:30:00.000001"])
        self.assertEqual(args.watermark, "2026-08-27 06:30:00.000001")

    def test_missing_batch_id_exits(self):
        with self.assertRaises(SystemExit):
            fargate_entrypoint.parse_args(["--export-config-s3-path", "s3://b/e.json"])


class TestMain(unittest.TestCase):
    ARGV = ["--export-config-s3-path", "s3://b/export.json", "--batch-id", "b-9"]

    def test_success_prints_summary_and_exits_zero(self):
        captured = {}

        def runner(spark, s3, secrets, cfg, batch_id, previous, deliver=True,
                   watermark=None):
            captured.update(cfg=cfg, batch_id=batch_id, previous=previous,
                            deliver=deliver, watermark=watermark)
            return {"status": "succeeded", "batch_id": batch_id}

        out = io.StringIO()
        with redirect_stdout(out):
            code = fargate_entrypoint.main(
                self.ARGV, session_factory=lambda: "fake-spark",
                clients_factory=fake_clients, runner=runner)

        self.assertEqual(code, 0)
        self.assertEqual(captured["batch_id"], "b-9")
        self.assertEqual(captured["cfg"]["job_id"], "t")
        self.assertTrue(captured["deliver"])  # default: monolithic path delivers
        self.assertIsNone(captured["watermark"])  # absent = full extract
        # the run report of record is the LAST stdout line
        last_line = out.getvalue().strip().splitlines()[-1]
        self.assertEqual(json.loads(last_line)["status"], "succeeded")

    def test_skip_delivery_flag_reaches_runner(self):
        captured = {}

        def runner(spark, s3, secrets, cfg, batch_id, previous, deliver=True,
                   watermark=None):
            captured["deliver"] = deliver
            return {"status": "succeeded"}

        code = fargate_entrypoint.main(
            self.ARGV + ["--skip-delivery"],
            session_factory=lambda: "fake-spark",
            clients_factory=fake_clients, runner=runner)
        self.assertEqual(code, 0)
        self.assertFalse(captured["deliver"])

    def test_watermark_reaches_runner_and_empty_means_none(self):
        # UIFPE-168: "" arrives when a starter has no stored watermark — the
        # runner must see None (baseline/full), never an empty string
        captured = {}

        def runner(spark, s3, secrets, cfg, batch_id, previous, deliver=True,
                   watermark=None):
            captured["watermark"] = watermark
            return {"status": "succeeded"}

        fargate_entrypoint.main(
            self.ARGV + ["--watermark", "2026-08-27 06:30:00.000001"],
            session_factory=lambda: "fake-spark",
            clients_factory=fake_clients, runner=runner)
        self.assertEqual(captured["watermark"], "2026-08-27 06:30:00.000001")

        fargate_entrypoint.main(
            self.ARGV + ["--watermark", ""],
            session_factory=lambda: "fake-spark",
            clients_factory=fake_clients, runner=runner)
        self.assertIsNone(captured["watermark"])

    def test_gate_breach_exits_nonzero(self):
        def runner(*a, **k):
            raise RuntimeError("quality gate breached; nothing rendered or delivered")

        code = fargate_entrypoint.main(
            self.ARGV, session_factory=lambda: "fake-spark",
            clients_factory=fake_clients, runner=runner)
        self.assertEqual(code, 1)

    def test_invalid_config_exits_nonzero_before_spark(self):
        class BadS3:
            def get_object(self, Bucket, Key):
                return {"Body": io.BytesIO(b'{"version": "1.0"}')}  # incomplete

        def must_not_be_called():
            raise AssertionError("Spark session must not be built for an invalid config")

        code = fargate_entrypoint.main(
            self.ARGV, session_factory=must_not_be_called,
            clients_factory=lambda: (BadS3(), object()),
            runner=lambda *a, **k: {})
        self.assertEqual(code, 1)
