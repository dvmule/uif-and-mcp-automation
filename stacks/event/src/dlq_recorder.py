"""DLQ recorder: turn failed subscriber deliveries into delivery-metrics rows.

The pipeline's DLQ (uif-event-<env>-product-dlq) receives an EventBridge
message whenever a subscription target rejects an event after the rule's
configured retries. This Lambda consumes that queue (SQS event source
mapping, partial-batch responses) and, per the Table 4 contract
(docs/delivery-metrics.md), inserts ONE row per failed delivery into
uif-event-<env>-delivery-metrics:

  * idempotent: the EventBridge EventId (body["id"]) is checked against
    gsi_event_id first, and the insert is conditional - a redelivered DLQ
    message never duplicates a row;
  * self-contained: the sent payload is copied in at DLQ time from
    publish-metrics (base-table read - payload is excluded from GSI
    projections), falling back to the DLQ'd event's own detail when no
    per-event row exists, so redrive works regardless of Table 3 retention;
  * lineage: body["id"] -> publish-metrics gsi_event_id gives publish_id,
    gold_run_id, event_run_id, primary_key, operation, version;
  * customer: RULE_ARN -> subscription-configs gsi_rule_arn (TARGET_ARN ->
    gsi_target_arn as the fallback); while subscriptions are still seeded
    PENDING with provisioner-future rule ARNs, the last resort parses the
    customer from the target's resource name;
  * FAILED rows carry NO ttl (two-phase TTL: an unresolved failure never
    silently expires); redrive/abandon set it later.

The DLQ is shared with the trigger Lambda's async-failure dead letters;
messages without a RULE_ARN attribute are not subscriber-delivery failures
- they are logged in full and dropped from the queue (they were already
only rotting there), never written to Table 4.

Read-only on publish-metrics and subscription-configs; the ONLY table this
writes is delivery-metrics.
"""

from __future__ import annotations

import gzip
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.client("dynamodb")

DELIVERY_METRICS_TABLE = os.environ["DELIVERY_METRICS_TABLE"]
PUBLISH_METRICS_TABLE = os.environ.get("PUBLISH_METRICS_TABLE", "")
SUBSCRIPTION_CONFIGS_TABLE = os.environ.get("SUBSCRIPTION_CONFIGS_TABLE", "")

# EventBridge sets these message attributes on dead-lettered deliveries.
ATTR_ERROR_CODE = "ERROR_CODE"
ATTR_ERROR_MESSAGE = "ERROR_MESSAGE"
ATTR_RULE_ARN = "RULE_ARN"
ATTR_TARGET_ARN = "TARGET_ARN"
ATTR_EXHAUSTED = "EXHAUSTED_RETRY_CONDITION"


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """SQS batch entry point. Returns partial-batch failures so only the
    records that genuinely errored are redelivered."""
    failures: list[dict[str, str]] = []
    for record in event.get("Records", []):
        try:
            _process_record(record)
        except Exception:  # noqa: BLE001 - fail ONLY this record, loudly
            logger.exception("DLQ record %s failed - returned to the queue",
                             record.get("messageId"))
            failures.append({"itemIdentifier": record["messageId"]})
    return {"batchItemFailures": failures}


def _process_record(record: dict[str, Any]) -> None:
    attrs = {name: (value or {}).get("stringValue")
             for name, value in (record.get("messageAttributes") or {}).items()}
    rule_arn = attrs.get(ATTR_RULE_ARN)
    if not rule_arn:
        # Not an EventBridge delivery failure (e.g. the trigger Lambda's own
        # async dead letter). Log the whole message so nothing is lost
        # silently, then let SQS delete it - Table 4 is delivery-only.
        logger.error("DLQ message %s has no RULE_ARN - not a subscriber "
                     "delivery failure; dropping after logging. Body: %s",
                     record.get("messageId"), record.get("body", "")[:2000])
        return

    body = json.loads(record["body"])
    # EventBridge applies the target's input transform BEFORE dead-lettering
    # (found live 2026-09-21), and our rule targets use InputPath = $.detail
    # - so a dead letter is normally the BARE CloudEvents envelope, and the
    # wrapped shape survives only for pre-InputPath targets. In both shapes
    # the correlator is the ENVELOPE id (Table 3 ledgers it as event_id);
    # the wrapper's bus-assigned id never reaches this Lambda any more.
    if "specversion" in body:  # bare envelope (post-InputPath)
        detail = body
        detail_type = _detail_type_from_envelope(body)
    else:  # wrapped event (pre-InputPath targets, e.g. other buses)
        detail = body.get("detail") or {}
        if isinstance(detail, str):
            detail = json.loads(detail)
        detail_type = str(body.get("detail-type") or "")
    event_id = detail.get("id") or body.get("id")
    if not event_id:
        raise ValueError("Dead-lettered event has no id")

    # Idempotency: a DLQ redelivery (or a retried batch) must not duplicate.
    if _delivery_row_exists(event_id):
        logger.info("Delivery failure for event %s already recorded - skipped",
                    event_id)
        return

    # The envelope subject ({domain}/{key}) is the domain authority; the
    # detail-type leads with it too (product.updated.v1).
    domain = (str(detail.get("subject", "")).split("/", 1)[0]
              or (detail_type.split(".", 1)[0] if detail_type else "")
              or "unknown")

    lineage = _publish_lineage(event_id)
    subscription = _resolve_subscription(rule_arn, attrs.get(ATTR_TARGET_ARN))

    customer_id = (subscription.get("customer_id")
                   or _customer_from_target(attrs.get(ATTR_TARGET_ARN))
                   or "unknown")
    domain = lineage.get("domain") or domain
    primary_key = (lineage.get("primary_key")
                   or str(detail.get("subject", "")).split("/", 1)[-1]
                   or "unknown")
    operation = lineage.get("operation") or _operation_from_detail_type(detail_type)

    # The payload copied in AT DLQ TIME: Table 3's stored bytes when the
    # per-event row exists, else the dead-lettered event's own detail.
    payload = lineage.get("payload") or gzip.compress(
        json.dumps(detail).encode("utf-8"))

    failed_at = str(detail.get("time") or body.get("time") or _utcnow())
    now = _utcnow()

    item: dict[str, Any] = {
        "domain_customer": {"S": f"{domain}#{customer_id}"},
        "delivery_id": {"S": f"{failed_at}#{event_id}"},
        "customer_id": {"S": customer_id},
        "domain": {"S": domain},
        "primary_key": {"S": primary_key},
        "operation": {"S": operation},
        "detail_type": {"S": detail_type},
        "event_id": {"S": str(event_id)},
        "status": {"S": "FAILED"},
        "reason": {"S": attrs.get(ATTR_ERROR_CODE) or "UNKNOWN"},
        "error_msg": {"S": (attrs.get(ATTR_ERROR_MESSAGE) or "")[:1000]},
        "exhausted_by": {"S": attrs.get(ATTR_EXHAUSTED) or "UNKNOWN"},
        "target_arn": {"S": attrs.get(ATTR_TARGET_ARN) or "unknown"},
        "failed_at": {"S": failed_at},
        "redrive_count": {"N": "0"},
        "redriven_by": {"NULL": True},
        "last_redriven_at": {"NULL": True},
        "replayed_at": {"NULL": True},
        "abandoned_at": {"NULL": True},
        "abandoned_by": {"NULL": True},
        "abandon_reason": {"NULL": True},
        "payload": {"B": payload},
        "rule_arn": {"S": rule_arn},
        "redrive_run_id": {"NULL": True},
        "dlq_received_at": {"S": now},
        "last_updated_at": {"S": now},
        # NO ttl while FAILED - an unresolved failure never silently expires.
    }
    # Lineage keys are OMITTED when absent, never written NULL: gold_run_id
    # keys delivery-metrics' gsi_gold_run, and DynamoDB rejects a NULL-typed
    # index key outright (ValidationException on the whole PutItem) - the
    # 2026-09-21 live test proved a lineage-less dead letter crash-looped the
    # recorder on exactly this. Omission is also what makes the GSI sparse.
    for key in ("gold_run_id", "event_run_id", "publish_id"):
        if lineage.get(key):
            item[key] = {"S": lineage[key]}

    try:
        dynamodb.put_item(
            TableName=DELIVERY_METRICS_TABLE,
            Item=item,
            ConditionExpression="attribute_not_exists(domain_customer)",
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            logger.info("Delivery row for event %s already exists - skipped", event_id)
            return
        raise
    logger.info("Recorded FAILED delivery: customer=%s event=%s reason=%s "
                "(lineage %s)", customer_id, event_id,
                attrs.get(ATTR_ERROR_CODE), lineage.get("publish_id") or "none")


def _delivery_row_exists(event_id: str) -> bool:
    response = dynamodb.query(
        TableName=DELIVERY_METRICS_TABLE,
        IndexName="gsi_event_id",
        KeyConditionExpression="event_id = :e",
        ExpressionAttributeValues={":e": {"S": str(event_id)}},
        Limit=1,
    )
    return bool(response.get("Items"))


def _publish_lineage(event_id: str) -> dict[str, Any]:
    """Table 3 lineage for the failed publish: gsi_event_id -> keys, then a
    base-table GetItem for the payload (excluded from GSI projections).
    Missing lineage degrades - the row is still written, self-contained."""
    if not PUBLISH_METRICS_TABLE:
        return {}
    try:
        items = dynamodb.query(
            TableName=PUBLISH_METRICS_TABLE,
            IndexName="gsi_event_id",
            KeyConditionExpression="event_id = :e",
            ExpressionAttributeValues={":e": {"S": str(event_id)}},
            Limit=1,
        ).get("Items", [])
        if not items:
            logger.warning("No publish-metrics row for event %s - recording "
                           "without Table 3 lineage", event_id)
            return {}
        hit = items[0]
        # publish_id is the base-table key; GSI keys project automatically...
        # but publish_id must be reconstructed from the projected columns if
        # absent (INCLUDE projections always carry the table keys, so it is
        # present in practice).
        publish_id = hit.get("publish_id", {}).get("S")
        lineage = {
            "publish_id": publish_id,
            "domain": hit.get("domain", {}).get("S"),
            "gold_run_id": hit.get("gold_run_id", {}).get("S"),
            "event_run_id": hit.get("event_run_id", {}).get("S"),
            "primary_key": hit.get("primary_key", {}).get("S"),
            "operation": hit.get("operation", {}).get("S"),
            "version": hit.get("version", {}).get("S"),
        }
        if publish_id:
            row = dynamodb.get_item(
                TableName=PUBLISH_METRICS_TABLE,
                Key={"publish_id": {"S": publish_id}},
            ).get("Item") or {}
            if "payload" in row and "B" in row["payload"]:
                lineage["payload"] = row["payload"]["B"]
        return lineage
    except Exception:  # noqa: BLE001 - lineage is best-effort by contract
        logger.exception("publish-metrics lineage lookup failed for %s", event_id)
        return {}


def _resolve_subscription(rule_arn: str | None, target_arn: str | None) -> dict[str, Any]:
    """RULE_ARN -> subscription row (gsi_rule_arn); TARGET_ARN -> gsi_target_arn
    as the fallback. Best-effort: seeds are PENDING with provisioner-future
    rule ARNs, so a miss is expected until the provisioner exists."""
    if not SUBSCRIPTION_CONFIGS_TABLE:
        return {}
    for index, key, value in (("gsi_rule_arn", "rule_arn", rule_arn),
                              ("gsi_target_arn", "target_arn", target_arn)):
        if not value:
            continue
        try:
            items = dynamodb.query(
                TableName=SUBSCRIPTION_CONFIGS_TABLE,
                IndexName=index,
                KeyConditionExpression=f"{key} = :v",
                ExpressionAttributeValues={":v": {"S": value}},
                Limit=1,
            ).get("Items", [])
            if items:
                return {"customer_id": items[0].get("customer_id", {}).get("S")}
        except Exception:  # noqa: BLE001 - resolution is best-effort
            logger.exception("subscription lookup via %s failed", index)
    return {}


def _customer_from_target(target_arn: str | None) -> str | None:
    """Last-resort customer extraction from a managed target's resource name:
    uif-event-<env>-<customer>-... (naming convention). None if unparseable."""
    if not target_arn:
        return None
    name = target_arn.rsplit(":", 1)[-1].rsplit("/", 1)[-1]
    parts = name.split("-")
    if len(parts) >= 4 and parts[0] == "uif" and parts[1] == "event":
        return parts[3]
    return None


def _detail_type_from_envelope(envelope: dict[str, Any]) -> str:
    """Reconstruct the bus DetailType from the envelope's CloudEvents type:
    com.pearson.{domain}.{entity}.{operation}.{version} -> {domain}.{operation}.{version}
    (the entity is dropped - detail types are domain-scoped)."""
    parts = str(envelope.get("type", "")).split(".")
    if len(parts) >= 6 and parts[:2] == ["com", "pearson"]:
        return f"{parts[2]}.{parts[-2]}.{parts[-1]}"
    return ""


def _operation_from_detail_type(detail_type: str) -> str:
    """product.updated.v2 -> updated; channel tags (product.SQS) -> unknown."""
    parts = detail_type.split(".")
    if len(parts) >= 2 and parts[-1].startswith("v"):
        return parts[-2]
    return "unknown"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
