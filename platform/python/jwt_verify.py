"""RS* JWT verification and claim checks, standard library only.

Verification needs no crypto library: RSASSA-PKCS1-v1_5 with a public key is
modular exponentiation (`pow`) plus a digest (`hashlib`). Keeping it stdlib
keeps every caller's Lambda zip free of a native wheel and a layer.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.request
from typing import NamedTuple

#: Digest per algorithm. RS* only: an HMAC algorithm here would let a caller
#: sign with the public key as the secret, and `none` would let them sign with
#: nothing.
_DIGEST = {
    "RS256": hashlib.sha256,
    "RS384": hashlib.sha384,
    "RS512": hashlib.sha512,
}

#: The DigestInfo DER prefix the PKCS#1 v1.5 block must carry (RFC 8017 §9.2).
_DER_PREFIX = {
    "RS256": bytes.fromhex("3031300d060960864801650304020105000420"),
    "RS384": bytes.fromhex("3041300d060960864801650304020205000430"),
    "RS512": bytes.fromhex("3051300d060960864801650304020305000440"),
}

#: A token longer than this is refused before any parsing or modexp.
MAX_TOKEN_BYTES = 8192


class TokenInvalid(Exception):
    """The token is absent, malformed, unverifiable or expired -> 401."""


class SubjectNotAllowed(Exception):
    """The token verifies but its subject is not on the allow-list -> 403."""


def _b64u(segment: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))
    except (binascii.Error, ValueError) as exc:
        raise TokenInvalid("malformed base64url") from exc


def _claims(segment: str) -> dict:
    try:
        value = json.loads(_b64u(segment))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise TokenInvalid("malformed JSON") from exc
    if not isinstance(value, dict):
        raise TokenInvalid("segment is not an object")
    return value


def bearer(headers: dict) -> str:
    """The token out of an Authorization header. Raises if there is none."""
    raw = headers.get("authorization") or ""
    scheme, _, token = raw.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise TokenInvalid("no bearer token")
    return token.strip()


def header_kid(token: str) -> str:
    """The `kid` from the token's header, to select a JWKS key.

    Read BEFORE any verification, so it is untrusted input: it only ever
    selects a candidate key, and a wrong one makes the signature fail.
    """
    kid = _claims(token.split(".", 1)[0]).get("kid")
    if not kid or not isinstance(kid, str):
        raise TokenInvalid("no kid in header")
    return kid


def verify_signature(token: str, key: dict, allowed_algs=frozenset(_DIGEST)):
    """Verify `token` against one JWKS key. Returns its payload claims.

    The padded block is REBUILT from the digest and compared whole, rather
    than parsed out of the decrypted block — a parser that skips past the
    padding accepts forged signatures (Bleichenbacher 2006).
    """
    if len(token.encode()) > MAX_TOKEN_BYTES:
        raise TokenInvalid("token too large")
    try:
        header_b64, payload_b64, sig_b64 = token.split(".")
    except ValueError as exc:
        raise TokenInvalid("not three segments") from exc

    header = _claims(header_b64)
    alg = header.get("alg")
    if alg not in allowed_algs or alg not in _DIGEST:
        raise TokenInvalid(f"algorithm {alg!r} not allowed")
    # A key that declares its algorithm pins it, so a caller cannot downgrade
    # the digest by rewriting the header alone.
    if key.get("alg") and key["alg"] != alg:
        raise TokenInvalid("algorithm does not match the key")
    if key.get("kty") != "RSA":
        raise TokenInvalid("key is not RSA")

    n = int.from_bytes(_b64u(key["n"]), "big")
    e = int.from_bytes(_b64u(key["e"]), "big")
    signature = _b64u(sig_b64)
    size = (n.bit_length() + 7) // 8
    if len(signature) != size:
        raise TokenInvalid("signature length does not match the modulus")

    block = pow(int.from_bytes(signature, "big"), e, n).to_bytes(size, "big")
    info = (_DER_PREFIX[alg]
            + _DIGEST[alg](f"{header_b64}.{payload_b64}".encode()).digest())
    if size < len(info) + 11:
        raise TokenInvalid("modulus too small for this algorithm")
    expected = b"\x00\x01" + b"\xff" * (size - 3 - len(info)) + b"\x00" + info
    if not hmac.compare_digest(block, expected):
        raise TokenInvalid("signature does not verify")
    return _claims(payload_b64)


def check_claims(claims: dict, *, issuer: str, allowed_subs,
                 token_type: str | None = None, leeway: int = 60, now=None):
    """Claim checks. `SubjectNotAllowed` is raised LAST, so a 403 only ever
    follows a token that is otherwise entirely valid."""
    now = int(time.time()) if now is None else int(now)

    if claims.get("iss") != issuer:
        raise TokenInvalid("unexpected issuer")
    if token_type and claims.get("type") != token_type:
        raise TokenInvalid("unexpected token type")

    exp = claims.get("exp")
    if not isinstance(exp, (int, float)):
        raise TokenInvalid("no exp claim")
    if now > exp + leeway:
        raise TokenInvalid("token expired")
    for claim in ("nbf", "iat"):
        value = claims.get(claim)
        if isinstance(value, (int, float)) and now + leeway < value:
            raise TokenInvalid(f"token {claim} is in the future")

    subject = claims.get("sub")
    if not subject or subject not in set(allowed_subs):
        raise SubjectNotAllowed("subject not authorized")
    return claims


class JwksCache:
    """A JWKS held in memory across invocations.

    Serves a STALE copy when a refresh fails: a cached key is still a valid
    key, and failing closed on the issuer's availability would make every cold
    start depend on it. An unknown `kid` refreshes at most once per
    `min_refresh_seconds`, so a caller sending random kids cannot turn this
    into traffic against the issuer.
    """

    def __init__(self, url: str, ttl: int = 3600, timeout: int = 5,
                 min_refresh_seconds: int = 60):
        self.url = url
        self.ttl = ttl
        self.timeout = timeout
        self.min_refresh_seconds = min_refresh_seconds
        self._keys: dict[str, dict] = {}
        self._fetched_at = 0.0

    def _fetch(self, now: float) -> bool:
        try:
            request = urllib.request.Request(self.url, headers={
                "accept": "application/json"})
            with urllib.request.urlopen(request, timeout=self.timeout) as body:
                document = json.loads(body.read())
            keys = {k["kid"]: k for k in document.get("keys", []) if k.get("kid")}
        except (urllib.error.URLError, json.JSONDecodeError, OSError,
                KeyError, TypeError, ValueError) as exc:
            self._fetched_at = now  # do not retry on every request
            print(f"[WARN] JWKS refresh failed ({type(exc).__name__}); "
                  f"serving {len(self._keys)} cached key(s)")
            return False
        self._keys, self._fetched_at = keys, now
        return True

    def key(self, kid: str, now=None) -> dict:
        now = time.time() if now is None else now
        if not self._keys or now - self._fetched_at > self.ttl:
            self._fetch(now)
        if kid not in self._keys and now - self._fetched_at > self.min_refresh_seconds:
            self._fetch(now)
        if kid not in self._keys:
            raise TokenInvalid("unknown signing key")
        return self._keys[kid]


class Denial(NamedTuple):
    """Why a request was refused. The caller renders its own response shape,
    so this package stays free of any one stack's conventions."""

    status: int
    error: str
    headers: dict


class Authorizer:
    """Configuration, key cache and the decision, in one place.

    ONE implementation for every API that takes these tokens. Two copies of a
    claim check drift, and the one that drifts is the one nobody is testing.
    """

    def __init__(self, *, required=True, issuer="", jwks_url="",
                 allowed_subs=(), token_type=None, algs=("RS512",),
                 leeway=60, label="API", jwks=None):
        self.required = required
        self.issuer = issuer
        self.jwks_url = jwks_url
        self.allowed_subs = frozenset(allowed_subs)
        self.token_type = token_type or None
        self.algs = frozenset(algs)
        self.leeway = leeway
        self.label = label
        self._jwks = jwks

    @classmethod
    def from_env(cls, label="API", env=None):
        """Built from JWT_* variables. REQUIRED DEFAULTS TO TRUE: a deployment
        that forgets the variable must refuse, not serve data openly."""
        env = os.environ if env is None else env
        def split(name, default=""):
            return [v.strip() for v in env.get(name, default).split(",")
                    if v.strip()]

        return cls(
            required=env.get("JWT_REQUIRED", "true").strip().lower() != "false",
            issuer=env.get("JWT_ISSUER", ""),
            jwks_url=env.get("JWT_JWKS_URL", ""),
            allowed_subs=split("JWT_ALLOWED_SUBS"),
            token_type=env.get("JWT_TOKEN_TYPE", "") or None,
            algs=split("JWT_ALGS", "RS512"),
            label=label)

    @property
    def configured(self) -> bool:
        return bool(self.issuer and self.jwks_url and self.allowed_subs)

    def keys(self) -> JwksCache:
        if self._jwks is None:
            self._jwks = JwksCache(self.jwks_url)
        return self._jwks

    def check(self, headers: dict) -> Denial | None:
        """None when the request may proceed, else why it may not.

        401 and 403 mean different things to a caller: 401 is "your token is
        missing, malformed, unverifiable or expired", 403 is "your token is
        good and this subject is not allowed".
        """
        if not self.required:
            return None
        if not self.configured:
            # Switched on but not switched through. Serving data openly
            # because a variable is unset is the failure this replaces.
            print(f"[ERROR] {self.label} JWT_AUTH_MISCONFIGURED — "
                  "JWT_REQUIRED is on but issuer, JWKS url or subject "
                  "allow-list is empty; refusing every request")
            return Denial(500, "authorization misconfigured", {})
        try:
            token = bearer(headers)
            claims = verify_signature(token, self.keys().key(header_kid(token)),
                                      allowed_algs=self.algs)
            check_claims(claims, issuer=self.issuer,
                         allowed_subs=self.allowed_subs,
                         token_type=self.token_type, leeway=self.leeway)
        except SubjectNotAllowed:
            print(f"[WARN] {self.label} JWT_AUTH_SUBJECT_DENIED")
            return Denial(403, "subject not authorized", {})
        except TokenInvalid as exc:
            # The reason is logged, never returned: an error distinguishing
            # "expired" from "bad signature" tells a caller which half to fix.
            print(f"[WARN] {self.label} JWT_AUTH_TOKEN_REJECTED reason={exc}")
            return Denial(401, "unauthorized", {"www-authenticate": "Bearer"})
        return None
