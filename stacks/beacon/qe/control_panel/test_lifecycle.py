# test_lifecycle.py
# Drafts -> submit (= validate + publish) -> catalog. The approval queue was
# removed by platform-owner decision (UIFPE-195): a draft that passes engine
# validation publishes directly. Uses a mapping config the engine accepts
# when available (otherwise the submit gate reports engine_available=false
# and fails closed — also asserted).

import unittest

from control_panel import lifecycle, validation
from lifecycle_fixtures import MAPPING, client, make_draft  # shared, non-test module


class TestLifecycle(unittest.TestCase):
    def setUp(self):
        lifecycle.reset()

    def test_create_requires_fields(self):
        response = client.post("/drafts", json={"job_id": "x"})
        self.assertEqual(response.status_code, 422)

    def test_submit_blocked_by_unresolved_items(self):
        draft = make_draft(unresolved=[{"target": "rn", "reason": "window"}])
        response = client.post(f"/drafts/{draft['id']}/submit")
        self.assertEqual(response.status_code, 422)
        self.assertIn("unresolved", response.json()["detail"])

    def test_submit_validates_with_engine(self):
        draft = make_draft()
        # UIFPE-196: the draft carries its minted identity from creation
        self.assertEqual(draft["export_id"], 100000)
        self.assertEqual(draft["name"], "product-export")
        self.assertEqual(draft["job_id"], "100000")
        response = client.post(f"/drafts/{draft['id']}/submit")
        if validation.ENGINE_AVAILABLE:
            # validated => published: submit returns the VERSION directly
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["version"], 1)
            self.assertEqual(response.json()["export_id"], 100000)
            self.assertEqual(response.json()["approved_by"], "engine-validation")
        else:
            # fail closed: no engine, no publish
            self.assertEqual(response.status_code, 422)

    @unittest.skipUnless(validation.ENGINE_AVAILABLE, "needs engine")
    def test_submit_rejects_invalid_mapping(self):
        bad = dict(MAPPING, mappings=[{"type": "derived", "target": "x",
                                       "expression": "reflect('java.lang.Runtime')"}])
        draft = make_draft(mapping_config=bad)
        response = client.post(f"/drafts/{draft['id']}/submit")
        self.assertEqual(response.status_code, 422)
        # the failure must not publish anything
        self.assertEqual(client.get("/catalog").json(), [])

    @unittest.skipUnless(validation.ENGINE_AVAILABLE, "needs engine")
    def test_same_name_publishes_as_distinct_exports(self):
        # UIFPE-196 acceptance 1: two authors, same NAME -> two exports with
        # distinct ids, each at v1 — never v2 of someone else's export
        first = client.post(f"/drafts/{make_draft()['id']}/submit").json()
        second = client.post(f"/drafts/{make_draft()['id']}/submit").json()

        self.assertEqual(first["name"], second["name"])
        self.assertEqual((first["export_id"], first["version"]), (100000, 1))
        self.assertEqual((second["export_id"], second["version"]), (100001, 1))

        catalog = client.get("/catalog").json()
        self.assertEqual({c["job_id"] for c in catalog}, {"100000", "100001"})
        self.assertTrue(all(c["approved_by"] == "engine-validation" for c in catalog))
        self.assertTrue(all(c["name"] == "product-export" for c in catalog))

        versions = client.get("/catalog/100000/versions").json()
        self.assertEqual([v["version"] for v in versions], [1])

    @unittest.skipUnless(validation.ENGINE_AVAILABLE, "needs engine")
    def test_versioning_machinery_intact_for_future_edit_flow(self):
        # the Edit flow (deferred) will re-submit a draft carrying an EXISTING
        # export_id; the machinery must append v2, not clobber v1 (D22)
        v1 = client.post(f"/drafts/{make_draft()['id']}/submit").json()
        edit = make_draft()
        stored = lifecycle.get_draft(edit["id"])
        stored["export_id"], stored["job_id"] = v1["export_id"], v1["job_id"]
        lifecycle._save_draft(stored)
        v2, error = lifecycle.submit(edit["id"])
        self.assertIsNone(error)
        self.assertEqual((v2["export_id"], v2["version"]), (v1["export_id"], 2))
        versions = client.get(f"/catalog/{v1['job_id']}/versions").json()
        self.assertEqual([v["version"] for v in versions], [1, 2])

    @unittest.skipUnless(validation.ENGINE_AVAILABLE, "needs engine")
    def test_edit_endpoint_publishes_next_version(self):
        # UIFPE-199: edit carries the EXISTING id — submit appends v2
        v1 = client.post(f"/drafts/{make_draft()['id']}/submit").json()
        edit = client.post(f"/catalog/{v1['job_id']}/edit",
                           json={"author": "abdul"}).json()
        self.assertEqual(edit["export_id"], v1["export_id"])
        self.assertEqual(edit["status"], "draft")
        v2 = client.post(f"/drafts/{edit['id']}/submit").json()
        self.assertEqual((v2["export_id"], v2["version"]), (v1["export_id"], 2))
        versions = client.get(f"/catalog/{v1['job_id']}/versions").json()
        self.assertEqual([v["version"] for v in versions], [1, 2])

    def test_edit_unknown_export_is_404(self):
        response = client.post("/catalog/999999/edit", json={})
        self.assertEqual(response.status_code, 404)

    def test_approval_endpoints_are_gone(self):
        # the queue and its verbs were removed with the approval step —
        # the unauthenticated API must not keep dead governance routes
        self.assertEqual(client.get("/approvals").status_code, 404)
        self.assertEqual(
            client.post("/approvals/x/approve", json={"approver": "s"}).status_code, 404)
        self.assertEqual(
            client.post("/approvals/x/reject", json={"approver": "s"}).status_code, 404)

    @unittest.skipUnless(validation.ENGINE_AVAILABLE, "needs engine")
    def test_rejected_legacy_draft_still_editable(self):
        # pre-UIFPE-195 records can carry status "rejected"; editing one
        # must still reopen it as a draft
        draft = make_draft()
        stored = lifecycle.get_draft(draft["id"])
        stored["status"] = "rejected"
        lifecycle._save_draft(stored)
        response = client.put(f"/drafts/{draft['id']}",
                              json={"settings": {"formats": ["csv", "json"]}})
        self.assertEqual(response.json()["status"], "draft")

    def test_published_draft_not_editable(self):
        if not validation.ENGINE_AVAILABLE:
            self.skipTest("needs engine")
        draft = make_draft()
        client.post(f"/drafts/{draft['id']}/submit")
        response = client.put(f"/drafts/{draft['id']}", json={"settings": {}})
        self.assertEqual(response.status_code, 422)
        self.assertIn("published", response.json()["detail"])
