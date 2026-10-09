"""UIFPE-522 / ADR-0020 step 1 — the transform's bookmark behaviour, pinned on
the Glue script's source (it runs only under Spark; see test_gold_announce for
the same technique). Each test names the regression it stops."""
import re
import unittest
from pathlib import Path

SRC = (Path(__file__).resolve().parents[2]
       / "src/apps/pipeline/generic_gold_transform.py").read_text(encoding="utf-8")


def _calls(pattern: str) -> list:
    return re.findall(pattern, SRC)


class TestBookmarkIsTheRunStart(unittest.TestCase):

    def test_processing_runs_store_their_own_start(self):
        # normal delta path AND the full load / rebuild (decision 3) both pass
        # started_at - never last_run_at, never a source-derived max
        self.assertEqual(len(_calls(r"_advance_bookmark\(\s*started_at\.isoformat\(\)")), 2)

    def test_run_end_is_never_the_bookmark(self):
        self.assertFalse(_calls(r"_advance_bookmark\(\s*datetime\.now"))
        # the END is what the state writer stamps as last_run_at, inside the writer
        self.assertIn("bookmark, RUN_ID, datetime.now(timezone.utc).isoformat()", SRC)

    def test_idle_run_advances_nothing_unless_the_row_has_no_bookmark(self):
        # ADR-0020: "a run that processes nothing advances nothing" - but a row
        # with no bookmark is a FIRST run, and a first run stores its own start
        # even when idle (UIFPE-522 follow-up: product 2026-09-23 06:30Z was
        # idle on its first run and left the row with no bookmark at all)
        self.assertIn('stored_bookmark = gold_cdc.resolve_bookmark(cfg.get("watermark"))', SRC)
        self.assertIn("_advance_bookmark(None if stored_bookmark else started_at.isoformat(),\n"
                      "                              silver_snapshots)", SRC)
        # the unconditional idle form must be gone
        self.assertNotIn("_advance_bookmark(None, silver_snapshots)", SRC)

    def test_rebuild_does_not_skip_the_bookmark(self):
        # decision 3: the full load's bookmark is its own start, and it resets
        # the recompute clock in the same write
        self.assertIn("_advance_bookmark(started_at.isoformat(), silver_snapshots,\n"
                      "                              recompute_at=started_at.isoformat())", SRC)


class TestDetectionScansFromTheBookmark(unittest.TestCase):

    def test_every_source_scans_from_one_lagged_bookmark(self):
        self.assertIn("gold_cdc.lagged_watermark(bookmark, WATERMARK_LAG_MINUTES)", SRC)
        self.assertEqual(SRC.count("gold_cdc.lagged_watermark("), 1)

    def test_no_per_source_watermark_survives(self):
        for gone in ("resolve_source_watermarks", "merge_watermarks",
                     "max_watermarks_from_views", "last_processed_silver_updated_at",
                     "new_watermarks", "_advance_watermarks"):
            with self.subTest(gone=gone):
                self.assertNotIn(gone, SRC)

    def test_row_bookmark_is_read_before_seeding(self):
        self.assertIn("gold_cdc.resolve_bookmark(cfg.get(\"watermark\"))", SRC)
        self.assertIn("bookmark = stored_bookmark or _seed_bookmark()", SRC)


class TestSeedFallsBackSafely(unittest.TestCase):

    def test_seed_reads_this_jobs_successful_runs_for_this_entity(self):
        self.assertIn('Key("job_name").eq(JOB_NAME)', SRC)
        self.assertIn('Attr("gold_id").eq(GOLD_ID)', SRC)
        self.assertIn("gold_cdc.latest_successful_start(items, GOLD_ID)", SRC)

    def test_seed_failure_and_no_history_fall_back_to_epoch(self):
        seed_fn = SRC[SRC.index("def _seed_bookmark"):SRC.index("def _advance_bookmark")]
        self.assertEqual(seed_fn.count("return gold_cdc.EPOCH_WATERMARK"), 2)
        self.assertIn("except Exception", seed_fn)


if __name__ == "__main__":
    unittest.main()
