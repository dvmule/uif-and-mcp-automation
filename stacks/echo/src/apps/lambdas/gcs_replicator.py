# gcs_replicator.py
# Lambda: copies Bronze Parquet from S3 to a GCS bucket for pipelines that opt
# in via `replication.gcs.enabled=true` in their DynamoDB config.
#
# Two invocation shapes:
#
#   1. Manual / Phase-4 backfill —
#        {"pipeline_id": "<id>", "partition_value": "2026-06-03"}
#      Copies s3_target_path/_extract_date=<partition_value>/* → gcs_prefix/...
#
#   2. EventBridge (Glue Job State Change, state=SUCCEEDED) — auto-trigger
#      after a Bronze run. The handler reads the Bronze metrics row for the
#      run, derives the run window from `started_at`, and copies every S3
#      object under `s3_target_path` written during this run. Preserves the
#      relative path so any partition layout (date, dt, hour, …) just works.
#
# Failure policy: per-object retry (3 attempts, 1s/2s/4s backoff). After the
# last attempt, errors accumulate but the handler keeps going so a single bad
# object doesn't strand the rest of the partition. The job-level status is:
#   replicated  — every object copied (or nothing to copy)
#   partial     — some objects copied, some failed
#   failed      — zero objects copied AND errors occurred
#   skipped     — replication.gcs.enabled is not true (no-op)

import json
import logging
import os
import time
from datetime import datetime, timezone

import boto3

from echo_resources import echo, echo_legacy

# google-cloud-storage + google-auth come from the gcs Lambda layer.
from google.cloud import storage
from google.oauth2 import service_account

logger = logging.getLogger()
logger.setLevel(logging.INFO)

REGION         = os.environ.get("REGION",         "eu-west-1")
CONFIG_TABLE   = os.environ.get("CONFIG_TABLE",   echo_legacy("glue-job-config"))
METRICS_TABLE  = os.environ.get("METRICS_TABLE",  echo_legacy("glue-job-metrics"))
JOB_NAME       = os.environ.get("JOB_NAME",       echo("replicate-gcs"))

MAX_ATTEMPTS         = int(os.environ.get("MAX_ATTEMPTS",         "3"))
BACKOFF_BASE_S       = float(os.environ.get("BACKOFF_BASE_S",     "1.0"))
# Glue SUCCEEDED can fire microseconds before the Bronze script's finally-block
# write_metrics() completes — retry the metrics lookup briefly to bridge it.
METRICS_LOOKUP_TRIES = int(os.environ.get("METRICS_LOOKUP_TRIES", "5"))
METRICS_LOOKUP_SLEEP = float(os.environ.get("METRICS_LOOKUP_SLEEP", "2.0"))

dynamo     = boto3.resource("dynamodb", region_name=REGION)
s3         = boto3.client("s3",         region_name=REGION)
secrets    = boto3.client("secretsmanager", region_name=REGION)
cloudwatch = boto3.client("cloudwatch",  region_name=REGION)

METRICS_NAMESPACE = os.environ.get("METRICS_NAMESPACE", "EnterpriseDataConnect/GcsReplication")


# ── entry point ────────────────────────────────────────────────────

def handler(event, context):
    """Route the event to the matching code path, then replicate."""
    if _is_eventbridge_glue(event):
        plan = _plan_from_eventbridge(event)
    else:
        plan = _plan_from_manual(event or {})

    if plan.get("status"):
        # Short-circuit: invalid event, skipped, or no-replication-config.
        return _result(plan["status"], **{k: v for k, v in plan.items() if k != "status"})

    return _replicate(plan)


# ── event handling ─────────────────────────────────────────────────

def _is_eventbridge_glue(event) -> bool:
    return (
        isinstance(event, dict)
        and event.get("source") == "aws.glue"
        and isinstance(event.get("detail"), dict)
    )


def _plan_from_manual(event: dict) -> dict:
    pipeline_id     = event.get("pipeline_id")
    partition_value = event.get("partition_value")
    if not pipeline_id or not partition_value:
        return {"status": "failed", "error": "event missing pipeline_id or partition_value"}

    cfg = _get_pipeline_config(pipeline_id)
    if not cfg:
        return {"status": "failed", "pipeline_id": pipeline_id,
                "error": f"pipeline_id '{pipeline_id}' not found"}

    if ((cfg.get("destination") or {}).get("cloud") or "aws").lower() == "gcp":
        return {
            "status":      "skipped",
            "pipeline_id": pipeline_id,
            "reason":      "destination.cloud=gcp; Bronze→GCS replication not applicable (GCP is already the primary destination)",
        }

    gcs_cfg = (cfg.get("replication") or {}).get("gcs") or {}
    if not gcs_cfg.get("enabled"):
        return {"status": "skipped", "pipeline_id": pipeline_id,
                "partition_value": partition_value}

    s3_path = (cfg.get("target") or {}).get("s3_path")
    if not s3_path:
        return {"status": "failed", "pipeline_id": pipeline_id,
                "error": "target.s3_path missing on pipeline config"}

    src_bucket, src_base_prefix = _parse_s3_path(s3_path)
    src_prefix = f"{src_base_prefix}_extract_date={partition_value}/"

    return {
        "pipeline_id":     pipeline_id,
        "trigger":         "manual",
        "partition_value": partition_value,
        "src_bucket":      src_bucket,
        "src_prefix":      src_prefix,
        "src_base_prefix": src_base_prefix,
        "modified_after":  None,   # no time filter for manual
        "gcs_cfg":         gcs_cfg,
    }


def _plan_from_eventbridge(event: dict) -> dict:
    detail   = event["detail"]
    state    = detail.get("state")
    job_name = detail.get("jobName")
    run_id   = detail.get("jobRunId")
    if state != "SUCCEEDED":
        return {"status": "skipped", "reason": f"glue state={state}"}
    if not job_name or not run_id:
        return {"status": "failed",
                "error": "EventBridge event missing detail.jobName or detail.jobRunId"}

    bronze = _get_bronze_metrics_with_retry(job_name, run_id)
    if not bronze:
        return {"status": "failed", "job_name": job_name, "run_id": run_id,
                "error": f"no metrics row for {job_name}/{run_id} after {METRICS_LOOKUP_TRIES} tries"}

    pipeline_id    = bronze.get("pipeline_id")
    started_at_iso = bronze.get("started_at")
    s3_target      = bronze.get("s3_target_path")
    if not pipeline_id or not started_at_iso or not s3_target:
        return {"status": "failed", "job_name": job_name, "run_id": run_id,
                "error": "Bronze metrics row missing pipeline_id/started_at/s3_target_path"}

    # Only act on SUCCEEDED runs (already checked) — skip if Bronze status itself
    # wasn't SUCCESS (e.g. PARTIAL or anything we don't recognise).
    if bronze.get("status") and bronze["status"] not in ("SUCCESS", "SUCCEEDED"):
        return {"status": "skipped", "reason": f"bronze status={bronze['status']}",
                "pipeline_id": pipeline_id}

    cfg = _get_pipeline_config(pipeline_id)
    if not cfg:
        return {"status": "failed", "pipeline_id": pipeline_id,
                "error": f"pipeline_id '{pipeline_id}' not found"}

    if ((cfg.get("destination") or {}).get("cloud") or "aws").lower() == "gcp":
        return {
            "status":      "skipped",
            "pipeline_id": pipeline_id,
            "reason":      "destination.cloud=gcp; Bronze→GCS replication not applicable (GCP is already the primary destination)",
        }

    gcs_cfg = (cfg.get("replication") or {}).get("gcs") or {}
    if not gcs_cfg.get("enabled"):
        return {"status": "skipped", "pipeline_id": pipeline_id, "trigger": "eventbridge"}

    src_bucket, src_base_prefix = _parse_s3_path(s3_target)
    started_at = datetime.fromisoformat(started_at_iso)

    return {
        "pipeline_id":     pipeline_id,
        "trigger":         "eventbridge",
        "bronze_run_id":   run_id,
        "src_bucket":      src_bucket,
        "src_prefix":      src_base_prefix,   # list the whole table prefix
        "src_base_prefix": src_base_prefix,
        "modified_after":  started_at,
        "gcs_cfg":         gcs_cfg,
    }


def _get_bronze_metrics_with_retry(job_name: str, run_id: str) -> dict | None:
    for attempt in range(1, METRICS_LOOKUP_TRIES + 1):
        resp = dynamo.Table(METRICS_TABLE).get_item(Key={"job_name": job_name, "run_id": run_id})
        if "Item" in resp:
            return resp["Item"]
        if attempt < METRICS_LOOKUP_TRIES:
            logger.info(f"[METRICS LOOKUP {attempt}/{METRICS_LOOKUP_TRIES}] not yet present, sleeping {METRICS_LOOKUP_SLEEP}s")
            time.sleep(METRICS_LOOKUP_SLEEP)
    return None


# ── replication ────────────────────────────────────────────────────

def _replicate(plan: dict) -> dict:
    pipeline_id     = plan["pipeline_id"]
    gcs_cfg         = plan["gcs_cfg"]
    src_bucket      = plan["src_bucket"]
    src_prefix      = plan["src_prefix"]
    src_base_prefix = plan["src_base_prefix"]
    modified_after  = plan["modified_after"]

    started_at = datetime.now(timezone.utc)
    run_id     = f"{pipeline_id}_{int(started_at.timestamp() * 1000)}"

    try:
        gcs_bucket = _build_gcs_client(gcs_cfg["credentials_secret_arn"]).bucket(gcs_cfg["bucket"])
    except Exception as exc:
        logger.exception("[AUTH] failed to build GCS client")
        return _write_metrics_and_return(
            run_id=run_id, pipeline_id=pipeline_id, plan=plan, started_at=started_at,
            status="failed", files_ok=0, files_skipped=0, files_failed=0, bytes_ok=0,
            errors=[f"gcs_auth: {exc}"],
        )

    files_ok = 0
    files_skipped = 0   # already present in GCS with matching size — idempotency win
    files_failed = 0
    bytes_ok = 0
    errors: list[str] = []
    objects_seen = 0

    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=src_bucket, Prefix=src_prefix):
        for obj in page.get("Contents", []) or []:
            key = obj["Key"]
            size = int(obj.get("Size", 0))
            if key.endswith("/") or size == 0:
                continue
            if modified_after is not None and obj["LastModified"] < modified_after:
                continue
            objects_seen += 1

            rel_path = key[len(src_base_prefix):] if key.startswith(src_base_prefix) else key.rsplit("/", 1)[-1]
            dst_key  = _dest_key(gcs_cfg["prefix"], rel_path)
            dst_blob = gcs_bucket.blob(dst_key)
            try:
                # Idempotency: if dest already exists at the same size, skip. Cheap
                # HEAD-equivalent. Bronze files are immutable so size-match implies
                # content-match; we don't pay for a CRC compare.
                dst_blob.reload()  # raises NotFound if absent
                if dst_blob.size == size:
                    files_skipped += 1
                    continue
                logger.info(f"[OVERWRITE] gs://{gcs_cfg['bucket']}/{dst_key} exists at {dst_blob.size}B, source is {size}B — re-uploading")
            except Exception:
                pass  # not found → fall through to upload

            try:
                _copy_one_with_retry(src_bucket, key, size, gcs_bucket, dst_key)
                files_ok += 1
                bytes_ok += size
            except Exception as exc:
                files_failed += 1
                errors.append(f"{key}: {exc}")
                logger.warning(f"[REPLICATE FAIL] s3://{src_bucket}/{key} → gs://{gcs_cfg['bucket']}/{dst_key}: {exc}")

    if objects_seen == 0:
        status = "replicated"
        logger.info(f"[EMPTY] no qualifying objects under s3://{src_bucket}/{src_prefix}")
    elif files_failed == 0:
        status = "replicated"
    elif (files_ok + files_skipped) == 0:
        status = "failed"
    else:
        status = "partial"

    return _write_metrics_and_return(
        run_id=run_id, pipeline_id=pipeline_id, plan=plan, started_at=started_at,
        status=status, files_ok=files_ok, files_skipped=files_skipped,
        files_failed=files_failed, bytes_ok=bytes_ok, errors=errors,
    )


# ── helpers ────────────────────────────────────────────────────────

def _get_pipeline_config(pipeline_id: str) -> dict | None:
    resp = dynamo.Table(CONFIG_TABLE).get_item(
        Key={"pipeline_id": pipeline_id, "version": "latest"}
    )
    return resp.get("Item")


def _build_gcs_client(secret_arn: str) -> storage.Client:
    raw = secrets.get_secret_value(SecretId=secret_arn)["SecretString"]
    sa_info = json.loads(raw)
    creds = service_account.Credentials.from_service_account_info(sa_info)
    return storage.Client(project=sa_info.get("project_id"), credentials=creds)


def _parse_s3_path(s3_path: str) -> tuple[str, str]:
    if not s3_path.startswith("s3://"):
        raise ValueError(f"s3_path must start with s3://, got: {s3_path}")
    without_scheme = s3_path[len("s3://"):]
    bucket, _, key_prefix = without_scheme.partition("/")
    if key_prefix and not key_prefix.endswith("/"):
        key_prefix += "/"
    return bucket, key_prefix


def _dest_key(gcs_prefix: str, rel_path: str) -> str:
    if not gcs_prefix.endswith("/"):
        gcs_prefix += "/"
    return gcs_prefix + rel_path


def _copy_one_with_retry(src_bucket: str, src_key: str, size: int,
                         gcs_bucket: storage.Bucket, dst_key: str) -> None:
    last_exc: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            body = s3.get_object(Bucket=src_bucket, Key=src_key)["Body"]
            blob = gcs_bucket.blob(dst_key)
            blob.upload_from_file(body, size=size, rewind=False)
            return
        except Exception as exc:  # noqa: BLE001 — retry policy is broad on purpose
            last_exc = exc
            if attempt < MAX_ATTEMPTS:
                sleep_s = BACKOFF_BASE_S * (2 ** (attempt - 1))
                logger.info(f"[RETRY {attempt}/{MAX_ATTEMPTS}] {src_key}: {exc} (sleeping {sleep_s}s)")
                time.sleep(sleep_s)
    assert last_exc is not None
    raise last_exc


# ── metrics write ─────────────────────────────────────────────────

def _write_metrics_and_return(*, run_id, pipeline_id, plan, started_at, status,
                              files_ok, files_skipped, files_failed, bytes_ok, errors):
    completed_at = datetime.now(timezone.utc)
    duration_sec = int((completed_at - started_at).total_seconds())
    gcs_cfg = plan["gcs_cfg"]
    item = {
        "job_name":           JOB_NAME,
        "run_id":             run_id,
        "pipeline_id":        pipeline_id,
        "trigger":            plan.get("trigger", "manual"),
        "bronze_run_id":      plan.get("bronze_run_id"),
        "partition_value":    plan.get("partition_value"),
        "replication_status": status,
        "files_replicated":   files_ok,
        "files_skipped":      files_skipped,
        "files_failed":       files_failed,
        "bytes_replicated":   bytes_ok,
        "errors_count":       len(errors),
        "error_message":      " | ".join(errors[:3]) if errors else None,
        "gcs_destination":    f"gs://{gcs_cfg['bucket']}/{gcs_cfg['prefix']}",
        "started_at":         started_at.isoformat(),
        "completed_at":       completed_at.isoformat(),
        "duration_sec":       duration_sec,
        "ttl":                int(time.time()) + (90 * 86400),
    }
    item = {k: v for k, v in item.items() if v is not None}
    try:
        dynamo.Table(METRICS_TABLE).put_item(Item=item)
    except Exception:
        logger.exception("[METRICS] put_item failed — continuing")

    _emit_cw_metrics(pipeline_id, status, files_ok, files_skipped,
                     files_failed, bytes_ok, duration_sec)

    logger.info(
        f"[DONE] pipeline_id={pipeline_id} trigger={plan.get('trigger')} status={status} "
        f"files_ok={files_ok} files_skipped={files_skipped} files_failed={files_failed} bytes={bytes_ok}"
    )
    return _result(status, pipeline_id=pipeline_id,
                   trigger=plan.get("trigger"),
                   bronze_run_id=plan.get("bronze_run_id"),
                   partition_value=plan.get("partition_value"),
                   files_replicated=files_ok, files_skipped=files_skipped,
                   files_failed=files_failed, bytes_replicated=bytes_ok,
                   run_id=run_id, error=item.get("error_message"))


def _emit_cw_metrics(pipeline_id, status, files_ok, files_skipped, files_failed,
                     bytes_ok, duration_sec) -> None:
    """Best-effort metric emission. Never raise — observability must not block a run."""
    dims = [{"Name": "PipelineId", "Value": pipeline_id}]
    metrics = [
        {"MetricName": "FilesReplicated",    "Value": files_ok,     "Unit": "Count",   "Dimensions": dims},
        {"MetricName": "FilesSkipped",       "Value": files_skipped, "Unit": "Count",  "Dimensions": dims},
        {"MetricName": "FilesFailed",        "Value": files_failed, "Unit": "Count",   "Dimensions": dims},
        {"MetricName": "BytesReplicated",    "Value": bytes_ok,     "Unit": "Bytes",   "Dimensions": dims},
        {"MetricName": "DurationSeconds",    "Value": duration_sec, "Unit": "Seconds", "Dimensions": dims},
        # Status counter — one of {replicated, partial, failed, skipped} per run, so
        # a sum over a window equals run count and ratios give success rate.
        {"MetricName": f"RunStatus_{status}", "Value": 1,           "Unit": "Count",   "Dimensions": dims},
    ]
    try:
        cloudwatch.put_metric_data(Namespace=METRICS_NAMESPACE, MetricData=metrics)
    except Exception:
        logger.exception("[CW] put_metric_data failed — continuing")


def _result(status: str, **kw) -> dict:
    out = {"status": status}
    out.update({k: v for k, v in kw.items() if v is not None})
    return out
