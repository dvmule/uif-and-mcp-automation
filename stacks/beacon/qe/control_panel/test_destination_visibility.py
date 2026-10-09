# test_destination_visibility.py (UIFPE-546)
# Destinations are private to their owner or public to everyone, chosen at
# creation. The owner is the shell session's verified `sub` (UIFPE-487).
# Covered here: the pure visibility/manageability rules, owner stamping and
# defaults at creation, the owner-only guards on edit/delete/test, the
# legacy-row (no owner, no visibility) behaviour, the auth-off posture that
# keeps every other suite token-free, and the list endpoint filtering with a
# real signed session.

import unittest
from unittest import mock

from fastapi.testclient import TestClient

from control_panel import auth, registries
from control_panel.api import app
from test_auth import ENV, FakeSecrets, make_token, use_secrets

client = TestClient(app)


def _email_body(name, visibility=None):
    body = {"name": name, "channel": "email", "recipients": ["ops@pearson.com"]}
    if visibility is not None:
        body["visibility"] = visibility
    return body


class LocalDestinations(unittest.TestCase):
    """Registries in local mode: no table configured, records in memory."""

    def setUp(self):
        self._saved = list(registries._destinations)
        registries._destinations.clear()

    def tearDown(self):
        registries._destinations.clear()
        registries._destinations.extend(self._saved)


class TestVisibilityRules(LocalDestinations):
    def test_private_row_is_only_visible_to_its_owner(self):
        row = {"owner": "amy", "visibility": "private"}
        self.assertTrue(registries.destination_visible(row, "amy"))
        self.assertFalse(registries.destination_visible(row, "ben"))

    def test_public_and_legacy_rows_are_visible_to_everyone(self):
        self.assertTrue(registries.destination_visible(
            {"owner": "amy", "visibility": "public"}, "ben"))
        # legacy: neither field — reads as public (migration-free rollout)
        self.assertTrue(registries.destination_visible({}, "ben"))

    def test_no_caller_identity_means_no_filtering(self):
        row = {"owner": "amy", "visibility": "private"}
        self.assertTrue(registries.destination_visible(row, None))

    def test_public_is_usable_by_all_but_manageable_by_owner_only(self):
        row = {"owner": "amy", "visibility": "public"}
        self.assertTrue(registries.destination_manageable(row, "amy"))
        self.assertFalse(registries.destination_manageable(row, "ben"))

    def test_legacy_and_auth_off_rows_stay_manageable(self):
        self.assertTrue(registries.destination_manageable({}, "ben"))
        self.assertTrue(registries.destination_manageable(
            {"owner": "amy", "visibility": "private"}, None))


class TestCreation(LocalDestinations):
    def test_owner_is_stamped_and_default_is_private(self):
        record, error = registries.add_destination(_email_body("Amy digest"),
                                                   owner="amy")
        self.assertIsNone(error)
        self.assertEqual(record["owner"], "amy")
        self.assertEqual(record["visibility"], "private")

    def test_author_can_choose_public(self):
        record, error = registries.add_destination(
            _email_body("Team digest", visibility="public"), owner="amy")
        self.assertIsNone(error)
        self.assertEqual(record["visibility"], "public")

    def test_without_identity_no_owner_and_public_default(self):
        record, error = registries.add_destination(_email_body("Local digest"))
        self.assertIsNone(error)
        self.assertNotIn("owner", record)
        self.assertEqual(record["visibility"], "public")

    def test_unknown_visibility_refused(self):
        record, error = registries.add_destination(
            _email_body("Bad", visibility="team"), owner="amy")
        self.assertIsNone(record)
        self.assertIn("visibility", error)


class TestOwnerGuards(LocalDestinations):
    def setUp(self):
        super().setUp()
        self.record, _ = registries.add_destination(
            _email_body("Amy digest"), owner="amy")

    def test_non_owner_cannot_edit(self):
        record, error, status = registries.update_destination(
            self.record["id"], {"recipients": ["ben@pearson.com"]}, username="ben")
        self.assertIsNone(record)
        self.assertEqual(status, 403)
        self.assertIn("owner", error)

    def test_non_owner_cannot_delete_or_test(self):
        _, error, status = registries.delete_destination(self.record["id"],
                                                         username="ben")
        self.assertEqual(status, 403)
        _, error, status = registries.test_destination(self.record["id"],
                                                       username="ben")
        self.assertEqual(status, 403)

    def test_owner_can_flip_visibility(self):
        record, error, status = registries.update_destination(
            self.record["id"], {"visibility": "public"}, username="amy")
        self.assertEqual(status, 200)
        self.assertEqual(record["visibility"], "public")

    def test_unknown_visibility_refused_on_edit(self):
        _, error, status = registries.update_destination(
            self.record["id"], {"visibility": "team"}, username="amy")
        self.assertEqual(status, 422)

    def test_editing_a_legacy_row_stamps_the_editor_as_owner(self):
        legacy, _ = registries.add_destination(_email_body("Old digest"))
        self.assertNotIn("owner", legacy)
        record, _, status = registries.update_destination(
            legacy["id"], {"visibility": "private"}, username="ben")
        self.assertEqual(status, 200)
        self.assertEqual(record["owner"], "ben")

    def test_auth_off_keeps_everything_manageable(self):
        record, error, status = registries.update_destination(
            self.record["id"], {"visibility": "public"}, username=None)
        self.assertEqual(status, 200)


class TestListEndpointFiltering(LocalDestinations):
    """The list endpoint through the real middleware, with a signed session."""

    def setUp(self):
        super().setUp()
        use_secrets(FakeSecrets())
        self.env = mock.patch.dict("os.environ", ENV)
        self.env.start()

    def tearDown(self):
        self.env.stop()
        auth.set_secrets_client_for_testing(None)
        super().tearDown()

    def _get(self, sub):
        return client.get("/destinations",
                          headers={"Authorization": f"Bearer {make_token(sub=sub)}"})

    def test_list_returns_own_plus_public_only(self):
        registries.add_destination(_email_body("Amy private"), owner="amy")
        registries.add_destination(_email_body("Amy public", visibility="public"),
                                   owner="amy")
        registries.add_destination(_email_body("Legacy"))  # no owner: public

        amy = {d["name"] for d in self._get("amy").json()}
        ben = {d["name"] for d in self._get("ben").json()}
        self.assertEqual(amy, {"Amy private", "Amy public", "Legacy"})
        self.assertEqual(ben, {"Amy public", "Legacy"})

    def test_create_stamps_the_session_user(self):
        response = client.post(
            "/destinations", json=_email_body("Mine"),
            headers={"Authorization": f"Bearer {make_token(sub='amy')}"})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["owner"], "amy")
        self.assertEqual(response.json()["visibility"], "private")

    def test_non_owner_edit_is_403_through_the_api(self):
        record, _ = registries.add_destination(_email_body("Amy digest"),
                                               owner="amy")
        response = client.put(
            f"/destinations/{record['id']}", json={"visibility": "public"},
            headers={"Authorization": f"Bearer {make_token(sub='ben')}"})
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
