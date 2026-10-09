# auth.py — verify the RELAY shell's session token (UIFPE-487).
#
# The shell's interim login (stacks/echo control panel) signs a compact token:
# HMAC-SHA256 over a base64url JSON payload {"sub": <username>, "exp": <epoch>},
# keyed by the `token_secret` in the shell's login secret. That design existed
# precisely so other surfaces could verify sessions later — this module is that
# later. Beacon never mints tokens and never reads the `users` map; it only
# needs `token_secret` to check signatures.
#
# Enabled when BEACON_AUTH_SECRET names the login secret (Terraform sets it in
# every deployed env). Unset = auth off — local dev and the test suite — and
# /health reports auth_enabled so a misconfigured deployment is visible on the
# dashboard, the same pattern as every other enabled() flag here.

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

_secrets_client = None
# token_secret cache: re-read at most every 5 minutes, so a rotation in
# Secrets Manager takes effect without a deploy and a warm Lambda does not
# call Secrets Manager on every request.
_cache: dict = {"value": None, "expires_at": 0.0}
_CACHE_TTL_SECONDS = 300

# Paths that stay open: the deploy pipeline and the UI's first paint probe
# /health before any session exists, and it leaks no data.
OPEN_PATHS = {"/health"}


def enabled() -> bool:
    return bool(os.environ.get("BEACON_AUTH_SECRET"))


def set_secrets_client_for_testing(client) -> None:
    global _secrets_client
    _secrets_client = client
    _cache["value"], _cache["expires_at"] = None, 0.0


def _client():
    global _secrets_client
    if _secrets_client is None:
        import boto3
        _secrets_client = boto3.client("secretsmanager")
    return _secrets_client


def _token_secret() -> str:
    now = time.monotonic()
    if _cache["value"] and now < _cache["expires_at"]:
        return _cache["value"]
    raw = _client().get_secret_value(SecretId=os.environ["BEACON_AUTH_SECRET"])
    secret = json.loads(raw["SecretString"]).get("token_secret") or ""
    if not secret:
        raise AuthError("login is not configured (token_secret missing)")
    _cache["value"], _cache["expires_at"] = secret, now + _CACHE_TTL_SECONDS
    return secret


class AuthError(Exception):
    """The request carries no usable session. The message is safe to return."""


def _b64u_decode(txt: str) -> bytes:
    return base64.urlsafe_b64decode(txt + "=" * (-len(txt) % 4))


def verify_bearer(authorization: str | None) -> str:
    """Username when the token's signature and expiry both hold, else
    AuthError. Mirrors the shell's _verify_token exactly — one identical 401
    for missing, malformed, forged and expired, so a probe learns nothing."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise AuthError("sign in to use Beacon (no session token)")
    token = authorization[7:].strip()
    body, _, sig = token.partition(".")
    if not body or not sig:
        raise AuthError("session token is not valid")
    expected = hmac.new(_token_secret().encode(), body.encode(),
                        hashlib.sha256).digest()
    expected_b64 = base64.urlsafe_b64encode(expected).rstrip(b"=").decode()
    if not hmac.compare_digest(sig, expected_b64):
        raise AuthError("session token is not valid")
    try:
        payload = json.loads(_b64u_decode(body))
    except Exception:
        raise AuthError("session token is not valid")
    if int(payload.get("exp", 0)) <= int(time.time()):
        raise AuthError("session expired — sign in again")
    return str(payload.get("sub") or "")


class ShellSessionMiddleware(BaseHTTPMiddleware):
    """401 every request without a valid shell session.

    Added BEFORE CORSMiddleware in api.py so CORS wraps it: preflight OPTIONS
    is answered by CORS without reaching this check, and a 401 still carries
    the CORS headers the browser needs to show the error to the UI."""

    async def dispatch(self, request, call_next):
        if (not enabled() or request.method == "OPTIONS"
                or request.url.path in OPEN_PATHS):
            return await call_next(request)
        try:
            request.state.username = verify_bearer(
                request.headers.get("authorization"))
        except AuthError as e:
            return JSONResponse(status_code=401, content={"detail": str(e)})
        except Exception:  # Secrets Manager unreachable: fail CLOSED, honestly
            return JSONResponse(
                status_code=503,
                content={"detail": "login verification is unavailable — "
                                   "the API could not read the login secret"})
        return await call_next(request)
