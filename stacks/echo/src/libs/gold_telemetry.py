"""CloudWatch Embedded Metric Format (EMF) documents for the gold layer.

UIFPE-543. Every gold writer prints ONE JSON line built here and CloudWatch
Logs turns it into metrics under the UIF/Gold namespace: no PutMetricData, no
agent, no IAM. Shared by the transform and reconcile Glue jobs (via
--extra-py-files) and the dispatcher Lambda (flat zip), so this module imports
nothing outside the standard library and never touches AWS itself.

Two documents:

* ``run_document`` - one per Glue run, emitted beside the metrics-table row.
  Dimensions Env/GoldId/Status plus an Env/Status rollup, so ONE alarm on
  ``RunCount{Status=FAILED}`` covers the fleet and a per-entity view still
  exists.
* ``tick_document`` - one per enabled transform entity per dispatcher sweep.
  The dispatcher is the only thing that runs when a pipeline does NOT, so the
  freshness signal (minutes since the last successful transform against
  1.5 x the cadence) has to come from here, not from the job.

Emit with ``print``: a logger prefix in front of the JSON stops CloudWatch
recognising the document.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

NAMESPACE = "UIF/Gold"
FRESHNESS_FACTOR = 1.5
UNKNOWN_STATUS = "UNKNOWN"
_ERROR_CHARS = 240

# CloudWatch has no "Minutes" unit; ages are unit None with the unit in the name.
_NONE = "None"
_COUNT = "Count"
_SECONDS = "Seconds"


def parse_iso(value):
    """ISO-8601 -> aware UTC datetime, or None. Accepts a trailing Z, an
    offset, or a naive value (taken as UTC). Never raises: a malformed stamp
    in a config row must cost one metric, not the sweep."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def minutes_between(earlier, now) -> int | None:
    """Whole minutes from ``earlier`` (ISO string or datetime) to ``now``,
    floored at 0 so a clock ahead of ours never reports a negative age."""
    if isinstance(earlier, str):
        start = parse_iso(earlier)
    elif isinstance(earlier, datetime):
        start = earlier
    else:
        start = None
    if start is None or now is None:
        return None
    return max(0, int((now - start).total_seconds() // 60))


def freshness_limit_minutes(interval_minutes) -> int | None:
    """How stale an entity may get before it is BREACHED: 1.5 x its cadence.
    The half-interval absorbs a late slot and a long run without paging on a
    healthy pipeline."""
    if not interval_minutes or interval_minutes <= 0:
        return None
    return int(interval_minutes * FRESHNESS_FACTOR)


def normalize_status(status) -> str:
    """First token, upper-cased: the reconcile writes ``SKIPPED (reason)``
    and a dimension value must be finite."""
    if not isinstance(status, str) or not status.strip():
        return UNKNOWN_STATUS
    return status.strip().split()[0].upper()


def _timestamp_ms(now) -> int:
    return int((now or datetime.now(timezone.utc)).timestamp() * 1000)


def _document(dimension_sets, metrics, properties, now) -> dict:
    """Assemble one EMF document. ``metrics`` is a list of (name, unit, value);
    every dimension key and metric name is also a top-level key, as the format
    requires."""
    doc = {
        "_aws": {
            "Timestamp": _timestamp_ms(now),
            "CloudWatchMetrics": [{
                "Namespace": NAMESPACE,
                "Dimensions": [list(keys) for keys in dimension_sets],
                "Metrics": [{"Name": name, "Unit": unit}
                            for name, unit, _value in metrics],
            }],
        },
    }
    doc.update(properties)
    for name, _unit, value in metrics:
        doc[name] = value
    return doc


def run_document(env, gold_id, status, *, job_name=None, run_id=None,
                 rows_read=0, rows_written=0, affected_keys=0,
                 duration_seconds=0, error=None, now=None) -> dict:
    """The per-run document, emitted where the metrics-table row is written."""
    properties = {
        "Env": str(env),
        "GoldId": str(gold_id),
        "Status": normalize_status(status),
        "StatusDetail": status if isinstance(status, str) else None,
        "JobName": job_name,
        "RunId": run_id,
    }
    if error:
        properties["ErrorMessage"] = str(error)[:_ERROR_CHARS]
    metrics = [
        ("RunCount", _COUNT, 1),
        ("RowsRead", _COUNT, int(rows_read or 0)),
        ("RowsWritten", _COUNT, int(rows_written or 0)),
        ("AffectedKeys", _COUNT, int(affected_keys or 0)),
        ("DurationSeconds", _SECONDS, int(duration_seconds or 0)),
    ]
    return _document([("Env", "GoldId", "Status"), ("Env", "Status")],
                     metrics, properties, now)


def tick_document(env, gold_id, *, frequency, interval_minutes,
                  last_success_at, bookmark, dispatched, now,
                  freshness_source=None) -> dict:
    """The per-entity sweep document.

    ``last_success_at`` is the end of the last successful TRANSFORM run
    (``metadata.last_transform_at``); the caller says in ``freshness_source``
    which attribute it read, so a row still on the ``last_run_at`` fallback
    is visible in the log. No successful run at all -> ``NeverRun`` is 1 and
    the age metrics are omitted rather than faked."""
    limit = freshness_limit_minutes(interval_minutes)
    age = minutes_between(last_success_at, now)
    bookmark_age = minutes_between(bookmark, now)
    properties = {
        "Env": str(env),
        "GoldId": str(gold_id),
        "Frequency": frequency,
        "FreshnessLimitMinutes": limit,
        "FreshnessSource": freshness_source,
        "LastSuccessAt": last_success_at if isinstance(last_success_at, str) else None,
        "Bookmark": bookmark if isinstance(bookmark, str) else None,
    }
    metrics = [
        ("Dispatched", _COUNT, 1 if dispatched else 0),
        ("NeverRun", _COUNT, 0 if age is not None else 1),
    ]
    if age is not None:
        metrics.append(("MinutesSinceLastSuccess", _NONE, age))
        if limit is not None:
            metrics.append(("FreshnessBreached", _COUNT, 1 if age > limit else 0))
    if bookmark_age is not None:
        metrics.append(("BookmarkAgeMinutes", _NONE, bookmark_age))
    return _document([("Env", "GoldId"), ("Env",)], metrics, properties, now)


def render(doc) -> str:
    """One compact line. ``default=str`` so a stray Decimal from DynamoDB
    cannot turn a metric into a crash."""
    return json.dumps(doc, separators=(",", ":"), default=str)


def emit(doc, write=print) -> str:
    """Print the document as its own line and return what was written."""
    line = render(doc)
    write(line)
    return line
