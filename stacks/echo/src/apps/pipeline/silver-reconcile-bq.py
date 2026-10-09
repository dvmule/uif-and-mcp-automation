# generic_silver_reconcile_gcp_bq.py
# BQ-native Silver reconciliation for pipelines with silver.write_mode="bq_native".
# Sibling of generic_silver_reconcile_gcp.py. Reads Oracle PKs via JDBC
# (unchanged — Oracle still lives on-prem and is reached over the same VPC
# connection), loads them into a transient BigQuery staging table, then uses
# a single BQ DELETE to remove rows from the native BQ Silver table whose
# primary keys no longer exist in Oracle.
#
# No Iceberg, no BigLake, no metadata pointer refresh. The entire anti-join
# happens server-side in BigQuery as `DELETE FROM target t WHERE NOT EXISTS
# (SELECT 1 FROM oracle_pks o WHERE pk_match)` — simpler than the Spark-side
# anti-join the Iceberg reconcile does, and idempotent on retry.
#
# Safety rails preserved exactly: max_deletes_per_run fails the run when
# orphan_count exceeds the cap; dry_run mode skips the DELETE and records
# orphan_count only. Run-metrics fields (orphan_count, rows_deleted,
# bq_status) match the Iceberg variant for dashboard parity.

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
from pyspark.sql import functions as F, DataFrame  # noqa: F401

# ─────────────────────────────────────────────────────────────────
# BOOTSTRAP
# ─────────────────────────────────────────────────────────────────
import argparse as _argparse
_p = _argparse.ArgumentParser(add_help=False)
_p.add_argument("--JOB_NAME",   default="unknown")
_p.add_argument("--JOB_RUN_ID", default="unknown")
_p.add_argument("--env",        default="dev")
_p.add_argument("--dry-run",    action="store_true", default=False)
_glue_defaults, _ = _p.parse_known_args(sys.argv[1:])

args        = getResolvedOptions(sys.argv, ["pipeline_id", "CONFIG_TABLE", "METRICS_TABLE"])
JOB_NAME    = _glue_defaults.JOB_NAME
RUN_ID      = _glue_defaults.JOB_RUN_ID
PIPELINE_ID = args["pipeline_id"]
ENV         = _glue_defaults.env
DRY_RUN     = _glue_defaults.dry_run
REGION      = "eu-west-1"
CONFIG_TABLE  = args["CONFIG_TABLE"]
METRICS_TABLE = args["METRICS_TABLE"]

dynamo     = boto3.resource("dynamodb",     region_name=REGION)
secrets    = boto3.client("secretsmanager", region_name=REGION)
started_at = datetime.now(timezone.utc)

RUN_ID_SHORT = RUN_ID.replace("-", "")[:12] if RUN_ID else "noid"


# ─────────────────────────────────────────────────────────────────
# HELPERS — verbatim from sibling reconcile
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
        "job_name":          JOB_NAME,
        "run_id":            RUN_ID,
        "pipeline_id":       PIPELINE_ID,
        "status":            status_msg,
        "destination_cloud": "gcp",
        "silver_write_mode": "bq_native",
        "started_at":        started_at.isoformat(),
        "completed_at":      completed_at.isoformat(),
        "duration_sec":      0,
        "ttl":               int(time.time()) + (90 * 86400),
    })


def write_metrics(run_status: str, orphan_count: int = 0, rows_deleted: int = 0,
                  error: str = None, bq_status: str = "not_attempted",
                  bq_table: str = None):
    completed_at = datetime.now(timezone.utc)
    item = {
        "job_name":          JOB_NAME,
        "run_id":            RUN_ID,
        "pipeline_id":       PIPELINE_ID,
        "destination_cloud": "gcp",
        "silver_write_mode": "bq_native",
        "status":            run_status,
        "orphan_count":      orphan_count,
        "rows_deleted":      rows_deleted,
        "bq_status":         bq_status,
        "bq_table":          bq_table,
        "error_message":     error,
        "started_at":        started_at.isoformat(),
        "completed_at":      completed_at.isoformat(),
        "duration_sec":      int((completed_at - started_at).total_seconds()),
        "ttl":               int(time.time()) + (90 * 86400),
    }
    item = {k: v for k, v in item.items() if v is not None}
    dynamo.Table(METRICS_TABLE).put_item(Item=to_dynamo_safe(item))
    print(
        f"[METRICS WRITTEN] status={run_status} orphans={orphan_count} "
        f"rows_deleted={rows_deleted} bq_status={bq_status} "
        f"duration={item['duration_sec']}s"
    )


def build_pk_conditions(primary_key_cols: list, alias_left: str = "t",
                        alias_right: str = "o") -> str:
    """NULL-safe equality on each PK column for use inside `NOT EXISTS` /
    anti-semi-joins. BigQuery rejects `IS NOT DISTINCT FROM` as a join
    condition for hash joins (the error is `LEFT ANTISEMI JOIN cannot be
    used without a condition that is an equality of fields from both sides`),
    so we cast each side to STRING and COALESCE with a sentinel that the
    real data cannot equal. This works across all PK column types
    (INT64, STRING, NUMERIC, DATE, etc.) without schema introspection.

    Performance trade-off: clustering can't be used on the CAST'd expression
    so reconcile does a full scan of `target_fqtn`. Acceptable because
    reconcile is nightly and bounded by `max_deletes_per_run`; for very
    large tables, swap in IFNULL with type-specific sentinels.
    """
    return " AND ".join([
        f"COALESCE(CAST({alias_left}.`{c}` AS STRING), '__NULL_PK__') = "
        f"COALESCE(CAST({alias_right}.`{c}` AS STRING), '__NULL_PK__')"
        for c in primary_key_cols
    ])


def resolve_credentials(secret_arn: str) -> tuple[str, str, str]:
    secret_str = secrets.get_secret_value(SecretId=secret_arn)["SecretString"]
    creds = json.loads(secret_str)
    dsn = creds.get("dsn") or "//{host}:{port}/{service}".format(**creds)
    jdbc_url = f"jdbc:oracle:thin:@{dsn}"
    return creds["username"], creds["password"], jdbc_url


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
    if not gs_path.startswith("gs://"):
        raise ValueError(f"expected gs:// URI, got {gs_path!r}")
    without_scheme = gs_path[len("gs://"):]
    bucket, _, prefix = without_scheme.partition("/")
    if prefix and not prefix.endswith("/"):
        prefix += "/"
    return bucket, prefix


# NLS session init — same as sibling reconcile
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
    status        = "FAILED"
    error_msg     = None
    orphan_count  = 0
    rows_deleted  = 0
    bq_status     = "not_attempted"
    bq_table_fqtn = None
    oracle_pks    = None
    spark         = None

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

    write_mode = (silver_cfg.get("write_mode") or "iceberg_biglake").lower()
    if write_mode != "bq_native":
        raise ValueError(
            f"generic_silver_reconcile_gcp_bq invoked for pipeline {PIPELINE_ID} "
            f"but silver.write_mode='{write_mode}' (expected 'bq_native'). "
            f"Check dispatcher routing."
        )

    rec_cfg = cfg.get("reconcile", {})
    if not rec_cfg.get("enabled", False):
        print(f"[SKIPPED] reconciliation not enabled for {PIPELINE_ID}")
        _write_skipped_metrics("reconcile disabled")
        return

    dest_cfg = cfg.get("destination") or {}
    if (dest_cfg.get("cloud") or "aws").lower() != "gcp":
        raise ValueError(
            f"generic_silver_reconcile_gcp_bq invoked for pipeline {PIPELINE_ID} "
            f"but destination.cloud != 'gcp'"
        )
    gcp_secret_arn = dest_cfg.get("gcp_credentials_secret_arn")
    if not gcp_secret_arn:
        raise ValueError("destination.gcp_credentials_secret_arn missing on config")

    primary_key_cols    = silver_cfg["primary_key_cols"]
    bq_project          = silver_cfg["bq_project"]
    bq_dataset          = silver_cfg["bq_dataset"]
    bq_location         = silver_cfg.get("bq_location") or None
    silver_tbl          = silver_cfg["table"]  # match generic_silver_bq_native._target_fqtn casing
    extract_table       = cfg["extract"]["table"]
    secret_arn          = cfg["source"]["secret_arn"]
    max_deletes_per_run = int(rec_cfg.get("max_deletes_per_run", 5000))
    bronze_gcs_path     = cfg["target"]["gcs_path"]
    bronze_bucket, _    = _parse_gs_path(bronze_gcs_path)

    target_fqtn   = f"{bq_project}.{bq_dataset}.{silver_tbl}"
    oracle_pks_fqtn = f"{bq_project}.{bq_dataset}._oracle_pks_{silver_tbl.lower()}_{RUN_ID_SHORT}"
    bq_table_fqtn   = target_fqtn

    # Phase-aware extract view (for JDBC tuning knobs)
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

    pk_partition_col  = rec_cfg.get("pk_partition_col")
    pk_lower_bound    = str(rec_cfg.get("pk_lower_bound",  1))
    pk_upper_bound    = str(rec_cfg.get("pk_upper_bound",  10_000_000))
    pk_num_partitions = str(rec_cfg.get("pk_partitions",   4))

    # ─── Bootstrap GCP credentials BEFORE Spark starts ────────────
    keyfile_path, gcp_project_id, sa_info = _bootstrap_gcp_credentials(gcp_secret_arn)

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

    # GCS Hadoop connector for writing transient Oracle-PK Parquet to gs://
    hconf = sc._jsc.hadoopConfiguration()
    hconf.set("fs.gs.impl",
              "com.google.cloud.hadoop.fs.gcs.GoogleHadoopFileSystem")
    hconf.set("fs.AbstractFileSystem.gs.impl",
              "com.google.cloud.hadoop.fs.gcs.GoogleHadoopFS")
    hconf.set("fs.gs.project.id", gcp_project_id)
    hconf.set("fs.gs.auth.service.account.enable", "true")
    hconf.set("google.cloud.auth.service.account.enable", "true")
    hconf.set("fs.gs.auth.service.account.email",          sa_info["client_email"])
    hconf.set("fs.gs.auth.service.account.private.key.id", sa_info["private_key_id"])
    hconf.set("fs.gs.auth.service.account.private.key",    sa_info["private_key"])
    hconf.set("google.cloud.auth.service.account.email",          sa_info["client_email"])
    hconf.set("google.cloud.auth.service.account.private.key.id", sa_info["private_key_id"])
    hconf.set("google.cloud.auth.service.account.private.key",    sa_info["private_key"])

    print("[SPARK INITIALISED] BQ-native reconcile mode")

    try:
        # ─── Read Oracle PKs ───────────────────────────────────────
        oracle_user, oracle_pass, jdbc_url = resolve_credentials(secret_arn)
        pk_col_list = ", ".join(primary_key_cols)
        oracle_sql = f"(SELECT {pk_col_list} FROM {extract_table}) t"
        print(f"[ORACLE PK SQL] {oracle_sql[:200]}")

        fetch_size = str(ext_resolved.get("jdbc_fetchsize", 10_000))
        read_timeout_ms = str(ext_resolved.get("jdbc_read_timeout_ms", 7_200_000))

        reader = (
            spark.read.format("jdbc")
            .option("url",                       jdbc_url)
            .option("dbtable",                   oracle_sql)
            .option("user",                      oracle_user)
            .option("password",                  oracle_pass)
            .option("driver",                    "oracle.jdbc.OracleDriver")
            .option("fetchsize",                 fetch_size)
            .option("sessionInitStatement",      _NLS_INIT)
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

        # ─── Stage Oracle PKs to GCS → BQ ──────────────────────────
        staging_gcs_path = (
            f"gs://{bronze_bucket}/_bq_staging/_oracle_pks_{silver_tbl.lower()}/"
            f"{RUN_ID_SHORT}/"
        )
        print(f"[STAGING WRITE] gcs={staging_gcs_path}")
        (oracle_pks
         .coalesce(max(1, int(glue_cfg.get("num_workers", 2))))
         .write
         .mode("overwrite")
         .parquet(staging_gcs_path))

        from google.cloud import bigquery
        from google.oauth2 import service_account
        creds = service_account.Credentials.from_service_account_file(keyfile_path)
        bq = bigquery.Client(project=bq_project, credentials=creds)

        load_job = bq.load_table_from_uri(
            f"{staging_gcs_path}*.parquet",
            oracle_pks_fqtn,
            location=bq_location,
            job_config=bigquery.LoadJobConfig(
                source_format=bigquery.SourceFormat.PARQUET,
                write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
            ),
        )
        load_job.result()
        # 24h TTL
        oracle_tbl = bq.get_table(oracle_pks_fqtn)
        oracle_tbl.expires = datetime.now(timezone.utc) + timedelta(hours=24)
        bq.update_table(oracle_tbl, ["expires"])
        print(f"[BQ LOAD] {oracle_pks_fqtn} rows={load_job.output_rows}")

        # ─── Count orphans (BQ-side anti-join) ─────────────────────
        pk_join = build_pk_conditions(primary_key_cols, "t", "o")
        count_sql = (
            f"SELECT COUNT(*) AS c "
            f"FROM `{target_fqtn.replace('.', '`.`')}` t "
            f"WHERE NOT EXISTS ("
            f"  SELECT 1 FROM `{oracle_pks_fqtn.replace('.', '`.`')}` o "
            f"  WHERE {pk_join}"
            f")"
        )
        print(f"[ORPHAN COUNT SQL] {count_sql}")
        row = list(bq.query(count_sql, location=bq_location).result())[0]
        orphan_count = int(row["c"])
        print(f"[ORPHANS FOUND] count={orphan_count}")

        bq_status = "ok"

        # ─── Safety cap check ──────────────────────────────────────
        if orphan_count > max_deletes_per_run:
            error_msg = (
                f"Orphan count ({orphan_count}) exceeds safety cap "
                f"({max_deletes_per_run}). Aborting to prevent accidental "
                f"mass-delete. Increase max_deletes_per_run if intentional."
            )
            print(f"[SAFETY CAP EXCEEDED] {error_msg}")
            raise RuntimeError(error_msg)

        # ─── Dry-run mode ──────────────────────────────────────────
        if DRY_RUN or rec_cfg.get("dry_run", False):
            print(f"[DRY RUN] found {orphan_count} orphans — skipping deletion")
            status = "SKIPPED"
            _write_skipped_metrics(f"dry-run (orphans={orphan_count})")
            job.commit()
            return

        # ─── DELETE orphans ────────────────────────────────────────
        if orphan_count > 0:
            delete_sql = (
                f"DELETE FROM `{target_fqtn.replace('.', '`.`')}` t "
                f"WHERE NOT EXISTS ("
                f"  SELECT 1 FROM `{oracle_pks_fqtn.replace('.', '`.`')}` o "
                f"  WHERE {pk_join}"
                f")"
            )
            print(f"[DELETE SQL] {delete_sql}")
            delete_job = bq.query(delete_sql, location=bq_location)
            delete_job.result()
            rows_deleted = delete_job.num_dml_affected_rows or 0
            print(f"[DELETED] rows_deleted={rows_deleted}")
        else:
            rows_deleted = 0
            print("[NO ORPHANS] no rows to delete")

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
        if oracle_pks is not None:
            try:
                oracle_pks.unpersist()
            except Exception:
                pass
        # Drop the transient oracle_pks staging table.
        try:
            from google.cloud import bigquery as _bq
            from google.oauth2 import service_account as _sa
            _creds = _sa.Credentials.from_service_account_file(keyfile_path)
            _bq.Client(project=bq_project, credentials=_creds).delete_table(
                oracle_pks_fqtn, not_found_ok=True
            )
            print(f"[CLEANUP] dropped staging {oracle_pks_fqtn}")
        except Exception as exc:
            print(f"[CLEANUP WARNING] failed to drop {oracle_pks_fqtn}: {exc}")
        write_metrics(
            status, orphan_count=orphan_count, rows_deleted=rows_deleted,
            error=error_msg, bq_status=bq_status, bq_table=bq_table_fqtn,
        )
        if spark is not None:
            spark.stop()

    if status != "SUCCEEDED":
        raise RuntimeError(
            f"Reconciliation for {PIPELINE_ID} failed — see error_message in metrics table."
        )


run()
