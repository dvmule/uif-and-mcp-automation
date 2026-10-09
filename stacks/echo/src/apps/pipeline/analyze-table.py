"""
analyze_table.py — Glue ETL job (FLEX execution class).
Runs Oracle data-dictionary queries via Spark JDBC and writes sizing
recommendations (including jdbc_predicates for composite strategies) to
DynamoDB pipeline_analysis_{env}.

Required arguments (passed per-run):
  --secret_arn      Secrets Manager ARN for Oracle credentials
  --table           SCHEMA.TABLE (e.g. APPS.EGO_MTL_SY_ITEMS_EXT_B)

Optional arguments (passed per-run):
  --partition_col   Force numeric_range on this column (bypasses metadata queries)
  --date_col        Force date-based strategy on this column
  --hash_col        Column to ORA_HASH on for date_hash strategy
  --id_col          Numeric ID column for date_id_range strategy
  --date_start      Override MIN date bound (YYYY-MM-DD) — skips MIN probe
  --date_end        Override MAX date bound (YYYY-MM-DD) — skips MAX probe
  --hash_buckets    ORA_HASH bucket count (default: 8)

Injected from default_arguments:
  --ANALYSIS_TABLE  DynamoDB table to write results to
"""
import sys
import json
import math
import time
import traceback
import boto3
from datetime import datetime, timezone, date as _date
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

import argparse as _argparse

_p = _argparse.ArgumentParser(add_help=False)
_p.add_argument("--JOB_NAME",      default=None)
_p.add_argument("--env",           default="dev")
_p.add_argument("--JOB_RUN_ID",    default="unknown")
_p.add_argument("--partition_col", default=None)
_p.add_argument("--date_col",      default=None)
_p.add_argument("--hash_col",      default=None)
_p.add_argument("--id_col",        default=None)
_p.add_argument("--date_start",    default=None)
_p.add_argument("--date_end",      default=None)
_p.add_argument("--hash_buckets",  default="8")
_p.add_argument("--RESOURCE_PREFIX", default="")
_glue_defaults, _ = _p.parse_known_args(sys.argv[1:])
_env = getattr(_glue_defaults, "env", "dev")
if not _glue_defaults.JOB_NAME:
    # Prefix comes from Terraform, not from _env: this label is written into
    # the metrics row, and echo-{env} attributes the run to the wrong stack.
    _prefix = _glue_defaults.RESOURCE_PREFIX or f"echo-{_env}"
    _glue_defaults.JOB_NAME = f"{_prefix}-analyze-table"

from awsglue.utils import getResolvedOptions
from awsglue.context import GlueContext
from awsglue.job import Job
from pyspark.context import SparkContext

args = getResolvedOptions(sys.argv, ["secret_arn", "table", "ANALYSIS_TABLE"])

JOB_NAME       = _glue_defaults.JOB_NAME
RUN_ID         = _glue_defaults.JOB_RUN_ID
SECRET_ARN     = args["secret_arn"]
TABLE          = args["table"].upper()
ANALYSIS_TABLE = args["ANALYSIS_TABLE"]
REGION         = "eu-west-1"

PARTITION_COL  = _glue_defaults.partition_col.upper() if _glue_defaults.partition_col else None
DATE_COL       = _glue_defaults.date_col.upper()      if _glue_defaults.date_col      else None
HASH_COL       = _glue_defaults.hash_col.upper()      if _glue_defaults.hash_col      else None
ID_COL         = _glue_defaults.id_col.upper()        if _glue_defaults.id_col        else None
DATE_START     = _glue_defaults.date_start
DATE_END       = _glue_defaults.date_end
HASH_BUCKETS   = int(_glue_defaults.hash_buckets)

import os as _os
API_URL = _os.environ.get("EDS_API_BASE", _os.environ.get("API_URL", ""))

dynamo  = boto3.resource("dynamodb", region_name=REGION)
secrets = boto3.client("secretsmanager", region_name=REGION)

sc      = SparkContext()
glueCtx = GlueContext(sc)
spark   = glueCtx.spark_session
job     = Job(glueCtx)
job.init(JOB_NAME, args)
spark.sparkContext.setLogLevel("WARN")

started_at = datetime.now(timezone.utc)
status     = "FAILED"
result     = {}

_NLS_INIT = (
    "BEGIN "
    "EXECUTE IMMEDIATE 'ALTER SESSION SET NLS_DATE_FORMAT=''YYYY-MM-DD\"T\"HH24:MI:SS'''; "
    "EXECUTE IMMEDIATE 'ALTER SESSION SET TIME_ZONE=''UTC'''; "
    "END;"
)


# ── Helpers ───────────────────────────────────────────────────────

def to_dynamo_safe(obj):
    if isinstance(obj, float): return Decimal(str(obj))
    if isinstance(obj, dict):  return {k: to_dynamo_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):  return [to_dynamo_safe(i) for i in obj]
    return obj


def resolve_credentials():
    raw   = secrets.get_secret_value(SecretId=SECRET_ARN)["SecretString"]
    creds = json.loads(raw)
    dsn   = creds.get("dsn") or "//{host}:{port}/{service}".format(**creds)
    return creds["username"], creds["password"], f"jdbc:oracle:thin:@{dsn}"


def parse_table(table):
    parts = table.split(".", 1)
    if len(parts) != 2:
        raise ValueError(f"Table must be SCHEMA.TABLE, got: {table}")
    return parts[0], parts[1]


def jdbc_query(sql, jdbc_url, user, password, timeout_seconds=None):
    reader = (
        spark.read.format("jdbc")
        .option("url",                  jdbc_url)
        .option("dbtable",              f"({sql}) t")
        .option("user",                 user)
        .option("password",             password)
        .option("driver",               "oracle.jdbc.OracleDriver")
        .option("sessionInitStatement", _NLS_INIT)
        .option("fetchsize",            "500")
        .option("queryTimeout",         str(timeout_seconds or 300))  # 5 min default or specified
    )
    return [row.asDict() for row in reader.load().collect()]


# ── Pure-Python partition helpers ────────────────────────────────
# SYNC: mirror lambda/sizing_policy.py — Glue jobs cannot import Lambda modules.

MAX_PREDICATES      = 16
MAX_INITIAL_WORKERS = 8
GB_PER_SLICE        = 8.0
STAGED_BACKFILL_GB  = 80.0
GB_PER_WINDOW_FOR_ID_RANGE = 8.0
SMALL_TABLE_GB      = 20.0
NUMERIC_RANGE_MAX_GB = 40.0
DPU_FACTORS         = {"G.1X": 1, "G.2X": 2, "G.4X": 4, "G.8X": 8}
COST_NOTE = (
    "Predicates may exceed worker count; Spark runs JDBC partitions in waves. "
    "Wall-clock is longer but DPU rate stays capped."
)


def _stats_total_gb(stats):
    return (stats["num_rows"] * stats["avg_row_len"]) / (1024 ** 3)


def _target_parallelism(total_gb):
    preds = max(1, min(MAX_PREDICATES, math.ceil(total_gb / GB_PER_SLICE)))
    workers = min(MAX_INITIAL_WORKERS, preds)
    return preds, workers


def _choose_worker_type(total_gb, no_stats):
    if no_stats or total_gb >= SMALL_TABLE_GB:
        return "G.2X"
    return "G.1X"


def _month_ranges(start_date, end_date):
    y, m, result = start_date.year, start_date.month, []
    while _date(y, m, 1) <= end_date:
        s = _date(y, m, 1)
        ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
        result.append((s, _date(ny, nm, 1)))
        y, m = ny, nm
    return result


def _date_window_ranges(start_date, end_date, target):
    target = max(1, min(MAX_PREDICATES, target))
    total_months = (end_date.year - start_date.year) * 12 \
                   + (end_date.month - start_date.month) + 1
    if total_months <= target:
        return _month_ranges(start_date, end_date)
    if total_months // 3 <= target:
        windows, y, m = [], start_date.year, ((start_date.month - 1) // 3) * 3 + 1
        while _date(y, m, 1) <= end_date:
            s = _date(y, m, 1)
            nm = m + 3
            ny = y + (nm - 1) // 12
            nm = ((nm - 1) % 12) + 1
            windows.append((s, _date(ny, nm, 1)))
            y, m = ny, nm
        return windows
    total_years = end_date.year - start_date.year + 1
    bucket_years = max(1, math.ceil(total_years / target))
    windows, y = [], start_date.year
    while y <= end_date.year:
        windows.append((_date(y, 1, 1), _date(y + bucket_years, 1, 1)))
        y += bucket_years
    return windows


def _generate_predicates(strategy, date_col, date_bounds, total_gb,
                          id_col=None, id_min=None, id_max=None, id_splits=4,
                          hash_col=None, hash_buckets=8):
    target_preds, _ = _target_parallelism(total_gb)
    windows = _date_window_ranges(
        date_bounds["min_date"], date_bounds["max_date"], target=target_preds,
    )
    predicates = []
    if strategy == "date_range":
        for s, e in windows:
            predicates.append(f"{date_col} >= DATE '{s}' AND {date_col} < DATE '{e}'")
    elif strategy == "date_id_range":
        if id_min is None or id_max is None or not id_col:
            return _generate_predicates("date_range", date_col, date_bounds, total_gb)
        effective_id_splits = max(1, min(id_splits, MAX_PREDICATES // max(1, len(windows))))
        step = max(1, math.ceil((id_max - id_min + 1) / effective_id_splits))
        id_ranges = [
            (id_min + i * step, min(id_min + (i + 1) * step - 1, id_max))
            for i in range(effective_id_splits)
        ]
        for s, e in windows:
            for lo, hi in id_ranges:
                predicates.append(
                    f"{date_col} >= DATE '{s}' AND {date_col} < DATE '{e}' "
                    f"AND {id_col} BETWEEN {lo} AND {hi}"
                )
    elif strategy == "date_hash":
        hcol = hash_col or date_col
        effective_buckets = max(1, min(hash_buckets, MAX_PREDICATES // max(1, len(windows))))
        for s, e in windows:
            for b in range(effective_buckets):
                predicates.append(
                    f"{date_col} >= DATE '{s}' AND {date_col} < DATE '{e}' "
                    f"AND MOD(ORA_HASH({hcol}, {effective_buckets - 1}), {effective_buckets}) = {b}"
                )
    if len(predicates) > MAX_PREDICATES:
        print(f"[predicates] WARN: truncating {len(predicates)} → {MAX_PREDICATES}")
        predicates = predicates[:MAX_PREDICATES]
    return predicates


def _score_numeric_candidate(c):
    return ((2 if c["column"].endswith("_ID") else 0)
            + (1 if c["nulls"] == 0 else 0)
            + (1 if (c["max"] - c["min"]) > 100_000 else 0))


def _choose_partition_strategy(hint_col, pk_info, date_cols, numeric_candidates, total_gb=0.0):
    base = {"numeric_col": None, "date_col": None, "id_col": None, "hash_col": None}
    if hint_col:
        return {**base, "strategy": "numeric_range", "numeric_col": hint_col}
    pk_cols = pk_info.get("pk_cols", [])
    numeric_col_names = {c["column"] for c in numeric_candidates}
    if len(pk_cols) == 1 and pk_cols[0] in numeric_col_names and total_gb < NUMERIC_RANGE_MAX_GB:
        return {**base, "strategy": "numeric_range", "numeric_col": pk_cols[0]}
    if date_cols:
        not_null = [d for d in date_cols if d.get("nullable") == "N"]
        best_date_col = (not_null[0] if not_null else date_cols[0])["column"]
        id_cands = [c for c in numeric_candidates if c["column"].endswith("_ID")]
        target_windows, _ = _target_parallelism(total_gb)
        gb_per_window = total_gb / max(1, target_windows)
        if id_cands and total_gb >= SMALL_TABLE_GB and gb_per_window >= GB_PER_WINDOW_FOR_ID_RANGE:
            best_id = max(id_cands, key=_score_numeric_candidate)
            return {**base, "strategy": "date_id_range",
                    "date_col": best_date_col, "id_col": best_id["column"]}
        if numeric_candidates and total_gb >= SMALL_TABLE_GB and gb_per_window >= GB_PER_WINDOW_FOR_ID_RANGE:
            best_num = max(numeric_candidates, key=_score_numeric_candidate)
            return {**base, "strategy": "date_id_range",
                    "date_col": best_date_col, "id_col": best_num["column"]}
        hash_col = None
        if id_cands:
            hash_col = max(id_cands, key=_score_numeric_candidate)["column"]
        elif numeric_candidates:
            hash_col = max(numeric_candidates, key=_score_numeric_candidate)["column"]
        else:
            hash_col = best_date_col
        return {**base, "strategy": "date_hash",
                "date_col": best_date_col, "hash_col": hash_col}
    if numeric_candidates:
        best = max(numeric_candidates, key=_score_numeric_candidate)
        return {**base, "strategy": "numeric_range", "numeric_col": best["column"]}
    return {**base, "strategy": "numeric_range"}


def _compute_partition_sizing(stats, strategy_cfg, date_bounds, hash_buckets=8):
    total_gb = _stats_total_gb(stats)
    target_preds, num_workers = _target_parallelism(total_gb)
    worker_type = _choose_worker_type(total_gb, stats["no_stats"])
    strategy = strategy_cfg["strategy"]
    if strategy == "numeric_range":
        return {"total_gb": total_gb, "jdbc_partitions": target_preds,
                "num_workers": num_workers, "worker_type": worker_type,
                "id_splits": None, "effective_hash_buckets": None}
    windows_count = 1
    if date_bounds:
        windows_count = max(1, len(_date_window_ranges(
            date_bounds["min_date"], date_bounds["max_date"], target=target_preds,
        )))
    id_splits = None
    effective_hash_buckets = hash_buckets
    if strategy == "date_id_range":
        id_splits = max(1, min(
            MAX_PREDICATES // windows_count,
            max(1, math.ceil(target_preds / windows_count)),
        ))
    elif strategy == "date_hash":
        per_window = max(1, MAX_PREDICATES // windows_count)
        effective_hash_buckets = max(1, min(hash_buckets, per_window))
    return {"total_gb": total_gb, "jdbc_partitions": None,
            "num_workers": num_workers, "worker_type": worker_type,
            "id_splits": id_splits, "effective_hash_buckets": effective_hash_buckets}


def _maybe_staged_backfill(total_gb, date_bounds, worker_type, num_workers):
    if total_gb < STAGED_BACKFILL_GB or not date_bounds:
        return None
    min_d, max_d = date_bounds["min_date"], date_bounds["max_date"]
    num_chunks = max(2, math.ceil(total_gb / STAGED_BACKFILL_GB))
    total_years = max_d.year - min_d.year + 1
    years_per_chunk = max(1, math.ceil(total_years / num_chunks))
    est_gb_per_chunk = round(total_gb / num_chunks, 2)
    chunks, y = [], min_d.year
    while y <= max_d.year:
        chunk_end_year = min(y + years_per_chunk - 1, max_d.year)
        d_start = min_d if y == min_d.year else _date(y, 1, 1)
        d_end = max_d if chunk_end_year >= max_d.year else _date(chunk_end_year, 12, 31)
        chunks.append({"date_start": str(d_start), "date_end": str(d_end), "est_gb": est_gb_per_chunk})
        y = chunk_end_year + 1
    return {
        "recommended": True,
        "reason": (
            f"{round(total_gb, 1)} GB exceeds single-job cost target "
            f"({STAGED_BACKFILL_GB} GB). Run {len(chunks)} staged backfill jobs."
        ),
        "chunks": chunks,
        "per_chunk": {
            "worker_type": worker_type, "num_workers": num_workers,
            "max_predicates": MAX_PREDICATES,
        },
    }


def _enrich_recommendations(rec, sizing, date_bounds):
    wt, nw = rec.get("worker_type", "G.1X"), rec.get("num_workers", 1)
    total_gb = sizing.get("total_gb", rec.get("total_gb", 0))
    rec["estimated_dpu_capacity"] = int(nw) * DPU_FACTORS.get(wt, 1)
    rec["cost_note"] = COST_NOTE
    if rec.get("jdbc_predicates_count") is None and rec.get("jdbc_predicates"):
        rec["jdbc_predicates_count"] = len(rec["jdbc_predicates"])
    staged = _maybe_staged_backfill(total_gb, date_bounds, wt, nw)
    if staged:
        rec["staged_backfill"] = staged
    return rec


_GLUE_INC_LEAN = json.dumps({"worker_type": "G.1X", "num_workers": 1, "timeout_minutes": 60})
_EXT_INC_LEAN  = json.dumps({
    "jdbc_partition_col": None, "jdbc_lower_bound": None, "jdbc_upper_bound": None,
    "jdbc_partitions": None, "jdbc_predicates": None,
})


def _build_apply_command(pipeline_id, rec):
    """Emit a copy-paste shell command that writes the analyze recommendation
    into the two-profile config shape (extract.initial / extract.incremental,
    glue.initial / glue.incremental). Mirror of pipeline_controller's emitter
    so both analyze paths stay in sync after the SPEA9I migration.

    Falls back gracefully when date_bounds is null (date-strategy with no
    probed range) by emitting just the initial-profile worker sizing.
    Operator can re-run with --date_start/--date_end for predicates.
    """
    url      = f"{API_URL}/pipelines/{pipeline_id}"
    wt, nw   = rec["worker_type"], rec["num_workers"]
    strategy = rec["partition_strategy"]
    glue_init = json.dumps({"worker_type": wt, "num_workers": nw, "timeout_minutes": 240})

    common_setup = (
        f"CURRENT=$(curl -s {url}) && \\\n"
        f"echo $CURRENT | jq \\\n"
        f"  --argjson glue_init {json.dumps(glue_init)} \\\n"
        f"  --argjson glue_inc  {json.dumps(_GLUE_INC_LEAN)} \\\n"
        f"  --argjson ext_inc   {json.dumps(_EXT_INC_LEAN)} \\\n"
    )
    common_del = (
        f"   del(.glue.worker_type, .glue.num_workers, .glue.timeout_minutes,\n"
        f"       .extract.jdbc_partition_col, .extract.jdbc_lower_bound,\n"
        f"       .extract.jdbc_upper_bound, .extract.jdbc_partitions,\n"
        f"       .extract.jdbc_predicates)\n"
        f"  ' | curl -s -X PUT {url} -H 'Content-Type: application/json' -d @-"
    )

    if strategy == "numeric_range" and rec.get("jdbc_partition_col"):
        col, lo, hi, parts = (rec["jdbc_partition_col"], rec["jdbc_lower_bound"],
                               rec["jdbc_upper_bound"], rec["jdbc_partitions"])
        return (
            common_setup +
            f"  '.glue.initial     = ($glue_init | fromjson) |\n"
            f"   .glue.incremental = ($glue_inc  | fromjson) |\n"
            f"   .extract.initial.jdbc_partition_col = \"{col}\" |\n"
            f"   .extract.initial.jdbc_lower_bound   = {lo}    |\n"
            f"   .extract.initial.jdbc_upper_bound   = {hi}    |\n"
            f"   .extract.initial.jdbc_partitions    = {parts} |\n"
            f"   .extract.initial.jdbc_predicates    = null    |\n"
            f"   .extract.incremental = ($ext_inc | fromjson) |\n" +
            common_del
        )

    if rec.get("jdbc_predicates"):
        preds_json = json.dumps(rec["jdbc_predicates"])
        return (
            f"# Step 1 — save predicates to disk (avoids argv-length issues):\n"
            f"python3 -c \"import json; open('/tmp/predicates.json','w').write(json.dumps({preds_json}))\"\n\n"
            f"# Step 2 — apply via API (two-profile shape):\n"
            + common_setup +
            f"  --argjson preds \"$(cat /tmp/predicates.json)\" \\\n"
            f"  '.glue.initial     = ($glue_init | fromjson) |\n"
            f"   .glue.incremental = ($glue_inc  | fromjson) |\n"
            f"   .extract.initial.jdbc_predicates    = $preds |\n"
            f"   .extract.initial.jdbc_partition_col = null   |\n"
            f"   .extract.initial.jdbc_lower_bound   = null   |\n"
            f"   .extract.initial.jdbc_upper_bound   = null   |\n"
            f"   .extract.initial.jdbc_partitions    = null   |\n"
            f"   .extract.incremental = ($ext_inc | fromjson) |\n" +
            common_del
        )

    # Fallback: strategy known (e.g., date_id_range) but no predicates because
    # date_bounds wasn't resolvable. Emit just the initial-profile sizing so
    # the operator can re-run analyze with --date_start/--date_end later or
    # hand-author predicates.
    return (
        f"# Note: date_bounds were not resolved for strategy={strategy}.\n"
        f"# Re-run with --date_start=YYYY-MM-DD --date_end=YYYY-MM-DD\n"
        f"# to generate predicates, or fill extract.initial.jdbc_predicates manually.\n"
        f"# Applying initial-profile worker sizing now:\n"
        + common_setup +
        f"  '.glue.initial     = ($glue_init | fromjson) |\n"
        f"   .glue.incremental = ($glue_inc  | fromjson) |\n"
        f"   .extract.incremental = ($ext_inc | fromjson) |\n" +
        common_del
    )


# ── Oracle queries via Spark JDBC ────────────────────────────────

def _resolve_actual_owner(schema, table_name, jdbc_url, user, password):
    """Resolve actual table owner — handles Oracle EBS synonyms (e.g., APPS → INV)."""
    try:
        rows = jdbc_query(f"""
            SELECT TABLE_OWNER FROM ALL_SYNONYMS
            WHERE SYNONYM_NAME = '{table_name}'
              AND (OWNER = '{schema}' OR OWNER = 'PUBLIC')
              AND ROWNUM = 1
        """, jdbc_url, user, password)
        if rows and rows[0].get("TABLE_OWNER"):
            actual = rows[0]["TABLE_OWNER"]
            print(f"[synonym] {schema}.{table_name} → actual owner: {actual}")
            return actual
    except Exception as e:
        print(f"[WARN] synonym resolution failed: {e}")
    return schema


def fetch_date_cols(schema, table_name, jdbc_url, user, password, owner=None):
    owner = owner or schema
    try:
        rows = jdbc_query(f"""
            SELECT COLUMN_NAME, DATA_TYPE, NULLABLE
            FROM   ALL_TAB_COLUMNS
            WHERE  OWNER = '{owner}' AND TABLE_NAME = '{table_name}'
              AND  DATA_TYPE IN ('DATE', 'TIMESTAMP', 'TIMESTAMP(6)')
            ORDER BY COLUMN_ID
        """, jdbc_url, user, password)
        result = [{"column": r["COLUMN_NAME"], "data_type": r["DATA_TYPE"],
                   "nullable": r.get("NULLABLE", "Y")} for r in rows]
    except Exception as e:
        print(f"[WARN] date_cols query skipped (role-restricted?): {e}")
        result = []
    print(f"[date cols] {[d['column'] for d in result]}")
    return result


def fetch_all_columns(schema, table_name, jdbc_url, user, password, owner=None):
    """Fetch all columns for UI wizard schema review."""
    owner = owner or schema
    try:
        rows = jdbc_query(f"""
            SELECT COLUMN_NAME, DATA_TYPE, NULLABLE
            FROM   ALL_TAB_COLUMNS
            WHERE  OWNER = '{owner}' AND TABLE_NAME = '{table_name}'
            ORDER BY COLUMN_ID
        """, jdbc_url, user, password)
        result = [
            {"name": r["COLUMN_NAME"], "data_type": r["DATA_TYPE"],
             "nullable": r.get("NULLABLE", "Y") == "Y"}
            for r in rows
        ]
    except Exception as e:
        print(f"[WARN] column fetch failed: {e}")
        result = []
    print(f"[columns] fetched {len(result)} columns")
    return result


def fetch_indexes(schema, table_name, jdbc_url, user, password, owner=None):
    """Return the index inventory visible to the connected Oracle user.

    Output shape:
      [
        {"name": "EGO_MTL_SY_ITEMS_EXT_TL#_PK",
         "uniqueness": "UNIQUE",
         "type": "NORMAL",
         "columns": ["EXTENSION_ID", "LANGUAGE"]},
        ...
      ]

    Returns [] silently if the user lacks SELECT on ALL_INDEXES / ALL_IND_COLUMNS
    (rare — SELECT_CATALOG_ROLE covers it). Used to:
      - Mark partition_candidates / date_cols as indexed_as_leading
      - Pick a watermark column with index support
      - Enable INDEX-hint MIN/MAX probes on huge tables (sub-second vs ~1m scan)
    """
    owner = owner or schema
    try:
        rows = jdbc_query(f"""
            SELECT i.INDEX_NAME, i.UNIQUENESS, i.INDEX_TYPE,
                   ic.COLUMN_NAME, ic.COLUMN_POSITION
            FROM   ALL_INDEXES i
            JOIN   ALL_IND_COLUMNS ic
              ON   ic.INDEX_OWNER = i.OWNER
              AND  ic.INDEX_NAME  = i.INDEX_NAME
            WHERE  i.TABLE_OWNER = '{owner}'
              AND  i.TABLE_NAME  = '{table_name}'
              AND  i.STATUS      = 'VALID'
            ORDER  BY i.INDEX_NAME, ic.COLUMN_POSITION
        """, jdbc_url, user, password)
    except Exception as e:
        print(f"[WARN] index fetch failed (role-restricted?): {e}")
        return []

    by_name: dict[str, dict] = {}
    for r in rows:
        nm = r["INDEX_NAME"]
        if nm not in by_name:
            by_name[nm] = {
                "name":       nm,
                "uniqueness": r.get("UNIQUENESS"),
                "type":       r.get("INDEX_TYPE"),
                "columns":    [],
            }
        by_name[nm]["columns"].append(r["COLUMN_NAME"])

    result = list(by_name.values())
    print(f"[indexes] {len(result)} indexes: "
          + ", ".join(f"{ix['name']}({','.join(ix['columns'][:3])}{'...' if len(ix['columns'])>3 else ''})"
                       for ix in result[:6])
          + (" ..." if len(result) > 6 else ""))
    return result


def _index_columns_for(col_name: str, indexes: list) -> tuple[list[str], list[str]]:
    """Return (leading_in, present_in) — names of indexes where col_name is the
    leading column, and names of indexes where col_name appears at any position.
    """
    leading_in = [ix["name"] for ix in indexes
                  if ix["columns"] and ix["columns"][0] == col_name]
    present_in = [ix["name"] for ix in indexes
                  if col_name in ix["columns"]]
    return leading_in, present_in


def probe_date_bounds(schema, table_name, date_col, jdbc_url, user, password,
                       owner=None, indexes=None):
    owner = owner or schema
    indexes = indexes or []

    def _to_date(v):
        if v is None: return None
        if isinstance(v, datetime): return v.date()
        if isinstance(v, _date): return v
        if isinstance(v, str) and v.startswith("Timestamp"):
            try: return datetime.fromisoformat(v.split("'")[1]).date()
            except: pass
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try: return datetime.strptime(str(v), fmt).date()
            except ValueError: continue
        return None

    # 1) Stats-table path. Oracle DATE LOW_VALUE/HIGH_VALUE comes back as raw
    # bytes in the dictionary view (Spark's JDBC sees the underlying RAW(7)
    # representation, which _to_date can't parse). Kept for backward compat
    # when the column happens to round-trip through a string-typed parser.
    try:
        b = jdbc_query(
            f"""SELECT s.LOW_VALUE, s.HIGH_VALUE
               FROM ALL_TAB_COL_STATISTICS s
               WHERE s.OWNER = '{owner}' AND s.TABLE_NAME = '{table_name}'
                 AND s.COLUMN_NAME = '{date_col}'""",
            jdbc_url, user, password)
        if b and b[0].get("LOW_VALUE"):
            min_d, max_d = _to_date(b[0]["LOW_VALUE"]), _to_date(b[0]["HIGH_VALUE"])
            if min_d and max_d:
                print(f"[date bounds] {date_col}: {min_d} – {max_d} (from stats)")
                return {"min_date": min_d, "max_date": max_d, "source": "stats"}
    except Exception as e:
        print(f"[WARN] date bounds from stats failed: {e}")

    # 2) INDEX-hinted MIN/MAX. When the date column is the leading column of
    # an index, Oracle can resolve MIN/MAX as a 2-row index scan (sub-second
    # on 100M+ row tables). Without an index it falls back to a full scan,
    # which is exactly the SPEA9I incident shape — minutes per call, scales
    # linearly with table size. Skip the hint if no leading index exists and
    # go straight to the bounded probe.
    leading_in, _ = _index_columns_for(date_col, indexes)
    if leading_in:
        idx_name = leading_in[0]
        try:
            print(f"[date bounds] using INDEX({idx_name}) hint for {date_col}")
            rows = jdbc_query(
                f"SELECT /*+ INDEX(t {idx_name}) */ "
                f"  MIN({date_col}) AS MIN_D, MAX({date_col}) AS MAX_D "
                f"FROM {schema}.{table_name} t",
                jdbc_url, user, password, timeout_seconds=60)
            if rows and rows[0].get("MIN_D") and rows[0].get("MAX_D"):
                min_d = _to_date(rows[0]["MIN_D"])
                max_d = _to_date(rows[0]["MAX_D"])
                if min_d and max_d:
                    print(f"[date bounds] {date_col}: {min_d} – {max_d} (index-hinted)")
                    return {"min_date": min_d, "max_date": max_d,
                            "source": "index_hint", "index": idx_name}
        except Exception as e:
            print(f"[WARN] index-hinted date bounds probe failed: {e}")

    # 3) Unhinted live probe. Last resort — 60s timeout caps the damage on
    # unindexed columns. Caller can pass --date_start/--date_end to bypass.
    try:
        print(f"[date bounds] falling back to live MIN/MAX probe for {date_col}")
        rows = jdbc_query(
            f"SELECT MIN({date_col}) AS MIN_D, MAX({date_col}) AS MAX_D "
            f"FROM {schema}.{table_name}",
            jdbc_url, user, password, timeout_seconds=60)
        if rows and rows[0].get("MIN_D") and rows[0].get("MAX_D"):
            min_d = _to_date(rows[0]["MIN_D"])
            max_d = _to_date(rows[0]["MAX_D"])
            if min_d and max_d:
                print(f"[date bounds] {date_col}: {min_d} – {max_d} (live probe)")
                return {"min_date": min_d, "max_date": max_d, "source": "live_probe"}
    except Exception as e:
        print(f"[WARN] live date bounds probe failed: {e}")
    return None


# ── Main analysis ─────────────────────────────────────────────────

try:
    SCHEMA, TABLE_NAME = parse_table(TABLE)
    for val in (SCHEMA, TABLE_NAME):
        if not all(c.isalnum() or c == "_" for c in val):
            raise ValueError(f"Invalid identifier: {val}")

    username, password, jdbc_url = resolve_credentials()
    print(f"[DEBUG] JDBC URL (first 80 chars): {jdbc_url[:80]}")

    # Test connectivity
    try:
        test_conn = jdbc_query("SELECT 1 as TEST_VALUE FROM DUAL", jdbc_url, username, password)
        print(f"[DEBUG] Connectivity test passed: {test_conn}")
    except Exception as e:
        print(f"[DEBUG] Connectivity test failed: {e}")
        raise RuntimeError(f"JDBC connection failed: {e}")

    # 1. Table-level stats (fallback if ALL_TABLES has no rows)
    table_rows = jdbc_query(f"""
        SELECT NUM_ROWS, AVG_ROW_LEN,
               TO_CHAR(LAST_ANALYZED, 'YYYY-MM-DD') AS LAST_ANALYZED
        FROM   ALL_TABLES
        WHERE  OWNER = '{SCHEMA}' AND TABLE_NAME = '{TABLE_NAME}'
    """, jdbc_url, username, password)

    print(f"[DEBUG] ALL_TABLES query returned {len(table_rows) if table_rows else 0} rows")

    if table_rows:
        r             = table_rows[0]
        num_rows      = r.get("NUM_ROWS")
        avg_row_len   = r.get("AVG_ROW_LEN")
        last_analyzed_str = r.get("LAST_ANALYZED")
        last_analyzed = (datetime.strptime(last_analyzed_str, "%Y-%m-%d")
                         if last_analyzed_str else None)
        no_stats      = False  # Oracle stats found
    else:
        num_rows = avg_row_len = last_analyzed = None
        no_stats = True  # No Oracle stats — use defaults
        print(f"[DEBUG] Table not found in ALL_TABLES; using defaults for estimation...")
        # Avoid expensive COUNT(*) that hangs on large tables without stats.
        # Use DBA_SEGMENTS to estimate size from allocated space instead.
        try:
            seg_rows = jdbc_query(f"""
                SELECT BYTES FROM DBA_SEGMENTS
                WHERE OWNER = '{SCHEMA}' AND SEGMENT_NAME = '{TABLE_NAME}'
                  AND SEGMENT_TYPE = 'TABLE'
            """, jdbc_url, username, password, timeout_seconds=30)
            if seg_rows and seg_rows[0].get("BYTES"):
                allocated_bytes = int(seg_rows[0]["BYTES"])
                # Estimate rows assuming 500 bytes/row average
                num_rows = max(1000, allocated_bytes // 500)
                avg_row_len = 500
                print(f"[DEBUG] Estimated from DBA_SEGMENTS: {num_rows} rows ({allocated_bytes} bytes)")
            else:
                # Last resort: use safe conservative default for large tables
                num_rows = 1_000_000
                avg_row_len = 500
                print(f"[DEBUG] DBA_SEGMENTS not available; using conservative default (1M rows)")
        except Exception as e:
            # If DBA_SEGMENTS fails, use safe default
            num_rows = 1_000_000
            avg_row_len = 500
            print(f"[DEBUG] DBA_SEGMENTS query failed ({e}); using conservative default")

    if num_rows is None:
        raise RuntimeError(
            f"Could not determine table size for {SCHEMA}.{TABLE_NAME}. "
            f"Table not found in ALL_TABLES and direct query failed."
        )

    num_rows       = int(num_rows or 0)
    avg_row_len    = int(avg_row_len or 500)
    stats_age_days = (datetime.now() - last_analyzed).days if last_analyzed else None
    print(f"[stats] rows={num_rows} avg_row_bytes={avg_row_len} "
          f"stats_age_days={stats_age_days} no_stats={no_stats}")

    stats = {"num_rows": num_rows, "avg_row_len": avg_row_len,
             "stats_age_days": stats_age_days, "no_stats": no_stats}
    total_gb = _stats_total_gb(stats)

    # 1a. Resolve actual owner (handles Oracle EBS synonyms)
    actual_owner = _resolve_actual_owner(SCHEMA, TABLE_NAME, jdbc_url, username, password)

    # 1b. Fetch all columns for UI wizard
    all_columns = fetch_all_columns(SCHEMA, TABLE_NAME, jdbc_url, username, password, owner=actual_owner)

    # 1c. Index inventory — used to mark partition candidates / date cols as
    # indexed, and to enable INDEX-hinted MIN/MAX probes for huge tables.
    indexes = fetch_indexes(SCHEMA, TABLE_NAME, jdbc_url, username, password, owner=actual_owner)

    # 2. Date col detection (skip when caller provided explicit hints)
    has_hints = bool(PARTITION_COL or DATE_COL)
    if has_hints:
        pk_info, date_cols_detected = {"pk_cols": []}, []
    else:
        pk_info            = {"pk_cols": []}   # ALL_CONSTRAINTS role-restricted — skip
        date_cols_detected = fetch_date_cols(SCHEMA, TABLE_NAME, jdbc_url, username, password, owner=actual_owner)

    if DATE_COL and not date_cols_detected:
        date_cols_detected = [{"column": DATE_COL, "data_type": "DATE", "nullable": "N"}]

    # 2a. Enrich date_cols with index info — operator-visible signal for which
    # date column would be index-friendly as a watermark (avoid full-table
    # scans on every incremental tick, the SPEA9I anti-pattern).
    for dc in date_cols_detected:
        leading_in, present_in = _index_columns_for(dc["column"], indexes)
        dc["indexed_as_leading"] = bool(leading_in)
        dc["indexes"]            = present_in

    # 3. Numeric partition candidates
    if DATE_COL and ID_COL:
        candidates = []
        try:
            b = jdbc_query(
                f"""SELECT s.LOW_VALUE, s.HIGH_VALUE
                   FROM ALL_TAB_COL_STATISTICS s
                   WHERE s.OWNER = '{actual_owner}' AND s.TABLE_NAME = '{TABLE_NAME}'
                     AND s.COLUMN_NAME = '{ID_COL}'""",
                jdbc_url, username, password)
            if b and b[0].get("LOW_VALUE"):
                lo = int(float(str(b[0]["LOW_VALUE"]).split()[-1]))
                hi = int(float(str(b[0]["HIGH_VALUE"]).split()[-1]))
                candidates = [{"column": ID_COL, "min": lo, "max": hi, "nulls": 0, "distinct": 0}]
        except Exception as e:
            print(f"[WARN] id_col stats lookup failed: {e}")
    elif PARTITION_COL:
        candidates = []
        try:
            b = jdbc_query(
                f"""SELECT s.LOW_VALUE, s.HIGH_VALUE
                   FROM ALL_TAB_COL_STATISTICS s
                   WHERE s.OWNER = '{actual_owner}' AND s.TABLE_NAME = '{TABLE_NAME}'
                     AND s.COLUMN_NAME = '{PARTITION_COL}'""",
                jdbc_url, username, password)
            if b and b[0].get("LOW_VALUE"):
                lo = int(float(str(b[0]["LOW_VALUE"]).split()[-1]))
                hi = int(float(str(b[0]["HIGH_VALUE"]).split()[-1]))
                candidates = [{"column": PARTITION_COL, "min": lo, "max": hi, "nulls": 0, "distinct": 0}]
        except Exception as e:
            print(f"[WARN] partition_col stats lookup failed: {e}")
    else:
        try:
            col_rows = jdbc_query(f"""
                SELECT s.COLUMN_NAME, s.NUM_NULLS, s.NUM_DISTINCT,
                       UTL_RAW.CAST_TO_NUMBER(s.LOW_VALUE)  AS LOW_VAL,
                       UTL_RAW.CAST_TO_NUMBER(s.HIGH_VALUE) AS HIGH_VAL
                FROM ALL_TAB_COL_STATISTICS s
                JOIN ALL_TAB_COLUMNS c
                    ON  c.OWNER = s.OWNER AND c.TABLE_NAME = s.TABLE_NAME
                    AND c.COLUMN_NAME = s.COLUMN_NAME
                WHERE s.OWNER = '{actual_owner}' AND s.TABLE_NAME = '{TABLE_NAME}'
                  AND c.DATA_TYPE = 'NUMBER' AND c.NULLABLE = 'N'
                  AND s.LOW_VALUE IS NOT NULL AND s.HIGH_VALUE IS NOT NULL
                ORDER BY s.NUM_NULLS ASC, s.NUM_DISTINCT DESC NULLS LAST
            """, jdbc_url, username, password)
            def _safe_int(v):
                if v is None: return None
                try:
                    f = float(v)
                    return None if math.isnan(f) or math.isinf(f) else int(f)
                except (TypeError, ValueError):
                    return None
            candidates = [
                {"column": r["COLUMN_NAME"], "min": _safe_int(r["LOW_VAL"]),
                 "max": _safe_int(r["HIGH_VAL"]), "nulls": int(r.get("NUM_NULLS") or 0),
                 "distinct": int(r.get("NUM_DISTINCT") or 0)}
                for r in col_rows
                if _safe_int(r.get("LOW_VAL")) is not None and _safe_int(r.get("HIGH_VAL")) is not None
            ]
        except Exception as e:
            print(f"[WARN] UTL_RAW candidate stats lookup failed: {e}")
            candidates = []

    # 3a. Enrich numeric candidates with index info — drives sizing choice
    # downstream and informs the operator whether the chosen partition column
    # is backed by an index.
    for c in candidates:
        leading_in, present_in = _index_columns_for(c["column"], indexes)
        c["indexed_as_leading"] = bool(leading_in)
        c["indexes"]            = present_in
    print(f"[candidates] {len(candidates)} numeric partition candidates")

    # 4. Strategy selection + caller overrides
    strategy_cfg = _choose_partition_strategy(
        PARTITION_COL, pk_info, date_cols_detected, candidates, total_gb=total_gb,
    )
    if HASH_COL and strategy_cfg["strategy"] == "date_hash":
        strategy_cfg = {**strategy_cfg, "hash_col": HASH_COL}
    if DATE_COL and HASH_COL and not ID_COL and strategy_cfg["strategy"] != "date_hash":
        strategy_cfg = {**strategy_cfg, "strategy": "date_hash",
                        "date_col": DATE_COL, "hash_col": HASH_COL,
                        "id_col": None, "numeric_col": None}
    if DATE_COL and ID_COL and strategy_cfg["strategy"] != "date_id_range":
        strategy_cfg = {**strategy_cfg, "strategy": "date_id_range",
                        "date_col": DATE_COL, "id_col": ID_COL, "numeric_col": None}

    numeric_col = strategy_cfg.get("numeric_col")
    if (strategy_cfg["strategy"] == "numeric_range" and numeric_col
            and not any(c["column"] == numeric_col for c in candidates)):
        try:
            b = jdbc_query(
                f"""SELECT s.LOW_VALUE, s.HIGH_VALUE
                   FROM ALL_TAB_COL_STATISTICS s
                   WHERE s.OWNER = '{actual_owner}' AND s.TABLE_NAME = '{TABLE_NAME}'
                     AND s.COLUMN_NAME = '{numeric_col}'""",
                jdbc_url, username, password)
            if b and b[0].get("LOW_VALUE"):
                lo = int(float(str(b[0]["LOW_VALUE"]).split()[-1]))
                hi = int(float(str(b[0]["HIGH_VALUE"]).split()[-1]))
                candidates = [{"column": numeric_col, "min": lo, "max": hi, "nulls": 0, "distinct": 0}]
        except Exception as e:
            print(f"[WARN] numeric col stats lookup failed for {numeric_col}: {e}")

    # 5. Date bounds
    date_bounds = None
    if strategy_cfg["strategy"] != "numeric_range" and strategy_cfg.get("date_col"):
        if DATE_START or DATE_END:
            d_start = (datetime.strptime(DATE_START, "%Y-%m-%d").date()
                       if DATE_START else _date(2000, 1, 1))
            d_end   = (datetime.strptime(DATE_END,   "%Y-%m-%d").date()
                       if DATE_END else _date.today())
            date_bounds = {"min_date": d_start, "max_date": d_end}
            print(f"[date bounds] using supplied range: {d_start} – {d_end}")
        else:
            date_bounds = probe_date_bounds(
                SCHEMA, TABLE_NAME, strategy_cfg["date_col"], jdbc_url, username, password,
                owner=actual_owner, indexes=indexes)

    # 6. Sizing + predicates
    sizing     = _compute_partition_sizing(stats, strategy_cfg, date_bounds, HASH_BUCKETS)
    predicates = []
    _id_col    = strategy_cfg.get("id_col")
    id_candidate = None
    if strategy_cfg["strategy"] != "numeric_range" and date_bounds:
        id_candidate = next((c for c in candidates if c["column"] == _id_col), None)

        if id_candidate is None and _id_col and strategy_cfg["strategy"] == "date_id_range":
            try:
                print(f"[id bounds] ID bounds not found in stats, probing live for {_id_col}")
                # Use 60-second timeout for probe queries to prevent hangs on large tables
                rows = jdbc_query(
                    f"SELECT MIN({_id_col}) AS MIN_ID, MAX({_id_col}) AS MAX_ID "
                    f"FROM {SCHEMA}.{TABLE_NAME}",
                    jdbc_url, username, password, timeout_seconds=60)
                if rows and rows[0].get("MIN_ID") is not None:
                    id_min_live = int(rows[0]["MIN_ID"])
                    id_max_live = int(rows[0]["MAX_ID"])
                    id_candidate = {"column": _id_col, "min": id_min_live, "max": id_max_live, "nulls": 0, "distinct": 0}
                    print(f"[id bounds] {_id_col}: {id_min_live} – {id_max_live} (live probe)")
            except Exception as e:
                print(f"[WARN] ID bounds live probe failed for {_id_col}: {e}")

        effective_buckets = sizing.get("effective_hash_buckets") or HASH_BUCKETS
        predicates   = _generate_predicates(
            strategy     = strategy_cfg["strategy"],
            date_col     = strategy_cfg["date_col"],
            date_bounds  = date_bounds,
            total_gb     = sizing["total_gb"],
            id_col       = _id_col,
            id_min       = id_candidate["min"] if id_candidate else None,
            id_max       = id_candidate["max"] if id_candidate else None,
            id_splits    = sizing["id_splits"] or 4,
            hash_col     = strategy_cfg.get("hash_col"),
            hash_buckets = effective_buckets,
        )
        print(f"[predicates] generated {len(predicates)} ({strategy_cfg['strategy']})")

    # 7. Best numeric candidate for numeric_range
    best = None
    if strategy_cfg["strategy"] == "numeric_range" and candidates:
        target = strategy_cfg.get("numeric_col")
        best   = next((c for c in candidates if c["column"] == target), None) \
                 or max(candidates, key=_score_numeric_candidate)

    strategy = strategy_cfg["strategy"]
    rec = {
        "partition_strategy":    strategy,
        "worker_type":           sizing["worker_type"],
        "num_workers":           sizing["num_workers"],
        "jdbc_partitions":       sizing["jdbc_partitions"],
        "jdbc_partition_col":    best["column"] if best else None,
        "jdbc_lower_bound":      best["min"]    if best else None,
        "jdbc_upper_bound":      best["max"]    if best else None,
        "jdbc_predicates":       predicates if predicates else None,
        "jdbc_predicates_count": len(predicates) if predicates else None,
        "partition_date_col":    strategy_cfg.get("date_col"),
        "partition_id_col":      strategy_cfg.get("id_col"),
        "partition_hash_col":    strategy_cfg.get("hash_col"),
        "date_min":              str(date_bounds["min_date"]) if date_bounds else None,
        "date_max":              str(date_bounds["max_date"]) if date_bounds else None,
        "id_min":                id_candidate["min"] if id_candidate and strategy_cfg.get("id_col") else None,
        "id_max":                id_candidate["max"] if id_candidate and strategy_cfg.get("id_col") else None,
        "total_gb":              round(sizing["total_gb"], 2),
    }
    warning = None
    if no_stats:
        warning = (f"No Oracle stats — run DBMS_STATS.gather_table_stats"
                   f"('{SCHEMA}', '{TABLE_NAME}') for accurate sizing.")
    elif stats_age_days is not None and stats_age_days > 7:
        probe_source = date_bounds.get("source") if date_bounds else None
        if probe_source == "live_probe":
            warning = f"Oracle stats are {stats_age_days} days old — bounds via live MIN/MAX probe."
        else:
            warning = f"Oracle stats are {stats_age_days} days old."

    # Watermark-column index check. The SPEA9I incident proximate cause was a
    # date watermark column with no index — every incremental tick did a full
    # table scan. Surface this loudly so the operator either asks the DBA for
    # an index or picks a different watermark column before re-enabling.
    chosen_date = strategy_cfg.get("date_col")
    chosen_id   = strategy_cfg.get("id_col")
    rec["watermark_index_status"] = "unknown"
    if chosen_date and indexes:
        dc = next((d for d in date_cols_detected if d["column"] == chosen_date), None)
        if dc and dc.get("indexed_as_leading"):
            rec["watermark_index_status"] = "indexed_as_leading"
        elif dc and dc.get("indexes"):
            rec["watermark_index_status"] = "indexed_secondary"
        else:
            rec["watermark_index_status"] = "NOT_INDEXED"
            unindexed_warn = (
                f"Date column '{chosen_date}' is NOT indexed. Every incremental "
                f"run will scan the full {round(sizing['total_gb'], 1)} GB / "
                f"{num_rows:,} rows. This was the SPEA9I anti-pattern — ask the "
                f"DBA to add an index, or pick a different watermark column."
            )
            warning = f"{warning} | {unindexed_warn}" if warning else unindexed_warn
    if chosen_id and indexes:
        ic = next((c for c in candidates if c["column"] == chosen_id), None)
        rec["id_col_indexed_as_leading"] = bool(ic and ic.get("indexed_as_leading"))

    if warning:
        rec["warning"] = warning

    _enrich_recommendations(rec, sizing, date_bounds)

    pipeline_id = f"pearson-product-{TABLE_NAME}"
    apply_cmd   = _build_apply_command(pipeline_id, rec)

    # Best numeric candidate for UI min_pk/max_pk output
    best_candidate = None
    if strategy == "numeric_range" and candidates:
        numeric_col = rec.get("jdbc_partition_col")
        best_candidate = next((c for c in candidates if c["column"] == numeric_col), None) or (candidates[0] if candidates else None)

    result = {
        # UI wizard fields (matching AnalyzeResult TypeScript interface)
        "table":         TABLE,
        "row_count":     num_rows,
        "columns":       all_columns,
        "min_pk":        best_candidate["min"] if best_candidate else None,
        "max_pk":        best_candidate["max"] if best_candidate else None,
        # Legacy / analysis fields
        "run_id":               RUN_ID,
        "table_name":           TABLE,
        "analyzed_at":          started_at.isoformat(),
        "status":               "COMPLETED",
        "stats": {
            "row_count":      num_rows,
            "avg_row_bytes":  avg_row_len,
            "total_size_gb":  round(sizing["total_gb"], 2),
            "stats_age_days": stats_age_days,
        },
        "date_cols":            [d["column"] for d in date_cols_detected],
        "date_cols_detail":     date_cols_detected,
        "partition_candidates": candidates,
        "index_inventory":      indexes,
        "recommendations":      rec,
        "apply_command":        apply_cmd,
        "ttl": int(time.time()) + (30 * 86400),
    }
    status = "COMPLETED"
    print(f"[DONE] strategy={strategy}  predicates={len(predicates)}  "
          f"workers={rec['num_workers']}  worker_type={rec['worker_type']}")
    print(f"\n── Apply command ──\n{apply_cmd}\n")

except Exception:
    error_msg = traceback.format_exc()
    print(f"[FATAL]\n{error_msg}")
    result = {
        "run_id":      RUN_ID,
        "table_name":  TABLE,
        "analyzed_at": started_at.isoformat(),
        "status":      "FAILED",
        "error":       error_msg,
        "ttl":         int(time.time()) + (30 * 86400),
    }

finally:
    dynamo.Table(ANALYSIS_TABLE).put_item(Item=to_dynamo_safe(result))
    job.commit()
    spark.stop()

if status != "COMPLETED":
    raise RuntimeError(f"Analysis failed for {TABLE} — see {ANALYSIS_TABLE} / run_id={RUN_ID}")
