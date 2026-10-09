"""Delivery Fabric — the single writer of delivery outcomes (POC2, Option B).

Trigger: the shared delivery buffer (SQS ESM, batch 10, ReportBatchItemFailures).
Each record body is transformer-stamped: {"rule_arn": "...", "event": {envelope}}.

Per record:
  1. rule_arn -> subscription-configs (gsi_rule_arn): customer + real target
  2. deliver THE EVENT ITSELF: sqs.send_message / kinesis.put_record
  3. the API RESPONSE is the positive acknowledgment -> write the outcome row
     born DELIVERED, with the target's ack (MessageId / SequenceNumber) attached

There is no ATTEMPTED state, no flip, no retry-window wait, no cross-check:
the outcome is observed, not inferred. A delivery exception -> the record
returns via batchItemFailures -> buffer redelivers with backoff -> after
maxReceiveCount the buffer DLQ gets it and failed_writer records FAILED.

At-least-once note: a crash between send and outcome-write means redelivery ->
a duplicate send to the consumer (platform contract: consumers dedupe on the
event id) -> the second outcome write bounces off the conditional put.
"""
import json
import os
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

DOMAIN = os.environ["DOMAIN"]
ddb = boto3.resource("dynamodb")
sqs = boto3.client("sqs")
kinesis = boto3.client("kinesis")
t_delivery = ddb.Table(os.environ["T_DELIVERY"])
t_publish = ddb.Table(os.environ["T_PUBLISH"])
t_subs = ddb.Table(os.environ["T_SUBSCRIPTIONS"])

TTL_DAYS = 90
_queue_url_cache: dict[str, str] = {}


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _queue_url(queue_arn: str) -> str:
    if queue_arn not in _queue_url_cache:
        name = queue_arn.split(":")[-1]
        _queue_url_cache[queue_arn] = sqs.get_queue_url(QueueName=name)["QueueUrl"]
    return _queue_url_cache[queue_arn]


def _extract(payload: dict, dotted_path: str):
    cur = payload
    for seg in dotted_path.split("."):
        cur = cur[seg]
    return cur


def _deliver(sub: dict, envelope: dict) -> tuple[str, str]:
    """Perform the actual delivery. Returns (ack_kind, ack_value) — the
    target API's positive acknowledgment. Raises on any failure."""
    target = sub["target"]
    body = json.dumps(envelope)
    if target["target_type"] == "SQS":
        resp = sqs.send_message(QueueUrl=_queue_url(target["target_arn"]),
                                MessageBody=body)
        return "SQS_MESSAGE_ID", resp["MessageId"]
    if target["target_type"] == "KINESIS":
        pk_path = target.get("target_config", {}).get(
            "partition_key_path", "detail.data.ppid")
        resp = kinesis.put_record(
            StreamName=target["target_arn"].split("/")[-1],
            Data=body.encode(),
            PartitionKey=str(_extract(envelope, pk_path)))
        return "KINESIS_SEQUENCE_NUMBER", resp["SequenceNumber"]
    raise ValueError(f"unsupported target_type {target['target_type']}")


def _process(record):
    msg = json.loads(record["body"])
    rule_arn, envelope = msg["rule_arn"], msg["event"]
    event_id = envelope["id"]

    subs = t_subs.query(IndexName="gsi_rule_arn",
                        KeyConditionExpression=Key("rule_arn").eq(rule_arn))["Items"]
    if not subs:
        raise LookupError(f"rule {rule_arn} not in subscription-configs")
    sub = subs[0]
    if sub.get("status") != "ACTIVE":
        # POC simplification: non-ACTIVE -> retry, so the event eventually
        # parks in the DLQ (recoverable) instead of vanishing. The tiered
        # design's real answer is a dedicated buffer with a disabled ESM.
        raise RuntimeError(f"subscription {sub['customer_id']} is {sub.get('status')}")
    customer = sub["customer_id"]

    # Sort-key purity: published_at comes from the ledger, as everywhere else.
    pm = t_publish.query(IndexName="gsi_event_id",
                         KeyConditionExpression=Key("event_id").eq(event_id))["Items"]
    if not pm:
        raise LookupError(f"event {event_id} not in publish ledger yet")
    row = pm[0]

    # THE delivery — and its acknowledgment.
    ack_kind, ack_value = _deliver(sub, envelope)

    try:
        t_delivery.put_item(
            Item={
                "domain_customer": f"{DOMAIN}#{customer}",
                "delivery_id": f"{row['published_at']}#{event_id}",
                "customer_id": customer, "domain": DOMAIN,
                "primary_key": row["primary_key"], "operation": row["operation"],
                "detail_type": envelope.get("detail-type", row.get("detail_type", "")),
                "event_id": event_id,
                "status": "DELIVERED",
                "confirmed_by": "TARGET_API_RESPONSE",
                "target_ack_kind": ack_kind, "target_ack": ack_value,
                "delivered_at": now_iso(),
                "gold_run_id": row["gold_run_id"], "event_run_id": row["event_run_id"],
                "ttl": int(datetime.now(timezone.utc).timestamp()) + TTL_DAYS * 86400,
            },
            ConditionExpression="attribute_not_exists(domain_customer)",
        )
        return {"customer": customer, "event_id": event_id, "outcome": "DELIVERED"}
    except ClientError as e:
        if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
        # Redelivered record whose earlier pass already wrote the outcome.
        return {"customer": customer, "event_id": event_id, "outcome": "duplicate"}


def handler(event, context):
    results, failures = [], []
    for record in event["Records"]:
        try:
            results.append(_process(record))
        except Exception as exc:  # isolate per record; buffer redrives it
            print(json.dumps({"error": str(exc), "messageId": record.get("messageId"),
                               "receive_count": record.get("attributes", {}).get(
                                   "ApproximateReceiveCount")}))
            failures.append({"itemIdentifier": record["messageId"]})

    print(json.dumps({"delivered": results, "retried_records": len(failures)}))
    return {"batchItemFailures": failures}
