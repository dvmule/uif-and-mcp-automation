# runs.py
# Run history for the panel's Runs screen — reads the DynamoDB runs table
# that the record_run Lambda writes (diagram steps 9-10). Same honesty
# contract as publisher.py: without BEACON_TABLE_RUNS the API says the
# history store is not configured rather than showing an empty history
# that looks like "no runs ever happened".

from __future__ import annotations

import json
import os

_ddb = None


def _client():
    global _ddb
    if _ddb is None:
        import boto3
        _ddb = boto3.client("dynamodb")
    return _ddb


def set_client_for_testing(ddb) -> None:
    global _ddb
    _ddb = ddb


def enabled() -> bool:
    return bool(os.environ.get("BEACON_TABLE_RUNS"))


def _parse_item(item: dict) -> dict:
    run = {
        "job_id": item["job_id"]["S"],
        "batch_id": item["batch_id"]["S"],
        "status": item.get("status", {}).get("S", "unknown"),
        # rows older than UIFPE-206 have no trigger — every run then was a
        # person pressing Run now, so manual is the honest default
        "trigger": item.get("trigger", {}).get("S", "manual"),
        "recorded_at": item.get("recorded_at", {}).get("S"),
    }
    if "summary" in item:  # stored as a JSON string (floats survive)
        try:
            run["summary"] = json.loads(item["summary"]["S"])
        except (json.JSONDecodeError, KeyError):
            run["summary"] = None
    if "error" in item:
        run["error"] = item["error"]["S"]
    # UIFPE-720: rows written before it have no start time — absent, not guessed
    if "started_at" in item:
        run["started_at"] = item["started_at"]["S"]
    return run


def record_stop(job_id: str, batch_id: str, note: str, started_at: str | None = None) -> bool:
    """UIFPE-203: a stopped execution never reaches the record_run Lambda's
    failure branch, so the API writes the terminal row itself — otherwise a
    stopped run would vanish from history (the UIFPE-200 lesson).
    UIFPE-720: `started_at` is the execution's start, when stop_run read it."""
    if not enabled():
        return False
    from datetime import datetime, timezone
    from beacon_export.lambdas.record_run import utc_stamp
    item = {
        "job_id": {"S": job_id},
        "batch_id": {"S": batch_id},
        "status": {"S": "stopped"},
        "recorded_at": {"S": datetime.now(timezone.utc)
                             .strftime("%Y-%m-%dT%H:%M:%SZ")},
        "error": {"S": note},
    }
    started = utc_stamp(started_at)
    if started:
        item["started_at"] = {"S": started}
    _client().put_item(TableName=os.environ["BEACON_TABLE_RUNS"], Item=item)
    return True


def list_runs(job_id: str | None = None, limit: int = 50) -> dict:
    if not enabled():
        return {"enabled": False, "runs": [],
                "reason": "BEACON_TABLE_RUNS not configured — run history "
                          "appears once the Step Functions stack is deployed"}
    table = os.environ["BEACON_TABLE_RUNS"]
    ddb = _client()
    if job_id:
        items = _all_pages(ddb.query, TableName=table,
                           KeyConditionExpression="job_id = :j",
                           ExpressionAttributeValues={":j": {"S": job_id}})
    else:
        items = _all_pages(ddb.scan, TableName=table)
    runs = sorted((_parse_item(i) for i in items),
                  key=lambda r: r.get("recorded_at") or "", reverse=True)
    return {"enabled": True, "runs": runs[:limit]}


def _all_pages(call, **kwargs) -> list[dict]:
    """Every item a Query/Scan returns, following LastEvaluatedKey. A single
    call stops at 1 MB, so the unscoped history (and, one day, a busy
    export's) silently lost its oldest rows once the table grew (UIFPE-610)."""
    items, key = [], None
    while True:
        page = call(**kwargs, **({"ExclusiveStartKey": key} if key else {}))
        items += page.get("Items", [])
        key = page.get("LastEvaluatedKey")
        if not key:
            return items


# ---- run counts for a time window (UIFPE-610) --------------------------------
# The Overview's Run outcomes donut and KPIs used to count the newest 50 runs
# across ALL exports — silently low once the fleet ran more than 50 times in
# the window. This counts every recorded run in [since, until) instead, and
# returns only the counts. Rows are compared by PARSED time, not by string:
# record_run and record_stop have not always written the same ISO shape.

SUMMARY_MAX_DAYS = 366


def _parse_time(value):
    from datetime import datetime, timezone
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def parse_window(since: str | None, until: str | None, now=None):
    """(since, until, error) — both aware datetimes. Missing until = now,
    missing since = 7 days before until (the Overview's old fixed window)."""
    from datetime import datetime, timedelta, timezone
    now = now or datetime.now(timezone.utc)
    end = _parse_time(until) if until else now
    if until and end is None:
        return None, None, f"until is not an ISO date-time: {until!r}"
    start = _parse_time(since) if since else end - timedelta(days=7)
    if since and start is None:
        return None, None, f"since is not an ISO date-time: {since!r}"
    if start >= end:
        return None, None, "since must be before until"
    if start > now:
        return None, None, "the window starts in the future — there are no runs yet"
    if end - start > timedelta(days=SUMMARY_MAX_DAYS):
        return None, None, f"the window may span at most {SUMMARY_MAX_DAYS} days"
    return start, end, None


def summarize_runs(since, until) -> dict:
    """Counts by status of every recorded run with since <= recorded_at <
    until. Classifying them (which count as outcomes, which are neutral) is
    the UI's runHealth rule — the API reports what the table holds."""
    if not enabled():
        return {"enabled": False,
                "reason": "BEACON_TABLE_RUNS not configured — run history "
                          "appears once the Step Functions stack is deployed"}
    items = _all_pages(
        _client().scan, TableName=os.environ["BEACON_TABLE_RUNS"],
        # counts only: never pull summaries/errors across for a donut
        ProjectionExpression="#s, recorded_at",
        ExpressionAttributeNames={"#s": "status"})  # status is reserved
    by_status: dict[str, int] = {}
    for it in items:
        at = _parse_time(it.get("recorded_at", {}).get("S"))
        if at is None or not (since <= at < until):
            continue
        status = it.get("status", {}).get("S", "unknown").lower()
        by_status[status] = by_status.get(status, 0) + 1
    return {"enabled": True, "since": since.isoformat(),
            "until": until.isoformat(),
            "total": sum(by_status.values()), "by_status": by_status}
