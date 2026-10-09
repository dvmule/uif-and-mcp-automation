"""Failed writer — the buffer DLQ consumer (POC2).

A message reaching the buffer DLQ means the Delivery Fabric could not deliver
it within maxReceiveCount attempts (or EventBridge could not reach the buffer
at all, in which case the body is the raw envelope without our transformer
wrapper — both shapes are handled). This Lambda records the FAILED outcome.

The precise SDK exception lives in the fabric's logs; this row records that
retries were exhausted, the receive count, and full lineage where resolvable.
Degrades gracefully when the ledger row is missing (null lineage, sort key
from the envelope's own time) rather than retrying forever — the 9d0fbfad
lesson from POC1.
"""
import json
import os
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

DOMAIN = os.environ["DOMAIN"]
ddb = boto3.resource("dynamodb")
t_delivery = ddb.Table(os.environ["T_DELIVERY"])
t_publish = ddb.Table(os.environ["T_PUBLISH"])
t_subs = ddb.Table(os.environ["T_SUBSCRIPTIONS"])


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _process(record):
    msg = json.loads(record["body"])
    if "rule_arn" in msg and "event" in msg:      # fabric-exhaustion shape
        rule_arn, envelope = msg["rule_arn"], msg["event"]
        reason = "DELIVERY_RETRIES_EXHAUSTED"
    else:                                          # EventBridge->buffer dead-letter shape
        rule_arn = (record.get("messageAttributes", {})
                    .get("RULE_ARN", {}) or {}).get("stringValue")
        envelope = msg
        reason = "BUFFER_DELIVERY_FAILED"
    event_id = envelope["id"]

    customer = None
    if rule_arn:
        subs = t_subs.query(IndexName="gsi_rule_arn",
                            KeyConditionExpression=Key("rule_arn").eq(rule_arn))["Items"]
        if subs:
            customer = subs[0]["customer_id"]
    if customer is None:
        customer = "unresolved"

    pm = t_publish.query(IndexName="gsi_event_id",
                         KeyConditionExpression=Key("event_id").eq(event_id))["Items"]
    row = pm[0] if pm else {}
    published_at = row.get("published_at") or envelope.get("time", "")

    sets = ["#st = :failed", "customer_id = :cust", "#dom = :dom",
            "event_id = :eid", "detail_type = :dt", "reason = :rsn",
            "receive_count = :rc", "failed_at = :now", "last_updated_at = :now"]
    values = {
        ":failed": "FAILED", ":cust": customer, ":dom": DOMAIN,
        ":eid": event_id,
        ":dt": envelope.get("detail-type", row.get("detail_type", "")),
        ":rsn": reason,
        ":rc": record.get("attributes", {}).get("ApproximateReceiveCount", ""),
        ":now": now_iso(),
    }
    # Lineage only when the ledger row exists. gold_run_id is a GSI key and
    # DynamoDB rejects NULL for key attributes; OMITTING it keeps a ghost row
    # sparsely out of gsi_gold_run — the correct semantic for an event no run
    # claims. (Found by the local suite: a NULL here made the degrade-write
    # throw ValidationException and the record retry forever.)
    for attr, ph, val in (("primary_key", ":pk2", row.get("primary_key")),
                           ("gold_run_id", ":gr", row.get("gold_run_id")),
                           ("event_run_id", ":er", row.get("event_run_id"))):
        if val is not None:
            sets.append(f"{attr} = {ph}")
            values[ph] = val

    t_delivery.update_item(
        Key={"domain_customer": f"{DOMAIN}#{customer}",
             "delivery_id": f"{published_at}#{event_id}"},
        UpdateExpression="SET " + ", ".join(sets),
        ExpressionAttributeNames={"#st": "status", "#dom": "domain"},
        ExpressionAttributeValues=values,
    )
    return {"customer": customer, "event_id": event_id, "reason": reason}


def handler(event, context):
    results, failures = [], []
    for record in event["Records"]:
        try:
            results.append(_process(record))
        except Exception as exc:
            print(json.dumps({"error": str(exc), "messageId": record.get("messageId")}))
            failures.append({"itemIdentifier": record["messageId"]})

    print(json.dumps({"failed_recorded": results, "retried_records": len(failures)}))
    return {"batchItemFailures": failures}
