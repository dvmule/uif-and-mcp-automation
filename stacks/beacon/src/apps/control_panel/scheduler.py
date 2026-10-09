# scheduler.py
# Per-export schedules (UIFPE-206): "run this export every day at 06:00".
#
# A schedule lives OUTSIDE the immutable config version (D22) — changing a
# time never mints a new version. Two stores, kept in sync by this module:
#   - DynamoDB (BEACON_TABLE_SCHEDULES): the record the API and UI read
#   - EventBridge Scheduler: one schedule per export, the thing that fires
# The schedule's target is the scheduled-starter Lambda, which resolves the
# export's LATEST published version at fire time and starts the same Step
# Functions execution Run-now uses — one run path for both triggers.
#
# EventBridge Scheduler only takes cron/rate/at, so the stored expression is
# always a cron() string (the UI's picker writes it); the timezone rides in
# the separate ScheduleExpressionTimezone field and the service handles
# daylight saving itself. FlexibleTimeWindow is OFF: a feed fires on the
# minute or not at all.
#
# The schedules live in the account's shared DEFAULT group (every team's do;
# a group of our own is a later policy ask). Isolation is by NAME: every
# schedule is <BEACON_SCHEDULE_NAME_PREFIX><export id>, e.g.
# beacon-dev-export-100001 — self-identifying next to other teams' schedules,
# env-scoped (dev and uat share the account), and exactly what the API role's
# scheduler permissions are scoped to.
#
# Env (all five required for schedules to be enabled):
#   BEACON_TABLE_SCHEDULES        DynamoDB table (hash job_id)
#   BEACON_SCHEDULER_GROUP        EventBridge Scheduler group ("default")
#   BEACON_SCHEDULE_NAME_PREFIX   e.g. "beacon-dev-export-"
#   BEACON_SCHEDULER_TARGET_ARN   the scheduled-starter Lambda
#   BEACON_SCHEDULER_ROLE_ARN     role Scheduler assumes to invoke the target

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone

_ddb = None
_scheduler = None

# AWS cron: exactly six space-separated fields inside cron( ). Field syntax
# beyond that (and the day-of-month/day-of-week "one must be ?" rule) is
# validated by the Scheduler service itself on create/update — it is the
# authority, we surface its rejection as a 422 rather than re-implementing it.
_CRON_SHAPE = re.compile(r"^cron\(\s*(\S+\s+){5}\S+\s*\)$")


def _clients():
    global _ddb, _scheduler
    if _ddb is None:
        import boto3
        session = boto3.Session()
        _ddb = session.client("dynamodb")
        _scheduler = session.client("scheduler")
    return _ddb, _scheduler


def set_clients_for_testing(ddb, scheduler=None) -> None:
    global _ddb, _scheduler
    _ddb, _scheduler = ddb, scheduler


def enabled() -> bool:
    return all(os.environ.get(k) for k in (
        "BEACON_TABLE_SCHEDULES", "BEACON_SCHEDULER_GROUP",
        "BEACON_SCHEDULE_NAME_PREFIX", "BEACON_SCHEDULER_TARGET_ARN",
        "BEACON_SCHEDULER_ROLE_ARN"))


def _schedule_name(job_id: str) -> str:
    return f"{os.environ['BEACON_SCHEDULE_NAME_PREFIX']}{job_id}"


def validate(body: dict) -> str | None:
    """None when valid, else the reason (publisher/renderers style)."""
    if not isinstance(body, dict):
        return "schedule must be an object"
    expression = body.get("expression")
    if not isinstance(expression, str) or not _CRON_SHAPE.match(expression):
        return ("expression must be an AWS cron() string with six fields, "
                "e.g. cron(0 6 * * ? *)")
    tz = body.get("timezone")
    # IANA zone ids (Europe/London, Asia/Kolkata, UTC). The Scheduler service
    # rejects unknown zones; this guard only catches shapes that are clearly
    # not a zone id so the error message stays ours.
    if not isinstance(tz, str) or not re.match(r"^[A-Za-z_+\-0-9]+(/[A-Za-z_+\-0-9]+)*$", tz):
        return "timezone must be an IANA zone id, e.g. Europe/London"
    if not isinstance(body.get("enabled", True), bool):
        return "enabled must be a boolean"
    return _validate_pause_window(body)


_DATE_SHAPE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _validate_pause_window(body: dict) -> str | None:
    """UIFPE-231: an optional pause window — calendar dates in the schedule's
    own timezone. PUT is a full replace, so a body without the fields CLEARS
    any stored window (that is how Resume works)."""
    frm, until = body.get("paused_from"), body.get("paused_until")
    if frm is None and until is None:
        return None
    if until is None:
        return "paused_until is required to pause for a window"
    for name, value in (("paused_from", frm), ("paused_until", until)):
        if value is not None and (
                not isinstance(value, str) or not _DATE_SHAPE.match(value)):
            return f"{name} must be a date, YYYY-MM-DD"
    if frm is not None and frm > until:
        return "paused_until must not be before paused_from"
    return None


def put_schedule(job_id: str, body: dict) -> tuple[dict | None, str | None]:
    """Create or replace the export's schedule. Returns (record, None) or
    (None, error). Scheduler first, DynamoDB second — if the service rejects
    the expression, the stored record never lies about what is armed."""
    if not enabled():
        return None, ("scheduling is not configured on this environment "
                      "(BEACON_TABLE_SCHEDULES / BEACON_SCHEDULER_*)")
    error = validate(body)
    if error:
        return None, error
    ddb, scheduler = _clients()
    is_enabled = bool(body.get("enabled", True))
    schedule = {
        "Name": _schedule_name(job_id),
        "GroupName": os.environ["BEACON_SCHEDULER_GROUP"],
        "ScheduleExpression": body["expression"],
        "ScheduleExpressionTimezone": body["timezone"],
        "FlexibleTimeWindow": {"Mode": "OFF"},
        "State": "ENABLED" if is_enabled else "DISABLED",
        "Target": {
            "Arn": os.environ["BEACON_SCHEDULER_TARGET_ARN"],
            "RoleArn": os.environ["BEACON_SCHEDULER_ROLE_ARN"],
            "Input": json.dumps({"job_id": job_id}),
        },
    }
    try:
        try:
            scheduler.create_schedule(**schedule)
        except scheduler.exceptions.ConflictException:
            scheduler.update_schedule(**schedule)
    except Exception as e:  # ValidationException: bad cron detail / unknown tz
        return None, f"the scheduler rejected this schedule: {e}"

    record = {
        "job_id": job_id,
        "expression": body["expression"],
        "timezone": body["timezone"],
        "enabled": is_enabled,
        # UIFPE-231: pause window — the AWS schedule stays ENABLED; the
        # scheduled-starter skips fires inside the window, so resume is the
        # window passing, never a re-enable step that could fail
        "paused_from": body.get("paused_from"),
        "paused_until": body.get("paused_until"),
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    item = {
        "job_id": {"S": record["job_id"]},
        "expression": {"S": record["expression"]},
        "timezone": {"S": record["timezone"]},
        "enabled": {"BOOL": record["enabled"]},
        "updated_at": {"S": record["updated_at"]},
    }
    if record["paused_until"]:
        item["paused_until"] = {"S": record["paused_until"]}
        if record["paused_from"]:
            item["paused_from"] = {"S": record["paused_from"]}
    ddb.put_item(TableName=os.environ["BEACON_TABLE_SCHEDULES"], Item=item)
    return record, None


def get_schedule(job_id: str) -> dict | None:
    """The stored record, or None when the export has no schedule."""
    if not enabled():
        return None
    ddb, _ = _clients()
    item = ddb.get_item(TableName=os.environ["BEACON_TABLE_SCHEDULES"],
                        Key={"job_id": {"S": job_id}}).get("Item")
    if not item:
        return None
    return {
        "job_id": item["job_id"]["S"],
        "expression": item["expression"]["S"],
        "timezone": item["timezone"]["S"],
        "enabled": item.get("enabled", {}).get("BOOL", True),
        "paused_from": item.get("paused_from", {}).get("S"),
        "paused_until": item.get("paused_until", {}).get("S"),
        "updated_at": item.get("updated_at", {}).get("S"),
    }


def list_schedules() -> list[dict]:
    """Every schedule record — one Scan of a table that holds at most one row
    per export. The Exports list needs a schedule per visible row (UIFPE-207);
    N per-export GETs from the browser would be sillier than one Scan here."""
    if not enabled():
        return []
    ddb, _ = _clients()
    rows, key = [], None
    while True:
        kwargs = {"TableName": os.environ["BEACON_TABLE_SCHEDULES"]}
        if key:
            kwargs["ExclusiveStartKey"] = key
        page = ddb.scan(**kwargs)
        for item in page.get("Items", []):
            rows.append({
                "job_id": item["job_id"]["S"],
                "expression": item["expression"]["S"],
                "timezone": item["timezone"]["S"],
                "enabled": item.get("enabled", {}).get("BOOL", True),
                "paused_from": item.get("paused_from", {}).get("S"),
                "paused_until": item.get("paused_until", {}).get("S"),
                "updated_at": item.get("updated_at", {}).get("S"),
            })
        key = page.get("LastEvaluatedKey")
        if not key:
            return rows


def delete_schedule(job_id: str) -> tuple[bool, str | None]:
    """Remove the schedule from both stores. Deleting a schedule that does
    not exist is not an error — the end state is the same."""
    if not enabled():
        return False, ("scheduling is not configured on this environment "
                       "(BEACON_TABLE_SCHEDULES / BEACON_SCHEDULER_*)")
    ddb, scheduler = _clients()
    try:
        scheduler.delete_schedule(
            Name=_schedule_name(job_id),
            GroupName=os.environ["BEACON_SCHEDULER_GROUP"])
    except scheduler.exceptions.ResourceNotFoundException:
        pass
    ddb.delete_item(TableName=os.environ["BEACON_TABLE_SCHEDULES"],
                    Key={"job_id": {"S": job_id}})
    return True, None
