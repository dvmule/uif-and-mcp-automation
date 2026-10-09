"""Shared key derivation, lineage lookup and Table 4 row writing.

Both writers import this. That is deliberate: in the previous POC the audit
consumer and the event handler each derived the sort key independently, so a
change to one silently diverged from the other. One module, one derivation.

THREE INVARIANTS, all learned the hard way:

  1. SORT-KEY PURITY: delivery_id is ALWAYS {envelope["time"]}#{event_id} —
     the CloudEvents envelope's OWN time, never the ledger's published_at and
     never now().

     THIS IS THE REVERSE OF WHAT THIS FILE SAID UNTIL 2026-10-01, and the
     earlier version was wrong. Production's dlq_recorder.py:139 does:

         failed_at = str(detail.get("time") or body.get("time") or _utcnow())
         "delivery_id": {"S": f"{failed_at}#{event_id}"}

     — and stacks/event/docs/delivery-metrics.md defines that field as "Event
     time (body["time"]) — when it was published", despite the name. So
     production keys on the envelope time. published_at is a DIFFERENT value:
     the envelope is stamped at construction
     (event_fabric_processor.py:1291) and published_at is written after
     PutEvents returns, hundreds of milliseconds later.

     Keying DELIVERED rows on published_at while production keys FAILED rows
     on the envelope time puts one delivery on two sort keys, so the
     conditional write below arbitrates nothing — it evaluates against an
     empty slot and always passes. One delivery would read as both DELIVERED
     and FAILED, with nothing in the table to resolve it.

  2. THE KEY NEEDS NO LEDGER READ. The envelope carries `time` and `id`, and
     it reaches both writers: the DLQ body IS the envelope, and the bus log's
     details.target_input IS the envelope. So a record can always be written
     at the correct key, even when its ledger row is missing. lineage() is
     ENRICHMENT ONLY and must never block a write — see write_delivered.

  3. published_at CANNOT BE READ OFF gsi_event_id. Production projects that
     index INCLUDE without published_at (see dynamodb.tf), so the index query
     gives us the base-table key and nothing more useful. lineage() therefore
     queries the index, then GetItems the base row. Reading it off the index
     works against a projection of ALL and returns None against production —
     the exact class of bug this POC exists to stop. This still matters for
     the enrichment fields; it is no longer load-bearing for the key.

NAMING, and why the obvious fix is the wrong one: the sort key's time
component is stored as `event_time`. Production stores the same fact as
`failed_at`, which is already a misnomer — it holds the publish time, not a
failure time — and reads worse still once DELIVERED rows share the table.

RENAMING IT IN PRODUCTION IS EXPENSIVE, which is easy to miss: `failed_at` is
one of gsi_gold_run's non_key_attributes, and DynamoDB does not allow a GSI's
projection to be modified. Renaming means recreating the index and backfilling
it on a live table. So the FAILED path writes BOTH — `event_time` (honest, and
what the key is derived from) and `failed_at` (unchanged, so the existing
projection and the ops UI keep working). The DELIVERED path writes only
`event_time`: `failed_at` on a successful delivery would be nonsense, and
nothing needs it there, because a GSI query always returns the base table keys
and delivery_id already carries the value.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.conditions import Key
from botocore.config import Config
from botocore.exceptions import ClientError

DOMAIN = os.environ["DOMAIN"]

# The default urllib3 pool is 10 connections, which throttles the poller's
# thread pool into serial behaviour without erroring — it just goes slow.
# Adaptive retries are for DynamoDB throttling under a burst: the poller writes
# a whole 15-minute window at once, so it is a far spikier client than the
# per-record push writer was.
_ddb = boto3.resource("dynamodb", config=Config(
    retries={"max_attempts": 10, "mode": "adaptive"},
    max_pool_connections=int(os.environ.get("DDB_POOL", "32"))))

t_publish = _ddb.Table(os.environ["T_PUBLISH"])
t_delivery = _ddb.Table(os.environ["T_DELIVERY"])
t_subs = _ddb.Table(os.environ["T_SUBSCRIPTIONS"])


def table(name: str):
    """A Table handle on the shared, pool-tuned resource."""
    return _ddb.Table(name)


TTL_DAYS = 90


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def ttl_epoch(days: int = TTL_DAYS) -> int:
    return int(datetime.now(timezone.utc).timestamp()) + days * 86400


# The base row carries `payload` — the gzip'd envelope that is the redrive
# source of truth — which is ~2.7 KB on a real product event and is excluded
# from every GSI projection by design. An unprojected GetItem would therefore
# drag that blob back on EVERY lookup just to read published_at: one per
# invocation record, so two per event per consumer at run scale. Ask for the
# five fields the writers actually use instead.
# `operation`, `version` and `status` are DynamoDB reserved words, hence the
# aliases.
_LINEAGE_PROJECTION = "published_at, gold_run_id, event_run_id, primary_key, #op"
_LINEAGE_NAMES = {"#op": "operation"}


def lineage(event_id: str) -> dict:
    """Ledger row for an event, or {} if it is not ledgered yet.

    Two hops by necessity — see invariant 2 in the module docstring.
    """
    hits = t_publish.query(
        IndexName="gsi_event_id",
        KeyConditionExpression=Key("event_id").eq(event_id),
    ).get("Items") or []
    if not hits:
        return {}
    # The index projection carries the base table key, always. Use it.
    publish_id = hits[0]["publish_id"]
    return t_publish.get_item(
        Key={"publish_id": publish_id},
        ProjectionExpression=_LINEAGE_PROJECTION,
        ExpressionAttributeNames=_LINEAGE_NAMES,
    ).get("Item") or {}


# ── Subscription lookup, cached for the life of the container ───────────────
# There are as many distinct rule ARNs as there are subscriptions — two here,
# tens in production — so under the poller this query was being repeated
# thousands of times per invocation for a near-static table. It is the cheapest
# of the three reads per record to remove.
#
# NEGATIVE RESULTS ARE NOT CACHED, deliberately. Caching a miss would hide a
# newly-created subscription for the whole TTL, and on the event_handler path a
# miss is what triggers the retry that lets a real failure be recorded. A hit
# cannot go stale in a way that matters: customer_id and target ARN are fixed
# for the life of a subscription, and a DELETED one stops producing records.
_SUB_CACHE_TTL = int(os.environ.get("SUB_CACHE_TTL_SECONDS", "300"))
_sub_cache: dict[str, tuple[float, dict]] = {}


def _cached_sub(cache_key: str, fetch) -> dict | None:
    hit = _sub_cache.get(cache_key)
    if hit is not None and (time.monotonic() - hit[0]) < _SUB_CACHE_TTL:
        return hit[1]
    row = fetch()
    if row is not None:
        _sub_cache[cache_key] = (time.monotonic(), row)
    return row


def customer_from_rule_arn(rule_arn: str) -> dict | None:
    def fetch():
        rows = t_subs.query(
            IndexName="gsi_rule_arn",
            KeyConditionExpression=Key("rule_arn").eq(rule_arn),
        ).get("Items") or []
        return rows[0] if rows else None

    return _cached_sub(f"rule#{rule_arn}", fetch)


def customer_from_target_arn(target_arn: str) -> dict | None:
    """Fallback for a dead letter that carries no rule ARN."""
    def fetch():
        rows = t_subs.query(
            IndexName="gsi_target_arn",
            KeyConditionExpression=Key("target_arn").eq(target_arn),
        ).get("Items") or []
        return rows[0] if rows else None

    return _cached_sub(f"target#{target_arn}", fetch)


def detail_type_from_envelope(env: dict) -> str:
    """Reconstruct the bus DetailType from the CloudEvents type.

    VERBATIM from dlq_recorder.py:290 —
      com.pearson.{domain}.{entity}.{operation}.{version}
        -> {domain}.{operation}.{version}        (the entity is dropped)

    This is NOT the same string as envelope["type"], and the difference
    matters: delivery-metrics.md defines detail_type as "EventBridge
    detail-type of the failed event, e.g. product.updated.v2" and says it is
    "used to rebuild the envelope on redrive". Writing the CloudEvents type
    there instead — which this POC did until 2026-10-01 — puts a value in the
    column that redrive cannot publish with.
    """
    parts = str(env.get("type", "")).split(".")
    if len(parts) >= 6 and parts[:2] == ["com", "pearson"]:
        return f"{parts[2]}.{parts[-2]}.{parts[-1]}"
    return ""


def unwrap(body: dict) -> tuple[dict, str]:
    """(envelope, detail_type) from either message shape.

    Mirrors dlq_recorder.py:98 exactly. A payload reaches us either BARE (the
    CloudEvents envelope on its own — what InputPath = $.detail produces, so
    what both the DLQ body and the bus log's target_input normally hold) or
    WRAPPED in EventBridge's own event. Production branches on "specversion"
    rather than guessing, and takes detail-type from the wrapper when it has
    one, because that is authoritative where the reconstruction is inference.
    """
    if "specversion" in body:
        return body, detail_type_from_envelope(body)

    detail = body.get("detail") or {}
    if isinstance(detail, str):
        detail = json.loads(detail)
    return detail, str(body.get("detail-type") or "")


def envelope_time(body: dict) -> str | None:
    """The sort key's time component. See invariant 1."""
    env, _ = unwrap(body)
    return str(env["time"]) if env.get("time") else None


def delivery_key(customer_id: str, event_time: str, event_id: str) -> dict:
    """The ONE derivation. See invariant 1: event_time is the ENVELOPE's time."""
    return {
        "domain_customer": f"{DOMAIN}#{customer_id}",
        "delivery_id": f"{event_time}#{event_id}",
    }


def write_delivered(*, customer_id: str, event_id: str, event_time: str,
                    row: dict, detail_type: str, target_arn: str,
                    latency_ms: int | None, total_attempts: int | None,
                    delivered_at: str | None = None) -> str:
    """Write an observed DELIVERED row.

    Conditional on the row not already being FAILED: a dead letter is durable
    evidence of failure, whereas a log record is best-effort, so evidence
    outranks the log. In principle the two are mutually exclusive — a delivery
    that eventually succeeded produces INVOCATION_SUCCESS and no dead letter —
    but the ordering is not guaranteed, and since invariant 1 now puts both
    writers on the SAME key this condition actually arbitrates. Under the old
    derivation it evaluated against an empty slot and could never fire.

    `row` is enrichment and may be {} — the key comes from the envelope, so a
    record whose ledger row is missing is still written, correctly keyed, with
    lineage_missing flagged. Absent lineage is visible; a mis-keyed row is not.
    """
    item = {
        **delivery_key(customer_id, event_time, event_id),
        "customer_id": customer_id,
        "domain": DOMAIN,
        "event_id": event_id,
        "event_time": event_time,
        "detail_type": detail_type,
        "status": "DELIVERED",
        "confirmed_by": "EVENT_BUS_LOG",
        "target_arn": target_arn,
        "gold_run_id": row.get("gold_run_id"),
        "event_run_id": row.get("event_run_id"),
        "primary_key": row.get("primary_key"),
        "operation": row.get("operation"),
        "published_at": row.get("published_at"),
        # WHEN THE DELIVERY HAPPENED, not when this row was written. The caller
        # passes the log record's own timestamp, which is fixed for that
        # record, so re-reading a window (a rewind, an overlap, a catch-up run)
        # rewrites an IDENTICAL row rather than restamping it with the time of
        # the re-read. UIFPE-453 asks "did customer Y receive event X, and
        # when?", so a field that moved every time we looked would answer the
        # second half wrongly.
        # now_iso() only as a fallback, for a caller with no record timestamp.
        "delivered_at": delivered_at or now_iso(),
        "ttl": ttl_epoch(),
    }
    if latency_ms is not None:
        item["ingestion_to_success_latency_ms"] = latency_ms
    if total_attempts is not None:
        item["total_attempts"] = total_attempts
    if not row:
        # The row is correctly keyed and the delivery is genuinely confirmed —
        # only the lineage columns are absent. Flagging beats dropping the
        # record (what the old no_ledger path did) and beats keying it on
        # something we can rederive differently later.
        item["lineage_missing"] = True

    try:
        t_delivery.put_item(
            Item={k: v for k, v in item.items() if v is not None},
            ConditionExpression="attribute_not_exists(domain_customer) OR #st <> :failed",
            ExpressionAttributeNames={"#st": "status"},
            ExpressionAttributeValues={":failed": "FAILED"},
        )
        return "rows_written"
    except ClientError as e:
        if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
        return "kept_failed"
