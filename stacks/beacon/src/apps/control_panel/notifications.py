# notifications.py (UIFPE-444)
# The Pearson Notifications OAuth client credential, managed THROUGH the API —
# the same trust model as destination credentials (D16): values are accepted,
# written to Secrets Manager, and never stored or returned anywhere else.
# This path exists because neither the fde role nor the deploy role can touch
# Secrets Manager (both denied live, 2026-09-16); the API's role carries a
# create/put/read grant fenced to the notifications path (iam.tf,
# NotificationsSecretManage).
#
# TEMPORARY BOOTSTRAP (owner decision 2026-09-16): this module, its two
# endpoints, and the IAM statement are reverted once the credential exists —
# value rotation afterwards rides the console permissions asked of Reyas.

from __future__ import annotations

import json
import os

_secrets = None
_secrets_initialized = False

PLACEHOLDER = "PLACEHOLDER"


def set_secrets_client_for_testing(client) -> None:
    global _secrets, _secrets_initialized
    _secrets = client
    _secrets_initialized = client is not None


def _client():
    global _secrets, _secrets_initialized
    if not _secrets_initialized:
        import boto3
        _secrets = boto3.client("secretsmanager")
        _secrets_initialized = True
    return _secrets


def secret_ref() -> str | None:
    return os.environ.get("BEACON_NOTIFICATIONS_SECRET") or None


def store_credentials(body: dict) -> tuple[dict | None, str | None, int]:
    """Create or rotate the credential secret. Returns (result, error,
    http_status). The values are written to Secrets Manager and NEVER echoed
    back — the response only says where they went."""
    ref = secret_ref()
    if not ref:
        return None, ("this deployment has no notifications wiring "
                      "(BEACON_NOTIFICATIONS_SECRET) — email delivery is not "
                      "configured here"), 503
    for key in ("client_id", "client_secret"):
        value = body.get(key)
        if not isinstance(value, str) or not value.strip():
            return None, f"missing required field: {key}", 422
    value = {
        "grant_type": str(body.get("grant_type") or "client_credentials"),
        "client_id": body["client_id"].strip(),
        "client_secret": body["client_secret"].strip(),
    }
    # optional passthroughs the token endpoint may want
    for key in ("scope", "redirect_uri"):
        if body.get(key):
            value[key] = str(body[key]).strip()

    secrets = _client()
    try:
        try:
            secrets.create_secret(Name=ref, SecretString=json.dumps(value))
            created = True
        except Exception as e:  # noqa: BLE001 — only Exists falls through
            if type(e).__name__ != "ResourceExistsException":
                raise
            secrets.put_secret_value(SecretId=ref,
                                     SecretString=json.dumps(value))
            created = False
    except Exception as e:  # noqa: BLE001 — surfaced, never a bare 500
        return None, (f"could not store the credential "
                      f"({type(e).__name__}: {e})"), 502
    return {"secret_ref": ref, "stored": True, "created": created}, None, 200


def status() -> dict:
    """Whether the credential exists and looks real — WITHOUT returning any
    of it. 'configured' is what the panel/operator needs to know before
    trying an email destination."""
    ref = secret_ref()
    if not ref:
        return {"enabled": False, "configured": False,
                "reason": "BEACON_NOTIFICATIONS_SECRET not set"}
    try:
        raw = _client().get_secret_value(SecretId=ref)
        value = json.loads(raw["SecretString"])
    except Exception as e:  # noqa: BLE001 — absence is a state, not an error
        if type(e).__name__ == "ResourceNotFoundException":
            return {"enabled": True, "configured": False, "secret_ref": ref,
                    "reason": "credentials not stored yet — PUT "
                              "/notifications/credentials"}
        return {"enabled": True, "configured": False, "secret_ref": ref,
                "reason": f"could not read the secret ({type(e).__name__})"}
    real = all(value.get(k) and value.get(k) != PLACEHOLDER
               for k in ("client_id", "client_secret"))
    return {"enabled": True, "configured": real, "secret_ref": ref,
            **({} if real else {"reason": "placeholder values stored"})}
