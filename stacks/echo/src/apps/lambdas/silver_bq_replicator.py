# silver_bq_replicator.py
# Lambda: replicates Silver Iceberg tables from S3 to GCS and exposes them as
# BigQuery BigLake external tables. Opted into per pipeline via
# `replication.silver_bq.enabled=true` in the DynamoDB config.
#
# Two invocation shapes:
#
#   1. Manual / backfill —
#        {"pipeline_id": "<id>"}
#      Mirrors the current Iceberg snapshot for that pipeline and refreshes
#      the BQ external table. Useful for first-time bootstrap.
#
#   2. EventBridge (Glue Job State Change, state=SUCCEEDED) — auto-trigger
#      after a Silver run. The handler reads the Silver metrics row for the
#      run, derives silver.s3_path from the pipeline config, and replicates
#      the current Iceberg snapshot.
#
# The Iceberg metadata chain (metadata.json → manifest-list.avro → manifest.avro)
# is rewritten so every `s3://...` URI becomes `gs://...` before upload. BigLake
# follows the chain from GCS, so without this rewrite BQ would try to read the
# Parquet data files from S3 and fail.
#
# Status values written to the metrics row:
#   replicated  — mirror + BQ DDL both succeeded (or nothing to copy)
#   bq_failed   — files mirrored but BQ DDL failed (next run will retry cheaply)
#   partial     — some files copied, some failed; BQ not updated
#   failed      — zero files copied AND errors occurred
#   skipped     — replication.silver_bq.enabled is not true (no-op)

import json
import logging
import os
import time
from datetime import datetime, timezone
from io import BytesIO

import boto3

from echo_resources import echo, echo_legacy

# google-cloud-storage + google-cloud-bigquery + google-auth + fastavro come
# from the silver-bq-client Lambda layer.
import fastavro
from google.cloud import bigquery, storage
from google.oauth2 import service_account

logger = logging.getLogger()
logger.setLevel(logging.INFO)

REGION         = os.environ.get("REGION",         "eu-west-1")
CONFIG_TABLE   = os.environ.get("CONFIG_TABLE",   echo_legacy("glue-job-config"))
METRICS_TABLE  = os.environ.get("METRICS_TABLE",  echo_legacy("glue-job-metrics"))
JOB_NAME       = os.environ.get("JOB_NAME",       echo("replicate-bq"))

MAX_ATTEMPTS         = int(os.environ.get("MAX_ATTEMPTS",         "3"))
BACKOFF_BASE_S       = float(os.environ.get("BACKOFF_BASE_S",     "1.0"))
# Glue SUCCEEDED can fire microseconds before the Silver script's finally-block
# write_metrics() completes — retry the metrics lookup briefly to bridge it.
METRICS_LOOKUP_TRIES = int(os.environ.get("METRICS_LOOKUP_TRIES", "5"))
METRICS_LOOKUP_SLEEP = float(os.environ.get("METRICS_LOOKUP_SLEEP", "2.0"))

dynamo     = boto3.resource("dynamodb",         region_name=REGION)
s3         = boto3.client("s3",                  region_name=REGION)
secrets    = boto3.client("secretsmanager",     region_name=REGION)
cloudwatch = boto3.client("cloudwatch",         region_name=REGION)

METRICS_NAMESPACE = os.environ.get("METRICS_NAMESPACE",
                                   "EnterpriseDataConnect/SilverBqReplication")


# ── entry point ────────────────────────────────────────────────────

def handler(event, context):
    if _is_eventbridge_glue(event):
        plan = _plan_from_eventbridge(event)
    else:
        plan = _plan_from_manual(event or {})

    if plan.get("status"):
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
    pipeline_id = event.get("pipeline_id")
    if not pipeline_id:
        return {"status": "failed", "error": "event missing pipeline_id"}

    cfg = _get_pipeline_config(pipeline_id)
    if not cfg:
        return {"status": "failed", "pipeline_id": pipeline_id,
                "error": f"pipeline_id '{pipeline_id}' not found"}

    return _plan_from_config(cfg, trigger="manual")


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

    silver = _get_silver_metrics_with_retry(job_name, run_id)
    if not silver:
        return {"status": "failed", "job_name": job_name, "run_id": run_id,
                "error": f"no metrics row for {job_name}/{run_id} after {METRICS_LOOKUP_TRIES} tries"}

    pipeline_id = silver.get("pipeline_id")
    if not pipeline_id:
        return {"status": "failed", "job_name": job_name, "run_id": run_id,
                "error": "Silver metrics row missing pipeline_id"}

    # Silver script writes status="SUCCESS" on a normal run, "SKIPPED" when
    # silver.enabled=false, "FAILED" on error. Only act on SUCCESS.
    silver_status = silver.get("status")
    if silver_status not in ("SUCCESS", "SUCCEEDED"):
        return {"status": "skipped", "reason": f"silver status={silver_status}",
                "pipeline_id": pipeline_id}

    cfg = _get_pipeline_config(pipeline_id)
    if not cfg:
        return {"status": "failed", "pipeline_id": pipeline_id,
                "error": f"pipeline_id '{pipeline_id}' not found"}

    plan = _plan_from_config(cfg, trigger="eventbridge")
    if "status" not in plan:
        plan["silver_run_id"] = run_id
    return plan


def _plan_from_config(cfg: dict, trigger: str) -> dict:
    pipeline_id = cfg.get("pipeline_id")

    if ((cfg.get("destination") or {}).get("cloud") or "aws").lower() == "gcp":
        return {
            "status":      "skipped",
            "pipeline_id": pipeline_id,
            "trigger":     trigger,
            "reason":      "destination.cloud=gcp; Silver→BigQuery replication not applicable (GCP is already the primary destination)",
        }

    bq_cfg = (cfg.get("replication") or {}).get("silver_bq") or {}
    if not bq_cfg.get("enabled"):
        return {"status": "skipped", "pipeline_id": pipeline_id, "trigger": trigger}

    silver_cfg = cfg.get("silver") or {}
    if not silver_cfg.get("enabled"):
        return {"status": "skipped", "pipeline_id": pipeline_id, "trigger": trigger,
                "reason": "silver not enabled"}

    silver_s3_path = silver_cfg.get("s3_path")
    if not silver_s3_path:
        return {"status": "failed", "pipeline_id": pipeline_id,
                "error": "silver.s3_path missing on pipeline config"}

    src_bucket, src_base_prefix = _parse_s3_path(silver_s3_path)

    return {
        "pipeline_id":     pipeline_id,
        "trigger":         trigger,
        "src_bucket":      src_bucket,
        "src_base_prefix": src_base_prefix,
        "silver_s3_path":  silver_s3_path,
        "silver_db":       silver_cfg.get("database"),
        "silver_table":    silver_cfg.get("table"),
        "bq_cfg":          bq_cfg,
    }


def _get_silver_metrics_with_retry(job_name: str, run_id: str) -> dict | None:
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
    bq_cfg          = plan["bq_cfg"]
    src_bucket      = plan["src_bucket"]
    src_base_prefix = plan["src_base_prefix"]

    started_at = datetime.now(timezone.utc)
    run_id     = f"{pipeline_id}_{int(started_at.timestamp() * 1000)}"

    try:
        creds, project_id = _load_gcp_credentials(bq_cfg["credentials_secret_arn"])
        gcs_client = storage.Client(project=project_id, credentials=creds)
        gcs_bucket = gcs_client.bucket(bq_cfg["bucket"])
    except Exception as exc:
        logger.exception("[AUTH] failed to build GCS client")
        return _write_metrics_and_return(
            run_id=run_id, pipeline_id=pipeline_id, plan=plan, started_at=started_at,
            status="failed", files_ok=0, files_skipped=0, files_failed=0, bytes_ok=0,
            metadata_pointer=None, bq_status="not_attempted",
            errors=[f"gcs_auth: {exc}"],
        )

    # Mirror data/ and metadata/ from S3 to GCS. Size-idempotent.
    mirror = _mirror_iceberg_dir(src_bucket, src_base_prefix, gcs_bucket, bq_cfg["prefix"])
    files_ok      = mirror["files_ok"]
    files_skipped = mirror["files_skipped"]
    files_failed  = mirror["files_failed"]
    bytes_ok      = mirror["bytes_ok"]
    errors        = mirror["errors"]

    # Determine mirror status. Even when zero data files moved (everything
    # already mirrored), we still want to rewrite metadata + refresh BQ — the
    # Iceberg snapshot pointer may have advanced.
    if files_failed > 0 and (files_ok + files_skipped) == 0:
        return _write_metrics_and_return(
            run_id=run_id, pipeline_id=pipeline_id, plan=plan, started_at=started_at,
            status="failed", files_ok=files_ok, files_skipped=files_skipped,
            files_failed=files_failed, bytes_ok=bytes_ok,
            metadata_pointer=None, bq_status="not_attempted",
            errors=errors,
        )
    if files_failed > 0:
        return _write_metrics_and_return(
            run_id=run_id, pipeline_id=pipeline_id, plan=plan, started_at=started_at,
            status="partial", files_ok=files_ok, files_skipped=files_skipped,
            files_failed=files_failed, bytes_ok=bytes_ok,
            metadata_pointer=None, bq_status="not_attempted",
            errors=errors,
        )

    # Rewrite the Iceberg metadata chain (metadata.json + every Avro it
    # references) so paths point at gs:// instead of s3://, then upload the
    # rewritten copies into the same GCS metadata/ folder.
    try:
        metadata_pointer = _rewrite_metadata_chain(
            src_bucket=src_bucket,
            src_base_prefix=src_base_prefix,
            gcs_bucket=gcs_bucket,
            gcs_prefix=bq_cfg["prefix"],
        )
    except Exception as exc:
        logger.exception("[METADATA] rewrite failed")
        errors.append(f"metadata_rewrite: {exc}")
        return _write_metrics_and_return(
            run_id=run_id, pipeline_id=pipeline_id, plan=plan, started_at=started_at,
            status="failed", files_ok=files_ok, files_skipped=files_skipped,
            files_failed=files_failed, bytes_ok=bytes_ok,
            metadata_pointer=None, bq_status="not_attempted",
            errors=errors,
        )

    # Issue the BQ DDL. This is the only step that doesn't share idempotency
    # with the mirror: a failure here leaves GCS in a usable state but BQ
    # pointing at the old snapshot. Status `bq_failed` so the next Silver
    # SUCCEEDED retries cheaply.
    bq_status = "not_attempted"
    try:
        bq_client = bigquery.Client(project=bq_cfg["bq_project"], credentials=creds)
        _refresh_bq_external_table(
            bq_client=bq_client,
            bq_cfg=bq_cfg,
            silver_table=plan.get("silver_table"),
            metadata_uri=f"gs://{bq_cfg['bucket']}/{metadata_pointer}",
        )
        bq_status = "ok"
        status = "replicated"
    except Exception as exc:
        logger.exception("[BQ] CREATE OR REPLACE EXTERNAL TABLE failed")
        errors.append(f"bq_ddl: {exc}")
        bq_status = "failed"
        status = "bq_failed"

    return _write_metrics_and_return(
        run_id=run_id, pipeline_id=pipeline_id, plan=plan, started_at=started_at,
        status=status, files_ok=files_ok, files_skipped=files_skipped,
        files_failed=files_failed, bytes_ok=bytes_ok,
        metadata_pointer=metadata_pointer, bq_status=bq_status,
        errors=errors,
    )


# ── mirror phase ───────────────────────────────────────────────────

def _mirror_iceberg_dir(src_bucket: str, src_base_prefix: str,
                        gcs_bucket: storage.Bucket, gcs_prefix: str) -> dict:
    """Copy every object under the Silver Iceberg directory to GCS.

    Mirrors data/ first (additive — Iceberg never overwrites a data file
    without an expire-snapshots run, which the project doesn't run), then
    metadata/. Order matters: BQ resolves data files through manifests, so a
    metadata pointer must never advance ahead of its data files in GCS.

    Returns a counters dict. Errors are collected, not raised — a single bad
    object doesn't strand the rest of the snapshot.
    """
    files_ok = 0
    files_skipped = 0
    files_failed = 0
    bytes_ok = 0
    errors: list[str] = []

    for sub in ("data/", "metadata/"):
        prefix = src_base_prefix + sub
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=src_bucket, Prefix=prefix):
            for obj in page.get("Contents", []) or []:
                key  = obj["Key"]
                size = int(obj.get("Size", 0))
                if key.endswith("/") or size == 0:
                    continue

                rel_path = key[len(src_base_prefix):]
                dst_key  = _dest_key(gcs_prefix, rel_path)
                dst_blob = gcs_bucket.blob(dst_key)

                try:
                    dst_blob.reload()  # raises NotFound if absent
                    if dst_blob.size == size:
                        files_skipped += 1
                        continue
                    logger.info(f"[OVERWRITE] gs://{gcs_bucket.name}/{dst_key} exists at {dst_blob.size}B, source is {size}B")
                except Exception:
                    pass

                try:
                    _copy_one_with_retry(src_bucket, key, size, gcs_bucket, dst_key)
                    files_ok += 1
                    bytes_ok += size
                except Exception as exc:
                    files_failed += 1
                    errors.append(f"{key}: {exc}")
                    logger.warning(f"[REPLICATE FAIL] s3://{src_bucket}/{key} → gs://{gcs_bucket.name}/{dst_key}: {exc}")

    return {
        "files_ok": files_ok, "files_skipped": files_skipped,
        "files_failed": files_failed, "bytes_ok": bytes_ok, "errors": errors,
    }


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


# ── metadata-rewrite phase ─────────────────────────────────────────

def _rewrite_metadata_chain(*, src_bucket: str, src_base_prefix: str,
                             gcs_bucket: storage.Bucket, gcs_prefix: str) -> str:
    """Rewrite metadata.json + the Avro chain it references so paths are gs://.

    Returns the GCS object key of the current metadata pointer (relative to
    the bucket), e.g. `silver/erp_orders/metadata/v17.metadata.json`.
    """
    s3_uri_prefix = f"s3://{src_bucket}/{src_base_prefix}"
    gcs_uri_prefix = f"gs://{gcs_bucket.name}/{gcs_prefix}"

    pointer_key = _resolve_current_metadata_pointer(src_bucket, src_base_prefix)
    logger.info(f"[METADATA] current pointer: s3://{src_bucket}/{pointer_key}")

    metadata_obj = s3.get_object(Bucket=src_bucket, Key=pointer_key)
    metadata_doc = json.loads(metadata_obj["Body"].read())

    manifest_list_keys: list[str] = []
    for snap in metadata_doc.get("snapshots", []) or []:
        ml = snap.get("manifest-list")
        if ml and ml.startswith(s3_uri_prefix):
            manifest_list_keys.append(ml[len("s3://"):].split("/", 1)[1])

    current_snapshot_id = metadata_doc.get("current-snapshot-id")
    current_ml_key: str | None = None
    for snap in metadata_doc.get("snapshots", []) or []:
        if snap.get("snapshot-id") == current_snapshot_id:
            ml = snap.get("manifest-list", "")
            if ml.startswith(s3_uri_prefix):
                current_ml_key = ml[len("s3://"):].split("/", 1)[1]

    if current_ml_key is None:
        raise RuntimeError(
            f"metadata.json snapshot-id={current_snapshot_id} has no s3:// manifest-list "
            f"(or is at an unexpected path); cannot rewrite"
        )

    # Walk the current manifest-list to collect the manifest paths it points at,
    # then rewrite each manifest.avro before rewriting the manifest-list itself.
    manifest_keys = _rewrite_manifests_referenced_by(
        src_bucket=src_bucket, manifest_list_key=current_ml_key,
        s3_uri_prefix=s3_uri_prefix, gcs_uri_prefix=gcs_uri_prefix,
        gcs_bucket=gcs_bucket, gcs_prefix=gcs_prefix, src_base_prefix=src_base_prefix,
    )
    logger.info(f"[METADATA] rewrote {len(manifest_keys)} manifest(s)")

    # Rewrite every manifest-list referenced by the metadata.json. The current
    # one is the only one BQ will read, but rewriting all snapshots keeps the
    # GCS copy internally consistent so a future time-travel query works.
    for ml_key in manifest_list_keys:
        _rewrite_avro_paths(
            src_bucket=src_bucket, src_key=ml_key,
            s3_uri_prefix=s3_uri_prefix, gcs_uri_prefix=gcs_uri_prefix,
            gcs_bucket=gcs_bucket, gcs_prefix=gcs_prefix, src_base_prefix=src_base_prefix,
        )
    logger.info(f"[METADATA] rewrote {len(manifest_list_keys)} manifest-list(s)")

    # Rewrite the metadata.json itself: top-level `location`, every snapshot's
    # `manifest-list`, and any `metadata-log` entries.
    rewritten_doc = _rewrite_metadata_json(metadata_doc, s3_uri_prefix, gcs_uri_prefix)
    gcs_pointer_key = _dest_key(gcs_prefix, pointer_key[len(src_base_prefix):])
    gcs_bucket.blob(gcs_pointer_key).upload_from_string(
        json.dumps(rewritten_doc), content_type="application/json"
    )
    logger.info(f"[METADATA] uploaded rewritten pointer → gs://{gcs_bucket.name}/{gcs_pointer_key}")

    return gcs_pointer_key


def _metadata_json_version_from_key(key: str) -> int:
    """Extract monotonic version from an Iceberg metadata.json object key.

    Supports Glue catalog ``{NNNNN}-{uuid}.metadata.json`` and Hadoop catalog
    ``v{N}.metadata.json``. Returns -1 when the leaf name is not parseable.
    """
    name = key.rsplit("/", 1)[-1]
    if not name.endswith(".metadata.json"):
        return -1
    stem = name[: -len(".metadata.json")]
    if stem.startswith("v"):
        tail = stem[1:]
        if tail.isdigit():
            return int(tail)
        head = tail.split("-", 1)[0]
        if head.isdigit():
            return int(head)
    head = stem.split("-", 1)[0]
    if head.isdigit():
        return int(head)
    return -1


def _resolve_current_metadata_pointer(src_bucket: str, src_base_prefix: str) -> str:
    """Return the S3 key of the current Iceberg ``*.metadata.json``.

    Resolution order:
      1. ``metadata/version-hint.text`` → ``v{N}.metadata.json`` (Hadoop catalog)
      2. Highest-version ``*.metadata.json`` under ``metadata/`` — supports both
         ``v{N}.metadata.json`` and Glue's ``{NNNNN}-{uuid}.metadata.json``.
    """
    hint_key = f"{src_base_prefix}metadata/version-hint.text"
    try:
        body = s3.get_object(Bucket=src_bucket, Key=hint_key)["Body"].read().decode("utf-8").strip()
        n = int(body)
        return f"{src_base_prefix}metadata/v{n}.metadata.json"
    except s3.exceptions.NoSuchKey:
        logger.info(f"[METADATA] no version-hint.text — falling back to listing *.metadata.json")
    except Exception as exc:
        logger.warning(f"[METADATA] version-hint.text unreadable ({exc}) — falling back")

    paginator = s3.get_paginator("list_objects_v2")
    candidates: list[str] = []
    for page in paginator.paginate(Bucket=src_bucket, Prefix=f"{src_base_prefix}metadata/"):
        for obj in page.get("Contents", []) or []:
            k = obj["Key"]
            if _metadata_json_version_from_key(k) >= 0:
                candidates.append(k)
    if not candidates:
        raise RuntimeError(
            f"no *.metadata.json under s3://{src_bucket}/{src_base_prefix}metadata/"
        )
    return max(candidates, key=_metadata_json_version_from_key)


def _rewrite_manifests_referenced_by(*, src_bucket: str, manifest_list_key: str,
                                      s3_uri_prefix: str, gcs_uri_prefix: str,
                                      gcs_bucket: storage.Bucket, gcs_prefix: str,
                                      src_base_prefix: str) -> list[str]:
    """Read manifest-list, find every referenced manifest, rewrite each."""
    body = s3.get_object(Bucket=src_bucket, Key=manifest_list_key)["Body"].read()
    reader = fastavro.reader(BytesIO(body))
    manifest_keys: list[str] = []
    for rec in reader:
        mp = rec.get("manifest_path") or ""
        if mp.startswith(s3_uri_prefix):
            key = mp[len("s3://"):].split("/", 1)[1]
            if key not in manifest_keys:
                manifest_keys.append(key)

    for mk in manifest_keys:
        _rewrite_avro_paths(
            src_bucket=src_bucket, src_key=mk,
            s3_uri_prefix=s3_uri_prefix, gcs_uri_prefix=gcs_uri_prefix,
            gcs_bucket=gcs_bucket, gcs_prefix=gcs_prefix, src_base_prefix=src_base_prefix,
        )
    return manifest_keys


def _rewrite_avro_paths(*, src_bucket: str, src_key: str,
                         s3_uri_prefix: str, gcs_uri_prefix: str,
                         gcs_bucket: storage.Bucket, gcs_prefix: str,
                         src_base_prefix: str) -> None:
    """Download an Avro file, rewrite every s3:// URI to gs://, re-upload to GCS.

    Walks each record recursively and replaces any string field whose value
    starts with `s3_uri_prefix`. Handles manifest-list records (manifest_path)
    and manifest records (data_file.file_path) without enumerating the schema.
    """
    body = s3.get_object(Bucket=src_bucket, Key=src_key)["Body"].read()
    reader = fastavro.reader(BytesIO(body))
    schema = reader.writer_schema
    records = list(reader)

    def _walk(value):
        if isinstance(value, str):
            if value.startswith(s3_uri_prefix):
                return gcs_uri_prefix + value[len(s3_uri_prefix):]
            return value
        if isinstance(value, dict):
            return {k: _walk(v) for k, v in value.items()}
        if isinstance(value, list):
            return [_walk(v) for v in value]
        return value

    rewritten = [_walk(r) for r in records]

    buf = BytesIO()
    fastavro.writer(buf, schema, rewritten)
    buf.seek(0)
    dst_key = _dest_key(gcs_prefix, src_key[len(src_base_prefix):])
    gcs_bucket.blob(dst_key).upload_from_file(buf, content_type="application/octet-stream")


def _rewrite_metadata_json(doc: dict, s3_uri_prefix: str, gcs_uri_prefix: str) -> dict:
    """Recursive string-substitution on the metadata.json document."""
    def _walk(value):
        if isinstance(value, str):
            if value.startswith(s3_uri_prefix):
                return gcs_uri_prefix + value[len(s3_uri_prefix):]
            return value
        if isinstance(value, dict):
            return {k: _walk(v) for k, v in value.items()}
        if isinstance(value, list):
            return [_walk(v) for v in value]
        return value
    return _walk(doc)


# ── BigQuery DDL ───────────────────────────────────────────────────

def _refresh_bq_external_table(*, bq_client: bigquery.Client, bq_cfg: dict,
                                silver_table: str | None, metadata_uri: str) -> None:
    project = bq_cfg["bq_project"]
    dataset = bq_cfg["bq_dataset"]
    table   = (bq_cfg.get("bq_table") or silver_table or "").lower()
    if not table:
        raise ValueError("bq_table not set and silver.table missing — cannot resolve target table name")

    fqtn = f"`{project}`.`{dataset}`.`{table}`"
    # BigLake Iceberg external table — points at the rewritten metadata pointer
    # in GCS. BQ resolves the snapshot through the manifest chain (now also in GCS).
    ddl = (
        f"CREATE OR REPLACE EXTERNAL TABLE {fqtn} "
        f"OPTIONS (format='ICEBERG', uris=['{metadata_uri}'])"
    )
    logger.info(f"[BQ] {ddl}")
    job = bq_client.query(ddl, location=bq_cfg.get("bq_location"))
    job.result()


# ── helpers ────────────────────────────────────────────────────────

def _get_pipeline_config(pipeline_id: str) -> dict | None:
    resp = dynamo.Table(CONFIG_TABLE).get_item(
        Key={"pipeline_id": pipeline_id, "version": "latest"}
    )
    item = resp.get("Item")
    if item is not None:
        item.setdefault("pipeline_id", pipeline_id)
    return item


def _load_gcp_credentials(secret_arn: str):
    raw = secrets.get_secret_value(SecretId=secret_arn)["SecretString"]
    sa_info = json.loads(raw)
    creds = service_account.Credentials.from_service_account_info(sa_info)
    return creds, sa_info.get("project_id")


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


# ── metrics write ─────────────────────────────────────────────────

def _write_metrics_and_return(*, run_id, pipeline_id, plan, started_at, status,
                              files_ok, files_skipped, files_failed, bytes_ok,
                              metadata_pointer, bq_status, errors):
    completed_at = datetime.now(timezone.utc)
    duration_sec = int((completed_at - started_at).total_seconds())
    bq_cfg = plan["bq_cfg"]
    item = {
        "job_name":           JOB_NAME,
        "run_id":             run_id,
        "pipeline_id":        pipeline_id,
        "trigger":            plan.get("trigger", "manual"),
        "silver_run_id":      plan.get("silver_run_id"),
        "replication_status": status,
        "bq_status":          bq_status,
        "files_replicated":   files_ok,
        "files_skipped":      files_skipped,
        "files_failed":       files_failed,
        "bytes_replicated":   bytes_ok,
        "errors_count":       len(errors),
        "error_message":      " | ".join(errors[:3]) if errors else None,
        "gcs_destination":    f"gs://{bq_cfg['bucket']}/{bq_cfg['prefix']}",
        "metadata_pointer":   metadata_pointer,
        "bq_project":         bq_cfg.get("bq_project"),
        "bq_dataset":         bq_cfg.get("bq_dataset"),
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
        f"bq={bq_status} files_ok={files_ok} files_skipped={files_skipped} "
        f"files_failed={files_failed} bytes={bytes_ok}"
    )
    return _result(status, pipeline_id=pipeline_id,
                   trigger=plan.get("trigger"),
                   silver_run_id=plan.get("silver_run_id"),
                   bq_status=bq_status,
                   metadata_pointer=metadata_pointer,
                   files_replicated=files_ok, files_skipped=files_skipped,
                   files_failed=files_failed, bytes_replicated=bytes_ok,
                   run_id=run_id, error=item.get("error_message"))


def _emit_cw_metrics(pipeline_id, status, files_ok, files_skipped, files_failed,
                     bytes_ok, duration_sec) -> None:
    dims = [{"Name": "PipelineId", "Value": pipeline_id or "unknown"}]
    metrics = [
        {"MetricName": "FilesReplicated",      "Value": files_ok,      "Unit": "Count",   "Dimensions": dims},
        {"MetricName": "FilesSkipped",         "Value": files_skipped, "Unit": "Count",   "Dimensions": dims},
        {"MetricName": "FilesFailed",          "Value": files_failed,  "Unit": "Count",   "Dimensions": dims},
        {"MetricName": "BytesReplicated",      "Value": bytes_ok,      "Unit": "Bytes",   "Dimensions": dims},
        {"MetricName": "DurationSeconds",      "Value": duration_sec,  "Unit": "Seconds", "Dimensions": dims},
        {"MetricName": f"RunStatus_{status}",  "Value": 1,             "Unit": "Count",   "Dimensions": dims},
    ]
    try:
        cloudwatch.put_metric_data(Namespace=METRICS_NAMESPACE, MetricData=metrics)
    except Exception:
        logger.exception("[CW] put_metric_data failed — continuing")


def _result(status: str, **kw) -> dict:
    out = {"status": status}
    out.update({k: v for k, v in kw.items() if v is not None})
    return out
