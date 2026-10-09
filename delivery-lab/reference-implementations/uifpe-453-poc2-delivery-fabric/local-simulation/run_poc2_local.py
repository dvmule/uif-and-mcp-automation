"""POC2 local verification — drives the REAL lambda_src handlers under moto.

Local-vs-AWS fidelity notes:
- moto does not substitute <aws.events.rule-arn>, so each local rule's
  input template hard-codes its own ARN (identical message shape).
- The SQS event source mapping is synthesized: we receive from the buffer,
  build the ESM Records shape, call the real handler, delete successes and
  leave batchItemFailures in the queue (visibility 0 locally so strikes
  re-receive immediately). DLQ movement on maxReceiveCount is simulated by
  the harness when moto doesn't move messages itself.
"""
import importlib
import json
import os
import sys

os.environ["AWS_ACCESS_KEY_ID"] = "testing"
os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
os.environ["AWS_DEFAULT_REGION"] = "eu-west-1"

PREFIX = "uif-event-poc2"
DOMAIN = "product"
DETAIL_TYPE = "product.created.v1"
os.environ["T_PUBLISH"] = f"{PREFIX}-publish-metrics"
os.environ["T_DELIVERY"] = f"{PREFIX}-delivery-metrics"
os.environ["T_SUBSCRIPTIONS"] = f"{PREFIX}-subscription-configs"
os.environ["DOMAIN"] = DOMAIN
os.environ["PREFIX"] = PREFIX
os.environ["BUS_NAME"] = f"{PREFIX}-{DOMAIN}-bus"
os.environ["DETAIL_TYPE"] = DETAIL_TYPE

# Import the REAL handler modules being shipped (repo layout first, then the
# authoring-workspace layout, so the suite runs from either location).
_here = os.path.dirname(os.path.abspath(__file__))
for _cand in (os.path.join(_here, "..", "terraform", "lambda_src"),
              os.path.join(_here, "..", "poc2-terraform", "lambda_src")):
    if os.path.isfile(os.path.join(_cand, "delivery_fabric.py")):
        sys.path.insert(0, _cand)
        break

import boto3
from moto import mock_aws

PASS, FAIL = "PASS", "FAIL"
results = []


def check(name, cond, detail=""):
    results.append((PASS if cond else FAIL, name))
    print(f"  [{PASS if cond else FAIL}] {name}" + (f"  ({detail})" if detail else ""))


def build_infra(events, sqs, kinesis, ddb):
    for name, extra_gsis in (
        (os.environ["T_PUBLISH"], []),
        (os.environ["T_DELIVERY"], []),
    ):
        pass  # created explicitly below for differing schemas

    ddb.create_table(
        TableName=os.environ["T_PUBLISH"], BillingMode="PAY_PER_REQUEST",
        AttributeDefinitions=[{"AttributeName": "publish_id", "AttributeType": "S"},
                              {"AttributeName": "event_id", "AttributeType": "S"},
                              {"AttributeName": "gold_run_id", "AttributeType": "S"}],
        KeySchema=[{"AttributeName": "publish_id", "KeyType": "HASH"}],
        GlobalSecondaryIndexes=[
            {"IndexName": "gsi_event_id",
             "KeySchema": [{"AttributeName": "event_id", "KeyType": "HASH"}],
             "Projection": {"ProjectionType": "ALL"}},
            {"IndexName": "gsi_gold_run",
             "KeySchema": [{"AttributeName": "gold_run_id", "KeyType": "HASH"}],
             "Projection": {"ProjectionType": "ALL"}}])

    ddb.create_table(
        TableName=os.environ["T_DELIVERY"], BillingMode="PAY_PER_REQUEST",
        AttributeDefinitions=[{"AttributeName": "domain_customer", "AttributeType": "S"},
                              {"AttributeName": "delivery_id", "AttributeType": "S"},
                              {"AttributeName": "gold_run_id", "AttributeType": "S"},
                              {"AttributeName": "customer_id", "AttributeType": "S"},
                              {"AttributeName": "event_id", "AttributeType": "S"}],
        KeySchema=[{"AttributeName": "domain_customer", "KeyType": "HASH"},
                   {"AttributeName": "delivery_id", "KeyType": "RANGE"}],
        GlobalSecondaryIndexes=[
            {"IndexName": "gsi_gold_run",
             "KeySchema": [{"AttributeName": "gold_run_id", "KeyType": "HASH"},
                            {"AttributeName": "customer_id", "KeyType": "RANGE"}],
             "Projection": {"ProjectionType": "ALL"}},
            {"IndexName": "gsi_event_id",
             "KeySchema": [{"AttributeName": "event_id", "KeyType": "HASH"}],
             "Projection": {"ProjectionType": "ALL"}}])

    ddb.create_table(
        TableName=os.environ["T_SUBSCRIPTIONS"], BillingMode="PAY_PER_REQUEST",
        AttributeDefinitions=[{"AttributeName": "customer_id", "AttributeType": "S"},
                              {"AttributeName": "domain_detail_type", "AttributeType": "S"},
                              {"AttributeName": "rule_arn", "AttributeType": "S"}],
        KeySchema=[{"AttributeName": "customer_id", "KeyType": "HASH"},
                   {"AttributeName": "domain_detail_type", "KeyType": "RANGE"}],
        GlobalSecondaryIndexes=[
            {"IndexName": "gsi_rule_arn",
             "KeySchema": [{"AttributeName": "rule_arn", "KeyType": "HASH"}],
             "Projection": {"ProjectionType": "ALL"}}])

    events.create_event_bus(Name=os.environ["BUS_NAME"])
    buffer = sqs.create_queue(QueueName=f"{PREFIX}-delivery-buffer",
                              Attributes={"VisibilityTimeout": "30"})["QueueUrl"]
    buffer_dlq = sqs.create_queue(QueueName=f"{PREFIX}-delivery-buffer-dlq")["QueueUrl"]
    buffer_arn = sqs.get_queue_attributes(QueueUrl=buffer, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]

    pmc_q = sqs.create_queue(QueueName=f"{PREFIX}-pmc-{DOMAIN}-product-created-v1")["QueueUrl"]
    pmc_arn = sqs.get_queue_attributes(QueueUrl=pmc_q, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]
    kinesis.create_stream(StreamName=f"{PREFIX}-pdz-{DOMAIN}-product-created-v1", ShardCount=1)
    pdz_arn = kinesis.describe_stream(
        StreamName=f"{PREFIX}-pdz-{DOMAIN}-product-created-v1")["StreamDescription"]["StreamARN"]

    subs = boto3.resource("dynamodb").Table(os.environ["T_SUBSCRIPTIONS"])
    rule_arns = {}
    for cust, ttype, tarn in (("pmc", "SQS", pmc_arn), ("pdz", "KINESIS", pdz_arn)):
        rule = f"{PREFIX}-on-{DOMAIN}-product-created-v1-{cust}"
        events.put_rule(Name=rule, EventBusName=os.environ["BUS_NAME"], State="ENABLED",
                        EventPattern=json.dumps({"detail-type": [DETAIL_TYPE]}))
        rule_arn = events.describe_rule(Name=rule, EventBusName=os.environ["BUS_NAME"])["Arn"]
        rule_arns[cust] = rule_arn
        # moto fidelity: hard-code the rule ARN (reserved var unsupported locally)
        events.put_targets(Rule=rule, EventBusName=os.environ["BUS_NAME"], Targets=[{
            "Id": f"buffer-{cust}", "Arn": buffer_arn,
            "InputTransformer": {
                "InputPathsMap": {"event": "$"},
                "InputTemplate": json.dumps({"rule_arn": rule_arn}).rstrip("}")
                                  + ', "event": <event>}',
            }}])
        subs.put_item(Item={
            "customer_id": cust, "domain_detail_type": f"{DOMAIN}#{DETAIL_TYPE}",
            "domain": DOMAIN, "detail_type": DETAIL_TYPE,
            "rule_arn": rule_arn, "status": "ACTIVE",
            "target": {"target_type": ttype, "target_arn": tarn,
                        "target_config": {"partition_key_path": "detail.data.ppid"}}})
    return {"buffer": buffer, "buffer_dlq": buffer_dlq, "buffer_arn": buffer_arn,
            "pmc_q": pmc_q, "rule_arns": rule_arns}


def receive_all(sqs, url, attrs=True, max_pages=50):
    """Drain visible messages. Received messages go in-flight (visibility 30),
    so the loop terminates; seen-id dedupe guards any visibility quirk."""
    msgs, seen = [], set()
    for _ in range(max_pages):
        page = sqs.receive_message(
            QueueUrl=url, MaxNumberOfMessages=10,
            AttributeNames=["All"] if attrs else []).get("Messages", [])
        fresh = [m for m in page if m["MessageId"] not in seen]
        if not fresh:
            return msgs
        seen.update(m["MessageId"] for m in fresh)
        msgs.extend(fresh)
    return msgs


def queue_count(sqs, url):
    a = sqs.get_queue_attributes(
        QueueUrl=url, AttributeNames=["ApproximateNumberOfMessages",
                                       "ApproximateNumberOfMessagesNotVisible"])["Attributes"]
    return int(a["ApproximateNumberOfMessages"]) + int(a["ApproximateNumberOfMessagesNotVisible"])


def as_esm_event(msgs, source_arn, receive_count="1"):
    return {"Records": [{
        "messageId": m["MessageId"], "receiptHandle": m["ReceiptHandle"],
        "body": m["Body"],
        "attributes": {"ApproximateReceiveCount":
                       m.get("Attributes", {}).get("ApproximateReceiveCount", receive_count)},
        "eventSourceARN": source_arn,
    } for m in msgs]}


def drain_buffer(fabric, sqs, res, expect_failures=False):
    """One ESM pass: receive everything, invoke the real handler, delete
    successes, leave failures in the queue. Returns (n_ok, failed_message_ids)."""
    msgs = receive_all(sqs, res["buffer"])
    if not msgs:
        return 0, []
    out = fabric.handler(as_esm_event(msgs, res["buffer_arn"]), None)
    failed_ids = {f["itemIdentifier"] for f in out["batchItemFailures"]}
    # moto quirk: change_message_visibility(0) is not honored, so the harness
    # owns retry simulation — delete EVERYTHING and hand failed bodies back.
    failed_records = [(m["MessageId"], m["Body"]) for m in msgs
                      if m["MessageId"] in failed_ids]
    for m in msgs:
        sqs.delete_message(QueueUrl=res["buffer"], ReceiptHandle=m["ReceiptHandle"])
    return len(msgs) - len(failed_ids), failed_records


def retry_records(handler_module, res, failed_records, receive_count):
    """Harness ESM-retry: re-invoke the real handler with the SAME records."""
    ev = {"Records": [{
        "messageId": mid, "receiptHandle": "rh", "body": body,
        "attributes": {"ApproximateReceiveCount": str(receive_count)},
        "eventSourceARN": res["buffer_arn"],
    } for mid, body in failed_records]}
    out = handler_module.handler(ev, None)
    failed_ids = {f["itemIdentifier"] for f in out["batchItemFailures"]}
    return ([(mid, body) for mid, body in failed_records if mid in failed_ids])


def rows_for(run_id):
    from boto3.dynamodb.conditions import Key
    t = boto3.resource("dynamodb").Table(os.environ["T_DELIVERY"])
    return t.query(IndexName="gsi_gold_run",
                   KeyConditionExpression=Key("gold_run_id").eq(run_id))["Items"]


with mock_aws():
    events = boto3.client("events")
    sqs = boto3.client("sqs")
    kinesis = boto3.client("kinesis")
    ddb = boto3.client("dynamodb")
    res = build_infra(events, sqs, kinesis, ddb)

    fabric = importlib.import_module("delivery_fabric")
    failed_writer = importlib.import_module("failed_writer")
    test_publisher = importlib.import_module("test_publisher")

    # ═══ S1 — clean run: 10 events, outcomes born DELIVERED with acks ═══
    print("\nS1 — clean run (10 events x pmc SQS + pdz Kinesis)")
    pub = test_publisher.handler({"gold_run_id": "fr-1", "events": 10}, None)
    check("publisher: 10 published, ledger written", pub["published"] == 10)
    n_ok, failed = drain_buffer(fabric, sqs, res)
    check("fabric: 20 deliveries in one pass, zero failures",
          n_ok == 20 and not failed)
    rows = rows_for("fr-1")
    acks = {r["customer_id"]: r.get("target_ack_kind") for r in rows}
    check("20 outcome rows, ALL born DELIVERED",
          len(rows) == 20 and all(r["status"] == "DELIVERED" for r in rows))
    check("every row carries a real target ack",
          all(r.get("target_ack") and r.get("confirmed_by") == "TARGET_API_RESPONSE"
              for r in rows)
          and acks.get("pmc") == "SQS_MESSAGE_ID"
          and acks.get("pdz") == "KINESIS_SEQUENCE_NUMBER")
    check("pmc consumer queue physically holds 10 envelopes",
          queue_count(sqs, res["pmc_q"]) == 10)
    sh_it = kinesis.get_shard_iterator(
        StreamName=f"{PREFIX}-pdz-{DOMAIN}-product-created-v1",
        ShardId="shardId-000000000000", ShardIteratorType="TRIM_HORIZON")["ShardIterator"]
    recs = kinesis.get_records(ShardIterator=sh_it)["Records"]
    check("pdz stream physically holds 10 records", len(recs) == 10,
          f"got {len(recs)}")
    check("row target_acks match the stream's sequence numbers",
          {r["target_ack"] for r in rows if r["customer_id"] == "pdz"}
          == {r["SequenceNumber"] for r in recs})

    # ═══ S2 — broken target: retries -> DLQ -> FAILED rows ═══
    print("\nS2 — pdz target broken (nonexistent stream)")
    subs_t = boto3.resource("dynamodb").Table(os.environ["T_SUBSCRIPTIONS"])
    subs_t.update_item(Key={"customer_id": "pdz",
                            "domain_detail_type": f"{DOMAIN}#{DETAIL_TYPE}"},
                       UpdateExpression="SET #t.target_arn = :bad",
                       ExpressionAttributeNames={"#t": "target"},
                       ExpressionAttributeValues={
                           ":bad": f"arn:aws:kinesis:eu-west-1:123456789012:stream/{PREFIX}-nonexistent"})
    pub = test_publisher.handler({"gold_run_id": "fr-2", "events": 2}, None)
    n_ok, failed = drain_buffer(fabric, sqs, res)
    check("first strike: pmc 2x delivered, pdz 2x failed",
          n_ok == 2 and len(failed) == 2)
    for strike in range(2, 6):                     # strikes 2..5 (maxReceiveCount)
        failed = retry_records(fabric, res, failed, strike)
    check("pdz kept failing through all 5 strikes", len(failed) == 2)
    # after 5 strikes SQS redrives to the buffer DLQ -> failed-writer
    out = failed_writer.handler({"Records": [{
        "messageId": mid, "receiptHandle": "rh", "body": body,
        "attributes": {"ApproximateReceiveCount": "5"},
        "eventSourceARN": res["buffer_arn"] + "-dlq",
    } for mid, body in failed]}, None)
    check("failed-writer recorded both, no retries", not out["batchItemFailures"])
    rows = rows_for("fr-2")
    by = {(r["customer_id"], r["status"]) for r in rows}
    check("fr-2 outcome: pmc 2x DELIVERED, pdz 2x FAILED (retries exhausted)",
          len(rows) == 4 and ("pmc", "DELIVERED") in by and ("pdz", "FAILED") in by
          and all(r["reason"] == "DELIVERY_RETRIES_EXHAUSTED"
                  for r in rows if r["status"] == "FAILED"))
    subs_t.update_item(Key={"customer_id": "pdz",
                            "domain_detail_type": f"{DOMAIN}#{DETAIL_TYPE}"},
                       UpdateExpression="SET #t.target_arn = :good",
                       ExpressionAttributeNames={"#t": "target"},
                       ExpressionAttributeValues={
                           ":good": kinesis.describe_stream(
                               StreamName=f"{PREFIX}-pdz-{DOMAIN}-product-created-v1")
                               ["StreamDescription"]["StreamARN"]})

    # ═══ S3 — redelivery of an already-delivered record (at-least-once) ═══
    print("\nS3 — duplicate redelivery")
    pub = test_publisher.handler({"gold_run_id": "fr-3", "events": 1}, None)
    drain_buffer(fabric, sqs, res)
    n_before = len(rows_for("fr-3"))
    # simulate ESM redelivery: same transformed message, sent again
    env = {"version": "0", "id": pub["events"][0]["event_id"],
           "detail-type": DETAIL_TYPE, "time": "2026-09-24T12:00:00Z",
           "detail": {"metadata": {"operation": "created"},
                       "data": {"ppid": pub["events"][0]["ppid"]}}}
    sqs.send_message(QueueUrl=res["buffer"], MessageBody=json.dumps(
        {"rule_arn": res["rule_arns"]["pmc"], "event": env}))
    n_ok, failed = drain_buffer(fabric, sqs, res)
    check("redelivery: send repeated (at-least-once), outcome write deduped",
          n_ok == 1 and not failed and len(rows_for("fr-3")) == n_before)

    # ═══ S4 — ledger missing entirely: degrade-write on the DLQ path ═══
    print("\nS4 — event with no ledger row (degrade-write)")
    ghost = {"version": "0", "id": "ghost-0001", "detail-type": DETAIL_TYPE,
             "time": "2026-09-24T12:30:00.000000Z",
             "detail": {"metadata": {"operation": "created"},
                         "data": {"ppid": "GHOST1"}}}
    body = json.dumps({"rule_arn": res["rule_arns"]["pmc"], "event": ghost})
    sqs.send_message(QueueUrl=res["buffer"], MessageBody=body)
    n_ok, failed = drain_buffer(fabric, sqs, res)
    check("fabric refuses to deliver without ledger (retry, not silent write)",
          n_ok == 0 and len(failed) == 1)
    failed_writer.handler({"Records": [{
        "messageId": mid, "receiptHandle": "rh", "body": body,
        "attributes": {"ApproximateReceiveCount": "5"},
        "eventSourceARN": res["buffer_arn"] + "-dlq",
    } for mid, body in failed]}, None)
    from boto3.dynamodb.conditions import Key as _K
    ghost_rows = boto3.resource("dynamodb").Table(os.environ["T_DELIVERY"]).query(
        IndexName="gsi_event_id",
        KeyConditionExpression=_K("event_id").eq("ghost-0001"))["Items"]
    check("degrade-write: FAILED row exists, keyed off envelope time, null lineage",
          len(ghost_rows) == 1 and ghost_rows[0]["status"] == "FAILED"
          and ghost_rows[0]["delivery_id"].startswith("2026-09-24T12:30:00")
          and ghost_rows[0].get("gold_run_id") is None)

    # ═══ S5 — non-ACTIVE subscription: no delivery, no silent loss ═══
    print("\nS5 — SUSPENDED subscription")
    subs_t.update_item(Key={"customer_id": "pmc",
                            "domain_detail_type": f"{DOMAIN}#{DETAIL_TYPE}"},
                       UpdateExpression="SET #s = :v",
                       ExpressionAttributeNames={"#s": "status"},
                       ExpressionAttributeValues={":v": "SUSPENDED"})
    pmc_count_before = queue_count(sqs, res["pmc_q"])
    test_publisher.handler({"gold_run_id": "fr-5", "events": 1}, None)
    n_ok, failed = drain_buffer(fabric, sqs, res)
    check("suspended: pdz delivered, pmc record retained for retry (parked, not lost)",
          n_ok == 1 and len(failed) == 1
          and queue_count(sqs, res["pmc_q"]) == pmc_count_before)
    subs_t.update_item(Key={"customer_id": "pmc",
                            "domain_detail_type": f"{DOMAIN}#{DETAIL_TYPE}"},
                       UpdateExpression="SET #s = :v",
                       ExpressionAttributeNames={"#s": "status"},
                       ExpressionAttributeValues={":v": "ACTIVE"})
    failed = retry_records(fabric, res, failed, 2)   # the parked record retries
    check("resume: the parked event delivers on the next pass",
          not failed and queue_count(sqs, res["pmc_q"]) == pmc_count_before + 1)

    n_pass = sum(1 for r in results if r[0] == PASS)
    print(f"\n{'='*60}\nRESULT: {n_pass}/{len(results)} checks passed")
    for status, name in results:
        if status == FAIL:
            print(f"  FAILED: {name}")
