# test_authorship.py (UIFPE-611)
# The export page's Author used to show whatever the client sent — and the
# wizard sent a hardcoded "karthik" on every create and edit. Now the API
# stamps the verified login (the shell session's `sub`): author = who created
# the export, carried unchanged to later versions; published_by = who
# published each version (the page's "Modified by"). With auth off the body
# value is only a fallback, and nothing is invented without one.

import unittest
from unittest import mock

from control_panel import auth, lifecycle, validation
from lifecycle_fixtures import MAPPING, client
from test_auth import ENV, FakeSecrets, make_token, use_secrets


def as_user(sub):
    return {"Authorization": f"Bearer {make_token(sub=sub)}"}


BODY = {"name": "authorship-export", "domain": "product",
        "mapping_config": MAPPING, "settings": {"formats": ["csv"]},
        "unresolved": []}


class SignedIn(unittest.TestCase):
    def setUp(self):
        lifecycle.reset()
        use_secrets(FakeSecrets())
        self.env = mock.patch.dict("os.environ", ENV)
        self.env.start()

    def tearDown(self):
        self.env.stop()
        auth.set_secrets_client_for_testing(None)

    def publish(self, draft_id, sub):
        response = client.post(f"/drafts/{draft_id}/submit", headers=as_user(sub))
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()


@unittest.skipUnless(validation.ENGINE_AVAILABLE, "engine not importable")
class TestAuthorStamping(SignedIn):
    def test_the_login_is_the_author_whatever_the_body_says(self):
        response = client.post("/drafts", json={**BODY, "author": "karthik"},
                               headers=as_user("amy@pearson.com"))
        self.assertEqual(response.status_code, 201)
        draft = response.json()
        self.assertEqual(draft["author"], "amy@pearson.com")
        self.assertEqual(draft["history"][0]["by"], "amy@pearson.com")

    def test_first_version_records_its_publisher(self):
        draft = client.post("/drafts", json=BODY, headers=as_user("amy")).json()
        v1 = self.publish(draft["id"], "amy")
        self.assertEqual((v1["author"], v1["published_by"]), ("amy", "amy"))

    def test_an_edit_keeps_the_author_and_records_the_editor(self):
        draft = client.post("/drafts", json=BODY, headers=as_user("amy")).json()
        v1 = self.publish(draft["id"], "amy")
        edit = client.post(f"/catalog/{v1['job_id']}/edit",
                           json={"author": "karthik"}, headers=as_user("ben")).json()
        self.assertEqual(edit["author"], "amy")  # the creator, never the body
        self.assertEqual(edit["editor"], "ben")
        self.assertEqual(edit["history"][0]["by"], "ben")
        v2 = self.publish(edit["id"], "ben")
        self.assertEqual(v2["version"], 2)
        self.assertEqual((v2["author"], v2["published_by"]), ("amy", "ben"))
        # and the catalog row the export page reads carries both
        latest = client.get(f"/catalog/{v1['job_id']}/versions",
                            headers=as_user("amy")).json()[-1]
        self.assertEqual((latest["author"], latest["published_by"]), ("amy", "ben"))

    def test_the_publisher_is_whoever_presses_publish(self):
        # someone other than the drafter publishes: the login that submits wins
        draft = client.post("/drafts", json=BODY, headers=as_user("amy")).json()
        v1 = self.publish(draft["id"], "carol")
        self.assertEqual((v1["author"], v1["published_by"]), ("amy", "carol"))


@unittest.skipUnless(validation.ENGINE_AVAILABLE, "engine not importable")
class TestAuthOff(unittest.TestCase):
    """Auth off (local dev, the rest of the suite): no login to read."""

    def setUp(self):
        lifecycle.reset()

    def test_the_body_author_is_the_fallback(self):
        draft = client.post("/drafts", json={**BODY, "author": "dev"}).json()
        self.assertEqual(draft["author"], "dev")
        v1 = client.post(f"/drafts/{draft['id']}/submit").json()
        self.assertEqual(v1["published_by"], "dev")

    def test_without_one_it_says_unknown_rather_than_guessing(self):
        response = client.post("/drafts", json=BODY)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["author"], lifecycle.UNKNOWN_USER)

    def test_a_legacy_version_without_an_author_takes_the_editor(self):
        draft = client.post("/drafts", json={**BODY, "author": "dev"}).json()
        v1 = client.post(f"/drafts/{draft['id']}/submit").json()
        stored = lifecycle.catalog_versions(v1["job_id"])[-1]
        stored.pop("author")
        lifecycle._update_version(stored)
        edit = client.post(f"/catalog/{v1['job_id']}/edit", json={"author": "ed"}).json()
        self.assertEqual(edit["author"], "ed")


if __name__ == "__main__":
    unittest.main()
