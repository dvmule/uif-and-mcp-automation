"""Scheduled poll of the bus log group -> observed DELIVERED rows.

REPLACES the CloudWatch Logs subscription filter that drove delivery-writer.
Measured on 2026-09-30 at 10,000 events x 2 consumers, the push design cost
**7,666 Lambda invocations for 20,020 records — an average batch of 2.6**
(min 1, max 13). A subscription filter flushes eagerly rather than
accumulating, so batching never arrives: extrapolated to a 1M-event run x 10
consumers that is ~3.85M invocations. Polling a 15-minute window instead is a
fixed ~96 invocations a day whatever the volume.

THE BIGGER REASON, which is correctness not cost. The push design was purely
reactive: if a log record never arrived, nothing was ever going to ask for it
again, so a missing DELIVERED row was undetectable and unrecoverable. AWS
states bus log delivery is best-effort, so that gap was the design's
structural weakness. A timestamp-windowed poll is re-runnable over any window
still inside the log group's retention, which makes the same gap a recoverable
one — see the manual-window override in handler().

THE JOIN is unchanged, and is still the whole point:

  An INVOCATION_SUCCESS record carries `event_id` (the BUS-assigned EventId, a
  GUID we never persist) plus details.rule_arn, details.target_arn and —
  because the bus is configured include_detail = FULL — details.target_input.
  The rules deliver with InputPath = $.detail, so target_input IS the
  CloudEvents envelope and envelope["id"] IS the ledgered event_id. We join on
  a column that already exists: no new attribute, no new GSI, no change to
  event_fabric_processor.py.

  target_input also supplies envelope["time"], which is the sort key's time
  component (outcome_row invariant 1, and what production's dlq_recorder
  keys on). So BOTH halves of the key come out of the log record itself and
  the ledger read is enrichment only — a record that arrives before its
  ledger row is still written at the right key.

FOUR THINGS THAT ARE LOAD-BEARING HERE, each learned rather than assumed:

  1. FilterLogEvents, NOT Logs Insights. Insights is billed per GB scanned and
     caps a result set at 10,000 rows — one 15-minute window of this POC's
     traffic already exceeds that, silently. FilterLogEvents pages with
     nextToken, has no row cap, carries no scan charge, and takes the same
     filter pattern the subscription filter used.

  2. THE WINDOW TRAILS `now` BY POLL_LAG_SECONDS. FilterLogEvents selects on
     the log event's own timestamp, but a record is only visible once
     INGESTED, and vended delivery from the bus lags. Querying up to `now`
     would silently miss anything ingested after the query, permanently,
     because the watermark would already have moved past it. The lag is the
     only thing protecting against that, so the poller MEASURES its own lag
     (max ingestionTime - timestamp, reported every run) and you tune
     POLL_LAG_SECONDS from that number rather than from a guess.

  3. CONDITIONAL PutItem, NOT BatchWriteItem. BatchWriteItem cannot carry a
     ConditionExpression, and the condition is the invariant that failure
     evidence outranks derived success (see outcome_row.write_delivered). The
     25x request saving is not worth losing it, so the writes are parallelised
     with a thread pool instead.

  4. THE WATERMARK COMMITS ONLY ON A COMPLETE WINDOW. Records arrive
     interleaved across log streams, so the last page of a window is not
     guaranteed to hold the window's latest timestamp, and a partial
     checkpoint could therefore skip records. If a run cannot finish its
     window it commits nothing and says so loudly; the next run redoes it,
     which is wasteful but cannot lose a row. Bound the work per run with
     POLL_MAX_WINDOW_SECONDS instead of checkpointing mid-window.

WHAT IT DELIBERATELY DOES NOT DO: write FAILED rows. INVOCATION_FAILURE and
INVOCATION_DLQ records are counted and reported as corroboration only. The
consumer DLQ is durable and the bus logs are best-effort, so the event-handler
owns failure.
"""
from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import boto3
from botocore.config import Config

import outcome_row as core

DETAIL_TYPE = os.environ["DETAIL_TYPE"]
BUS_LOG_GROUP = os.environ["BUS_LOG_GROUP"]
POLLER_ID = os.environ.get("POLLER_ID", "bus-log-poll")

# See note 2 in the module docstring: this is the only protection against
# losing a late-ingested record, and the run report tells you what to set it to.
LAG_SECONDS = int(os.environ.get("POLL_LAG_SECONDS", "600"))
# Re-read a little before the watermark. Writes are idempotent, so overlap is
# free insurance against a boundary record landing on the wrong side.
OVERLAP_SECONDS = int(os.environ.get("POLL_OVERLAP_SECONDS", "60"))
# Caps the work one run can be asked to do, so a backlog drains window by
# window with guaranteed forward progress instead of timing out forever.
MAX_WINDOW_SECONDS = int(os.environ.get("POLL_MAX_WINDOW_SECONDS", "900"))
COLD_START_LOOKBACK_SECONDS = int(os.environ.get("POLL_COLD_START_LOOKBACK_SECONDS", "3600"))
CONCURRENCY = int(os.environ.get("POLL_CONCURRENCY", "24"))
# Stop paginating with this much of the Lambda's budget left, so the run can
# still report cleanly rather than being killed mid-page.
RESERVE_MS = int(os.environ.get("POLL_RESERVE_MS", "20000"))

# SUCCESSES ONLY. The poller writes DELIVERED and nothing else, so reading
# INVOCATION_FAILURE / INVOCATION_DLQ meant fetching and parsing records it
# would always discard — 432 of 20,020 on the 2026-09-30 run.
#
# It is not a large saving: FilterLogEvents applies the pattern SERVER-SIDE and
# carries no per-GB scan charge, so what you save is pages and Lambda parse
# time, not ingestion or storage. Those are paid when the bus writes the
# record, whichever pattern we use.
#
# What it gives up is the corroboration count that made the 2026-09-30
# reconciliation work (19,588 success + 432 failure = 20,020 received, so
# nothing lost). That is replaced by metric filters on the log group
# (eventbridge.tf), which are cheaper, alarmable, and keep counting even when
# the poller is not running.
#
# Quoted-string form rather than the JSON `{ $.message_type = ... }` form
# deliberately: the substring syntax is the one proven live on this log group.
# A pattern that matches nothing fails SILENTLY — the poller would read zero
# records and advance its watermark — so this is not the place to try
# unverified syntax. Watch `records` in the run report after any change to it.
FILTER_PATTERN = os.environ.get(
    "POLL_FILTER_PATTERN", '"INVOCATION_SUCCESS"')

OUTCOME_TYPES = ("INVOCATION_SUCCESS", "INVOCATION_FAILURE", "INVOCATION_DLQ")

_logs = boto3.client("logs", config=Config(
    retries={"max_attempts": 10, "mode": "adaptive"},
    max_pool_connections=CONCURRENCY + 8))

_state = core.table(os.environ["T_STATE"])

# event_id -> ledger row, for the life of ONE invocation. Each event produces
# one success record per consumer, so this halves the lineage lookups at two
# consumers and saves more as consumers are added. Cleared in handler() —
# a warm container must not serve a previous run's ledger view.
_lineage_cache: dict[str, dict] = {}


def _iso(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, timezone.utc).isoformat()


def _ms(iso: str) -> int:
    return int(datetime.fromisoformat(
        iso.replace("Z", "+00:00")).timestamp() * 1000)


def _iso_z(ts_ms: int) -> str:
    """Ledger-style timestamp, so delivered_at and published_at compare."""
    return datetime.fromtimestamp(ts_ms / 1000, timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.%fZ")


# ── Watermark ───────────────────────────────────────────────────────────────
# A one-item table rather than an SSM parameter: it is the same datastore the
# rest of the POC already talks to, and a conditional write is available if the
# schedule is ever allowed to overlap (today reserved_concurrent_executions = 1
# makes overlap impossible, which is cheaper than coordinating).

def _read_watermark() -> int | None:
    item = _state.get_item(Key={"poller_id": POLLER_ID}).get("Item")
    if not item or "last_ts_ms" not in item:
        return None
    return int(item["last_ts_ms"])


RUN_HISTORY_DAYS = int(os.environ.get("POLL_RUN_HISTORY_DAYS", "30"))


def _record_run(report: dict, started_iso: str, committed: bool) -> None:
    """One durable row per run, in the SAME table as the cursor.

    Not a new table: the key schema is poller_id alone, so a `run#` prefix
    coexists with the cursor item without changing anything. One extra PutItem
    per run — 96 a day at the default cadence.

    WRITTEN FOR EVERY RUN, including ones that did not commit, and that is the
    whole point. `last_run` on the cursor item only survives a committed run,
    so a run that timed out or failed used to leave no durable trace at all —
    precisely the run worth looking at later. The Lambda log has it for 14
    days; this keeps the shape of it for RUN_HISTORY_DAYS.

    Monitoring should still come from the CloudWatch metrics (see lambda.tf),
    not from scanning this. These rows are for audit and for answering "what
    did the 03:15 run actually do" after the logs have gone.
    """
    try:
        _state.put_item(Item={
            "poller_id": f"run#{POLLER_ID}#{started_iso}",
            "started_at": started_iso,
            "finished_at": core.now_iso(),
            "committed": committed,
            "report": {k: v for k, v in report.items()
                       if isinstance(v, (int, str, bool))},
            "ttl": core.ttl_epoch(RUN_HISTORY_DAYS),
        })
    except Exception as exc:  # noqa: BLE001
        # History is diagnostics. Losing a row must never fail a run that
        # already did its real work and moved the cursor.
        print(json.dumps({"warn": "could not record run history",
                          "error": f"{type(exc).__name__}: {exc}"}))


def _commit_watermark(ts_ms: int, report: dict) -> None:
    _state.put_item(Item={
        "poller_id": POLLER_ID,
        "last_ts_ms": ts_ms,
        "last_ts_iso": _iso(ts_ms),
        "updated_at": core.now_iso(),
        # Ints and strings only — a float here would need Decimal conversion.
        "last_run": {k: v for k, v in report.items()
                     if isinstance(v, (int, str, bool))},
    })


def _window(now_ms: int) -> tuple[int, int, bool]:
    """[start, end) to query, and whether the backlog was capped."""
    end = now_ms - LAG_SECONDS * 1000
    wm = _read_watermark()
    start = (wm - OVERLAP_SECONDS * 1000) if wm is not None \
        else (end - COLD_START_LOOKBACK_SECONDS * 1000)

    capped = False
    if end - start > MAX_WINDOW_SECONDS * 1000:
        end = start + MAX_WINDOW_SECONDS * 1000
        capped = True
    return start, end, capped


# ── Reading ─────────────────────────────────────────────────────────────────

def _pages(start_ms: int, end_ms: int):
    token = None
    while True:
        kwargs = {"logGroupName": BUS_LOG_GROUP,
                  "startTime": start_ms, "endTime": end_ms}
        if FILTER_PATTERN:
            kwargs["filterPattern"] = FILTER_PATTERN
        if token:
            kwargs["nextToken"] = token
        resp = _logs.filter_log_events(**kwargs)
        yield resp.get("events", [])
        token = resp.get("nextToken")
        if not token:
            return


def _envelope_from(details: dict) -> dict | None:
    """The CloudEvents envelope EventBridge sent to the target."""
    raw = details.get("target_input")
    if not raw:
        return None
    try:
        return json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        return None


# ── Per-record work ─────────────────────────────────────────────────────────

def _job_from_success(rec: dict, ts_ms: int) -> tuple[str, dict | None]:
    """(outcome, job) — job is None when the record cannot be turned into a row."""
    details = rec.get("details") or {}
    rule_arn = details.get("rule_arn")
    target_arn = details.get("target_arn")

    env = _envelope_from(details)
    if env is None:
        # Either include_detail is NONE, or the 1 MB cap truncated
        # target_input (it is the first field EventBridge drops). Either way
        # there is no way back to the ledgered event id, so say so rather than
        # writing a row keyed on the bus GUID.
        print(json.dumps({"error": "no target_input on INVOCATION_SUCCESS - "
                                   "cannot correlate to the ledger",
                          "bus_event_id": rec.get("event_id"),
                          "rule_arn": rule_arn}))
        return "uncorrelatable", None

    event_id = env.get("id")
    event_time = core.envelope_time(env)
    if not event_id or not event_time:
        # Both halves of the sort key live in the envelope (invariant 1), so
        # without them there is no correct key and nothing safe to write.
        print(json.dumps({"error": "envelope has no id / no time - cannot key "
                                   "the row",
                          "has_id": bool(event_id), "has_time": bool(event_time),
                          "rule_arn": rule_arn}))
        return "uncorrelatable", None

    sub = core.customer_from_rule_arn(rule_arn) if rule_arn else None
    if sub is None and target_arn:
        sub = core.customer_from_target_arn(target_arn)
    if sub is None:
        print(json.dumps({"error": "rule/target not in subscription-configs",
                          "rule_arn": rule_arn, "target_arn": target_arn}))
        return "no_subscription", None

    # The BUS DetailType, reconstructed exactly as production's recorder does
    # — NOT envelope["type"], which is the CloudEvents type and is a different
    # string. No silent fallback to the configured DETAIL_TYPE either: one
    # Lambda serves a whole bus and a customer may hold several subscriptions
    # on different detail-types (pmc on .created and .updated), so a single
    # configured value would mislabel rows rather than default them.
    _, detail_type = core.unwrap(env)
    if not detail_type:
        print(json.dumps({"error": "cannot derive the bus detail-type from the "
                                   "envelope type",
                          "envelope_type": env.get("type"),
                          "event_id": event_id}))
        return "uncorrelatable", None

    return "pending", {
        "customer_id": sub["customer_id"],
        "event_id": event_id,
        "event_time": event_time,
        "detail_type": detail_type,
        "target_arn": target_arn,
        "latency_ms": details.get("ingestion_to_success_latency_ms"),
        "total_attempts": details.get("total_attempts"),
        # The log record's OWN timestamp — fixed for that record, so a re-read
        # produces a byte-identical row instead of restamping it with the time
        # of the re-read.
        "delivered_at": _iso_z(ts_ms),
    }


def _resolve_lineage(event_ids: set[str], pool: ThreadPoolExecutor) -> None:
    todo = [e for e in event_ids if e not in _lineage_cache]
    if not todo:
        return
    for event_id, row in zip(todo, pool.map(core.lineage, todo)):
        _lineage_cache[event_id] = row


def _write(job: dict) -> str:
    row = _lineage_cache.get(job["event_id"]) or {}
    if not row:
        # NOT a blocker any more. The key comes from the envelope (invariant
        # 1/2), so the row is written correctly keyed with lineage_missing set,
        # and the enrichment columns can be back-filled later by re-reading the
        # window. The previous build dropped the record here, because it keyed
        # on the ledger's published_at and had nothing to key with.
        print(json.dumps({"warn": "no ledger row - writing a correctly-keyed "
                                  "row with lineage_missing",
                          "event_id": job["event_id"],
                          "customer_id": job["customer_id"]}))
    try:
        outcome = core.write_delivered(row=row, **job)
    except Exception as exc:  # noqa: BLE001 — one bad row must not fail a run
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}",
                          "event_id": job["event_id"]}))
        return "write_error"
    return outcome if row else f"{outcome}_lineage_missing"


# ── Handler ─────────────────────────────────────────────────────────────────

def handler(event, context):
    """Scheduled every POLL_RATE. Also takes a manual window override:

        {"start_iso": "2026-09-30T12:00:00Z",
         "end_iso":   "2026-09-30T12:30:00Z",
         "commit": false}

    which is the catch-up path: re-read any window still inside the log
    group's retention without disturbing the live watermark. `commit: true`
    moves the watermark to end_iso as well, for a deliberate rewind.
    """
    _lineage_cache.clear()
    started_iso = core.now_iso()
    event = event or {}
    now_ms = int(time.time() * 1000)

    lag_floor = now_ms - LAG_SECONDS * 1000
    manual = bool(event.get("start_iso"))
    reads_into_lag = False

    if manual:
        start_ms = _ms(event["start_iso"])
        end_ms = _ms(event["end_iso"]) if event.get("end_iso") else lag_floor
        capped = False

        if end_ms > lag_floor:
            # The window reaches into the ingestion-lag zone, where records
            # exist but may not be readable yet.
            reads_into_lag = True
            if event.get("commit"):
                # THE ONE WAY A MANUAL RUN CAN LOSE RECORDS. Committing a
                # watermark past the lag floor means the next run starts after
                # timestamps whose records had not been ingested when we
                # looked — and nothing ever asks for them again. Reading into
                # the zone is fine; moving the cursor over it is not. The
                # scheduled path is clamped by _window(); this is the same
                # clamp for the manual one.
                end_ms = lag_floor
                reads_into_lag = False
    else:
        start_ms, end_ms, capped = _window(now_ms)

    if end_ms <= start_ms:
        report = {"skipped": "watermark already current",
                  "watermark": _iso(start_ms + OVERLAP_SECONDS * 1000)}
        print(json.dumps(report))
        _record_run(report, started_iso, committed=False)
        return report

    counts: dict[str, int] = {}
    pages = 0
    max_ingest_lag_ms = 0
    complete = True

    def bump(key: str, n: int = 1) -> None:
        counts[key] = counts.get(key, 0) + n

    with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        for log_events in _pages(start_ms, end_ms):
            pages += 1
            jobs: list[dict] = []

            for log_event in log_events:
                # log_records_read, not "records" and not
                # "invocation_success_found". "record" already means three
                # things here — a CloudWatch log record, a gold/product record
                # and a DynamoDB row — so the bare word is unusable. And naming
                # it after the message type would bake POLL_FILTER_PATTERN into
                # the field: widen the pattern for diagnostics and a field
                # called invocation_success_found would be counting failures.
                # This counts what was READ, whatever the pattern matched.
                bump("log_records_read")
                # ingestionTime - timestamp is the real ingestion lag, and the
                # reason POLL_LAG_SECONDS can be set from evidence.
                lag = int(log_event.get("ingestionTime", 0)) - int(log_event["timestamp"])
                max_ingest_lag_ms = max(max_ingest_lag_ms, lag)

                try:
                    rec = json.loads(log_event["message"])
                except ValueError:
                    bump("unparseable")
                    continue

                mt = rec.get("message_type")
                if mt == "INVOCATION_SUCCESS":
                    outcome, job = _job_from_success(rec, int(log_event["timestamp"]))
                    if job is not None:
                        jobs.append(job)
                    else:
                        bump(outcome)
                elif mt in OUTCOME_TYPES:
                    # Corroboration only — the DLQ path owns FAILED.
                    bump(f"noted_{mt.lower()}")
                else:
                    bump("ignored")

            if jobs:
                _resolve_lineage({j["event_id"] for j in jobs}, pool)
                for outcome in pool.map(_write, jobs):
                    bump(outcome)

            if context is not None and \
                    context.get_remaining_time_in_millis() < RESERVE_MS:
                complete = False
                break

    report = {
        "window_start": _iso(start_ms),
        "window_end": _iso(end_ms),
        "window_seconds": (end_ms - start_ms) // 1000,
        "pages": pages,
        "complete": complete,
        "backlog_capped": capped,
        "max_observed_ingest_lag_seconds": max_ingest_lag_ms // 1000,
        "manual": manual,
        **counts,
    }

    # Every record read must land in exactly one outcome bucket. If it does
    # not, a code path is dropping records silently — which is the one failure
    # this design cannot tolerate, since a missing row is indistinguishable
    # from an undelivered event. Reported rather than asserted so a run still
    # completes, but it is the field to alarm on.
    accounted = sum(v for k, v in counts.items() if k != "log_records_read")
    unaccounted = counts.get("log_records_read", 0) - accounted
    if unaccounted:
        report["unaccounted"] = unaccounted
        print(json.dumps({"error": "log records read but not accounted for in "
                                   "any outcome — a record is being dropped",
                          "log_records_read": counts.get("log_records_read", 0),
                          "accounted": accounted,
                          "unaccounted": unaccounted}))

    if reads_into_lag:
        # Safe, because nothing was committed — but say so, or an incomplete
        # count from this window gets mistaken for evidence of loss.
        report["warning"] = (
            f"window ends inside the {LAG_SECONDS}s ingestion-lag zone; "
            f"records may not all be readable yet. Nothing committed, so "
            f"re-run it later for the full count")

    if complete and (not manual or event.get("commit")):
        _commit_watermark(end_ms, report)
        report["watermark_committed"] = _iso(end_ms)
    elif not complete:
        # Nothing is committed, so nothing is lost — but the next run will redo
        # this window, and it will keep failing the same way until the work per
        # run fits. Say what to change.
        report["action_required"] = (
            f"window of {(end_ms - start_ms) // 1000}s did not finish in one run; "
            f"lower POLL_MAX_WINDOW_SECONDS or raise the schedule rate")

    print(json.dumps(report))
    _record_run(report, started_iso,
                committed="watermark_committed" in report)
    return report
