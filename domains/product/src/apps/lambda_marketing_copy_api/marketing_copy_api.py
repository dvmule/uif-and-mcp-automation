"""Marketing-copy retrieval API (UIFPE-254) — GET /marketing-copy/{contentId}.

The read half of the content service: one DynamoDB GetItem against the
serving table the sync Lambda maintains, gunzip, JSON envelope. Fronted by
the Mule gateway (UIFPE-255), which owns consumer authentication; this
backend's own defense in depth is a gateway-injected shared header whose
expected value lives in Secrets Manager.

Header-check posture, stated precisely (the adversarial round's finding):
  * secret has NO VALUE (or doesn't exist) -> check DISABLED. That is the
    deliberate pre-gateway dev posture; it arms itself the moment UIFPE-255
    fills the secret, with no code change.
  * secret FETCH FAILS for any other reason (throttle, IAM drift, outage)
    -> the check keeps enforcing the LAST KNOWN value if one was ever
    fetched, and every degradation is logged — a fetch error must never
    silently disarm an armed check.

ETag is the CONTENT HASH the sync stores (md5 of the raw content), never a
timestamp: the platform's silent-maintenance invariant deliberately repairs
values WITHOUT moving `_gold_updated_at`, so a timestamp ETag would let a
revalidating cache 304 its way past a real content change forever. Items
written before the hash existed get it computed on the fly.

Response contract (also in src/api/marketing-copy/openapi.yaml):
  200  envelope + ETag + Cache-Control      304  If-None-Match matched
  401  no IES bearer token from the gateway, or one malformed, unverifiable
       or expired. A CONSUMER never sees this (see below).
  403  shared-header check enforced and failed; or a token that verifies
       whose subject is not on the allow-list
  404  unknown contentId OR content not yet published    405  not GET
  500  JWT_REQUIRED is on but its configuration is incomplete

WHAT AUTHENTICATES WHAT. Two hops, and this handler is the far end of the
second:

    consumer -> gateway    EntraID. The gateway owns it; nothing here sees it.
    gateway  -> function   a Pearson Identity (IES) token the gateway mints
                           for itself, verified here against the issuer's
                           JWKS and pinned to an allow-listed subject.

So the allow-listed subject names the GATEWAY, not an end user. The verifier
and the variable names are the gold retrieval API's, shared rather than
reimplemented (platform/python/jwt_verify.py). Unlike the shared-header check
above it DEFAULTS ON and refuses every request when switched on without being
configured. Both checks exist because this function url is reachable
directly.

Import-time AWS-free; clients injectable for tests.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import time

import jwt_verify

ENV_NAME = os.environ.get("ENV_NAME", "dev")
TABLE_NAME = os.environ.get("TABLE_NAME", f"uif-{ENV_NAME}-marketing-copy-serving")
GATEWAY_SECRET_ID = os.environ.get(
    "GATEWAY_SECRET_ID", f"echo-{ENV_NAME}-uif-marketing-copy-gateway")
GATEWAY_HEADER_NAME = os.environ.get(
    "GATEWAY_HEADER_NAME", "x-uif-gateway-auth").lower()
CACHE_MAX_AGE = int(os.environ.get("CACHE_MAX_AGE", "3600"))

#: The gateway hop's identity check, from JWT_* variables — the SAME
#: implementation and the same variable names the gold retrieval API uses
#: (platform/python/jwt_verify.py). NOT consumer auth: that is EntraID at the
#: gateway, one hop earlier, and never reaches here.
#:
#: DEFAULTS ON: a deployment that forgets the variables refuses rather than
#: serving content openly. Issuer tokens carry no `aud` claim, so the subject
#: allow-list is the whole authorization decision on this hop.
_auth = jwt_verify.Authorizer.from_env("marketing-copy-api")

_SECRET_TTL_SECONDS = 300
_secret_cache: dict = {"value": None, "fetched": 0.0, "state": "never"}


def _expected_header(secrets) -> str | None:
    """The shared-header value; None = check disabled. Disabled is a
    DELIBERATE state (secret empty/absent), never the result of a fetch
    error — an error keeps the last known value enforcing."""
    now = time.time()
    if now - _secret_cache["fetched"] < _SECRET_TTL_SECONDS:
        return _secret_cache["value"]
    try:
        raw = secrets.get_secret_value(SecretId=GATEWAY_SECRET_ID)
        value = (raw.get("SecretString") or "").strip() or None
        if value is None and _secret_cache["state"] != "disabled":
            print("[WARN] gateway shared-header check DISABLED — the secret "
                  f"{GATEWAY_SECRET_ID} has no value (expected until "
                  "UIFPE-255 wires the Mule route)")
        _secret_cache.update(value=value, fetched=now,
                             state="disabled" if value is None else "armed")
        return value
    except Exception as e:
        kind = type(e).__name__
        if "ResourceNotFound" in kind + str(e):
            if _secret_cache["state"] != "disabled":
                print(f"[WARN] gateway shared-header check DISABLED — "
                      f"secret {GATEWAY_SECRET_ID} not found")
            _secret_cache.update(value=None, fetched=now, state="disabled")
            return None
        # transient/permission failure: NEVER silently disarm — keep
        # whatever posture we last knew, and say so every time
        print(f"[ERROR] gateway secret fetch failed ({kind}) — keeping the "
              f"last known posture ({_secret_cache['state']})")
        _secret_cache["fetched"] = now
        return _secret_cache["value"]


def _response(status: int, body: dict | None, headers: dict | None = None) -> dict:
    out = {"statusCode": status,
           "headers": {"content-type": "application/json", **(headers or {})}}
    if body is not None:
        out["body"] = json.dumps(body)
    return out


def _content_id_candidates(path: str) -> list:
    """The segment after /marketing-copy/, tried RAW first and URL-decoded
    second: Function URLs deliver rawPath already percent-decoded while
    other fronts may not, and a contentId is an uncontrolled natural key —
    double-decoding a literal %XX id would 404 a published row."""
    import urllib.parse
    parts = [p for p in path.split("/") if p]
    for i, p in enumerate(parts):
        if p == "marketing-copy" and i + 1 < len(parts):
            raw = parts[i + 1].strip()
            if not raw or len(raw) > 512:
                return []
            decoded = urllib.parse.unquote(raw).strip()
            return [raw] if decoded == raw else [raw, decoded]
    return []


def handler(event: dict, _context=None, ddb=None, secrets=None) -> dict:
    import boto3
    ddb = ddb or boto3.client("dynamodb")
    secrets = secrets or boto3.client("secretsmanager")

    method = (event.get("requestContext", {}).get("http", {}).get("method")
              or event.get("httpMethod") or "GET")
    if method == "OPTIONS":
        return _response(200, {})
    if method != "GET":
        return _response(405, {"error": "GET only"})

    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    expected = _expected_header(secrets)
    if expected is not None and headers.get(GATEWAY_HEADER_NAME) != expected:
        return _response(403, {"error": "missing or invalid gateway header"})

    # Consumer identity, after the cheap network-boundary check above: this one
    # can cost a modular exponentiation and, on a cold start, a JWKS fetch.
    denial = _auth.check(headers)
    if denial is not None:
        return _response(denial.status, {"error": denial.error}, denial.headers)

    candidates = _content_id_candidates(
        event.get("rawPath") or event.get("path") or "")
    if not candidates:
        return _response(404, {"error": "no contentId in path — expected "
                                        "/marketing-copy/{contentId}"})

    item, cid = None, candidates[0]
    for cand in candidates:
        got = ddb.get_item(TableName=TABLE_NAME,
                           Key={"contentId": {"S": cand}}).get("Item")
        if got:
            item, cid = got, cand
            break
    if not item or "contentGz" not in item:
        # unknown id, or metadata-only row: same answer — nothing published
        return _response(404, {"error": f"no published content for "
                                        f"contentId {cid!r}"})

    blob = item["contentGz"]["B"]
    raw = gzip.decompress(blob)
    # the sync writes contentHash; compute it for any item that predates it
    etag_body = (item.get("contentHash", {}).get("S")
                 or hashlib.md5(raw).hexdigest())
    etag = f'"{etag_body}"'
    cache = {"etag": etag, "cache-control": f"max-age={CACHE_MAX_AGE}"}
    if headers.get("if-none-match") == etag:
        return {"statusCode": 304, "headers": cache}

    # The id and the content only: the classifying attributes travel on the
    # product record that links here, so the copy endpoint does not repeat them.
    return _response(200, {
        "contentId": cid,
        "marketingCopyContentText": raw.decode("utf-8"),
    }, headers=cache)
