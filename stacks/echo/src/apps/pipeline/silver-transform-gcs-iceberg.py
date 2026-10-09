# generic_silver_transform_gcp_iceberg.py
# Sibling of generic_silver_transform.py for the GCP-native Silver path
# (destination.cloud="gcp"). Reads Bronze Parquet from gs://, writes Iceberg
# to a GCS warehouse via the Hadoop catalog, then refreshes a BigQuery
# BigLake EXTERNAL TABLE so the Silver snapshot is queryable from BQ.
#
# Why a sibling job (and not a flag in generic_silver_transform.py):
#   * Glue 5.1 ships GCSFileIO + SparkCatalog (Hadoop) but NOT
#     org.apache.iceberg.gcp.bigquery.BigQueryMetastoreCatalog (Phase 0,
#     2026-06-10). We therefore use the Hadoop catalog and post-write DDL
#     against BigQuery to publish a BigLake view. The AWS Silver job, by
#     contrast, uses GlueCatalog directly. Two different catalogs +
#     different post-write registration steps = two scripts is cleanest.
#   * Keeps the AWS path zero-risk during rollout — no behavioural change
#     to the existing job.
#
# Helpers ported verbatim from siblings:
#   * `_bootstrap_gcp_credentials` from generic_bronze_ingest.py — same
#     /tmp/gcp-sa.json pattern (Hadoop GCS connector wants a keyfile on
#     disk; google-cloud-bigquery wants the same file).
#   * `to_dynamo_safe`, `load_config`, `write_metrics`,
#     `_write_skipped_metrics`, `build_pk_conditions`, `deduplicate`,
#     and the watermark advance — all copied from generic_silver_transform.py.
#   * BigLake DDL pattern (`CREATE OR REPLACE EXTERNAL TABLE … OPTIONS
#     (format='ICEBERG', uris=[...])`) — see silver_bq_replicator.py's
#     `_refresh_bq_external_table` for the documented reference.
#   * Metadata-pointer resolution — see silver_bq_replicator.py's
#     `_resolve_current_metadata_pointer`. In *this* job the Iceberg
#     warehouse is *already* GCS-native, so we resolve the pointer from
#     gs:// instead of s3:// and do NOT rewrite any URIs (the metadata
#     chain already references gs:// data files).

import json
import os
import sys
import time
import traceback
import boto3
from datetime import datetime, timezone
from decimal import Decimal

from awsglue.utils import getResolvedOptions
from awsglue.context import GlueContext
from awsglue.job import Job
from pyspark.context import SparkContext
from pyspark.sql import functions as F, Window, DataFrame

# ─────────────────────────────────────────────────────────────────
# STEP 1 — BOOTSTRAP
# Matches the AWS Silver job's argparse pattern.
# ─────────────────────────────────────────────────────────────────
import argparse as _argparse
_p = _argparse.ArgumentParser(add_help=False)
_p.add_argument("--JOB_NAME",   default="unknown")
_p.add_argument("--JOB_RUN_ID", default="unknown")
_p.add_argument("--env",        default="dev")
_glue_defaults, _ = _p.parse_known_args(sys.argv[1:])

args        = getResolvedOptions(sys.argv, ["pipeline_id", "CONFIG_TABLE", "METRICS_TABLE"])
JOB_NAME    = _glue_defaults.JOB_NAME
RUN_ID      = _glue_defaults.JOB_RUN_ID
PIPELINE_ID = args["pipeline_id"]
ENV         = _glue_defaults.env
REGION      = "eu-west-1"
CONFIG_TABLE  = args["CONFIG_TABLE"]
METRICS_TABLE = args["METRICS_TABLE"]

dynamo     = boto3.resource("dynamodb",       region_name=REGION)
secrets    = boto3.client("secretsmanager",   region_name=REGION)
started_at = datetime.now(timezone.utc)


# ─────────────────────────────────────────────────────────────────
# HELPERS (verbatim from generic_silver_transform.py except where noted)
# ─────────────────────────────────────────────────────────────────
def to_dynamo_safe(obj):
    if isinstance(obj, float):
        return Decimal(str(obj))
    if isinstance(obj, dict):
        return {k: to_dynamo_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [to_dynamo_safe(i) for i in obj]
    return obj


def load_config() -> dict:
    resp = dynamo.Table(CONFIG_TABLE).get_item(
        Key={"pipeline_id": PIPELINE_ID, "version": "latest"}
    )
    if "Item" not in resp:
        raise ValueError(f"No config found for pipeline_id={PIPELINE_ID} version=latest")
    return resp["Item"]


def _write_skipped_metrics():
    completed_at = datetime.now(timezone.utc)
    dynamo.Table(METRICS_TABLE).put_item(Item={
        "job_name":         JOB_NAME,
        "run_id":           RUN_ID,
        "pipeline_id":      PIPELINE_ID,
        "status":           "SKIPPED",
        "destination_cloud": "gcp",
        "started_at":       started_at.isoformat(),
        "completed_at":     completed_at.isoformat(),
        "duration_sec":     0,
        "ttl":              int(time.time()) + (90 * 86400),
    })


def write_metrics(run_status: str, rows_read: int, rows_written: int,
                  silver_table: str, error: str = None,
                  bq_status: str = "not_attempted", bq_table: str = None):
    completed_at = datetime.now(timezone.utc)
    item = {
        "job_name":          JOB_NAME,
        "run_id":            RUN_ID,
        "pipeline_id":       PIPELINE_ID,
        "silver_table":      silver_table,
        "destination_cloud": "gcp",
        "bq_status":         bq_status,
        "bq_table":          bq_table,
        "status":            run_status,
        "rows_read":         rows_read,
        "rows_written":      rows_written,
        "rows_rejected":     max(0, rows_read - rows_written),
        "error_message":     error,
        "started_at":        started_at.isoformat(),
        "completed_at":      completed_at.isoformat(),
        "duration_sec":      int((completed_at - started_at).total_seconds()),
        "ttl":               int(time.time()) + (90 * 86400),
    }
    item = {k: v for k, v in item.items() if v is not None}
    dynamo.Table(METRICS_TABLE).put_item(Item=to_dynamo_safe(item))
    print(
        f"[METRICS WRITTEN] status={run_status} rows_written={rows_written} "
        f"bq_status={bq_status} duration={item['duration_sec']}s"
    )


def build_pk_conditions(primary_key_cols: list) -> str:
    return " AND ".join([f"t.`{c}` = s.`{c}`" for c in primary_key_cols])


def deduplicate(df: DataFrame, primary_key_cols: list) -> DataFrame:
    w = Window.partitionBy(primary_key_cols).orderBy(F.desc("_extract_timestamp"))
    return (
        df
        .withColumn("_row_num", F.row_number().over(w))
        .filter(F.col("_row_num") == 1)
        .withColumn("_silver_updated_at", F.current_timestamp())
        .drop("_row_num", "_pipeline_id", "_glue_run_id",
              "_extract_mode", "_extract_date", "_extract_timestamp")
    )


# ─────────────────────────────────────────────────────────────────
# GCP HELPERS
# Ported verbatim from generic_bronze_ingest.py._bootstrap_gcp_credentials.
# Reused for BigQuery DDL — google-cloud-bigquery loads creds from the same
# /tmp/gcp-sa.json keyfile.
# ─────────────────────────────────────────────────────────────────
def _bootstrap_gcp_credentials(secret_arn: str) -> tuple[str, str, dict]:
    """Fetch GCP SA JSON. Writes /tmp keyfile (for google-cloud-bigquery
    Python client) AND returns the parsed dict so the Hadoop GCS connector
    can be configured with inline credentials — executors don't share the
    driver's /tmp filesystem so a keyfile path is invisible to them."""
    raw = secrets.get_secret_value(SecretId=secret_arn)["SecretString"]
    sa_info = json.loads(raw)
    project_id = sa_info.get("project_id")
    if not project_id:
        raise ValueError(f"GCP service-account secret {secret_arn} missing project_id")
    keyfile = "/tmp/gcp-sa.json"
    with open(keyfile, "w") as f:
        f.write(raw)
    os.chmod(keyfile, 0o600)
    print(f"[GCP CREDENTIALS] keyfile=/tmp/gcp-sa.json project={project_id}")
    return keyfile, project_id, sa_info


def _parse_gs_path(gs_path: str) -> tuple[str, str]:
    """gs://bucket/prefix → (bucket, prefix-with-trailing-slash)."""
    if not gs_path.startswith("gs://"):
        raise ValueError(f"expected gs:// URI, got {gs_path!r}")
    without_scheme = gs_path[len("gs://"):]
    bucket, _, prefix = without_scheme.partition("/")
    if prefix and not prefix.endswith("/"):
        prefix += "/"
    return bucket, prefix


def _resolve_current_metadata_pointer_gcs(*, gcs_client, bucket_name: str,
                                          base_prefix: str, db: str, table: str) -> str:
    """Return the gs:// URI of the current Iceberg metadata.json under the
    Hadoop-catalog warehouse.

    Hadoop catalog layout:
        <warehouse>/<database>/<table>/metadata/version-hint.text  → "N"
        <warehouse>/<database>/<table>/metadata/v{N}.metadata.json

    Ported from silver_bq_replicator._resolve_current_metadata_pointer; same
    fallback to lexicographic max if version-hint is absent (older snapshots
    or in-flight writes).
    """
    table_prefix = f"{base_prefix}{db}/{table}/"
    hint_blob = gcs_client.bucket(bucket_name).blob(f"{table_prefix}metadata/version-hint.text")

    try:
        body = hint_blob.download_as_text().strip()
        n = int(body)
        return f"gs://{bucket_name}/{table_prefix}metadata/v{n}.metadata.json"
    except Exception as exc:
        print(f"[METADATA] version-hint.text unreadable ({exc}) — falling back to listing")

    candidates: list[str] = []
    for blob in gcs_client.list_blobs(bucket_name, prefix=f"{table_prefix}metadata/"):
        name = blob.name
        if name.endswith(".metadata.json") and "/v" in name:
            candidates.append(name)
    if not candidates:
        raise RuntimeError(
            f"no v*.metadata.json under gs://{bucket_name}/{table_prefix}metadata/"
        )

    def _ver(key: str) -> int:
        leaf = key.rsplit("/", 1)[-1]
        try:
            return int(leaf.split(".")[0][1:])
        except Exception:
            return -1

    best = max(candidates, key=_ver)
    return f"gs://{bucket_name}/{best}"


def _refresh_biglake_external_table(*, silver_cfg: dict, keyfile_path: str) -> tuple[str, str]:
    """Issue `CREATE OR REPLACE EXTERNAL TABLE` so BQ points at the current
    Iceberg snapshot.

    Returns (bq_status, bq_fqtn). Non-fatal failures return ("failed", fqtn);
    the Iceberg data IS written regardless, the operator can re-register the
    BigLake view via the silver_bq_replicator manual entrypoint.

    DDL pattern is verbatim from silver_bq_replicator._refresh_bq_external_table
    — the only difference is we load creds from a keyfile (since this runs in
    Glue, not Lambda) and the metadata URI is gs:// directly (no rewrite
    needed: the Iceberg warehouse is already on GCS).
    """
    # Imports deferred — google-cloud-bigquery is installed via
    # --additional-python-modules on the Glue job, not pre-bundled.
    from google.cloud import bigquery, storage
    from google.oauth2 import service_account

    creds = service_account.Credentials.from_service_account_file(keyfile_path)

    bq_project   = silver_cfg["bq_project"]
    bq_dataset   = silver_cfg["bq_dataset"]
    bq_location  = silver_cfg.get("bq_location") or None
    table_lower  = silver_cfg["table"].lower()

    gcs_warehouse = silver_cfg["gcs_path"]
    bucket_name, base_prefix = _parse_gs_path(gcs_warehouse)

    gcs_client = storage.Client(project=bq_project, credentials=creds)
    metadata_uri = _resolve_current_metadata_pointer_gcs(
        gcs_client=gcs_client, bucket_name=bucket_name,
        base_prefix=base_prefix, db=bq_dataset, table=table_lower,
    )
    print(f"[BIGLAKE] metadata uri={metadata_uri}")

    fqtn = f"`{bq_project}`.`{bq_dataset}`.`{table_lower}`"
    ddl = (
        f"CREATE OR REPLACE EXTERNAL TABLE {fqtn} "
        f"OPTIONS (format='ICEBERG', uris=['{metadata_uri}'])"
    )
    print(f"[BIGLAKE DDL] {ddl}")

    try:
        bq_client = bigquery.Client(project=bq_project, credentials=creds)
        job = bq_client.query(ddl, location=bq_location)
        job.result()
        print(f"[BIGLAKE OK] {fqtn}")
        return "ok", fqtn
    except Exception as exc:
        # Non-fatal: Iceberg data is already written. Surface in metrics
        # so operators can rerun the BigLake registration out-of-band.
        print(f"[BIGLAKE WARNING] CREATE OR REPLACE EXTERNAL TABLE failed: {exc}")
        return "failed", fqtn


# ─────────────────────────────────────────────────────────────────
# MAIN — wrapped in run() so SKIPPED cases can return cleanly
# without sys.exit() (which Glue 5.1 treats as FAILED).
# ─────────────────────────────────────────────────────────────────
def run():
    status         = "FAILED"
    error_msg      = None
    rows_read      = 0
    rows_written   = 0
    silver_table   = "unknown"
    bq_status      = "not_attempted"
    bq_table_fqtn  = None
    new_bronze     = None
    deduped        = None
    spark          = None

    cfg = load_config()
    print(f"[CONFIG LOADED] pipeline_id={PIPELINE_ID}")

    if not cfg.get("enabled", True):
        print(f"[SKIPPED] pipeline {PIPELINE_ID} is disabled")
        _write_skipped_metrics()
        return

    silver_cfg = cfg.get("silver", {})
    if not silver_cfg.get("enabled", False):
        print(f"[SKIPPED] silver not enabled for {PIPELINE_ID}")
        _write_skipped_metrics()
        return

    dest_cfg = cfg.get("destination") or {}
    if (dest_cfg.get("cloud") or "aws").lower() != "gcp":
        # Defensive: dispatcher routes by cloud, but if a Lambda-side bug
        # ever invokes this job for an AWS pipeline we want a clear failure
        # not a silent mis-write.
        raise ValueError(
            f"generic_silver_transform_gcp_iceberg invoked for pipeline "
            f"{PIPELINE_ID} but destination.cloud != 'gcp'"
        )
    gcp_secret_arn = dest_cfg.get("gcp_credentials_secret_arn")
    if not gcp_secret_arn:
        raise ValueError("destination.gcp_credentials_secret_arn missing on config")

    primary_key_cols = silver_cfg.get("primary_key_cols") or []
    # Defence-in-depth — same global-window guard as the AWS Silver job
    # (see EGO_MTL_SY_ITEMS_EXT_B 2026-05-28 incident in CLAUDE.md).
    if not primary_key_cols:
        raise ValueError(
            f"silver.primary_key_cols is empty/missing for {PIPELINE_ID} — "
            "refusing to run (would collapse all rows to one via global window)"
        )

    silver_gcs_path     = silver_cfg["gcs_path"]
    bq_dataset          = silver_cfg["bq_dataset"]
    silver_tbl          = silver_cfg["table"]
    silver_tbl_lower    = silver_tbl.lower()
    last_processed_date = silver_cfg.get("last_processed_date", "1970-01-01")
    bronze_gcs_path     = cfg["target"]["gcs_path"]
    glue_cfg            = cfg.get("glue", {})
    silver_table        = f"{bq_dataset}.{silver_tbl_lower}"

    # ─── Bootstrap GCP credentials BEFORE Spark starts ────────────
    keyfile_path, project_id, sa_info = _bootstrap_gcp_credentials(gcp_secret_arn)

    # ─── Init Spark ───────────────────────────────────────────────
    sc      = SparkContext()
    glueCtx = GlueContext(sc)
    spark   = glueCtx.spark_session
    job     = Job(glueCtx)
    job.init(JOB_NAME, args)

    spark.sparkContext.setLogLevel("WARN")
    spark.conf.set("spark.sql.shuffle.partitions",
                   str(glue_cfg.get("shuffle_partitions", 8)))

    # GCS Hadoop connector — must use sc._jsc.hadoopConfiguration() (NOT
    # spark.conf.set with spark.hadoop.* prefix), because conf mutations
    # after SparkContext init don't propagate to the live Hadoop conf or
    # to executors. Credentials are passed inline (no keyfile path) since
    # /tmp on the driver isn't visible to executors.
    hconf = sc._jsc.hadoopConfiguration()
    hconf.set("fs.gs.impl",
              "com.google.cloud.hadoop.fs.gcs.GoogleHadoopFileSystem")
    hconf.set("fs.AbstractFileSystem.gs.impl",
              "com.google.cloud.hadoop.fs.gcs.GoogleHadoopFS")
    hconf.set("fs.gs.project.id", project_id)
    hconf.set("fs.gs.auth.service.account.enable", "true")
    hconf.set("google.cloud.auth.service.account.enable", "true")
    hconf.set("fs.gs.auth.service.account.email",         sa_info["client_email"])
    hconf.set("fs.gs.auth.service.account.private.key.id", sa_info["private_key_id"])
    hconf.set("fs.gs.auth.service.account.private.key",    sa_info["private_key"])
    hconf.set("google.cloud.auth.service.account.email",         sa_info["client_email"])
    hconf.set("google.cloud.auth.service.account.private.key.id", sa_info["private_key_id"])
    hconf.set("google.cloud.auth.service.account.private.key",    sa_info["private_key"])

    # Iceberg catalog wired to a *Hadoop* catalog rooted at the GCS warehouse.
    # See module docstring for why we're not using BigQueryMetastoreCatalog.
    # `--datalake-formats=iceberg` already sets spark.sql.extensions at session
    # creation; setting it again would raise AnalysisException.
    spark.conf.set("spark.sql.catalog.bq_iceberg",
        "org.apache.iceberg.spark.SparkCatalog")
    spark.conf.set("spark.sql.catalog.bq_iceberg.type",       "hadoop")
    # HadoopFileIO routes all GCS I/O through the Hadoop GCS connector
    # (already authenticated above via hconf). GCSFileIO would use the
    # GCP Java client library which has its own ADC-based auth path and
    # would 401 on executors without GOOGLE_APPLICATION_CREDENTIALS +
    # the keyfile distributed to every worker.
    spark.conf.set("spark.sql.catalog.bq_iceberg.io-impl",
        "org.apache.iceberg.hadoop.HadoopFileIO")
    spark.conf.set("spark.sql.catalog.bq_iceberg.warehouse", silver_gcs_path)

    print("[SPARK + ICEBERG (HADOOP/GCS) INITIALISED]")

    try:
        # ─── Read new Bronze partitions from GCS ──────────────────
        print(f"[READ BRONZE] path={bronze_gcs_path} since={last_processed_date}")
        bronze_df  = (
            spark.read
            .option("basePath", bronze_gcs_path)
            .parquet(bronze_gcs_path)
        )
        new_bronze = bronze_df.filter(
            F.col("_extract_date") > F.to_date(F.lit(last_processed_date))
        )
        new_bronze.cache()

        rows_read = new_bronze.count()
        print(f"[READ BRONZE] new rows={rows_read}")

        if rows_read == 0:
            print("[SILVER] no new Bronze partitions — already up to date")
            status = "SUCCEEDED"
            job.commit()
            return

        max_date_row        = new_bronze.agg(F.max("_extract_date").alias("d")).collect()[0]
        max_extract_date    = max_date_row["d"]
        new_last_processed  = (
            max_extract_date.isoformat()
            if hasattr(max_extract_date, "isoformat")
            else str(max_extract_date)
        )

        # ─── Deduplicate ─────────────────────────────────────────
        deduped      = deduplicate(new_bronze, primary_key_cols)
        deduped.cache()
        rows_written = deduped.count()
        print(
            f"[DEDUP] after dedup={rows_written} "
            f"(dropped {rows_read - rows_written} duplicates)"
        )
        deduped.createOrReplaceTempView("silver_updates")

        # ─── Ensure database directory exists in the Hadoop catalog ─
        # In a Hadoop catalog the "database" is just a sub-directory of
        # the warehouse — CREATE DATABASE creates the path.
        spark.sql(f"CREATE DATABASE IF NOT EXISTS bq_iceberg.`{bq_dataset}`")

        table_full = f"bq_iceberg.`{bq_dataset}`.`{silver_tbl_lower}`"

        # ─── Create table on first run, merge on subsequent runs ─
        try:
            spark.sql(f"SELECT 1 FROM {table_full} LIMIT 0")
            table_exists = True
        except Exception:
            table_exists = False

        if not table_exists:
            print(f"[ICEBERG] creating {table_full} under {silver_gcs_path}")
            spark.sql(f"""
                CREATE TABLE {table_full}
                USING iceberg
                AS SELECT * FROM silver_updates
            """)
        else:
            print(f"[ICEBERG] merging into {table_full}")
            pk_join = build_pk_conditions(primary_key_cols)
            spark.sql(f"""
                MERGE INTO {table_full} t
                USING silver_updates s
                ON {pk_join}
                WHEN MATCHED THEN UPDATE SET *
                WHEN NOT MATCHED THEN INSERT *
            """)

        print(f"[SILVER] rows written={rows_written}")

        # ─── Watermark advance anomaly guard ─────────────────────
        # Same 99%-rejection threshold as the AWS Silver job.
        rows_rejected = rows_read - rows_written
        if rows_read > 100 and rows_rejected > 0.99 * rows_read:
            error_msg = (
                f"[ANOMALY] {rows_rejected}/{rows_read} rows rejected by dedup "
                f"({rows_rejected/rows_read*100:.2f}%) — refusing to advance "
                f"watermark from {last_processed_date}. Inspect "
                f"primary_key_cols={primary_key_cols} and bronze partitions."
            )
            print(error_msg)
            raise RuntimeError(error_msg)

        # ─── Refresh BigLake external table (non-fatal on failure) ─
        try:
            bq_status, bq_table_fqtn = _refresh_biglake_external_table(
                silver_cfg=silver_cfg, keyfile_path=keyfile_path,
            )
        except Exception as exc:
            # Helper already handles BQ-client failures and returns ("failed",...).
            # This catches *credential* / *metadata pointer* failures.
            print(f"[BIGLAKE WARNING] could not refresh external table: {exc}")
            bq_status = "failed"

        # ─── Advance Silver watermark ─────────────────────────────
        # "silver" and "metadata" are DynamoDB reserved words — alias both.
        dynamo.Table(CONFIG_TABLE).update_item(
            Key={"pipeline_id": PIPELINE_ID, "version": "latest"},
            UpdateExpression=(
                "SET #slv.last_processed_date = :d, "
                "#meta.last_run_id = :rid, "
                "#meta.last_run_at = :ts"
            ),
            ExpressionAttributeNames={
                "#slv":  "silver",
                "#meta": "metadata",
            },
            ExpressionAttributeValues={
                ":d":   new_last_processed,
                ":rid": RUN_ID,
                ":ts":  datetime.now(timezone.utc).isoformat(),
            },
        )
        print(f"[WATERMARK ADVANCED] {last_processed_date} → {new_last_processed}")

        status = "SUCCEEDED"
        job.commit()

    except Exception:
        error_msg = traceback.format_exc()
        print(f"[FATAL ERROR]\n{error_msg}")

    finally:
        for df in (new_bronze, deduped):
            if df is not None:
                try:
                    df.unpersist()
                except Exception:
                    pass
        write_metrics(
            status, rows_read, rows_written, silver_table,
            error=error_msg, bq_status=bq_status, bq_table=bq_table_fqtn,
        )
        if spark is not None:
            spark.stop()

    if status != "SUCCEEDED":
        raise RuntimeError(
            f"Silver pipeline {PIPELINE_ID} failed — see error_message in metrics table."
        )


run()
