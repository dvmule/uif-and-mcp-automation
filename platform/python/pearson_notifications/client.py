# client.py
# The Pearson Notifications API, as one small transport shared by every UIF
# stack (UIFPE-543; lifted from beacon's email_delivery, UIFPE-444). Sends a
# FileShare.StandardEmail event: subject + HTML body to a list of recipients.
#
# Auth: the PI API v1 tokens endpoint (JSON userName/password; the token rides
# the response's "data" field). The credential lives in Secrets Manager - never
# in code or config; its client_id/client_secret keys carry the PI userName /
# password. The token is cached in module state for a warm Lambda, and a 401
# from the events API re-mints once and retries, so token expiry never fails a
# send.
#
# stdlib HTTP on purpose: every consumer is a pure-code Lambda zip with nothing
# vendored, and this module is copied into those zips as-is.

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

# token cache, keyed by secret ref - survives across invocations of a warm
# Lambda, dies with the sandbox (never persisted anywhere)
_token_cache: dict[str, dict] = {}
_EXPIRY_SLACK_SECONDS = 60
_DEFAULT_TOKEN_TTL_SECONDS = 890  # the v1 response carries no expiry; mirrors the SFS reference cache
_HTTP_TIMEOUT_SECONDS = 10


def post_json(url: str, *, data: bytes, headers: dict) -> tuple[int, dict]:
    """POST returning (status, parsed json). 4xx/5xx come back as the status
    with whatever body the service sent - the caller decides what is fatal.
    10 s, not 30: an API Lambda may wait on this synchronously under API
    Gateway's 29 s cap, so a hung endpoint must come back as a readable error."""
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=_HTTP_TIMEOUT_SECONDS) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read() or b"{}")
        except json.JSONDecodeError:
            body = {}
        return e.code, body


class NotificationsClient:
    """Mint a token, send an email event, re-mint once on 401."""

    def __init__(self, secrets_client, *, secret_ref: str, api_url: str,
                 token_url: str, post=None, now=time.monotonic):
        self._secrets = secrets_client
        self._secret_ref = secret_ref
        self._api_url = api_url.rstrip("/")
        self._token_url = token_url
        self._post = post or post_json
        self._now = now

    # -- auth -----------------------------------------------------------------
    def _credentials(self) -> dict:
        raw = self._secrets.get_secret_value(SecretId=self._secret_ref)
        return json.loads(raw["SecretString"])

    def _mint_token(self) -> str:
        # PI API v1: a JSON userName/password POST, the token arriving in the
        # response's "data". The secret's client_id / client_secret keys CARRY
        # the PI userName / password - the storage shape predates that finding
        # and re-keying it buys nothing.
        creds = self._credentials()
        payload = {"userName": creds.get("username") or creds["client_id"],
                   "password": creds.get("password") or creds["client_secret"]}
        status, body = self._post(
            self._token_url,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"})
        data = body.get("data")
        if isinstance(data, dict):  # tolerate a wrapped shape
            token = data.get("token") or data.get("access_token")
        else:
            token = data
        if status not in (200, 201) or not token or not isinstance(token, str):
            raise RuntimeError(
                f"the PI tokens endpoint refused the request (HTTP {status}) "
                "- check the notifications credentials secret (client_id = "
                "PI userName, client_secret = PI password)")
        _token_cache[self._secret_ref] = {
            "token": token,
            "expires_at": self._now() + int(body.get("expires_in", _DEFAULT_TOKEN_TTL_SECONDS)),
        }
        return token

    def _token(self) -> str:
        cached = _token_cache.get(self._secret_ref)
        if cached and cached["expires_at"] - _EXPIRY_SLACK_SECONDS > self._now():
            return cached["token"]
        return self._mint_token()

    # -- send -----------------------------------------------------------------
    def send_email(self, *, recipients: list[str], subject: str,
                   body_html: str) -> dict:
        """One FileShare.StandardEmail event to every recipient. Returns the
        API's response body; raises RuntimeError when the API refuses."""
        event = {
            "productType": "ESB",
            "appType": "GIP",
            "eventType": "FileShare.StandardEmail",
            "recipientIds": list(recipients),
            "eventModel": {"FileShare": {"BodyOfEmail": body_html,
                                         "SubjectOfEmail": subject}},
            "templateLocale": "EN-US",
        }
        payload = json.dumps(event).encode()

        def attempt(token: str) -> tuple[int, dict]:
            return self._post(f"{self._api_url}/events", data=payload,
                              headers={"Content-Type": "application/json",
                                       "X-Authorization": token})

        status, body = attempt(self._token())
        if status == 401:  # expired/revoked mid-flight: re-mint ONCE, retry
            _token_cache.pop(self._secret_ref, None)
            status, body = attempt(self._token())
        if status not in (200, 201, 202):
            raise RuntimeError(
                f"Notifications API refused the email event (HTTP {status}): "
                f"{json.dumps(body)[:300]}")
        return body


def event_id(response: dict) -> str | None:
    """The API's id for an accepted event, whichever key it used."""
    return response.get("eventId") or response.get("id")
