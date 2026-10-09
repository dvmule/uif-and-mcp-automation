# generic_silver_bq_native.py
# BQ-native Silver for GCP pipelines with silver.write_mode="bq_native".
# Replaces the Spark-based generic_silver_transform_gcp_bq.py — no Spark, no
# cross-cloud Parquet shuffle. All compute runs in BigQuery slots.
#
# Two execution paths, resolved at startup from silver.last_processed_date:
#
#   INITIAL    (sentinel watermark, "", null, or "1970-01-01"):
#     1. LOAD all Bronze hive partitions → BQ staging table
#     2. CREATE OR REPLACE target PARTITION BY ... CLUSTER BY ...
#        AS SELECT * EXCEPT (_extract_date), CURRENT_TIMESTAMP AS _silver_updated_at
#        FROM staging QUALIFY ROW_NUMBER() OVER (...) = 1
#     3. Drop staging
#
#   INCREMENTAL (real timestamp watermark):
#     1. Idempotent CREATE OR REPLACE EXTERNAL TABLE over Bronze prefix
#     2. ALTER target ADD COLUMN IF NOT EXISTS — auto-evolve for new cols only
#     3. MERGE target USING (SELECT * EXCEPT(_extract_date), CURRENT_TIMESTAMP AS _silver_updated_at
#                            FROM external WHERE <incremental filter>)
#        ON pk-equality (IS NOT DISTINCT FROM for NULL-safety)
#           [+ PK BETWEEN min/max of the slice — clustered block pruning]
#        WHEN MATCHED THEN UPDATE SET <enumerated non-pk cols>
#        WHEN NOT MATCHED THEN INSERT ROW
#
#     Incremental filter (build_incremental_filter): exact timestamp watermark
#     `_extract_date >= DATE(lp_ts) AND _extract_timestamp > TIMESTAMP(lp_ts)`
#     once silver.last_processed_ts exists (date predicate prunes hive
#     partitions; ts predicate catches same-day Bronze re-runs); legacy
#     date-only `_extract_date > last_processed_date` until then.
#
# Glue Python Shell job (max_capacity = 1 DPU = $0.06/hr). No Spark cluster.
# BQ does all the heavy work — typical wall-clock: 2-3 min initial, <1 min
# incremental.
#
# Origin: tasks/silver-bq-native-migration.md (Phase 2). Phase 1 spike validated
# the LOAD+CTAS pattern on apps-xxpso-pos-orasale-stg (190M rows in 2m 30s).

from __future__ import annotations
import argparse, json, os, sys, time, traceback
from datetime import datetime, timezone, timedelta
from decimal import Decimal

import boto3
from google.cloud import bigquery
from google.api_core import exceptions as gcp_exceptions
from google.cloud.exceptions import NotFound
from google.oauth2 import service_account

from bq_partition_ddl import build_partition_clause

# Glue Python Shell pipes stdout (not a tty) → Python block-buffers print(),
# so CloudWatch shows nothing until the process exits. Line-buffer instead so
# each step's log line lands live — essential for watching a long MERGE.
sys.stdout.reconfigure(line_buffering=True)


def _is_empty_bronze_error(exc: BaseException) -> bool:
    """True when Bronze GCS prefix has no parquet (BQ LOAD/uris 404)."""
    msg = str(exc)
    return "Not found: Uris" in msg or (
        "404" in msg and "bronze" in msg.lower()
    )


# ─────────────────────────────────────────────────────────────────
# BOOTSTRAP — Python Shell uses sys.argv; no getResolvedOptions.
# ─────────────────────────────────────────────────────────────────
def _parse_args():
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--JOB_NAME",      default=None)
    p.add_argument("--JOB_RUN_ID",    default=f"local_{int(time.time())}")
    p.add_argument("--pipeline_id",   required=True)
    p.add_argument("--env",           default="dev")
    p.add_argument("--CONFIG_TABLE",  required=True)
    p.add_argument("--METRICS_TABLE", required=True)
    p.add_argument("--RESOURCE_PREFIX", default="")
    known, _ = p.parse_known_args(sys.argv[1:])
    if not known.JOB_NAME:
        # Terraform renamed this job silver-bq-native ->
        # silver-transform-bq-native in UIFPE-355; the label has to follow, or
        # metrics rows name a job that does not exist.
        _prefix = known.RESOURCE_PREFIX or f"echo-{known.env}"
        known.JOB_NAME = f"{_prefix}-silver-transform-bq-native"
    return known


ARGS          = _parse_args()
PIPELINE_ID   = ARGS.pipeline_id
JOB_NAME      = ARGS.JOB_NAME
RUN_ID        = ARGS.JOB_RUN_ID
ENV           = ARGS.env
CONFIG_TABLE  = ARGS.CONFIG_TABLE
METRICS_TABLE = ARGS.METRICS_TABLE
REGION        = "eu-west-1"

dynamo     = boto3.resource("dynamodb",     region_name=REGION)
secrets    = boto3.client("secretsmanager", region_name=REGION)
started_at = datetime.now(timezone.utc)

# Short suffix so concurrent runs don't collide on staging table names.
RUN_ID_SHORT = RUN_ID.replace("-", "")[:12] if RUN_ID else "noid"

# Phase resolution sentinel — mirror lambda/pipeline_profiles.SENTINEL_WATERMARKS.
SENTINEL_WATERMARKS = {"", None, "1970-01-01", "1970-01-01T00:00:00"}


# ─────────────────────────────────────────────────────────────────
# DYNAMO / METRICS HELPERS
# Same shape + table as the Spark job so dashboards keep working.
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


def write_skipped_metrics():
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


def write_metrics(*, status: str, phase: str, rows_read: int, rows_written: int,
                  silver_table: str, error: str | None = None,
                  bq_status: str = "not_attempted", bq_table: str | None = None,
                  load_seconds: float = 0.0, query_seconds: float = 0.0):
    completed_at = datetime.now(timezone.utc)
    item = {
        "job_name":          JOB_NAME,
        "run_id":            RUN_ID,
        "pipeline_id":       PIPELINE_ID,
        "silver_table":      silver_table,
        "destination_cloud": "gcp",
        "silver_write_mode": "bq_native",
        "phase":             phase,
        "bq_status":         bq_status,
        "bq_table":          bq_table,
        "status":            status,
        "rows_read":         rows_read,
        "rows_written":      rows_written,
        "rows_rejected":     max(0, rows_read - rows_written),
        "error_message":     error,
        "load_seconds":      round(load_seconds, 1),
        "query_seconds":     round(query_seconds, 1),
        "started_at":        started_at.isoformat(),
        "completed_at":      completed_at.isoformat(),
        "duration_sec":      int((completed_at - started_at).total_seconds()),
        "ttl":               int(time.time()) + (90 * 86400),
    }
    item = {k: v for k, v in item.items() if v is not None}
    dynamo.Table(METRICS_TABLE).put_item(Item=to_dynamo_safe(item))
    print(
        f"[METRICS WRITTEN] status={status} phase={phase} rows_written={rows_written} "
        f"bq_status={bq_status} duration={item['duration_sec']}s"
    )


def advance_watermark(new_last_processed: str, new_last_processed_ts: str | None = None):
    """Write back silver.last_processed_date (+ last_processed_ts) + metadata.last_run_*."""
    update_expr = (
        "SET #slv.last_processed_date = :d, "
        "#meta.last_run_id = :rid, "
        "#meta.last_run_at = :ts"
    )
    values = {
        ":d":   new_last_processed,
        ":rid": RUN_ID,
        ":ts":  datetime.now(timezone.utc).isoformat(),
    }
    if new_last_processed_ts:
        update_expr += ", #slv.last_processed_ts = :lpts"
        values[":lpts"] = new_last_processed_ts
    dynamo.Table(CONFIG_TABLE).update_item(
        Key={"pipeline_id": PIPELINE_ID, "version": "latest"},
        UpdateExpression=update_expr,
        ExpressionAttributeNames={"#slv": "silver", "#meta": "metadata"},
        ExpressionAttributeValues=values,
    )
    print(
        f"[WATERMARK ADVANCED] silver.last_processed_date → {new_last_processed}"
        + (f" last_processed_ts → {new_last_processed_ts}" if new_last_processed_ts else "")
    )


# ─────────────────────────────────────────────────────────────────
# PHASE RESOLUTION + GCP CREDENTIAL BOOTSTRAP
# ─────────────────────────────────────────────────────────────────
def resolve_phase(silver_cfg: dict) -> str:
    """initial when watermark is a sentinel; incremental otherwise."""
    lpd = silver_cfg.get("last_processed_date")
    return "initial" if lpd in SENTINEL_WATERMARKS else "incremental"


def gcp_client(secret_arn: str, bq_project: str) -> bigquery.Client:
    raw = secrets.get_secret_value(SecretId=secret_arn)["SecretString"]
    info = json.loads(raw)
    creds = service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )
    print(f"[GCP CREDENTIALS] project={info.get('project_id')} sa={info.get('client_email')}")
    return bigquery.Client(credentials=creds, project=bq_project)


# ─────────────────────────────────────────────────────────────────
# SHARED — PK NULL-safe equality + decimal target list
# ─────────────────────────────────────────────────────────────────
def to_bq_column_name(name: str) -> str:
    """Map Oracle/Parquet field names to BigQuery SQL identifiers.

    BQ LOAD/BigLake sanitize ``#`` to ``_`` (``SHEET#`` → ``SHEET_``). Config
    and target schemas may still carry the Oracle name — always emit SQL with
    the sanitized form."""
    return name.replace("#", "_")


def bq_ident(name: str) -> str:
    return f"`{to_bq_column_name(name)}`"


def canonical_bq_columns(cols: list[str]) -> list[str]:
    """Drop Oracle/raw duplicates that map to the same BQ identifier."""
    seen: set[str] = set()
    out: list[str] = []
    for c in cols:
        bq = to_bq_column_name(c)
        if bq in seen:
            continue
        seen.add(bq)
        out.append(bq)
    return out


def resolve_source_field_name(col: str, source_by_name: dict) -> str:
    """Field name as it appears on the external/staging table."""
    bq = to_bq_column_name(col)
    if bq in source_by_name:
        return bq
    if col in source_by_name:
        return col
    return bq


def build_pk_conditions(primary_key_cols: list,
                        alias_left: str = "t", alias_right: str = "s",
                        null_safe: bool = True) -> str:
    """PK equality for MERGE ON.

    null_safe=True (default): `IS NOT DISTINCT FROM` returns TRUE when both
    sides are NULL — required for composite PKs with nullable cols (e.g.
    fipm.fpi_par_batch's FCT_PERIOD_ID). BQ supports it in MERGE; for
    reconcile NOT EXISTS, see generic_silver_reconcile_gcp_bq.py — that needs
    the COALESCE(CAST(... AS STRING)) workaround instead.

    null_safe=False: plain `=`. Semantically identical when the merge source
    slice has no NULL PKs (a NULL never matches under either operator then),
    and much friendlier to the optimizer — IS NOT DISTINCT FROM blocks the
    broadcast join path, forcing a full shuffle of the target. On a small
    reservation that shuffle exceeds BQ's disk/memory shuffle quota
    ('Resources exceeded during query execution'). Callers should pass
    null_safe=False whenever the slice is proven NULL-free."""
    op = "IS NOT DISTINCT FROM" if null_safe else "="
    return " AND ".join([
        f"{alias_left}.{bq_ident(c)} {op} {alias_right}.{bq_ident(c)}"
        for c in primary_key_cols
    ])


def build_incremental_filter(silver_cfg: dict) -> str:
    """WHERE clause selecting the unprocessed Bronze slice.

    Timestamp-granularity when silver.last_processed_ts exists: the date
    predicate prunes hive partitions, the timestamp predicate is exact — a
    Bronze re-run later on an already-processed _extract_date is picked up
    by the next Silver run instead of being skipped forever.

    Date-only (legacy, strictly-greater) when the field is absent: preserves
    historic behavior for configs that predate last_processed_ts. Every
    successful run writes the ts back, so pipelines self-migrate onto the
    exact filter after one incremental.
    """
    lp_ts = silver_cfg.get("last_processed_ts")
    if lp_ts:
        return (
            f"_extract_date >= DATE('{lp_ts[:10]}') "
            f"AND _extract_timestamp > TIMESTAMP('{lp_ts}')"
        )
    lp_date = silver_cfg["last_processed_date"][:10]
    return f"_extract_date > DATE('{lp_date}')"


NUMERIC_PK_LITERALS = {
    "INTEGER":    lambda v: str(int(v)),
    "INT64":      lambda v: str(int(v)),
    "NUMERIC":    lambda v: f"NUMERIC '{v}'",
    "BIGNUMERIC": lambda v: f"BIGNUMERIC '{v}'",
}


def build_pk_range_predicate(pk_col: str, target_field, pk_min, pk_max) -> str | None:
    """Optional `AND t.pk BETWEEN min AND max` for the MERGE ON clause.

    With the target clustered on its PK, an explicit range on the merge key
    lets BQ prune clustered blocks instead of scanning the full table —
    the dominant cost when a small delta merges into a large target.
    Literals are typed to match the TARGET column so pruning isn't defeated
    by an implicit cast of the clustering column."""
    if pk_min is None or pk_max is None or target_field is None:
        return None
    render = NUMERIC_PK_LITERALS.get(target_field.field_type)
    if render is None:
        return None
    return f"t.{bq_ident(pk_col)} BETWEEN {render(pk_min)} AND {render(pk_max)}"


def _merge_source_select_expr(col: str, target_by_name: dict, source_by_name: dict) -> str:
    """Project a Bronze external column for MERGE source, casting when the target
    table was narrowed (e.g. RSS_LINE_ID INT64) but Parquet still reads as
    BIGNUMERIC via decimal_target_types."""
    bq_col = to_bq_column_name(col)
    src_name = resolve_source_field_name(col, source_by_name)
    tf = target_by_name.get(col) or target_by_name.get(bq_col)
    sf = source_by_name.get(src_name)
    # BQ Python client reports INT64 columns as field_type "INTEGER".
    if (
        tf
        and sf
        and tf.field_type in ("INT64", "INTEGER")
        and sf.field_type in ("BIGNUMERIC", "NUMERIC")
    ):
        expr = f"CAST({bq_ident(src_name)} AS INT64)"
    else:
        expr = bq_ident(src_name)
    if expr != bq_ident(bq_col):
        return f"{expr} AS {bq_ident(bq_col)}"
    return expr


# Phase 1 spike learning: Oracle NUMBER → Parquet DECIMAL(38,10) can overflow
# BQ default NUMERIC(38,9). Fall back to BIGNUMERIC, then STRING.
DECIMAL_TARGET_TYPES = ["NUMERIC", "BIGNUMERIC", "STRING"]


# ─────────────────────────────────────────────────────────────────
# INITIAL PATH — LOAD + CTAS dedup
# ─────────────────────────────────────────────────────────────────
def run_initial(*, bq: bigquery.Client, cfg: dict, silver_cfg: dict) -> dict:
    target_fqtn  = _target_fqtn(silver_cfg)
    staging_fqtn = _staging_fqtn(silver_cfg, "init")
    bronze_prefix = _bronze_prefix(cfg)
    pk_cols      = [to_bq_column_name(c) for c in silver_cfg["primary_key_cols"]]
    partition_clause, part_err = build_partition_clause(silver_cfg)
    if part_err:
        raise ValueError(f"[BQ PARTITION] invalid config: {part_err}")
    if partition_clause:
        print(f"[BQ PARTITION] {partition_clause}")
    location = silver_cfg.get("bq_location") or None

    print(f"[INITIAL] target={target_fqtn} staging={staging_fqtn} prefix={bronze_prefix}")

    # ── 1. LOAD all Bronze hive partitions → staging ────────────
    t0 = time.time()
    load_cfg = bigquery.LoadJobConfig(
        source_format     = bigquery.SourceFormat.PARQUET,
        write_disposition = bigquery.WriteDisposition.WRITE_TRUNCATE,
        decimal_target_types = DECIMAL_TARGET_TYPES,
        hive_partitioning = bigquery.HivePartitioningOptions.from_api_repr({
            "mode": "AUTO",
            "sourceUriPrefix": bronze_prefix,
        }),
    )
    load_job = bq.load_table_from_uri(
        bronze_prefix + "*", staging_fqtn, job_config=load_cfg, location=location,
    )
    try:
        load_job.result()
    except (gcp_exceptions.NotFound, NotFound, Exception) as exc:
        if _is_empty_bronze_error(exc):
            bq.delete_table(staging_fqtn, not_found_ok=True)
            msg = f"Empty Bronze: no parquet files at {bronze_prefix}"
            print(f"[INITIAL WARNING] {msg}")
            return {
                "status": "WARNING",
                "phase":  "initial",
                "rows_read": 0, "rows_written": 0,
                "bq_status": "empty_bronze", "bq_table": target_fqtn,
                "load_seconds": time.time() - t0, "query_seconds": 0.0,
                "new_last_processed": None,
                "warning_message": msg,
            }
        raise
    load_s = time.time() - t0
    staging_rows = bq.get_table(staging_fqtn).num_rows
    print(f"[INITIAL LOAD] {staging_rows:,} rows in {load_s:.1f}s ({load_job.output_bytes or 0:,} bytes)")

    if staging_rows == 0:
        bq.delete_table(staging_fqtn, not_found_ok=True)
        return {
            "status": "SUCCEEDED",
            "phase":  "initial",
            "rows_read": 0, "rows_written": 0,
            "bq_status": "ok", "bq_table": target_fqtn,
            "load_seconds": load_s, "query_seconds": 0.0,
            "new_last_processed": None,  # nothing to advance
        }

    # ── 2. CREATE OR REPLACE target via CTAS dedup ──────────────
    t1 = time.time()
    cluster_cols = pk_cols[:4]                                                # BQ caps cluster at 4
    cluster_sql  = ", ".join(bq_ident(c) for c in cluster_cols) if cluster_cols else ""
    pk_partition_sql = ", ".join(bq_ident(c) for c in pk_cols)
    # silver.int64_cols: Oracle NUMBER ids land as BIGNUMERIC via
    # decimal_target_types — a 32-byte clustering/join key. Narrow the listed
    # columns to INT64 (caller asserts values fit); the incremental MERGE
    # already casts its source side when the target is INT64.
    int64_sql = ", ".join(
        f"CAST({bq_ident(c)} AS INT64) AS {bq_ident(c)}"
        for c in canonical_bq_columns(silver_cfg.get("int64_cols") or [])
    )

    target_sql = f"""
    CREATE OR REPLACE TABLE `{target_fqtn}`
    {partition_clause}
    {f'CLUSTER BY {cluster_sql}' if cluster_sql else ''}
    AS
    SELECT
      * EXCEPT (_extract_date){f' REPLACE ({int64_sql})' if int64_sql else ''},
      CURRENT_TIMESTAMP() AS _silver_updated_at
    FROM `{staging_fqtn}`
    QUALIFY ROW_NUMBER() OVER (
      PARTITION BY {pk_partition_sql}
      ORDER BY _extract_timestamp DESC
    ) = 1
    """
    print(f"[INITIAL CTAS] starting ...")
    ctas_job = bq.query(target_sql, location=location)
    ctas_job.result()
    query_s = time.time() - t1
    target_rows = bq.get_table(target_fqtn).num_rows
    print(
        f"[INITIAL CTAS] {target_rows:,} rows (dropped {staging_rows - target_rows} dupes) "
        f"in {query_s:.1f}s slot_ms={ctas_job.slot_millis or 0:,} "
        f"bytes_processed={ctas_job.total_bytes_processed or 0:,}"
    )

    # Watermark advance: max _extract_date/_extract_timestamp in staging (the
    # boundary we just processed). STRING projections so we don't depend on
    # the BQ DATE/TIMESTAMP→Python type coercion.
    boundary_row = next(iter(bq.query(
        f"SELECT FORMAT_DATE('%Y-%m-%d', MAX(_extract_date)) AS d, "
        f"CAST(MAX(_extract_timestamp) AS STRING) AS ts FROM `{staging_fqtn}`",
        location=location,
    ).result()))
    new_last_processed    = boundary_row.d
    new_last_processed_ts = boundary_row.ts

    # ── 3. Drop staging ─────────────────────────────────────────
    bq.delete_table(staging_fqtn, not_found_ok=True)
    print(f"[INITIAL] dropped staging {staging_fqtn}")

    return {
        "status": "SUCCEEDED",
        "phase":  "initial",
        "rows_read":    int(staging_rows),
        "rows_written": int(target_rows),
        "bq_status":    "ok",
        "bq_table":     target_fqtn,
        "load_seconds": load_s,
        "query_seconds": query_s,
        "new_last_processed": new_last_processed,
        "new_last_processed_ts": new_last_processed_ts,
    }


# ─────────────────────────────────────────────────────────────────
# INCREMENTAL PATH — BigLake external table + MERGE
# ─────────────────────────────────────────────────────────────────
def _ensure_external_table(*, bq: bigquery.Client, ext_fqtn: str,
                           bronze_prefix: str, location: str | None):
    """CREATE OR REPLACE EXTERNAL TABLE over Bronze. Idempotent — safe to
    re-run every Silver invocation. Refreshes the file list so newly-written
    Bronze partitions are visible."""
    ddl = f"""
    CREATE OR REPLACE EXTERNAL TABLE `{ext_fqtn}`
    WITH PARTITION COLUMNS (_extract_date DATE)
    OPTIONS (
      format                    = 'PARQUET',
      uris                      = ['{bronze_prefix}*'],
      hive_partition_uri_prefix = '{bronze_prefix}',
      decimal_target_types      = ['NUMERIC', 'BIGNUMERIC', 'STRING']
    )
    """
    bq.query(ddl, location=location).result()
    print(f"[EXTERNAL] {ext_fqtn} refreshed over {bronze_prefix}")


def _schema_evolve(*, bq: bigquery.Client, target_fqtn: str,
                   source_schema: list, location: str | None) -> dict:
    """Bring target schema forward to match source. Two safe operations:

    1. **ADD COLUMN IF NOT EXISTS** for fields in source missing from target.
    2. **ALTER COLUMN ... SET DATA TYPE** for safe widenings — currently only
       NUMERIC -> BIGNUMERIC. This happens routinely when Spark Bronze writes
       DECIMAL(38,10) for Oracle NUMBER columns; BQ reads the resulting
       Parquet as BIGNUMERIC via `decimal_target_types`, while a target table
       CTAS'd earlier (under a different Spark precision) may have NUMERIC for
       the same column. The MERGE then fails with `Value of type BIGNUMERIC
       cannot be assigned to t.<col>, which has type NUMERIC`.

    Type narrowings, renames, and drops are NOT applied — they would lose data
    and must be done by hand via `bq update`. Returns {added, widened} lists.
    """
    try:
        target_tbl = bq.get_table(target_fqtn)
    except NotFound:
        return {"added": [], "widened": []}  # initial path — caller will CTAS

    target_by_name = {f.name: f for f in target_tbl.schema}
    added:   list[str] = []
    widened: list[str] = []

    for f in source_schema:
        tf = target_by_name.get(f.name)
        if tf is None:
            sql = (
                f"ALTER TABLE `{target_fqtn}` "
                f"ADD COLUMN IF NOT EXISTS `{f.name}` {f.field_type}"
            )
            bq.query(sql, location=location).result()
            added.append(f.name)
            print(f"[SCHEMA EVOLVE] +{f.name} {f.field_type}")
        elif tf.field_type == "NUMERIC" and f.field_type == "BIGNUMERIC":
            sql = (
                f"ALTER TABLE `{target_fqtn}` "
                f"ALTER COLUMN `{f.name}` SET DATA TYPE BIGNUMERIC"
            )
            bq.query(sql, location=location).result()
            widened.append(f.name)
            print(f"[SCHEMA EVOLVE] ~{f.name} NUMERIC -> BIGNUMERIC")

    return {"added": added, "widened": widened}


def run_incremental(*, bq: bigquery.Client, cfg: dict, silver_cfg: dict) -> dict:
    target_fqtn  = _target_fqtn(silver_cfg)
    ext_fqtn     = _external_fqtn(cfg, silver_cfg)
    bronze_prefix = _bronze_prefix(cfg)
    pk_cols      = [to_bq_column_name(c) for c in silver_cfg["primary_key_cols"]]
    last_processed = silver_cfg["last_processed_date"]
    location     = silver_cfg.get("bq_location") or None

    where_clause = build_incremental_filter(silver_cfg)
    print(f"[INCREMENTAL] target={target_fqtn} ext={ext_fqtn} since={last_processed} "
          f"filter={where_clause}")

    # ── 1. Refresh BigLake external table ────────────────────────
    t0 = time.time()
    _ensure_external_table(bq=bq, ext_fqtn=ext_fqtn,
                           bronze_prefix=bronze_prefix, location=location)
    ext_setup_s = time.time() - t0

    ext_tbl = bq.get_table(ext_fqtn)
    # Strip BigLake auto-detected partition col _extract_date from source schema —
    # we don't want it on the target.
    source_schema  = [f for f in ext_tbl.schema if f.name != "_extract_date"]
    source_by_name = {f.name: f for f in source_schema}
    for f in source_schema:
        source_by_name.setdefault(to_bq_column_name(f.name), f)

    # PK slice stats: NULL count for any single-column PK (drives the plain-=
    # vs IS NOT DISTINCT FROM decision), MIN/MAX for numeric PKs (drives the
    # BETWEEN block-pruning predicate). pk_nulls also guards pruning
    # correctness: a NULL PK can't match inside BETWEEN.
    single_pk = pk_cols[0] if len(pk_cols) == 1 else None
    pk_field  = source_by_name.get(single_pk) if single_pk else None
    pk_range_eligible = pk_field is not None and pk_field.field_type in NUMERIC_PK_LITERALS
    pk_stats_sql = ""
    if pk_field is not None:
        pk_stats_sql = f", COUNTIF({bq_ident(single_pk)} IS NULL) AS pk_nulls"
        if pk_range_eligible:
            pk_stats_sql += (
                f", MIN({bq_ident(single_pk)}) AS pk_min, "
                f"MAX({bq_ident(single_pk)}) AS pk_max"
            )

    # Detect new rows via partition filter. If zero, exit early with a no-op
    # success — common case on quiet pipelines and the SAME bug the Spark path
    # had (only triggered Silver when Bronze had rows). Here we still refresh
    # the external table (cheap, idempotent) so the next real run is ready.
    count_sql = (
        f"SELECT COUNT(*) AS n, FORMAT_DATE('%Y-%m-%d', MAX(_extract_date)) AS max_d, "
        f"CAST(MAX(_extract_timestamp) AS STRING) AS max_ts{pk_stats_sql} "
        f"FROM `{ext_fqtn}` WHERE {where_clause}"
    )
    count_job = bq.query(count_sql, location=location)
    count_row = next(iter(count_job.result()))
    new_rows  = int(count_row.n or 0)
    max_d     = count_row.max_d
    max_ts    = count_row.max_ts
    print(f"[INCREMENTAL] candidate rows={new_rows:,} new_max_date={max_d} new_max_ts={max_ts}")

    if new_rows == 0:
        return {
            "status": "SUCCEEDED",
            "phase":  "incremental",
            "rows_read": 0, "rows_written": 0,
            "bq_status": "ok", "bq_table": target_fqtn,
            "load_seconds": ext_setup_s, "query_seconds": 0.0,
            "new_last_processed": None,
            "new_last_processed_ts": None,
        }

    # ── 2. Schema evolution: ADD COLUMN + safe widenings ─────────
    evolve = _schema_evolve(
        bq=bq, target_fqtn=target_fqtn,
        source_schema=source_schema, location=location,
    )

    # ── 3. MERGE filtered slice into target ──────────────────────
    target_tbl  = bq.get_table(target_fqtn)
    target_cols = canonical_bq_columns([f.name for f in target_tbl.schema])
    target_by_name = {f.name: f for f in target_tbl.schema}
    for f in target_tbl.schema:
        target_by_name.setdefault(to_bq_column_name(f.name), f)
    # Don't emit _silver_updated_at from the SELECT — we add it server-side.
    select_cols = [c for c in target_cols if c != "_silver_updated_at"]

    pk_set = set(pk_cols)
    non_pk_cols = [c for c in target_cols if c not in pk_set and c != "_silver_updated_at"]
    if non_pk_cols:
        set_assignments = ", ".join(f"t.{bq_ident(c)} = s.{bq_ident(c)}" for c in non_pk_cols)
        set_assignments += ", t.`_silver_updated_at` = CURRENT_TIMESTAMP()"
        update_clause = f"WHEN MATCHED THEN UPDATE SET {set_assignments} "
    else:
        update_clause = ""

    pk_slice_null_free = pk_field is not None and int(count_row.pk_nulls or 0) == 0
    pk_join = build_pk_conditions(pk_cols, "t", "s",
                                  null_safe=not pk_slice_null_free)
    if not pk_slice_null_free:
        print("[INCREMENTAL MERGE] null-safe join (composite PK or NULLs in slice)")
    if pk_range_eligible and pk_slice_null_free:
        range_pred = build_pk_range_predicate(
            single_pk, target_by_name.get(single_pk),
            count_row.pk_min, count_row.pk_max,
        )
        if range_pred:
            pk_join += f" AND {range_pred}"
            print(f"[INCREMENTAL MERGE] pk range pruning: {range_pred}")
    select_list = ", ".join(
        _merge_source_select_expr(c, target_by_name, source_by_name) for c in select_cols
    )
    insert_cols = ", ".join(bq_ident(c) for c in target_cols)
    insert_vals = ", ".join(
        f"s.{bq_ident(c)}" if c != "_silver_updated_at" else "CURRENT_TIMESTAMP()"
        for c in target_cols
    )

    merge_sql = f"""
    MERGE INTO `{target_fqtn}` t
    USING (
      SELECT {select_list}
      FROM `{ext_fqtn}`
      WHERE {where_clause}
      QUALIFY ROW_NUMBER() OVER (
        PARTITION BY {', '.join(bq_ident(c) for c in pk_cols)}
        ORDER BY _extract_timestamp DESC
      ) = 1
    ) s
    ON {pk_join}
    {update_clause}
    WHEN NOT MATCHED THEN INSERT ({insert_cols}) VALUES ({insert_vals})
    """
    t1 = time.time()
    print(f"[INCREMENTAL MERGE] starting ...")
    merge_job = bq.query(merge_sql, location=location)
    merge_job.result()
    query_s = time.time() - t1
    affected = merge_job.num_dml_affected_rows or 0
    print(
        f"[INCREMENTAL MERGE] affected_rows={affected:,} in {query_s:.1f}s "
        f"slot_ms={merge_job.slot_millis or 0:,} "
        f"bytes_processed={merge_job.total_bytes_processed or 0:,}"
    )

    return {
        "status": "SUCCEEDED",
        "phase":  "incremental",
        "rows_read":    int(new_rows),
        "rows_written": int(affected),
        "bq_status":    "ok",
        "bq_table":     target_fqtn,
        "load_seconds": ext_setup_s,
        "query_seconds": query_s,
        "schema_added_cols": evolve["added"],
        "schema_widened_cols": evolve["widened"],
        "new_last_processed": max_d,
        "new_last_processed_ts": max_ts,
    }


# ─────────────────────────────────────────────────────────────────
# FQTN HELPERS
# ─────────────────────────────────────────────────────────────────
def _target_fqtn(silver_cfg: dict) -> str:
    return f"{silver_cfg['bq_project']}.{silver_cfg['bq_dataset']}.{silver_cfg['table']}"


def _staging_fqtn(silver_cfg: dict, suffix: str) -> str:
    return f"{silver_cfg['bq_project']}.{silver_cfg['bq_dataset']}._stg_{silver_cfg['table']}_{suffix}_{RUN_ID_SHORT}"


def _external_fqtn(cfg: dict, silver_cfg: dict) -> str:
    # The external table is a BigLake view over Bronze GCS Parquet — it's a
    # Bronze-tier resource and lives in the Bronze BQ dataset, not Silver's.
    # Source: target.bq_dataset (alongside target.gcs_path which already names
    # the Bronze GCS landing zone).
    bronze_dataset = (cfg.get("target") or {}).get("bq_dataset")
    if not bronze_dataset:
        raise ValueError(
            "target.bq_dataset is required on GCP bq_native pipelines — "
            "this is the BQ dataset where the BigLake external table over "
            "Bronze GCS Parquet lives. Distinct from silver.bq_dataset."
        )
    return f"{silver_cfg['bq_project']}.{bronze_dataset}.{silver_cfg['table']}_EXT"


def _bronze_prefix(cfg: dict) -> str:
    p = cfg["target"]["gcs_path"]
    return p if p.endswith("/") else p + "/"


# ─────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────
def run():
    status        = "FAILED"
    error_msg     = None
    phase         = "unknown"
    rows_read     = 0
    rows_written  = 0
    silver_table  = "unknown"
    bq_status     = "not_attempted"
    bq_table_fqtn = None
    load_seconds  = 0.0
    query_seconds = 0.0

    try:
        cfg = load_config()
        print(f"[CONFIG LOADED] pipeline_id={PIPELINE_ID}")

        if not cfg.get("enabled", True):
            print(f"[SKIPPED] pipeline disabled")
            write_skipped_metrics()
            return

        silver_cfg = cfg.get("silver") or {}
        if not silver_cfg.get("enabled", False):
            print(f"[SKIPPED] silver not enabled")
            write_skipped_metrics()
            return

        # Guardrails — same as the Spark variant.
        write_mode = (silver_cfg.get("write_mode") or "iceberg_biglake").lower()
        if write_mode != "bq_native":
            raise ValueError(
                f"generic_silver_bq_native invoked for {PIPELINE_ID} but "
                f"silver.write_mode='{write_mode}' (expected 'bq_native'). "
                f"Check dispatcher routing."
            )
        dest_cfg = cfg.get("destination") or {}
        if (dest_cfg.get("cloud") or "aws").lower() != "gcp":
            raise ValueError(
                f"generic_silver_bq_native invoked for {PIPELINE_ID} but "
                f"destination.cloud != 'gcp'"
            )
        gcp_secret_arn = dest_cfg.get("gcp_credentials_secret_arn")
        if not gcp_secret_arn:
            raise ValueError("destination.gcp_credentials_secret_arn missing on config")
        if not silver_cfg.get("primary_key_cols"):
            raise ValueError(
                f"silver.primary_key_cols empty for {PIPELINE_ID} — refusing to run "
                f"(would collapse rows to one via global window in dedup)."
            )
        if not silver_cfg.get("bq_project") or not silver_cfg.get("bq_dataset") or not silver_cfg.get("table"):
            raise ValueError("silver.bq_project/bq_dataset/table required")
        if not cfg.get("target", {}).get("gcs_path"):
            raise ValueError("target.gcs_path required for GCP destination")
        if not cfg.get("target", {}).get("bq_dataset"):
            raise ValueError(
                "target.bq_dataset required for GCP bq_native — names the "
                "Bronze BQ dataset where the BigLake external table lives. "
                "Must differ from silver.bq_dataset."
            )
        if cfg["target"]["bq_dataset"] == silver_cfg["bq_dataset"]:
            raise ValueError(
                f"target.bq_dataset ({cfg['target']['bq_dataset']}) must differ "
                f"from silver.bq_dataset — Bronze external table and Silver "
                f"managed table belong in different datasets for tier separation."
            )

        silver_table = f"{silver_cfg['bq_dataset']}.{silver_cfg['table']}"
        phase = resolve_phase(silver_cfg)
        print(f"[PHASE] pipeline={PIPELINE_ID} phase={phase} "
              f"last_processed={silver_cfg.get('last_processed_date')!r}")

        bq = gcp_client(gcp_secret_arn, silver_cfg["bq_project"])

        if phase == "initial":
            result = run_initial(bq=bq, cfg=cfg, silver_cfg=silver_cfg)
        else:
            result = run_incremental(bq=bq, cfg=cfg, silver_cfg=silver_cfg)

        status        = result["status"]
        rows_read     = result["rows_read"]
        rows_written  = result["rows_written"]
        bq_status     = result["bq_status"]
        bq_table_fqtn = result["bq_table"]
        load_seconds  = result["load_seconds"]
        query_seconds = result["query_seconds"]
        if result.get("warning_message"):
            error_msg = result["warning_message"]

        if result.get("new_last_processed"):
            advance_watermark(result["new_last_processed"],
                              result.get("new_last_processed_ts"))

    except Exception:
        error_msg = traceback.format_exc()
        print(f"[FATAL ERROR]\n{error_msg}")
        if "Not found: Uris" in error_msg:
            status = "WARNING"

    finally:
        write_metrics(
            status=status, phase=phase,
            rows_read=rows_read, rows_written=rows_written,
            silver_table=silver_table, error=error_msg,
            bq_status=bq_status, bq_table=bq_table_fqtn,
            load_seconds=load_seconds, query_seconds=query_seconds,
        )

    if status not in ("SUCCEEDED", "WARNING"):
        raise RuntimeError(
            f"Silver pipeline {PIPELINE_ID} failed — see error_message in {METRICS_TABLE}."
        )


if __name__ == "__main__":
    run()
