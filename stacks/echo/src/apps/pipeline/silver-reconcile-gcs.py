# generic_silver_reconcile_gcp.py
# Sibling of generic_silver_reconcile.py for the GCP-native Silver path
# (destination.cloud="gcp"). Reads Oracle PKs via JDBC (unchanged — Oracle
# still lives on-prem and is reached over the same VPC connection), reads
# Silver Iceberg PKs from the GCS-rooted Hadoop catalog, anti-joins to
# identify orphans, and deletes them with the same safety cap + dry-run
# support as the AWS reconcile.
#
# After deleting orphans the Iceberg snapshot pointer advances; we re-issue
# `CREATE OR REPLACE EXTERNAL TABLE` so BigQuery resolves through the new
# metadata.json (otherwise the BigLake view would still show the deleted
# rows until the next Silver transform run).
#
# Glue 5.1 (matches the GCP Silver transform; the AWS reconcile is still
# on 4.0 — moving it is out of scope for Phase 3).
#
# Helpers ported verbatim from sibling files (see module docstring of
# generic_silver_transform_gcp_iceberg.py for the full provenance list).

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
from pyspark.sql import functions as F, DataFrame  # noqa: F401  (DataFrame re-exported for parity)

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

dynamo     = boto3.resource("dynamodb",       region_name=REGION)
secrets    = boto3.client("secretsmanager",   region_name=REGION)
started_at = datetime.now(timezone.utc)


# ─────────────────────────────────────────────────────────────────
# HELPERS (verbatim from generic_silver_reconcile.py except _bootstrap_gcp)
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
    """Build the ON clause for MERGE/JOIN statements from PK column names."""
    return " AND ".join([
        f"{alias_left}.`{c}` = {alias_right}.`{c}`" for c in primary_key_cols
    ])


def resolve_credentials(secret_arn: str) -> tuple[str, str, str]:
    """Fetch Oracle username, password, JDBC URL from Secrets Manager."""
    secret_str = secrets.get_secret_value(SecretId=secret_arn)["SecretString"]
    creds = json.loads(secret_str)
    dsn = creds.get("dsn") or "//{host}:{port}/{service}".format(**creds)
    jdbc_url = f"jdbc:oracle:thin:@{dsn}"
    return creds["username"], creds["password"], jdbc_url


# Ported verbatim from generic_bronze_ingest.py — same /tmp/gcp-sa.json
# pattern Bronze uses, reused here for the BQ external-table refresh.
def _bootstrap_gcp_credentials(secret_arn: str) -> tuple[str, str, dict]:
    """See generic_silver_transform_gcp_iceberg.py for rationale on returning
    sa_info (executors can't reach driver's /tmp keyfile)."""
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


def _resolve_current_metadata_pointer_gcs(*, gcs_client, bucket_name: str,
                                          base_prefix: str, db: str, table: str) -> str:
    """Mirror of the transform job helper — see that file for full notes."""
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
    """Re-issue CREATE OR REPLACE EXTERNAL TABLE post-DELETE so the BigLake
    view tracks the new Iceberg snapshot.

    Same shape as the transform job's helper; duplicated rather than imported
    because Glue jobs upload each script independently from S3.
    """
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
        print(f"[BIGLAKE WARNING] CREATE OR REPLACE EXTERNAL TABLE failed: {exc}")
        return "failed", fqtn


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
    status        = "FAILED"
    error_msg     = None
    orphan_count  = 0
    rows_deleted  = 0
    bq_status     = "not_attempted"
    bq_table_fqtn = None
    oracle_pks    = None
    iceberg_pks   = None
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

    rec_cfg = cfg.get("reconcile", {})
    if not rec_cfg.get("enabled", False):
        print(f"[SKIPPED] reconciliation not enabled for {PIPELINE_ID}")
        _write_skipped_metrics("reconcile disabled")
        return

    dest_cfg = cfg.get("destination") or {}
    if (dest_cfg.get("cloud") or "aws").lower() != "gcp":
        raise ValueError(
            f"generic_silver_reconcile_gcp invoked for pipeline {PIPELINE_ID} "
            f"but destination.cloud != 'gcp'"
        )
    gcp_secret_arn = dest_cfg.get("gcp_credentials_secret_arn")
    if not gcp_secret_arn:
        raise ValueError("destination.gcp_credentials_secret_arn missing on config")

    primary_key_cols    = silver_cfg["primary_key_cols"]
    bq_dataset          = silver_cfg["bq_dataset"]
    silver_tbl_lower    = silver_cfg["table"].lower()
    silver_gcs_path     = silver_cfg["gcs_path"]
    extract_table       = cfg["extract"]["table"]
    secret_arn          = cfg["source"]["secret_arn"]
    max_deletes_per_run = int(rec_cfg.get("max_deletes_per_run", 5000))

    # Phase-aware extract view — see generic_silver_reconcile.py / pipeline_profiles.py.
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

    # GCS Hadoop connector — set on live Hadoop conf with inline credentials.
    # See generic_silver_transform_gcp_iceberg.py for rationale.
    hconf = sc._jsc.hadoopConfiguration()
    hconf.set("fs.gs.impl",
              "com.google.cloud.hadoop.fs.gcs.GoogleHadoopFileSystem")
    hconf.set("fs.AbstractFileSystem.gs.impl",
              "com.google.cloud.hadoop.fs.gcs.GoogleHadoopFS")
    hconf.set("fs.gs.project.id", gcp_project_id)
    hconf.set("fs.gs.auth.service.account.enable", "true")
    hconf.set("google.cloud.auth.service.account.enable", "true")
    hconf.set("fs.gs.auth.service.account.email",         sa_info["client_email"])
    hconf.set("fs.gs.auth.service.account.private.key.id", sa_info["private_key_id"])
    hconf.set("fs.gs.auth.service.account.private.key",    sa_info["private_key"])
    hconf.set("google.cloud.auth.service.account.email",         sa_info["client_email"])
    hconf.set("google.cloud.auth.service.account.private.key.id", sa_info["private_key_id"])
    hconf.set("google.cloud.auth.service.account.private.key",    sa_info["private_key"])

    # Iceberg Hadoop catalog over GCS — same as the transform job.
    spark.conf.set("spark.sql.catalog.bq_iceberg",
        "org.apache.iceberg.spark.SparkCatalog")
    spark.conf.set("spark.sql.catalog.bq_iceberg.type",       "hadoop")
    # See generic_silver_transform_gcp_iceberg.py — HadoopFileIO not GCSFileIO
    # so all GCS I/O goes through the already-authenticated Hadoop GCS connector.
    spark.conf.set("spark.sql.catalog.bq_iceberg.io-impl",
        "org.apache.iceberg.hadoop.HadoopFileIO")
    spark.conf.set("spark.sql.catalog.bq_iceberg.warehouse", silver_gcs_path)

    print("[SPARK + ICEBERG (HADOOP/GCS) INITIALISED]")

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

        # ─── Read Iceberg Silver PKs (GCS warehouse, Hadoop catalog) ─
        table_full = f"bq_iceberg.`{bq_dataset}`.`{silver_tbl_lower}`"
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
                f"Orphan count ({orphan_count}) exceeds safety cap "
                f"({max_deletes_per_run}). Aborting to prevent accidental "
                f"mass-delete. Increase max_deletes_per_run if intentional."
            )
            print(f"[SAFETY CAP EXCEEDED] {error_msg}")
            status = "FAILED"
            write_metrics(status, orphan_count=orphan_count, rows_deleted=0,
                          error=error_msg)
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

            # ─── Refresh BigLake external table — pointer advanced ─
            try:
                bq_status, bq_table_fqtn = _refresh_biglake_external_table(
                    silver_cfg=silver_cfg, keyfile_path=keyfile_path,
                )
            except Exception as exc:
                print(f"[BIGLAKE WARNING] could not refresh external table: {exc}")
                bq_status = "failed"
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
