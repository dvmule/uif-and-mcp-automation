# generic_silver_reconcile.py
# Nightly hard-delete reconciliation job for Silver Iceberg tables.
# Scans Oracle primary keys, anti-joins against Iceberg to identify orphan rows
# (rows no longer in Oracle), and deletes them with safety caps and dry-run support.
# Driven entirely by DynamoDB config item — no code changes per table.

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
from pyspark.sql import functions as F, DataFrame

# ─────────────────────────────────────────────────────────────────
# BOOTSTRAP
# Mirrors Bronze/Silver's argparse pattern for Glue 4.0 compatibility.
# ─────────────────────────────────────────────────────────────────
import argparse as _argparse
_p = _argparse.ArgumentParser(add_help=False)
_p.add_argument("--JOB_NAME",   default="unknown")
_p.add_argument("--JOB_RUN_ID", default="unknown")
_p.add_argument("--env",        default="dev")
_p.add_argument("--dry-run",    action="store_true", default=False)
_glue_defaults, _ = _p.parse_known_args(sys.argv[1:])

args        = getResolvedOptions(
    sys.argv, ["pipeline_id", "CONFIG_TABLE", "METRICS_TABLE", "SILVER_WAREHOUSE"]
)
JOB_NAME    = _glue_defaults.JOB_NAME
RUN_ID      = _glue_defaults.JOB_RUN_ID
PIPELINE_ID = args["pipeline_id"]
ENV         = _glue_defaults.env
DRY_RUN     = _glue_defaults.dry_run
REGION      = "eu-west-1"
CONFIG_TABLE  = args["CONFIG_TABLE"]
METRICS_TABLE = args["METRICS_TABLE"]

# Iceberg warehouse root, passed by Terraform from the stack's own bucket
# variable. REQUIRED, not defaulted: a derived s3://echo-{env}-... value
# silently writes this stack's tables into the other stack's bucket, which
# is what it was doing before UIFPE-355 phase 2 was completed.
SILVER_WAREHOUSE = args["SILVER_WAREHOUSE"]

dynamo     = boto3.resource("dynamodb", region_name=REGION)
secrets    = boto3.client("secretsmanager", region_name=REGION)
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


def _write_skipped_metrics(reason: str = ""):
    completed_at = datetime.now(timezone.utc)
    status_msg = f"SKIPPED ({reason})" if reason else "SKIPPED"
    dynamo.Table(METRICS_TABLE).put_item(Item={
        "job_name":     JOB_NAME,
        "run_id":       RUN_ID,
        "pipeline_id":  PIPELINE_ID,
        "status":       status_msg,
        "started_at":   started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
        "duration_sec": 0,
        "ttl":          int(time.time()) + (90 * 86400),
    })


def write_metrics(run_status: str, orphan_count: int = 0, rows_deleted: int = 0, error: str = None):
    completed_at = datetime.now(timezone.utc)
    item = {
        "job_name":      JOB_NAME,
        "run_id":        RUN_ID,
        "pipeline_id":   PIPELINE_ID,
        "status":        run_status,
        "orphan_count":  orphan_count,
        "rows_deleted":  rows_deleted,
        "error_message": error,
        "started_at":    started_at.isoformat(),
        "completed_at":  completed_at.isoformat(),
        "duration_sec":  int((completed_at - started_at).total_seconds()),
        "ttl":           int(time.time()) + (90 * 86400),
    }
    dynamo.Table(METRICS_TABLE).put_item(Item=to_dynamo_safe(item))
    print(
        f"[METRICS WRITTEN] status={run_status} orphans={orphan_count} "
        f"rows_deleted={rows_deleted} duration={item['duration_sec']}s"
    )


def build_pk_conditions(primary_key_cols: list, alias_left: str = "t", alias_right: str = "o") -> str:
    """Build the ON clause for MERGE/JOIN statements from primary key column names."""
    return " AND ".join([f"{alias_left}.`{c}` = {alias_right}.`{c}`" for c in primary_key_cols])


def resolve_credentials(secret_arn: str) -> tuple[str, str, str]:
    """Fetch Oracle username, password, JDBC URL from Secrets Manager."""
    secret_str = secrets.get_secret_value(SecretId=secret_arn)["SecretString"]
    import json
    creds = json.loads(secret_str)
    dsn = creds.get("dsn") or "//{host}:{port}/{service}".format(**creds)
    jdbc_url = f"jdbc:oracle:thin:@{dsn}"
    # Include optional jdbc_extra_options if provided
    return creds["username"], creds["password"], jdbc_url


# NLS session init — run once per JDBC connection
_NLS_INIT = (
    "BEGIN "
    "EXECUTE IMMEDIATE 'ALTER SESSION SET NLS_DATE_FORMAT=''YYYY-MM-DD\"T\"HH24:MI:SS'''; "
    "EXECUTE IMMEDIATE 'ALTER SESSION SET TIME_ZONE=''UTC'''; "
    "END;"
)


# ─────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────
def run():
    status         = "FAILED"
    error_msg      = None
    orphan_count   = 0
    rows_deleted   = 0
    oracle_pks     = None
    iceberg_pks    = None
    spark          = None

    cfg = load_config()
    print(f"[CONFIG LOADED] pipeline_id={PIPELINE_ID}")

    if not cfg.get("enabled", True):
        print(f"[SKIPPED] pipeline {PIPELINE_ID} is disabled")
        _write_skipped_metrics("disabled")
        return

    silver_cfg = cfg.get("silver", {})
    if not silver_cfg.get("enabled", False):
        print(f"[SKIPPED] silver not enabled for {PIPELINE_ID}")
        _write_skipped_metrics("silver disabled")
        return

    rec_cfg = cfg.get("reconcile", {})
    if not rec_cfg.get("enabled", False):
        print(f"[SKIPPED] reconciliation not enabled for {PIPELINE_ID}")
        _write_skipped_metrics("reconcile disabled")
        return

    primary_key_cols  = silver_cfg["primary_key_cols"]
    silver_db         = silver_cfg["database"]
    silver_tbl        = silver_cfg["table"]
    extract_table     = cfg["extract"]["table"]
    secret_arn        = cfg["source"]["secret_arn"]
    max_deletes_per_run = int(rec_cfg.get("max_deletes_per_run", 5000))

    # Phase-aware extract view. After migration the JDBC tuning lives under
    # extract.initial / extract.incremental; reconcile picks the same phase
    # the bronze job will use on the next dispatch so JDBC timeouts/fetchsize
    # stay reachable through the schema change. See lambda/pipeline_profiles.py.
    _SENTINEL = {"", None, "1970-01-01T00:00:00"}
    _e        = cfg.get("extract") or {}
    _phase    = (
        "initial" if _e.get("mode") == "full"
        or _e.get("watermark_value") in _SENTINEL
        else "incremental"
    )
    _phase_block = _e.get(_phase) or {}
    ext_resolved = {**_e, **_phase_block}
    print(f"[PHASE] pipeline={PIPELINE_ID} phase={_phase}")

    # ─── JDBC Oracle PK read config ────────────────────────────────
    pk_partition_col  = rec_cfg.get("pk_partition_col")
    pk_lower_bound    = str(rec_cfg.get("pk_lower_bound",  1))
    pk_upper_bound    = str(rec_cfg.get("pk_upper_bound",  10_000_000))
    pk_num_partitions = str(rec_cfg.get("pk_partitions",   4))

    # ─── Init Spark ───────────────────────────────────────────────
    sc      = SparkContext()
    glueCtx = GlueContext(sc)
    spark   = glueCtx.spark_session
    job     = Job(glueCtx)
    job.init(JOB_NAME, args)

    spark.sparkContext.setLogLevel("WARN")
    glue_cfg = cfg.get("glue", {})
    spark.conf.set("spark.sql.shuffle.partitions",
                   str(glue_cfg.get("shuffle_partitions", 8)))
    spark.conf.set("spark.hadoop.fs.s3a.endpoint.region", REGION)

    # Iceberg catalog
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
        # ─── Resolve Oracle credentials ────────────────────────────
        oracle_user, oracle_pass, jdbc_url = resolve_credentials(secret_arn)
        print(f"[CREDENTIALS RESOLVED]")

        # ─── Read Oracle PKs ───────────────────────────────────────
        # Oracle doesn't support backticks — use bare column names (uppercase)
        pk_col_list = ", ".join(primary_key_cols)
        oracle_sql = f"(SELECT {pk_col_list} FROM {extract_table}) t"
        print(f"[ORACLE PK SQL] {oracle_sql[:200]}")

        fetch_size = str(ext_resolved.get("jdbc_fetchsize", 10_000))
        # See generic_bronze_ingest.read_oracle for rationale on these timeouts.
        read_timeout_ms = str(ext_resolved.get("jdbc_read_timeout_ms", 7_200_000))

        reader = (
            spark.read.format("jdbc")
            .option("url",                    jdbc_url)
            .option("dbtable",                oracle_sql)
            .option("user",                   oracle_user)
            .option("password",               oracle_pass)
            .option("driver",                 "oracle.jdbc.OracleDriver")
            .option("fetchsize",              fetch_size)
            .option("sessionInitStatement",   _NLS_INIT)
            .option("oracle.net.CONNECT_TIMEOUT", "30000")
            .option("oracle.net.READ_TIMEOUT",    read_timeout_ms)
            .option("oracle.jdbc.ReadTimeout",    read_timeout_ms)
            .option("oracle.net.disableOob",      "false")
            .option("oracle.jdbc.keepAlive",      "true")
        )

        if pk_partition_col:
            reader = (
                reader
                .option("partitionColumn", pk_partition_col)
                .option("lowerBound",      pk_lower_bound)
                .option("upperBound",      pk_upper_bound)
                .option("numPartitions",   pk_num_partitions)
            )

        oracle_pks = reader.load()
        oracle_pks.cache()
        oracle_count = oracle_pks.count()
        print(f"[ORACLE PKs] count={oracle_count}")

        # ─── Read Iceberg Silver PKs ───────────────────────────────
        table_full = f"glue_catalog.`{silver_db}`.`{silver_tbl}`"
        iceberg_sql = f"SELECT {pk_col_list} FROM {table_full}"
        iceberg_pks = spark.sql(iceberg_sql)
        iceberg_pks.cache()
        iceberg_count = iceberg_pks.count()
        print(f"[ICEBERG PKs] count={iceberg_count}")

        # ─── Anti-join: find orphans (rows in Iceberg not in Oracle) ─
        oracle_pks.createOrReplaceTempView("oracle_pks")
        iceberg_pks.createOrReplaceTempView("iceberg_pks")

        orphans = spark.sql(f"""
            SELECT i.* FROM iceberg_pks i
            LEFT ANTI JOIN oracle_pks o ON {build_pk_conditions(primary_key_cols, "i", "o")}
        """)
        orphans.cache()
        orphan_count = orphans.count()
        print(f"[ORPHANS FOUND] count={orphan_count}")

        # ─── Safety cap check ──────────────────────────────────────
        if orphan_count > max_deletes_per_run:
            error_msg = (
                f"Orphan count ({orphan_count}) exceeds safety cap ({max_deletes_per_run}). "
                f"Aborting to prevent accidental mass-delete. Increase max_deletes_per_run if intentional."
            )
            print(f"[SAFETY CAP EXCEEDED] {error_msg}")
            status = "FAILED"
            write_metrics(status, orphan_count=orphan_count, rows_deleted=0, error=error_msg)
            raise RuntimeError(error_msg)

        # ─── Dry-run mode ─────────────────────────────────────────
        if DRY_RUN or rec_cfg.get("dry_run", False):
            print(f"[DRY RUN] found {orphan_count} orphans — skipping deletion")
            status = "SKIPPED"
            _write_skipped_metrics(f"dry-run (orphans={orphan_count})")
            job.commit()
            return

        # ─── Delete orphans via MERGE ──────────────────────────────
        if orphan_count > 0:
            orphans.createOrReplaceTempView("orphans")
            pk_join = build_pk_conditions(primary_key_cols, "t", "o")
            delete_sql = f"""
                MERGE INTO {table_full} t USING orphans o ON {pk_join}
                WHEN MATCHED THEN DELETE
            """
            print(f"[DELETE SQL] {delete_sql[:200]}")
            spark.sql(delete_sql)
            rows_deleted = orphan_count
            print(f"[DELETED] rows_deleted={rows_deleted}")
        else:
            rows_deleted = 0
            print(f"[NO ORPHANS] no rows to delete")

        # ─── Update DynamoDB reconcile metadata ────────────────────
        dynamo.Table(CONFIG_TABLE).update_item(
            Key={"pipeline_id": PIPELINE_ID, "version": "latest"},
            UpdateExpression=(
                "SET reconcile.last_reconciled_at = :ts, "
                "reconcile.last_orphans_deleted = :deleted, "
                "#meta.last_run_id = :rid, "
                "#meta.last_run_at = :run_ts"
            ),
            ExpressionAttributeNames={
                "#meta": "metadata",
            },
            ExpressionAttributeValues={
                ":ts":      datetime.now(timezone.utc).isoformat(),
                ":deleted": rows_deleted,
                ":rid":     RUN_ID,
                ":run_ts":  datetime.now(timezone.utc).isoformat(),
            },
        )
        print(f"[RECONCILE METADATA UPDATED] last_orphans_deleted={rows_deleted}")

        status = "SUCCEEDED"
        job.commit()

    except Exception:
        error_msg = traceback.format_exc()
        print(f"[FATAL ERROR]\n{error_msg}")

    finally:
        for df in (oracle_pks, iceberg_pks):
            if df is not None:
                try:
                    df.unpersist()
                except Exception:
                    pass
        write_metrics(status, orphan_count=orphan_count, rows_deleted=rows_deleted, error=error_msg)
        if spark is not None:
            spark.stop()

    if status != "SUCCEEDED":
        raise RuntimeError(
            f"Reconciliation for {PIPELINE_ID} failed — see error_message in metrics table."
        )


run()
