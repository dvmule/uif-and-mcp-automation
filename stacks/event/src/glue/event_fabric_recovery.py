"""Event Fabric Recovery - Glue Python shell job.

The publish-side half of the recovery plane (the redrive Lambda is the
delivery-side half): re-publishes Table 3 rows that NEVER reached the bus
(status PUBLISH_FAILED), per the contract in docs/publish-metrics.md -
write rules 2-3 and the recovery flow, implemented exactly:

  * steward-triggered per Gold run: --domain --gold_run_id --started_by
    (attribution is mandatory; a recovery is a deliberate act);
  * Query gsi_recovery with recovery_scope = {domain}#{gold_run_id} -
    the sparse index holds exactly the outstanding failures, all versions;
  * per row: GetItem the base row (the gzip payload is excluded from every
    GSI projection), decompress the stored envelope and PutEvents it
    BYTE-IDENTICAL (batched, max 10) - never rebuilt from gold, so a
    replayed event matches the original regardless of later gold changes,
    and the envelope id (Table 3's event_id) is unchanged. DetailType is
    recomposed from the row ({domain}.{operation}.{version}); Source and
    the bus come from the envelope and the naming convention, so the job
    is domain-agnostic like the rest of the stack;
  * EventId returned -> rule 2 on the SAME row (PUBLISHED, REMOVE
    recovery_scope so it leaves gsi_recovery, ADD attempt_count);
    ErrorCode -> rule 3 (reason/error_msg updated, stays queued). Both
    condition on status = PUBLISH_FAILED, so racing replays cannot both
    apply - a lost race counts skipped, never double-published bookkeeping;
  * run accounting: the failed run's own INCREMENTAL pipeline-metrics row
    gets replay_count + 1 (best-effort), and this run writes its own row -
    mode REPLAY, same gold_run_id (it SHOULD appear in gsi_gold_run: "what
    has run for this Gold run" spans processor and recovery), worker_type
    "PYTHON_SHELL" as the engine indicator, started_by = the steward.

Plain boto3 + stdlib only, mirroring the processor, so the same file runs
as a Glue Python shell job and imports cleanly in unit tests.
"""

from __future__ import annotations

import gzip
import json
import logging
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("event-fabric-recovery")

_RETRY_CONFIG = Config(retries={"mode": "standard", "max_attempts": 5})

events = boto3.client("events", config=_RETRY_CONFIG)
dynamodb = boto3.client("dynamodb")

REQUIRED_ARGS = ["domain", "gold_run_id", "started_by",
                 "publish_metrics_table", "event_stack_prefix"]
OPTIONAL_ARGS = ["pipeline_metrics_table", "event_run_id", "metrics_ttl_days"]

PUT_EVENTS_BATCH_SIZE = 10


def parse_args(argv: list[str]) -> dict[str, str]:
    """Glue passes job arguments as --key value pairs (same hand parser as
    the processor - no awsglue import, identical in tests and runtime)."""
    raw: dict[str, str] = {}
    i = 0
    while i < len(argv):
        token = argv[i]
        if token.startswith("--"):
            key = token[2:]
            value = argv[i + 1] if i + 1 < len(argv) and not argv[i + 1].startswith("--") else ""
            raw[key] = value
            i += 2 if value != "" or (i + 1 < len(argv) and not argv[i + 1].startswith("--")) else 1
        else:
            i += 1
    missing = [a for a in REQUIRED_ARGS if not raw.get(a)]
    if missing:
        raise ValueError(f"Missing required job arguments: {missing}")
    return raw


def main(argv: list[str]) -> dict[str, Any]:
    args = parse_args(argv)
    domain = args["domain"]
    gold_run_id = args["gold_run_id"]
    table = args["publish_metrics_table"]
    prefix = args["event_stack_prefix"]
    bus = f"{prefix}-{domain}"
    # Glue passes no run id to python-shell scripts; the trigger may pass
    # the StartJobRun response id through, else a recovery id is minted.
    run_id = args.get("event_run_id") or f"rp_{uuid.uuid4().hex}"
    started_at = _utcnow()
    t0 = time.time()
    scope = f"{domain}#{gold_run_id}"

    rows = _outstanding_failures(table, scope)
    if not rows:
        summary = {"recoveryRunId": run_id, "domain": domain,
                   "goldRunId": gold_run_id, "outstanding": 0,
                   "republished": 0, "failed": 0, "skipped": 0,
                   "status": "NOTHING_TO_RECOVER"}
        logger.info("Recovery %s: %s", run_id, json.dumps(summary))
        return summary

    # Run-level accounting, up front per the contract: the failed run's own
    # processor row counts this replay. Best-effort - metrics never block
    # the republish itself.
    _bump_processor_replay_count(args, gold_run_id)

    republished = failed = skipped = 0
    for start in range(0, len(rows), PUT_EVENTS_BATCH_SIZE):
        batch = rows[start:start + PUT_EVENTS_BATCH_SIZE]
        entries, keyed = [], []
        for row in batch:
            publish_id = _s(row, "publish_id")
            base = dynamodb.get_item(
                TableName=table,
                Key={"publish_id": {"S": publish_id}}).get("Item") or {}
            payload = base.get("payload", {}).get("B")
            if payload is None:
                failed += _apply_rule3(table, publish_id, run_id,
                                       "PAYLOAD_MISSING",
                                       "row carries no stored payload")
                continue
            envelope = json.loads(gzip.decompress(_bytes(payload)))
            detail_type = (f"{domain}.{_s(row, 'operation')}."
                           f"{_s(row, 'version')}")
            entries.append({
                "EventBusName": bus,
                "Source": str(envelope.get("source", "")),
                "DetailType": detail_type,
                "Detail": json.dumps(envelope),
            })
            keyed.append((publish_id, str(envelope.get("id", ""))))
        if not entries:
            continue
        for (publish_id, envelope_id), (ok, code, msg) in zip(
                keyed, _publish_batch(entries)):
            if ok:
                outcome = _apply_rule2(table, publish_id, run_id, envelope_id)
                republished += outcome == "republished"
                skipped += outcome == "skipped"
            else:
                failed += _apply_rule3(table, publish_id, run_id, code, msg)

    if failed == 0:
        status = "SUCCEEDED"
    elif republished:
        status = "PARTIAL_FAILURE"
    else:
        status = "FAILED"
    _record_run(args, run_id, domain, gold_run_id, status,
                started_at=started_at, duration=time.time() - t0,
                outstanding=len(rows), republished=republished, failed=failed)

    summary = {"recoveryRunId": run_id, "domain": domain,
               "goldRunId": gold_run_id, "outstanding": len(rows),
               "republished": republished, "failed": failed,
               "skipped": skipped, "status": status}
    logger.info("Recovery %s %s: %s", run_id, status, json.dumps(summary))
    return summary


def _outstanding_failures(table: str, scope: str) -> list[dict[str, Any]]:
    """Every outstanding PUBLISH_FAILED row for the Gold run, all versions -
    gsi_recovery is sparse by construction, so no filter is needed."""
    rows: list[dict[str, Any]] = []
    kwargs: dict[str, Any] = {
        "TableName": table,
        "IndexName": "gsi_recovery",
        "KeyConditionExpression": "recovery_scope = :s",
        "ExpressionAttributeValues": {":s": {"S": scope}},
    }
    while True:
        page = dynamodb.query(**kwargs)
        rows.extend(page.get("Items", []))
        if "LastEvaluatedKey" not in page:
            return rows
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def _publish_batch(entries: list[dict[str, Any]]) -> list[tuple[bool, str, str]]:
    """One PutEvents call; per-entry outcomes, mirroring the processor. A
    whole-call failure marks every entry BATCH_CALL_FAILED so the rows stay
    queued for the next recovery instead of failing the run invisibly."""
    try:
        response = events.put_events(Entries=entries)
    except Exception as exc:  # noqa: BLE001 - the batch outcome carries it
        logger.exception("PutEvents call failed for a batch of %d entries",
                         len(entries))
        return [(False, "BATCH_CALL_FAILED", str(exc)[:500])] * len(entries)
    results = []
    for entry, result in zip(entries, response.get("Entries", [])):
        if result.get("ErrorCode"):
            logger.error("PutEvents rejected %s: %s %s", entry["DetailType"],
                         result.get("ErrorCode"), result.get("ErrorMessage"))
            results.append((False, result["ErrorCode"],
                            result.get("ErrorMessage") or ""))
        else:
            results.append((True, "", ""))
    return results


def _apply_rule2(table: str, publish_id: str, run_id: str,
                 envelope_id: str) -> str:
    """Write rule 2: PUBLISH_FAILED -> PUBLISHED on the SAME row. REMOVE
    recovery_scope takes it out of gsi_recovery; reason/error_msg clear;
    event_id stays the envelope id (the replay is byte-identical, so it IS
    the same envelope). Optimistic on status - a racing replay's win means
    the bus accepted the event twice, but only one run owns the row."""
    try:
        dynamodb.update_item(
            TableName=table,
            Key={"publish_id": {"S": publish_id}},
            UpdateExpression=("SET #s = :published, event_run_id = :run, "
                              "event_id = :eid, published_at = :now, "
                              "last_updated_at = :now "
                              "REMOVE recovery_scope, reason, error_msg "
                              "ADD attempt_count :one"),
            ConditionExpression="#s = :failed",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":published": {"S": "PUBLISHED"},
                ":failed": {"S": "PUBLISH_FAILED"},
                ":run": {"S": run_id},
                ":eid": {"S": envelope_id},
                ":now": {"S": _utcnow()},
                ":one": {"N": "1"},
            },
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            logger.info("Row %s already settled by a concurrent recovery",
                        publish_id)
            return "skipped"
        raise
    return "republished"


def _apply_rule3(table: str, publish_id: str, run_id: str, reason: str,
                 error_msg: str) -> int:
    """Write rule 3: still failed - reason/error_msg refreshed, attempt
    counted, recovery_scope left in place (stays queued). Returns 1 so the
    caller can count with +=; a lost race counts 0."""
    try:
        dynamodb.update_item(
            TableName=table,
            Key={"publish_id": {"S": publish_id}},
            UpdateExpression=("SET event_run_id = :run, reason = :why, "
                              "error_msg = :msg, last_updated_at = :now "
                              "ADD attempt_count :one"),
            ConditionExpression="#s = :failed",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":failed": {"S": "PUBLISH_FAILED"},
                ":run": {"S": run_id},
                ":why": {"S": reason or "UNKNOWN"},
                ":msg": {"S": (error_msg or "")[:1000]},
                ":now": {"S": _utcnow()},
                ":one": {"N": "1"},
            },
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            return 0
        raise
    logger.warning("Recovery of %s failed again: %s %s", publish_id, reason,
                   error_msg[:200])
    return 1


def _bump_processor_replay_count(args: dict[str, str], gold_run_id: str) -> None:
    """replay_count + 1 on the failed run's own INCREMENTAL row (the most
    recent one, via the sparse gsi_gold_run). Best-effort by contract."""
    table = args.get("pipeline_metrics_table")
    if not table:
        return
    try:
        items = dynamodb.query(
            TableName=table,
            IndexName="gsi_gold_run",
            KeyConditionExpression="gold_run_id = :g AND #m = :mode",
            ExpressionAttributeNames={"#m": "mode"},
            ExpressionAttributeValues={":g": {"S": gold_run_id},
                                       ":mode": {"S": "INCREMENTAL"}},
        ).get("Items", [])
        if not items:
            return
        latest = max(items, key=lambda i: _s(i, "started_at"))
        dynamodb.update_item(
            TableName=table,
            Key={"event_run_id": latest["event_run_id"],
                 "mode": latest["mode"]},
            UpdateExpression="ADD replay_count :one SET last_updated_at = :now",
            ExpressionAttributeValues={":one": {"N": "1"},
                                       ":now": {"S": _utcnow()}},
        )
    except Exception:  # noqa: BLE001 - metrics are best-effort by contract
        logger.exception("replay_count bump failed for %s", gold_run_id)


def _record_run(args: dict[str, str], run_id: str, domain: str,
                gold_run_id: str, status: str, *, started_at: str,
                duration: float, outstanding: int, republished: int,
                failed: int) -> None:
    """This run's own pipeline-metrics row: mode REPLAY, SAME gold_run_id
    (so gsi_gold_run answers 'what has run for this Gold run' across
    processor and recovery), engine indicator PYTHON_SHELL."""
    table = args.get("pipeline_metrics_table")
    if not table:
        return
    ttl_days = int(args.get("metrics_ttl_days") or "90")
    item = {
        "event_run_id": {"S": run_id},
        "mode": {"S": "REPLAY"},
        "domain": {"S": domain},
        "gold_run_id": {"S": gold_run_id},
        "operation": {"S": "OVERALL"},
        "status": {"S": status},
        "worker_type": {"S": "PYTHON_SHELL"},
        "source_row_count": {"N": str(outstanding)},
        "published_row_count": {"N": str(republished)},
        "failed_row_count": {"N": str(failed)},
        "started_by": {"S": args["started_by"]},
        "started_at": {"S": started_at},
        "completed_at": {"S": _utcnow()},
        "duration_seconds": {"N": str(round(duration, 3))},
        "last_updated_at": {"S": _utcnow()},
        "ttl": {"N": str(int(time.time()) + ttl_days * 86400)},
    }
    try:
        dynamodb.put_item(TableName=table, Item=item)
    except Exception:  # noqa: BLE001 - metrics are best-effort by contract
        logger.exception("pipeline-metrics write failed for %s", run_id)


def _s(item: dict[str, Any] | None, attr: str) -> str:
    return (item or {}).get(attr, {}).get("S", "")


def _bytes(value: Any) -> bytes:
    return bytes(value) if not isinstance(value, bytes) else value


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    main(sys.argv[1:])
