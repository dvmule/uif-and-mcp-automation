# gold_dispatcher.py
# Gold-only scheduler dispatcher (ADR-0004: independent control plane).
# Triggered by EventBridge with {"frequency": "<one of gold_frequencies
# .ALL_FREQUENCIES>"}. Scans the gold pipelines config table and starts the
# matching Glue job per enabled pipeline, with an in-flight guard so an
# EventBridge tick can never stack runs of the same gold_id — nor overlap a
# transform and a reconcile on one gold_id, which would race the reconcile's
# orphan anti-join against the transform's writes.
#
# Ported from uif-relay-echo's scheduler_dispatcher._dispatch_gold with one
# deliberate fix: "gold-hourly" is routed here (upstream, the gold_hourly
# rule emitted a frequency the handler never matched, so hourly gold configs
# were silently never dispatched).
#
# ── UIFPE-149: per-job schedules, and what it changed here ───────────────────
# BEFORE: two cadences, each its own EventBridge rule, and the rule's payload
# named the cadence. Every pipeline on a cadence therefore shared one clock —
# two daily tables could not run at different times, because there was exactly
# one daily rule.
#
# NOW: a pipeline declares its OWN schedule — `schedule.frequency` (one of the
# eleven day-tiling rungs, 5m → 24h, or `gold-weekly` with its `weekly_days`
# set, UIFPE-532) plus `schedule.start_time` (a time of day, UTC) — and
# ONE sweep rule ticks every 5 minutes. This module decides which pipelines are
# DUE. Scheduling moved out of infrastructure and into config-as-code, which is
# what lets the wizard set a table's schedule without a Terraform change.
#
# The decision is SLOT-BASED, not elapsed-time-based: a pipeline's slots are its
# start time plus multiples of its interval, and it is due when its last
# scheduled run started before the most recent slot. Three properties follow,
# and all three matter:
#   * a skipped tick does not lose a run. Throttling, a blind guard, a Lambda
#     error — the next tick computes the same previous slot and dispatches, just
#     late. Elapsed-time scheduling would have silently shifted the phase.
#   * the phase never drifts, because it is derived from the start time rather
#     than from the previous run's finish.
#   * a job that overruns its interval does not build a backlog; it just misses
#     the slots it was still running through.
#
# Because a skipped tick is recoverable, the in-flight guard now fails CLOSED
# for every cadence. That is a change from the previous fail-open posture, and
# it is only safe BECAUSE of slot-based due-ness — see _guard_unavailable_result.
#
# Two other things this ticket fixed here:
#   * the frequency vocabulary lives in ONE place, gold_frequencies.py, and
#     legacy values are NORMALIZED so a new dispatcher still serves an
#     un-migrated `gold` config through the deploy-order window. Without that,
#     product's live 06:30 run would go missing with nothing louder than an
#     INFO line.
#   * the run-history walk is BOUNDED by the timeout horizon. It used to
#     paginate the job's entire history on every tick, twice — the very thing
#     that throttles, and throttling is what used to silently disable the guard.
#     At 288 sweeps a day that is no longer survivable.
#
# It also added a FAILURE COOLDOWN: without one, a deterministically broken
# pipeline on gold-5m starts 288 runs a day (x2, max_retries=1) at 8-16 DPU
# until someone reads the invoice. The guard blocks in-flight runs, never
# failed ones — that is by design and separately tested.
#
# KNOWN LIMITATION (deliberate, framework owner 2026-08-11): the guard is
# check-then-act with no lock, and EventBridge delivery is at-least-once, so
# two duplicate invocations of one rule can both observe "nothing in flight"
# and both call StartJobRun. The window is seconds. Damage is bounded — the
# MERGE is hash-gated and idempotent (ADR-0003), so the cost is a doubled
# change-log append plus a raced watermark advance, and consumers already
# dedupe on (pk, run_id). The real fix is a conditional-write lease in the
# config table, which would make this Lambda a config WRITER (it is
# deliberately read-only: Scan + GetItem, no UpdateItem in its IAM policy).
# Not taken. Revisit if duplicate-delivery is ever actually observed.

import logging
import os
from datetime import datetime, timedelta, timezone

import boto3
from boto3.dynamodb.conditions import Attr

# Bare sibling imports, NOT `from lambda_dispatcher.echo_resources import echo`:
# the deployment artifact is a flat Lambda zip (infra/dispatcher.tf archives
# this directory's contents, not the directory itself), so at runtime
# echo_resources.py and gold_frequencies.py sit next to this module at the zip
# root. Keep them bare — and keep gold_frequencies.py in dispatcher.tf's
# archive_file source list, or every tick will fail on ImportError.
from gold_resources import (
    MAINTENANCE_ARGS,
    gold_config_table,
    gold_reconcile_job,
    gold_transform_job,
)
from gold_frequencies import (
    ALL_FREQUENCIES,
    DEFAULT_COOLDOWN_MINUTES,
    DEFAULT_FREQUENCY,
    DEFAULT_RECOMPUTE_EVERY_N_RUNS,
    DEFAULT_WEEKLY_DAYS,
    RECONCILE_FREQUENCY,
    TICK_FREQUENCY,
    default_start_time,
    format_time_of_day,
    interval_minutes,
    is_sweep,
    is_weekly,
    normalize,
    parse_time_of_day,
    parse_weekly_days,
    previous_slot,
    previous_slot_at_interval,
)
from gold_telemetry import emit, tick_document

logger = logging.getLogger()
logger.setLevel(logging.INFO)

REGION = os.environ.get("REGION", "eu-west-1")

# Resource names: an explicit env var (set by Terraform) wins; otherwise
# gold_resources derives them from ENV, and RAISES when ENV is unset instead
# of guessing "dev" (review finding #15). Resolution is DEFERRED to the
# handler rather than evaluated at import: a misconfigured function then
# fails the invocation with a clear message (and the module stays importable
# in tests, which set ENV or pass env= explicitly).
_NAME_FOR = {
    "GOLD_JOB_NAME":           gold_transform_job,
    "GOLD_RECONCILE_JOB_NAME": gold_reconcile_job,
    "GOLD_CONFIG_TABLE":       gold_config_table,
}

# Any of these states means a run is not finished and could still write.
# STARTING and WAITING were missing (review finding #8): a run sits in
# STARTING while Glue provisions capacity — often minutes — and in WAITING
# when it is queued behind the account's concurrency limit. Both were
# invisible to the guard, so an EventBridge tick could stack a second run on
# the same gold_id and race it on the same Gold table and watermark.
IN_FLIGHT_STATES = ("STARTING", "RUNNING", "STOPPING", "WAITING")

# Terminal states that start a cooldown (UIFPE-149). STOPPED is included on
# purpose: a human who stops a runaway run does not want the next tick to
# restart it 5 minutes later. The escape hatch is a manual `start-job-run`,
# which bypasses this Lambda entirely.
FAILURE_STATES = ("FAILED", "TIMEOUT", "ERROR", "STOPPED")

# MAINTENANCE_ARGS (--refresh_columns / --rebuild) lives in gold_resources,
# shared with gold_announce: such a run BLOCKS the in-flight guard - two
# writers on one Gold table is what the guard prevents, whatever started
# them - but never SATISFIES a schedule slot.

# Run-history walk bound. Nothing that started longer ago than the largest
# configured Glue timeout can still be in flight, so the newest-first walk can
# stop there. The margin absorbs clock skew plus the STARTING/WAITING time that
# elapses before Glue stamps StartedOn.
GUARD_HORIZON_MARGIN_MINUTES = 60

# Absolute backstop so a pathological history cannot burn the Lambda's 120s
# timeout. Hitting it means the guard is INCOMPLETE — treated exactly like an
# API failure (never silently truncated into a false "nothing in flight").
GUARD_MAX_PAGES = 20

dynamo = boto3.resource("dynamodb", region_name=REGION)
glue   = boto3.client("glue",       region_name=REGION)


def resource_name(env_var: str) -> str:
    """Resolve one resource name at call time (see #15 note above)."""
    return os.environ.get(env_var) or _NAME_FOR[env_var]()


def _horizon_minutes(configs) -> int:
    """How far back the run-history walk must look to be authoritative.

    A run cannot still be in flight once it has outlived its Glue timeout, so
    the horizon is the LARGEST timeout any scanned config could have handed to
    StartJobRun (both the CDC and full_load profiles), floored at the heavy
    default and padded by GUARD_HORIZON_MARGIN_MINUTES. Derived from the
    configs rather than hardcoded because a pipeline is free to raise its own
    timeout, and a horizon shorter than a real timeout would let the walk stop
    before reaching a run that is genuinely still running."""
    longest = 0
    for cfg in configs:
        glue_cfg = cfg.get("glue") or {}
        for block in (glue_cfg, glue_cfg.get("full_load") or {}):
            try:
                longest = max(longest, int(block.get("timeout_minutes") or 0))
            except (TypeError, ValueError):
                continue          # validated at sync time; ignore junk here
    return (max(longest, FULL_LOAD_DEFAULTS["timeout_minutes"])
            + GUARD_HORIZON_MARGIN_MINUTES)


def _run_started_before(run, cutoff) -> bool:
    """True only when we can PROVE this run started before cutoff.

    Used solely to decide whether the newest-first walk may stop early. A run
    with no usable StartedOn — a WAITING run queued behind the concurrency
    limit may not have one yet — returns False, so it never shortens the walk.
    Uncertainty must never remove a run from the guard; it may only decline to
    end the walk."""
    started = run.get("StartedOn")
    if not started:
        return False
    try:
        return started < cutoff
    except TypeError:
        return False              # naive/aware mismatch: treat as unknown


def _is_maintenance(run) -> bool:
    """A --refresh_columns / --rebuild run: blocks the guard, never satisfies a
    schedule slot. See MAINTENANCE_ARGS."""
    args = run.get("Arguments") or {}
    return any(k in args for k in MAINTENANCE_ARGS)


def _job_run_facts(job_name: str, horizon_minutes: int,
                   arg_key: str = "--gold_id"):
    """One walk of job_name's recent run history, returning four things:

      in_flight  gold_ids with an unfinished run (IN_FLIGHT_STATES)
      newest     gold_id -> its most recent run in ANY state, maintenance
                 included. get_job_runs returns newest-first, so the first
                 sighting wins. Feeds the failure cooldown.
      scheduled  gold_id -> its most recent NON-maintenance run. Feeds the
                 incremental's due-ness, which a column repair must not
                 satisfy.
      maintenance gold_id -> its most recent MAINTENANCE run. The exact
                 complement, and it feeds the REFRESH sweep's due-ness:
                 kept so a manual repair still blocks the in-flight guard
                 slot, and an incremental is not evidence that it ran.
      complete   False when the walk could not finish (API failure, or the page
                 backstop). The caller turns that into a refusal to dispatch.

    One walk, four answers, because the walk is the expensive part: it is a
    paginated Glue API call and this runs 288 times a day.

    The walk is BOUNDED (UIFPE-149). It used to paginate the job's entire
    history on every tick, twice per tick, against a history that only grows.
    At sweep cadence that is what throttles — and a throttled guard used to mean
    no guard at all."""
    in_flight, newest, scheduled, maintenance = set(), {}, {}, {}
    complete = True
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=horizon_minutes)
    paginator = glue.get_paginator("get_job_runs")
    try:
        pages = 0
        for page in paginator.paginate(JobName=job_name, MaxResults=100):
            pages += 1
            runs = page.get("JobRuns") or []
            for run in runs:
                gold_id = (run.get("Arguments") or {}).get(arg_key)
                if not gold_id:
                    continue
                newest.setdefault(gold_id, run)
                if _is_maintenance(run):
                    maintenance.setdefault(gold_id, run)
                else:
                    scheduled.setdefault(gold_id, run)
                if run.get("JobRunState") in IN_FLIGHT_STATES:
                    in_flight.add(gold_id)
            # Newest-first: once an ENTIRE page predates the horizon, no later
            # page can hold a run that is still running.
            if runs and all(_run_started_before(r, cutoff) for r in runs):
                break
            if pages >= GUARD_MAX_PAGES:
                complete = False
                logger.warning(
                    f"[GOLD_DISPATCH_GUARD_INCOMPLETE] job={job_name} run "
                    f"history exceeded {GUARD_MAX_PAGES} pages without "
                    f"reaching the {horizon_minutes}-minute horizon — the "
                    f"in-flight guard for this tick is NOT authoritative."
                )
                break
    except Exception as e:
        complete = False
        logger.warning(
            f"[GOLD_DISPATCH_GUARD_INCOMPLETE] could not enumerate job runs "
            f"for job={job_name} ({type(e).__name__}: {e}) — the in-flight "
            f"guard for this tick is NOT authoritative."
        )
    return in_flight, newest, scheduled, maintenance, complete


def _guarded_ids(job_names, horizon_minutes: int, facts_for: str = None):
    """gold_ids that must not be dispatched: the union of unfinished runs
    across ALL gold job names.

    Review finding #8 / #1's dispatcher half: the guard used to check only the
    job it was about to start, so `gold-reconcile` at 03:30 and a still-running
    `gold-transform` could overlap on one gold_id. That is precisely the
    reconcile/transform race — reconcile anti-joins Silver against Gold while
    the transform is writing Gold. Transform and reconcile are therefore
    mutually exclusive PER gold_id, across both job names.

    Returns (guarded, newest, scheduled, maintenance, complete). `guarded` is
    the cross-job union; `newest` and `scheduled` are for the ONE job named by
    facts_for, because both the cooldown and the due-ness decision concern the
    job about to start — a failed or recent reconcile is not a reason to hold
    back a transform, nor evidence that a transform ran."""
    guarded, newest_for, scheduled_for, maint_for = set(), {}, {}, {}
    complete = True
    for job_name in dict.fromkeys(job_names):  # de-dupe, keep order
        ids, newest, scheduled, maint, ok = _job_run_facts(
            job_name, horizon_minutes)
        guarded |= ids
        complete = complete and ok
        if job_name == facts_for:
            newest_for, scheduled_for, maint_for = newest, scheduled, maint
    return guarded, newest_for, scheduled_for, maint_for, complete


def _resolved_schedule(cfg):
    """(frequency, start_time, interval_minutes) after defaults and aliases.

    Never raises and never returns a partial answer: a malformed field falls
    back to the cadence default rather than dropping the pipeline out of the
    sweep. A typo in one config must not cost that pipeline every run until
    someone notices — gold_config.validate is where a bad value is REJECTED, at
    sync time, where a human is watching."""
    schedule = cfg.get("schedule") or {}
    frequency = normalize(schedule.get("frequency") or DEFAULT_FREQUENCY)
    interval = interval_minutes(frequency)
    if interval is None:
        frequency = DEFAULT_FREQUENCY
        interval = interval_minutes(frequency)
    start = schedule.get("start_time")
    if parse_time_of_day(start) is None:
        start = default_start_time(frequency)
    return frequency, start, interval


def _resolved_weekly_days(cfg):
    """The weekly rung's day set (UIFPE-532), defaulted like start_time: a
    malformed value must not drop the entity from the sweep. None for every
    other rung, so previous_slot ignores it."""
    frequency, _start, _interval = _resolved_schedule(cfg)
    if not is_weekly(frequency):
        return None
    schedule = cfg.get("schedule") or {}
    return parse_weekly_days(schedule.get("weekly_days")) or DEFAULT_WEEKLY_DAYS


def _due_skip(cfg, scheduled_run, now) -> str:
    """None when this pipeline is due, else the reason it is not.

    THE scheduling decision (UIFPE-149). Due when the last scheduled run started
    before the most recent slot — see gold_frequencies.previous_slot for why
    slots rather than elapsed time.

    A pipeline that has never run is always due; that is also how a first-ever
    full load gets triggered (the capacity profile is chosen separately, off
    target-table existence)."""
    frequency, start, _interval = _resolved_schedule(cfg)
    weekly_days = _resolved_weekly_days(cfg)
    slot = previous_slot(now, start, frequency, weekly_days)
    if slot is None:                      # unreachable via _resolved_schedule
        return None
    if not scheduled_run:
        return None                       # never run — always due
    started = scheduled_run.get("StartedOn")
    if not started:
        # An unfinished run with no StartedOn is already blocked by the
        # in-flight guard; anything else with no timestamp is unreadable, and
        # dispatching costs one run that the hash gate no-ops.
        return None
    try:
        if started < slot:
            return None
    except TypeError:
        return None                       # naive/aware mismatch: dispatch
    days = f", weekly_days {list(weekly_days)}" if weekly_days else ""
    return (f"last run {started:%Y-%m-%dT%H:%M}Z is at or after the current "
            f"{frequency} slot {slot:%Y-%m-%dT%H:%M}Z "
            f"(start_time {start} UTC{days})")


def _cooldown_skip(cfg, newest_run) -> str:
    """Reason string when this gold_id's newest run failed recently, else None.

    Why a cooldown exists at all: the in-flight guard blocks unfinished runs,
    never failed ones (deliberately — and pinned by
    test_terminal_states_do_not_block). At daily cadence a broken pipeline
    retries once a day. On gold-5m it starts 288 runs a day, doubled by
    max_retries=1, at 8-16 DPU, and the only signal is the invoice.

    A cooldown rather than block-until-success (framework owner, 2026-08-11):
    block-until-success wedges a pipeline permanently after one transient
    failure and needs a human to clear it, which is worse than a slow retry.

    UNCERTAINTY DOES NOT BLOCK HERE — the opposite of the in-flight guard. If
    the run carries no end timestamp we dispatch, because this is a cost
    optimization rather than a correctness guard, and a cooldown that fires on
    no evidence would silently stall healthy pipelines."""
    if not newest_run:
        return None
    state = newest_run.get("JobRunState")
    if state not in FAILURE_STATES:
        return None
    try:
        minutes = int((cfg.get("schedule") or {}).get(
            "failure_cooldown_minutes", DEFAULT_COOLDOWN_MINUTES))
    except (TypeError, ValueError):
        minutes = DEFAULT_COOLDOWN_MINUTES
    if minutes <= 0:
        return None                       # explicit opt-out
    ended = newest_run.get("CompletedOn") or newest_run.get("LastModifiedOn")
    if not ended:
        return None                       # no evidence — never guess
    try:
        age = (datetime.now(timezone.utc) - ended).total_seconds() / 60.0
    except TypeError:
        return None                       # naive/aware mismatch
    if age < minutes:
        return (f"newest run is {state}, ended {age:.0f} min ago; "
                f"failure_cooldown_minutes={minutes} — holding off")
    return None


# Heavy-profile constants — the fallback when a config's glue.full_load block
# is missing or partial. Deliberately the CURRENT production sizing, never the
# lean block: an initial 193.7M-row CTAS launched undersized times out, retries
# at the same size (max_retries=1), and repeats next cadence. False-heavy is
# cost; false-lean is an outage loop.
FULL_LOAD_DEFAULTS = {"worker_type": "G.2X", "num_workers": 16,
                      "timeout_minutes": 180}
# Lean fallbacks when glue keys are missing. Timeout was previously 60 — too
# tight for a first-ever incremental against a large source (review F2).
CDC_DEFAULTS = {"worker_type": "G.2X", "num_workers": 16,
                "timeout_minutes": 180}


def _resolve_env_token(value: str) -> str:
    """Config identifiers are env-tokenized (uif_echo_{env}_silver_mdm.…).
    gold_cdc.resolve_env is NOT in the Lambda zip, so the substitution is
    inlined here; qe/test_gold_dispatcher.py pins parity with the runtime."""
    env = os.environ.get("ENV")
    if not env:
        raise RuntimeError("ENV is not set — cannot resolve {env} tokens")
    return value.replace("{env}", env)


def _metadata_location(database: str, name: str):
    """Iceberg table pointer from the Glue catalog — changes on every commit.
    Raises on any API failure; callers decide the fail-open behavior."""
    t = glue.get_table(DatabaseName=database, Name=name)["Table"]
    return (t.get("Parameters") or {}).get("metadata_location")


def _target_table_exists(cfg) -> bool:
    """True/False from the catalog; None when the check itself failed.
    The not-found case is matched by exception NAME rather than the class on
    the client (`glue.exceptions.EntityNotFoundException`): botocore builds
    those classes per-client, and the class attribute is not an exception type
    under the unit tests' mocked client."""
    try:
        db = _resolve_env_token(cfg["target"]["database"])
        glue.get_table(DatabaseName=db, Name=cfg["target"]["table"])
        return True
    except Exception as e:
        code = (getattr(e, "response", None) or {}).get("Error", {}).get("Code")
        if type(e).__name__ == "EntityNotFoundException" \
                or code == "EntityNotFoundException":
            return False
        logger.warning(f"[TARGET CHECK FAILED] gold_id={cfg.get('gold_id')} "
                       f"({type(e).__name__}: {e})")
        return None


def _gate_skips(cfg) -> bool:
    """Snapshot pre-gate (F1): True only when EVERY source's live Iceberg
    metadata_location equals the map the last successful transform stored
    (watermark.silver_snapshots). Comparison is equality — the strings carry
    no order — and every uncertain case dispatches:
      - gate disabled per config, no stored map, alias missing from the map,
        live pointer unreadable, sources list changed: dispatch.
    A skip requires positive evidence of no change on all sources; anything
    less costs one Glue run that the hash gate then no-ops. The caller wraps
    this fail-open — a raise here must never block dispatch."""
    if cfg.get("snapshot_gate", True) is False:
        return False
    stored = (cfg.get("watermark") or {}).get("silver_snapshots") or {}
    sources = cfg.get("sources") or []
    if not stored or not sources:
        return False
    for src in sources:
        alias = src.get("alias")
        stored_loc = stored.get(alias)
        if not stored_loc:
            return False          # new/renamed source — must dispatch
        db, tbl = _resolve_env_token(src["silver_table"]).split(".", 1)
        live = _metadata_location(db, tbl)
        if not live or live != stored_loc:
            return False
    return True


def _capacity_args(cfg, frequency: str, first_run: bool) -> dict:
    """StartJobRun capacity kwargs (F2). Profile ownership:
      - reconcile: the lean glue block, reconcile's own num_workers fallback —
        explicitly the lean profile, chosen not inherited.
      - first run (target table absent): glue.full_load, falling back to
        FULL_LOAD_DEFAULTS key-by-key — never to the lean block.
      - CDC: the glue block's capacity keys.
    Only capacity keys live in profiles; shuffle_partitions and
    max_affected_fraction stay at the top of `glue`, read by the jobs on
    every run regardless of profile."""
    glue_cfg = cfg.get("glue", {}) or {}
    if frequency == RECONCILE_FREQUENCY:
        profile, defaults = glue_cfg, dict(CDC_DEFAULTS, num_workers=4)
    elif first_run:
        base = glue_cfg.get("full_load") or {}
        profile, defaults = base, FULL_LOAD_DEFAULTS
    else:
        profile, defaults = glue_cfg, CDC_DEFAULTS
    return {
        "WorkerType": profile.get("worker_type", defaults["worker_type"]),
        "NumberOfWorkers": int(profile.get("num_workers",
                                           defaults["num_workers"])),
        "Timeout": int(profile.get("timeout_minutes",
                                   defaults["timeout_minutes"])),
    }


def _scan_all(table, **scan_kwargs) -> list:
    """Every matching item, following LastEvaluatedKey.

    Review finding #7: a single table.scan() returns at most 1 MB of items, and
    FilterExpression is applied AFTER that limit — so an unpaginated scan
    silently stops dispatching pipelines once the config table grows past one
    page, and can even return zero items on a page whose rows all fail the
    filter. Mirrors sync_pipelines.py's loop."""
    items, pages = [], 0
    while True:
        page = table.scan(**scan_kwargs)
        items.extend(page.get("Items", []))
        pages += 1
        last_key = page.get("LastEvaluatedKey")
        if not last_key:
            break
        scan_kwargs["ExclusiveStartKey"] = last_key
    if pages > 1:
        logger.info(f"[SCAN] {len(items)} config items over {pages} pages")
    return items


def _in_scope(cfg, payload: str) -> bool:
    """Is this config a candidate for this sweep at all?

    For a transform sweep EVERY enabled pipeline is a candidate — which of them
    actually runs is the due-ness decision, not a payload match. That is the
    UIFPE-149 change: cadence stopped being a property of the RULE and became a
    property of the pipeline.

    Reconcile keeps its own opt-in."""
    if payload == RECONCILE_FREQUENCY:
        return bool((cfg.get("reconcile") or {}).get("enabled", False))
    # The recompute leg is decided per RUN inside the transform sweep, not by a
    # payload of its own (ADR-0020 decision 4), so there is no candidacy test.
    return True


def _resolved_recompute(cfg):
    """(columns, every_n_runs) for this pipeline's recompute leg, or None when
    it declares none.

    WHY THE LEG EXISTS (ADR-0020 decision 5). Two things change the correct Gold
    value while leaving nothing for a timestamp scan to find: a value defined as
    "effective now", which moves when a date boundary passes, and a hard delete,
    which stamps nothing. Detection is blind to both by construction, so the leg
    is the only thing that catches them.

    It is NOT --rebuild: a rebuild writes no change-log rows, so it would repair
    the table while telling no consumer. It is not --refresh_columns either —
    that argument marks a run as maintenance, which would stop it satisfying its
    schedule slot and suppress the announce.

    Placed by RATIO, not by a cadence of its own: one schedule per entity
    (decision 4), and the leg rides it once every N runs."""
    recompute = ((cfg.get("schedule") or {}).get("recompute") or {})
    cols = recompute.get("undetectable_columns") or []
    if isinstance(cols, str):                 # tolerate a comma string
        cols = [c.strip() for c in cols.split(",") if c.strip()]
    cols = [str(c) for c in cols if str(c).strip()]
    if not cols:
        return None
    every = recompute.get("every_n_runs", DEFAULT_RECOMPUTE_EVERY_N_RUNS)
    try:
        every = int(every)
    except (TypeError, ValueError):
        every = DEFAULT_RECOMPUTE_EVERY_N_RUNS
    if every < 1:
        every = DEFAULT_RECOMPUTE_EVERY_N_RUNS
    return cols, every


def _recompute_due(cfg, last_recompute_at, now) -> bool:
    """True when the leg's own slot has passed since it last ran.

    The same rule as the schedule itself — slots, not elapsed time — over a grid
    of (the entity's interval x N). So the leg inherits the drift-free phase and
    the tolerance of a skipped sweep, and needs no counter that a missed run
    would shift permanently. Never having run counts as due, which is also how a
    newly-declared leg gets its first pass."""
    resolved = _resolved_recompute(cfg)
    if not resolved:
        return False
    _cols, every = resolved
    _frequency, start, interval = _resolved_schedule(cfg)
    if not interval:
        return False
    slot = previous_slot_at_interval(now, start, interval * every)
    if slot is None:
        return False
    if not last_recompute_at:
        return True                       # never run — always due
    try:
        stamped = datetime.fromisoformat(str(last_recompute_at))
    except ValueError:
        return True                       # unparseable marker: repair, do not skip
    if stamped.tzinfo is None:
        stamped = stamped.replace(tzinfo=timezone.utc)
    try:
        return stamped < slot
    except TypeError:
        return True


def _emit_tick_telemetry(items, frequency, now, triggered):
    """One EMF document per enabled transform entity per sweep (UIFPE-543).

    Freshness reads metadata.last_transform_at, which only the transform
    stamps: metadata.last_run_at is ALSO written by the nightly reconcile
    and would hide a failing transform for a day. A row the new transform
    has not touched yet falls back to last_run_at, and the document names
    the attribute it used. Never raises - telemetry must not cost a
    dispatch. Printed, not logged: a logger prefix in front of the JSON
    stops CloudWatch reading it as a metric."""
    if frequency == RECONCILE_FREQUENCY:
        return
    dispatched = {t["gold_id"] for t in triggered}
    env = os.environ.get("ENV") or os.environ.get("ENVIRONMENT") or "unknown"
    for cfg in items:
        gold_id = cfg.get("gold_id")
        if not gold_id:
            continue
        try:
            freq, _start, interval = _resolved_schedule(cfg)
            meta = cfg.get("metadata") or {}
            source = ("last_transform_at" if meta.get("last_transform_at")
                      else "last_run_at")
            emit(tick_document(
                env, gold_id, frequency=freq, interval_minutes=interval,
                last_success_at=meta.get(source),
                bookmark=(cfg.get("watermark") or {}).get("bookmark"),
                dispatched=gold_id in dispatched, now=now,
                freshness_source=source))
        except Exception as e:
            logger.warning(f"[TELEMETRY SKIPPED] gold_id={gold_id} "
                           f"{type(e).__name__}: {e}")


def dispatch_gold(frequency: str) -> dict:
    """Scan gold configs and start the Glue runs that are due."""
    table = dynamo.Table(resource_name("GOLD_CONFIG_TABLE"))
    triggered, skipped = [], []
    now = datetime.now(timezone.utc)

    transform_job = resource_name("GOLD_JOB_NAME")
    reconcile_job = resource_name("GOLD_RECONCILE_JOB_NAME")
    job_name = reconcile_job if frequency == RECONCILE_FREQUENCY else transform_job

    items = _scan_all(
        table,
        FilterExpression=Attr("enabled").eq(True) & Attr("version").eq("latest"),
    )

    # Configs are scanned BEFORE the guard (UIFPE-149): the walk's look-back
    # horizon is derived from their Glue timeouts, so it cannot be computed
    # until they are in hand.
    horizon = _horizon_minutes(items)
    # Cross-job, per-gold_id exclusion — not just this job's own runs.
    (active, newest_runs, scheduled_runs, _maint_runs,
     guard_complete) = _guarded_ids([transform_job, reconcile_job],
                                    horizon, facts_for=job_name)

    # FAIL CLOSED, for every cadence. Before UIFPE-149 this path dispatched
    # anyway ("a missed cadence is worse than a rare stacked run"), which was the
    # right call when a missed tick meant a missed RUN — the rule fired once a
    # day and there was no second chance.
    #
    # Slot-based due-ness removed that dilemma. A skipped sweep does not lose the
    # slot: the next sweep, five minutes later, computes the same previous_slot,
    # still sees the last run before it, and dispatches. So refusing to dispatch
    # blind now costs minutes of lateness instead of a whole run, while
    # dispatching blind still risks two writers on one Gold table and watermark.
    # There is no longer a cadence for which fail-open is the better trade.
    if not guard_complete:
        logger.error(
            f"[GOLD_DISPATCH_GUARD_FAIL_CLOSED] payload={frequency}: the "
            f"in-flight guard could not be verified, so NOTHING is dispatched "
            f"this sweep. Slot-based scheduling means nothing is LOST — the next "
            f"sweep retries the same slot — but while this repeats, gold is not "
            f"running. Alarm on this string.")
        summary = {"frequency": frequency, "job_name": job_name,
                   "triggered": [],
                   "skipped": [{"gold_id": c.get("gold_id", "<missing gold_id>"),
                                "reason": "guard_unavailable"}
                               for c in items if _in_scope(c, frequency)]}
        # Still measured: a sweep that dispatches nothing is exactly when
        # the freshness signal has to keep counting.
        _emit_tick_telemetry(items, frequency, now, summary["triggered"])
        return summary

    for cfg in items:
        # Per-pipeline isolation (review F1): one malformed config or one
        # failing catalog call must cost that pipeline's tick, never abort
        # dispatch for every other pipeline in the loop.
        gold_id = cfg.get("gold_id", "<missing gold_id>")
        try:
            if not _in_scope(cfg, frequency):
                logger.info(f"[SKIPPED] gold_id={gold_id} reconcile disabled")
                continue

            # THE schedule decision. Reconcile keeps its own fixed nightly rule,
            # so only transforms are slot-scheduled. One grid per entity
            # since ADR-0020 decision 4.
            if frequency != RECONCILE_FREQUENCY:
                not_due = _due_skip(cfg, scheduled_runs.get(gold_id), now)
            else:
                not_due = None
            if not_due:
                logger.info(f"[SKIPPED] gold_id={gold_id} not due — {not_due}")
                continue

            if gold_id in active:
                logger.info(
                    f"[SKIPPED] gold_id={gold_id} unfinished gold run in-flight "
                    f"(transform or reconcile) — not starting {job_name}")
                skipped.append({"gold_id": gold_id, "reason": "in_flight"})
                continue

            # Failure cooldown (UIFPE-149) — checked AFTER the in-flight guard,
            # which is the correctness-critical one, and BEFORE the catalog
            # probes below, which are pointless if this tick is skipping.
            cooldown = _cooldown_skip(cfg, newest_runs.get(gold_id))
            if cooldown:
                logger.info(f"[SKIPPED] gold_id={gold_id} {cooldown}")
                skipped.append({"gold_id": gold_id,
                                "reason": "failure_cooldown"})
                continue

            # First-run detection (F2): table existence is authoritative;
            # last_run_id is only a heavy-profile hint when the check itself
            # errored (it is also written by reconcile, and a SUCCEEDED run
            # can skip it after losing the guarded write — review F2).
            first_run = False
            # Bound before the branch: reconcile never computes a leg, and the
            # job-args block below reads this on every path.
            recompute_due = False
            if frequency != RECONCILE_FREQUENCY:
                exists = _target_table_exists(cfg)
                if exists is False:
                    first_run = True
                elif exists is None:
                    first_run = (cfg.get("metadata", {})
                                 .get("last_run_id") in (None, "None"))

                # ADR-0020 decision 5: is the recompute leg due this run?
                # Never on a first run — the first load computes every column
                # fresh, so a leg inside it is pure waste. This replaces the old
                # first_load_pending skip: with one grid there is no second
                # sweep left to race the first load.
                recompute_due = (
                    not first_run
                    and _recompute_due(
                        cfg, (cfg.get("watermark") or {}).get(
                            "last_recompute_at"), now))

                # Snapshot pre-gate (F1) — never for a first run, and never when
                # the leg is due: the leg repairs staleness that no pointer move
                # ever signals, so a matching snapshot map must not skip it.
                if exists is True and not recompute_due:
                    try:
                        if _gate_skips(cfg):
                            logger.info(
                                f"[GATE SKIPPED] gold_id={gold_id} every "
                                f"source's Iceberg metadata_location matches "
                                f"the last successful run — no Silver commit "
                                f"since; not starting {job_name}")
                            skipped.append({"gold_id": gold_id,
                                            "reason": "no_silver_change"})
                            continue
                    except Exception as e:
                        logger.warning(
                            f"[GOLD_GATE_FAIL_OPEN] gold_id={gold_id} snapshot "
                            f"gate errored ({type(e).__name__}: {e}) — "
                            f"dispatching WITHOUT gate protection. Alarm on "
                            f"this string if it persists.")
                elif exists is None and not recompute_due and not first_run:
                    # The probe failed on a pipeline that HAS run before, so the
                    # gate was applicable and could not even be attempted. Say
                    # it with the string dispatcher.tf tells operators to alarm
                    # on: silence here is what hid UIFPE-498, where a stale IAM
                    # scope disabled the gate estate-wide unnoticed.
                    logger.warning(
                        f"[GOLD_GATE_FAIL_OPEN] gold_id={gold_id} target probe "
                        f"was inconclusive, so the snapshot gate never ran — "
                        f"dispatching WITHOUT gate protection. Alarm on this "
                        f"string if it persists.")

            capacity = _capacity_args(cfg, frequency, first_run)
            if first_run:
                logger.info(f"[FULL-LOAD PROFILE] gold_id={gold_id} target "
                            f"table absent — using {capacity}")

            job_args = {"--gold_id": gold_id}
            leg_cols = []
            if recompute_due:
                # NOT --refresh_columns: that argument would mark the run
                # maintenance, so it would never satisfy this slot and would
                # suppress the announce every scheduled run owes.
                leg_cols, _every = _resolved_recompute(cfg)
                job_args["--recompute_columns"] = ",".join(leg_cols)
            resp = glue.start_job_run(
                JobName=job_name,
                Arguments=job_args,
                **capacity,
            )
            triggered.append({"gold_id": gold_id, "run_id": resp["JobRunId"]})
            # The resolved schedule is logged on every dispatch, not just on a
            # change: when someone asks "why did this run at 14:30", the answer
            # has to be in the log for THAT run rather than inferred from a
            # config that may since have been edited.
            freq, start, _iv = _resolved_schedule(cfg)
            # After the collapse to one sweep this line is the ONLY per-run
            # record that the leg fired — there is no separate Glue run left to
            # point at.
            leg = f"yes({len(leg_cols)} cols)" if leg_cols else "no"
            logger.info(f"[TRIGGERED] gold_id={gold_id} job={job_name} "
                        f"run_id={resp['JobRunId']} schedule={freq}"
                        f"@{format_time_of_day(parse_time_of_day(start))}Z "
                        f"recompute_leg={leg}")
        except Exception as e:
            logger.error(f"[PIPELINE FAILED] gold_id={gold_id} "
                         f"job={job_name}: {type(e).__name__}: {e}")
            skipped.append({"gold_id": gold_id, "reason": str(e)})

    _emit_tick_telemetry(items, frequency, now, triggered)
    return {"frequency": frequency, "job_name": job_name,
            "triggered": triggered, "skipped": skipped}


def handler(event, context=None):
    """EventBridge entry point. Two sweep kinds, many accepted payloads.

    Since UIFPE-149 the payload no longer selects a cadence — the pipelines do,
    via schedule.frequency + schedule.start_time. So EVERY transform payload
    collapses to one sweep that dispatches whatever is due. The per-cadence names
    and their legacy aliases stay accepted for exactly one reason: during a
    partially-applied Terraform run an old rule may still be firing, and it must
    keep gold running rather than raise on every tick."""
    requested = (event or {}).get("frequency")
    if requested == RECONCILE_FREQUENCY:
        payload = RECONCILE_FREQUENCY
    elif is_sweep(requested):
        payload = TICK_FREQUENCY
    else:
        raise ValueError(
            f"gold_dispatcher requires frequency in {ALL_FREQUENCIES}, "
            f"got {requested!r}"
        )
    summary = dispatch_gold(payload)
    if requested != payload:
        summary["requested_frequency"] = requested
    logger.info(f"[SUMMARY] {summary}")
    return summary
