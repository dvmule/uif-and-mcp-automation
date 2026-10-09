# record_run.py — Step Functions task-state stage (diagram steps 9-10):
# write the batch outcome to the DynamoDB runs table. Invoked on BOTH
# branches (success and failure) so the runs table is the complete history
# the control panel's Runs screen reads.
#
# Event (state input):
#   {"job_id": "...", "batch_id": "...", "status": "succeeded" | "failed",
#    "summary": {...}?, "error": "..."?, "export_config_s3_path": "..."?,
#    "started_at": "<ISO 8601>"?}
# UIFPE-720: started_at is the execution's start ($$.Execution.StartTime),
# stored in recorded_at's own UTC format so the panel can subtract the two.
# An unreadable value is dropped, never guessed — the run row still lands.
# The failure branch cannot know job_id directly (the config may never have
# been read) — it passes export_config_s3_path instead and the id is derived
# from the immutable config layout (UIFPE-200).
#
# UIFPE-168: on the SUCCESS branch the transform's report.json (written to
# <canonical>/_report/report.json — the state machine discards the task
# output itself, ResultPath=null) is merged into the run summary, restoring
# the row counts the runs table was missing, and the incremental high-water
# mark is committed to the extraction-state table. Advancing HERE and only
# here is the design's commit point: a failed batch leaves the watermark
# untouched, so the next run re-covers the window — duplicates are possible,
# gaps are not. Both steps are best-effort: recording the run row must never
# be lost to a report read.
#
# Env: BEACON_TABLE_RUNS — table name (hash job_id, range batch_id).
#      BEACON_TABLE_EXTRACTION_STATE — per-export watermark store (optional:
#      unset = watermarks never advance, incremental feeds keep re-baselining).
# The summary is stored as a JSON string: run reports contain floats, which
# DynamoDB maps reject, and the panel treats the report as opaque anyway.

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from beacon_export.lambdas.job_identity import resolve_job_id


def _get_s3_text(s3, uri: str) -> str:
    bucket, _, key = uri.removeprefix("s3://").partition("/")
    return s3.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8")


def _load_report(s3, event: dict) -> dict | None:
    """The transform's report.json for this batch, or None. Absent for
    pre-UIFPE-168 engine versions and for batches that never packaged."""
    try:
        cfg = json.loads(_get_s3_text(s3, event["export_config_s3_path"]))
        canonical = str(cfg["canonical_s3_prefix"]).replace(
            "{batch_id}", event["batch_id"])
        return json.loads(_get_s3_text(
            s3, canonical.rstrip("/") + "/_report/report.json"))
    except Exception:
        return None


def _merge_report(summary: dict | None, report: dict) -> dict:
    """Row counts flat on the summary (the shape the panel's run tables
    read), plus the extraction outcome."""
    merged = dict(summary or {})
    transform = report.get("transform") or {}
    for key in ("output_rows", "rejected_rows", "filtered_rows"):
        if key in transform:
            merged[key] = transform[key]
    if report.get("extraction") is not None:
        merged["extraction"] = report["extraction"]
    return merged


def _advance_watermark(ddb, job_id: str, batch_id: str, extraction: dict) -> None:
    """Commit the batch's high-water mark — monotonic, conditional, never
    regressing (the gold pipeline's merge_watermarks posture): overlapping
    batches race safely because only a strictly greater value wins, and the
    timestamps sort lexicographically == chronologically. A no-change window
    reports no high-water mark and the state stays put."""
    hwm = (extraction or {}).get("high_water_mark")
    table = os.environ.get("BEACON_TABLE_EXTRACTION_STATE")
    if not hwm or not table:
        return
    try:
        ddb.update_item(
            TableName=table,
            Key={"job_id": {"S": job_id}},
            UpdateExpression="SET watermark = :w, updated_at = :t, batch_id = :b",
            ConditionExpression="attribute_not_exists(watermark) OR watermark < :w",
            ExpressionAttributeValues={
                ":w": {"S": str(hwm)},
                ":t": {"S": datetime.now(timezone.utc)
                       .strftime("%Y-%m-%dT%H:%M:%SZ")},
                ":b": {"S": batch_id}})
    except Exception:
        pass  # ConditionalCheckFailed = a concurrent batch already advanced past us


def utc_stamp(value) -> str | None:
    """An ISO 8601 time (Step Functions writes `2026-09-30T09:12:00.123Z`)
    as recorded_at's format, `YYYY-MM-DDTHH:MM:SSZ` in UTC; None when it
    cannot be read. A naive value is taken as UTC."""
    if not value:
        return None
    try:
        text = str(value).strip().replace("Z", "+00:00")
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def handler(event: dict, context=None, *, ddb_client=None, s3_client=None) -> dict:
    if ddb_client is None:
        import boto3
        ddb_client = boto3.client("dynamodb")
    table = os.environ["BEACON_TABLE_RUNS"]

    job_id = resolve_job_id(event)
    summary = event.get("summary")
    if event.get("status") == "succeeded" and event.get("export_config_s3_path"):
        report = None
        try:
            if s3_client is None:
                import boto3
                s3_client = boto3.client("s3")
            report = _load_report(s3_client, event)
        except Exception:
            report = None  # enrichment is best-effort; the run row is not
        if report is not None:
            summary = _merge_report(summary, report)
            _advance_watermark(ddb_client, job_id, event["batch_id"],
                               report.get("extraction") or {})

    item = {
        "job_id": {"S": job_id},
        "batch_id": {"S": event["batch_id"]},
        "status": {"S": event.get("status", "unknown")},
        "recorded_at": {"S": datetime.now(timezone.utc)
                             .strftime("%Y-%m-%dT%H:%M:%SZ")},
    }
    if summary is not None:
        item["summary"] = {"S": json.dumps(summary)}
    if event.get("error"):
        item["error"] = {"S": str(event["error"])[:2000]}
    if event.get("trigger"):  # manual | scheduled (UIFPE-206)
        item["trigger"] = {"S": str(event["trigger"])}
    started = utc_stamp(event.get("started_at"))  # UIFPE-720
    if started:
        item["started_at"] = {"S": started}

    ddb_client.put_item(TableName=table, Item=item)
    return {"job_id": job_id, "batch_id": event["batch_id"],
            "recorded": True, "status": item["status"]["S"]}
