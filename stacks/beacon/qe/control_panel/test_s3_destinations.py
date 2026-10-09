# test_s3_destinations.py (UIFPE-440)
# Self-service external S3 destinations: the partner-policy snippet the panel
# hands the author, and the s3 connection test that runs inside the deliver
# Lambda (under the deliver role — the identity partners grant).

import io
import json
import os
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from control_panel import registries
from control_panel.api import app

client = TestClient(app)

ROLE_ARN = "arn:aws:iam::283556840805:role/uif-beacon-dev-deliver-role"
ENV = {
    "BEACON_DELIVER_FUNCTION": "uif-beacon-dev-deliver",
    "BEACON_DELIVER_ROLE_ARN": ROLE_ARN,
    "BEACON_DATA_BUCKET": "uif-beacon-dev-data",
}


class FakeLambda:
    def __init__(self, payload=None, function_error=None):
        self.payload = payload if payload is not None else {"ok": True}
        self.function_error = function_error
        self.invocations = []

    def invoke(self, FunctionName, Payload):
        self.invocations.append((FunctionName, json.loads(Payload)))
        response = {"Payload": io.BytesIO(json.dumps(self.payload).encode())}
        if self.function_error:
            response["FunctionError"] = self.function_error
        return response


class S3DestinationCase(unittest.TestCase):
    def setUp(self):
        registries._destinations.clear()
        self.lam = FakeLambda()
        # memory mode (no BEACON_TABLE_REGISTRIES); the sentinel ddb/secrets
        # mark the clients initialized so no real boto3 session is created
        registries.set_destination_clients_for_testing(object(), object(), self.lam)
        self.env = mock.patch.dict(os.environ, ENV)
        self.env.start()

    def tearDown(self):
        self.env.stop()
        registries.set_destination_clients_for_testing(None, None)
        registries._destinations.clear()

    def _register(self, prefix="s3://partner-bucket/inbound"):
        response = client.post("/destinations", json={
            "name": "partner-x", "channel": "s3",
            "destination_s3_prefix": prefix})
        self.assertEqual(response.status_code, 201)
        return response.json()


class TestPartnerPolicySnippet(S3DestinationCase):
    def test_external_bucket_gets_the_exact_statement(self):
        record = self._register()
        policy = record["partner_policy"]
        statement = policy["statement"]
        self.assertEqual(statement["Principal"], {"AWS": ROLE_ARN})
        # GetObject = the adapter's copy-verify; DeleteObject = marker cleanup
        self.assertEqual(statement["Action"],
                         ["s3:PutObject", "s3:GetObject", "s3:DeleteObject"])
        self.assertEqual(statement["Resource"], "arn:aws:s3:::partner-bucket/inbound/*")
        self.assertIn("Bucket owner enforced", policy["note"])
        # the listing carries it too, and it is never stored on the record
        rows = client.get("/destinations").json()
        self.assertIn("partner_policy", rows[0])
        self.assertNotIn("partner_policy", registries._destinations[0])

    def test_bucket_root_prefix_covers_the_whole_bucket(self):
        record = self._register("s3://partner-bucket")
        self.assertEqual(record["partner_policy"]["statement"]["Resource"],
                         "arn:aws:s3:::partner-bucket/*")

    def test_our_own_data_bucket_needs_no_partner_grant(self):
        record = self._register("s3://uif-beacon-dev-data/partner-drop/x")
        self.assertNotIn("partner_policy", record)

    def test_no_role_arn_wired_means_no_snippet(self):
        os.environ.pop("BEACON_DELIVER_ROLE_ARN")
        record = self._register()
        self.assertNotIn("partner_policy", record)

    def test_non_s3_channels_have_no_snippet(self):
        self.assertIsNone(registries.partner_policy(
            {"channel": "sftp", "host": "x"}))


class TestS3ConnectionTestRoute(S3DestinationCase):
    def test_passing_test_flips_to_verified(self):
        dest = self._register()
        self.assertEqual(dest["status"], "pending_connection_test")
        response = client.post(f"/destinations/{dest['id']}/test")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True, "status": "verified"})
        # invoked the deliver function with the test contract
        function, payload = self.lam.invocations[0]
        self.assertEqual(function, "uif-beacon-dev-deliver")
        self.assertEqual(payload, {
            "action": "connection_test", "channel": "s3",
            "destination_s3_prefix": "s3://partner-bucket/inbound"})
        # persisted
        rows = client.get("/destinations").json()
        self.assertEqual(rows[0]["status"], "verified")

    def test_failing_test_reports_and_stays_pending(self):
        self.lam.payload = {"ok": False, "error": "could not write to bucket"}
        dest = self._register()
        response = client.post(f"/destinations/{dest['id']}/test")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body["ok"])
        self.assertIn("could not write", body["error"])
        rows = client.get("/destinations").json()
        self.assertEqual(rows[0]["status"], "pending_connection_test")

    def test_lambda_crash_is_a_502_not_a_verdict(self):
        self.lam.payload = {"errorType": "RuntimeError", "errorMessage": "boom"}
        self.lam.function_error = "Unhandled"
        dest = self._register()
        response = client.post(f"/destinations/{dest['id']}/test")
        self.assertEqual(response.status_code, 502)
        self.assertIn("RuntimeError", response.json()["detail"])

    def test_missing_wiring_is_an_honest_503(self):
        os.environ.pop("BEACON_DELIVER_FUNCTION")
        dest = self._register()
        response = client.post(f"/destinations/{dest['id']}/test")
        self.assertEqual(response.status_code, 503)
        self.assertIn("BEACON_DELIVER_FUNCTION", response.json()["detail"])

    def test_email_destination_tests_through_the_deliver_lambda(self):
        # UIFPE-444: the email test sends a real mail via the deliver Lambda
        response = client.post("/destinations", json={
            "name": "mail", "channel": "email",
            "recipients": ["partner@example.com"]})
        self.assertEqual(response.status_code, 201)
        dest = response.json()
        self.assertEqual(dest["status"], "pending_connection_test")
        test = client.post(f"/destinations/{dest['id']}/test")
        self.assertEqual(test.status_code, 200)
        self.assertEqual(test.json(), {"ok": True, "status": "verified"})
        function, payload = self.lam.invocations[-1]
        self.assertEqual(payload, {"action": "connection_test",
                                   "channel": "email",
                                   "recipients": ["partner@example.com"]})
