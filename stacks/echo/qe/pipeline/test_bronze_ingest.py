# test_bronze_ingest.py
# Unit tests for the logic functions in bronze-ingest.py.
# Tests run without Spark or AWS credentials — all external calls are mocked.

import json
import sys
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch, call

from job_loader import load_pipeline_module


# ── Stub out awsglue before importing the job module ─────────────
sys.modules["awsglue"]                    = MagicMock()
sys.modules["awsglue.utils"]              = MagicMock()
sys.modules["awsglue.context"]            = MagicMock()
sys.modules["pyspark"]                    = MagicMock()
sys.modules["pyspark.context"]            = MagicMock()
sys.modules["pyspark.sql"]                = MagicMock()
sys.modules["pyspark.sql.functions"]      = MagicMock()


class TestQualityChecks(unittest.TestCase):
    """Tests for Step 7 — run_quality_checks() logic (mocked DataFrame)"""

    def _run_checks(self, rows_total, null_counts_map, min_expected, not_null_cols, phase="initial"):
        """
        Simulates the quality check logic without a real Spark DataFrame.
        Mirrors bronze-ingest.run_quality_checks: min_rows_expected is
        only enforced when phase == "initial" (full or first-run). For
        incremental, any rows_total >= 0 is valid.
        Returns (rows_total, null_counts, rows_rejected) or raises.
        """
        if phase == "initial" and rows_total < min_expected:
            raise ValueError(
                f"Quality gate failed: got {rows_total} rows, expected >= {min_expected}"
            )
        filtered_nulls = {
            col: null_counts_map.get(col, 0)
            for col in not_null_cols
            if col in null_counts_map
        }
        return rows_total, filtered_nulls, 0  # reject_on_nulls=false

    def test_passes_when_rows_above_minimum(self):
        rows, nulls, rejected = self._run_checks(
            rows_total=500,
            null_counts_map={"ORDER_ID": 0},
            min_expected=100,
            not_null_cols=["ORDER_ID"],
        )
        self.assertEqual(rows, 500)
        self.assertEqual(rejected, 0)

    def test_raises_when_initial_rows_below_minimum(self):
        with self.assertRaises(ValueError) as ctx:
            self._run_checks(
                rows_total=50,
                null_counts_map={},
                min_expected=100,
                not_null_cols=[],
                phase="initial",
            )
        self.assertIn("Quality gate failed", str(ctx.exception))

    def test_incremental_with_low_row_count_passes(self):
        """min_rows_expected must NOT be enforced for incremental runs —
        a delta of 2 rows is legitimate, not a partial dump."""
        rows, _, rejected = self._run_checks(
            rows_total=2,
            null_counts_map={},
            min_expected=10,           # would fail under old logic
            not_null_cols=[],
            phase="incremental",
        )
        self.assertEqual(rows, 2)
        self.assertEqual(rejected, 0)

    def test_incremental_with_zero_rows_passes(self):
        """0 rows on incremental = steady-state no-op."""
        rows, _, _ = self._run_checks(
            rows_total=0,
            null_counts_map={},
            min_expected=10,
            not_null_cols=[],
            phase="incremental",
        )
        self.assertEqual(rows, 0)

    def test_null_counts_recorded_but_rows_not_filtered(self):
        rows, nulls, rejected = self._run_checks(
            rows_total=200,
            null_counts_map={"ORDER_ID": 5, "STATUS": 10},
            min_expected=0,
            not_null_cols=["ORDER_ID", "STATUS"],
        )
        self.assertEqual(nulls["ORDER_ID"], 5)
        self.assertEqual(nulls["STATUS"], 10)
        self.assertEqual(rejected, 0)

    def test_zero_min_rows_never_fails_on_count(self):
        rows, _, rejected = self._run_checks(
            rows_total=0,
            null_counts_map={},
            min_expected=0,
            not_null_cols=[],
        )
        self.assertEqual(rows, 0)
        self.assertEqual(rejected, 0)


class TestWatermarkAdvance(unittest.TestCase):
    """Tests for Step 10 — advance_watermark() logic"""

    def test_returns_na_for_full_mode(self):
        ext = {"mode": "full"}
        result = "N/A" if ext["mode"] != "incremental" else "advanced"
        self.assertEqual(result, "N/A")

    def test_incremental_mode_triggers_watermark_update(self):
        ext = {"mode": "incremental", "watermark_col": "LAST_UPDATED"}
        triggered = ext["mode"] == "incremental"
        self.assertTrue(triggered)


class TestDynamoSafeSerializer(unittest.TestCase):
    """Tests for the to_dynamo_safe() float→Decimal helper"""

    def _to_dynamo_safe(self, obj):
        if isinstance(obj, float):
            return Decimal(str(obj))
        if isinstance(obj, dict):
            return {k: self._to_dynamo_safe(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self._to_dynamo_safe(i) for i in obj]
        return obj

    def test_float_converted_to_decimal(self):
        result = self._to_dynamo_safe(3.14)
        self.assertIsInstance(result, Decimal)
        self.assertEqual(result, Decimal("3.14"))

    def test_dict_values_converted_recursively(self):
        result = self._to_dynamo_safe({"rows": 1.0, "count": 5})
        self.assertIsInstance(result["rows"], Decimal)
        self.assertEqual(result["count"], 5)

    def test_list_items_converted(self):
        result = self._to_dynamo_safe([1.5, 2.5, "ok"])
        self.assertIsInstance(result[0], Decimal)
        self.assertIsInstance(result[1], Decimal)
        self.assertEqual(result[2], "ok")

    def test_non_float_primitives_unchanged(self):
        self.assertEqual(self._to_dynamo_safe("hello"), "hello")
        self.assertEqual(self._to_dynamo_safe(42), 42)
        self.assertIsNone(self._to_dynamo_safe(None))


class TestMetricsItem(unittest.TestCase):
    """Tests for Step 11 — metrics item structure"""

    def test_metrics_item_has_required_keys(self):
        required_keys = [
            "job_name", "run_id", "pipeline_id", "extract_mode",
            "rows_read", "rows_written", "rows_rejected",
            "status", "started_at", "completed_at", "duration_sec", "ttl",
        ]
        item = {
            "job_name":      "echo-dev-bronze-ingest",
            "run_id":        "jr_test123",
            "pipeline_id":   "oracle-erp-orders",
            "extract_mode":  "incremental",
            "rows_read":     1000,
            "rows_written":  1000,
            "rows_rejected": 0,
            "status":        "SUCCEEDED",
            "started_at":    "2026-05-12T08:00:00+00:00",
            "completed_at":  "2026-05-12T08:02:00+00:00",
            "duration_sec":  120,
            "ttl":           9999999999,
        }
        for key in required_keys:
            self.assertIn(key, item, f"Missing required metrics key: {key}")

    def test_status_is_failed_on_exception(self):
        status = "SUCCEEDED"
        try:
            raise RuntimeError("Simulated failure")
        except Exception:
            status = "FAILED"
        self.assertEqual(status, "FAILED")

    def test_status_succeeded_on_clean_run(self):
        status = "FAILED"
        try:
            pass  # no exception
            status = "SUCCEEDED"
        except Exception:
            status = "FAILED"
        self.assertEqual(status, "SUCCEEDED")


class TestResolveSilverTrigger(unittest.TestCase):
    """The REAL routing policy, imported — not a copy.

    This class used to reimplement resolve_silver_trigger locally, with a
    comment asking whoever changed one to keep the other in sync. That is why
    the UIFPE-355 job renames shipped broken with 477 tests green: Terraform's
    job names changed, the copy agreed with the stale original, and nothing
    compared either to glue.tf. The logic now lives in silver_routing.py, which
    has no Spark or boto3 imports precisely so this test can load it.

    Job names are built from the prefix the job is GIVEN. Passing an env name
    is no longer possible — see test_job_name_drift.py.
    """

    PREFIX = "uif-echo-dev"

    @classmethod
    def setUpClass(cls):
        cls._routing = load_pipeline_module("silver_routing.py")

    _DEFAULT = object()  # so prefix="" reaches the function instead of
                         # being swallowed by a truthiness default

    def _resolve(self, *, dest_cloud, write_mode, rows_read, prefix=_DEFAULT):
        return self._routing.resolve_silver_trigger(
            dest_cloud=dest_cloud,
            write_mode=write_mode,
            resource_prefix=self.PREFIX if prefix is self._DEFAULT else prefix,
            rows_read=rows_read,
        )

    def test_a_missing_prefix_raises_rather_than_guessing(self):
        with self.assertRaises(ValueError):
            self._resolve(dest_cloud="aws", write_mode=None, rows_read=1, prefix="")

    # ── AWS: Spark Iceberg job, 0-row guard ────────────────────────
    def test_aws_routes_to_silver_transform_with_rows(self):
        job, fire = self._resolve(dest_cloud="aws", write_mode=None, rows_read=42)
        self.assertEqual(job, "uif-echo-dev-silver-transform")
        self.assertTrue(fire)

    def test_aws_skips_on_zero_rows(self):
        _, fire = self._resolve(dest_cloud="aws", write_mode=None, rows_read=0)
        self.assertFalse(fire)

    # ── GCP Iceberg: Spark, 0-row guard ─────────────────────────────
    def test_gcp_default_routes_to_iceberg_with_rows(self):
        job, fire = self._resolve(dest_cloud="gcp", write_mode=None, rows_read=1,
                                  prefix="eds-prod")
        self.assertEqual(job, "eds-prod-silver-transform-gcs-iceberg")
        self.assertTrue(fire)

    def test_gcp_iceberg_explicit_skips_zero(self):
        _, fire = self._resolve(dest_cloud="gcp", write_mode="iceberg_biglake",
                                rows_read=0)
        self.assertFalse(fire)

    # ── GCP bq_native: new Python Shell job, ALWAYS triggers ───────
    def test_gcp_bq_native_routes_to_python_shell(self):
        job, fire = self._resolve(dest_cloud="gcp", write_mode="bq_native",
                                  rows_read=999)
        self.assertEqual(job, "uif-echo-dev-silver-transform-bq-native")
        self.assertTrue(fire)

    def test_gcp_bq_native_triggers_on_zero_rows(self):
        """The 0-row recovery fix — bq_native always fires so prior failed
        Silver triggers (IAM gap, transient BQ error) self-heal on the next
        Bronze run."""
        job, fire = self._resolve(dest_cloud="gcp", write_mode="bq_native",
                                  rows_read=0)
        self.assertEqual(job, "uif-echo-dev-silver-transform-bq-native")
        self.assertTrue(fire, "bq_native must trigger even on 0 rows for recovery")

    def test_gcp_bq_native_case_insensitive(self):
        _, fire = self._resolve(dest_cloud="gcp", write_mode="BQ_NATIVE",
                                rows_read=0)
        self.assertTrue(fire)

    def test_prefix_propagates(self):
        # Each stack's prefix, including the two that are not echo-{env} at
        # all: dev is uif-echo-dev and prod is eds-prod.
        for prefix in ("uif-echo-dev", "echo-uat", "eds-prod"):
            with self.subTest(prefix=prefix):
                job, _ = self._resolve(dest_cloud="gcp", write_mode="bq_native",
                                       rows_read=1, prefix=prefix)
                self.assertEqual(job, f"{prefix}-silver-transform-bq-native")


# ── extract.custom_schema (Spark customSchema passthrough) ────────
#
# Spark maps an unconstrained Oracle NUMBER to DecimalType(38,10). A value
# needing >38 unscaled digits aborts the read with
# DECIMAL_PRECISION_EXCEEDS_MAX_PRECISION. extract.custom_schema overrides the
# inferred type for named columns only, so `SELECT *` still picks up new
# source columns — the alternative is enumerating extract.columns purely to
# cast one field on a 300-column EBS table.
#
# The failure mode this guards is silent: _resolve_extract only forwards
# top-level extract keys that appear in _SOURCE_CONTRACT, and when a phase
# block exists it does out.update(phase_block) and drops everything else. A
# custom_schema outside the contract would never reach Spark, with no error.
_MODULE_SRC = None


def _module_source():
    """Read bronze-ingest.py as text.

    The behaviour tests below copy the logic (this file's convention — see
    TestResolveSilverTrigger — because importing the Bronze module needs the
    full Spark/boto3/DynamoDB bootstrap). A copy cannot catch someone dropping
    the key from the real _SOURCE_CONTRACT, so the guard tests assert against
    the actual source instead.
    """
    global _MODULE_SRC
    if _MODULE_SRC is None:
        import os
        # UIFPE-355: glue_jobs/ moved to src/apps/pipeline/.
        path = os.path.join(os.path.dirname(__file__), "..", "..",
                            "src", "apps", "pipeline",
                            "bronze-ingest.py")
        with open(path, "r", encoding="utf-8") as fh:
            _MODULE_SRC = fh.read()
    return _MODULE_SRC


class TestCustomSchemaResolution(unittest.TestCase):
    """extract.custom_schema must survive phase resolution on BOTH phases.
    Copies _resolve_extract — keep in sync with bronze-ingest."""

    _LEAN_INC_EXTRACT = {
        "jdbc_partition_col": None,
        "jdbc_lower_bound":   None,
        "jdbc_upper_bound":   None,
        "jdbc_partitions":    None,
        "jdbc_predicates":    None,
    }
    _SOURCE_CONTRACT = {
        "table", "mode", "watermark_col", "watermark_fallback_col",
        "watermark_value", "columns", "custom_sql", "custom_schema",
    }

    def _resolve_extract(self, cfg, phase):
        e = cfg.get("extract") or {}
        out = {}
        if phase == "incremental":
            out.update(self._LEAN_INC_EXTRACT)
        for k in self._SOURCE_CONTRACT:
            if k in e:
                out[k] = e[k]
        phase_block = e.get(phase)
        if phase_block is None:
            if phase == "initial":
                for k, v in e.items():
                    if k in self._SOURCE_CONTRACT or k in ("initial", "incremental"):
                        continue
                    out[k] = v
        else:
            out.update(phase_block)
        return out

    def _cfg(self, **extra):
        """Shape of the real failing pipeline: both phase blocks present."""
        ext = {
            "table": "APPS.MTL_SYSTEM_ITEMS_B",
            "mode": "incremental",
            "watermark_col": "LAST_UPDATE_DATE",
            "watermark_value": "1970-01-01T00:00:00",
            "initial":     {"jdbc_partition_col": None},
            "incremental": {"jdbc_partition_col": None},
        }
        ext.update(extra)
        return {"extract": ext}

    def test_carried_into_initial_phase(self):
        ext = self._resolve_extract(
            self._cfg(custom_schema="COVERAGE_SCHEDULE_ID STRING"), "initial")
        self.assertEqual(ext.get("custom_schema"), "COVERAGE_SCHEDULE_ID STRING")

    def test_carried_into_incremental_phase(self):
        ext = self._resolve_extract(
            self._cfg(custom_schema="COVERAGE_SCHEDULE_ID STRING"), "incremental")
        self.assertEqual(ext.get("custom_schema"), "COVERAGE_SCHEDULE_ID STRING")

    def test_absent_when_not_configured(self):
        for phase in ("initial", "incremental"):
            with self.subTest(phase=phase):
                ext = self._resolve_extract(self._cfg(), phase)
                self.assertIsNone(ext.get("custom_schema"))

    def test_key_outside_the_contract_is_dropped(self):
        """Why custom_schema has to be in _SOURCE_CONTRACT: with a phase block
        present, a non-contract top-level key never reaches the job."""
        cfg = self._cfg(not_in_contract="COVERAGE_SCHEDULE_ID STRING")
        for phase in ("initial", "incremental"):
            with self.subTest(phase=phase):
                ext = self._resolve_extract(cfg, phase)
                self.assertIsNone(ext.get("not_in_contract"))


class TestCustomSchemaWiredIntoModule(unittest.TestCase):
    """Source-level guards — a copied-logic test cannot catch these."""

    def test_in_source_contract(self):
        src = _module_source()
        start = src.index("_SOURCE_CONTRACT = {")
        block = src[start:src.index("}", start)]
        self.assertIn('"custom_schema"', block,
                      "custom_schema missing from _SOURCE_CONTRACT — "
                      "_resolve_extract would silently drop it")

    def test_read_from_extract_config(self):
        self.assertIn('ext.get("custom_schema")', _module_source())

    def test_applied_on_predicate_read_path(self):
        self.assertIn('props["customSchema"] = custom_schema', _module_source())

    def test_applied_on_single_partition_read_path(self):
        self.assertIn('.option("customSchema", custom_schema)', _module_source())

    def test_override_is_logged(self):
        self.assertIn("[JDBC] customSchema override", _module_source())


if __name__ == "__main__":
    unittest.main()
