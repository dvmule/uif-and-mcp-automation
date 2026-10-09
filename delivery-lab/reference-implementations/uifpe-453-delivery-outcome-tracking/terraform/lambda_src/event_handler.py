"""The DOMAIN DLQ -> FAILED rows. The AUTHORITATIVE failure path.

A dead letter is durable evidence that delivery did not happen. The event-bus
logs are best-effort by AWS's own statement, so this handler — not the
delivery-poller — owns FAILED, and its write beats a DELIVERED row.

THIS MIRRORS A LAMBDA THAT ALREADY EXISTS IN PRODUCTION:
stacks/event/src/dlq_recorder.py, contract in
stacks/event/docs/delivery-metrics.md. It is reproduced here rather than
invented, so the POC tests the interaction between the two writers on the
REAL key derivation. Where the two differ, the difference is deliberate and
commented.

TOPOLOGY, corrected 2026-10-01. There are TWO DLQs per subscription and only
one of them is a failure signal:

  * the DOMAIN DLQ, one per bus — every rule's DeadLetterConfig points here,
    and the shared dlq-recorder's event source mapping reads it. THIS is the
    delivery-failure signal.
  * the per-subscription "-dlq" — the subscription QUEUE's own RedrivePolicy
    target, for consumer-side poison messages. Nothing reads it, by design.

Pointing a rule's DeadLetterConfig at the per-subscription queue was a real
bug, found live on 2026-09-21: failed deliveries went somewhere nothing
consumed and were invisible. See subscription_provisioner.py:331.

An earlier build of this POC gave every customer ONE queue serving both
purposes, which is why it carried a `failure_origin` attribute to tell
EventBridge failures from consumer failures. Production needs no such
disambiguation — consumer poison never reaches the domain DLQ — so the
attribute is gone.

KEYING. sort_time is ALWAYS the envelope's own time, via
outcome_row.envelope_time(). There is no ledger dependency and therefore no
degrade path: the previous build preferred the ledger's published_at and fell
back to the envelope time only after five receives, which meant a dead letter
arriving before its ledger row was either retried for minutes or written at a
different sort key than the DELIVERED row for the same delivery.
"""
from __future__ import annotations

import json
import os

import outcome_row as core

DOMAIN = os.environ["DOMAIN"]


def _attr(attrs: dict, name: str) -> str | None:
    return (attrs.get(name) or {}).get("stringValue")


def _subscription(attrs: dict) -> dict | None:
    """Customer from the DLQ message's attributes.

    TARGET_ARN first, matching production: delivery-metrics.md defines
    customer_id as "derived from the DLQ message's TARGET_ARN". RULE_ARN is a
    fallback rather than the primary, and both indexes are unique per
    subscription because the provisioner puts the detail-type in every
    resource name.
    """
    target_arn = _attr(attrs, "TARGET_ARN")
    if target_arn:
        sub = core.customer_from_target_arn(target_arn)
        if sub:
            return sub
    rule_arn = _attr(attrs, "RULE_ARN")
    if rule_arn:
        return core.customer_from_rule_arn(rule_arn)
    return None


def _write_failed(*, customer_id: str, event_id: str, event_time: str,
                  detail_type: str, row: dict, attrs: dict) -> None:
    key = core.delivery_key(customer_id, event_time, event_id)

    sets = [
        "#st = :failed", "customer_id = :cust", "#dom = :dom",
        "event_id = :eid", "event_time = :etime", "detail_type = :dt",
        "reason = :rsn", "error_msg = :em", "target_arn = :tgt",
        # failed_at IS the envelope time in production, not the failure
        # moment — dlq_recorder.py:139/156 writes the same value it uses for
        # the sort key, and delivery-metrics.md defines it as "Event time
        # (body["time"]) — when it was published". Writing now() here, as this
        # handler did until 2026-10-01, silently redefines a field that
        # gsi_gold_run PROJECTS and the ops UI reads.
        # It is kept alongside event_time rather than replaced: see the note in
        # outcome_row about the rename, which would require recreating the GSI.
        "failed_at = :etime",
        "dlq_received_at = :now", "last_updated_at = :now",
        "#t = :ttl",
    ]
    names = {"#st": "status", "#dom": "domain", "#t": "ttl"}
    values = {
        ":failed": "FAILED", ":cust": customer_id, ":dom": DOMAIN,
        ":eid": event_id, ":etime": event_time, ":dt": detail_type,
        ":rsn": _attr(attrs, "ERROR_CODE") or "UNKNOWN",
        ":em": (_attr(attrs, "ERROR_MESSAGE") or "")[:1000],
        ":tgt": _attr(attrs, "TARGET_ARN") or "",
        ":now": core.now_iso(), ":ttl": core.ttl_epoch(),
    }

    # Production records which retry bound was hit; it is the difference
    # between "target kept rejecting" and "target was unreachable too long".
    exhausted_by = _attr(attrs, "EXHAUSTED_RETRY_CONDITION")
    if exhausted_by:
        sets.append("exhausted_by = :exh")
        values[":exh"] = exhausted_by

    # Sparse lineage: never SET a GSI key attribute to NULL — a null
    # gold_run_id fails with ValidationException and the record then retries
    # forever. (Found in the POC2 local suite, 2026-09-24.)
    enriched = False
    for attr, ph, val in (
        ("gold_run_id", ":gr", row.get("gold_run_id")),
        ("event_run_id", ":er", row.get("event_run_id")),
        ("primary_key", ":pk2", row.get("primary_key")),
        ("operation", ":op", row.get("operation")),
        ("published_at", ":pat", row.get("published_at")),
    ):
        if val is not None:
            sets.append(f"{attr} = {ph}")
            values[ph] = val
            enriched = True

    if not enriched:
        sets.append("lineage_missing = :lm")
        values[":lm"] = True

    # UpdateItem, not the conditional PutItem production uses. Production's
    # recorder is insert-only — ConditionExpression="attribute_not_exists(
    # domain_customer)" — which was safe while Table 4 held failures ONLY.
    # Once DELIVERED rows share the key, a DELIVERED row written first would
    # REJECT the failure insert and the failure would be silently lost. So
    # adopting option 6 requires relaxing that condition in production; this
    # models the corrected behaviour (failure always wins) rather than the
    # current one.
    # REMOVE the success-only attributes. Without this, a row that the poller
    # already wrote as DELIVERED keeps confirmed_by = EVENT_BUS_LOG, a
    # delivered_at timestamp and the success latency after being flipped to
    # FAILED — so it asserts both that the event was delivered at a specific
    # time and that delivery failed. Found 2026-10-01 while tracing a
    # DELIVERED-then-dead-lettered row end to end; update_item merges, it does
    # not replace.
    core.t_delivery.update_item(
        Key=key,
        UpdateExpression=("SET " + ", ".join(sets)
                          + " REMOVE confirmed_by, delivered_at, "
                            "ingestion_to_success_latency_ms"),
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
    )


def handler(event, _context):
    failures = []

    for record in event.get("Records", []):
        message_id = record["messageId"]
        try:
            body = json.loads(record["body"])
            attrs = record.get("messageAttributes") or {}

            # Wrapped or bare, and the bus DetailType with it — one helper,
            # mirroring dlq_recorder.py:98.
            env, detail_type = core.unwrap(body)
            event_id = env.get("id") or body.get("id")
            event_time = core.envelope_time(body)
            if not event_id or not event_time:
                raise ValueError("DLQ body carries no CloudEvents id / time")

            if not _attr(attrs, "RULE_ARN") and not _attr(attrs, "TARGET_ARN"):
                # Production policy: Table 4 is delivery-only. A dead letter
                # with neither attribute is not a subscriber delivery (the
                # trigger Lambda's own async failures land here), so it is
                # logged in full and dropped rather than recorded.
                print(json.dumps({"dropped": "not a subscriber delivery",
                                  "messageId": message_id, "body": body}))
                continue

            sub = _subscription(attrs)
            if sub is None:
                # A config gap, not a transient fault. Retry: the queue's own
                # retention bounds it and dropping would lose a real failure.
                raise LookupError("rule/target not in subscription-configs")

            if not detail_type:
                raise ValueError("cannot derive the bus detail-type "
                                 f"(envelope type: {env.get('type')!r})")

            _write_failed(customer_id=sub["customer_id"], event_id=event_id,
                          event_time=event_time, detail_type=detail_type,
                          row=core.lineage(event_id), attrs=attrs)

        except Exception as exc:  # noqa: BLE001 — per-record isolation
            print(json.dumps({"error": str(exc), "messageId": message_id}))
            failures.append({"itemIdentifier": message_id})

    return {"batchItemFailures": failures}
