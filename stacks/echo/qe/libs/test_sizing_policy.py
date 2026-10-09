"""Unit tests for cost-aware initial-load sizing (lambda/sizing_policy.py)."""

import sys
import unittest
from datetime import date
from pathlib import Path

_LAMBDA = Path(__file__).resolve().parent.parent / "lambda"
if str(_LAMBDA) not in sys.path:
    sys.path.insert(0, str(_LAMBDA))

import sizing_policy as sp  # noqa: E402


def _stats(num_rows: int, avg_row_len: int = 500, no_stats: bool = False) -> dict:
    return {"num_rows": num_rows, "avg_row_len": avg_row_len, "no_stats": no_stats}


def _gb_to_rows(gb: float, avg_row_len: int = 500) -> int:
    return int(gb * (1024 ** 3) / avg_row_len)


class TestTargetParallelism(unittest.TestCase):
    def test_sparse_dimension(self):
        preds, workers = sp.target_parallelism(0.5)
        self.assertEqual(preds, 1)
        self.assertEqual(workers, 1)

    def test_medium_fact(self):
        preds, workers = sp.target_parallelism(25)
        self.assertEqual(preds, 4)
        self.assertEqual(workers, 4)

    def test_large_fact_capped(self):
        preds, workers = sp.target_parallelism(120)
        self.assertEqual(preds, 15)
        self.assertEqual(workers, sp.MAX_INITIAL_WORKERS)


class TestWorkerType(unittest.TestCase):
    def test_small_table_g1x(self):
        self.assertEqual(sp.choose_worker_type(5, False), "G.1X")

    def test_medium_g2x(self):
        self.assertEqual(sp.choose_worker_type(25, False), "G.2X")

    def test_no_stats_g2x(self):
        self.assertEqual(sp.choose_worker_type(1, True), "G.2X")

    def test_no_stats_g2x_min_two_workers(self):
        stats = _stats(_gb_to_rows(0.5), no_stats=True)
        bounds = {"min_date": date(2010, 1, 1), "max_date": date(2026, 1, 1)}
        cfg = {"strategy": "date_hash", "date_col": "CREATION_DATE", "hash_col": "ID"}
        out = sp.compute_partition_sizing(stats, cfg, bounds)
        self.assertEqual(out["worker_type"], "G.2X")
        self.assertGreaterEqual(out["num_workers"], 2)

class TestComputePartitionSizing(unittest.TestCase):
    def test_pk_only_30gb(self):
        stats = _stats(_gb_to_rows(30))
        cfg = {"strategy": "numeric_range"}
        out = sp.compute_partition_sizing(stats, cfg, None)
        self.assertEqual(out["worker_type"], "G.2X")
        self.assertLessEqual(out["num_workers"], sp.MAX_INITIAL_WORKERS)
        self.assertLessEqual(out["jdbc_partitions"], sp.MAX_INITIAL_PREDICATES)

    def test_sparse_long_history_uses_few_workers(self):
        stats = _stats(_gb_to_rows(0.5))
        bounds = {"min_date": date(1951, 1, 1), "max_date": date(2026, 1, 1)}
        cfg = sp.choose_partition_strategy(
            None,
            {"pk_cols": []},
            [{"column": "DATE_FROM", "nullable": "N"}],
            [{"column": "ORG_ID", "min": 1, "max": 100, "nulls": 0, "distinct": 10}],
            total_gb=0.5,
        )
        out = sp.compute_partition_sizing(stats, cfg, bounds)
        self.assertLessEqual(out["num_workers"], 2)
        self.assertEqual(out["worker_type"], "G.1X")

    def test_large_fact_workers_capped(self):
        stats = _stats(_gb_to_rows(120))
        bounds = {"min_date": date(2010, 1, 1), "max_date": date(2026, 1, 1)}
        cfg = {"strategy": "date_id_range", "date_col": "D", "id_col": "ID"}
        out = sp.compute_partition_sizing(stats, cfg, bounds)
        self.assertEqual(out["num_workers"], sp.MAX_INITIAL_WORKERS)
        self.assertEqual(out["worker_type"], "G.2X")


class TestGeneratePredicates(unittest.TestCase):
    def test_predicate_count_capped(self):
        bounds = {"min_date": date(2010, 1, 1), "max_date": date(2026, 1, 1)}
        preds = sp.generate_predicates(
            "date_id_range", "PERIOD_ENDING", bounds, total_gb=120,
            id_col="SALES_ID", id_min=1, id_max=100_000_000, id_splits=8,
        )
        self.assertLessEqual(len(preds), sp.MAX_INITIAL_PREDICATES)
        self.assertGreater(len(preds), 0)


class TestStagedBackfill(unittest.TestCase):
    def test_not_recommended_small_table(self):
        bounds = {"min_date": date(2010, 1, 1), "max_date": date(2026, 1, 1)}
        self.assertIsNone(sp.maybe_staged_backfill(25, bounds, "G.2X", 4))

    def test_recommended_large_table(self):
        bounds = {"min_date": date(2010, 1, 1), "max_date": date(2026, 1, 1)}
        staged = sp.maybe_staged_backfill(120, bounds, "G.2X", 8)
        self.assertIsNotNone(staged)
        self.assertTrue(staged["recommended"])
        self.assertGreaterEqual(len(staged["chunks"]), 2)


class TestEnrichRecommendations(unittest.TestCase):
    def test_dpu_capacity(self):
        rec = {"worker_type": "G.2X", "num_workers": 8}
        sizing = {"total_gb": 25}
        sp.enrich_recommendations(rec, sizing, None)
        self.assertEqual(rec["estimated_dpu_capacity"], 16)
        self.assertIn("cost_note", rec)


if __name__ == "__main__":
    unittest.main()
