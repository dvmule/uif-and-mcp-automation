# gold_frequencies.py
# The ONE definition of the gold control plane's scheduling vocabulary and slot
# arithmetic (UIFPE-149).
#
# WHY THIS FILE EXISTS: the frequency list previously sat in four Python copies
# (the dispatcher, gold_config.validate, compile_intake_delta, and the tests)
# plus Terraform and two TypeScript unions. With two values that was tolerable.
# With eleven it is a trap — a config naming a cadence nothing understands is
# skipped FOREVER, and the only evidence is one INFO log line per tick.
# Repo rule is "extract after the third repetition"; four copies is past it.
#
# WHY IT LIVES IN scripts/: it must be importable by a BARE
# `import gold_frequencies` from three entry points, and only this directory
# satisfies all three without a per-file sys.path hack (which
# domains/product/CLAUDE.md forbids):
#   * the deployed Lambda — the artifact is a FLAT zip, so a bare import works
#     wherever the file came from. infrastructure/terraform/dispatcher.tf
#     archives it from HERE with a bare `filename`;
#   * `py src/apps/scripts/sync_pipelines.py` — Python puts the script's own
#     directory on sys.path[0], so gold_config.py's bare import resolves;
#   * pytest — qe/conftest.py puts both scripts/ and lambda_dispatcher/ on
#     sys.path.
# Keep it free of boto3 and Spark: it is imported by a 128 MB Lambda on every
# tick and must stay trivially unit-testable. datetime is stdlib and fine.
#
# The TypeScript copy in ui/src/lib/pipelines.ts cannot import this module. It
# is pinned by qe/test_gold_frequency_parity.py, and the slot arithmetic — which
# both sides implement — is pinned by a SHARED VECTOR FILE,
# qe/fixtures/schedule-slots.json, asserted by pytest and vitest alike.

from datetime import datetime, timedelta

# ── The ladder ───────────────────────────────────────────────────────────────
# Ordered fastest → slowest; dict order is insertion order (3.7+), and that
# order drives the UI dropdown, so it is a contract rather than an accident.
#
# EVERY INTERVAL DIVIDES 1440 MINUTES EXACTLY (5·288, 10·144, 15·96, 30·48,
# 60·24, 120·12, 240·6, 360·4, 480·3, 720·2, 1440·1). That is the property the
# whole scheduling model rests on: a start time plus one of these intervals
# tiles the day perfectly, so the slots are uniformly spaced with NO short or
# long gap at midnight. An interval that did not divide 1440 (say 7 hours) could
# not be honored from a fixed time of day without an irregular wrap. If anyone
# ever adds one, `test_every_interval_divides_a_day` fails and this comment is
# why.
#
# `default_start_time` is the time of day a pipeline inherits when it names
# none. The values are deliberately STAGGERED so that domains which never set a
# start time do not all pile onto :00 and fan out simultaneous Glue starts
# against one concurrency ceiling. The two that matter are exact: gold-24h
# keeps 06:30 UTC (product's LIVE dev cadence, 30 min after the Silver daily)
# and gold-1h keeps :45 (after the Bronze 30-minute ticks settle). Renaming and
# re-modelling the cadences must not move a running pipeline's clock.
CADENCES = {
    "gold-5m":  {"minutes":    5, "default_start_time": "00:00",
                 "label": "every 5 minutes"},
    "gold-10m": {"minutes":   10, "default_start_time": "00:02",
                 "label": "every 10 minutes"},
    "gold-15m": {"minutes":   15, "default_start_time": "00:04",
                 "label": "every 15 minutes"},
    "gold-30m": {"minutes":   30, "default_start_time": "00:06",
                 "label": "every 30 minutes"},
    "gold-1h":  {"minutes":   60, "default_start_time": "00:45",
                 "label": "every hour"},
    "gold-2h":  {"minutes":  120, "default_start_time": "00:47",
                 "label": "every 2 hours"},
    "gold-4h":  {"minutes":  240, "default_start_time": "00:49",
                 "label": "every 4 hours"},
    "gold-6h":  {"minutes":  360, "default_start_time": "00:51",
                 "label": "every 6 hours"},
    "gold-8h":  {"minutes":  480, "default_start_time": "00:53",
                 "label": "every 8 hours"},
    "gold-12h": {"minutes":  720, "default_start_time": "00:55",
                 "label": "every 12 hours"},
    "gold-24h": {"minutes": 1440, "default_start_time": "06:30",
                 "label": "every 24 hours"},
}

MINUTES_PER_DAY = 1440

# ── Weekly: the one rung that does not tile a day (UIFPE-532) ───────────────
# A weekly entity runs on a SET of weekdays at one time of day, so its slots are
# a 7-day grid `(weekday in weekly_days, start_time)` rather than "start time +
# k x interval" — which is why it is NOT a member of CADENCES: the tiling
# invariant above (every interval divides 1440) is about day-local grids, and
# this one is week-local. `minutes` is kept for the recompute leg, whose grid is
# (interval x every_n_runs) on a fixed epoch anchor and works for any interval.
# Weekdays are ISO order, evaluated in UTC — the UI converts from the user's
# zone and stores UTC, day and time together.
WEEKLY_FREQUENCY = "gold-weekly"
WEEKLY_CADENCE = {"minutes": 7 * MINUTES_PER_DAY, "default_start_time": "06:30",
                  "label": "weekly"}
WEEKDAYS = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")
DEFAULT_WEEKLY_DAYS = ("MON",)

TRANSFORM_FREQUENCIES = tuple(CADENCES) + (WEEKLY_FREQUENCY,)

# ── Payloads vs config values ────────────────────────────────────────────────
# These are DIFFERENT vocabularies, and conflating them was the old design's
# limitation. Before UIFPE-149 each cadence was its own EventBridge rule and the
# payload named the cadence, so every pipeline on a cadence shared one clock —
# two daily tables could not run at different times.
#
# Now a pipeline declares `schedule.frequency` (a rung, above) plus
# `schedule.start_time` (its own time of day), and ONE sweep rule ticks often
# enough to serve the finest rung. The dispatcher works out which pipelines are
# due. So the payload vocabulary is just two sweep kinds:
TICK_FREQUENCY = "gold-tick"          # transform sweep — evaluates due-ness
RECONCILE_FREQUENCY = "gold-reconcile"  # orphan hard-delete detection

# The sweep must tick at least as often as the finest rung, or a gold-5m
# pipeline could not be served. It is fixed rather than derived from the
# registered configs: deriving it would make an infrastructure change a
# consequence of editing a domain YAML, and 288 invocations/day of a 128 MB
# Lambda costs pennies.
TICK_CRON = "cron(0/5 * * * ? *)"
TICK_MINUTES = 5

# There is ONE transform sweep (ADR-0020 decision 4). A second sweep for the
# periodic recompute was retired with UIFPE-457: it needed its own rule, its own
# payload and its own slot grid, and the two grids had to stay mutually
# exclusive or each would satisfy the other's slot and both would silently stop.
# The recompute now rides the entity's own scheduled run once every N runs, so
# the ratio is the only thing declared and there is no second clock to reconcile.

RECONCILE_CRON = "cron(30 3 * * ? *)"  # 03:30 UTC, 30 min after Silver reconcile

# Payloads the handler accepts. The cadence names and their legacy aliases are
# ALSO accepted and treated as a transform sweep — during a partially-applied
# Terraform run an old per-cadence rule may still be firing, and it must keep
# driving transforms rather than erroring out on every tick.
SWEEP_PAYLOADS = ((TICK_FREQUENCY,) + TRANSFORM_FREQUENCIES
                  + ("gold", "gold-hourly", "gold-60m"))
ALL_FREQUENCIES = SWEEP_PAYLOADS + (RECONCILE_FREQUENCY,)

# Refresh mode — for the MANUAL repair tool (--refresh_columns), which survives
# the sweep's retirement: it is what an operator runs after a systemic hash
# movement such as a physical column rename, and `silent` is why that repair can
# be made without writing a change-log row for every entity in the table.
# `emit` writes op=REFRESH change-log rows per moved channel, so
# consumers learn the values changed; `silent` repairs the table and tells
# nobody. `emit` is the default ON PURPOSE — the whole reason a targeted refresh
# is used instead of --rebuild is that --rebuild writes no change-log rows and
# would repair the table while notifying no consumer.
REFRESH_MODES = ("emit", "silent")
DEFAULT_REFRESH_MODE = "emit"

# ADR-0020 decision 5: the recompute leg runs once every N scheduled runs, as a
# RATIO rather than a cadence of its own so the relationship survives a change
# to the entity's cadence — 8 gives two hours at a 15-minute cadence and four at
# 30. The leg exists only for what no timestamp scan can see (a date boundary
# passing, a hard delete), so N bounds how long those may go unreflected.
DEFAULT_RECOMPUTE_EVERY_N_RUNS = 8

# The cadence a config with no `schedule.frequency` inherits. Was the literal
# "gold" before UIFPE-149; it resolves to the same clock, so this is a rename,
# not a behavior change.
DEFAULT_FREQUENCY = "gold-24h"

# How long the dispatcher holds off after a FAILED/TIMEOUT/ERROR/STOPPED run,
# when a config does not set schedule.failure_cooldown_minutes. 0 in a config is
# an explicit opt-out.
#
# It lives here, not in gold_dispatcher.py, because gold_config.validate and
# auto_init both need it and must not import a boto3-laden module to get it.
#
# Why any cooldown at all: the in-flight guard blocks unfinished runs and never
# failed ones (deliberately, and separately tested). At daily cadence a broken
# pipeline retries once a day. On gold-5m it starts 288 runs a day, doubled by
# the Glue job's max_retries=1, at 8-16 DPU — and the first signal is the bill.
DEFAULT_COOLDOWN_MINUTES = 30

# ── Legacy aliases ───────────────────────────────────────────────────────────
# NOT cosmetic — these prevent a SILENTLY MISSED RUN.
#
# Product is live in dev with `schedule.frequency: gold` in its synced DynamoDB
# item, and the documented deploy order (domains/product/CLAUDE.md) applies
# Terraform at step 2 but does not sync configs until step 4. In that window a
# NEW dispatcher reads OLD configs. Without normalization those pipelines would
# resolve to no known cadence.
#
# `gold-60m` is here because the original request listed both "every 60 mins"
# and "every 1 hr". They are one interval; two names would split configs across
# two identically-scheduled rungs, so 60m is an alias rather than a rung.
#
# LIFECYCLE: `pipelines/base/*.yaml` carries no legacy value as of UIFPE-149,
# so this table exists purely for already-deployed DynamoDB items. It can be
# deleted once EVERY environment has been re-synced — not before.
LEGACY_ALIASES = {
    "gold":        "gold-24h",
    "gold-hourly": "gold-1h",
    "gold-60m":    "gold-1h",
}


def normalize(frequency):
    """Canonical cadence name, resolving legacy aliases.

    Unknown values pass through unchanged so callers can reject them with a
    message naming what they actually received."""
    return LEGACY_ALIASES.get(frequency, frequency)


def is_transform_frequency(frequency):
    return normalize(frequency) in TRANSFORM_FREQUENCIES


def is_valid(frequency):
    return normalize(frequency) in ALL_FREQUENCIES


def is_sweep(payload):
    """True when this EventBridge payload means "evaluate transform due-ness"."""
    return payload in SWEEP_PAYLOADS


def cron(payload):
    """The EventBridge expression for a sweep payload, for the parity gate."""
    if payload == TICK_FREQUENCY:
        return TICK_CRON
    if payload == RECONCILE_FREQUENCY:
        return RECONCILE_CRON
    return None


def _entry(frequency):
    """The rung's table row: a CADENCES member, or the weekly rung."""
    freq = normalize(frequency)
    if freq == WEEKLY_FREQUENCY:
        return WEEKLY_CADENCE
    return CADENCES.get(freq)


def is_weekly(frequency):
    return normalize(frequency) == WEEKLY_FREQUENCY


def interval_minutes(frequency):
    entry = _entry(frequency)
    return entry["minutes"] if entry else None


def label(frequency):
    if normalize(frequency) == RECONCILE_FREQUENCY:
        return "nightly reconcile"
    entry = _entry(frequency)
    return entry["label"] if entry else None


def default_start_time(frequency):
    entry = _entry(frequency)
    return entry["default_start_time"] if entry else None


def parse_weekly_days(value):
    """A weekly entity's day set -> tuple in ISO order, or None if unusable.

    Accepts a list/tuple/set of the seven tokens, case-insensitive, duplicates
    dropped. None for anything else — including an empty set, which would be
    an entity that never runs. As with parse_time_of_day, the CALLER decides
    whether None is a validation error (gold_config, the console route) or a
    reason to fall back to the default (the dispatcher)."""
    if isinstance(value, str) or not isinstance(value, (list, tuple, set, frozenset)):
        return None
    seen = set()
    for v in value:
        if not isinstance(v, str) or v.strip().upper() not in WEEKDAYS:
            return None
        seen.add(v.strip().upper())
    if not seen:
        return None
    return tuple(d for d in WEEKDAYS if d in seen)


# ── Start times and slots ────────────────────────────────────────────────────

def parse_time_of_day(value):
    """"HH:MM" -> minutes since midnight UTC, or None if unparseable.

    Returning None rather than raising: this is called on config values written
    by humans and by the wizard, and the CALLER decides whether a bad value is a
    validation error (gold_config) or a reason to fall back to the cadence
    default (the dispatcher, which must never crash a whole sweep over one
    malformed field).

    STRICTLY one or two digits per part, and both parts required. The rule is
    tight on purpose: this function has a TypeScript twin in ui/src/lib/
    pipelines.ts, and loose parsing is where the two drifted — ":30" was rejected
    here (int("") raises) but accepted there as 00:30, because JavaScript's
    Number("") is 0. The shared vector in qe/fixtures/schedule-slots.json pins
    the agreement; this docstring is why the rule looks pedantic."""
    if not isinstance(value, str):
        return None
    parts = value.strip().split(":")
    if len(parts) != 2 or not all(
            p.isdigit() and 1 <= len(p) <= 2 for p in parts):
        return None
    hh, mm = int(parts[0]), int(parts[1])
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        return None
    return hh * 60 + mm


def format_time_of_day(minutes):
    return f"{(minutes // 60) % 24:02d}:{minutes % 60:02d}"


def slot_minutes(start_time, frequency):
    """Every slot in a day, as minutes since midnight, ascending.

    The slots are all times congruent to the start time modulo the interval —
    which tiles the day exactly because every interval divides 1440. A start
    time of 06:30 on gold-8h therefore yields 02:30, 06:30, 10:30, 14:30, 18:30,
    22:30... no: 480-minute steps from phase 390 give 390, 870, 1350 — 06:30,
    14:30, 22:30. Uniformly 8 hours apart, including across midnight.

    Returns [] for an unknown cadence or an unparseable start time; callers
    resolve defaults before calling. The weekly rung has at most ONE slot on a
    run day, so it yields just the anchor."""
    interval = interval_minutes(frequency)
    anchor = parse_time_of_day(start_time)
    if not interval or anchor is None:
        return []
    if is_weekly(frequency):
        return [anchor]
    phase = anchor % interval
    return list(range(phase, MINUTES_PER_DAY, interval))


def previous_slot(now, start_time, frequency, weekly_days=None):
    """The most recent scheduled instant at or before `now`, as a datetime.

    This is the whole scheduling decision: a pipeline is due when its last run
    started BEFORE this instant. Anchoring on slots rather than on "time since
    the last run" is what makes the model robust —
      * a tick that is skipped (throttling, a blind guard, a Lambda error) does
        not lose the slot: the next tick still sees the same previous_slot and
        dispatches, just late;
      * a run that overruns its interval does not accumulate a backlog; it
        simply misses the slots it was still running through;
      * the phase never drifts, because it is computed from the start time
        rather than from the previous run.

    `now` must be timezone-aware UTC. Seconds are truncated: slots are
    minute-granular. A negative offset is correct and intended — it lands on
    yesterday's slot when `now` precedes today's first one.

    The weekly rung takes the entity's `weekly_days` (UIFPE-532) and walks the
    7-day grid instead; `weekly_days` is ignored for every other rung."""
    if is_weekly(frequency):
        return previous_weekly_slot(now, start_time, weekly_days)
    return previous_slot_at_interval(
        now, start_time, interval_minutes(frequency))


def _weekly_candidates(now, start_time, weekly_days):
    """The weekly grid around `now`: (days, anchor minutes, today's midnight)."""
    days = parse_weekly_days(weekly_days) or DEFAULT_WEEKLY_DAYS
    anchor = parse_time_of_day(start_time)
    if anchor is None:
        return None, None, None
    midnight = datetime(now.year, now.month, now.day, tzinfo=now.tzinfo)
    return days, anchor, midnight


def previous_weekly_slot(now, start_time, weekly_days=None):
    """The most recent `(weekday in weekly_days, start_time)` instant at or
    before `now`. Same contract as previous_slot: slot-based, so a skipped tick
    still finds the same slot; uneven gaps (Tue -> Thu is two days, Thu -> Tue
    is five) need no interval. Walks back at most seven days — with at least
    one day in the set there is always a slot in that window."""
    days, anchor, midnight = _weekly_candidates(now, start_time, weekly_days)
    if anchor is None:
        return None
    for back in range(0, 8):
        cand = midnight - timedelta(days=back) + timedelta(minutes=anchor)
        if WEEKDAYS[cand.weekday()] in days and cand <= now:
            return cand
    return None                           # unreachable with a non-empty set


def next_weekly_slot(now, start_time, weekly_days=None):
    """The next `(weekday in weekly_days, start_time)` instant strictly after
    `now` — for operator-facing messages."""
    days, anchor, midnight = _weekly_candidates(now, start_time, weekly_days)
    if anchor is None:
        return None
    for ahead in range(0, 8):
        cand = midnight + timedelta(days=ahead) + timedelta(minutes=anchor)
        if WEEKDAYS[cand.weekday()] in days and cand > now:
            return cand
    return None


def previous_slot_at_interval(now, start_time, interval):
    """`previous_slot` over an explicit interval in minutes.

    Split out for ADR-0020 decision 5: the recompute leg runs once every N
    scheduled runs, so its grid is (the entity's interval x N) — a multiple of
    a cadence rather than a rung of its own. One arithmetic serves both, so the
    leg inherits every property above, including the drift-free phase and the
    tolerance of a skipped sweep.

    The grid is anchored at a FIXED reference instant, not at today's midnight,
    because a leg interval need not divide a day: every cadence rung does (see
    the note on CADENCES), but `interval x N` generally does not — gold-24h x 8
    is eight days. Anchoring day-locally would restart the grid every midnight,
    so an eight-day leg would come due daily and the ratio would silently be 1.
    For an interval that DOES divide 1440 the two anchorings coincide exactly,
    which is why `previous_slot` is unaffected."""
    anchor = parse_time_of_day(start_time)
    if not interval or anchor is None:
        return None
    # The epoch carries `now`'s own tzinfo so an aware or naive `now` yields
    # the same kind back — callers compare the result against run timestamps.
    base = datetime(1970, 1, 1, tzinfo=now.tzinfo) + timedelta(minutes=anchor)
    step = timedelta(minutes=interval)
    return base + ((now - base) // step) * step


def next_slot(now, start_time, frequency, weekly_days=None):
    """The next scheduled instant strictly after `now` — for operator-facing
    messages ("next run at …"). Not used by the dispatch decision."""
    if is_weekly(frequency):
        return next_weekly_slot(now, start_time, weekly_days)
    prev = previous_slot(now, start_time, frequency)
    interval = interval_minutes(frequency)
    if prev is None:
        return None
    return prev + timedelta(minutes=interval)
