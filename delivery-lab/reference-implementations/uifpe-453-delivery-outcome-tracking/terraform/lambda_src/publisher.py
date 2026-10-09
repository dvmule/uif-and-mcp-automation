"""Test publisher — mimics the PUBLISH CONTRACT of the real Glue processor.

Reproduces, from stacks/event/src/glue/event_fabric_processor.py:
  * the CloudEvents 1.0 envelope shape built by _build_cloud_event
  * PutEvents in batches of 10, with per-entry accept/reject handling
  * the Table 3 ledger row, keyed publish_id, with
        event_id = THE ENVELOPE ID
    which is the single most important detail here. The real processor sets
    rec["event_id"] = rec["envelope_id"] and throws the bus-assigned EventId
    away, because rule targets deliver with InputPath = $.detail, so a dead
    letter carries only the envelope and the envelope id is the one value the
    failure path can correlate on. Both previous POCs ledgered the bus
    EventId instead, which made their results untransferable to production.
  * the conditional put, so re-running a gold_run_id cannot overwrite history

  * PER-CHANNEL FAN-OUT, added 2026-10-01. One occurrence becomes one bus
    entry per channel in CHANNELS, all sharing ONE event_id
    (event_fabric_processor.py:338 — "same occurrence -> same CloudEvents id on
    every channel, so cross-channel consumers can correlate and dedupe") but
    each with its OWN `time` and its own field projection. Still ONE ledger
    row per occurrence, whose payload is the `message` copy — "the
    message-channel projection is the fullest - keep it as the stored replay
    payload" — and whose status is PUBLISHED only when EVERY channel entry was
    accepted.

    This matters because subscription rules match detail-type AND
    detail.channel, with the channel derived from target type
    ({"SQS": "event", "KINESIS": "message", "API": "api"}). So an SQS
    subscriber and a Kinesis subscriber on the same detail-type receive
    DIFFERENT copies, with different `time` values, hence DIFFERENT sort keys
    for the same event_id. Until this landed the POC emitted a single copy and
    both consumers shared one delivery_id — which made the wrong
    `expected = events x subscribers` arithmetic look correct.

Deliberately NOT reproduced (upstream of the delivery question): the Athena
delta read, the run registry, per-channel hash comparison (every requested
channel emits here, rather than only those whose hash moved), and run-level
metrics.

ORDERING matches production: publish first, ledger second. That leaves a small
window in which a bus log record exists before its ledger row. The
delivery-poller closes it structurally rather than by retrying: its read window
trails live by POLL_LAG_SECONDS (10 minutes by default), by which point the
ledger row is long written. A record that still cannot be correlated is
reported and left, and re-reading that window once the ledger catches up
recovers it.
"""
from __future__ import annotations

import gzip
import json
import os
import secrets
import uuid
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

import outcome_row as core

DOMAIN = os.environ["DOMAIN"]
DETAIL_TYPE = os.environ["DETAIL_TYPE"]
BUS = os.environ["BUS_NAME"]
SOURCE = "com.pearson.uif.poc"
PUBLISH_VERSION = "v1"

events = boto3.client("events")

# event_fabric_processor.py:70. The channel a subscription receives is fixed by
# its target type — subscription_provisioner.py:164
# TARGET_TYPE_CHANNEL = {"SQS": "event", "KINESIS": "message", "API": "api"}.
CHANNELS = ("event", "message", "api")

# The copy kept as the ledger's replay payload: the fullest projection.
PAYLOAD_CHANNEL = "message"

# ── A realistic product record ──────────────────────────────────────────────
# Field names are the 37 the live product envelope carries (decoded from a real
# publish-metrics payload, 2026-09-28); values are synthetic. The POC carried a
# two-field `data` until 2026-10-01, which made every envelope ~647 bytes
# against production's ~2,750 — so with include_detail = FULL the bus log
# volume and the 1 MB per-record cap (where target_input is the FIRST field
# EventBridge drops, and it is the field this whole design joins on) were both
# understated by roughly 4x.
_PRODUCT_RECORD = {
    "productId": None,                       # filled per record
    "parentProductId": "A103000180000",
    "isbn10": "0134685997",
    "isbn13": "9780134685991",
    "itemType": "Digital Product",
    "inventoryItemStatus": "Active",
    "editionNumber": "7",
    "copyrightYear": "2026",
    "finalTextPageCount": "1248",
    "deliveryMethod": "Online",
    "enablingTechnology": "MyLab",
    "lineOfBusiness": "Higher Education",
    "organizationCode": "09B",
    "owningSubsidiaryName": "Pearson Education Inc",
    "owningSubsidiaryCountryCode": "US",
    "ipOwnerCategory": "Wholly Owned",
    "sourceItemNumber": "ITM-0094412",
    "webConsumingSystem": "Pearson Store",
    "disciplineLevel1": "Mathematics",
    "disciplineLevel2": "Calculus",
    "disciplineLevel3": "Single Variable Calculus",
    "disciplineLevel4": "Limits and Continuity",
    "productClassificationLevel1": "Courseware",
    "productClassificationLevel2": "Digital Courseware",
    "learnerStageLevel1": "Higher Education",
    "learnerStageLevel2": "Undergraduate",
    "languageLevel1": "English",
    "languageLevel2": "en-US",
    "categories": ["STEM", "Mathematics", "Courseware"],
    "associatedPrograms": ["MyLab Math", "Mastering"],
    "standardComponents": ["eText", "Study Plan", "Homework"],
    "customComponents": ["Interactive Figures"],
    "instructorResourceFiles": ["Instructor Solutions Manual", "TestGen"],
    "contributors": ["Author A. Example", "Author B. Example"],
    "description": "Single Variable Calculus, 7th Edition, digital courseware "
                   "with integrated homework and adaptive study plan.",
    "longDescription": "A comprehensive single-variable calculus programme "
                       "covering limits, continuity, differentiation, "
                       "integration and series, delivered as digital "
                       "courseware with algorithmic homework, an adaptive "
                       "study plan, interactive figures and a complete eText. "
                       "Designed for a two-semester undergraduate sequence.",
    "marketingCopy": "Build conceptual understanding and procedural fluency "
                     "with thousands of algorithmic exercises, immediate "
                     "feedback and an adaptive study plan that targets each "
                     "learner's gaps.",
}

# Production projects a per-channel field set (channel_sets from the registered
# gold mapping), so copies can differ in size.
#
# `event` CARRIES THE FULL RECORD here, and that is evidence-led rather than a
# guess: the only real measurement available is a live envelope decoded on
# 2026-10-01 which carried all 37 fields at 2,753 bytes. Since the eventing
# path publishes the `event` copy, sizing it at anything smaller would make the
# include_detail = FULL volume and 1 MB-truncation numbers optimistic again —
# which is the specific thing this record exists to stop.
#
# The narrower `api` set below is illustrative only. Per-channel projections are
# mapping config and are NOT verified for this POC; if the real `event`
# projection turns out to be narrower, these numbers get better, not worse.
_CHANNEL_FIELDS = {
    "event": None,    # every field — see note above
    "message": None,  # beacon; the live 37-field envelope was on this channel
    "api": ("productId", "isbn13", "itemType", "inventoryItemStatus",
            "organizationCode", "categories", "standardComponents",
            "description", "marketingCopy"),
}


def _projected(ppid: str, channel: str) -> dict:
    record = {**_PRODUCT_RECORD, "productId": ppid}
    fields = _CHANNEL_FIELDS.get(channel)
    if fields is None:
        return record
    return {k: record[k] for k in fields if k in record}


def _glue_run_id() -> str:
    """A Glue run id, `jr_` + 64 hex — the shape real run ids actually have.

    In production both gold_run_id and event_run_id are Glue job run ids
    (`jr_66ca1176…`, 32 random bytes hex-encoded): gold_run_id comes from the
    gold job that produced the changes, event_run_id from the eventing job's
    own --JOB_RUN_ID, so they are two DIFFERENT ids. Generating them here keeps
    key sizes and log lines honest — a short readable run id makes
    publish_id look far shorter than it is in production.
    """
    return f"jr_{secrets.token_hex(32)}"


def _envelope(event_id: str, ppid: str, correlation_id: str,
              operation: str, channel: str, data: dict) -> dict:
    """The same field set _build_cloud_event produces."""
    return {
        "specversion": "1.0",
        "id": event_id,
        "source": SOURCE,
        # com.pearson.{domain}.{entity}.{operation}.{version} — the SIX-part
        # reverse-DNS form the real event_type config carries. The prefix is
        # not decoration: dlq_recorder._detail_type_from_envelope requires
        # >= 6 parts beginning com.pearson to rebuild the bus DetailType, and
        # returns "" otherwise. This POC emitted the bare four-part form until
        # 2026-10-01, so production's own parser would have produced an empty
        # detail_type on every POC event — and the POC could not have noticed.
        "type": f"com.pearson.{DOMAIN}.{DOMAIN}.{operation}.{PUBLISH_VERSION}",
        "time": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "datacontenttype": "application/json",
        "dataschema": f"com.pearson.{DOMAIN}.{DOMAIN}.{operation}.{PUBLISH_VERSION}",
        "subject": f"{DOMAIN}/{ppid}",
        # A per-run uuid4, as event_fabric_processor.py:206 generates — NOT the
        # gold_run_id. This POC put the gold_run_id here until 2026-10-01, and
        # that was a trap rather than a shortcut: it makes
        # `gold_run_id = envelope["correlationid"]` look correct, and in
        # production that would write a uuid4 into gold_run_id — the HASH KEY of
        # gsi_gold_run — so the index would fill with meaningless partitions and
        # every run-scoped query would silently return nothing.
        # gold_run_id belongs to the ledger (outcome_row.lineage). Carrying the
        # run id on the envelope is a separate, ADDITIVE proposal: a `goldrunid`
        # extension attribute, because correlationid's published schema pins it
        # to format: "uuid" (.claude/skills/generate-event-schema/SKILL.md:250).
        "correlationid": correlation_id,
        # {product_id}|{routing_attribute value}, degrading to the bare key when
        # the attribute is null — organizationCode is the product domain's
        # routing_attribute (product_events.tf:141), and "09B" is a real value
        # shape from the live envelope.
        "partitionkey": (f"{ppid}|{data['organizationCode']}"
                         if data.get("organizationCode") else str(ppid)),
        "channel": channel,
        "dataclassification": "internal",
        "piipresent": False,
        # Production's `data` is the PROJECTED PRODUCT RECORD and carries no
        # run metadata at all — gold_run_id lives only in Table 3. It was in
        # here until 2026-10-01, which made it look as though the envelope
        # could supply lineage. It cannot.
        "data": data,
    }


def handler(event, _context):
    # Both default to freshly generated jr_-shaped Glue run ids, as production
    # has them. Pass gold_run_id explicitly when you want a readable handle for
    # a test run — the value is echoed in the return, so either way the run is
    # queryable via gsi_gold_run afterwards.
    gold_run_id = event.get("gold_run_id") or _glue_run_id()
    event_run_id = event.get("event_run_id") or _glue_run_id()
    # ONE per run, as event_fabric_processor.py:206 does. Deliberately not the
    # gold_run_id — see the note on correlationid in _envelope.
    correlation_id = str(uuid.uuid4())
    count = int(event.get("count", 3))
    operation = event.get("operation", "created")
    # Which channels this run emits. Production emits a copy only for channels
    # whose hash moved AND whose published schema is active in Apicurio
    # (event_fabric_processor.py:340), so this is data-driven there.
    #
    # DEFAULT ["event"]: `channel` says which SYSTEM a copy is for — "event" is
    # eventing, "message" is beacon — so the eventing path publishes the event
    # copy. With routing on detail-type alone (owner decision, 2026-10-01) that
    # single bus entry reaches EVERY matching subscription, whatever its target
    # type. One occurrence, one bus entry, N log records for N subscribers.
    channels = event.get("channels") or (
        os.environ.get("PUBLISH_CHANNELS", "event").split(","))
    bad = [c for c in channels if c not in CHANNELS]
    if bad:
        raise ValueError(f"unknown channel(s) {bad}; valid: {list(CHANNELS)}")

    # entries/meta are FLAT across (record x channel); `records` accumulates
    # per-OCCURRENCE state, because the ledger holds one row per occurrence
    # however many channel copies it produced.
    entries, meta = [], []
    records: dict[str, dict] = {}

    for i in range(count):
        ppid = f"A{1017000000000 + i:013d}"
        # A UUID, exactly as the real processor does (event_fabric_processor.py
        # line 338: event_id = str(uuid.uuid4())). Deliberately NOT a readable
        # id: in production the ledgered event_id and the bus-assigned EventId
        # are BOTH uuid4, so they are indistinguishable by inspection — which
        # is precisely why the old POCs' correlation bug went unnoticed. A
        # readable id here would make this POC unable to reproduce that
        # confusion. The ppid -> event_id mapping is returned below, so a test
        # run is still traceable without sacrificing the fidelity.
        # ONE id per occurrence, shared by every channel copy — exactly as
        # event_fabric_processor.py:338 does, and the reason one event_id can
        # own several delivery rows with DIFFERENT sort keys.
        event_id = str(uuid.uuid4())
        records[ppid] = {"event_id": event_id, "entries_ok": 0,
                         "entries_failed": 0, "payload_json": None,
                         "reason": None, "error_msg": None}

        for channel in channels:
            # A fresh `time` per copy, because _build_cloud_event stamps
            # datetime.now() on every call. The live pair measured 4.73s apart
            # from the ledger's published_at, so this is not a rounding
            # difference.
            env = _envelope(event_id, ppid, correlation_id, operation, channel,
                            _projected(ppid, channel))
            payload_json = json.dumps(env)
            # The FIRST channel's envelope seeds the replay payload and the
            # message copy overwrites it when present — exactly the processor's
            # order (event_fabric_processor.py:358 seeds on first append, :366
            # replaces for the message channel). So with publish_channels =
            # ["event"] the payload is the event copy, not empty.
            if (channel == PAYLOAD_CHANNEL
                    or records[ppid]["payload_json"] is None):
                records[ppid]["payload_json"] = payload_json
            records[ppid].setdefault("emitted", {})[channel] = env
            entries.append({
                "Source": SOURCE,
                "DetailType": DETAIL_TYPE,
                "EventBusName": BUS,
                "Detail": payload_json,
            })
            meta.append((ppid, channel))

    per_channel_ok = {c: 0 for c in channels}
    per_channel_failed = {c: 0 for c in channels}

    for start in range(0, len(entries), 10):  # PutEvents takes at most 10
        batch, batch_meta = entries[start:start + 10], meta[start:start + 10]
        resp = events.put_events(Entries=batch)

        for entry_resp, (ppid, channel) in zip(
                resp.get("Entries", []), batch_meta):
            rec = records[ppid]
            if entry_resp.get("ErrorCode"):
                rec["entries_failed"] += 1
                per_channel_failed[channel] += 1
                rec["reason"] = str(entry_resp["ErrorCode"])
                rec["error_msg"] = str(entry_resp.get("ErrorMessage") or "")[:1000]
            else:
                rec["entries_ok"] += 1
                per_channel_ok[channel] += 1

    # ── One ledger row per OCCURRENCE, after every channel copy is settled ──
    published, failed, skipped = [], [], 0

    for ppid, rec in records.items():
        # Production's rule: ok = entries_failed == 0, across ALL channels. So a
        # record whose `event` copy was rejected while `message` succeeded is
        # PUBLISH_FAILED with NO event_id — and the message copy still gets
        # delivered, producing an INVOCATION_SUCCESS the poller cannot enrich.
        # It writes it anyway, correctly keyed, flagged lineage_missing.
        ok = rec["entries_failed"] == 0
        now = core.now_iso()
        payload_json = rec["payload_json"] or ""

        # Field-for-field the row the real processor writes (see
        # event_fabric_processor.py:751). payload is the gzip'd envelope of the
        # PAYLOAD_CHANNEL copy and is the redrive source of truth; it is
        # excluded from every GSI projection, which is why lineage() projects
        # its GetItem rather than pulling the whole row.
        item = {
            "publish_id": f"{DOMAIN}#{gold_run_id}#{ppid}#{operation}#{PUBLISH_VERSION}",
            "domain": DOMAIN,
            "gold_run_id": gold_run_id,
            "event_run_id": event_run_id,
            "primary_key": ppid,
            "operation": operation,
            "version": PUBLISH_VERSION,
            "status": "PUBLISHED" if ok else "PUBLISH_FAILED",
            "payload": gzip.compress(payload_json.encode("utf-8")),
            "payload_size_bytes": len(payload_json),
            "published_at": now if ok else None,
            "reason": rec["reason"] if not ok else None,
            "error_msg": rec["error_msg"] if not ok else None,
            "attempt_count": 1,
            "last_updated_at": now,
            "ttl": core.ttl_epoch(),
        }
        # GSI key attributes are OMITTED, not set to NULL, so the sparse
        # indexes hold exactly what they should — the real processor's rule.
        # The bus EventId is in entry_resp["EventId"] and is deliberately
        # DISCARDED: the ledger keys on the ENVELOPE id.
        if ok:
            item["event_id"] = rec["event_id"]
        else:
            item["recovery_scope"] = f"{DOMAIN}#{gold_run_id}"

        try:
            core.t_publish.put_item(
                Item=item,
                ConditionExpression="attribute_not_exists(publish_id)",
            )
            if ok:
                published.append({"ppid": ppid, "event_id": rec["event_id"]})
            else:
                failed.append({"ppid": ppid, "error": rec["reason"]})
        except ClientError as e:
            if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
            skipped += 1

    out = {"gold_run_id": gold_run_id, "event_run_id": event_run_id,
           "channels": channels,
           "occurrences": count,
           "bus_entries": len(entries),
           "bus_entries_ok": per_channel_ok,
           "bus_entries_failed": per_channel_failed,
           "published": len(published), "skipped_existing": skipped,
           "publish_failed": failed, "events": published,
           "payload_size_bytes": len(
               records[next(iter(records))]["payload_json"] or "") if records else 0}

    # Opt-in, OFF by default: the exact envelopes that went on the bus, per
    # record per channel. The local suite needs them because only the
    # `message` copy is in the ledger, and a hand-rebuilt `event` copy would
    # have its own `time` — which is now the sort key's component, so a
    # reconstruction could agree with the handlers while disagreeing with what
    # was published. That self-consistency is exactly what hid the key bug.
    # Never pass this on a large live run: it returns every envelope.
    if event.get("return_envelopes"):
        out["envelopes"] = {ppid: rec.get("emitted", {})
                            for ppid, rec in records.items()}

    print(json.dumps({k: v for k, v in out.items() if k != "envelopes"}))
    return out
