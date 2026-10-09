# test_ui_login.py
# Unit tests for the interim UI login handler in pipeline_controller.py.
# Runs without AWS credentials — boto3 is stubbed before import.

import base64
import hashlib
import hmac
import json
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Same import dance as test_pipeline_controller.py: use the real boto3 but
# stub client/resource construction while the module is imported.
sys.modules.setdefault("oracledb", MagicMock())

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "lambda"))

import boto3  # noqa: E402

_real_resource, _real_client = boto3.resource, boto3.client
boto3.resource = MagicMock(return_value=MagicMock())
boto3.client = MagicMock(return_value=MagicMock())

import pipeline_controller as pc  # noqa: E402

boto3.resource, boto3.client = _real_resource, _real_client


TOKEN_SECRET = "dGVzdC1zZWNyZXQtdmFsdWUtZm9yLXVuaXQtdGVzdHM="


def _secret(users):
    return {"SecretString": json.dumps({"users": users, "token_secret": TOKEN_SECRET})}


def _body(resp):
    return json.loads(resp["body"])


class TestTokenSigning(unittest.TestCase):
    def test_round_trip(self):
        exp = int(time.time()) + 60
        tok = pc._sign_token({"sub": "alice", "exp": exp}, TOKEN_SECRET)
        payload = pc._verify_token(tok, TOKEN_SECRET)
        self.assertEqual(payload["sub"], "alice")
        self.assertEqual(payload["exp"], exp)

    def test_tampered_payload_rejected(self):
        tok = pc._sign_token({"sub": "alice", "exp": int(time.time()) + 60}, TOKEN_SECRET)
        body, _, sig = tok.partition(".")
        forged = pc._b64u(json.dumps({"sub": "root", "exp": int(time.time()) + 60}).encode())
        self.assertIsNone(pc._verify_token(f"{forged}.{sig}", TOKEN_SECRET))

    def test_wrong_secret_rejected(self):
        tok = pc._sign_token({"sub": "alice", "exp": int(time.time()) + 60}, TOKEN_SECRET)
        self.assertIsNone(pc._verify_token(tok, "some-other-secret"))

    def test_expired_rejected(self):
        tok = pc._sign_token({"sub": "alice", "exp": int(time.time()) - 1}, TOKEN_SECRET)
        self.assertIsNone(pc._verify_token(tok, TOKEN_SECRET))

    def test_malformed_rejected(self):
        for bad in ("", "no-dot", ".", "a.", ".b"):
            with self.subTest(token=bad):
                self.assertIsNone(pc._verify_token(bad, TOKEN_SECRET))

    def test_signature_is_hmac_sha256_over_the_body(self):
        tok = pc._sign_token({"sub": "a", "exp": 1}, TOKEN_SECRET)
        body, _, sig = tok.partition(".")
        want = base64.urlsafe_b64encode(
            hmac.new(TOKEN_SECRET.encode(), body.encode(), hashlib.sha256).digest()
        ).rstrip(b"=").decode()
        self.assertEqual(sig, want)


class TestLogin(unittest.TestCase):
    def test_valid_credentials_return_a_token(self):
        with patch.object(pc.secrets, "get_secret_value",
                          return_value=_secret({"alice": "s3cret"})):
            resp = pc.login({"username": "alice", "password": "s3cret"})
        self.assertEqual(resp["statusCode"], 200)
        out = _body(resp)
        self.assertEqual(out["username"], "alice")
        self.assertEqual(pc._verify_token(out["token"], TOKEN_SECRET)["sub"], "alice")
        self.assertIn("expiresAt", out)

    def test_empty_stored_password_is_not_provisioned(self):
        """The whole point: "" must NEVER authenticate a blank submission."""
        with patch.object(pc.secrets, "get_secret_value",
                          return_value=_secret({"alice": ""})):
            for pw in ("", "anything"):
                with self.subTest(password=pw):
                    resp = pc.login({"username": "alice", "password": pw})
                    self.assertEqual(resp["statusCode"], 401)

    def test_wrong_password_rejected(self):
        with patch.object(pc.secrets, "get_secret_value",
                          return_value=_secret({"alice": "s3cret"})):
            resp = pc.login({"username": "alice", "password": "wrong"})
        self.assertEqual(resp["statusCode"], 401)

    def test_unknown_user_rejected(self):
        with patch.object(pc.secrets, "get_secret_value",
                          return_value=_secret({"alice": "s3cret"})):
            resp = pc.login({"username": "mallory", "password": "s3cret"})
        self.assertEqual(resp["statusCode"], 401)

    def test_all_denials_are_indistinguishable(self):
        """Unknown user, unprovisioned and wrong password must look identical."""
        with patch.object(pc.secrets, "get_secret_value",
                          return_value=_secret({"alice": "s3cret", "bob": ""})):
            seen = {
                json.dumps(_body(pc.login({"username": u, "password": p})), sort_keys=True)
                for u, p in (("mallory", "x"), ("bob", ""), ("alice", "wrong"))
            }
        self.assertEqual(len(seen), 1, f"denial responses differ: {seen}")

    def test_missing_username_is_400_not_401(self):
        resp = pc.login({"password": "s3cret"})
        self.assertEqual(resp["statusCode"], 400)

    def test_username_is_trimmed(self):
        with patch.object(pc.secrets, "get_secret_value",
                          return_value=_secret({"alice": "s3cret"})):
            resp = pc.login({"username": "  alice  ", "password": "s3cret"})
        self.assertEqual(resp["statusCode"], 200)

    def test_non_string_stored_password_rejected(self):
        with patch.object(pc.secrets, "get_secret_value",
                          return_value={"SecretString": json.dumps(
                              {"users": {"alice": None}, "token_secret": TOKEN_SECRET})}):
            resp = pc.login({"username": "alice", "password": ""})
        self.assertEqual(resp["statusCode"], 401)

    def test_missing_token_secret_is_500_not_a_signed_token(self):
        with patch.object(pc.secrets, "get_secret_value",
                          return_value={"SecretString": json.dumps(
                              {"users": {"alice": "s3cret"}})}):
            resp = pc.login({"username": "alice", "password": "s3cret"})
        self.assertEqual(resp["statusCode"], 500)

    def test_unreadable_secret_is_500(self):
        with patch.object(pc.secrets, "get_secret_value",
                          side_effect=Exception("AccessDenied")):
            resp = pc.login({"username": "alice", "password": "s3cret"})
        self.assertEqual(resp["statusCode"], 500)

    def test_password_never_appears_in_logs(self):
        pw = "sup3r-s3cret-passphrase"
        with patch.object(pc.secrets, "get_secret_value",
                          return_value=_secret({"alice": "other"})):
            with self.assertLogs(pc.logger, level="INFO") as logged:
                pc.login({"username": "alice", "password": pw})
        self.assertNotIn(pw, "\n".join(logged.output))


class TestLoginRouting(unittest.TestCase):
    def _event(self, method, path, body=None):
        return {
            "requestContext": {"http": {"method": method}},
            "rawPath": path,
            "body": json.dumps(body) if body is not None else None,
        }

    def test_post_auth_login_reaches_the_handler(self):
        with patch.object(pc, "login", return_value=pc.ok({"token": "t"})) as m:
            pc.handler(self._event("POST", "/auth/login",
                                   {"username": "alice", "password": "s3cret"}), None)
        m.assert_called_once()
        self.assertEqual(m.call_args[0][0]["username"], "alice")

    def test_get_auth_login_does_not_reach_the_handler(self):
        with patch.object(pc, "login") as m:
            pc.handler(self._event("GET", "/auth/login"), None)
        m.assert_not_called()


if __name__ == "__main__":
    unittest.main()
