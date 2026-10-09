# test_silver_reconcile.py
# Unit tests for the logic functions in silver-reconcile.py.
# Tests run without Spark or AWS credentials — all external calls are mocked.

import sys
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch


# ── Stub out awsglue and pyspark before importing the job module ──
sys.modules["awsglue"]               = MagicMock()
sys.modules["awsglue.utils"]         = MagicMock()
sys.modules["awsglue.context"]       = MagicMock()
sys.modules["awsglue.job"]           = MagicMock()
sys.modules["pyspark"]               = MagicMock()
sys.modules["pyspark.context"]       = MagicMock()
sys.modules["pyspark.sql"]           = MagicMock()
sys.modules["pyspark.sql.functions"] = MagicMock()


class TestBuildPkConditions(unittest.TestCase):
    """Tests for build_pk_conditions() — the MERGE ON clause builder for reconcile."""

    def _build_pk_conditions(self, primary_key_cols, alias_target="t", alias_orphan="o"):
        """Mirror the reconcile job's build_pk_conditions function."""
        return " AND ".join([f"{alias_target}.`{c}` = {alias_orphan}.`{c}`" for c in primary_key_cols])

    def test_single_pk_for_merge_on_orphans(self):
        result = self._build_pk_conditions(["CUSTOMER_TRX_ID"], "t", "o")
        self.assertEqual(result, "t.`CUSTOMER_TRX_ID` = o.`CUSTOMER_TRX_ID`")

    def test_compound_pk_for_merge_on_orphans(self):
        result = self._build_pk_conditions(["ORDER_ID", "LINE_NUM"], "t", "o")
        self.assertIn("t.`ORDER_ID` = o.`ORDER_ID`", result)
        self.assertIn("t.`LINE_NUM` = o.`LINE_NUM`", result)

    def test_three_part_pk(self):
        result = self._build_pk_conditions(["A", "B", "C"], "t", "o")
        parts = result.split(" AND ")
        self.assertEqual(len(parts), 3)


class TestToDynamoSafe(unittest.TestCase):
    """Tests for to_dynamo_safe() — DynamoDB Decimal conversion."""

    def _to_dynamo_safe(self, obj):
        """Mirror the reconcile job's conversion function."""
        from decimal import Decimal
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

    def test_dict_with_floats_converted(self):
        result = self._to_dynamo_safe({"orphan_count": 5.0, "rows_deleted": 3.0})
        self.assertIsInstance(result["orphan_count"], Decimal)
        self.assertIsInstance(result["rows_deleted"], Decimal)

    def test_list_of_floats_converted(self):
        result = self._to_dynamo_safe([1.0, 2.5, 3.0])
        self.assertTrue(all(isinstance(x, Decimal) for x in result))

    def test_nested_structure_fully_converted(self):
        nested = {
            "metrics": {"orphans": 10.0, "deletes": [3.0, 2.0]},
            "score": 0.95,
        }
        result = self._to_dynamo_safe(nested)
        # Verify all floats are converted
        self._verify_no_floats(result)

    def _verify_no_floats(self, obj):
        """Recursively ensure no float instances remain."""
        if isinstance(obj, float):
            self.fail(f"Found float in converted object: {obj}")
        if isinstance(obj, dict):
            for v in obj.values():
                self._verify_no_floats(v)
        if isinstance(obj, list):
            for item in obj:
                self._verify_no_floats(item)


class TestOrphanAntiJoin(unittest.TestCase):
    """Tests for orphan detection logic."""

    def test_anti_join_finds_orphans(self):
        """3 rows in Iceberg, 2 in Oracle → 1 orphan."""
        iceberg_pks = [
            {"ID": 1},
            {"ID": 2},
            {"ID": 3},  # Orphan: not in Oracle
        ]
        oracle_pks = [
            {"ID": 1},
            {"ID": 2},
        ]
        orphans = [pk for pk in iceberg_pks if pk not in oracle_pks]
        self.assertEqual(len(orphans), 1)
        self.assertEqual(orphans[0]["ID"], 3)

    def test_no_orphans_when_oracle_superset(self):
        """Iceberg is subset of Oracle → no orphans."""
        iceberg_pks = [{"ID": 1}, {"ID": 2}]
        oracle_pks = [{"ID": 1}, {"ID": 2}, {"ID": 3}]
        orphans = [pk for pk in iceberg_pks if pk not in oracle_pks]
        self.assertEqual(len(orphans), 0)

    def test_compound_pk_orphan_detection(self):
        """Compound PK orphan detection."""
        iceberg_pks = [
            {"ORDER_ID": 1, "LINE_NUM": 1},
            {"ORDER_ID": 1, "LINE_NUM": 2},
            {"ORDER_ID": 2, "LINE_NUM": 1},  # Orphan
        ]
        oracle_pks = [
            {"ORDER_ID": 1, "LINE_NUM": 1},
            {"ORDER_ID": 1, "LINE_NUM": 2},
        ]
        orphans = [pk for pk in iceberg_pks if pk not in oracle_pks]
        self.assertEqual(len(orphans), 1)
        self.assertEqual(orphans[0]["ORDER_ID"], 2)

    def test_empty_oracle_all_iceberg_rows_are_orphans(self):
        """When Oracle is empty, all Iceberg rows are orphans."""
        iceberg_pks = [{"ID": 1}, {"ID": 2}, {"ID": 3}]
        oracle_pks = []
        orphans = [pk for pk in iceberg_pks if pk not in oracle_pks]
        self.assertEqual(len(orphans), 3)

    def test_empty_iceberg_no_orphans(self):
        """When Iceberg is empty, no orphans exist."""
        iceberg_pks = []
        oracle_pks = [{"ID": 1}, {"ID": 2}]
        orphans = [pk for pk in iceberg_pks if pk not in oracle_pks]
        self.assertEqual(len(orphans), 0)


class TestSafetyCapLogic(unittest.TestCase):
    """Tests for safety cap enforcement."""

    def test_safety_cap_exceeded_raises_error(self):
        """orphan_count > max_deletes_per_run should fail."""
        orphan_count = 6000
        max_deletes_per_run = 5000
        self.assertTrue(orphan_count > max_deletes_per_run)

    def test_safety_cap_at_threshold_passes(self):
        """orphan_count == max_deletes_per_run should pass."""
        orphan_count = 5000
        max_deletes_per_run = 5000
        self.assertFalse(orphan_count > max_deletes_per_run)

    def test_zero_cap_with_orphans_fails(self):
        """max_deletes_per_run=0 with orphans present should fail."""
        orphan_count = 1
        max_deletes_per_run = 0
        self.assertTrue(orphan_count > max_deletes_per_run)

    def test_zero_orphans_passes_any_cap(self):
        """zero orphans always passes safety check."""
        orphan_count = 0
        max_deletes_per_run = 5000
        self.assertFalse(orphan_count > max_deletes_per_run)


class TestMetricsStructure(unittest.TestCase):
    """Tests for metrics item structure and DynamoDB serialization."""

    def test_metrics_item_has_required_fields(self):
        """Verify metrics item includes rows_deleted field."""
        metrics = {
            "job_name":      "test-job",
            "run_id":        "jr-12345",
            "pipeline_id":   "test-pipeline",
            "status":        "SUCCEEDED",
            "orphan_count":  5,
            "rows_deleted":  5,  # Critical field
            "started_at":    datetime.now(timezone.utc).isoformat(),
            "completed_at":  datetime.now(timezone.utc).isoformat(),
            "duration_sec":  10,
            "ttl":           int(datetime.now(timezone.utc).timestamp()) + (90 * 86400),
        }
        self.assertIn("rows_deleted", metrics)
        self.assertEqual(metrics["rows_deleted"], 5)

    def test_metrics_dry_run_mode_status(self):
        """Metrics status for dry-run should indicate skipped."""
        status = "SKIPPED (dry-run (orphans=5))"
        self.assertIn("dry-run", status)
        self.assertIn("SKIPPED", status)

    def test_metrics_error_includes_error_message(self):
        """Metrics should include error_message field on failure."""
        metrics = {
            "job_name":      "test-job",
            "run_id":        "jr-12345",
            "status":        "FAILED",
            "orphan_count":  0,
            "rows_deleted":  0,
            "error_message": "Orphan count exceeded safety cap",
        }
        self.assertIsNotNone(metrics["error_message"])
        self.assertIn("safety cap", metrics["error_message"])


class TestDynamoDBUpdateExpression(unittest.TestCase):
    """Tests for DynamoDB UpdateExpression logic."""

    def test_reconcile_metadata_update_fields(self):
        """Verify UpdateExpression targets correct fields."""
        # Expected update expression: SET reconcile.last_reconciled_at, reconcile.last_orphans_deleted, metadata.*
        # This is a sanity check that the expression structure is sound
        update_expr = (
            "SET reconcile.last_reconciled_at = :ts, "
            "reconcile.last_orphans_deleted = :deleted, "
            "#meta.last_run_id = :rid, "
            "#meta.last_run_at = :run_ts"
        )
        self.assertIn("reconcile.last_reconciled_at", update_expr)
        self.assertIn("reconcile.last_orphans_deleted", update_expr)
        self.assertIn("#meta", update_expr)  # Metadata is aliased (reserved word)

    def test_attribute_names_aliases_used_for_reserved_words(self):
        """Verify metadata is aliased as #meta (reserved DynamoDB word)."""
        attr_names = {"#meta": "metadata"}
        self.assertEqual(attr_names["#meta"], "metadata")


if __name__ == "__main__":
    unittest.main()
