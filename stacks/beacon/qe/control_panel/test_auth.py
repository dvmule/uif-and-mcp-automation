# test_auth.py (UIFPE-487)
# The shell-session gate on the Beacon API: token verification (signature,
# expiry, shape), the middleware's open paths, the fail-closed posture when
# the login secret is unreadable, and that auth stays OFF when unconfigured —
# which is what keeps every other suite in this directory token-free.

import base64
import hashlib
import hmac
import json
import time
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from control_panel import auth
from control_panel.api import app

client = TestClient(app)

SECRET_NAME = "uif/echo/dev/ui/login"
TOKEN_SECRET = "dGVzdC1zZWNyZXQ="
ENV = {"BEACON_AUTH_SECRET": SECRET_NAME}


def make_token(token_secret=TOKEN_SECRET, *, sub="karthik", exp=None):
    """Sign a token exactly the way the shell's login does."""
    payload = {"sub": sub, "exp": exp if exp is not None else int(time.time()) + 3600}
    body = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    ).rstrip(b"=").decode()
    sig = base64.urlsafe_b64encode(
        hmac.new(token_secret.encode(), body.encode(), hashlib.sha256).digest()
    ).rstrip(b"=").decode()
    return f"{body}.{sig}"


class FakeSecrets:
    def __init__(self, value=None):
        self.value = value if value is not None else {"users": {"karthik": "x"},
                                                      "token_secret": TOKEN_SECRET}
        self.reads = 0

    def get_secret_value(self, SecretId):
        self.reads += 1
        return {"SecretString": json.dumps(self.value)}


class BrokenSecrets:
    def get_secret_value(self, SecretId):
        raise RuntimeError("secrets manager unreachable")


def use_secrets(fake):
    auth.set_secrets_client_for_testing(fake)
    return fake


class TestVerifyBearer(unittest.TestCase):
    def setUp(self):
        use_secrets(FakeSecrets())
        self.env = mock.patch.dict("os.environ", ENV)
        self.env.start()

    def tearDown(self):
        self.env.stop()
        auth.set_secrets_client_for_testing(None)

    def test_valid_token_returns_the_username(self):
        self.assertEqual(auth.verify_bearer(f"Bearer {make_token()}"), "karthik")

    def test_missing_and_malformed_refused(self):
        for header in (None, "", "Bearer ", "Bearer not-a-token",
                       "Basic abc", f"Bearer {make_token()}extra"):
            with self.assertRaises(auth.AuthError):
                auth.verify_bearer(header)

    def test_wrong_signature_refused(self):
        forged = make_token(token_secret="d3Jvbmc=")
        with self.assertRaises(auth.AuthError):
            auth.verify_bearer(f"Bearer {forged}")

    def test_expired_token_refused(self):
        stale = make_token(exp=int(time.time()) - 1)
        with self.assertRaises(auth.AuthError):
            auth.verify_bearer(f"Bearer {stale}")

    def test_token_secret_is_cached_between_verifications(self):
        fake = use_secrets(FakeSecrets())
        auth.verify_bearer(f"Bearer {make_token()}")
        auth.verify_bearer(f"Bearer {make_token()}")
        self.assertEqual(fake.reads, 1)


class TestMiddleware(unittest.TestCase):
    def setUp(self):
        use_secrets(FakeSecrets())
        self.env = mock.patch.dict("os.environ", ENV)
        self.env.start()

    def tearDown(self):
        self.env.stop()
        auth.set_secrets_client_for_testing(None)

    def test_request_without_token_is_401(self):
        response = client.get("/registries/formats")
        self.assertEqual(response.status_code, 401)
        self.assertIn("sign in", response.json()["detail"])

    def test_request_with_valid_token_passes(self):
        response = client.get("/registries/formats",
                              headers={"Authorization": f"Bearer {make_token()}"})
        self.assertEqual(response.status_code, 200)

    def test_health_stays_open_and_reports_auth(self):
        response = client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["auth_enabled"])

    def test_unreadable_secret_fails_closed(self):
        use_secrets(BrokenSecrets())
        response = client.get("/registries/formats",
                              headers={"Authorization": f"Bearer {make_token()}"})
        self.assertEqual(response.status_code, 503)

    def test_missing_token_secret_key_refused(self):
        use_secrets(FakeSecrets({"users": {"karthik": "x"}}))
        response = client.get("/registries/formats",
                              headers={"Authorization": f"Bearer {make_token()}"})
        self.assertEqual(response.status_code, 401)


class TestAuthDisabled(unittest.TestCase):
    def test_unconfigured_deployment_is_open_and_says_so(self):
        with mock.patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop("BEACON_AUTH_SECRET", None)
            self.assertFalse(auth.enabled())
            response = client.get("/registries/formats")
            self.assertEqual(response.status_code, 200)
            self.assertFalse(client.get("/health").json()["auth_enabled"])


if __name__ == "__main__":
    unittest.main()
