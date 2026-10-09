# test_runs_summary.py
# UIFPE-610: the Overview's Run outcomes donut and KPIs count EVERY recorded
# run in the chosen window — not the newest 50 across all exports, and not
# only the first 1 MB Scan page. Fake DynamoDB that pages, no AWS.

import os
import unittest
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from control_panel import runs
from control_panel.api import app

client = TestClient(app)


def row(status, at, job="100001", batch=None):
    return {"job_id": {"S": job}, "batch_id": {"S": batch or f"b-{at}-{status}"},
            "status": {"S": status}, "recorded_at": {"S": at}}


class PagingDdb:
    """Scan returns `page_size` items per call with a LastEvaluatedKey, like
    the real service at its 1 MB boundary; query pages the same way."""

    def __init__(self, items, page_size=2):
        self.items, self.page_size = items, page_size
        self.scans = []

    def _page(self, items, kwargs):
        start = int(kwargs.get("ExclusiveStartKey", {}).get("i", 0))
        chunk = items[start:start + self.page_size]
        out = {"Items": chunk}
        if start + self.page_size < len(items):
            out["LastEvaluatedKey"] = {"i": start + self.page_size}
        return out

    def scan(self, **kwargs):
        self.scans.append(kwargs)
        return self._page(self.items, kwargs)

    def query(self, **kwargs):
        job = kwargs["ExpressionAttributeValues"][":j"]["S"]
        return self._page([i for i in self.items if i["job_id"]["S"] == job], kwargs)


class SummaryCase(unittest.TestCase):
    ITEMS = [
        row("succeeded", "2026-09-20T01:00:00Z"),
        row("succeeded", "2026-09-21T01:00:00+00:00"),  # the other ISO shape
        row("failed", "2026-09-22T01:00:00Z"),
        row("stopped", "2026-09-22T02:00:00Z"),
        row("skipped", "2026-09-23T00:00:00Z"),
        row("SUCCEEDED", "2026-09-24T23:59:59Z"),  # case folded
        row("succeeded", "2026-09-25T00:00:00Z"),  # == until: excluded
        row("succeeded", "2026-09-19T23:59:59Z"),  # before since: excluded
        row("failed", "not-a-time"),               # unreadable: excluded
    ]

    def setUp(self):
        os.environ["BEACON_TABLE_RUNS"] = "runs-table"
        self.ddb = PagingDdb(list(self.ITEMS))
        runs.set_client_for_testing(self.ddb)

    def tearDown(self):
        os.environ.pop("BEACON_TABLE_RUNS", None)
        runs.set_client_for_testing(None)

    def get(self, **params):
        return client.get("/runs/summary", params=params)


class TestSummary(SummaryCase):
    def test_counts_every_page_in_the_window_edges_inclusive_exclusive(self):
        body = self.get(since="2026-09-20T00:00:00Z", until="2026-09-25T00:00:00Z").json()
        self.assertTrue(body["enabled"])
        self.assertEqual(body["by_status"],
                         {"succeeded": 3, "failed": 1, "stopped": 1, "skipped": 1})
        self.assertEqual(body["total"], 6)
        # it really paged (9 items, 2 per page) rather than stopping at one
        self.assertEqual(len(self.ddb.scans), 5)
        self.assertEqual(self.ddb.scans[1]["ExclusiveStartKey"], {"i": 2})

    def test_asks_only_for_the_two_fields_it_counts(self):
        self.get(since="2026-09-20T00:00:00Z", until="2026-09-25T00:00:00Z")
        scan = self.ddb.scans[0]
        self.assertEqual(scan["ProjectionExpression"], "#s, recorded_at")
        self.assertEqual(scan["ExpressionAttributeNames"], {"#s": "status"})

    def test_more_than_fifty_runs_are_all_counted(self):
        # the old Overview counted the newest 50 across every export
        many = [row("succeeded", f"2026-09-2{d}T{h:02d}:00:00Z", batch=f"b{d}{h}")
                for d in range(0, 5) for h in range(24)]
        runs.set_client_for_testing(PagingDdb(many, page_size=37))
        body = self.get(since="2026-09-20T00:00:00Z", until="2026-09-25T00:00:00Z").json()
        self.assertEqual(body["total"], 120)

    def test_defaults_to_the_last_seven_days(self):
        start, end, error = runs.parse_window(
            None, None, now=datetime(2026, 9, 25, 12, tzinfo=timezone.utc))
        self.assertIsNone(error)
        self.assertEqual((end - start).days, 7)
        self.assertEqual(end.isoformat(), "2026-09-25T12:00:00+00:00")

    def test_bad_windows_are_refused(self):
        for params, words in (
            ({"since": "yesterday"}, "not an ISO"),
            ({"until": "soon"}, "not an ISO"),
            ({"since": "2026-09-25T00:00:00Z", "until": "2026-09-20T00:00:00Z"}, "before"),
            ({"since": "2099-01-01T00:00:00Z", "until": "2099-01-02T00:00:00Z"}, "future"),
            ({"since": "2024-01-01T00:00:00Z", "until": "2026-01-01T00:00:00Z"}, "366"),
        ):
            response = self.get(**params)
            self.assertEqual(response.status_code, 422, params)
            self.assertIn(words, response.json()["detail"], params)

    def test_a_naive_time_is_read_as_utc(self):
        body = self.get(since="2026-09-20T00:00:00", until="2026-09-21T00:00:00").json()
        self.assertEqual(body["by_status"], {"succeeded": 1})

    def test_summary_is_not_mistaken_for_a_run_id(self):
        # /runs/summary is declared before /runs/{run_id}
        self.assertIn("by_status", self.get().json())

    def test_disabled_mode_is_honest(self):
        os.environ.pop("BEACON_TABLE_RUNS", None)
        body = self.get().json()
        self.assertFalse(body["enabled"])
        self.assertIn("not configured", body["reason"])


class TestListRunsPaging(SummaryCase):
    def test_unscoped_history_follows_every_page(self):
        # UIFPE-610: list_runs read one Scan page only
        result = runs.list_runs(None, limit=200)
        self.assertEqual(len(result["runs"]), len(self.ITEMS))

    def test_scoped_history_follows_every_page(self):
        result = runs.list_runs("100001", limit=200)
        self.assertEqual(len(result["runs"]), len(self.ITEMS))
