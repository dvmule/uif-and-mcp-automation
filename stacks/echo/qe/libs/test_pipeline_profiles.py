# test_pipeline_profiles.py
# Unit tests for the two-profile (initial vs incremental) resolver and the
# corresponding API validator. Runs offline — no AWS calls.

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

# UIFPE-355: paths follow the stack layout. qe/conftest.py already puts src/libs
# and every src/apps/* directory on sys.path, so the two inserts this file used to
# make for lambda/ and scripts/ are gone — only the by-path load below still needs
# a root, and it resolves from the stack root rather than guessing a sibling.
_STACK_ROOT = Path(__file__).resolve().parents[2]

import boto3  # noqa: E402
_real_resource = boto3.resource
_real_client   = boto3.client
boto3.resource = MagicMock(return_value=MagicMock())
boto3.client   = MagicMock(return_value=MagicMock())

import pipeline_profiles as pp  # noqa: E402
import pipeline_controller as pc  # noqa: E402

boto3.resource = _real_resource
boto3.client   = _real_client


# Re-import the migration-script transform so we can unit-test it without
# invoking the CLI.
import importlib.util
_mig_path = _STACK_ROOT / "src" / "apps" / "cli" / "migrate_pipeline_profiles.py"
_spec = importlib.util.spec_from_file_location("migrate_pipeline_profiles", _mig_path)
mig = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mig)


# ─── resolve_phase ────────────────────────────────────────────────

class TestResolvePhase(unittest.TestCase):
    def test_mode_full_is_initial(self):
        self.assertEqual(pp.resolve_phase({"extract": {"mode": "full"}}), "initial")

    def test_sentinel_watermark_is_initial(self):
        self.assertEqual(
            pp.resolve_phase({"extract": {"mode": "incremental", "watermark_value": "1970-01-01T00:00:00"}}),
            "initial",
        )

    def test_empty_string_watermark_is_initial(self):
        self.assertEqual(
            pp.resolve_phase({"extract": {"mode": "incremental", "watermark_value": ""}}),
            "initial",
        )

    def test_missing_watermark_is_initial(self):
        self.assertEqual(
            pp.resolve_phase({"extract": {"mode": "incremental"}}),
            "initial",
        )

    def test_real_timestamp_is_incremental(self):
        self.assertEqual(
            pp.resolve_phase({"extract": {"mode": "incremental",
                                          "watermark_value": "2026-06-10T12:34:56"}}),
            "incremental",
        )

    def test_no_extract_block_is_initial(self):
        self.assertEqual(pp.resolve_phase({}), "initial")


# ─── resolve_extract / resolve_glue layered merge ─────────────────

class TestResolveExtractLegacy(unittest.TestCase):
    """A legacy flat config (no extract.initial) must keep behaving as it did
    pre-migration — the bronze job pulls the same JDBC fields it always did."""

    def test_legacy_initial_returns_flat_fields(self):
        cfg = {"extract": {
            "table": "X", "mode": "incremental",
            "watermark_col": "WM", "watermark_value": "1970-01-01T00:00:00",
            "jdbc_predicates": ["A", "B"], "jdbc_partitions": 4,
        }}
        out = pp.resolve_extract(cfg, "initial")
        self.assertEqual(out["jdbc_predicates"], ["A", "B"])
        self.assertEqual(out["jdbc_partitions"], 4)
        self.assertEqual(out["table"], "X")

    def test_legacy_incremental_returns_lean(self):
        cfg = {"extract": {
            "table": "X", "mode": "incremental",
            "watermark_value": "2026-06-10T00:00:00",
            "jdbc_predicates": ["A", "B"], "jdbc_partitions": 4,
        }}
        out = pp.resolve_extract(cfg, "incremental")
        self.assertIsNone(out["jdbc_predicates"])
        self.assertIsNone(out["jdbc_partitions"])
        self.assertEqual(out["table"], "X")


class TestResolveExtractNewShape(unittest.TestCase):
    def test_initial_block_wins(self):
        cfg = {"extract": {
            "table": "X", "mode": "incremental",
            "initial":     {"jdbc_predicates": ["P"]},
            "incremental": {"jdbc_predicates": None},
        }}
        out = pp.resolve_extract(cfg, "initial")
        self.assertEqual(out["jdbc_predicates"], ["P"])

    def test_incremental_block_overrides_lean(self):
        cfg = {"extract": {
            "table": "X", "mode": "incremental",
            "initial":     {"jdbc_predicates": ["P"]},
            "incremental": {"jdbc_fetchsize": 5000, "jdbc_predicates": None},
        }}
        out = pp.resolve_extract(cfg, "incremental")
        self.assertIsNone(out["jdbc_predicates"])
        self.assertEqual(out["jdbc_fetchsize"], 5000)


class TestResolveGlue(unittest.TestCase):
    def test_legacy_initial_returns_flat(self):
        cfg = {"glue": {"worker_type": "G.4X", "num_workers": 20, "timeout_minutes": 240}}
        out = pp.resolve_glue(cfg, "initial")
        self.assertEqual(out["worker_type"], "G.4X")
        self.assertEqual(out["num_workers"], 20)

    def test_legacy_incremental_returns_lean(self):
        cfg = {"glue": {"worker_type": "G.4X", "num_workers": 20}}
        out = pp.resolve_glue(cfg, "incremental")
        self.assertEqual(out["worker_type"], "G.1X")
        # UIFPE-355: 2, not 1. AWS Glue rejects StartJobRun with
        # NumberOfWorkers=1 on any G-series worker type - sizing_policy.py
        # records the constraint as GLUE_MIN_WORKERS["G.1X"] = 2, and
        # clamp_glue_workers() enforces it. This assertion expected 1, so it was
        # asserting a configuration that cannot launch; LEAN_INCREMENTAL_GLUE is
        # correct at 2 and the test was the stale side.
        self.assertEqual(out["num_workers"], 2)

    def test_new_shape_initial(self):
        cfg = {"glue": {
            "initial":     {"worker_type": "G.2X", "num_workers": 16, "timeout_minutes": 240},
            "incremental": {"worker_type": "G.1X", "num_workers": 1,  "timeout_minutes": 60},
        }}
        self.assertEqual(pp.resolve_glue(cfg, "initial")["num_workers"], 16)
        self.assertEqual(pp.resolve_glue(cfg, "incremental")["num_workers"], 1)


# ─── _validate_extract_and_glue_profiles ──────────────────────────

class TestProfileValidator(unittest.TestCase):
    def _cfg_with_incremental_preds(self, count: int, allow_heavy: bool = False):
        return {
            "pipeline_id": "test",
            "extract": {"mode": "incremental",
                        "incremental": {"jdbc_predicates": [f"WHERE_{i}" for i in range(count)],
                                        "allow_heavy": allow_heavy}},
            "glue":    {"incremental": {"worker_type": "G.1X", "num_workers": 1}},
            "schedule": {"frequency": "30min"},
        }

    def test_lenient_default_does_not_reject(self):
        original = pc.STRICT_PROFILE_VALIDATION
        pc.STRICT_PROFILE_VALIDATION = False
        try:
            body = self._cfg_with_incremental_preds(200)  # 200 predicates, lenient
            self.assertIsNone(pc._validate_extract_and_glue_profiles(body))
        finally:
            pc.STRICT_PROFILE_VALIDATION = original

    def test_strict_rejects_too_many_predicates(self):
        original = pc.STRICT_PROFILE_VALIDATION
        pc.STRICT_PROFILE_VALIDATION = True
        try:
            body = self._cfg_with_incremental_preds(16)
            err = pc._validate_extract_and_glue_profiles(body)
            self.assertIsNotNone(err)
            self.assertIn("jdbc_predicates", err)
        finally:
            pc.STRICT_PROFILE_VALIDATION = original

    def test_allow_heavy_bypasses_strict(self):
        original = pc.STRICT_PROFILE_VALIDATION
        pc.STRICT_PROFILE_VALIDATION = True
        try:
            body = self._cfg_with_incremental_preds(16, allow_heavy=True)
            self.assertIsNone(pc._validate_extract_and_glue_profiles(body))
        finally:
            pc.STRICT_PROFILE_VALIDATION = original

    def test_strict_rejects_worker_combo(self):
        original = pc.STRICT_PROFILE_VALIDATION
        pc.STRICT_PROFILE_VALIDATION = True
        try:
            body = {
                "pipeline_id": "test",
                "extract": {"incremental": {}},
                "glue":    {"incremental": {"worker_type": "G.4X", "num_workers": 16}},
                "schedule": {"frequency": "30min"},
            }
            err = pc._validate_extract_and_glue_profiles(body)
            self.assertIsNotNone(err)
            self.assertIn("worker_type", err)
        finally:
            pc.STRICT_PROFILE_VALIDATION = original


# ─── migrate_pipeline_profiles.transform() ────────────────────────

class TestMigrationTransform(unittest.TestCase):
    def test_legacy_to_new_shape(self):
        legacy = {
            "pipeline_id": "p1",
            "extract": {
                "table": "X.Y", "mode": "incremental",
                "watermark_col": "WM", "watermark_value": "1970-01-01T00:00:00",
                "jdbc_predicates": ["A", "B"], "jdbc_partitions": 4,
                "jdbc_fetchsize": 10000, "jdbc_read_timeout_ms": 7200000,
            },
            "glue": {"worker_type": "G.2X", "num_workers": 16, "timeout_minutes": 240},
        }
        out = mig.transform(legacy)
        self.assertIn("initial", out["extract"])
        self.assertIn("incremental", out["extract"])
        self.assertEqual(out["extract"]["initial"]["jdbc_predicates"], ["A", "B"])
        self.assertIsNone(out["extract"]["incremental"]["jdbc_predicates"])
        # Connection-level knobs propagate to both profiles
        self.assertEqual(out["extract"]["initial"]["jdbc_fetchsize"], 10000)
        self.assertEqual(out["extract"]["incremental"]["jdbc_fetchsize"], 10000)
        # Source contract preserved at root
        self.assertEqual(out["extract"]["table"], "X.Y")
        self.assertEqual(out["extract"]["mode"], "incremental")
        # Old flat JDBC keys removed from extract root
        self.assertNotIn("jdbc_predicates", {k for k in out["extract"].keys()
                                              if k not in ("initial", "incremental")})
        # Glue split
        self.assertEqual(out["glue"]["initial"]["num_workers"], 16)
        self.assertEqual(out["glue"]["incremental"]["num_workers"], 1)
        self.assertEqual(out["glue"]["incremental"]["worker_type"], "G.1X")

    def test_already_migrated_is_idempotent(self):
        cfg = {
            "extract": {"initial": {"jdbc_predicates": ["X"]},
                        "incremental": {"jdbc_predicates": None},
                        "table": "T", "mode": "incremental"},
            "glue":    {"initial": {"num_workers": 4}, "incremental": {"num_workers": 1}},
        }
        self.assertTrue(mig.already_migrated(cfg))


if __name__ == "__main__":
    unittest.main()
