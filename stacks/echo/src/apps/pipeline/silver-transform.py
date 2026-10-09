# generic_silver_transform.py
# Single reusable AWS Glue job for Silver layer transformation.
# Reads new Bronze Parquet partitions, deduplicates to latest record per primary key,
# and merges into Apache Iceberg tables in the AWS Glue Data Catalog.
# Driven entirely by a DynamoDB config item — no code changes needed per table.

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
# Mirrors the Bronze job's argparse pattern exactly.
# --env is added here so Bronze can pass it when triggering Silver.
# ─────────────────────────────────────────────────────────────────
import argparse as _argparse
_p = _argparse.ArgumentParser(add_help=False)
_p.add_argument("--JOB_NAME",   default="unknown")
_p.add_argument("--JOB_RUN_ID", default="unknown")
_p.add_argument("--env",        default="dev")
_glue_defaults, _ = _p.parse_known_args(sys.argv[1:])

args        = getResolvedOptions(
    sys.argv, ["pipeline_id", "CONFIG_TABLE", "METRICS_TABLE", "SILVER_WAREHOUSE"]
)
JOB_NAME    = _glue_defaults.JOB_NAME
RUN_ID      = _glue_defaults.JOB_RUN_ID
PIPELINE_ID = args["pipeline_id"]
ENV         = _glue_defaults.env
REGION      = "eu-west-1"
CONFIG_TABLE  = args["CONFIG_TABLE"]
METRICS_TABLE = args["METRICS_TABLE"]

# Iceberg warehouse root, passed by Terraform from the stack's own bucket
# variable. REQUIRED, not defaulted: a derived s3://echo-{env}-... value
# silently writes this stack's tables into the other stack's bucket, which
# is what it was doing before UIFPE-355 phase 2 was completed.
SILVER_WAREHOUSE = args["SILVER_WAREHOUSE"]

dynamo     = boto3.resource("dynamodb", region_name=REGION)
started_at = datetime.now(timezone.utc)


# ─────────────────────────────────────────────────────────────────
# HELPERS
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
        "job_name":     JOB_NAME,
        "run_id":       RUN_ID,
        "pipeline_id":  PIPELINE_ID,
        "status":       "SKIPPED",
        "started_at":   started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
        "duration_sec": 0,
        "ttl":          int(time.time()) + (90 * 86400),
    })


def write_metrics(run_status: str, rows_read: int, rows_written: int,
                  silver_table: str, error: str = None, load_phase: str = "incremental"):
    completed_at = datetime.now(timezone.utc)
    item = {
        "job_name":      JOB_NAME,
        "run_id":        RUN_ID,
        "pipeline_id":   PIPELINE_ID,
        "silver_table":  silver_table,
        "status":        run_status,
        "load_phase":    load_phase,
        "rows_read":     rows_read,
        "rows_written":  rows_written,
        "rows_rejected": max(0, rows_read - rows_written),
        "error_message": error,
        "started_at":    started_at.isoformat(),
        "completed_at":  completed_at.isoformat(),
        "duration_sec":  int((completed_at - started_at).total_seconds()),
        "ttl":           int(time.time()) + (90 * 86400),
    }
    dynamo.Table(METRICS_TABLE).put_item(Item=to_dynamo_safe(item))
    print(
        f"[METRICS WRITTEN] status={run_status} rows_written={rows_written} "
        f"duration={item['duration_sec']}s"
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
# MAIN — wrapped in run() so SKIPPED cases can return cleanly
# without sys.exit() (which Glue 5.1 treats as FAILED).
# ─────────────────────────────────────────────────────────────────
def run():
    status       = "FAILED"
    error_msg    = None
    rows_read    = 0
    rows_written = 0
    silver_table = "unknown"
    load_phase   = "incremental"
    new_bronze   = None
    deduped      = None
    spark        = None

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

    primary_key_cols    = silver_cfg.get("primary_key_cols") or []
    # Defence-in-depth: if PK list is empty, Window.partitionBy([]) collapses
    # all bronze rows to a single silver row (EGO_MTL_SY_ITEMS_EXT_B 2026-05-28).
    # Server-side validation in pipeline_controller._validate_silver_config()
    # is the primary guard; this is the last-resort backstop.
    if not primary_key_cols:
        raise ValueError(
            f"silver.primary_key_cols is empty/missing for {PIPELINE_ID} — "
            "refusing to run (would collapse all rows to one via global window)"
        )
    silver_db           = silver_cfg["database"]
    silver_tbl          = silver_cfg["table"]
    silver_s3_path      = silver_cfg["s3_path"]
    last_processed_date = silver_cfg.get("last_processed_date", "1970-01-01")
    bronze_s3_path      = cfg["target"]["s3_path"]
    glue_cfg            = cfg.get("glue", {})
    silver_table        = f"{silver_db}.{silver_tbl}"

    # ─── Init Spark ───────────────────────────────────────────────
    sc      = SparkContext()
    glueCtx = GlueContext(sc)
    spark   = glueCtx.spark_session
    job     = Job(glueCtx)
    job.init(JOB_NAME, args)

    spark.sparkContext.setLogLevel("WARN")
    spark.conf.set("spark.sql.shuffle.partitions",
                   str(glue_cfg.get("shuffle_partitions", 8)))
    # Glue 5.1 defaults S3A to us-east-2 — must match deployment region.
    spark.conf.set("spark.hadoop.fs.s3a.endpoint.region", REGION)

    # Iceberg catalog wired to Glue Data Catalog.
    # --datalake-formats=iceberg sets spark.sql.extensions automatically at
    # SparkSession creation — setting it again here would raise AnalysisException.
    spark.conf.set("spark.sql.catalog.glue_catalog",
        "org.apache.iceberg.spark.SparkCatalog")
    spark.conf.set("spark.sql.catalog.glue_catalog.catalog-impl",
        "org.apache.iceberg.aws.glue.GlueCatalog")
    spark.conf.set("spark.sql.catalog.glue_catalog.io-impl",
        "org.apache.iceberg.aws.s3.S3FileIO")
    spark.conf.set("spark.sql.catalog.glue_catalog.warehouse",
        SILVER_WAREHOUSE)

    print("[SPARK + ICEBERG INITIALISED]")

    try:
        # ─── Read new Bronze partitions ───────────────────────────
        print(f"[READ BRONZE] path={bronze_s3_path} since={last_processed_date}")
        bronze_df  = (
            spark.read
            .option("basePath", bronze_s3_path)
            .parquet(bronze_s3_path)
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

        # MAX(_extract_date) computed here for the watermark advance later.
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

        # ─── Ensure Glue database exists ─────────────────────────
        spark.sql(f"CREATE DATABASE IF NOT EXISTS glue_catalog.`{silver_db}`")

        table_full = f"glue_catalog.`{silver_db}`.`{silver_tbl}`"

        # ─── Create table on first run, merge on subsequent runs ─
        try:
            spark.sql(f"SELECT 1 FROM {table_full} LIMIT 0")
            table_exists = True
        except Exception:
            table_exists = False

        if not table_exists:
            load_phase = "initial"
            print(f"[ICEBERG] creating {table_full} at {silver_s3_path}")
            spark.sql(f"""
                CREATE TABLE {table_full}
                USING iceberg
                LOCATION '{silver_s3_path}'
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

        # ─── Watermark advance guard ─────────────────────────────
        # If dedup rejected >99% of bronze rows on a non-trivial read, treat as
        # anomaly: do not advance the watermark — the bronze partitions remain
        # eligible for the next run after the operator investigates.
        # Catches the empty-PK / global-window fingerprint even if the early
        # ValueError check above is somehow bypassed.
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
        write_metrics(status, rows_read, rows_written, silver_table, error_msg, load_phase)
        if spark is not None:
            spark.stop()

    if status != "SUCCEEDED":
        raise RuntimeError(
            f"Silver pipeline {PIPELINE_ID} failed — see error_message in metrics table."
        )


run()
