"""API Gateway HTTP API REQUEST authorizer: source-IP allowlist + CI header secret.

Phase 1 auth stop-gap for the Apicurio stack (UIFPE-36, DEPLOY-DEV.md Phase 1).
Nothing else stands in front of the public HTTP API, so this function is the only
thing between the internet and a BFF that can write the governance registry.

Two credential paths, on purpose:

  * **Source IP** — a browser loading the governance SPA authenticates by network
    position, because anything the SPA carries is served to the public and is
    therefore not a secret. The allowlist is connect.pearson.com's *external*
    range, which is what API Gateway sees.
  * **A shared header secret** — CI authenticates by secret, because GitHub-hosted
    runner addresses move and cannot be allowlisted. The value lives in Secrets
    Manager and is set by hand; Terraform creates only the empty container.

What this does NOT do: it authenticates a network location and a shared secret, not
a user. There is no identity, no per-user authorization, and no audit trail of who
did what — every allowed request is anonymous. Delete this function and its Terraform
in one commit when Entra federation lands.

Failure posture is closed. Any shape of event this function does not understand — a
missing `sourceIp`, a malformed address, an unreadable secret — returns a deny rather
than raising, because an unhandled exception becomes a 500 and the failure mode of a
security control must be closed, not confusing.
"""

import hmac
import ipaddress
import os

import boto3  # in the Lambda Python runtime; nothing to package
from botocore.exceptions import ClientError


def _parse_cidrs(raw):
    """Allowlisted networks from the comma-separated env var.

    Terraform already validates every entry with `can(cidrnetmask(c))`, so an
    unparseable entry here means the env var was edited outside Terraform. Skip it
    and say so: skipping narrows the allowlist, which fails closed, whereas raising
    would take the whole control offline and return 500 to everyone.
    """
    nets = []
    for entry in (raw or "").split(","):
        entry = entry.strip()
        if not entry:
            continue
        try:
            nets.append(ipaddress.ip_network(entry, strict=False))
        except ValueError:
            print("authorizer: ignoring unparseable ALLOWED_CIDRS entry %r" % entry)
    return tuple(nets)


ALLOWED_NETS = _parse_cidrs(os.environ.get("ALLOWED_CIDRS"))

# Payload format 2.0 lower-cases every header key before the event reaches us, so
# the configured name is compared lower-case and the incoming keys are normalised
# to match. An empty value disables the header path entirely.
CI_SECRET_HEADER = (os.environ.get("CI_SECRET_HEADER") or "").strip().lower()
CI_SECRET_ARN = (os.environ.get("CI_SECRET_ARN") or "").strip()

# Cold-start cache for the CI secret. Two variables, not one, because "no value set
# yet" and "could not reach Secrets Manager" need different answers:
#   _ci_secret          the value, as bytes, or None
#   _ci_secret_resolved True once the answer is definitive and must not be re-fetched
# A definitive "no version exists" is cached (the operator has not set the value, and
# re-asking on every request would spend a Secrets Manager call per invocation to
# learn the same thing). A transient failure is NOT cached, so the next invocation
# retries instead of the container being poisoned for its whole lifetime.
_ci_secret = None
_ci_secret_resolved = False
_secrets = None


def _client():
    """Secrets Manager client, built once.

    This function runs outside the VPC, so it reaches the public Secrets Manager
    endpoint and does not depend on the interface endpoint the ECS tasks need
    (`var.secretsmanager_endpoint_sg_id`) — one less shared dependency for a
    control that has to work when the rest of the stack does not.
    """
    global _secrets
    if _secrets is None:
        _secrets = boto3.client("secretsmanager")
    return _secrets


def _load_ci_secret():
    """Populate the cold-start cache. Never raises."""
    global _ci_secret, _ci_secret_resolved
    if not CI_SECRET_ARN:
        _ci_secret, _ci_secret_resolved = None, True
        return
    try:
        value = _client().get_secret_value(SecretId=CI_SECRET_ARN).get("SecretString")
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code == "ResourceNotFoundException":
            # The container exists but has no AWSCURRENT version: the operator has
            # not set the value yet. Definitive, and must not deny anyone — the
            # source-IP path is independent of it, so VPN users keep working and
            # only the CI path is unavailable.
            print("authorizer: CI secret has no value set; header path disabled")
            _ci_secret, _ci_secret_resolved = None, True
            return
        # Throttling, KMS, credentials, network: unknown and possibly temporary.
        print("authorizer: CI secret unreadable (%s); will retry" % (code or "unknown"))
        _ci_secret, _ci_secret_resolved = None, False
        return
    except Exception as exc:  # noqa: BLE001 - this function must never raise
        print("authorizer: CI secret lookup failed (%s); will retry" % type(exc).__name__)
        _ci_secret, _ci_secret_resolved = None, False
        return
    _ci_secret = value.encode("utf-8") if isinstance(value, str) and value else None
    _ci_secret_resolved = True


def _ip_allowed(raw):
    """Whether `raw` is a well-formed address inside the allowlist."""
    if not isinstance(raw, str) or not raw.strip():
        return False
    try:
        addr = ipaddress.ip_address(raw.strip())
    except ValueError:
        return False
    # A v6 address against a v4 network is False, not an error (ipaddress compares
    # versions first), so a client arriving over IPv6 is denied rather than crashing.
    return any(addr in net for net in ALLOWED_NETS)


def _header_matches(headers):
    """Whether the request carries the CI secret in the configured header."""
    if not CI_SECRET_HEADER or not isinstance(headers, dict):
        return False
    presented = None
    for key, value in headers.items():
        if isinstance(key, str) and key.strip().lower() == CI_SECRET_HEADER:
            presented = value
            break
    if not isinstance(presented, str) or not presented:
        return False
    if not _ci_secret_resolved:
        _load_ci_secret()
    if not _ci_secret:
        return False
    # compare_digest, not ==. This is a bearer-style secret and the only
    # authentication in front of the registry; `==` short-circuits on the first
    # differing byte, which leaks the shared prefix through response timing and
    # makes the secret recoverable byte by byte.
    return hmac.compare_digest(presented.encode("utf-8"), _ci_secret)


def handler(event, context):  # noqa: ARG001 - context is the Lambda contract
    """Return the payload-format-2.0 simple response: {"isAuthorized": bool}."""
    if not isinstance(event, dict):
        print("authorizer: deny (event is not an object)")
        return {"isAuthorized": False}

    request_ctx = event.get("requestContext")
    http = request_ctx.get("http") if isinstance(request_ctx, dict) else None
    source_ip = http.get("sourceIp") if isinstance(http, dict) else None

    # IP first: it is a stdlib comparison, so the common browser case costs no
    # Secrets Manager call. Order does not change the outcome — either path allows.
    if _ip_allowed(source_ip):
        return {"isAuthorized": True}

    if _header_matches(event.get("headers")):
        print("authorizer: allow via CI header from %r" % (source_ip,))
        return {"isAuthorized": True}

    print("authorizer: deny %r (not allowlisted, no valid CI header)" % (source_ip,))
    return {"isAuthorized": False}


# Warm the secret at cold start so the first CI request does not pay the fetch.
# Called last, after every symbol it needs exists, and it swallows its own errors,
# so a Secrets Manager problem cannot turn into an init failure that 500s the
# source-IP path as well.
_load_ci_secret()
