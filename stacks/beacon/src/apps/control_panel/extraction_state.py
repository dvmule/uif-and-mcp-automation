# extraction_state.py
# Per-export incremental-extraction state (UIFPE-168): the stored high-water
# mark an incremental feed's next window starts from.
#
# Like schedules (UIFPE-206) and transform sizing, this lives OUTSIDE the
# immutable config versions (D22) in its own DynamoDB table — the extraction
# MODE is versioned authoring config; the watermark VALUE is runtime state.
# Ownership split:
#   - the record_run Lambda WRITES it (success branch only, monotonic
#     conditional advance — the commit point that makes failed batches safe);
#   - the API (this module) READS it for run-start and the panel, and
#     RESETS it — delete = the next run re-baselines with a full extract,
#     the operator affordance for a Gold rebuild or a partner resync.
#
# Env: BEACON_TABLE_EXTRACTION_STATE (hash job_id). Unset = feature dark:
# incremental feeds run every batch as a baseline full extract — safe
# (over-delivery, never gaps), and visible in the run rows' extraction block.

from __future__ import annotations

import os

_ddb = None
_ddb_initialized = False


def _client():
    global _ddb, _ddb_initialized
    if not _ddb_initialized:
        import boto3
        _ddb = boto3.client("dynamodb")
        _ddb_initialized = True
    return _ddb


def set_client_for_testing(ddb) -> None:
    global _ddb, _ddb_initialized
    _ddb = ddb
    _ddb_initialized = ddb is not None


def enabled() -> bool:
    return bool(os.environ.get("BEACON_TABLE_EXTRACTION_STATE"))


def get_state(job_id: str) -> dict | None:
    """The stored state record, or None when the export has no watermark yet
    (never ran incrementally, or was reset)."""
    if not enabled():
        return None
    item = _client().get_item(
        TableName=os.environ["BEACON_TABLE_EXTRACTION_STATE"],
        Key={"job_id": {"S": job_id}}).get("Item")
    if not item:
        return None
    return {
        "job_id": item["job_id"]["S"],
        "watermark": item.get("watermark", {}).get("S"),
        "updated_at": item.get("updated_at", {}).get("S"),
        "batch_id": item.get("batch_id", {}).get("S"),
    }


def watermark(job_id: str) -> str:
    """The watermark for a run start, "" when there is none — the state
    machine's input contract requires the key either way. Unreadable state
    degrades to "" (baseline full extract): over-delivery, never a gap."""
    try:
        return (get_state(job_id) or {}).get("watermark") or ""
    except Exception:
        return ""


def reset(job_id: str) -> tuple[bool, str | None]:
    """Drop the stored watermark so the next run re-baselines with a full
    extract. Resetting an export that has no state is not an error — the
    end state is the same (the schedules-delete posture)."""
    if not enabled():
        return False, ("extraction state is not configured on this "
                       "environment (BEACON_TABLE_EXTRACTION_STATE)")
    _client().delete_item(
        TableName=os.environ["BEACON_TABLE_EXTRACTION_STATE"],
        Key={"job_id": {"S": job_id}})
    return True, None
