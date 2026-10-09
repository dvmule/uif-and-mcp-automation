"""Local simulation for the production-faithful POC — moto drives the REAL handlers.

Run:
    pip install -r requirements.txt
    python run_poc_local.py

WHAT THIS CAN AND CANNOT COVER.

It imports the actual lambda_src modules, so handler logic is genuinely under
test. But moto does not implement EventBridge event-bus logging or vended log
delivery, so the EMISSION of a log record is not simulated: the suite writes
the records AWS would emit into a moto CloudWatch Logs group with
put_log_events, and the poller then reads them back with the same
FilterLogEvents call it makes in production. That validates windowing,
pagination, correlation, the watermark and row writing; it does NOT validate
that EventBridge emits what we think it emits. Only the live run settles that.

filterPattern is left EMPTY here (POLL_FILTER_PATTERN=""), because moto's
filter-pattern support is partial and a silently-unmatched pattern would make
the suite pass for the wrong reason. The poller re-checks message_type after
parsing regardless, so an empty pattern exercises the same code path — it just
reads more records than production would.

The tables are created with PRODUCTION projections (INCLUDE / KEYS_ONLY), which
is the point: S3 asserts that the lineage lookup still works when published_at
is NOT projected, and S4 asserts the projection itself, so the regression is
caught even where moto is more permissive than DynamoDB.

ENVELOPES COME OUT OF THE LEDGER, not from a helper that rebuilds them
(ledgered_envelope). Since 2026-10-01 the sort key's time component is the
envelope's own `time`, so a reconstructed envelope could agree with the
handlers while disagreeing with what was actually published — self-consistent
and wrong, which is the failure mode this suite exists to prevent.

The scenarios that matter most:

  S5     a log record becomes a DELIVERED row, keyed {envelope.time}#{event_id}
  S10    the DOMAIN DLQ (one per bus, as production) becomes a FAILED row
  S10b   both writers derive the SAME key for one delivery — the test the old
         {published_at}#{event_id} derivation could not pass
  S10c   regression guard: with published_at forced to a wrong value, the key
         still follows the envelope. S10b alone is not enough, because the two
         timestamps have been observed IDENTICAL on this machine
  S11    a success log cannot overwrite a FAILED row, and the conditional write
         now actually fires — under the old derivation it evaluated against an
         empty slot and never could
  S12    no ledger row still writes, correctly keyed, flagged lineage_missing
  S13-16 what the push design could not do at all: resume from a watermark,
         refuse to read into the ingestion-lag window, back-fill by re-reading
         a past window, and clamp a committing manual window to the lag floor
"""
from __future__ import annotations

import gzip
import json
import os
import sys
import time
from datetime import datetime, timezone

import boto3
from moto import mock_aws

REGION = "eu-west-1"
PREFIX = "uif-event-poc"
DOMAIN = "product"
DETAIL_TYPE = "product.created.v1"
BUS_LOG_GROUP = f"/aws/events/{PREFIX}-{DOMAIN}-bus"
# subscription_provisioner.py:164 — a subscription's channel is fixed by its
# target type, and every rule filters on it.
TARGET_TYPE_CHANNEL = {"SQS": "event", "KINESIS": "message", "API": "api"}
# UUIDs, as the provisioner generates them (the live values).
SUBSCRIPTION_IDS = {"pmc": "9d9bd9f9-63af-4ce8-9cea-d594dfb0eaa3",
                    "pdz": "6b1f4d2e-8c53-4a9b-b0e7-2f9d6c81a345"}
LOG_STREAM = "bus-log-stream-1"
POLL_LAG_SECONDS = 60

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "terraform", "lambda_src")
assert os.path.isfile(os.path.join(SRC, "delivery_poller.py")), f"no handlers at {SRC}"
sys.path.insert(0, SRC)

checks = failures = 0


def check(ok: bool, label: str) -> None:
    global checks, failures
    checks += 1
    if not ok:
        failures += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# ── infrastructure, with PRODUCTION projections ─────────────────────────────
def build_infra():
    ddb = boto3.resource("dynamodb", region_name=REGION)

    ddb.create_table(
        TableName=f"{PREFIX}-publish-metrics",
        BillingMode="PAY_PER_REQUEST",
        KeySchema=[{"AttributeName": "publish_id", "KeyType": "HASH"}],
        AttributeDefinitions=[
            {"AttributeName": "publish_id", "AttributeType": "S"},
            {"AttributeName": "event_id", "AttributeType": "S"},
            {"AttributeName": "recovery_scope", "AttributeType": "S"},
        ],
        GlobalSecondaryIndexes=[
            {"IndexName": "gsi_event_id",
             "KeySchema": [{"AttributeName": "event_id", "KeyType": "HASH"}],
             # EXACTLY production: published_at is absent on purpose.
             "Projection": {"ProjectionType": "INCLUDE",
                            "NonKeyAttributes": ["domain", "gold_run_id", "primary_key",
                                                 "version", "operation", "event_run_id"]}},
            {"IndexName": "gsi_recovery",
             "KeySchema": [{"AttributeName": "recovery_scope", "KeyType": "HASH"}],
             "Projection": {"ProjectionType": "INCLUDE",
                            "NonKeyAttributes": ["primary_key", "version", "operation",
                                                 "event_run_id", "attempt_count"]}},
        ],
    )

    ddb.create_table(
        TableName=f"{PREFIX}-delivery-metrics",
        BillingMode="PAY_PER_REQUEST",
        KeySchema=[{"AttributeName": "domain_customer", "KeyType": "HASH"},
                   {"AttributeName": "delivery_id", "KeyType": "RANGE"}],
        AttributeDefinitions=[
            {"AttributeName": "domain_customer", "AttributeType": "S"},
            {"AttributeName": "delivery_id", "AttributeType": "S"},
            {"AttributeName": "gold_run_id", "AttributeType": "S"},
            {"AttributeName": "customer_id", "AttributeType": "S"},
            {"AttributeName": "event_id", "AttributeType": "S"},
        ],
        GlobalSecondaryIndexes=[
            {"IndexName": "gsi_gold_run",
             "KeySchema": [{"AttributeName": "gold_run_id", "KeyType": "HASH"},
                           {"AttributeName": "customer_id", "KeyType": "RANGE"}],
             "Projection": {"ProjectionType": "INCLUDE",
                            "NonKeyAttributes": ["domain", "primary_key", "operation",
                                                 "detail_type", "status", "reason",
                                                 "failed_at", "redrive_count"]}},
            {"IndexName": "gsi_event_id",
             "KeySchema": [{"AttributeName": "event_id", "KeyType": "HASH"}],
             "Projection": {"ProjectionType": "KEYS_ONLY"}},
        ],
    )

    ddb.create_table(
        TableName=f"{PREFIX}-subscription-configs",
        BillingMode="PAY_PER_REQUEST",
        KeySchema=[{"AttributeName": "customer_id", "KeyType": "HASH"},
                   {"AttributeName": "domain_detail_type", "KeyType": "RANGE"}],
        AttributeDefinitions=[
            {"AttributeName": "customer_id", "AttributeType": "S"},
            {"AttributeName": "domain_detail_type", "AttributeType": "S"},
            {"AttributeName": "rule_arn", "AttributeType": "S"},
            {"AttributeName": "target_arn", "AttributeType": "S"},
        ],
        GlobalSecondaryIndexes=[
            {"IndexName": "gsi_rule_arn",
             "KeySchema": [{"AttributeName": "rule_arn", "KeyType": "HASH"}],
             "Projection": {"ProjectionType": "ALL"}},
            {"IndexName": "gsi_target_arn",
             "KeySchema": [{"AttributeName": "target_arn", "KeyType": "HASH"}],
             "Projection": {"ProjectionType": "ALL"}},
        ],
    )

    events = boto3.client("events", region_name=REGION)
    bus = f"{PREFIX}-{DOMAIN}-bus"
    events.create_event_bus(Name=bus)

    sqs = boto3.client("sqs", region_name=REGION)
    kinesis = boto3.client("kinesis", region_name=REGION)

    arns = {}
    for cust, kind in (("pmc", "SQS"), ("pdz", "KINESIS")):
        rule = f"{PREFIX}-on-{DOMAIN}-product-created-v1-{cust}"
        # detail-type AND detail.channel, as subscription_provisioner.py:313
        # builds it — the channel comes from the TARGET TYPE, so pmc (SQS) and
        # pdz (KINESIS) match DIFFERENT copies of one occurrence.
        events.put_rule(Name=rule, EventBusName=bus,
                        EventPattern=json.dumps({
                            "detail-type": [DETAIL_TYPE],
                            "detail": {"channel": [TARGET_TYPE_CHANNEL[kind]]}}))
        rule_arn = events.describe_rule(Name=rule, EventBusName=bus)["Arn"]
        if kind == "SQS":
            url = sqs.create_queue(QueueName=f"{PREFIX}-{cust}-x")["QueueUrl"]
            target_arn = sqs.get_queue_attributes(
                QueueUrl=url, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]
        else:
            kinesis.create_stream(StreamName=f"{PREFIX}-{cust}-x", ShardCount=1)
            target_arn = kinesis.describe_stream(
                StreamName=f"{PREFIX}-{cust}-x")["StreamDescription"]["StreamARN"]
        arns[cust] = {"rule_arn": rule_arn, "target_arn": target_arn,
                      "target_type": kind,
                      "channel": TARGET_TYPE_CHANNEL[kind]}

        # Field-for-field against the live pmc/pdz rows on
        # product#product.updated.v1 — see the note in dynamodb.tf.
        ddb.Table(f"{PREFIX}-subscription-configs").put_item(Item={
            "customer_id": cust,
            "domain_detail_type": f"{DOMAIN}#{DETAIL_TYPE}",
            "domain": DOMAIN, "detail_type": DETAIL_TYPE,
            "operation": DETAIL_TYPE.split(".")[-2],
            "version": DETAIL_TYPE.split(".")[-1],
            "subscription_id": SUBSCRIPTION_IDS[cust],
            "policy_sid": "uif" + SUBSCRIPTION_IDS[cust].replace("-", ""),
            "record_version": 1,
            "rule_name": rule, "rule_arn": rule_arn, "target_arn": target_arn,
            "status": "ACTIVE", "status_reason": None,
            "created_at": "2026-10-01T00:00:00Z",
            "created_by": "local-simulation",
            "updated_at": "2026-10-01T00:00:00Z",
            "updated_by": "subscription-provisioner",
            "deleted_at": None, "deleted_by": None,
            "target": {
                "target_type": kind,
                "target_arn": target_arn,
                "target_config": {
                    "consumer_principal_arn":
                        f"arn:aws:iam::123456789012:role/{cust}-event-consumer",
                    "retry_policy": {"max_attempts": 3, "max_age_seconds": 900},
                    **({"message_group_id": None, "dlq_arn": f"{target_arn}-dlq"}
                       if kind == "SQS"
                       else {"partition_key_path": "$.detail.partitionkey",
                             "dlq_arn": None}),
                },
            },
        })

    dlq_url = sqs.create_queue(QueueName=f"{PREFIX}-dlq")["QueueUrl"]

    # The poller's watermark table — POC machinery, not a replica of anything
    # in stacks/event.
    ddb.create_table(
        TableName=f"{PREFIX}-poller-state",
        BillingMode="PAY_PER_REQUEST",
        KeySchema=[{"AttributeName": "poller_id", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "poller_id", "AttributeType": "S"}],
    )

    # The bus log group the poller reads. In AWS this is filled by vended log
    # delivery; here the suite writes into it directly.
    logs = boto3.client("logs", region_name=REGION)
    logs.create_log_group(logGroupName=BUS_LOG_GROUP)
    logs.create_log_stream(logGroupName=BUS_LOG_GROUP, logStreamName=LOG_STREAM)

    return {"bus": bus, "arns": arns, "dlq_url": dlq_url, "logs": logs}


def envelope(event_id: str, ppid: str, gold_run_id: str) -> dict:
    return {
        "specversion": "1.0", "id": event_id, "source": "com.pearson.uif.poc",
        # Six-part reverse-DNS, as the publisher and the real processor emit:
        # production's _detail_type_from_envelope needs >= 6 parts starting
        # com.pearson to rebuild the bus DetailType, and returns "" otherwise.
        "type": f"com.pearson.{DOMAIN}.{DOMAIN}.created.v1", "time": now_iso(),
        "subject": f"{DOMAIN}/{ppid}", "correlationid": gold_run_id,
        "partitionkey": f"{ppid}|MDM", "channel": "message",
        "data": {"ppid": ppid, "gold_run_id": gold_run_id},
    }


def ledgered_envelope(row: dict) -> dict:
    """The envelope the publisher ACTUALLY published, out of the ledger.

    Not a reconstruction. publish-metrics.payload is the gzip'd envelope (the
    redrive source of truth), so this is byte-for-byte what EventBridge
    received and therefore what target_input carries back. A hand-built
    envelope would have its own `time`, and since `time` is now the sort key's
    component (outcome_row invariant 1), a reconstruction could agree with the
    handlers while disagreeing with reality — which is precisely the class of
    self-consistent-but-wrong test that hid the key bug until 2026-10-01.
    """
    raw = row["payload"]
    return json.loads(gzip.decompress(getattr(raw, "value", raw)))


class Ctx:
    """Minimal Lambda context — the poller checks its remaining budget."""

    def __init__(self, remaining_ms: int = 300_000):
        self._remaining = remaining_ms

    def get_remaining_time_in_millis(self) -> int:
        return self._remaining


_streams: set[str] = set()


def emit(logs, records: list[dict], ts_ms: int | None = None,
         stream: str = LOG_STREAM) -> int:
    """Write the records EventBridge would emit into the bus log group.

    Out-of-order timestamps go to their own stream: a single stream expects
    chronological batches, and FilterLogEvents reads across all streams in the
    group anyway — which incidentally is why the poller cannot checkpoint
    mid-window.
    """
    ts = ts_ms if ts_ms is not None else int(time.time() * 1000)
    if stream not in _streams and stream != LOG_STREAM:
        logs.create_log_stream(logGroupName=BUS_LOG_GROUP, logStreamName=stream)
        _streams.add(stream)
    logs.put_log_events(
        logGroupName=BUS_LOG_GROUP, logStreamName=stream,
        logEvents=[{"timestamp": ts, "message": json.dumps(r)} for r in records])
    return ts


def iso(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, timezone.utc).isoformat()


def poll_window(poller, start_ms: int, end_ms: int, commit: bool = False,
                remaining_ms: int = 300_000) -> dict:
    """A manual-window run: the catch-up path, and how most scenarios drive it."""
    return poller.handler({"start_iso": iso(start_ms), "end_iso": iso(end_ms),
                           "commit": commit}, Ctx(remaining_ms))


def invocation_success(env: dict, arns: dict, latency: int = 412) -> dict:
    return {
        "resource_arn": f"arn:aws:events:{REGION}:123456789012:event-bus/x",
        "event_id": "7f8d2c1a-4b61-4a2f-b8e0-1d9c3e5a9b3e",  # bus GUID, unused
        "message_type": "INVOCATION_SUCCESS",
        "log_level": "INFO",
        "details": {
            "rule_arn": arns["rule_arn"],
            "target_arn": arns["target_arn"],
            "target_input": json.dumps(env),  # include_detail = FULL
            "total_attempts": 1,
            "final_invocation_status": "SUCCESS",
            "ingestion_to_success_latency_ms": latency,
        },
    }


@mock_aws
def main() -> int:
    os.environ.update({
        "AWS_DEFAULT_REGION": REGION, "AWS_ACCESS_KEY_ID": "test",
        "AWS_SECRET_ACCESS_KEY": "test", "PREFIX": PREFIX, "DOMAIN": DOMAIN,
        "DETAIL_TYPE": DETAIL_TYPE,
        "T_PUBLISH": f"{PREFIX}-publish-metrics",
        "T_DELIVERY": f"{PREFIX}-delivery-metrics",
        "T_SUBSCRIPTIONS": f"{PREFIX}-subscription-configs",
        "BUS_NAME": f"{PREFIX}-{DOMAIN}-bus",
        # ── poller ──
        "BUS_LOG_GROUP": BUS_LOG_GROUP,
        "T_STATE": f"{PREFIX}-poller-state",
        "POLL_LAG_SECONDS": str(POLL_LAG_SECONDS),
        "POLL_OVERLAP_SECONDS": "5",
        "POLL_MAX_WINDOW_SECONDS": "900",
        # Smaller than MAX_WINDOW so a cold-start scheduled run covers the
        # whole lookback in one go. With the 3600s default it would be capped
        # to the OLDEST 900s and need four runs to reach the present — correct
        # backlog-draining behaviour, but not what S13/S14 are testing.
        "POLL_COLD_START_LOOKBACK_SECONDS": "600",
        # See the module docstring: moto's filter-pattern support is partial,
        # so match nothing and let the poller's own message_type check filter.
        "POLL_FILTER_PATTERN": "",
        # Exercises the thread pool without making moto's shared patching a
        # flakiness source.
        "POLL_CONCURRENCY": "4",
    })

    infra = build_infra()
    logs = infra["logs"]
    import outcome_row as core
    import delivery_poller
    import event_handler
    import publisher

    ddb = boto3.resource("dynamodb", region_name=REGION)
    t_pub = ddb.Table(f"{PREFIX}-publish-metrics")
    t_del = ddb.Table(f"{PREFIX}-delivery-metrics")

    # ── S0: subscription-configs ROW is a replica, not just the table ───────
    # The table definition was always verbatim; the seeded ROW was not — it
    # carried 10 of the 22 attributes a provisioned row actually has. The
    # expected set below is read off the LIVE pmc and pdz rows on
    # product#product.updated.v1 (2026-10-01), so this guard fails if either
    # side drifts again. It matters because the row is load-bearing now:
    # target.target_type is the channel source and
    # target_config.retry_policy.max_age_seconds bounds the settling window.
    print("-- S0 subscription-configs row matches the live attribute set")
    LIVE_ATTRS = {
        "customer_id", "domain_detail_type", "created_at", "created_by",
        "deleted_at", "deleted_by", "detail_type", "domain", "operation",
        "policy_sid", "record_version", "rule_arn", "rule_name", "status",
        "status_reason", "subscription_id", "target", "target_arn",
        "updated_at", "updated_by", "version",
    }
    LIVE_TARGET_CONFIG = {
        "SQS": {"consumer_principal_arn", "dlq_arn", "message_group_id",
                "retry_policy"},
        "KINESIS": {"consumer_principal_arn", "dlq_arn", "partition_key_path",
                    "retry_policy"},
    }
    t_subs = ddb.Table(f"{PREFIX}-subscription-configs")
    for cust in ("pmc", "pdz"):
        row = t_subs.get_item(Key={"customer_id": cust,
                                   "domain_detail_type":
                                       f"{DOMAIN}#{DETAIL_TYPE}"})["Item"]
        missing = LIVE_ATTRS - set(row)
        extra = set(row) - LIVE_ATTRS
        check(not missing, f"{cust}: no missing attributes ({missing or 'none'})")
        check(not extra, f"{cust}: no invented attributes ({extra or 'none'})")
        check(set(row["target"]) == {"target_type", "target_arn", "target_config"},
              f"{cust}: target map matches")
        tt = row["target"]["target_type"]
        check(set(row["target"]["target_config"]) == LIVE_TARGET_CONFIG[tt],
              f"{cust}: target_config matches the live {tt} shape")
        check(set(row["target"]["target_config"]["retry_policy"]) ==
              {"max_attempts", "max_age_seconds"},
              f"{cust}: retry_policy matches")
    check(all(len(t_subs.get_item(Key={"customer_id": c,
                                       "domain_detail_type":
                                           f"{DOMAIN}#{DETAIL_TYPE}"}
                                 )["Item"]["subscription_id"]) == 36
              for c in ("pmc", "pdz")),
          "subscription_id is a 36-char UUID, as the provisioner generates")

    # ── S1: publisher ledgers the ENVELOPE id, not a bus GUID ───────────────
    print("-- S1 publisher: ledger keys on the envelope id")
    out = publisher.handler({"gold_run_id": "gr-local-1", "count": 3,
                             "return_envelopes": True}, None)
    check(out["published"] == 3, f"published 3 ({out['published']})")
    rows = t_pub.scan()["Items"]
    check(len(rows) == 3, f"3 ledger rows ({len(rows)})")
    by_pk = {r["primary_key"]: r for r in rows}
    ev, ev1 = (by_pk["A1017000000000"]["event_id"],
               by_pk["A1017000000001"]["event_id"])
    # uuid4, exactly as the real processor generates it. The point of asserting
    # the SHAPE rather than a literal: in production the ledgered event_id and
    # the bus-assigned EventId are both uuid4 and indistinguishable by eye, so
    # a readable id here would hide the very confusion this POC guards against.
    import uuid as _uuid
    def _is_uuid4(s):
        try:
            return str(_uuid.UUID(s, version=4)) == s
        except (ValueError, AttributeError, TypeError):
            return False
    check(all(_is_uuid4(r["event_id"]) for r in rows),
          f"event_id is a uuid4 envelope id, as production ({ev})")
    check(all("published_at" in r for r in rows), "every row carries published_at")
    # The gzip'd envelope: the redrive source of truth, and the reason
    # lineage() must project its GetItem instead of pulling the whole row.
    check(all(r.get("payload") is not None for r in rows),
          "every row carries the gzip'd payload")
    check(all(int(r["payload_size_bytes"]) > 0 for r in rows),
          f"payload_size_bytes recorded ({by_pk['A1017000000000']['payload_size_bytes']})")
    check(all(r.get("attempt_count") is not None and r.get("last_updated_at")
              for r in rows), "attempt_count and last_updated_at present")
    check(all("recovery_scope" not in r for r in rows),
          "recovery_scope OMITTED on success, keeping gsi_recovery sparse")

    # correlationid must be a per-run uuid4, NOT the gold_run_id. If it ever
    # carries the gold run id again, `gold_run_id = envelope["correlationid"]`
    # starts looking correct — and in production that writes a uuid4 into
    # gold_run_id, which is the HASH KEY of gsi_gold_run.
    env_s1 = ledgered_envelope(rows[0])
    check(_is_uuid4(env_s1["correlationid"]),
          f"correlationid is a uuid4, as production ({env_s1['correlationid']})")
    check(env_s1["correlationid"] != rows[0]["gold_run_id"],
          "correlationid is NOT the gold_run_id")
    check("gold_run_id" not in env_s1.get("data", {}),
          "the envelope's data carries no run metadata, as production")

    # ── S1c: one ledger row per OCCURRENCE, payload = the message copy ──────
    # Production emits one bus entry per channel but writes ONE ledger row,
    # whose payload is the message-channel copy ("the fullest projection").
    # And the record is realistic in size now: the live product envelope is
    # 2,753 bytes against the two-field 647 this POC used to emit, which is
    # what made the include_detail = FULL volume estimates ~4x optimistic.
    print("-- S1c one ledger row per occurrence; realistic payload")
    check(out["bus_entries"] == out["occurrences"] * len(out["channels"]),
          f"{out['bus_entries']} bus entries for {out['occurrences']} "
          f"occurrences x {len(out['channels'])} channels")
    check(len(rows) == out["occurrences"],
          f"but only {len(rows)} ledger rows — one per occurrence")
    check(env_s1["channel"] == "event",
          "the ledgered payload is the published (eventing) copy")
    check(len(env_s1["data"]) == 37,
          f"data carries the full 37-field product record ({len(env_s1['data'])})")
    check(int(rows[0]["payload_size_bytes"]) > 2000,
          f"envelope is production-sized ({rows[0]['payload_size_bytes']} bytes "
          f"vs 2753 live)")

    # ── S1b: omitting the run ids yields jr_-shaped Glue run ids ────────────
    print("-- S1b publisher: generated run ids are jr_-shaped")
    gen = publisher.handler({"count": 1}, None)
    # Routing is detail-type only, so the eventing channel alone is published
    # and ONE bus entry reaches every matching subscription.
    check(gen["channels"] == ["event"],
          f"default publish_channels is [event] ({gen['channels']})")
    check(gen["bus_entries"] == gen["occurrences"],
          f"one bus entry per occurrence ({gen['bus_entries']})")
    import re as _re
    jr = _re.compile(r"^jr_[0-9a-f]{64}$")
    check(bool(jr.match(gen["gold_run_id"])),
          f"gold_run_id is jr_ + 64 hex ({gen['gold_run_id'][:14]}...)")
    check(bool(jr.match(gen["event_run_id"])),
          "event_run_id is jr_ + 64 hex")
    check(gen["gold_run_id"] != gen["event_run_id"],
          "gold_run_id and event_run_id are DIFFERENT ids, as production")

    # ── S2: re-run is idempotent ────────────────────────────────────────────
    print("-- S2 publisher: conditional put protects history")
    again = publisher.handler({"gold_run_id": "gr-local-1", "count": 3}, None)
    check(again["skipped_existing"] == 3, f"3 skipped ({again['skipped_existing']})")

    # ── S3: the regression that motivated this rebuild ──────────────────────
    print("-- S3 lineage: published_at is reachable though NOT projected")
    from boto3.dynamodb.conditions import Key
    idx = t_pub.query(IndexName="gsi_event_id",
                      KeyConditionExpression=Key("event_id").eq(ev))["Items"][0]
    check("publish_id" in idx, "index result carries the base key (the join hop)")
    row = core.lineage(ev)
    check(bool(row.get("published_at")),
          "lineage() resolves published_at via a PROJECTED GetItem")
    check("payload" not in row,
          "the projected GetItem does NOT drag back the gzip'd payload")

    # ── S4: assert the projection itself ────────────────────────────────────
    # moto may be more permissive than DynamoDB about INCLUDE, so assert the
    # DEFINITION too. If this ever passes while S3's mechanism is removed,
    # production would break and the suite would not notice.
    print("-- S4 projection guard")
    desc = boto3.client("dynamodb", region_name=REGION).describe_table(
        TableName=f"{PREFIX}-publish-metrics")["Table"]
    gsi = next(g for g in desc["GlobalSecondaryIndexes"]
               if g["IndexName"] == "gsi_event_id")
    nk = gsi["Projection"].get("NonKeyAttributes", [])
    check(gsi["Projection"]["ProjectionType"] == "INCLUDE", "gsi_event_id is INCLUDE")
    check("published_at" not in nk,
          "published_at is NOT projected — matching production")

    # ── S5: the poller turns a log record into a DELIVERED row ──────────────
    print("-- S5 delivery-poller: INVOCATION_SUCCESS -> DELIVERED")
    # ONE envelope per occurrence, on the eventing channel. Routing is
    # detail-type only, so this same envelope reaches pmc (SQS) AND pdz
    # (Kinesis) — two log records, two rows, one shared delivery_id.
    env0 = out["envelopes"]["A1017000000000"]["event"]
    t5 = emit(logs, [invocation_success(env0, infra["arns"]["pmc"])])
    res = poll_window(delivery_poller, t5 - 1000, t5 + 1000)
    check(res.get("rows_written") == 1, f"one row written ({res})")
    check(res.get("log_records_read") == 1, f"read 1 record from the log group ({res})")
    items = t_del.scan()["Items"]
    check(len(items) == 1, f"1 delivery row ({len(items)})")
    r = items[0]
    check(r["status"] == "DELIVERED", f"status DELIVERED ({r['status']})")
    check(r["confirmed_by"] == "EVENT_BUS_LOG", "confirmed_by EVENT_BUS_LOG")
    check(r["domain_customer"] == f"{DOMAIN}#pmc", f"pk resolved from rule_arn ({r['domain_customer']})")
    # THE CORRECTED DERIVATION (outcome_row invariant 1). The sk time component
    # is the ENVELOPE's time — what production's dlq_recorder keys on — NOT the
    # ledger's published_at, which is stamped later and differs.
    check(r["delivery_id"] == f"{env0['time']}#{ev}",
          "sk is {envelope.time}#{event_id}, as production keys it")
    check(r["event_time"] == env0["time"], "event_time records the sk component")
    check(r["published_at"] == row["published_at"],
          "published_at is still carried, as an enrichment attribute")
    # detail_type is the BUS DetailType, rebuilt by production's own parser
    # from the six-part CloudEvents type — NOT envelope["type"] itself.
    check(env0["type"] == f"com.pearson.{DOMAIN}.{DOMAIN}.created.v1",
          f"envelope type is the six-part reverse-DNS form ({env0['type']})")
    check(r["detail_type"] == f"{DOMAIN}.created.v1",
          f"detail_type is the BUS detail-type ({r['detail_type']})")
    check(r["detail_type"] != env0["type"],
          "and it is NOT the CloudEvents type")


    # ── S6: re-reading the same window is idempotent ────────────────────────
    # Not a nicety: overlap is deliberate (POLL_OVERLAP_SECONDS) and the
    # catch-up path re-reads windows on purpose, so this property is what makes
    # both safe.
    print("-- S6 re-read of the same window")
    first = t_del.scan()["Items"][0]
    res = poll_window(delivery_poller, t5 - 1000, t5 + 1000)
    check(res.get("rows_written") == 1, "the record is processed again")
    check(len(t_del.scan()["Items"]) == 1, "still 1 row — same key, same values")

    # A rewind must not RESTAMP the row either. delivered_at comes from the log
    # record's own timestamp, not now(), so the answer to "and when?" survives
    # any number of re-reads. ttl is the one field that legitimately moves.
    again = t_del.scan()["Items"][0]
    check(again["delivered_at"] == first["delivered_at"],
          f"delivered_at is unchanged by the re-read ({again['delivered_at']})")
    volatile = {"ttl"}
    drifted = {k for k in set(first) | set(again)
               if k not in volatile and first.get(k) != again.get(k)}
    check(not drifted, f"no field drifted on re-read ({drifted or 'none'})")

    # ── S7: per-CHANNEL fan-out — one event_id, two DIFFERENT sort keys ─────
    # The case the POC could not express until 2026-10-01. pmc is SQS so its
    # rule matches channel "event"; pdz is KINESIS so its rule matches
    # "message". One occurrence therefore produces two bus entries sharing an
    # event_id but each stamped with its own `time`, so the two delivery rows
    # have DIFFERENT delivery_ids. Any code assuming "one event_id -> one
    # delivery_id" passes a single-channel POC and is wrong in production.
    # ONE bus entry reaching BOTH subscribers, because routing is detail-type
    # only — the channel attribute is informational ("event" = eventing,
    # "message" = beacon), not a routing discriminator. So the two consumers
    # receive the SAME envelope: two rows, same event_id, same delivery_id,
    # distinguished by the partition key alone.
    print("-- S7 fan-out: one bus entry, both subscribers")
    check(env0["channel"] == "event",
          f"the eventing channel is published ({env0['channel']})")
    check(len(env0["data"]) == 37,
          f"and it carries the full product record ({len(env0['data'])} fields)")

    t7 = emit(logs, [invocation_success(env0, infra["arns"]["pdz"])])
    poll_window(delivery_poller, t7 - 1000, t7 + 1000)
    items = t_del.scan()["Items"]
    check(len(items) == 2, f"2 rows, one per customer ({len(items)})")
    check({i["domain_customer"] for i in items} == {f"{DOMAIN}#pmc", f"{DOMAIN}#pdz"},
          "pmc and pdz both present")
    check({i["event_id"] for i in items} == {ev},
          "both rows carry the SAME event_id")
    check(len({i["delivery_id"] for i in items}) == 1,
          "and the SAME delivery_id — one envelope, so one sort key")
    by_cust = {i["customer_id"]: i for i in items}
    check(all(by_cust[c]["delivery_id"] == f"{env0['time']}#{ev}"
              for c in ("pmc", "pdz")),
          "both keyed on that envelope's time")
    check(by_cust["pmc"]["target_arn"] != by_cust["pdz"]["target_arn"],
          "but each records its own target")

    # ── S7b: one event_id, two channel copies, DIFFERENT sort keys ──────────
    # The production shape, forced rather than hoped for. Two copies of one
    # occurrence — same id, different channel, different time — must produce
    # two rows at two different sort keys. Code that assumes "one event_id ->
    # one delivery_id" passes a single-channel POC and breaks here.
    print("-- S7b one event_id spanning two DIFFERENT sort keys")
    fan_id = "ev-twochan-0001"
    fan_event = envelope(fan_id, "A6666666666666", "gr-local-1")
    fan_event.update(channel="event", time="2026-10-01T10:00:00.000000Z")
    fan_msg = dict(fan_event, channel="message",
                   time="2026-10-01T10:00:02.500000Z")

    t7b = emit(logs, [invocation_success(fan_event, infra["arns"]["pmc"]),
                      invocation_success(fan_msg, infra["arns"]["pdz"])],
               int(time.time() * 1000) - 240_000,
               stream="bus-log-stream-s7b")
    poll_window(delivery_poller, t7b - 1000, t7b + 1000)

    fan_rows = [i for i in t_del.scan()["Items"] if i["event_id"] == fan_id]
    check(len(fan_rows) == 2, f"two rows for one event_id ({len(fan_rows)})")
    check(len({i["delivery_id"] for i in fan_rows}) == 2,
          "TWO distinct delivery_ids — one per channel copy")
    fan_by_cust = {i["customer_id"]: i for i in fan_rows}
    check(fan_by_cust["pmc"]["delivery_id"] ==
          f"2026-10-01T10:00:00.000000Z#{fan_id}",
          "pmc keyed on the event copy's time")
    check(fan_by_cust["pdz"]["delivery_id"] ==
          f"2026-10-01T10:00:02.500000Z#{fan_id}",
          "pdz keyed on the message copy's time")

    # ── S8: no target_input -> refuses to write a mis-keyed row ─────────────
    print("-- S8 include_detail=NONE / truncated target_input")
    rec = invocation_success(env0, infra["arns"]["pmc"])
    del rec["details"]["target_input"]
    before_s8 = len(t_del.scan()["Items"])
    t8 = emit(logs, [rec])
    res = poll_window(delivery_poller, t8 - 1, t8 + 1000)
    check(res.get("uncorrelatable") == 1,
          f"reported uncorrelatable rather than guessing ({res})")
    # A delta, not an absolute: scenarios that run before this one legitimately
    # add rows, and a hardcoded total makes the suite brittle to insertions.
    check(len(t_del.scan()["Items"]) == before_s8, "no extra row written")

    # ── S9: failure records are corroboration only ──────────────────────────
    print("-- S9 INVOCATION_FAILURE is not written by the poller")
    fail = invocation_success(env0, infra["arns"]["pmc"])
    fail["message_type"] = "INVOCATION_FAILURE"
    t9 = emit(logs, [fail])
    res = poll_window(delivery_poller, t9 - 1, t9 + 1000)
    check(res.get("noted_invocation_failure") == 1,
          f"noted, not written — the DLQ path owns FAILED ({res})")

    # ── S10: the DOMAIN DLQ -> FAILED, keyed from the envelope ──────────────
    print("-- S10 event-handler: domain DLQ -> FAILED at the envelope-time key")
    env1 = out["envelopes"]["A1017000000001"]["event"]   # pmc's copy
    sqs_record = {
        "messageId": "m1",
        "body": json.dumps(env1),
        "messageAttributes": {
            # TARGET_ARN is what production resolves the customer from
            # (delivery-metrics.md); RULE_ARN is the fallback.
            "TARGET_ARN": {"stringValue": infra["arns"]["pmc"]["target_arn"]},
            "RULE_ARN": {"stringValue": infra["arns"]["pmc"]["rule_arn"]},
            "ERROR_CODE": {"stringValue": "NO_RESOURCE"},
            "ERROR_MESSAGE": {"stringValue": "queue does not exist"},
            "EXHAUSTED_RETRY_CONDITION": {"stringValue": "MaximumRetryAttempts"},
        },
    }
    res = event_handler.handler({"Records": [sqs_record]}, None)
    check(res["batchItemFailures"] == [], f"no batch failures ({res})")
    key = {"domain_customer": f"{DOMAIN}#pmc",
           "delivery_id": f"{env1['time']}#{ev1}"}
    got = t_del.get_item(Key=key).get("Item")
    check(got is not None, "FAILED row is at the ENVELOPE-derived sort key")
    check(got and got["status"] == "FAILED", "status FAILED")
    check(got and got["exhausted_by"] == "MaximumRetryAttempts",
          "exhausted_by recorded, as production does")
    check(got and "failure_origin" not in got,
          "no failure_origin — the domain DLQ never carries consumer poison")
    # failed_at is the ENVELOPE time in production, not the failure moment, and
    # it is PROJECTED into gsi_gold_run — so writing now() into it (as this
    # handler did until 2026-10-01) silently redefines what the ops UI reads.
    check(got and got["failed_at"] == env1["time"],
          f"failed_at holds the envelope time, as production defines it "
          f"({got.get('failed_at')})")
    check(got and got["event_time"] == got["failed_at"],
          "event_time and failed_at agree — same fact, two names")

    # ── S10b: THE TEST THE OLD DERIVATION COULD NOT PASS ────────────────────
    # Both writers must land on ONE key for a single delivery. Under the old
    # {published_at}#{event_id} derivation the poller wrote 345ms away from the
    # recorder's row, so a delivery could read as DELIVERED *and* FAILED with
    # nothing to resolve it — and the conditional write in write_delivered
    # evaluated against an empty slot, so it could never arbitrate.
    print("-- S10b both writers derive the SAME key for one delivery")
    # NOT asserted here: that env1["time"] and published_at differ. They
    # usually do — 4.73 SECONDS apart on the live record decoded 2026-10-01 —
    # but the publisher stamps both within one clock tick under moto on
    # Windows, and they have been observed IDENTICAL. That made this check
    # intermittent, which is worse than absent. S10c forces the divergence and
    # asserts the derivation instead.
    poller_key = core.delivery_key("pmc", env1["time"], ev1)
    check(poller_key == key, "poller and event-handler agree on the key")
    check(len([i for i in t_del.scan()["Items"] if i["event_id"] == ev1]) == 1,
          "exactly ONE row exists for this delivery, not two")

    # ── S10c: REGRESSION GUARD for the 2026-10-01 key bug ───────────────────
    # S10b compares two real timestamps, and that alone is NOT a dependable
    # guard: the publisher stamps the envelope and writes published_at
    # microseconds apart, and they were observed IDENTICAL on this machine. A
    # test that only catches the bug when the clock happens to tick between two
    # statements is the same false comfort that let the bug survive. So force
    # the divergence and assert the derivation itself.
    # Backdated onto its own stream so this record's window stays disjoint from
    # the windows S11/S12/S15 read — total-row assertions elsewhere depend on
    # each scenario seeing only its own records.
    print("-- S10c the key follows the ENVELOPE even when the ledger disagrees")
    ev2 = by_pk["A1017000000002"]["event_id"]
    env2 = ledgered_envelope(by_pk["A1017000000002"])
    stale = "2020-01-01T00:00:00.000000Z"
    t_pub.update_item(
        Key={"publish_id": by_pk["A1017000000002"]["publish_id"]},
        UpdateExpression="SET published_at = :p",
        ExpressionAttributeValues={":p": stale})

    t10c = emit(logs, [invocation_success(env2, infra["arns"]["pmc"])],
                int(time.time() * 1000) - 120_000, stream="bus-log-stream-s10c")
    poll_window(delivery_poller, t10c - 1000, t10c + 1000)

    right = core.delivery_key("pmc", env2["time"], ev2)
    wrong = core.delivery_key("pmc", stale, ev2)
    check(t_del.get_item(Key=right).get("Item") is not None,
          "row is at {envelope.time}#{event_id}")
    check(t_del.get_item(Key=wrong).get("Item") is None,
          "and NOT at {published_at}#{event_id} — the old, wrong derivation")
    check(t_del.get_item(Key=right)["Item"]["published_at"] == stale,
          "published_at is still recorded as an attribute, just not as the key")

    # ── S11: evidence outranks the log ──────────────────────────────────────
    # Now a real test: the condition is evaluated against the row the recorder
    # actually wrote, so kept_failed is reachable.
    # ── S10d: DELIVERED then dead-lettered leaves NO success residue ────────
    # The reverse order of S11. The poller wrote ev/pmc as DELIVERED back in
    # S5; a dead letter for the same delivery now arrives. update_item MERGES,
    # so without an explicit REMOVE the row would keep confirmed_by,
    # delivered_at and the success latency while reading FAILED — asserting
    # both that it was delivered at a given time and that it failed.
    print("-- S10d DELIVERED -> dead letter: success attributes are removed")
    pmc_key = core.delivery_key("pmc", env0["time"], ev)
    was = t_del.get_item(Key=pmc_key)["Item"]
    check(was["status"] == "DELIVERED" and "delivered_at" in was,
          "the row starts out DELIVERED with a delivered_at")

    event_handler.handler({"Records": [{
        "messageId": "m-flip", "body": json.dumps(env0),
        "messageAttributes": {
            "TARGET_ARN": {"stringValue": infra["arns"]["pmc"]["target_arn"]},
            "RULE_ARN": {"stringValue": infra["arns"]["pmc"]["rule_arn"]},
            "ERROR_CODE": {"stringValue": "SQS_QUEUE_ACCESS_DENIED"}}}]}, None)

    now_row = t_del.get_item(Key=pmc_key)["Item"]
    check(now_row["status"] == "FAILED", f"now FAILED ({now_row['status']})")
    residue = {"confirmed_by", "delivered_at",
               "ingestion_to_success_latency_ms"} & set(now_row)
    check(not residue, f"no success residue left ({residue or 'none'})")
    check(now_row["reason"] == "SQS_QUEUE_ACCESS_DENIED", "reason recorded")
    check(now_row["event_time"] == env0["time"],
          "and it is the SAME row — key unchanged")

    print("-- S11 a later success log must NOT overwrite FAILED")
    t11 = emit(logs, [invocation_success(env1, infra["arns"]["pmc"])])
    res = poll_window(delivery_poller, t11 - 1, t11 + 1000)
    check(res.get("kept_failed") == 1,
          f"the conditional write FIRED and kept FAILED ({res})")
    got = t_del.get_item(Key=key)["Item"]
    check(got["status"] == "FAILED", f"still FAILED ({got['status']})")
    check(len([i for i in t_del.scan()["Items"] if i["event_id"] == ev1]) == 1,
          "still one row — no second row at a different time base")

    # ── S11b: ONE event failing for TWO consumers must give TWO rows ────────
    # UIFPE-453 asks for one outcome row PER SUBSCRIBED CONSUMER, and one
    # event_id spans every customer and every channel by design
    # (event_fabric_processor.py:338 — "same occurrence -> same CloudEvents id
    # on every channel").
    #
    # PRODUCTION CANNOT DO THIS TODAY. dlq_recorder.py:111 probes
    # gsi_event_id by event_id ALONE and returns early if any row exists, so
    # the first customer's failure suppresses every other customer's. The
    # probe is also redundant: the PutItem below it already carries
    # attribute_not_exists(domain_customer), which deduplicates a redelivery
    # correctly — per row, per customer. Removing the probe is the fix.
    #
    # This handler writes on the full key with no probe, hence two rows.
    print("-- S11b one event, two consumers, both fail -> TWO rows")
    shared = envelope("ev-fanout-0001", "A7777777777777", "gr-local-1")
    both = [
        {"messageId": f"m-{cust}", "body": json.dumps(shared),
         "messageAttributes": {
             "TARGET_ARN": {"stringValue": infra["arns"][cust]["target_arn"]},
             "RULE_ARN": {"stringValue": infra["arns"][cust]["rule_arn"]},
             "ERROR_CODE": {"stringValue": "NO_RESOURCE"}}}
        for cust in ("pmc", "pdz")
    ]
    res = event_handler.handler({"Records": both}, None)
    check(res["batchItemFailures"] == [], f"both records handled ({res})")
    fanout = [i for i in t_del.scan()["Items"]
              if i["event_id"] == "ev-fanout-0001"]
    check(len(fanout) == 2,
          f"TWO rows for one event_id — one per consumer ({len(fanout)})")
    check({i["domain_customer"] for i in fanout} ==
          {f"{DOMAIN}#pmc", f"{DOMAIN}#pdz"}, "one row each for pmc and pdz")
    check(len({i["delivery_id"] for i in fanout}) == 1,
          "same delivery_id — identical envelope, so identical sort key")

    # ── S12: no ledger row -> written anyway, correctly keyed and flagged ───
    # BEHAVIOUR CHANGE, and the point of invariant 2: the key comes from the
    # envelope, so the absence of a ledger row costs the enrichment columns and
    # nothing else. The previous build DROPPED the record here.
    print("-- S12 log record with no ledger row")
    ghost = envelope("ev-ghost-0001", "A9999999999999", "gr-ghost")
    t12 = emit(logs, [invocation_success(ghost, infra["arns"]["pmc"])])
    res = poll_window(delivery_poller, t12 - 1, t12 + 1000)
    check(res.get("rows_written_lineage_missing") == 1,
          f"written despite no ledger row ({res})")
    ghost_row = t_del.get_item(Key=core.delivery_key(
        "pmc", ghost["time"], "ev-ghost-0001")).get("Item")
    check(ghost_row is not None, "the row is at the correct envelope-time key")
    check(ghost_row and ghost_row["status"] == "DELIVERED",
          "status DELIVERED — the delivery genuinely was confirmed")
    check(ghost_row and ghost_row.get("lineage_missing") is True,
          "lineage_missing flags the absent enrichment")
    check(ghost_row and "gold_run_id" not in ghost_row,
          "no null GSI key attribute written")

    # ── S13: the watermark makes a scheduled run resume ─────────────────────
    print("-- S13 watermark: a scheduled run resumes where the last one stopped")
    t_state = ddb.Table(f"{PREFIX}-poller-state")
    check(t_state.get_item(Key={"poller_id": "bus-log-poll"}).get("Item") is None,
          "manual windows have NOT touched the live watermark")

    # Old enough to sit outside the lag window, so a scheduled run will take
    # it. Its own stream, because its timestamp precedes what LOG_STREAM holds.
    t13 = emit(logs, [invocation_success(env0, infra["arns"]["pdz"])],
               int(time.time() * 1000) - (POLL_LAG_SECONDS + 120) * 1000,
               stream="bus-log-stream-backdated")
    res = delivery_poller.handler({}, Ctx())
    check(res.get("complete") is True, f"scheduled run completed ({res.get('complete')})")
    check("watermark_committed" in res, f"watermark committed ({res.get('watermark_committed')})")
    wm = t_state.get_item(Key={"poller_id": "bus-log-poll"})["Item"]
    check(int(wm["last_ts_ms"]) > t13, "watermark is past the record it just read")

    second = delivery_poller.handler({}, Ctx())
    check(second.get("log_records_read", 0) == 0 or second.get("skipped"),
          f"the next run re-reads (almost) nothing — it resumed ({second})")

    # ── S14: the lag window is not read into ────────────────────────────────
    # The guard that stops a late-ingested record being skipped forever. A
    # record newer than now - POLL_LAG_SECONDS must be left for a later run.
    print("-- S14 ingestion-lag guard")
    fresh = envelope("ev-fresh-0001", "A8888888888888", "gr-local-1")
    emit(logs, [invocation_success(fresh, infra["arns"]["pmc"])])  # timestamp = now
    res = delivery_poller.handler({}, Ctx())
    check(res.get("log_records_read", 0) == 0 or res.get("skipped"),
          f"a record inside the lag window is NOT consumed ({res})")

    # ── S15: re-reading a past window BACK-FILLS lineage ────────────────────
    # The capability a subscription filter could not offer at any price, and it
    # is now a back-fill rather than a rescue: S12's ghost row already exists,
    # correctly keyed, flagged lineage_missing. Ledger the event, re-read the
    # same window, and the enrichment columns arrive ON THE SAME ROW — because
    # the key never depended on the ledger in the first place.
    print("-- S15 catch-up: re-read a past window, lineage is back-filled")
    ghost_key = core.delivery_key("pmc", ghost["time"], "ev-ghost-0001")

    # Count only THIS delivery's rows: the re-read window also catches S14's
    # in-lag record, so a whole-table count would move for an unrelated reason.
    def ghost_rows():
        return [i for i in t_del.scan()["Items"]
                if i["event_id"] == "ev-ghost-0001"]

    before = len(ghost_rows())
    t_pub.put_item(Item={
        "publish_id": f"{DOMAIN}#gr-ghost#A9999999999999#UPSERT#1",
        "event_id": "ev-ghost-0001", "gold_run_id": "gr-ghost",
        "event_run_id": "gr-ghost-er", "primary_key": "A9999999999999",
        "operation": "UPSERT", "status": "PUBLISHED",
        "published_at": now_iso(), "domain": DOMAIN,
    })
    res = poll_window(delivery_poller, t12 - 1, t12 + 1000)
    check(res.get("rows_written") == 1, f"re-read wrote with lineage this time ({res})")
    check(before == 1 and len(ghost_rows()) == 1,
          f"still exactly ONE row for this delivery — an update, not a second "
          f"row ({before} -> {len(ghost_rows())})")
    filled = t_del.get_item(Key=ghost_key)["Item"]
    check(filled.get("gold_run_id") == "gr-ghost", "gold_run_id back-filled")
    check(filled.get("primary_key") == "A9999999999999", "primary_key back-filled")
    check("lineage_missing" not in filled,
          "the lineage_missing flag is cleared by the replacing put")

    # ── S16: a COMMITTING manual window cannot skip past the lag floor ──────
    # The one way a manual run could lose records: commit a watermark past
    # timestamps whose log records had not been ingested when we looked, so the
    # next run starts after them and nothing asks again. Reading into the lag
    # zone is fine; moving the cursor over it is not.
    print("-- S16 a committing manual window is clamped to the lag floor")
    span_start = int(time.time() * 1000) - 600_000
    req_end = int(time.time() * 1000) + 60_000  # ahead of now, deliberately

    res = poll_window(delivery_poller, span_start, req_end, commit=True)
    actual_end = int(datetime.fromisoformat(res["window_end"]).timestamp() * 1000)
    check(actual_end <= req_end - POLL_LAG_SECONDS * 1000,
          f"end pulled back to the lag floor before committing ({res['window_end']})")
    check("warning" not in res, "clamped, so no incomplete-count warning needed")
    wm2 = int(t_state.get_item(Key={"poller_id": "bus-log-poll"})["Item"]["last_ts_ms"])
    check(wm2 <= req_end - POLL_LAG_SECONDS * 1000,
          "the committed watermark is behind the lag floor")

    # Read-only into the same zone is allowed, but flagged — an incomplete
    # count must not be mistaken for evidence of loss.
    res = poll_window(delivery_poller, span_start, req_end, commit=False)
    check("warning" in res, f"read-only run into the lag zone is flagged ({res.get('warning', '')[:40]}...)")
    check(int(datetime.fromisoformat(res["window_end"]).timestamp() * 1000) == req_end,
          "read-only run is NOT clamped — it reads what it was asked to")

    # ── S17: every record read lands in exactly one outcome bucket ──────────
    # The invariant behind the PollerUnaccountedRecords metric. A record that
    # is read and falls through every branch would be silently dropped, and a
    # missing row is indistinguishable from an undelivered event — so the
    # poller computes records - sum(outcomes) itself and reports any residue.
    # Tested on a window holding a DELIBERATE MIX, not just successes.
    print("-- S17 accounting: records == sum of outcome buckets")
    mix_base = int(time.time() * 1000) - 300_000
    good = envelope("ev-mix-0001", "A5555555555555", "gr-local-1")
    no_input = invocation_success(good, infra["arns"]["pmc"])
    del no_input["details"]["target_input"]          # -> uncorrelatable
    a_failure = invocation_success(good, infra["arns"]["pmc"])
    a_failure["message_type"] = "INVOCATION_FAILURE"  # -> noted, not written
    unknown = invocation_success(good, infra["arns"]["pmc"])
    unknown["message_type"] = "RULE_MATCH"            # -> ignored

    t17 = emit(logs, [invocation_success(good, infra["arns"]["pmc"]),
                      no_input, a_failure, unknown],
               mix_base, stream="bus-log-stream-s17")
    res = poll_window(delivery_poller, t17 - 1000, t17 + 1000)

    # `not isinstance(v, bool)` is load-bearing: bool subclasses int in Python,
    # so complete/backlog_capped/manual would otherwise count as buckets.
    buckets = {k: v for k, v in res.items()
               if isinstance(v, int) and not isinstance(v, bool) and k not in
               ("log_records_read", "window_seconds", "pages",
                "max_observed_ingest_lag_seconds")}
    check(res["log_records_read"] == 4, f"4 records read ({res['log_records_read']})")
    check(sum(buckets.values()) == res["log_records_read"],
          f"outcomes sum to records ({buckets} == {res['log_records_read']})")
    check("unaccounted" not in res,
          f"nothing unaccounted ({res.get('unaccounted', 0)})")
    check(res.get("rows_written_lineage_missing") == 1 and
          res.get("uncorrelatable") == 1 and
          res.get("noted_invocation_failure") == 1 and
          res.get("ignored") == 1,
          f"one of each outcome, as constructed ({buckets})")

    # ── S17b: every run leaves a durable row, committed or not ──────────────
    # The cursor's `last_run` only survives a COMMITTED run, so a run that
    # timed out or was skipped used to leave nothing durable — exactly the run
    # worth inspecting later. Run rows live in the SAME table under a `run#`
    # prefix (no second table, the key schema is poller_id alone) and carry a
    # ttl, while the cursor deliberately has none so it never expires.
    print("-- S17b run history: one row per run, including non-committing ones")
    all_items = t_state.scan()["Items"]
    run_rows = [i for i in all_items
                if str(i["poller_id"]).startswith("run#")]
    cursor = [i for i in all_items if i["poller_id"] == "bus-log-poll"]
    check(len(run_rows) > 1, f"multiple run rows recorded ({len(run_rows)})")
    check(len(cursor) == 1, "exactly one cursor item")
    check("ttl" not in cursor[0],
          "the CURSOR carries no ttl, so DynamoDB never expires it")
    check(all("ttl" in r for r in run_rows), "every run row carries a ttl")
    check(any(r["committed"] is False for r in run_rows),
          "non-committing runs are recorded too — the gap this closes")
    sample_run = next(r for r in run_rows if r["committed"] is True)
    check({"started_at", "finished_at", "committed", "report"}
          <= set(sample_run), f"run row shape ({sorted(sample_run)})")
    check("log_records_read" in sample_run["report"] or
          "skipped" in sample_run["report"],
          f"the report is embedded ({sorted(sample_run['report'])})")

    # ── S18: a backlog must DRAIN, not just advance ─────────────────────────
    # Each run covers (max_window - overlap) of new ground while the schedule
    # interval of wall clock passes. So catch-up capacity is
    # (max_window - overlap - rate) per run, and with max_window == rate it is
    # NEGATIVE — the poller loses `overlap` every cycle and stays behind
    # forever. That was the shipped default (900s max against rate(15 minutes))
    # until 2026-10-01.
    #
    # Here: rewind the watermark an hour, then run repeatedly and require the
    # gap to the lag floor to SHRINK monotonically and reach zero.
    print("-- S18 backlog drains to the lag floor")
    os.environ["POLL_MAX_WINDOW_SECONDS"] = "1800"   # > the 900s 'interval'
    import importlib
    importlib.reload(delivery_poller)

    now_ms = int(time.time() * 1000)
    behind_ms = now_ms - 3_600_000
    t_state.put_item(Item={"poller_id": "bus-log-poll",
                           "last_ts_ms": behind_ms,
                           "last_ts_iso": iso(behind_ms),
                           "updated_at": now_iso()})

    gaps = []
    for _ in range(4):
        r = delivery_poller.handler({}, Ctx())
        if r.get("skipped"):
            gaps.append(0)
            break
        wm = int(t_state.get_item(
            Key={"poller_id": "bus-log-poll"})["Item"]["last_ts_ms"])
        lag_floor = int(time.time() * 1000) - POLL_LAG_SECONDS * 1000
        gaps.append(max(0, lag_floor - wm))

    # Monotonically non-increasing, and caught up to within a few seconds. NOT
    # exactly zero: the watermark commits at `end`, and wall clock has moved on
    # by the time the gap is measured, so a few ms always remain. Asserting 0
    # would be asserting that time stopped.
    CAUGHT_UP_MS = 5_000
    # Monotonicity is only meaningful WHILE BEHIND. Once caught up the gap
    # jitters by single-digit ms with the wall clock (6ms then 11ms, observed),
    # so requiring it to never grow at all makes the test timing-dependent —
    # the fourth such assertion I wrote today and the fourth to flap.
    while_behind = [(gaps[i], gaps[i + 1]) for i in range(len(gaps) - 1)
                    if gaps[i] > CAUGHT_UP_MS]
    check(all(b < a for a, b in while_behind),
          f"the gap shrinks on every run that starts behind "
          f"({[f'{a}->{b}ms' for a, b in while_behind]})")
    check(gaps[-1] < CAUGHT_UP_MS,
          f"and catches up to the lag floor ({gaps[-1]}ms behind, "
          f"from {gaps[0]}ms)")
    check(gaps[0] > 1_000_000 and gaps[1] < 100_000,
          f"most of a 1-hour backlog clears in ONE run "
          f"({gaps[0]}ms -> {gaps[1]}ms)")

    os.environ["POLL_MAX_WINDOW_SECONDS"] = "900"
    importlib.reload(delivery_poller)

    print(f"\n{'=' * 62}\n{checks - failures}/{checks} checks passed"
          + ("" if not failures else f"   ({failures} FAILED)"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
