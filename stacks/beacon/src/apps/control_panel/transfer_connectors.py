# transfer_connectors.py — the API's Transfer Family connector lifecycle
# (UIFPE-339). One SFTP connector per SFTP destination, managed HERE because
# connectors are runtime data like schedules and destination secrets: the
# deploy role never touches them, the API role's transfer:* grants are
# tag-fenced (Project tag mandatory at create — see iam.tf's
# TransferConnectorCreate), and delivery only needs the connector id.
#
# The pin-on-first-contact host-key flow survives the move: a connector is
# created WITHOUT trusted host keys (it can test but must not transfer), the
# connection test captures the server's key, and the pin writes it into the
# connector's TrustedHostKeys — from then on the service itself refuses an
# impostor server, which is exactly what the Paramiko adapter enforced.

from __future__ import annotations

import os

PROJECT_TAG = "beacon"

_transfer = None
_initialized = False


def set_transfer_client_for_testing(client) -> None:
    global _transfer, _initialized
    _transfer = client
    _initialized = client is not None


def _client():
    global _transfer, _initialized
    if not _initialized:
        import boto3
        _transfer = boto3.client("transfer")
        _initialized = True
    return _transfer


def access_role_arn() -> str | None:
    """The shared access role connectors assume (transfer.tf). Missing env =
    this deployment has no Transfer Family support — sftp registration is
    refused honestly (UIFPE-438: there is no paramiko fallback anymore)."""
    return os.environ.get("BEACON_TRANSFER_ACCESS_ROLE_ARN") or None


def enabled() -> bool:
    return access_role_arn() is not None


def _url(dest: dict) -> str:
    return f"sftp://{dest['host']}:{int(dest.get('port', 22))}"


def create_connector(dest: dict) -> str:
    """Create the destination's connector; returns the connector id.
    No TrustedHostKeys yet — the connection test pins them (first contact).
    UserSecretId accepts only the secret's full ARN (live finding: the
    friendly name fails CreateConnector's pattern validation)."""
    response = _client().create_connector(
        Url=_url(dest),
        AccessRole=access_role_arn(),
        SftpConfig={"UserSecretId": dest.get("secret_arn") or dest["secret_ref"]},
        Tags=[{"Key": "Project", "Value": PROJECT_TAG}],
    )
    return response["ConnectorId"]


def test_connection(connector_id: str) -> dict:
    """Run the service-side connection test. Returns the same shape the
    Paramiko tester returned so registries.test_destination stays uniform:
    {ok, error?, host_key?, host_key_type?}."""
    response = _client().test_connection(ConnectorId=connector_id)
    ok = str(response.get("Status", "")).upper() == "OK"
    result: dict = {"ok": ok}
    if not ok:
        result["error"] = response.get("StatusMessage") or f"status {response.get('Status')!r}"
    host_key = (response.get("SftpConnectionDetails") or {}).get("HostKey")
    if host_key:
        # Transfer returns "<type> <base64> [comment]" — same wire format the
        # record and TrustedHostKeys use; split the type out for display
        result["host_key"] = host_key
        result["host_key_type"] = host_key.split(" ", 1)[0] if " " in host_key else "ssh-rsa"
    return result


def pin_host_key(connector_id: str, host_key: str) -> None:
    """Write the captured key into the connector's TrustedHostKeys — after
    this, the SERVICE refuses any other server identity on every transfer."""
    connector = _client().describe_connector(ConnectorId=connector_id)["Connector"]
    _client().update_connector(
        ConnectorId=connector_id,
        SftpConfig={"UserSecretId": connector["SftpConfig"]["UserSecretId"],
                    "TrustedHostKeys": [host_key]},
    )


def update_connector_url(connector_id: str, dest: dict, *, clear_host_keys: bool) -> None:
    """Point the connector at a moved server. A host/port change invalidates
    the pinned key (it belongs to the OLD server), so the caller clears it —
    transfers stay blocked until the next test re-pins."""
    connector = _client().describe_connector(ConnectorId=connector_id)["Connector"]
    sftp_config = {"UserSecretId": connector["SftpConfig"]["UserSecretId"]}
    if not clear_host_keys:
        existing = (connector.get("SftpConfig") or {}).get("TrustedHostKeys")
        if existing:
            sftp_config["TrustedHostKeys"] = existing
    _client().update_connector(ConnectorId=connector_id, Url=_url(dest),
                               SftpConfig=sftp_config)


def delete_connector(connector_id: str) -> None:
    """Delete tolerates already-gone (a retry after partial failure)."""
    try:
        _client().delete_connector(ConnectorId=connector_id)
    except Exception as e:  # noqa: BLE001 — matched by name, fakes == boto3
        if type(e).__name__ != "ResourceNotFoundException":
            raise


def egress_ips(connector_id: str) -> list[str]:
    """The service-managed IPs deliveries egress from — what a partner
    allowlists instead of the platform NAT EIPs (UIFPE-339 impact note)."""
    connector = _client().describe_connector(ConnectorId=connector_id)["Connector"]
    return list(connector.get("ServiceManagedEgressIpAddresses") or [])
