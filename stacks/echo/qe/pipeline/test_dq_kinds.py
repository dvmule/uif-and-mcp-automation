# test_dq_kinds.py
# Unit tests for the per-kind handlers in src/apps/pipeline/dq-kinds/.
# Kebab-case layout — handlers load via job_loader (same as silver-quality-check).

import sys
import unittest
from unittest.mock import MagicMock

from job_loader import load_dq_kind_module, load_pipeline_module

for mod in (
    "awsglue", "awsglue.utils", "awsglue.context", "awsglue.job",
    "pyspark", "pyspark.context", "pyspark.sql", "pyspark.sql.functions",
):
    sys.modules.setdefault(mod, MagicMock())


class TestReconcileKind(unittest.TestCase):
    def _config(self, **overrides):
        cfg = {
            "check_id": "reconcile-test",
            "kind": "reconcile",
            "subject": {"layer": "silver", "database": "silver_x", "table": "t_x"},
            "parameters": {
                "pk_cols": ["ID"],
                "max_deletes_per_run": 100,
                "dry_run": True,
                "oracle_secret_arn": "arn:aws:secretsmanager:eu-west-1:000:secret:x",
                "jdbc_url": "jdbc:oracle:thin:@//h:1521/s",
            },
        }
        cfg.update(overrides)
        return cfg

    def test_dry_run_returns_skipped(self):
        run_reconcile = load_dq_kind_module("reconcile").run_reconcile
        spark = MagicMock()
        orphans_df = MagicMock(); orphans_df.count.return_value = 3
        spark.sql.return_value = orphans_df
        result = run_reconcile(spark, self._config(),
                               oracle_pk_loader=lambda **_: MagicMock(count=lambda: 100),
                               iceberg_pk_loader=lambda **_: MagicMock(count=lambda: 103))
        self.assertEqual(result["status"], "SKIPPED")
        self.assertEqual(result["orphan_count"], 3)
        self.assertEqual(result["rows_deleted"], 0)
        self.assertIn("dry-run", (result.get("message") or ""))

    def test_safety_cap_exceeded_returns_failed(self):
        run_reconcile = load_dq_kind_module("reconcile").run_reconcile
        spark = MagicMock()
        orphans_df = MagicMock(); orphans_df.count.return_value = 9999
        spark.sql.return_value = orphans_df
        cfg = self._config()
        cfg["parameters"]["dry_run"] = False
        result = run_reconcile(spark, cfg,
                               oracle_pk_loader=lambda **_: MagicMock(count=lambda: 1),
                               iceberg_pk_loader=lambda **_: MagicMock(count=lambda: 10000))
        self.assertEqual(result["status"], "FAILED")
        self.assertIn("safety cap", (result.get("message") or "").lower())

    def test_zero_orphans_succeeded_no_delete_call(self):
        run_reconcile = load_dq_kind_module("reconcile").run_reconcile
        spark = MagicMock()
        orphans_df = MagicMock(); orphans_df.count.return_value = 0
        spark.sql.return_value = orphans_df
        cfg = self._config()
        cfg["parameters"]["dry_run"] = False
        result = run_reconcile(spark, cfg,
                               oracle_pk_loader=lambda **_: MagicMock(count=lambda: 50),
                               iceberg_pk_loader=lambda **_: MagicMock(count=lambda: 50))
        self.assertEqual(result["status"], "SUCCEEDED")
        self.assertEqual(result["orphan_count"], 0)
        self.assertEqual(result["rows_deleted"], 0)


class TestNullRateKind(unittest.TestCase):
    def _make_spark_with_table(self, total_rows, null_rows):
        spark = MagicMock()
        def sql_side(query):
            df = MagicMock()
            if "WHERE" in query.upper():
                df.first.return_value = MagicMock(__getitem__=lambda _self, _k: null_rows)
            else:
                df.first.return_value = MagicMock(__getitem__=lambda _self, _k: total_rows)
            return df
        spark.sql.side_effect = sql_side
        return spark

    def _config(self, column="COL_A", threshold_pct=0.1):
        return {
            "check_id": "null-rate-test",
            "kind": "null-rate",
            "subject": {"layer": "silver", "database": "silver_x", "table": "t_x"},
            "parameters": {"column": column, "threshold_pct": threshold_pct},
        }

    def test_below_threshold_succeeded(self):
        run_null_rate = load_dq_kind_module("null-rate").run_null_rate
        spark = self._make_spark_with_table(total_rows=5, null_rows=0)
        result = run_null_rate(spark, self._config())
        self.assertEqual(result["status"], "SUCCEEDED")
        self.assertAlmostEqual(result["null_ratio"], 0.0)

    def test_at_or_above_threshold_failed(self):
        run_null_rate = load_dq_kind_module("null-rate").run_null_rate
        spark = self._make_spark_with_table(total_rows=5, null_rows=1)
        result = run_null_rate(spark, self._config(threshold_pct=0.1))
        self.assertEqual(result["status"], "FAILED")
        self.assertAlmostEqual(result["null_ratio"], 0.2)
        self.assertIn("exceeds threshold", (result.get("message") or "").lower())

    def test_empty_table_returns_skipped(self):
        run_null_rate = load_dq_kind_module("null-rate").run_null_rate
        spark = self._make_spark_with_table(total_rows=0, null_rows=0)
        result = run_null_rate(spark, self._config())
        self.assertEqual(result["status"], "SKIPPED")
        self.assertIn("empty", (result.get("message") or "").lower())

    def test_missing_column_param_failed(self):
        run_null_rate = load_dq_kind_module("null-rate").run_null_rate
        spark = MagicMock()
        cfg = self._config()
        cfg["parameters"].pop("column")
        result = run_null_rate(spark, cfg)
        self.assertEqual(result["status"], "FAILED")
        self.assertIn("column", (result.get("message") or "").lower())


class TestDispatcher(unittest.TestCase):
    def test_reconcile_routes_to_run_reconcile(self):
        dispatch_check = load_pipeline_module("silver-quality-check.py").dispatch_check
        cfg = {"kind": "reconcile", "subject": {"database": "d", "table": "t"},
               "parameters": {"pk_cols": ["ID"], "max_deletes_per_run": 1,
                              "dry_run": True, "oracle_secret_arn": "arn:x",
                              "jdbc_url": "jdbc:x"}}
        spark = MagicMock()
        orphans_df = MagicMock(); orphans_df.count.return_value = 0
        spark.sql.return_value = orphans_df
        result = dispatch_check("reconcile", spark, cfg,
                                oracle_pk_loader=lambda **_: MagicMock(count=lambda: 0),
                                iceberg_pk_loader=lambda **_: MagicMock(count=lambda: 0))
        self.assertIn(result["status"], ("SUCCEEDED", "SKIPPED"))

    def test_null_rate_routes_to_run_null_rate(self):
        dispatch_check = load_pipeline_module("silver-quality-check.py").dispatch_check
        cfg = {"kind": "null-rate", "subject": {"database": "d", "table": "t"},
               "parameters": {"column": "C", "threshold_pct": 0.5}}
        spark = MagicMock()
        df = MagicMock(); df.first.return_value = MagicMock(__getitem__=lambda _self, _k: 10)
        spark.sql.return_value = df
        result = dispatch_check("null-rate", spark, cfg)
        self.assertEqual(result["status"], "FAILED")

    def test_unimplemented_kind_returns_not_implemented(self):
        dispatch_check = load_pipeline_module("silver-quality-check.py").dispatch_check
        for k in ("row-count", "freshness", "drift", "uniqueness", "custom-sql"):
            with self.subTest(kind=k):
                result = dispatch_check(k, MagicMock(), {"kind": k, "subject": {}, "parameters": {}})
                self.assertEqual(result["status"], "NOT_IMPLEMENTED")
                self.assertIn(k, (result.get("message") or ""))

    def test_unknown_kind_returns_failed(self):
        dispatch_check = load_pipeline_module("silver-quality-check.py").dispatch_check
        result = dispatch_check("does-not-exist", MagicMock(),
                                {"kind": "does-not-exist", "subject": {}, "parameters": {}})
        self.assertEqual(result["status"], "FAILED")
        self.assertIn("unknown", (result.get("message") or "").lower())


if __name__ == "__main__":
    unittest.main()
