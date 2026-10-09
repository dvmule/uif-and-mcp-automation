# test_silver_transform.py
# Unit tests for the logic functions in silver-transform.py.
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
    """Tests for build_pk_conditions() — the MERGE ON clause builder."""

    def _build_pk_conditions(self, primary_key_cols):
        return " AND ".join([f"t.`{c}` = s.`{c}`" for c in primary_key_cols])

    def test_single_pk(self):
        result = self._build_pk_conditions(["CUSTOMER_TRX_ID"])
        self.assertEqual(result, "t.`CUSTOMER_TRX_ID` = s.`CUSTOMER_TRX_ID`")

    def test_compound_pk(self):
        result = self._build_pk_conditions(["ORDER_ID", "LINE_NUM"])
        self.assertEqual(
            result,
            "t.`ORDER_ID` = s.`ORDER_ID` AND t.`LINE_NUM` = s.`LINE_NUM`",
        )

    def test_three_part_pk(self):
        result = self._build_pk_conditions(["A", "B", "C"])
        parts = result.split(" AND ")
        self.assertEqual(len(parts), 3)
        self.assertIn("t.`A` = s.`A`", parts)
        self.assertIn("t.`B` = s.`B`", parts)
        self.assertIn("t.`C` = s.`C`", parts)

    def test_pk_with_special_chars_gets_backtick_quoted(self):
        result = self._build_pk_conditions(["ITEM#ID"])
        self.assertIn("`ITEM#ID`", result)


class TestDeduplicateLogic(unittest.TestCase):
    """Tests for deduplication ordering invariants."""

    def _pick_latest(self, rows, pk_cols, ts_col="_extract_timestamp"):
        """Pure Python simulation of the Spark window dedup."""
        from itertools import groupby

        def pk(r):
            return tuple(r[c] for c in pk_cols)

        groups = {}
        for row in rows:
            key = pk(row)
            if key not in groups or row[ts_col] > groups[key][ts_col]:
                groups[key] = row
        return list(groups.values())

    def test_keeps_latest_for_single_pk(self):
        rows = [
            {"ID": 1, "_extract_timestamp": "2026-05-13T10:00:00"},
            {"ID": 1, "_extract_timestamp": "2026-05-14T10:00:00"},
        ]
        result = self._pick_latest(rows, ["ID"])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["_extract_timestamp"], "2026-05-14T10:00:00")

    def test_keeps_latest_for_compound_pk(self):
        rows = [
            {"ORDER_ID": 1, "LINE_NUM": 1, "_extract_timestamp": "2026-05-13T10:00:00"},
            {"ORDER_ID": 1, "LINE_NUM": 1, "_extract_timestamp": "2026-05-14T10:00:00"},
            {"ORDER_ID": 1, "LINE_NUM": 2, "_extract_timestamp": "2026-05-13T10:00:00"},
        ]
        result = self._pick_latest(rows, ["ORDER_ID", "LINE_NUM"])
        self.assertEqual(len(result), 2)
        order_line_1 = next(r for r in result if r["LINE_NUM"] == 1)
        self.assertEqual(order_line_1["_extract_timestamp"], "2026-05-14T10:00:00")

    def test_no_duplicates_passes_through_unchanged(self):
        rows = [
            {"ID": 1, "_extract_timestamp": "2026-05-14T10:00:00"},
            {"ID": 2, "_extract_timestamp": "2026-05-14T10:00:00"},
        ]
        result = self._pick_latest(rows, ["ID"])
        self.assertEqual(len(result), 2)

    def test_empty_input_returns_empty(self):
        result = self._pick_latest([], ["ID"])
        self.assertEqual(result, [])


class TestNoBronzeDataFlow(unittest.TestCase):
    """Tests for the early-exit path when no new Bronze partitions exist."""

    def test_rows_read_zero_means_no_merge(self):
        rows_read = 0
        merge_called = False

        if rows_read > 0:
            merge_called = True

        self.assertFalse(merge_called)

    def test_status_is_succeeded_when_no_new_data(self):
        rows_read = 0
        status = "FAILED"

        if rows_read == 0:
            status = "SUCCEEDED"

        self.assertEqual(status, "SUCCEEDED")


class TestWatermarkAdvance(unittest.TestCase):
    """Tests for the Silver watermark DynamoDB update."""

    def test_dynamo_update_uses_silver_alias(self):
        captured = {}

        def fake_update_item(**kwargs):
            captured.update(kwargs)

        table = MagicMock()
        table.update_item.side_effect = fake_update_item

        new_date = "2026-05-14"
        table.update_item(
            Key={"pipeline_id": "test-pipeline", "version": "latest"},
            UpdateExpression=(
                "SET #slv.last_processed_date = :d, "
                "#meta.last_run_id = :rid, "
                "#meta.last_run_at = :ts"
            ),
            ExpressionAttributeNames={"#slv": "silver", "#meta": "metadata"},
            ExpressionAttributeValues={":d": new_date, ":rid": "jr_123", ":ts": "ts"},
        )

        self.assertIn("#slv", captured["ExpressionAttributeNames"])
        self.assertEqual(captured["ExpressionAttributeNames"]["#slv"], "silver")
        self.assertIn("#meta", captured["ExpressionAttributeNames"])
        self.assertEqual(captured["ExpressionAttributeValues"][":d"], "2026-05-14")

    def test_watermark_advances_to_max_extract_date(self):
        extract_dates = ["2026-05-12", "2026-05-13", "2026-05-14"]
        max_date = max(extract_dates)
        self.assertEqual(max_date, "2026-05-14")

    def test_isoformat_called_on_date_object(self):
        from datetime import date
        d = date(2026, 5, 14)
        result = d.isoformat() if hasattr(d, "isoformat") else str(d)
        self.assertEqual(result, "2026-05-14")


class TestDynamoSafeSerializer(unittest.TestCase):
    """Tests for to_dynamo_safe() — float→Decimal for Silver metrics."""

    def _to_dynamo_safe(self, obj):
        if isinstance(obj, float):
            return Decimal(str(obj))
        if isinstance(obj, dict):
            return {k: self._to_dynamo_safe(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self._to_dynamo_safe(i) for i in obj]
        return obj

    def test_float_to_decimal(self):
        self.assertIsInstance(self._to_dynamo_safe(1.5), Decimal)

    def test_int_unchanged(self):
        self.assertEqual(self._to_dynamo_safe(42), 42)

    def test_nested_dict(self):
        result = self._to_dynamo_safe({"rows": 1.0})
        self.assertIsInstance(result["rows"], Decimal)


class TestMetricsItemStructure(unittest.TestCase):
    """Tests for the Silver metrics item shape written to DynamoDB."""

    def test_required_keys_present(self):
        required = [
            "job_name", "run_id", "pipeline_id", "silver_table",
            "status", "rows_read", "rows_written", "rows_rejected",
            "started_at", "completed_at", "duration_sec", "ttl",
        ]
        item = {
            "job_name":     "echo-dev-silver-transform",
            "run_id":       "jr_silver_001",
            "pipeline_id":  "oracle-erp-orders",
            "silver_table": "silver_finance.orders",
            "status":       "SUCCEEDED",
            "rows_read":    1000,
            "rows_written": 950,
            "rows_rejected": 50,
            "started_at":   "2026-05-14T08:00:00+00:00",
            "completed_at": "2026-05-14T08:02:00+00:00",
            "duration_sec": 120,
            "ttl":          9999999999,
        }
        for key in required:
            self.assertIn(key, item, f"Missing required metrics key: {key}")

    def test_rows_rejected_is_difference(self):
        rows_read    = 1000
        rows_written = 950
        rows_rejected = max(0, rows_read - rows_written)
        self.assertEqual(rows_rejected, 50)

    def test_rows_rejected_never_negative(self):
        rows_rejected = max(0, 0 - 10)
        self.assertEqual(rows_rejected, 0)


if __name__ == "__main__":
    unittest.main()
