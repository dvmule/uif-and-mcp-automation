# transfer_family_delivery.py — SFTP delivery through an AWS Transfer Family
# connector (UIFPE-339, retiring the in-Lambda Paramiko path per destination).
#
# The deliver Lambda no longer speaks SFTP: it hands the batch's pickup S3
# paths to StartFileTransfer and polls ListFileTransferResults until every
# file reports COMPLETED. The connector enforces what the Paramiko adapter
# enforced in code — the credential comes from the destination secret, the
# server identity from the connector's TrustedHostKeys (pinned on first
# contact by the connection test; the service refuses transfers before then).
#
# Two accepted differences from the Paramiko path (stated on UIFPE-339):
#   - no tmp-then-rename: files land under their final names, so a partner
#     polling aggressively may see a file mid-write;
#   - egress uses the connector's service-managed IPs, not the platform NAT
#     EIPs — partners allowlist the connector's IPs.

from __future__ import annotations

import time

# StartFileTransfer accepts at most 10 paths per call (service limit).
MAX_PATHS_PER_TRANSFER = 10

# The deliver Lambda runs with a 300 s timeout; leave headroom for the other
# targets of a multi-target batch and for the S3 listing that preceded us.
DEFAULT_POLL_SECONDS = 3
DEFAULT_TIMEOUT_SECONDS = 240


def chunk_paths(paths: list[str], size: int = MAX_PATHS_PER_TRANSFER) -> list[list[str]]:
    return [paths[i:i + size] for i in range(0, len(paths), size)]


def deliver(transfer_client, *, connector_id: str, pickup_bucket: str,
            pickup_key_prefix: str, names: list[str], remote_dir: str,
            poll_seconds: float = DEFAULT_POLL_SECONDS,
            timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
            sleep=time.sleep) -> list[dict]:
    """Send one batch's files through the connector; returns per-file results
    (the same list shape the other adapters return). Chunks preserve the
    caller's ordering, so the manifest-last rule survives: a chunk is only
    started after the previous one completed, and the manifest is in the
    final chunk.

    Raises RuntimeError with per-file detail if anything fails or times out —
    the deliver handler records it per target and fails the batch loudly."""
    results: list[dict] = []
    deadline = time.monotonic() + timeout_seconds
    for chunk in chunk_paths(names):
        send_paths = [f"/{pickup_bucket}/{pickup_key_prefix}{name}" for name in chunk]
        transfer_id = transfer_client.start_file_transfer(
            ConnectorId=connector_id,
            SendFilePaths=send_paths,
            RemoteDirectoryPath=remote_dir,
        )["TransferId"]
        outcomes = _await_transfer(transfer_client, connector_id, transfer_id,
                                   expected=len(chunk), deadline=deadline,
                                   poll_seconds=poll_seconds, sleep=sleep)
        failed = [o for o in outcomes if o.get("StatusCode") != "COMPLETED"]
        if failed:
            detail = "; ".join(
                f"{o.get('FilePath')}: {o.get('StatusCode')}"
                f" ({o.get('FailureCode') or ''} {o.get('FailureMessage') or ''})".strip()
                for o in failed)
            raise RuntimeError(
                f"transfer {transfer_id} via connector {connector_id}: "
                f"{len(failed)} of {len(chunk)} file(s) failed — {detail}")
        for name in chunk:
            results.append({"file": name, "status": "delivered",
                            "via": "transfer_family",
                            "transfer_id": transfer_id})
    return results


def _await_transfer(transfer_client, connector_id: str, transfer_id: str, *,
                    expected: int, deadline: float, poll_seconds: float,
                    sleep) -> list[dict]:
    """Poll until the transfer reports a terminal status for every file."""
    while True:
        outcomes, token = [], None
        while True:
            kwargs = {"ConnectorId": connector_id, "TransferId": transfer_id}
            if token:
                kwargs["NextToken"] = token
            page = transfer_client.list_file_transfer_results(**kwargs)
            outcomes += page.get("FileTransferResults", [])
            token = page.get("NextToken")
            if not token:
                break
        terminal = [o for o in outcomes
                    if o.get("StatusCode") in ("COMPLETED", "FAILED")]
        if len(terminal) >= expected:
            return outcomes
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"transfer {transfer_id} via connector {connector_id} timed "
                f"out: {len(terminal)} of {expected} file(s) reached a "
                "terminal status before the delivery deadline")
        sleep(poll_seconds)
