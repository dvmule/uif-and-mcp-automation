# test_retire.py (UIFPE-446)
# Retire = soft delete: the export leaves the catalog (and everything that
# reads it — dashboard, drift, pickers) but keeps every published version,
# run row, and delivered file. Reversible; guarded against the two holes
# that would let a hidden export keep acting (an existing schedule, and
# scheduling after retirement).

import os
import unittest
from unittest import mock

from control_panel import lifecycle, validation
from control_panel import api as api_module
from lifecycle_fixtures import client, make_draft
from test_persistence import FakeDdb


def _publish():
    return client.post(f"/drafts/{make_draft()['id']}/submit").json()


@unittest.skipUnless(validation.ENGINE_AVAILABLE, "needs engine")
class TestRetire(unittest.TestCase):
    def setUp(self):
        lifecycle.reset()

    def test_retire_hides_from_catalog_but_keeps_history(self):
        job = _publish()["job_id"]
        response = client.post(f"/catalog/{job}/retire")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["retired"])

        # gone from the default catalog — the dashboard, drift check, and
        # pickers all read this
        self.assertEqual(client.get("/catalog").json(), [])
        # but nothing was destroyed: explicit ask still finds it, flagged
        rows = client.get("/catalog", params={"include_retired": "true"}).json()
        self.assertEqual([(r["job_id"], r["retired"]) for r in rows],
                         [(job, True)])
        # versions survive and carry the flag for the detail page
        versions = client.get(f"/catalog/{job}/versions").json()
        self.assertEqual([v["version"] for v in versions], [1])
        self.assertTrue(all(v["retired"] for v in versions))

    def test_retired_export_cannot_run_edit_or_schedule(self):
        job = _publish()["job_id"]
        client.post(f"/catalog/{job}/retire")

        run = client.post(f"/catalog/{job}/run", json={})
        self.assertEqual(run.status_code, 409)
        self.assertIn("retired", run.json()["detail"])

        edit = client.post(f"/catalog/{job}/edit", json={})
        self.assertEqual(edit.status_code, 409)
        self.assertIn("retired", edit.json()["detail"])

        schedule = client.put(f"/catalog/{job}/schedule",
                              json={"expression": "cron(0 6 * * ? *)"})
        self.assertEqual(schedule.status_code, 409)
        self.assertIn("retired", schedule.json()["detail"])

    def test_retire_refused_while_schedule_exists(self):
        job = _publish()["job_id"]
        with mock.patch.object(api_module.scheduler, "get_schedule",
                               return_value={"job_id": job}):
            response = client.post(f"/catalog/{job}/retire")
        self.assertEqual(response.status_code, 409)
        self.assertIn("schedule", response.json()["detail"])
        # the refusal must not have half-retired it
        self.assertEqual(len(client.get("/catalog").json()), 1)

    def test_unretire_restores(self):
        job = _publish()["job_id"]
        client.post(f"/catalog/{job}/retire")
        response = client.post(f"/catalog/{job}/unretire")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["retired"])

        rows = client.get("/catalog").json()
        self.assertEqual([r["job_id"] for r in rows], [job])
        self.assertFalse(rows[0]["retired"])
        # acting on it works again — an edit draft opens
        self.assertEqual(client.post(f"/catalog/{job}/edit", json={}).status_code, 200)

    def test_retire_unknown_export_404s(self):
        for verb in ("retire", "unretire"):
            response = client.post(f"/catalog/999999/{verb}")
            self.assertEqual(response.status_code, 404)


class FakeDdbWithDelete(FakeDdb):
    def delete_item(self, TableName, Key):
        self.tables[TableName].pop(self._key(Key), None)


@unittest.skipUnless(validation.ENGINE_AVAILABLE, "needs engine")
class TestRetireMarkerItem(unittest.TestCase):
    """DynamoDB mode: the flag is a reserved marker item at (job_id, version 0)
    — real versions start at 1, so the slot is always free, and the item
    carries no `data` attribute so every catalog reader skips it."""

    def setUp(self):
        os.environ["BEACON_TABLE_DRAFTS"] = "drafts-table"
        os.environ["BEACON_TABLE_CATALOG"] = "catalog-table"
        self.ddb = FakeDdbWithDelete()
        lifecycle.set_ddb_for_testing(self.ddb)

    def tearDown(self):
        os.environ.pop("BEACON_TABLE_DRAFTS", None)
        os.environ.pop("BEACON_TABLE_CATALOG", None)
        lifecycle.set_ddb_for_testing(None)
        lifecycle.reset()

    def test_marker_roundtrip(self):
        job = _publish()["job_id"]

        self.assertIsNone(lifecycle.set_retired(job, True))
        marker = self.ddb.tables["catalog-table"][(job, "0")]
        self.assertTrue(marker["retired"]["BOOL"])
        self.assertNotIn("data", marker)          # never mistaken for a version

        self.assertTrue(lifecycle.is_retired(job))
        self.assertEqual(lifecycle.catalog(), [])
        # the marker never pollutes the version list (query path)
        self.assertEqual([v["version"] for v in lifecycle.catalog_versions(job)],
                         [1])

        self.assertIsNone(lifecycle.set_retired(job, False))
        self.assertNotIn((job, "0"), self.ddb.tables["catalog-table"])
        self.assertFalse(lifecycle.is_retired(job))
        self.assertEqual([r["job_id"] for r in lifecycle.catalog()], [job])

    def test_retire_unknown_job_refused(self):
        self.assertIsNotNone(lifecycle.set_retired("424242", True))
