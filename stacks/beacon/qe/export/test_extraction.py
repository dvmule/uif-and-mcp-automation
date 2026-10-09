# test_extraction.py
# UIFPE-168 at the stage-driver level: how beacon_export_job.run turns the
# export config's extraction block + the per-run watermark into engine
# parameters, what the summary reports, which gate semantics apply, and the
# report.json handoff the success branch reads. Fake spark/S3 — no Spark,
# no AWS (same harness as test_beacon_job_packaging).

import io
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import dynamic_mapping
from beacon_export import beacon_export_job

CSV_BYTES = b"ppid,isbn13\r\nA1,9781234567890\r\n"


class FakeS3:
    def __init__(self):
        self.objects = {}

    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)])}

    def put_object(self, Bucket, Key, Body, **kw):
        self.objects[(Bucket, Key)] = Body if isinstance(Body, bytes) else Body.read()

    def copy_object(self, Bucket, Key, CopySource):
        self.objects[(Bucket, Key)] = self.objects[
            (CopySource["Bucket"], CopySource["Key"])]

    def list_objects_v2(self, Bucket, Prefix):
        return {"Contents": [{"Key": k, "Size": len(v)}
                             for (b, k), v in self.objects.items()
                             if b == Bucket and k.startswith(Prefix)]}


class FakeWriter:
    def __init__(self, s3):
        self.s3 = s3

    def mode(self, *_):
        return self

    def options(self, **_):
        return self

    def format(self, *_):
        return self

    def save(self, prefix):
        bucket, _, key = prefix.removeprefix("s3://").partition("/")
        self.s3.objects[(bucket, key.rstrip("/") + "/part-00000.csv")] = CSV_BYTES


class FakeDF:
    columns = ["ppid", "isbn13"]
    # the render stage inspects dtypes for the complex-type guard (UIFPE-237)
    dtypes = [("ppid", "string"), ("isbn13", "string")]

    def __init__(self, s3):
        self._s3 = s3

    def coalesce(self, *_):
        return self

    @property
    def write(self):
        return FakeWriter(self._s3)


class FakeSpark:
    def __init__(self, s3):
        self.read = SimpleNamespace(parquet=lambda prefix: FakeDF(s3))


def make_cfg(extraction=None):
    cfg = {
        "version": "1.0",
        "job_id": "job",
        "mapping_config_s3_path": "s3://bkt/configs/mapping.json",
        "canonical_s3_prefix": "s3://bkt/staging/{batch_id}/canonical/",
        "quality_gates": {"min_rows": 1, "max_rejection_pct": 2.0},
        "renderings": [{"format": "csv",
                        "options": {"delimiter": ",", "formula_escape": False}}],
        "packaging": {
            "filename_pattern": "job_{yyyyMMdd}_{batch_id}.{ext}",
            "pickup_s3_prefix": "s3://bkt/pickup/{batch_id}/",
            "manifest": {"enabled": True, "checksum": "sha256"},
        },
        "delivery": {"channel": "s3", "adapter": "s3_copy",
                     "destination_s3_prefix": "s3://partner/in"},
    }
    if extraction is not None:
        cfg["extraction"] = extraction
    return cfg


INCREMENTAL = {"mode": "incremental", "source": "product",
               "watermark_column": "_gold_updated_at"}


class TestExtractionModes(unittest.TestCase):
    def _run(self, cfg, watermark=None, output_rows=1,
             high_water_mark="2026-08-27 06:30:00.000001"):
        """Drive run() with fakes; returns (summary, captured engine kwargs,
        captured gate kwargs, the S3 fake)."""
        s3 = FakeS3()
        s3.objects[("bkt", "configs/mapping.json")] = b"{}"
        engine_kw, gate_kw = {}, {}
        report = SimpleNamespace(
            to_dict=lambda: {"status": "succeeded", "output_rows": output_rows,
                             "rejected_rows": 0,
                             "high_water_mark": high_water_mark},
            high_water_mark=high_water_mark)

        def fake_engine_run(*a, **k):
            engine_kw.update(k)
            return report

        def fake_gate(report_dict, gates, previous=None, incremental=False):
            gate_kw.update(incremental=incremental)
            return SimpleNamespace(passed=True, to_dict=lambda: {"passed": True})

        with patch.object(dynamic_mapping, "load_config", lambda path: {}), \
             patch.object(dynamic_mapping, "run", fake_engine_run), \
             patch.object(beacon_export_job.quality_gate, "evaluate", fake_gate):
            summary = beacon_export_job.run(
                FakeSpark(s3), s3, None, cfg, "b-1", deliver=False,
                watermark=watermark)
        return summary, engine_kw, gate_kw, s3

    def test_missing_extraction_key_is_todays_full_load(self):
        # the D24/UIFPE-172 promise: pre-feature configs behave identically
        summary, engine_kw, gate_kw, _ = self._run(make_cfg())
        self.assertIsNone(engine_kw["source_filters"])
        self.assertIsNone(engine_kw["track_max"])
        self.assertFalse(gate_kw["incremental"])
        self.assertNotIn("extraction", summary)

    def test_explicit_full_mode_matches_missing_key(self):
        summary, engine_kw, gate_kw, _ = self._run(
            make_cfg(extraction={"mode": "full"}))
        self.assertIsNone(engine_kw["source_filters"])
        self.assertIsNone(engine_kw["track_max"])
        self.assertNotIn("extraction", summary)

    def test_incremental_with_watermark_builds_window_predicate(self):
        summary, engine_kw, gate_kw, _ = self._run(
            make_cfg(extraction=INCREMENTAL),
            watermark="2026-08-26 06:00:00.000000")
        self.assertEqual(
            engine_kw["source_filters"],
            {"product": "_gold_updated_at > TIMESTAMP '2026-08-26 06:00:00.000000'"})
        self.assertEqual(engine_kw["track_max"],
                         {"source": "product", "column": "_gold_updated_at"})
        # the windowed extract uses incremental gate semantics
        self.assertTrue(gate_kw["incremental"])
        self.assertEqual(summary["extraction"], {
            "mode": "incremental", "applied": "incremental",
            "watermark_in": "2026-08-26 06:00:00.000000",
            "high_water_mark": "2026-08-27 06:30:00.000001"})

    def test_incremental_without_watermark_is_the_baseline_full_extract(self):
        # first run / after reset: no window predicate, full gate semantics,
        # but the high-water mark is still tracked to seed the state
        summary, engine_kw, gate_kw, _ = self._run(make_cfg(extraction=INCREMENTAL))
        self.assertIsNone(engine_kw["source_filters"])
        self.assertEqual(engine_kw["track_max"],
                         {"source": "product", "column": "_gold_updated_at"})
        self.assertFalse(gate_kw["incremental"])
        self.assertEqual(summary["extraction"]["applied"], "full_baseline")
        self.assertIsNone(summary["extraction"]["watermark_in"])
        self.assertEqual(summary["extraction"]["high_water_mark"],
                         "2026-08-27 06:30:00.000001")

    def test_empty_increment_reports_no_high_water_mark(self):
        # a no-change window: the watermark must NOT move (max of zero rows)
        summary, _, _, _ = self._run(make_cfg(extraction=INCREMENTAL),
                                     watermark="2026-08-26 06:00:00.000000",
                                     output_rows=0, high_water_mark=None)
        self.assertIsNone(summary["extraction"]["high_water_mark"])
        self.assertEqual(summary["status"], "succeeded")

    def test_malformed_watermark_refused_before_the_engine(self):
        # the watermark is spliced into a Spark SQL predicate — anything but
        # a plain timestamp is an injection attempt or corrupted state
        for bad in ("2026-08-26' OR 1=1 --", "yesterday", "2026-08-26",
                    "2026-08-26 06:00:00.0000001",
                    "2026-08-26 06:00:00\n"):  # $ would accept this; \Z must not
            with self.assertRaises(ValueError, msg=f"{bad!r} accepted"):
                self._run(make_cfg(extraction=INCREMENTAL), watermark=bad)

    def test_watermark_against_full_mode_config_refused(self):
        # a stale starter passing a watermark to a full-mode version is a
        # contract violation, not something to silently ignore
        with self.assertRaises(ValueError):
            self._run(make_cfg(extraction={"mode": "full"}),
                      watermark="2026-08-26 06:00:00.000000")

    def test_report_json_written_for_the_success_branch(self):
        # UIFPE-168: the state machine discards the task output
        # (ResultPath=null) — record_run re-reads the summary from
        # <canonical>/_report/report.json (underscore prefix: Spark's file
        # index ignores it when the render stage reads canonical back)
        summary, _, _, s3 = self._run(make_cfg(extraction=INCREMENTAL),
                                      watermark="2026-08-26 06:00:00.000000")
        body = s3.objects[("bkt", "staging/b-1/canonical/_report/report.json")]
        self.assertEqual(json.loads(body), summary)

    def test_report_json_written_for_full_loads_too(self):
        # row counts in the runs table apply to every mode
        summary, _, _, s3 = self._run(make_cfg())
        body = s3.objects[("bkt", "staging/b-1/canonical/_report/report.json")]
        self.assertEqual(json.loads(body)["transform"]["output_rows"], 1)
