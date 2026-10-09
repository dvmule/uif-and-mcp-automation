"""Daily gold statistics: the pure arithmetic behind one row per entity per day.

UIFPE-544. The metrics table answers "what happened in this run" and expires
after 90 days; the change logs answer "how much moved" but only per entity.
Neither answers "how did the fleet do last month", which is what leadership and
consumers ask. This module turns a day's raw records into ONE row per entity,
and `gold_ops_summarize.py` (the Glue job) does nothing but fetch, call these
functions and write the result.

Kept pure and stdlib-only on purpose: every rule here - which runs count, how a
percentile is taken, when an entity was fresh - is a definition a number in a
leadership pack rests on, and a definition that cannot be unit-tested is a
number nobody can defend. Spark, boto3 and Glue stay in the job.
"""

from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta, timezone

from gold_telemetry import (freshness_limit_minutes, minutes_between,
                            parse_iso)

# A run counts towards a day if it STARTED that day (UTC). Using the end would
# put a run that crosses midnight in the day it did not do its work in, and
# would make "runs today" disagree with the Syncs page, which sorts on start.
DAY_KEY = "started_at"

TERMINAL_OK = "SUCCEEDED"
TERMINAL_FAILED = "FAILED"
# The reconcile writes "SKIPPED (no orphans)"; the transform writes "SKIPPED".
SKIPPED_PREFIX = "SKIPPED"

SCHEMA_VERSION = 2


def previous_day(now: datetime) -> date:
    """The day a nightly run summarises: yesterday in UTC."""
    return (now.astimezone(timezone.utc) - timedelta(days=1)).date()


def parse_day(value) -> date | None:
    """'YYYY-MM-DD' -> date, or None. Never raises: a malformed --day must be
    refused by the caller with a readable message, not a traceback."""
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def day_bounds(day: date) -> tuple[str, str]:
    """The ISO instants a day spans, inclusive start and exclusive end, as the
    metrics rows store them (UTC, '+00:00')."""
    start = datetime.combine(day, time.min, tzinfo=timezone.utc)
    return start.isoformat(), (start + timedelta(days=1)).isoformat()


def days_back(day: date, count: int) -> list[date]:
    """`count` days ending at `day`, oldest first - the back-fill's order, so a
    partial back-fill always leaves the OLDEST days done and no gaps."""
    count = max(1, int(count))
    return [day - timedelta(days=n) for n in range(count - 1, -1, -1)]


def status_of(row: dict) -> str:
    """The run's outcome as a single word. The reconcile's 'SKIPPED (reason)'
    and any future suffix collapse to their first token."""
    raw = row.get("status")
    if not isinstance(raw, str) or not raw.strip():
        return "UNKNOWN"
    return raw.strip().split()[0].upper()


def started_on(row: dict, day: date) -> bool:
    parsed = parse_iso(row.get(DAY_KEY))
    return parsed is not None and parsed.date() == day


def runs_for(rows, gold_id: str, day: date) -> list:
    """This entity's runs that started on this day, oldest first."""
    mine = [r for r in rows
            if str(r.get("gold_id") or "") == gold_id and started_on(r, day)]
    return sorted(mine, key=lambda r: str(r.get(DAY_KEY) or ""))


def as_int(value) -> int:
    """DynamoDB hands back Decimal; a missing counter is 0, never None, so the
    table never carries a null where a sum is expected."""
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def percentile(values, fraction: float) -> int:
    """Nearest-rank percentile over whole seconds; 0 for no values.

    Deliberately not interpolating: with three runs a day, interpolation
    invents a duration no run had, and a leadership pack should only ever
    quote a number some run actually took. Rank is ceil(p x n) - 1, the
    standard nearest-rank definition - not round(), whose banker's rounding
    would make p50 of two runs depend on parity."""
    ordered = sorted(as_int(v) for v in values)
    if not ordered:
        return 0
    rank = math.ceil(fraction * len(ordered)) - 1
    return ordered[max(0, min(len(ordered) - 1, rank))]


def summarise_runs(rows, gold_id: str, day: date) -> dict:
    """Counters and durations for one entity-day. A day with no runs yields
    zeros, never an absent key: the pack's denominators depend on it."""
    runs = runs_for(rows, gold_id, day)
    statuses = [status_of(r) for r in runs]
    finished = [r for r, s in zip(runs, statuses)
                if s in (TERMINAL_OK, TERMINAL_FAILED)]
    durations = [r.get("duration_sec") for r in finished]
    succeeded = [r for r, s in zip(runs, statuses) if s == TERMINAL_OK]
    return {
        "runs": len(runs),
        "succeeded": sum(1 for s in statuses if s == TERMINAL_OK),
        "failed": sum(1 for s in statuses if s == TERMINAL_FAILED),
        "skipped": sum(1 for s in statuses if s.startswith(SKIPPED_PREFIX)),
        # From the SAME terminal runs the percentiles use, so a reader can
        # divide it by `succeeded + failed` and get a mean that agrees with
        # p50. Percentiles cannot be summed, so a monthly pack has no other
        # way to say how much compute a domain used.
        "total_duration_sec": sum(as_int(v) for v in durations),
        "duration_p50_sec": percentile(durations, 0.5),
        "duration_p90_sec": percentile(durations, 0.9),
        "duration_max_sec": percentile(durations, 1.0),
        "rows_read": sum(as_int(r.get("rows_read")) for r in runs),
        "rows_written": sum(as_int(r.get("rows_written")) for r in runs),
        "affected_keys": sum(as_int(r.get("affected_keys")) for r in runs),
        # The last successful START of the day: where freshness is measured from.
        "last_success_at": str(succeeded[-1].get(DAY_KEY)) if succeeded else None,
    }


def summarise_reconcile(rows, gold_id: str, day: date) -> dict:
    runs = runs_for(rows, gold_id, day)
    return {
        "reconcile_runs": len(runs),
        "rows_deleted": sum(as_int(r.get("rows_deleted")) for r in runs),
        "orphans_found": sum(as_int(r.get("orphan_count")) for r in runs),
    }


def success_rate(summary: dict) -> float | None:
    """Succeeded over the runs that reached a terminal state. None when nothing
    terminal ran: a day with only gate-skips has no rate, and reporting 0%
    would read as an outage."""
    terminal = summary["succeeded"] + summary["failed"]
    if terminal == 0:
        return None
    return round(summary["succeeded"] / terminal, 4)


def freshness(last_success_at, day: date, interval_minutes) -> dict:
    """Was the entity inside its freshness target at the END of the day?

    Same rule as the alarm (UIFPE-543): 1.5 x the cadence. Measured at day end
    so every entity-day gets one comparable verdict, whatever its cadence, and
    the pack's "freshness attainment" is the share of entity-days that met it.
    """
    _start, end = day_bounds(day)
    day_end = parse_iso(end)
    limit = freshness_limit_minutes(interval_minutes)
    age = minutes_between(last_success_at, day_end)
    return {
        "freshness_limit_minutes": limit,
        "minutes_since_success_at_day_end": age,
        # Never run, or no cadence to judge against: unknown, not a pass.
        "fresh_at_day_end": None if age is None or limit is None else age <= limit,
    }


def changes_by_op_sql(table: str, day: date) -> str:
    """Rows the run wrote to the change log, by operation. NOT exploded: a row
    that moved for two channels is ONE row that changed."""
    return (f"SELECT op, count(*) AS n FROM {table} "
            f"WHERE change_date = DATE '{day.isoformat()}' GROUP BY op")


def changes_by_channel_sql(table: str, day: date) -> str:
    """The same rows per channel, exploded - so the per-channel numbers sum to
    MORE than the total when a row serves several channels. That is the honest
    shape of the question "how much did this channel see"."""
    return (f"SELECT channel, count(*) AS n FROM {table} "
            f"LATERAL VIEW explode(changed_channels) exploded AS channel "
            f"WHERE change_date = DATE '{day.isoformat()}' GROUP BY channel")


def counts_from_rows(rows, key: str) -> dict:
    """[(key, n), ...] -> {key: n}, ignoring null keys and negative counts."""
    out = {}
    for row in rows or []:
        name = row.get(key) if isinstance(row, dict) else getattr(row, key, None)
        value = row.get("n") if isinstance(row, dict) else getattr(row, "n", None)
        if name is None:
            continue
        out[str(name)] = max(0, as_int(value))
    return dict(sorted(out.items()))


def stats_row(*, gold_id: str, day: date, cfg: dict, runs: dict,
              reconcile: dict, changes_by_op: dict, changes_by_channel: dict,
              computed_at: datetime, source_run_id: str = "") -> dict:
    """One row of daily_stats. Every field is either a count that defaults to
    zero or an explicit null - a leadership pack cannot carry "missing"."""
    schedule = (cfg or {}).get("schedule") or {}
    interval = (cfg or {}).get("_interval_minutes")
    fresh = freshness(runs.get("last_success_at"), day, interval)
    watermark = (cfg or {}).get("watermark") or {}
    bookmark = watermark.get("bookmark")
    _start, end = day_bounds(day)
    return {
        # A real date, not a string: the column is a DATE so Athena's date
        # functions work on it without a cast, and Spark maps it directly.
        "day": day,
        "gold_id": gold_id,
        "domain": (cfg or {}).get("domain"),
        "enabled": bool((cfg or {}).get("enabled", False)),
        "frequency": schedule.get("frequency"),
        "interval_minutes": interval,
        "runs": runs["runs"],
        "succeeded": runs["succeeded"],
        "failed": runs["failed"],
        "skipped": runs["skipped"],
        "success_rate": success_rate(runs),
        "total_duration_sec": runs["total_duration_sec"],
        "duration_p50_sec": runs["duration_p50_sec"],
        "duration_p90_sec": runs["duration_p90_sec"],
        "duration_max_sec": runs["duration_max_sec"],
        "rows_read": runs["rows_read"],
        "rows_written": runs["rows_written"],
        "affected_keys": runs["affected_keys"],
        "reconcile_runs": reconcile["reconcile_runs"],
        "rows_deleted": reconcile["rows_deleted"],
        "orphans_found": reconcile["orphans_found"],
        "changes_total": sum(changes_by_op.values()) if changes_by_op else 0,
        "changes_by_op": changes_by_op or {},
        "changes_by_channel": changes_by_channel or {},
        "last_success_at": runs.get("last_success_at"),
        "freshness_limit_minutes": fresh["freshness_limit_minutes"],
        "minutes_since_success_at_day_end": fresh["minutes_since_success_at_day_end"],
        "fresh_at_day_end": fresh["fresh_at_day_end"],
        "bookmark_at_day_end": bookmark if isinstance(bookmark, str) else None,
        "bookmark_age_minutes": minutes_between(bookmark, parse_iso(end)),
        "computed_at": computed_at.astimezone(timezone.utc).isoformat(),
        "source_run_id": source_run_id or None,
        "schema_version": SCHEMA_VERSION,
    }


# Column order IS the table's DDL order; stats_row must fill exactly these.
COLUMNS = (
    ("day", "date"),
    ("gold_id", "string"),
    ("domain", "string"),
    ("enabled", "boolean"),
    ("frequency", "string"),
    ("interval_minutes", "int"),
    ("runs", "int"),
    ("succeeded", "int"),
    ("failed", "int"),
    ("skipped", "int"),
    ("success_rate", "double"),
    ("total_duration_sec", "bigint"),
    ("duration_p50_sec", "int"),
    ("duration_p90_sec", "int"),
    ("duration_max_sec", "int"),
    ("rows_read", "bigint"),
    ("rows_written", "bigint"),
    ("affected_keys", "bigint"),
    ("reconcile_runs", "int"),
    ("rows_deleted", "bigint"),
    ("orphans_found", "bigint"),
    ("changes_total", "bigint"),
    ("changes_by_op", "map<string,bigint>"),
    ("changes_by_channel", "map<string,bigint>"),
    ("last_success_at", "string"),
    ("freshness_limit_minutes", "int"),
    ("minutes_since_success_at_day_end", "int"),
    ("fresh_at_day_end", "boolean"),
    ("bookmark_at_day_end", "string"),
    ("bookmark_age_minutes", "int"),
    ("computed_at", "string"),
    ("source_run_id", "string"),
    ("schema_version", "int"),
)


def spark_schema() -> str:
    """The row schema as a Spark DDL string. Built from COLUMNS so the frame,
    the table and stats_row cannot drift apart; passing it explicitly also stops
    Spark inferring a type from the first day's values (a day where every
    `success_rate` is null would otherwise infer the wrong type and fail)."""
    return ", ".join(f"`{name}` {sql_type}" for name, sql_type in COLUMNS)


def as_tuple(row: dict) -> tuple:
    """A row's values in COLUMNS order. Positional on purpose: matching a dict
    to a schema by key is version-dependent in Spark, and a silent mismatch
    would write the right numbers into the wrong columns."""
    missing = [name for name, _ in COLUMNS if name not in row]
    if missing:
        raise KeyError(f"stats row is missing {missing}")
    extra = [k for k in row if k not in {name for name, _ in COLUMNS}]
    if extra:
        raise KeyError(f"stats row carries unknown field(s) {extra}")
    return tuple(row[name] for name, _ in COLUMNS)


def create_table_ddl(table: str, location: str) -> str:
    """Iceberg, partitioned by month so a year of the fleet is a handful of
    files rather than 365 tiny ones. `day` is a date so Athena's date
    functions work without a cast."""
    cols = ",\n  ".join(f"`{name}` {sql_type}" for name, sql_type in COLUMNS)
    return (f"CREATE TABLE IF NOT EXISTS {table} (\n  {cols}\n)\n"
            f"USING iceberg\nPARTITIONED BY (months(day))\n"
            f"LOCATION '{location.rstrip('/')}/'\n"
            f"TBLPROPERTIES ('format-version' = '2')")


def migrate_table_sql(table: str, existing: list) -> list:
    """The ALTER statements that bring an EXISTING table up to COLUMNS.

    `CREATE TABLE IF NOT EXISTS` no-ops on a live table, so a new column would
    otherwise only ever appear on a table built from scratch - and the first
    write would fail on a schema mismatch. Driven off COLUMNS rather than a
    hand-kept list of migrations, so this handles the next column too.

    `existing` is the column names the catalog reports; an empty list means the
    table is not there yet and CREATE will do the work, so nothing is emitted.
    """
    if not existing:
        return []
    have = {str(name).lower() for name in existing}
    return [f"ALTER TABLE {table} ADD COLUMN `{name}` {sql_type}"
            for name, sql_type in COLUMNS if name.lower() not in have]


def delete_day_sql(table: str, day: date) -> str:
    """A re-run replaces the day rather than appending a second copy, so the
    job is idempotent and a back-fill can be repeated safely."""
    return f"DELETE FROM {table} WHERE `day` = DATE '{day.isoformat()}'"


def recent_days_sql(table: str, day: date, days: int) -> str:
    """The trailing window the Overview draws, oldest first. Bounded at BOTH
    ends so a back-fill's older rows never inflate the file."""
    days = max(1, int(days))
    first = day - timedelta(days=days - 1)
    return (f"SELECT * FROM {table} WHERE `day` BETWEEN DATE '{first.isoformat()}' "
            f"AND DATE '{day.isoformat()}' ORDER BY `day`, gold_id")


# The Overview's two halves
#
# The page asks two questions with different freshness needs, so they have
# different sources (UIFPE-544 PR 2):
#
#   "right now"  -> live_window(), from the metrics rows and config rows the
#                   controller ALREADY reads. No new grant on a shared, latched
#                   role, and never more than seconds stale.
#   "this month" -> overview_summary(), pre-computed by the nightly job into one
#                   small file. A Lambda cannot read an Iceberg table, and
#                   giving the controller Athena for a 150-row query would buy
#                   seconds of latency and a wider blast radius for nothing.

SUMMARY_SCHEMA_VERSION = 1

# In the gold ARTIFACTS bucket, not the data bucket. The controller already
# holds that bucket's name in its environment and GetObject on it, so the
# Overview route needs no change to the latched echo root - and the data bucket
# stays purely Iceberg tables, which keeps storage.md's one-segment gold prefix
# unambiguous. The hyphen matches the prefixes already there (athena-results/,
# event-pipeline/, mappings/).
SUMMARY_KEY = "gold-stats/overview.json"

# The file always holds 30 days; a caller asking for fewer trims it. One file,
# one writer, and a shorter window never needs a second nightly pass.
SUMMARY_DAYS = 30

LIVE_WINDOW_HOURS = 24


def summary_s3_uri(bucket: str) -> str:
    """Only for log lines and errors - the readers address bucket and key."""
    return f"s3://{bucket.strip('/')}/{SUMMARY_KEY}"


def last_success_of(cfg: dict) -> tuple:
    """(timestamp, the attribute it came from) for an entity's last successful
    TRANSFORM. metadata.last_run_at is ALSO stamped by the nightly reconcile,
    so preferring it would show a failing transform as healthy for a day - the
    trap UIFPE-543 found, resolved here the same way and in the same order."""
    meta = (cfg or {}).get("metadata") or {}
    if meta.get("last_transform_at"):
        return meta["last_transform_at"], "last_transform_at"
    return meta.get("last_run_at"), "last_run_at"


def live_window(transform_rows, configs, now: datetime,
                hours: int = LIVE_WINDOW_HOURS) -> dict:
    """What the fleet has done in the last `hours`, and how many entities are
    inside their freshness target AS OF NOW.

    Counted over runs that STARTED inside the window, on the same definitions
    the daily table uses, so a tile and a row can never disagree."""
    # Normalised once: reporting a 0-hour window while counting a 1-hour one
    # is a caption that describes a different number from the one beside it.
    hours = max(1, int(hours))
    cutoff = now - timedelta(hours=hours)
    runs = []
    for row in transform_rows or []:
        started = parse_iso(row.get(DAY_KEY))
        if started is not None and started >= cutoff:
            runs.append(row)
    statuses = [status_of(r) for r in runs]

    fresh = stale = unknown = 0
    stale_entities = []
    entities = [c for c in (configs or []) if c.get("gold_id")]
    for cfg in entities:
        limit = freshness_limit_minutes(cfg.get("_interval_minutes"))
        age = minutes_between(last_success_of(cfg)[0], now)
        if age is None or limit is None:
            unknown += 1
        elif age <= limit:
            fresh += 1
        else:
            stale += 1
            stale_entities.append(str(cfg["gold_id"]))

    return {
        "as_of": now.astimezone(timezone.utc).isoformat(),
        "window_hours": hours,
        "entities": len(entities),
        "enabled": sum(1 for c in entities if c.get("enabled")),
        "runs": len(runs),
        "succeeded": sum(1 for s in statuses if s == TERMINAL_OK),
        "failed": sum(1 for s in statuses if s == TERMINAL_FAILED),
        "skipped": sum(1 for s in statuses if s.startswith(SKIPPED_PREFIX)),
        "rows_written": sum(as_int(r.get("rows_written")) for r in runs),
        "fresh": fresh,
        "stale": stale,
        "unknown": unknown,
        # Named, so the tile can say WHICH table is behind rather than a count.
        "stale_entities": sorted(stale_entities),
    }


def trim_summary(summary: dict, days: int) -> dict:
    """The file holds 30 days; a caller asking for 7 gets the NEWEST 7.

    Trimmed here rather than in the route so the fleet series and every entity
    series are cut by the same rule - a fleet total covering more days than the
    entity rows beneath it is the kind of mismatch nobody re-reads a page to
    check."""
    summary = summary or {}
    want = max(1, min(int(days), SUMMARY_DAYS))
    fleet = (summary.get("fleet") or [])[-want:]
    keep = {p.get("day") for p in fleet}
    entities = {k: [p for p in (v or []) if p.get("day") in keep]
                for k, v in (summary.get("entities") or {}).items()}
    out = dict(summary)
    out["days"] = want
    out["fleet"] = fleet
    out["entities"] = entities
    return out


def _day_str(value):
    """The table hands back a date; a JSON round trip hands back a string."""
    if isinstance(value, date) and not isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str) and value.strip():
        return value.strip()[:10]
    return None


def overview_summary(stats_rows, days: int, generated_at: datetime) -> dict:
    """The trailing window as the page draws it: one series for the fleet and
    one per entity. Built from the table's OWN rows, so the file cannot
    disagree with the table, and small enough that the controller serves it
    with a single GetObject."""
    per_day, per_entity = {}, {}
    for row in stats_rows or []:
        day_key = _day_str(row.get("day"))
        gold_id = row.get("gold_id")
        if not day_key or not gold_id:
            continue
        point = {
            "day": day_key,
            "runs": as_int(row.get("runs")),
            "failed": as_int(row.get("failed")),
            "rows_written": as_int(row.get("rows_written")),
            # None stays None: "never ran" is not "was stale".
            "fresh": row.get("fresh_at_day_end"),
        }
        per_entity.setdefault(str(gold_id), []).append(point)
        bucket = per_day.setdefault(day_key, {"day": day_key, "entities": 0,
                                              "runs": 0, "failed": 0,
                                              "rows_written": 0, "fresh": 0})
        bucket["entities"] += 1
        bucket["runs"] += point["runs"]
        bucket["failed"] += point["failed"]
        bucket["rows_written"] += point["rows_written"]
        bucket["fresh"] += 1 if point["fresh"] else 0
    return {
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "generated_at": generated_at.astimezone(timezone.utc).isoformat(),
        "days": max(1, int(days)),
        "fleet": [per_day[k] for k in sorted(per_day)],
        "entities": {k: sorted(v, key=lambda p: p["day"])
                     for k, v in sorted(per_entity.items())},
    }
