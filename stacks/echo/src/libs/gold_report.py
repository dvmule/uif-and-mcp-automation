"""The monthly gold leadership pack: the arithmetic, and nothing else.

UIFPE-544 PR 3. `daily_stats` answers "what happened on this day to this
table". Leadership asks a different question once a month, per domain: how many
tables did we serve, how many rows, did they meet their freshness target, how
often did a run fail, how much compute did it take. This module rolls the daily
rows up into that answer; `gold_ops_export.py` only fetches, calls it and
writes the CSV.

Pure and stdlib-only, for the same reason as `gold_stats`: every percentage
here is a number somebody will quote in a meeting, and a definition that cannot
be unit-tested is a number nobody can defend. The definitions it shares with
the daily table are IMPORTED from `gold_stats`, never restated.
"""

from __future__ import annotations

import csv
import io
from datetime import date, datetime, timedelta, timezone

from gold_stats import SCHEMA_VERSION as DAILY_SCHEMA_VERSION
# Shared, not restated: the daily table's Decimal handling must be this
# module's too, or a DynamoDB-sourced counter could total differently here.
from gold_stats import as_int

REPORT_SCHEMA_VERSION = 2

# Under the artifacts bucket, beside the Overview's file. One object per month
# at a stable key, so a re-run replaces that month and never appends a second
# copy - the same idempotency rule the daily table follows.
REPORT_PREFIX = "reports/monthly"

# Domain rows are what leadership reads; entity rows are how an engineer finds
# out which table moved a number. Both in ONE file so they always reconcile.
DOMAIN_GRAIN = "domain"
ENTITY_GRAIN = "entity"

UNASSIGNED = "unassigned"


def month_key(value) -> str:
    """'YYYY-MM' for a date, a datetime, or a 'YYYY-MM-DD' string."""
    if isinstance(value, (date, datetime)):
        return f"{value.year:04d}-{value.month:02d}"
    text = str(value or "").strip()
    return text[:7] if len(text) >= 7 else ""


def previous_month(now: datetime) -> str:
    """The month a monthly run reports on: the one that just ended.

    Taken from the first of the current month minus a day, so it is right in
    January and needs no month arithmetic of its own.
    """
    first = now.astimezone(timezone.utc).date().replace(day=1)
    return month_key(first - timedelta(days=1))


def parse_month(value) -> str | None:
    """'YYYY-MM' -> itself, or None. Never raises: a malformed --month must be
    refused by the caller with a readable message, not a traceback."""
    text = str(value or "").strip()
    if len(text) != 7 or text[4] != "-":
        return None
    try:
        year, month = int(text[:4]), int(text[5:])
    except ValueError:
        return None
    if not (1 <= month <= 12) or not (2000 <= year <= 2999):
        return None
    return f"{year:04d}-{month:02d}"


def month_bounds(month: str) -> tuple[date, date]:
    """The first and last day the month covers, inclusive, for a bounded query."""
    year, mon = int(month[:4]), int(month[5:])
    first = date(year, mon, 1)
    nxt = date(year + 1, 1, 1) if mon == 12 else date(year, mon + 1, 1)
    return first, nxt - timedelta(days=1)


def month_rows_sql(table: str, month: str) -> str:
    """The daily rows a month covers. Bounded at both ends, so a table holding
    years of history reads only the month being reported."""
    first, last = month_bounds(month)
    return (f"SELECT * FROM {table} WHERE `day` BETWEEN DATE '{first.isoformat()}' "
            f"AND DATE '{last.isoformat()}'")


def _pct(numerator: int, denominator: int):
    """A percentage to one decimal, or None when there is nothing to divide by.

    None, never 0.0: a month in which nothing reached a terminal state has no
    success rate, and 0% would be read as an outage. Same rule as
    `gold_stats.success_rate`.
    """
    if denominator <= 0:
        return None
    return round(100.0 * numerator / denominator, 1)


def _blank_totals() -> dict:
    return {"days": set(), "entities": set(), "entities_with_runs": set(),
            "runs": 0, "succeeded": 0, "failed": 0, "skipped": 0,
            "rows_read": 0, "rows_written": 0, "changes_total": 0,
            "reconcile_runs": 0, "rows_deleted": 0, "total_duration_sec": 0,
            "entity_days": 0, "freshness_met": 0, "freshness_unknown": 0}


def _accumulate(totals: dict, row: dict) -> None:
    gold_id = str(row.get("gold_id") or "")
    totals["days"].add(str(row.get("day") or ""))
    totals["entities"].add(gold_id)
    for field in ("runs", "succeeded", "failed", "skipped", "rows_read",
                  "rows_written", "changes_total", "reconcile_runs",
                  "rows_deleted", "total_duration_sec"):
        totals[field] += as_int(row.get(field))
    if as_int(row.get("runs")) > 0:
        totals["entities_with_runs"].add(gold_id)

    # One entity-day is one chance to meet the freshness target. A day whose
    # verdict is unknown is counted SEPARATELY, never as a miss: the metrics
    # rows expire after 90 days, so deep history has no evidence either way and
    # scoring it as a failure would invent an outage.
    totals["entity_days"] += 1
    verdict = row.get("fresh_at_day_end")
    if verdict is None:
        totals["freshness_unknown"] += 1
    elif verdict:
        totals["freshness_met"] += 1


def _report_row(month: str, grain: str, domain: str, gold_id: str,
                totals: dict, generated_at: datetime) -> dict:
    judged = totals["entity_days"] - totals["freshness_unknown"]
    terminal = totals["succeeded"] + totals["failed"]
    return {
        "month": month,
        "grain": grain,
        "domain": domain,
        "gold_id": gold_id,
        "days_covered": len(totals["days"]),
        # Two figures, not a product: how many tables the row covers, and how
        # many of them actually ran. A paused table must not look like a busy
        # one, and a count of tables is not a count of rows.
        "entities": len(totals["entities"] - {""}),
        "entities_with_runs": len(totals["entities_with_runs"] - {""}),
        "rows_read": totals["rows_read"],
        "rows_written": totals["rows_written"],
        "rows_changed": totals["changes_total"],
        "runs": totals["runs"],
        "succeeded": totals["succeeded"],
        # "Incidents" in the Story's words: a run that reached FAILED.
        "failed": totals["failed"],
        "skipped": totals["skipped"],
        "run_success_pct": _pct(totals["succeeded"], terminal),
        "entity_days": totals["entity_days"],
        "freshness_met": totals["freshness_met"],
        # Published so the attainment figure can be read with its coverage: a
        # 100% attainment over 2 judged days out of 30 is not a good month.
        "freshness_unknown": totals["freshness_unknown"],
        "freshness_attainment_pct": _pct(totals["freshness_met"], judged),
        "reconcile_runs": totals["reconcile_runs"],
        "rows_deleted": totals["rows_deleted"],
        # Seconds are the ADDITIVE figure and minutes are for reading. A
        # rounded per-row float cannot be summed: the first real pack had a
        # domain row of 272.6 minutes over entity rows of 253.9 and 18.6,
        # because 15,236 s and 1,118 s each round down while their exact total
        # of 16,354 s does not. The seconds always add, so they are what
        # reconciles and what a spreadsheet should total.
        #
        # DPU hours and cost stay absent - capacity varies per run and lives
        # only in Glue's own history, and a price in code goes stale.
        "compute_seconds": totals["total_duration_sec"],
        "compute_minutes": round(totals["total_duration_sec"] / 60.0, 1),
        "generated_at": generated_at.astimezone(timezone.utc).isoformat(),
        "daily_schema_version": DAILY_SCHEMA_VERSION,
        "schema_version": REPORT_SCHEMA_VERSION,
    }


def roll_up(stats_rows, month: str, generated_at: datetime) -> list:
    """The month's pack: one row per domain, then one row per entity.

    Domain rows are the sum of their entity rows by construction - they are
    accumulated from the same daily rows - so the two grains in the file can
    never disagree.
    """
    by_domain, by_entity, entity_domain = {}, {}, {}
    for row in stats_rows or []:
        if month_key(row.get("day")) != month:
            continue
        gold_id = str(row.get("gold_id") or "")
        if not gold_id:
            continue
        domain = str(row.get("domain") or "").strip() or UNASSIGNED
        entity_domain[gold_id] = domain
        by_domain.setdefault(domain, _blank_totals())
        by_entity.setdefault(gold_id, _blank_totals())
        _accumulate(by_domain[domain], row)
        _accumulate(by_entity[gold_id], row)

    out = [_report_row(month, DOMAIN_GRAIN, domain, "", totals, generated_at)
           for domain, totals in sorted(by_domain.items())]
    out += [_report_row(month, ENTITY_GRAIN, entity_domain[gold_id], gold_id,
                        totals, generated_at)
            for gold_id, totals in sorted(by_entity.items())]
    return out


# Column order IS the CSV's header order; a report row must fill exactly these.
COLUMNS = (
    "month", "grain", "domain", "gold_id", "days_covered",
    "entities", "entities_with_runs",
    "rows_read", "rows_written", "rows_changed",
    "runs", "succeeded", "failed", "skipped", "run_success_pct",
    "entity_days", "freshness_met", "freshness_unknown",
    "freshness_attainment_pct",
    "reconcile_runs", "rows_deleted", "compute_seconds", "compute_minutes",
    "generated_at", "daily_schema_version", "schema_version",
)


# The columns whose domain row MUST equal the sum of its entity rows. The
# library declares them so the job asserts all of them, rather than a tuple
# somebody hand-listed in the job and forgot to extend - which is exactly how
# `compute_minutes` went unchecked until a reader added the column up.
#
# Deliberately absent, and each for a different reason:
#   days_covered      distinct days overlap between entities, so it is not a sum
#   *_pct             a ratio of sums, never a sum of ratios
#   compute_minutes   rounded per row, so it cannot add exactly (see above)
#   month, grain, domain, gold_id, generated_at, *_version   not quantities
ADDITIVE_COLUMNS = (
    "entities", "entities_with_runs",
    "rows_read", "rows_written", "rows_changed",
    "runs", "succeeded", "failed", "skipped",
    "entity_days", "freshness_met", "freshness_unknown",
    "reconcile_runs", "rows_deleted", "compute_seconds",
)


def reconcile(rows) -> list:
    """Every way the domain rows and the entity rows disagree, as readable
    strings. Empty means the file holds together.

    Checked rather than trusted: the two grains are accumulated from the same
    daily rows and so cannot differ by construction, which is precisely why
    nobody would notice if a change broke it.
    """
    problems = []
    entities = [r for r in rows or [] if r.get("grain") == ENTITY_GRAIN]
    for domain_row in [r for r in rows or [] if r.get("grain") == DOMAIN_GRAIN]:
        domain = domain_row.get("domain")
        mine = [r for r in entities if r.get("domain") == domain]
        for column in ADDITIVE_COLUMNS:
            total = sum(as_int(r.get(column)) for r in mine)
            if as_int(domain_row.get(column)) != total:
                problems.append(
                    f"{domain} {column}: domain row says "
                    f"{domain_row.get(column)} but its {len(mine)} entity "
                    f"row(s) sum to {total}")
    return problems


def render_csv(rows) -> str:
    """The pack as CSV text, header always present.

    A header even when there are no rows: a reader who opens an empty file
    should see that the report ran and found nothing, not wonder whether the
    export broke. Percentages render EMPTY when None rather than as 0, so a
    spreadsheet averages them out instead of dragging an average to zero.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(COLUMNS)
    for row in rows or []:
        missing = [name for name in COLUMNS if name not in row]
        if missing:
            raise KeyError(f"report row is missing {missing}")
        writer.writerow(["" if row[name] is None else row[name]
                         for name in COLUMNS])
    return buffer.getvalue()


def report_key(month: str) -> str:
    """One object per month, at a key a re-run overwrites."""
    return f"{REPORT_PREFIX}/{month}.csv"


def latest_key() -> str:
    """A stable key for "the most recent pack", so a link in a runbook or an
    email never has to be edited."""
    return f"{REPORT_PREFIX}/latest.csv"
