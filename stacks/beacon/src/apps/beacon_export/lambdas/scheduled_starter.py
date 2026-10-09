# scheduled_starter.py — the EventBridge Scheduler target (UIFPE-206).
# One schedule per export invokes this with {"job_id": "<export id>"}; it
# starts the SAME Step Functions execution Run-now starts, so live status,
# stop and run history work unchanged for scheduled batches.
#
# At fire time it:
#   1. resolves the export's LATEST published version from the catalog table
#      (an edited export runs its newest version — the schedule never pins);
#   2. skips if a run for this export is already in flight (owner decision,
#      27 Aug 2026: skip and record, never overlap, never catch up) —
#      recorded as a `skipped` run row, neutral for health like `stopped`;
#   3. reads the transform sizing (every starter must supply job_config —
#      the state machine's input contract) and starts the execution with
#      trigger="scheduled".
#
# This module cannot import control_panel (the lambda zip ships beacon_export
# only), so the catalog read and the sizing read are small local copies of
# the API's logic, against the same tables.
#
# Env: BEACON_STATE_MACHINE_ARN, BEACON_TABLE_CATALOG, BEACON_TABLE_RUNS,
#      BEACON_TABLE_GLUE_CONFIG (optional — defaults below when unset),
#      BEACON_TABLE_EXTRACTION_STATE (optional — unset/unreadable means every
#      incremental feed baselines with a full extract, over-delivery not gaps),
#      BEACON_TABLE_SCHEDULES (optional — UIFPE-231 pause windows; unset or
#      unreadable FAILS OPEN: the feed keeps flowing, a pause is best-effort).

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from beacon_export.lambdas.job_identity import resolve_job_id

# Mirrors control_panel.publisher.DEFAULT_JOB_CONFIG — the values also match
# the Glue job definition's own settings, so all three fallbacks agree.
DEFAULT_JOB_CONFIG = {"worker_type": "G.1X", "workers": 2, "timeout_minutes": 60}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _latest_version(ddb, job_id: str) -> dict | None:
    """The latest published version record, from the catalog table (data is
    a JSON blob per version; the reserved counter item has no data attribute
    and is skipped). The whole record, not just the config URI — the
    extraction settings (UIFPE-168) ride on it too."""
    items = ddb.query(
        TableName=os.environ["BEACON_TABLE_CATALOG"],
        KeyConditionExpression="job_id = :j",
        ExpressionAttributeValues={":j": {"S": job_id}}).get("Items", [])
    versions = sorted(
        (json.loads(i["data"]["S"]) for i in items if "data" in i),
        key=lambda v: v["version"])
    return versions[-1] if versions else None


def _watermark(ddb, job_id: str) -> str:
    """The stored incremental high-water mark, "" when there is none — the
    state machine's input contract requires the key either way. Unreadable
    state degrades to "" on purpose: a baseline full extract over-delivers
    but never leaves a gap."""
    table = os.environ.get("BEACON_TABLE_EXTRACTION_STATE")
    if not table:
        return ""
    try:
        item = ddb.get_item(TableName=table,
                            Key={"job_id": {"S": job_id}}).get("Item") or {}
        return item.get("watermark", {}).get("S", "")
    except Exception:
        return ""


def _job_config(ddb) -> dict:
    """Transform sizing from beacon-<env>-glue-job-config, defaults on any
    miss — same fallback posture as the API's publisher.job_config."""
    table = os.environ.get("BEACON_TABLE_GLUE_CONFIG")
    if not table:
        return dict(DEFAULT_JOB_CONFIG)
    try:
        item = ddb.get_item(TableName=table,
                            Key={"config_key": {"S": "default"}}).get("Item") or {}
        return {
            "worker_type": item.get("worker_type", {}).get(
                "S", DEFAULT_JOB_CONFIG["worker_type"]),
            "workers": int(item.get("workers", {}).get(
                "N", DEFAULT_JOB_CONFIG["workers"])),
            "timeout_minutes": int(item.get("timeout_minutes", {}).get(
                "N", DEFAULT_JOB_CONFIG["timeout_minutes"])),
        }
    except Exception:
        return dict(DEFAULT_JOB_CONFIG)


def _run_in_flight(sfn, job_id: str) -> str | None:
    """The batch id of a RUNNING execution for this export, or None. The
    execution name is the batch id (not the job), so each running execution's
    input is inspected — cheap at the expected concurrency (0–2 running)."""
    running = sfn.list_executions(
        stateMachineArn=os.environ["BEACON_STATE_MACHINE_ARN"],
        statusFilter="RUNNING").get("executions", [])
    for execution in running:
        detail = sfn.describe_execution(executionArn=execution["executionArn"])
        if resolve_job_id(json.loads(detail.get("input") or "{}")) == job_id:
            return detail.get("name") or execution.get("name")
    return None


def _pause_window(ddb, job_id: str) -> dict | None:
    """UIFPE-231: the schedule record's pause window, or None. The AWS
    schedule stays ENABLED during a pause — this window is what makes a fire
    a no-op, so auto-resume is simply the window passing: there is no
    re-enable step that could fail and leave the feed off forever."""
    table = os.environ.get("BEACON_TABLE_SCHEDULES")
    if not table:
        return None
    try:
        item = ddb.get_item(TableName=table,
                            Key={"job_id": {"S": job_id}}).get("Item") or {}
        until = item.get("paused_until", {}).get("S")
        if not until:
            return None
        return {"from": item.get("paused_from", {}).get("S") or "0000-01-01",
                "until": until,
                "timezone": item.get("timezone", {}).get("S") or "UTC"}
    except Exception:
        return None  # fail open: an unreadable pause never stops the feed


def _paused_today(window: dict) -> bool:
    """Is today inside the window? 'Today' is the schedule's OWN timezone —
    the dates the operator picked mean calendar days where the feed runs.
    Both ends inclusive. ISO dates compare correctly as strings."""
    from zoneinfo import ZoneInfo
    try:
        today = datetime.now(ZoneInfo(window["timezone"])).date().isoformat()
    except Exception:
        today = datetime.now(timezone.utc).date().isoformat()
    return window["from"] <= today <= window["until"]


def _record(ddb, job_id: str, batch_id: str, status: str, note: str) -> None:
    """A run row the export's history shows — skipped rows included, so a
    silent non-run is visible (same lesson as UIFPE-200/203)."""
    ddb.put_item(TableName=os.environ["BEACON_TABLE_RUNS"], Item={
        "job_id": {"S": job_id},
        "batch_id": {"S": batch_id},
        "status": {"S": status},
        "trigger": {"S": "scheduled"},
        "recorded_at": {"S": _now()},
        "error": {"S": note},
    })


def handler(event: dict, context=None, *, ddb_client=None, sfn_client=None) -> dict:
    if ddb_client is None:
        import boto3
        ddb_client = boto3.client("dynamodb")
        sfn_client = boto3.client("stepfunctions")
    job_id = str(event["job_id"])
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")

    version = _latest_version(ddb_client, job_id)
    config_uri = ((version or {}).get("s3") or {}).get("export_config_uri")
    if not config_uri:
        # a schedule outliving its published config is an operator problem,
        # not a crash — record it where the export's history is read
        _record(ddb_client, job_id, f"b-sched-{stamp}", "skipped",
                "scheduled trigger skipped: no published version with an "
                "export config was found for this export")
        return {"started": False, "skipped": True, "job_id": job_id,
                "reason": "no published export config"}

    window = _pause_window(ddb_client, job_id)
    if window and _paused_today(window):
        _record(ddb_client, job_id, f"b-sched-{stamp}", "skipped",
                f"scheduled trigger skipped: schedule is paused until "
                f"{window['until']} (resumes automatically)")
        return {"started": False, "skipped": True, "job_id": job_id,
                "reason": f"paused until {window['until']}"}

    in_flight = _run_in_flight(sfn_client, job_id)
    if in_flight:
        _record(ddb_client, job_id, f"b-sched-{stamp}", "skipped",
                f"scheduled trigger skipped: batch {in_flight} is still "
                "running (schedules never overlap a running batch)")
        return {"started": False, "skipped": True, "job_id": job_id,
                "reason": f"batch {in_flight} still running"}

    batch_id = f"b-sched-{stamp}"
    sizing = _job_config(ddb_client)
    # UIFPE-168: incremental feeds carry their stored watermark in the
    # execution input (part of the contract like job_config/trigger — every
    # starter must supply it; the API's publisher.start_run is the lockstep
    # twin of this block). Full-mode feeds always send "".
    extraction = (version.get("settings") or {}).get("extraction") or {}
    watermark = (_watermark(ddb_client, job_id)
                 if extraction.get("mode") == "incremental" else "")
    response = sfn_client.start_execution(
        stateMachineArn=os.environ["BEACON_STATE_MACHINE_ARN"],
        name=batch_id,
        input=json.dumps({"export_config_s3_path": config_uri,
                          "job_config": sizing,
                          "trigger": "scheduled",
                          "watermark": watermark}))
    return {"started": True, "job_id": job_id, "batch_id": batch_id,
            "run_id": response["executionArn"], "export_config": config_uri,
            "job_config": sizing, "watermark": watermark}
