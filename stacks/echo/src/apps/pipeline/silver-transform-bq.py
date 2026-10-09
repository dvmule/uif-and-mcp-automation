# generic_silver_transform_gcp_bq.py
# BQ-native Silver transform for GCP pipelines with silver.write_mode="bq_native".
# Sibling of generic_silver_transform_gcp_iceberg.py. Reads Bronze Parquet from
# gs://, deduplicates in Spark (same window logic as the Iceberg variant),
# writes the deduped frame to a transient GCS Parquet prefix, loads it into a
# BigQuery staging table, then MERGEs into the native BQ target.
#
# No Iceberg, no BigLake external table, no metadata pointer resolution. Target
# is a managed BQ table physically owned by BigQuery — downstream queries hit
# BQ-native storage directly. Trade-off vs the Iceberg variant: no time-travel,
# but native partition pruning + clustering and lower per-query latency.
#
# Glue 5.1 (same as the Iceberg variant). NO iceberg-gcp-bundle JAR — only the
# GCS connector is needed for Spark to read Bronze Parquet from gs://. All
# writes to BQ happen via google-cloud-bigquery, not Spark.

import json
import os
import sys
import time
import traceback
import boto3
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from awsglue.utils import getResolvedOptions
from awsglue.context import GlueContext
from awsglue.job import Job
from pyspark.context import SparkContext
from pyspark.sql import functions as F, Window, DataFrame

from bq_partition_ddl import build_partition_clause

# ─────────────────────────────────────────────────────────────────
# BOOTSTRAP
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

# Short suffix used in transient BQ table names so concurrent runs don't collide.
RUN_ID_SHORT = RUN_ID.replace("-", "")[:12] if RUN_ID else "noid"


# ─────────────────────────────────────────────────────────────────
# HELPERS — verbatim from generic_silver_transform_gcp_iceberg.py
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
        "job_name":          JOB_NAME,
        "run_id":            RUN_ID,
        "pipeline_id":       PIPELINE_ID,
        "status":            "SKIPPED",
        "destination_cloud": "gcp",
        "silver_write_mode": "bq_native",
        "started_at":        started_at.isoformat(),
        "completed_at":      completed_at.isoformat(),
        "duration_sec":      0,
        "ttl":               int(time.time()) + (90 * 86400),
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
        "silver_write_mode": "bq_native",
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


def build_pk_conditions(primary_key_cols: list,
                        alias_left: str = "t", alias_right: str = "s") -> str:
    """NULL-safe equality on each PK column.

    Standard `=` returns NULL when either side is NULL, which causes MERGE to
    NOT match (so identical rows with a NULL PK component get INSERTED rather
    than UPDATED — doubling the table on every run). BigQuery's
    `IS NOT DISTINCT FROM` operator returns TRUE when both sides are NULL,
    which is the semantics we want for a composite PK that includes
    nullable columns (e.g. fipm.fpi_par_batch's FCT_PERIOD_ID).
    """
    return " AND ".join([
        f"{alias_left}.`{c}` IS NOT DISTINCT FROM {alias_right}.`{c}`"
        for c in primary_key_cols
    ])


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
# GCP CREDENTIAL BOOTSTRAP
# Ported verbatim from generic_bronze_ingest.py — Spark executors need inline
# creds (driver /tmp keyfile isn't shared), google-cloud-bigquery wants a file.
# ─────────────────────────────────────────────────────────────────
def _bootstrap_gcp_credentials(secret_arn: str) -> tuple[str, str, dict]:
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


# ─────────────────────────────────────────────────────────────────
# BQ WRITE HELPERS
# All operations go through the google-cloud-bigquery Python client. Spark
# never writes to BQ directly — it writes Parquet to a transient GCS prefix
# and we `bq load` from there.
# ─────────────────────────────────────────────────────────────────
def _bq_table_exists(bq_client, fqtn: str) -> bool:
    from google.cloud.exceptions import NotFound
    try:
        bq_client.get_table(fqtn)
        return True
    except NotFound:
        return False


def _bq_load_staging(*, bq_client, gcs_uri: str, staging_fqtn: str,
                     bq_location: str):
    """Load Parquet at gcs_uri into staging_fqtn, then set 24h TTL.

    write_disposition=WRITE_TRUNCATE so re-runs of the same RUN_ID_SHORT
    overwrite cleanly. Schema is auto-detected from Parquet.
    """
    from google.cloud import bigquery
    job_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.PARQUET,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
    )
    load_job = bq_client.load_table_from_uri(
        gcs_uri, staging_fqtn,
        location=bq_location, job_config=job_config,
    )
    load_job.result()
    print(f"[BQ LOAD] {staging_fqtn} ← {gcs_uri} rows={load_job.output_rows}")

    # 24h TTL so abandoned runs auto-clean.
    tbl = bq_client.get_table(staging_fqtn)
    tbl.expires = datetime.now(timezone.utc) + timedelta(hours=24)
    bq_client.update_table(tbl, ["expires"])
    return tbl


def _bq_create_target(*, bq_client, target_fqtn: str, staging_fqtn: str,
                      partition_clause: str, cluster_cols: list,
                      bq_location: str):
    """First-run path: CREATE TABLE … PARTITION BY … CLUSTER BY … AS SELECT *
    FROM staging. BQ infers schema from the staging table."""
    parts = []
    if partition_clause:
        parts.append(partition_clause)
    if cluster_cols:
        cluster_sql = ", ".join(f"`{c}`" for c in cluster_cols[:4])  # BQ caps at 4
        parts.append(f"CLUSTER BY {cluster_sql}")
    partition_cluster_clause = " ".join(parts)

    sql = (
        f"CREATE TABLE `{target_fqtn.replace('.', '`.`')}` "
        f"{partition_cluster_clause} "
        f"AS SELECT * FROM `{staging_fqtn.replace('.', '`.`')}`"
    )
    print(f"[BQ CREATE] {sql}")
    bq_client.query(sql, location=bq_location).result()


def _bq_schema_evolve(*, bq_client, target_fqtn: str, staging_fqtn: str,
                      bq_location: str):
    """Add columns present in staging but missing from target via
    ALTER TABLE … ADD COLUMN IF NOT EXISTS. Type changes are NOT auto-applied
    — they'll surface as a MERGE-time error and require manual bq update."""
    target_tbl  = bq_client.get_table(target_fqtn)
    staging_tbl = bq_client.get_table(staging_fqtn)
    target_cols = {f.name for f in target_tbl.schema}
    new_fields  = [f for f in staging_tbl.schema if f.name not in target_cols]
    if not new_fields:
        return
    for f in new_fields:
        sql = (
            f"ALTER TABLE `{target_fqtn.replace('.', '`.`')}` "
            f"ADD COLUMN IF NOT EXISTS `{f.name}` {f.field_type}"
        )
        print(f"[SCHEMA EVOLVE] {sql}")
        bq_client.query(sql, location=bq_location).result()


def _bq_merge_upsert(*, bq_client, target_fqtn: str, staging_fqtn: str,
                     primary_key_cols: list, bq_location: str) -> int:
    """MERGE staging into target via PK. UPDATE assignments are enumerated
    explicitly because BigQuery does not support `UPDATE SET *` (that's
    Iceberg/Spark syntax). INSERT uses `INSERT ROW` shorthand which BQ does
    support. Returns num_dml_affected_rows."""
    # Fetch staging schema to enumerate columns. Skip PK cols in the SET
    # clause — they're already equal by ON, and updating them is wasted work.
    staging_tbl = bq_client.get_table(staging_fqtn)
    pk_set = set(primary_key_cols)
    non_pk_cols = [f.name for f in staging_tbl.schema if f.name not in pk_set]
    if not non_pk_cols:
        # Edge case: table is PK-only. No-op MERGE — just INSERT new rows.
        update_clause = ""
    else:
        set_assignments = ", ".join(f"t.`{c}` = s.`{c}`" for c in non_pk_cols)
        update_clause = f"WHEN MATCHED THEN UPDATE SET {set_assignments} "

    pk_join = build_pk_conditions(primary_key_cols, "t", "s")
    sql = (
        f"MERGE INTO `{target_fqtn.replace('.', '`.`')}` t "
        f"USING `{staging_fqtn.replace('.', '`.`')}` s "
        f"ON {pk_join} "
        f"{update_clause}"
        f"WHEN NOT MATCHED THEN INSERT ROW"
    )
    print(f"[BQ MERGE] {sql}")
    job = bq_client.query(sql, location=bq_location)
    job.result()
    affected = job.num_dml_affected_rows or 0
    print(f"[BQ MERGE OK] affected_rows={affected}")
    return affected


def _materialise_to_bq(*, deduped_df, silver_cfg: dict, keyfile_path: str,
                       bronze_bucket: str, num_workers: int) -> tuple[str, str, int]:
    """End-to-end: write Parquet to GCS, load into BQ staging, MERGE into target.
    Returns (bq_status, target_fqtn, rows_written)."""
    from google.cloud import bigquery
    from google.oauth2 import service_account

    creds = service_account.Credentials.from_service_account_file(keyfile_path)

    bq_project   = silver_cfg["bq_project"]
    bq_dataset   = silver_cfg["bq_dataset"]
    bq_location  = silver_cfg.get("bq_location") or None
    table_lower  = silver_cfg["table"].lower()
    pk_cols      = silver_cfg["primary_key_cols"]
    partition_clause, part_err = build_partition_clause(silver_cfg)
    if part_err:
        raise ValueError(f"[BQ PARTITION] invalid config: {part_err}")
    if partition_clause:
        print(f"[BQ PARTITION] {partition_clause}")

    target_fqtn  = f"{bq_project}.{bq_dataset}.{table_lower}"
    staging_fqtn = f"{bq_project}.{bq_dataset}._stg_{table_lower}_{RUN_ID_SHORT}"

    # Write deduped frame to transient GCS Parquet.
    staging_gcs_path = f"gs://{bronze_bucket}/_bq_staging/{table_lower}/{RUN_ID_SHORT}/"
    print(f"[STAGING WRITE] gcs={staging_gcs_path}")
    (deduped_df
     .coalesce(max(1, num_workers))
     .write
     .mode("overwrite")
     .parquet(staging_gcs_path))

    bq_client = bigquery.Client(project=bq_project, credentials=creds)

    try:
        _bq_load_staging(
            bq_client=bq_client,
            gcs_uri=f"{staging_gcs_path}*.parquet",
            staging_fqtn=staging_fqtn,
            bq_location=bq_location,
        )

        if not _bq_table_exists(bq_client, target_fqtn):
            print(f"[BQ TARGET] does not exist — creating via CREATE TABLE AS SELECT")
            _bq_create_target(
                bq_client=bq_client,
                target_fqtn=target_fqtn,
                staging_fqtn=staging_fqtn,
                partition_clause=partition_clause,
                cluster_cols=pk_cols,
                bq_location=bq_location,
            )
            rows_written = bq_client.get_table(target_fqtn).num_rows
        else:
            _bq_schema_evolve(
                bq_client=bq_client,
                target_fqtn=target_fqtn,
                staging_fqtn=staging_fqtn,
                bq_location=bq_location,
            )
            rows_written = _bq_merge_upsert(
                bq_client=bq_client,
                target_fqtn=target_fqtn,
                staging_fqtn=staging_fqtn,
                primary_key_cols=pk_cols,
                bq_location=bq_location,
            )

        return "ok", target_fqtn, rows_written

    finally:
        try:
            bq_client.delete_table(staging_fqtn, not_found_ok=True)
            print(f"[CLEANUP] dropped staging {staging_fqtn}")
        except Exception as exc:
            print(f"[CLEANUP WARNING] failed to drop staging {staging_fqtn}: {exc}")


# ─────────────────────────────────────────────────────────────────
# MAIN
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

    write_mode = (silver_cfg.get("write_mode") or "iceberg_biglake").lower()
    if write_mode != "bq_native":
        raise ValueError(
            f"generic_silver_transform_gcp_bq invoked for pipeline {PIPELINE_ID} "
            f"but silver.write_mode='{write_mode}' (expected 'bq_native'). "
            f"Check dispatcher routing."
        )

    dest_cfg = cfg.get("destination") or {}
    if (dest_cfg.get("cloud") or "aws").lower() != "gcp":
        raise ValueError(
            f"generic_silver_transform_gcp_bq invoked for pipeline {PIPELINE_ID} "
            f"but destination.cloud != 'gcp'"
        )
    gcp_secret_arn = dest_cfg.get("gcp_credentials_secret_arn")
    if not gcp_secret_arn:
        raise ValueError("destination.gcp_credentials_secret_arn missing on config")

    primary_key_cols = silver_cfg.get("primary_key_cols") or []
    if not primary_key_cols:
        # Defence-in-depth — same global-window guard as the AWS/Iceberg variants.
        raise ValueError(
            f"silver.primary_key_cols is empty/missing for {PIPELINE_ID} — "
            "refusing to run (would collapse all rows to one via global window)"
        )

    bq_dataset          = silver_cfg["bq_dataset"]
    silver_tbl_lower    = silver_cfg["table"].lower()
    last_processed_date = silver_cfg.get("last_processed_date", "1970-01-01")
    bronze_gcs_path     = cfg["target"]["gcs_path"]
    glue_cfg            = cfg.get("glue", {})
    num_workers         = int(glue_cfg.get("num_workers", 2))
    silver_table        = f"{bq_dataset}.{silver_tbl_lower}"

    bronze_bucket, _ = _parse_gs_path(bronze_gcs_path)

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

    # GCS Hadoop connector — inline creds on the live Hadoop conf.
    # Required so Spark can read Bronze Parquet from gs:// and write the
    # transient staging Parquet back to gs://. See feedback memory
    # `feedback_gcp_glue_classpath_auth` for why this exact incantation.
    hconf = sc._jsc.hadoopConfiguration()
    hconf.set("fs.gs.impl",
              "com.google.cloud.hadoop.fs.gcs.GoogleHadoopFileSystem")
    hconf.set("fs.AbstractFileSystem.gs.impl",
              "com.google.cloud.hadoop.fs.gcs.GoogleHadoopFS")
    hconf.set("fs.gs.project.id", project_id)
    hconf.set("fs.gs.auth.service.account.enable", "true")
    hconf.set("google.cloud.auth.service.account.enable", "true")
    hconf.set("fs.gs.auth.service.account.email",          sa_info["client_email"])
    hconf.set("fs.gs.auth.service.account.private.key.id", sa_info["private_key_id"])
    hconf.set("fs.gs.auth.service.account.private.key",    sa_info["private_key"])
    hconf.set("google.cloud.auth.service.account.email",          sa_info["client_email"])
    hconf.set("google.cloud.auth.service.account.private.key.id", sa_info["private_key_id"])
    hconf.set("google.cloud.auth.service.account.private.key",    sa_info["private_key"])

    print("[SPARK INITIALISED] BQ-native Silver mode")

    try:
        # ─── Read new Bronze partitions from GCS ──────────────────
        print(f"[READ BRONZE] path={bronze_gcs_path} since={last_processed_date}")
        bronze_df = (
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

        max_date_row       = new_bronze.agg(F.max("_extract_date").alias("d")).collect()[0]
        max_extract_date   = max_date_row["d"]
        new_last_processed = (
            max_extract_date.isoformat()
            if hasattr(max_extract_date, "isoformat")
            else str(max_extract_date)
        )

        # ─── Deduplicate ─────────────────────────────────────────
        deduped      = deduplicate(new_bronze, primary_key_cols)
        deduped.cache()
        rows_after_dedup = deduped.count()
        print(
            f"[DEDUP] after dedup={rows_after_dedup} "
            f"(dropped {rows_read - rows_after_dedup} duplicates)"
        )

        # ─── Watermark-advance anomaly guard ─────────────────────
        rows_rejected = rows_read - rows_after_dedup
        if rows_read > 100 and rows_rejected > 0.99 * rows_read:
            raise RuntimeError(
                f"[ANOMALY] {rows_rejected}/{rows_read} rows rejected by dedup "
                f"({rows_rejected/rows_read*100:.2f}%) — refusing to advance "
                f"watermark from {last_processed_date}. Inspect "
                f"primary_key_cols={primary_key_cols} and bronze partitions."
            )

        # ─── Materialise to BQ ───────────────────────────────────
        bq_status, bq_table_fqtn, rows_written = _materialise_to_bq(
            deduped_df=deduped,
            silver_cfg=silver_cfg,
            keyfile_path=keyfile_path,
            bronze_bucket=bronze_bucket,
            num_workers=num_workers,
        )
        # rows_written from MERGE = total affected (matched + inserted); for
        # downstream visibility report the dedup count which is the input volume.
        rows_written = rows_after_dedup
        print(f"[SILVER] rows_written={rows_written} target={bq_table_fqtn}")

        # ─── Advance Silver watermark ─────────────────────────────
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
