# generic_bronze_ingest.py
# Single reusable AWS Glue 4.0 job for Bronze layer ingestion.
# Driven entirely by a DynamoDB config item — no code changes needed to onboard new tables.
# Only argument required from the dispatcher: --pipeline_id

import sys
import json
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
# STEP 1 — BOOTSTRAP
# Reads JOB_NAME, JOB_RUN_ID, pipeline_id from Glue resolved args.
# Initialises boto3 clients. Records started_at for duration tracking.
# ─────────────────────────────────────────────────────────────────
# Glue 4.0 pre-registers --JOB_NAME and --JOB_RUN_ID in its internal argparse
# parser, so passing them again to getResolvedOptions raises a conflicting
# option string error. Extract them directly from sys.argv first, then call
# getResolvedOptions only for custom arguments.
import argparse as _argparse
_p = _argparse.ArgumentParser(add_help=False)
_p.add_argument("--JOB_NAME",   default="unknown")
_p.add_argument("--JOB_RUN_ID", default="unknown")
_p.add_argument("--env",        default="dev")
_glue_defaults, _ = _p.parse_known_args(sys.argv[1:])

args = getResolvedOptions(
    sys.argv,
    ["pipeline_id", "CONFIG_TABLE", "METRICS_TABLE", "RESOURCE_PREFIX"],
)

JOB_NAME    = _glue_defaults.JOB_NAME
RUN_ID      = _glue_defaults.JOB_RUN_ID
PIPELINE_ID = args["pipeline_id"]
ENV         = _glue_defaults.env
REGION      = "eu-west-1"

CONFIG_TABLE  = args["CONFIG_TABLE"]
METRICS_TABLE = args["METRICS_TABLE"]

# Which stack this job belongs to. Terraform passes local.name_prefix; it is
# REQUIRED, not defaulted, because a default of echo-{env} is exactly how a
# uif-echo-dev run came to trigger echo-dev's Silver job.
RESOURCE_PREFIX = args["RESOURCE_PREFIX"]

dynamo  = boto3.resource("dynamodb", region_name=REGION)
secrets = boto3.client("secretsmanager", region_name=REGION)

started_at     = datetime.now(timezone.utc)
quality_result = {}
new_watermark  = "N/A"
status         = "FAILED"
error_msg      = None


class QualityGateWarning(Exception):
    """Initial/full load row count below min_rows_expected — non-fatal warning."""

    def __init__(self, message: str, *, rows_total: int = 0, null_counts: dict | None = None):
        super().__init__(message)
        self.rows_total = rows_total
        self.null_counts = null_counts or {}


# ─────────────────────────────────────────────────────────────────
# HELPER — Decimal-safe DynamoDB serialiser
# DynamoDB rejects Python float; convert recursively to Decimal.
# ─────────────────────────────────────────────────────────────────
def to_dynamo_safe(obj):
    if isinstance(obj, float):
        return Decimal(str(obj))
    if isinstance(obj, dict):
        return {k: to_dynamo_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [to_dynamo_safe(i) for i in obj]
    return obj


# ─────────────────────────────────────────────────────────────────
# STEP 2 — LOAD CONFIG FROM DYNAMODB
# GET from eds-{env}-glue-job-config using pipeline_id (PK) + "latest" (SK).
# Raises ValueError if not found.
# Exits cleanly (code 0) if enabled=false — writes a SKIPPED metric.
# ─────────────────────────────────────────────────────────────────
def load_config() -> dict:
    resp = dynamo.Table(CONFIG_TABLE).get_item(
        Key={"pipeline_id": PIPELINE_ID, "version": "latest"}
    )
    if "Item" not in resp:
        raise ValueError(f"No config found for pipeline_id={PIPELINE_ID} version=latest")
    return resp["Item"]


cfg = load_config()
print(f"[CONFIG LOADED] pipeline_id={PIPELINE_ID}")

if not cfg.get("enabled", True):
    print(f"[SKIPPED] Pipeline {PIPELINE_ID} is disabled in config. Exiting cleanly.")
    # Write audit record so the run history shows the pipeline was checked.
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
    sys.exit(0)

src      = cfg["source"]
tgt      = cfg["target"]
qual     = cfg.get("quality", {})


# ─────────────────────────────────────────────────────────────────
# STEP 2a — RESOLVE LOAD PHASE (initial vs incremental)
# Mirrors lambda/pipeline_profiles.py — duplicated inline because Glue
# scripts ship as standalone files (no shared package on the executor
# classpath). Keep both copies in sync.
#
# Phase determines which `extract.{phase}` JDBC config and `glue.{phase}`
# worker sizing to apply. Legacy flat configs (no extract.initial block)
# resolve to initial-phase semantics with the current top-level fields,
# so nothing breaks until the migration script rewrites them.
# ─────────────────────────────────────────────────────────────────
# The extract SQL builder, its watermark predicate and the sentinel set live
# in bronze_extract_sql.py so that tests import the real implementation instead
# of a copy. Shipped to Glue through --extra-py-files, like silver_routing.py.
from bronze_extract_sql import (  # noqa: E402
    SENTINEL_WATERMARKS as _SENTINEL_WATERMARKS,
    build_extract_sql,
    optional_watermark_fallback as _optional_watermark_fallback,
    require_watermark_column,
)

_LEAN_INC_EXTRACT = {
    "jdbc_partition_col": None,
    "jdbc_lower_bound":   None,
    "jdbc_upper_bound":   None,
    "jdbc_partitions":    None,
    "jdbc_predicates":    None,
}
_LEAN_INC_GLUE = {"worker_type": "G.1X", "num_workers": 2, "timeout_minutes": 60}
_SOURCE_CONTRACT = {
    "table", "mode", "watermark_col", "watermark_fallback_col",
    "watermark_value", "columns", "custom_sql", "custom_schema",
}


def _resolve_phase(_cfg: dict) -> str:
    e = _cfg.get("extract") or {}
    if e.get("mode") == "full":
        return "initial"
    if e.get("watermark_value") in _SENTINEL_WATERMARKS:
        return "initial"
    return "incremental"


def _resolve_extract(_cfg: dict, phase: str) -> dict:
    e = _cfg.get("extract") or {}
    out = {}
    if phase == "incremental":
        out.update(_LEAN_INC_EXTRACT)
    for k in _SOURCE_CONTRACT:
        if k in e:
            out[k] = e[k]
    phase_block = e.get(phase)
    if phase_block is None:
        if phase == "initial":
            for k, v in e.items():
                if k in _SOURCE_CONTRACT or k in ("initial", "incremental"):
                    continue
                out[k] = v
    else:
        out.update(phase_block)
    return out


def _resolve_glue(_cfg: dict, phase: str) -> dict:
    g = _cfg.get("glue") or {}
    phase_block = g.get(phase)
    if phase_block is not None:
        merged = dict(_LEAN_INC_GLUE) if phase == "incremental" else {}
        merged.update(phase_block)
        return merged
    if phase == "initial":
        flat = {k: v for k, v in g.items() if k not in ("initial", "incremental")}
        return flat or dict(_LEAN_INC_GLUE)
    return dict(_LEAN_INC_GLUE)


PHASE    = _resolve_phase(cfg)
ext      = _resolve_extract(cfg, PHASE)
glue_cfg = _resolve_glue(cfg, PHASE)
_phase_reason = (
    "mode=full"            if (cfg.get("extract") or {}).get("mode") == "full"
    else "watermark_sentinel" if PHASE == "initial"
    else f"watermark={(cfg.get('extract') or {}).get('watermark_value')}"
)
print(
    f"[PHASE] pipeline={PIPELINE_ID} phase={PHASE} reason={_phase_reason} "
    f"predicates={len(ext.get('jdbc_predicates') or [])} "
    f"workers={glue_cfg.get('num_workers')}/{glue_cfg.get('worker_type')}"
)

# ─────────────────────────────────────────────────────────────────
# STEP 2b — RESOLVE DESTINATION CLOUD
# destination.cloud="gcp" routes Bronze writes to gs:// via the
# Hadoop GCS connector. Default "aws" keeps the S3A path unchanged.
# Validation of the surrounding fields lives in pipeline_controller.py
# (`_validate_destination_config`) — by the time the job runs the
# config has already been schema-checked.
# ─────────────────────────────────────────────────────────────────
DEST_CFG       = cfg.get("destination") or {}
DEST_CLOUD     = (DEST_CFG.get("cloud") or "aws").lower()
GCP_SECRET_ARN = DEST_CFG.get("gcp_credentials_secret_arn") if DEST_CLOUD == "gcp" else None

if DEST_CLOUD not in ("aws", "gcp"):
    raise ValueError(f"destination.cloud must be 'aws' or 'gcp', got {DEST_CLOUD!r}")
if DEST_CLOUD == "gcp" and not GCP_SECRET_ARN:
    raise ValueError("destination.gcp_credentials_secret_arn is required when destination.cloud='gcp'")


def _resolve_target_path(target_cfg: dict, dest_cloud: str) -> str:
    """Return the Bronze write path appropriate for the configured cloud.

    Bronze writes are filesystem-agnostic via Spark — the only difference
    between AWS and GCP destinations is the URI scheme and the Hadoop FS
    connector wired in Spark conf (S3A vs GCS). Centralise the lookup so
    `write_bronze` and `write_metrics` can't drift.
    """
    if dest_cloud == "gcp":
        path = target_cfg.get("gcs_path")
        if not path or not isinstance(path, str) or not path.startswith("gs://"):
            raise ValueError(
                f"target.gcs_path is required and must start with 'gs://' when "
                f"destination.cloud='gcp', got {path!r}"
            )
        return path
    path = target_cfg.get("s3_path")
    if not path or not isinstance(path, str) or not path.startswith("s3://"):
        raise ValueError(
            f"target.s3_path is required and must start with 's3://' when "
            f"destination.cloud='aws', got {path!r}"
        )
    return path


TARGET_PATH = _resolve_target_path(tgt, DEST_CLOUD)
print(f"[DESTINATION] cloud={DEST_CLOUD} target={TARGET_PATH}")


# ─────────────────────────────────────────────────────────────────
# STEP 3 — RESOLVE ORACLE CREDENTIALS FROM SECRETS MANAGER
# Fetches the secret at source.secret_arn.
# Extracts username, password, dsn.
# Appends optional jdbc_extra_options (e.g. SSL version flags).
# ─────────────────────────────────────────────────────────────────
def resolve_credentials() -> tuple[str, str, str, str]:
    secret_str = secrets.get_secret_value(SecretId=src["secret_arn"])["SecretString"]
    creds = json.loads(secret_str)
    dsn = creds.get("dsn") or "//{host}:{port}/{service}".format(**creds)
    jdbc_url = f"jdbc:oracle:thin:@{dsn}"
    extra = src.get("jdbc_extra_options", "")
    if extra:
        jdbc_url = f"{jdbc_url}?{extra}"
    return creds["username"], creds["password"], dsn, jdbc_url


oracle_user, oracle_pass, oracle_dsn, jdbc_url = resolve_credentials()
# Mask host detail — dsn can contain internal hostnames
print(f"[CREDENTIALS RESOLVED] dsn=***")


# ─────────────────────────────────────────────────────────────────
# STEP 3b — GCP CREDENTIAL BOOTSTRAP (only when destination.cloud='gcp')
# The Hadoop GCS connector authenticates from a JSON keyfile on disk
# (not in-memory creds like google-auth), so we have to materialise
# the SA JSON to /tmp before Spark starts. Reuses the same secret-fetch
# pattern as silver_bq_replicator._load_gcp_credentials.
# ─────────────────────────────────────────────────────────────────
def _bootstrap_gcp_credentials(secret_arn: str) -> dict:
    """Fetch GCP service-account JSON, return parsed dict.

    We do NOT use the file-based keyfile path because executors run on
    different Glue worker nodes than the driver — a file at /tmp/gcp-sa.json
    on the driver is invisible to executors. Instead, we plumb the
    individual SA fields into Hadoop config (email/private_key/key_id) so
    every executor's serialised Hadoop config carries the creds.
    """
    raw = secrets.get_secret_value(SecretId=secret_arn)["SecretString"]
    sa_info = json.loads(raw)
    project_id = sa_info.get("project_id")
    if not project_id:
        raise ValueError(f"GCP service-account secret {secret_arn} missing project_id")
    print(f"[GCP CREDENTIALS] project={project_id} client_email={sa_info.get('client_email')}")
    return sa_info


_GCP_SA: dict = {}
_GCP_PROJECT = None
if DEST_CLOUD == "gcp":
    _GCP_SA = _bootstrap_gcp_credentials(GCP_SECRET_ARN)
    _GCP_PROJECT = _GCP_SA["project_id"]


# ─────────────────────────────────────────────────────────────────
# STEP 4 — INIT SPARK
# Creates SparkContext, GlueContext, SparkSession.
# Tunes Spark conf for JDBC→Parquet→S3 workloads:
#   - fileoutputcommitter v2: faster S3 rename/commit path
#   - shuffle.partitions=8: default 200 is wrong for small-to-mid Glue jobs
#   - parquet.mergeSchema=false: skip per-file schema merge on read
#   Note: spark.speculation is locked in Glue 5.1 (disabled by default)
# WARN level suppresses Spark INFO noise in CloudWatch logs.
# ─────────────────────────────────────────────────────────────────
sc      = SparkContext()
glueCtx = GlueContext(sc)
spark   = glueCtx.spark_session
job     = Job(glueCtx)
job.init(JOB_NAME, args)

spark.sparkContext.setLogLevel("WARN")

spark.conf.set("spark.sql.parquet.mergeSchema",           "false")
spark.conf.set("spark.sql.parquet.filterPushdown",        "true")
spark.conf.set("spark.sql.shuffle.partitions",
               str(glue_cfg.get("shuffle_partitions", 8)))
spark.conf.set(
    "spark.hadoop.mapreduce.fileoutputcommitter.algorithm.version", "2"
)
# Oracle EBS tables can contain dates before 1582-10-15 or timestamps before
# 1900-01-01. Spark 3.x refuses to write these to Parquet without an explicit
# rebase mode. CORRECTED writes values as-is (safe: Bronze is only read by
# Spark 3.x+).
spark.conf.set("spark.sql.parquet.int96RebaseModeInWrite",   "CORRECTED")
spark.conf.set("spark.sql.parquet.datetimeRebaseModeInWrite", "CORRECTED")
# Filesystem connector — branches on destination.cloud.
# AWS: S3A endpoint region (Glue 5.1 defaults to us-east-2 if not set,
#      so we pin it to the deployment region to avoid S3 timeouts).
# GCP: Hadoop GCS connector + service-account keyfile auth. Requires the
#      gcs-connector-hadoop3 JAR on --extra-jars (provisioned via glue_jars.tf).
if DEST_CLOUD == "aws":
    spark.conf.set("spark.hadoop.fs.s3a.endpoint.region", REGION)
else:
    # GCS connector config MUST be set on the live Hadoop Configuration
    # (not via spark.conf.set after SparkContext init — those mutations
    # don't propagate to the FileSystem cache or to executors). Use
    # _jsc.hadoopConfiguration() so the conf is serialised to executors
    # when the next Spark job is submitted (i.e. before df.write.parquet).
    # Credentials are passed inline (no keyfile) because /tmp on the driver
    # isn't visible to executors.
    hconf = sc._jsc.hadoopConfiguration()
    hconf.set("fs.gs.impl",
              "com.google.cloud.hadoop.fs.gcs.GoogleHadoopFileSystem")
    hconf.set("fs.AbstractFileSystem.gs.impl",
              "com.google.cloud.hadoop.fs.gcs.GoogleHadoopFS")
    hconf.set("fs.gs.project.id", _GCP_PROJECT)
    hconf.set("fs.gs.auth.service.account.enable", "true")
    hconf.set("google.cloud.auth.service.account.enable", "true")
    hconf.set("fs.gs.auth.service.account.email",         _GCP_SA["client_email"])
    hconf.set("fs.gs.auth.service.account.private.key.id", _GCP_SA["private_key_id"])
    hconf.set("fs.gs.auth.service.account.private.key",    _GCP_SA["private_key"])
    # GCS connector 2.x also accepts the google.cloud.* aliases — set both
    # so we work across connector minor versions.
    hconf.set("google.cloud.auth.service.account.email",         _GCP_SA["client_email"])
    hconf.set("google.cloud.auth.service.account.private.key.id", _GCP_SA["private_key_id"])
    hconf.set("google.cloud.auth.service.account.private.key",    _GCP_SA["private_key"])

print("[SPARK INITIALISED]")


# ─────────────────────────────────────────────────────────────────
# STEP 5 — BUILD EXTRACT SQL
# full:        SELECT [cols | *] FROM table
# incremental: WHERE effective_watermark > TO_TIMESTAMP(watermark_value)
# effective_watermark = watermark_col, or COALESCE(watermark_col, watermark_fallback_col)
# when watermark_fallback_col is set (captures rows with NULL primary watermark).
# custom_sql:  engineer-supplied query (e.g. a filter that needs a second view).
#              Put {{WATERMARK}} where the watermark filter belongs, normally
#              `... AND {{WATERMARK}}` in the OUTER WHERE (the predicate uses
#              bare column names, so it binds to whichever query block holds it).
#              It renders as `1=1` on initial/full runs and as the incremental
#              predicate above otherwise. Incremental requires the token and
#              watermark_col (the controller rejects the config; the job
#              raises if it gets past the controller). No ';' — ORA-00911.
#              The SELECT list must include watermark_col (and the fallback),
#              or the run fails rather than leaving the watermark stuck.
#              WARNING: custom_sql is executed as-is on Oracle; restrict
#              DynamoDB write access to prevent SQL injection.
#              KNOWN LIMITATION — a custom_sql FILTER IS NOT RETROACTIVE.
#              A row already in Silver that the filter later excludes (e.g. a
#              project reclassified after its rows loaded) is never removed,
#              and reloading does not remove it: every Silver variant only
#              inserts/updates; silver-reconcile checks PKs against
#              extract.table, not this query, so it sees the row still in
#              Oracle; and the bq_native initial rebuild reloads every Bronze
#              partition and keeps the newest pre-exclusion row. Removing it
#              needs a Bronze-prefix purge, a full re-extract and a Silver
#              rebuild (or a reconcile that honours custom_sql). Rows the filter
#              newly INCLUDES are not picked up by incremental runs either,
#              because their watermark is old — any change to the filter needs
#              a full re-extract (watermark back to the sentinel).
# All forms wrapped in (...) t subquery alias for JDBC compatibility.
# ─────────────────────────────────────────────────────────────────
def build_sql() -> str:
    return build_extract_sql(ext)


# ─────────────────────────────────────────────────────────────────
# STEP 6 — READ FROM ORACLE (PARALLEL JDBC)
# numPartitions + partitionColumn/lowerBound/upperBound = parallel splits.
# Falls back to single-partition read when no numeric column is configured.
# fetchsize: configurable via extract.jdbc_fetchsize (default 10000).
#   Lower this for wide rows / LOB-heavy tables to avoid executor OOM.
# sessionInitStatement: pins NLS_DATE_FORMAT and TIME_ZONE so date columns
#   parse correctly regardless of Oracle server NLS defaults.
# oracle.net.CONNECT_TIMEOUT: prevents hung connections blocking the job.
# Result is cached immediately — all downstream steps reuse this in-memory
# copy and do NOT re-read from Oracle.
# ─────────────────────────────────────────────────────────────────

# NLS session init — run once per JDBC connection when it opens
_NLS_INIT = (
    "BEGIN "
    "EXECUTE IMMEDIATE 'ALTER SESSION SET NLS_DATE_FORMAT=''YYYY-MM-DD\"T\"HH24:MI:SS'''; "
    "EXECUTE IMMEDIATE 'ALTER SESSION SET TIME_ZONE=''UTC'''; "
    "END;"
)


def read_oracle() -> DataFrame:
    predicates     = ext.get("jdbc_predicates")       # list of WHERE-clause strings
    partition_col  = ext.get("jdbc_partition_col")
    lower_bound    = ext.get("jdbc_lower_bound")
    upper_bound    = ext.get("jdbc_upper_bound")
    num_partitions = str(ext.get("jdbc_partitions",   4))
    fetch_size     = str(ext.get("jdbc_fetchsize",    10_000))
    # Hard cap on any single JDBC read. Default 2h matches the upper end
    # of pipeline glue.timeout_minutes; configurable per-pipeline. Bounds
    # the damage if Oracle returns a slow plan — instead of an executor
    # hanging until forced-kill (which leaks sessions Oracle can't reap
    # for ~SQLNET.EXPIRE_TIME), the read fails fast and finally{} runs.
    read_timeout_ms = str(ext.get("jdbc_read_timeout_ms", 7_200_000))
    # Spark maps an unconstrained Oracle NUMBER to DecimalType(38,10); a value
    # needing more than 38 unscaled digits aborts the whole read with
    # DECIMAL_PRECISION_EXCEEDS_MAX_PRECISION (seen on APPS.MTL_SYSTEM_ITEMS_B
    # .COVERAGE_SCHEDULE_ID, precision 49). customSchema overrides the inferred
    # type for the named columns ONLY — every other column stays auto-inferred,
    # so `SELECT *` keeps picking up new source columns. Preferred over listing
    # extract.columns just to cast one field on a 300-column EBS table.
    #   e.g. "COVERAGE_SCHEDULE_ID STRING"
    #        "COVERAGE_SCHEDULE_ID DECIMAL(38,0), OTHER_ID STRING"
    # STRING never overflows; a bounded DECIMAL still can, so prefer STRING
    # unless a consumer needs the column numeric.
    custom_schema   = ext.get("custom_schema")

    sql = build_sql()
    print(f"[EXTRACT SQL] {sql[:200]}")
    if custom_schema:
        print(f"[JDBC] customSchema override: {custom_schema}")

    if predicates:
        # Composite strategy (date_hash / date_id_range / date_range).
        # spark.read.jdbc with predicates= creates one RDD partition per string;
        # num_workers controls how many run concurrently.
        # sql is already "(SELECT ...) t" — Spark appends each predicate as WHERE.
        props = {
            "user":                       oracle_user,
            "password":                   oracle_pass,
            "driver":                     "oracle.jdbc.OracleDriver",
            "fetchsize":                  fetch_size,
            "sessionInitStatement":       _NLS_INIT,
            "oracle.net.CONNECT_TIMEOUT": "30000",
            "oracle.net.READ_TIMEOUT":    read_timeout_ms,
            "oracle.jdbc.ReadTimeout":    read_timeout_ms,
            "oracle.net.disableOob":      "false",
            "oracle.jdbc.keepAlive":      "true",
        }
        if custom_schema:
            props["customSchema"] = custom_schema
        print(f"[JDBC] predicate read: {len(predicates)} predicates")
        df = spark.read.jdbc(jdbc_url, sql, predicates=predicates, properties=props)

    else:
        reader = (
            spark.read.format("jdbc")
            .option("url",                    jdbc_url)
            .option("dbtable",                sql)
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

        if custom_schema:
            reader = reader.option("customSchema", custom_schema)

        if partition_col and lower_bound is not None and upper_bound is not None:
            reader = (
                reader
                .option("partitionColumn", partition_col)
                .option("lowerBound",      str(lower_bound))
                .option("upperBound",      str(upper_bound))
                .option("numPartitions",   num_partitions)
            )
            print(
                f"[JDBC] parallel read: col={partition_col} "
                f"partitions={num_partitions} bounds=[{lower_bound},{upper_bound}]"
            )
        else:
            print("[JDBC] single-partition read (set jdbc_partition_col + bounds for parallel reads on large tables)")

        df = reader.load()

    df.cache()
    print("[JDBC] DataFrame cached")
    return df


# ─────────────────────────────────────────────────────────────────
# STEP 7 — DATA QUALITY CHECKS  (single Spark pass)
# All aggregations — total row count, per-column null counts, and
# MAX(effective watermark) for incremental pipelines — are computed in one
# .agg() call so Oracle is read only once for quality purposes.
# Raises ValueError if row count < quality.min_rows_expected.
# Returns (df, quality_dict, max_wm_raw) — df is unchanged.
# ─────────────────────────────────────────────────────────────────
def run_quality_checks(df: DataFrame) -> tuple[DataFrame, dict, object]:
    null_cols = [c for c in qual.get("not_null_cols", []) if c in df.columns]
    wm_col    = ext.get("watermark_col") if ext["mode"] == "incremental" else None
    wm_fb     = _optional_watermark_fallback(ext) if wm_col else None
    # Keyed off mode, not phase, on purpose: this also fires on the initial
    # (sentinel) run of an incremental pipeline, whose MAX is what the
    # watermark advances to. A missing column fails the run here instead of
    # silently leaving the watermark where it was.
    if wm_col:
        require_watermark_column(wm_col, wm_fb, df.columns)

    agg_exprs = [F.count("*").alias("__total__")]
    agg_exprs += [
        F.sum(F.col(c).isNull().cast("int")).alias(f"__null_{c}__")
        for c in null_cols
    ]
    if wm_col:
        wm_expr = F.col(wm_col)
        if wm_fb:
            wm_expr = F.coalesce(F.col(wm_col), F.col(wm_fb))
        agg_exprs.append(F.max(wm_expr).alias("__max_wm__"))

    result     = df.agg(*agg_exprs).collect()[0]
    rows_total = result["__total__"]
    null_counts = {c: result[f"__null_{c}__"] or 0 for c in null_cols}
    max_wm_raw  = result["__max_wm__"] if wm_col else None

    min_expected = int(qual.get("min_rows_expected", 0))
    # min_rows_expected is a sanity check for INITIAL/FULL loads (catch a
    # partial source dump). For INCREMENTAL runs, the delta is whatever the
    # upstream actually changed since the last watermark — could legitimately
    # be 0 (steady state), 2 (a few late edits), or thousands. Don't reject
    # low counts on incremental.
    if PHASE == "incremental":
        if rows_total == 0:
            print(
                f"[QUALITY] incremental no-op: 0 new rows since watermark="
                f"{ext.get('watermark_value')}"
            )
        else:
            print(
                f"[QUALITY] incremental delta: {rows_total} new rows since "
                f"watermark={ext.get('watermark_value')} (min_rows_expected "
                f"not enforced on incremental)"
            )
    elif rows_total < min_expected:
        raise QualityGateWarning(
            f"Quality gate failed: got {rows_total} rows, expected >= {min_expected}",
            rows_total=rows_total,
            null_counts=null_counts,
        )

    print(
        f"[QUALITY] rows={rows_total} nulls={json.dumps(null_counts)}"
    )
    return df, {
        "rows_total":    rows_total,
        "rows_rejected": 0,
        "null_counts":   null_counts,
    }, max_wm_raw


# ─────────────────────────────────────────────────────────────────
# STEP 8 — ENRICH WITH PIPELINE METADATA COLUMNS
# Stamps every row with pipeline tracking columns.
# These columns do not exist in Oracle — they are added at ingest time.
# _extract_date drives Parquet partitioning on S3.
# ─────────────────────────────────────────────────────────────────
def enrich(df: DataFrame) -> DataFrame:
    return (
        df
        .withColumn("_pipeline_id",        F.lit(PIPELINE_ID))
        .withColumn("_glue_run_id",        F.lit(RUN_ID))
        .withColumn("_extract_mode",       F.lit(ext["mode"]))
        .withColumn("_extract_timestamp",  F.current_timestamp())
        .withColumn("_extract_date",       F.to_date(F.current_timestamp()))
    )


# ─────────────────────────────────────────────────────────────────
# STEP 9 — WRITE BRONZE (APPEND-ONLY PARQUET)
# mode="append" + partitionBy(_extract_date) = new files each run.
# Files are never overwritten — Bronze is immutable by design.
# Layout: s3://bucket/bronze/<domain>/<table>/extract_date=YYYY-MM-DD/
# maxRecordsPerFile: configurable via target.max_records_per_file
#   (default 500_000) to keep output files at a manageable size for
#   downstream Silver reads.
# NOTE: write and watermark advance are NOT atomic. If the job is killed
# between write_bronze and advance_watermark, the next run will re-extract
# the same window → Bronze duplicates. Deduplicate at Silver layer.
# ─────────────────────────────────────────────────────────────────
def write_bronze(df: DataFrame):
    partition_cols      = tgt.get("partition_cols", ["_extract_date"])
    compression         = tgt.get("compression", "snappy")
    max_records_per_file = str(tgt.get("max_records_per_file", 500_000))

    (
        df.write
        .mode("append")
        .partitionBy(*partition_cols)
        .option("compression",       compression)
        .option("maxRecordsPerFile", max_records_per_file)
        .parquet(TARGET_PATH)
    )
    print(f"[WRITE BRONZE] path={TARGET_PATH} partitions={partition_cols}")


# ─────────────────────────────────────────────────────────────────
# STEP 10 — ADVANCE WATERMARK
# Incremental only: accepts the pre-computed MAX(effective watermark) value
# from run_quality_checks so no additional Spark action is needed.
# Updates extract.watermark_value in DynamoDB so the next scheduled
# run picks up from exactly where this run ended.
# Skipped entirely for full-reload pipelines.
# ─────────────────────────────────────────────────────────────────
def advance_watermark(max_wm_raw) -> str:
    if ext["mode"] != "incremental":
        return "N/A"

    if max_wm_raw is None:
        print("[WATERMARK] no rows in extract window — watermark unchanged")
        return ext.get("watermark_value", "N/A")

    new_wm_str = max_wm_raw.isoformat() if hasattr(max_wm_raw, "isoformat") else str(max_wm_raw)

    # Compose the metadata map server-side rather than writing nested keys.
    # Older pipelines lack a `metadata` map entirely — SETting #meta.last_run_id
    # against a missing parent fails with ValidationException. Build the merged
    # map from the cfg snapshot loaded at job start and overwrite atomically.
    # 'extract' and 'metadata' are DynamoDB reserved keywords — alias them.
    merged_meta = dict(cfg.get("metadata") or {})
    merged_meta["last_run_id"] = RUN_ID
    merged_meta["last_run_at"] = datetime.now(timezone.utc).isoformat()

    dynamo.Table(CONFIG_TABLE).update_item(
        Key={"pipeline_id": PIPELINE_ID, "version": "latest"},
        UpdateExpression="SET #ext.watermark_value = :wm, #meta = :meta",
        ExpressionAttributeNames={
            "#ext":  "extract",
            "#meta": "metadata",
        },
        ExpressionAttributeValues={
            ":wm":   new_wm_str,
            ":meta": merged_meta,
        },
    )
    print(
        f"[WATERMARK ADVANCED] {ext.get('watermark_value')} → {new_wm_str}"
    )
    return new_wm_str


# ─────────────────────────────────────────────────────────────────
# STEP 11 — TRIGGER SILVER (non-fatal)
# Fires the Silver Glue job for this pipeline if silver.enabled=true.
# Non-fatal: Bronze succeeded regardless — Silver will pick up the
# data on the next trigger if this call fails.
# ─────────────────────────────────────────────────────────────────
# Routing policy lives in silver_routing.py so that tests import the real
# implementation instead of a copy. See that module's docstring: the copy is
# why the UIFPE-355 job renames shipped broken with a green suite.
from silver_routing import resolve_silver_trigger  # noqa: E402


def trigger_silver(config: dict, rows_read: int) -> None:
    silver_cfg = config.get("silver", {})
    if not silver_cfg.get("enabled", False):
        return

    silver_job, should_trigger = resolve_silver_trigger(
        dest_cloud = DEST_CLOUD,
        write_mode = silver_cfg.get("write_mode"),
        resource_prefix = RESOURCE_PREFIX,
        rows_read  = rows_read,
    )

    if not should_trigger:
        print(f"[SILVER SKIPPED] 0 rows + Spark-based silver — not starting {silver_job}")
        return

    try:
        glue_client = boto3.client("glue", region_name=REGION)
        glue_client.start_job_run(
            JobName=silver_job,
            Arguments={"--pipeline_id": PIPELINE_ID, "--env": ENV},
        )
        print(f"[SILVER TRIGGERED] {silver_job} started (rows_read={rows_read})")
    except Exception as exc:
        print(f"[WARN] Silver trigger failed: {exc} — Bronze data is safe, "
              f"Silver will catch up on next Bronze run (or manual start-job-run)")


# ─────────────────────────────────────────────────────────────────
# STEP 12 — WRITE METRICS (always runs via finally block)
# Persists a full run record to glue_job_metrics DynamoDB table.
# TTL = epoch + 90 days so old runs auto-expire without manual cleanup.
# Written on both SUCCESS and FAILURE — error_message captures traceback.
# React UI reads this table via the pipeline_controller Lambda API.
# ─────────────────────────────────────────────────────────────────
def write_metrics(
    run_status:    str,
    quality:       dict,
    new_wm:        str,
    error:         str = None,
):
    completed_at = datetime.now(timezone.utc)
    rows_total    = quality.get("rows_total",    0)
    rows_rejected = quality.get("rows_rejected", 0)
    item = {
        "job_name":           JOB_NAME,
        "run_id":             RUN_ID,
        "pipeline_id":        PIPELINE_ID,
        "oracle_table":       ext.get("table", "custom_sql"),
        "source_secret_arn":  src["secret_arn"],
        "extract_mode":       ext["mode"],
        "watermark_start":    ext.get("watermark_value", "N/A"),
        "watermark_end":      new_wm,
        "rows_read":          rows_total,
        "rows_written":       rows_total - rows_rejected,
        "rows_rejected":      rows_rejected,
        "null_counts":        json.dumps(quality.get("null_counts", {})),
        # destination_cloud + bronze_target_path are cloud-agnostic; the legacy
        # s3_target_path field is still populated for AWS pipelines so the
        # existing dashboard + gcs_replicator continue working unchanged.
        "destination_cloud":  DEST_CLOUD,
        "bronze_target_path": TARGET_PATH,
        "s3_target_path":     TARGET_PATH if DEST_CLOUD == "aws" else None,
        "gcs_target_path":    TARGET_PATH if DEST_CLOUD == "gcp" else None,
        "status":             run_status,
        "error_message":      error,
        "started_at":         started_at.isoformat(),
        "completed_at":       completed_at.isoformat(),
        "duration_sec":       int((completed_at - started_at).total_seconds()),
        "glue_worker_type":   glue_cfg.get("worker_type", "G.1X"),
        "glue_num_workers":   int(glue_cfg.get("num_workers", 2)),
        "load_phase":         PHASE,
        "ttl":                int(time.time()) + (90 * 86400),
    }
    # Strip None fields so DynamoDB doesn't store explicit NULLs that would
    # confuse the dashboard's "field exists?" checks.
    item = {k: v for k, v in item.items() if v is not None}
    dynamo.Table(METRICS_TABLE).put_item(Item=to_dynamo_safe(item))
    print(
        f"[METRICS WRITTEN] status={run_status} rows={item['rows_written']} "
        f"duration={item['duration_sec']}s"
    )


# ─────────────────────────────────────────────────────────────────
# MAIN — error-safe orchestration
# Steps 5–10 are wrapped so Step 11 (metrics) always fires,
# even if the job crashes mid-way.
# raw_df is unpersisted after write to release executor memory.
# job.commit() signals Glue that the run completed successfully,
# enabling job bookmark support if activated in future.
# Exit code 1 on failure so EventBridge / Step Functions detect it.
# ─────────────────────────────────────────────────────────────────
raw_df = None
try:
    raw_df                           = read_oracle()
    clean_df, quality_result, max_wm = run_quality_checks(raw_df)
    if quality_result["rows_total"] > 0:
        enriched_df = enrich(clean_df)
        write_bronze(enriched_df)
    new_watermark                    = advance_watermark(max_wm)
    # trigger_silver gates internally — bq_native always fires (self-handles
    # 0 rows + recovers from prior failed triggers); Spark variants skip 0-row.
    trigger_silver(cfg, quality_result["rows_total"])
    status                           = "SUCCEEDED"
    job.commit()

except QualityGateWarning as qgw:
    error_msg = str(qgw)
    quality_result = {
        "rows_total":    qgw.rows_total,
        "rows_rejected": 0,
        "null_counts":   qgw.null_counts,
    }
    status = "WARNING"
    print(f"[QUALITY WARNING] {error_msg}")
    job.commit()

except Exception:
    error_msg = traceback.format_exc()
    print(f"[FATAL ERROR]\n{error_msg}")

finally:
    if raw_df is not None:
        raw_df.unpersist()
    write_metrics(status, quality_result, new_watermark, error_msg)
    spark.stop()

# Raise after cleanup so Glue marks the job FAILED correctly.
# WARNING is a soft failure (quality gate) — metrics written, exit 0.
# sys.exit() is intentionally avoided — it raises SystemExit which Glue 5.1
# treats as a failure even with exit code 0.
if status not in ("SUCCEEDED", "WARNING"):
    raise RuntimeError(f"Pipeline {PIPELINE_ID} failed — see error_message in metrics table.")
