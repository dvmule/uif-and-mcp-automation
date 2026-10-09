"""Event redrive: re-deliver FAILED deliveries to the subscriber's CURRENT target.

The customer-facing repair tool for the delivery plane (Table 4 write rules
2-3 + the dispatch contract, docs/delivery-metrics.md). An event here was
ACCEPTED by the bus but rejected by the customer's target after EventBridge's
retries; the DLQ recorder ledgered it FAILED with the exact published payload
copied in. This Lambda pays that debt:

  * steward-invoked (no event source): {"domain", "customer_id",
    "redriven_by", optional "detail_type", optional "limit"} - the identity
    that pressed the button is per-item audit (redriven_by); the run's
    pipeline-metrics row carries the customer_id as started_by, per that
    table's contract;
  * resolves the subscription's CURRENT target from subscription-configs
    (never delivery-metrics' stored target_arn - "what rejected the event
    then" vs "where the customer receives now") and requires status
    ACTIVE/SUSPENDED, else the rows fail with SUBSCRIPTION_NOT_ACTIVE;
  * re-sends the row's own stored payload (gzip -> the CloudEvents envelope,
    the byte-level replay source) DIRECTLY to the target - SendMessageBatch,
    no PutEvents, no bus, no fan-out: only the owed customer receives it,
    identical in shape to a first delivery (rule targets deliver bare
    envelopes via InputPath), with a fresh envelope id;
  * bookkeeping per row, optimistic on status = FAILED so two racing
    redrives cannot both apply: success -> REPLAYED + new event_id +
    ttl (+90d, the terminal-only TTL); failure -> reason/error_msg updated,
    redrive_count incremented, row stays queued;
  * run-level: one pipeline-metrics row, mode = REDRIVE, event_run_id =
    rd_<uuid>, worker_type = "LAMBDA" (the engine indicator - no Glue job
    involved), no gold_run_id (a redrive is driven by a customer's failures,
    not a Gold run).

Managed SQS targets authorize through this function's own role; a
bring-your-own queue needs the {policy_sid}r statement the provisioner
writes once REDRIVE_ROLE_ARN is set (subscription-configs contract).
KINESIS/API targets are future scope - their rows fail visibly with
TARGET_TYPE_UNSUPPORTED rather than being skipped silently.
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.client("dynamodb")
sqs = boto3.client("sqs")

DELIVERY_METRICS_TABLE = os.environ["DELIVERY_METRICS_TABLE"]
SUBSCRIPTION_CONFIGS_TABLE = os.environ["SUBSCRIPTION_CONFIGS_TABLE"]
PIPELINE_METRICS_TABLE = os.environ.get("PIPELINE_METRICS_TABLE", "")
# Terminal delivery rows keep 90 days (Table 4 contract); the redrive's own
# run row uses a shorter operational window.
REPLAYED_TTL_DAYS = int(os.environ.get("REPLAYED_TTL_DAYS", "90"))
RUN_TTL_DAYS = int(os.environ.get("RUN_TTL_DAYS", "30"))

# One SendMessageBatch call takes at most 10 entries.
SEND_BATCH_SIZE = 10
# A steward redrive is bounded - Lambda timeout is the backstop, this is the
# deliberate cap; run again for the remainder (oldest-first either way).
DEFAULT_LIMIT = 200

SENDABLE = {"ACTIVE", "SUSPENDED"}


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    if not isinstance(event, dict):
        raise ValueError("Expected a JSON object payload")
    domain = str(event.get("domain") or "")
    customer = str(event.get("customer_id") or "")
    redriven_by = str(event.get("redriven_by") or "")
    if not (domain and customer and redriven_by):
        raise ValueError('Payload needs {"domain", "customer_id", '
                         '"redriven_by"} - redrives are attributed, always')
    only_detail_type = str(event.get("detail_type") or "")
    limit = int(event.get("limit") or DEFAULT_LIMIT)

    run_id = f"rd_{uuid.uuid4().hex}"
    started_at = _utcnow()
    t0 = time.time()

    rows = _outstanding_failures(domain, customer, only_detail_type, limit)
    if not rows:
        logger.info("Redrive %s: nothing outstanding for %s#%s%s", run_id,
                    domain, customer,
                    f" ({only_detail_type})" if only_detail_type else "")
        return {"redriveRunId": run_id, "domain": domain,
                "customerId": customer, "outstanding": 0, "replayed": 0,
                "failed": 0, "skipped": 0, "status": "NOTHING_TO_REDRIVE"}

    replayed = failed = skipped = 0
    # One subscription lookup per detail-type, not per row.
    subscriptions: dict[str, dict[str, Any] | None] = {}
    for row in rows:
        detail_type = _s(row, "detail_type")
        if detail_type not in subscriptions:
            subscriptions[detail_type] = _subscription(customer, domain, detail_type)
        sub = subscriptions[detail_type]

        if sub is None or _s(sub, "status") not in SENDABLE:
            outcome = _mark_failed(row, run_id, redriven_by,
                                   "SUBSCRIPTION_NOT_ACTIVE",
                                   f"subscription {domain}#{detail_type} is "
                                   f"{_s(sub, 'status') if sub else 'absent'}")
        else:
            target = sub.get("target", {}).get("M", {})
            if _s(target, "target_type") != "SQS":
                outcome = _mark_failed(row, run_id, redriven_by,
                                       "TARGET_TYPE_UNSUPPORTED",
                                       f"redrive to {_s(target, 'target_type')} "
                                       "targets is future scope")
            else:
                outcome = _redrive_row(row, _s(target, "target_arn"),
                                       run_id, redriven_by)
        if outcome == "replayed":
            replayed += 1
        elif outcome == "failed":
            failed += 1
        else:
            skipped += 1

    if replayed and not failed:
        status = "SUCCEEDED"
    elif replayed:
        status = "PARTIAL_FAILURE"
    else:
        status = "FAILED"
    _record_run(run_id, domain, customer, status, started_at,
                duration=time.time() - t0, outstanding=len(rows),
                replayed=replayed, failed=failed)

    summary = {"redriveRunId": run_id, "domain": domain,
               "customerId": customer, "outstanding": len(rows),
               "replayed": replayed, "failed": failed, "skipped": skipped,
               "status": status}
    logger.info("Redrive %s %s: %s", run_id, status, json.dumps(summary))
    return summary


def _outstanding_failures(domain: str, customer: str, detail_type: str,
                          limit: int) -> list[dict[str, Any]]:
    """FAILED rows for the customer in the domain, oldest first (delivery_id
    leads with failed_at). status is a filter, not a key: the page walks the
    partition until `limit` failures are found or it ends."""
    rows: list[dict[str, Any]] = []
    kwargs: dict[str, Any] = {
        "TableName": DELIVERY_METRICS_TABLE,
        "KeyConditionExpression": "domain_customer = :dc",
        "FilterExpression": "#s = :failed",
        "ExpressionAttributeNames": {"#s": "status"},
        "ExpressionAttributeValues": {
            ":dc": {"S": f"{domain}#{customer}"},
            ":failed": {"S": "FAILED"},
        },
    }
    if detail_type:
        kwargs["FilterExpression"] += " AND detail_type = :dt"
        kwargs["ExpressionAttributeValues"][":dt"] = {"S": detail_type}
    while True:
        page = dynamodb.query(**kwargs)
        rows.extend(page.get("Items", []))
        if len(rows) >= limit or "LastEvaluatedKey" not in page:
            return rows[:limit]
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def _subscription(customer: str, domain: str, detail_type: str) -> dict[str, Any] | None:
    return dynamodb.get_item(
        TableName=SUBSCRIPTION_CONFIGS_TABLE,
        Key={"customer_id": {"S": customer},
             "domain_detail_type": {"S": f"{domain}#{detail_type}"}},
    ).get("Item")


def _redrive_row(row: dict[str, Any], target_arn: str, run_id: str,
                 redriven_by: str) -> str:
    """Send the stored envelope to the current target; settle the row."""
    payload = row.get("payload", {}).get("B")
    if payload is None:
        return _mark_failed(row, run_id, redriven_by, "PAYLOAD_MISSING",
                            "delivery row carries no stored payload")
    envelope = json.loads(gzip.decompress(_bytes(payload)))
    # Fresh envelope id: the ledger's event_id follows it, and a consumer
    # deduping on id treats the redrive as the new delivery it is. Everything
    # else is the stored bytes - the payload is never rebuilt from gold.
    new_id = str(uuid.uuid4())
    envelope["id"] = new_id

    name = target_arn.rsplit(":", 1)[-1]
    account = target_arn.split(":")[4]
    try:
        queue_url = sqs.get_queue_url(
            QueueName=name, QueueOwnerAWSAccountId=account)["QueueUrl"]
        sqs.send_message(QueueUrl=queue_url, MessageBody=json.dumps(envelope))
    except ClientError as exc:
        err = exc.response.get("Error", {})
        return _mark_failed(row, run_id, redriven_by,
                            err.get("Code", "SEND_FAILED"),
                            err.get("Message", str(exc)))
    return _mark_replayed(row, run_id, redriven_by, new_id)


def _mark_replayed(row: dict[str, Any], run_id: str, redriven_by: str,
                   new_event_id: str) -> str:
    """Table 4 write rule 2 - conditional on status = FAILED, so two racing
    redrives cannot both count the same row."""
    now = _utcnow()
    ttl = int(time.time()) + REPLAYED_TTL_DAYS * 86400
    try:
        dynamodb.update_item(
            TableName=DELIVERY_METRICS_TABLE,
            Key=_key(row),
            UpdateExpression=(
                "SET #s = :replayed, event_id = :eid, redrive_run_id = :run, "
                "redriven_by = :who, last_redriven_at = :now, "
                "replayed_at = :now, #ttl = :ttl, last_updated_at = :now "
                "ADD redrive_count :one"
            ),
            ConditionExpression="#s = :failed",
            ExpressionAttributeNames={"#s": "status", "#ttl": "ttl"},
            ExpressionAttributeValues={
                ":replayed": {"S": "REPLAYED"}, ":failed": {"S": "FAILED"},
                ":eid": {"S": new_event_id}, ":run": {"S": run_id},
                ":who": {"S": redriven_by}, ":now": {"S": now},
                ":ttl": {"N": str(ttl)}, ":one": {"N": "1"},
            },
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            logger.info("Row %s already settled by a concurrent redrive - "
                        "the message WAS sent; the other run owns the row",
                        _s(row, "delivery_id"))
            return "skipped"
        raise
    return "replayed"


def _mark_failed(row: dict[str, Any], run_id: str, redriven_by: str,
                 reason: str, error_msg: str) -> str:
    """Table 4 write rule 3 - same condition, NO ttl: the row stays queued."""
    try:
        dynamodb.update_item(
            TableName=DELIVERY_METRICS_TABLE,
            Key=_key(row),
            UpdateExpression=(
                "SET reason = :why, error_msg = :msg, redrive_run_id = :run, "
                "redriven_by = :who, last_redriven_at = :now, "
                "last_updated_at = :now ADD redrive_count :one"
            ),
            ConditionExpression="#s = :failed",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":failed": {"S": "FAILED"}, ":why": {"S": reason},
                ":msg": {"S": error_msg[:1000]}, ":run": {"S": run_id},
                ":who": {"S": redriven_by}, ":now": {"S": _utcnow()},
                ":one": {"N": "1"},
            },
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            return "skipped"
        raise
    logger.warning("Redrive of %s failed: %s %s", _s(row, "delivery_id"),
                   reason, error_msg[:200])
    return "failed"


def _record_run(run_id: str, domain: str, customer: str, status: str,
                started_at: str, *, duration: float, outstanding: int,
                replayed: int, failed: int) -> None:
    """The run's pipeline-metrics row (mode REDRIVE): no gold_run_id, engine
    indicator in worker_type, started_by = the customer (the human is
    per-item on delivery-metrics' redriven_by). Best-effort by contract."""
    if not PIPELINE_METRICS_TABLE:
        return
    item = {
        "event_run_id": {"S": run_id},
        "mode": {"S": "REDRIVE"},
        "domain": {"S": domain},
        "operation": {"S": "OVERALL"},
        "status": {"S": status},
        "worker_type": {"S": "LAMBDA"},
        "reason": ({"S": "TARGET_REJECTED"} if failed else {"NULL": True}),
        "source_row_count": {"N": str(outstanding)},
        "published_row_count": {"N": str(replayed)},
        "failed_row_count": {"N": str(failed)},
        "started_by": {"S": customer},
        "started_at": {"S": started_at},
        "completed_at": {"S": _utcnow()},
        "duration_seconds": {"N": str(round(duration, 3))},
        "last_updated_at": {"S": _utcnow()},
        "ttl": {"N": str(int(time.time()) + RUN_TTL_DAYS * 86400)},
    }
    try:
        dynamodb.put_item(TableName=PIPELINE_METRICS_TABLE, Item=item)
    except Exception:  # noqa: BLE001 - metrics are best-effort by contract
        logger.exception("pipeline-metrics write failed for %s", run_id)


def _key(row: dict[str, Any]) -> dict[str, Any]:
    return {"domain_customer": row["domain_customer"],
            "delivery_id": row["delivery_id"]}


def _s(item: dict[str, Any] | None, attr: str) -> str:
    return (item or {}).get(attr, {}).get("S", "")


def _bytes(value: Any) -> bytes:
    # The client API returns Binary attributes as raw bytes; the resource
    # API (and some test doubles) wrap them.
    return bytes(value) if not isinstance(value, bytes) else value


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
