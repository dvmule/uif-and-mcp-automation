# test_notifications.py (UIFPE-444)
# The Notifications credential endpoints: store-through-the-API (the D16
# trust model — accepted, written to Secrets Manager, never returned) and the
# honest status read. Exists because no human or deploy role can touch
# Secrets Manager; the API's role carries a path-fenced grant.

import json
import os
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from control_panel import notifications
from control_panel.api import app

client = TestClient(app)

REF = "uif/beacon/test/notifications/api"


class FakeSecrets:
    class ResourceExistsException(Exception):
        pass

    class ResourceNotFoundException(Exception):
        pass

    def __init__(self):
        self.values = {}

    def create_secret(self, Name, SecretString):
        if Name in self.values:
            raise self.ResourceExistsException()
        self.values[Name] = SecretString
        return {"ARN": f"arn:{Name}", "Name": Name}

    def put_secret_value(self, SecretId, SecretString):
        assert SecretId in self.values
        self.values[SecretId] = SecretString
        return {"ARN": f"arn:{SecretId}"}

    def get_secret_value(self, SecretId):
        if SecretId not in self.values:
            raise self.ResourceNotFoundException()
        return {"SecretString": self.values[SecretId]}


class NotificationsCase(unittest.TestCase):
    def setUp(self):
        self.secrets = FakeSecrets()
        notifications.set_secrets_client_for_testing(self.secrets)
        self.env = mock.patch.dict(os.environ,
                                   {"BEACON_NOTIFICATIONS_SECRET": REF})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        notifications.set_secrets_client_for_testing(None)


class TestStoreCredentials(NotificationsCase):
    def test_first_put_creates_and_never_echoes_values(self):
        response = client.put("/notifications/credentials", json={
            "client_id": "cid", "client_secret": "cs", "scope": "sc"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(),
                         {"secret_ref": REF, "stored": True, "created": True})
        self.assertNotIn("cs", response.text)  # the secret never comes back
        stored = json.loads(self.secrets.values[REF])
        self.assertEqual(stored, {"grant_type": "client_credentials",
                                  "client_id": "cid", "client_secret": "cs",
                                  "scope": "sc"})

    def test_second_put_rotates_in_place(self):
        client.put("/notifications/credentials",
                   json={"client_id": "old", "client_secret": "old"})
        response = client.put("/notifications/credentials", json={
            "client_id": "new", "client_secret": "new2",
            "grant_type": "password", "redirect_uri": "https://x"})
        self.assertEqual(response.json()["created"], False)
        stored = json.loads(self.secrets.values[REF])
        self.assertEqual(stored["client_id"], "new")
        self.assertEqual(stored["grant_type"], "password")
        self.assertEqual(stored["redirect_uri"], "https://x")

    def test_missing_fields_422(self):
        response = client.put("/notifications/credentials",
                              json={"client_id": "cid"})
        self.assertEqual(response.status_code, 422)
        self.assertIn("client_secret", response.json()["detail"])

    def test_unwired_deployment_503(self):
        os.environ.pop("BEACON_NOTIFICATIONS_SECRET")
        response = client.put("/notifications/credentials", json={
            "client_id": "cid", "client_secret": "cs"})
        self.assertEqual(response.status_code, 503)


class TestStatus(NotificationsCase):
    def test_absent_secret_reads_unconfigured(self):
        body = client.get("/notifications/credentials").json()
        self.assertEqual(body["configured"], False)
        self.assertIn("not stored yet", body["reason"])

    def test_placeholder_reads_unconfigured(self):
        self.secrets.values[REF] = json.dumps(
            {"client_id": "PLACEHOLDER", "client_secret": "PLACEHOLDER"})
        body = client.get("/notifications/credentials").json()
        self.assertEqual(body["configured"], False)
        self.assertIn("placeholder", body["reason"])

    def test_real_values_read_configured_without_leaking(self):
        client.put("/notifications/credentials",
                   json={"client_id": "cid", "client_secret": "cs"})
        response = client.get("/notifications/credentials")
        self.assertEqual(response.json()["configured"], True)
        self.assertNotIn("cid", response.text)
        self.assertNotIn("cs", response.text.replace('"secret_ref"', ""))
