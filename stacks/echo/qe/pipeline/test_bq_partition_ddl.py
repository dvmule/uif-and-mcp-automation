import unittest

from bq_partition_ddl import build_partition_clause, validate_silver_bq_partition


class TestBuildPartitionClause(unittest.TestCase):
    def test_legacy_default_date_day(self):
        clause, err = build_partition_clause({})
        self.assertIsNone(err)
        self.assertEqual(clause, "PARTITION BY DATE(`_silver_updated_at`)")

    def test_legacy_explicit_field(self):
        clause, err = build_partition_clause({"bq_partition_field": "_extract_date"})
        self.assertIsNone(err)
        self.assertEqual(clause, "PARTITION BY DATE(`_extract_date`)")

    def test_legacy_unpartitioned(self):
        clause, err = build_partition_clause({"bq_partition_field": None})
        self.assertIsNone(err)
        self.assertEqual(clause, "")

    def test_bq_partition_null_disables(self):
        clause, err = build_partition_clause({
            "bq_partition_field": "_silver_updated_at",
            "bq_partition": None,
        })
        self.assertIsNone(err)
        self.assertEqual(clause, "")

    def test_date_day_explicit(self):
        clause, err = build_partition_clause({
            "bq_partition": {"type": "date_day", "field": "_extract_timestamp"},
        })
        self.assertIsNone(err)
        self.assertEqual(clause, "PARTITION BY DATE(`_extract_timestamp`)")

    def test_integer_range(self):
        clause, err = build_partition_clause({
            "int64_cols": ["SALES_ID"],
            "bq_partition": {
                "type": "integer_range",
                "field": "SALES_ID",
                "range": {"start": 0, "end": 100, "interval": 10},
            },
        })
        self.assertIsNone(err)
        self.assertIn("RANGE_BUCKET", clause)
        self.assertIn("GENERATE_ARRAY(0, 100, 10)", clause)

    def test_integer_range_requires_int64_cols(self):
        _, err = build_partition_clause({
            "bq_partition": {
                "type": "integer_range",
                "field": "SALES_ID",
                "range": {"start": 0, "end": 100, "interval": 10},
            },
        })
        self.assertIn("int64_cols", err or "")

    def test_conflicting_field_and_legacy(self):
        _, err = build_partition_clause({
            "bq_partition_field": "_extract_date",
            "bq_partition": {"type": "date_day", "field": "_silver_updated_at"},
        })
        self.assertIn("conflicts", err or "")

    def test_too_many_buckets(self):
        _, err = build_partition_clause({
            "int64_cols": ["X"],
            "bq_partition": {
                "type": "integer_range",
                "field": "X",
                "range": {"start": 0, "end": 100_000_000, "interval": 1},
            },
        })
        self.assertIn("buckets", err or "")


class TestValidateSilverBqPartition(unittest.TestCase):
    def test_valid_passes(self):
        self.assertIsNone(validate_silver_bq_partition({
            "bq_partition": {"type": "date_day", "field": "TS"},
        }))
