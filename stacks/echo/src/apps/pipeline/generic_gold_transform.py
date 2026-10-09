# generic_gold_transform.py
# Single reusable AWS Glue job for the Gold layer — incremental CDC edition.
# Driven entirely by a Gold pipeline config in DynamoDB + a mapping (SQL + YAML)
# stored in S3. Implements ADR-0003:
#
#   maintenance diff (new channels D7 / new attributes D12, silent backfills)
#   → Phase 1: scan every source from the bookmark → affected keys  (ADR-0020)
#   → Phase 2: rebuild full entities for affected keys only          (D1)
#   → per-channel hash diff vs current Gold                          (D4)
#   → append change verdicts to {table}_changes                      (D5, first)
#   → MERGE gated on the canonical hash                              (D3, second)
#   → advance the bookmark in one guarded update                     (D6, last)
#
# Pure logic lives in gold_cdc.py (shipped via --extra-py-files) so it can be
# unit-tested without Spark. See domains/product/qe/test_gold_cdc.py.
#
# Job arguments (beyond Glue's own JOB_NAME / JOB_RUN_ID):
#   --gold_id                 (required) pipeline id in the gold config table
#   --env                     (required) environment token; NO default — #15
#   --rebuild                 "true"/"false"; atomic CREATE OR REPLACE — #10
#   --watermark_lag_minutes   delta-scan overlap, default 15 — #5

import json
import sys
import time
import traceback
from datetime import datetime, timezone
from decimal import Decimal
from urllib.parse import urlparse

import boto3
import yaml
from boto3.dynamodb.conditions import Attr, Key

from awsglue.utils import getResolvedOptions
from awsglue.context import GlueContext
from awsglue.job import Job
from pyspark.context import SparkContext
from pyspark.sql import functions as F
from pyspark.sql.types import StructType

import gold_cdc  # --extra-py-files on Glue; src/apps/pipeline on sys.path in tests
import gold_telemetry  # UIFPE-543: EMF documents, delivered like gold_cdc

# ─────────────────────────────────────────────────────────────────
# STEP 1 — BOOTSTRAP (mirrors Silver job)
# ─────────────────────────────────────────────────────────────────
import argparse as _argparse
_p = _argparse.ArgumentParser(add_help=False)
_p.add_argument("--JOB_NAME",   default="unknown")
_p.add_argument("--JOB_RUN_ID", default="unknown")
# No default for --env: silently falling back to "dev" would point a prod run
# at the dev Glue databases (review finding #15). Terraform sets --env in the
# job's default_arguments, so a missing value means a broken deployment and
# must fail loudly.
_p.add_argument("--env",        default=None)
_p.add_argument("--rebuild",    default="false")
# F3: column-scoped historical repair after a mapping-logic change. Comma
# list of master-schema property names; mutually exclusive with --rebuild.
# Mode 'emit' (default) writes op=REFRESH change-log rows for the channels
# whose hash moved; 'silent' repairs without notifying any channel.
_p.add_argument("--refresh_columns", default="")
_p.add_argument("--refresh_mode",    default="emit")
# ADR-0020 decision 5: the recompute leg INSIDE a scheduled run, for what no
# timestamp scan can see (a date boundary passing, a hard delete). Distinct from
# --refresh_columns on purpose — that argument marks a run as maintenance, which
# would stop this satisfying its schedule slot and suppress the announce.
#
# The list DECLARES which columns the leg exists to repair; it does not restrict
# what gets recomputed. The leg selects Gold entities and Phase 2 rebuilds their
# whole rows from the mapping SQL, which is the cheap path — the SQL produces
# every column anyway, and only rows whose hash moved are merged. Composing a
# partial row against Gold's stored values, as --refresh_columns does, would be
# strictly more work. The declaration is still load-bearing: it is what
# test_derivation_blocks checks so no column is both undetectable and
# unrepaired, and it is what a narrower leg would read if one is ever needed.
_p.add_argument("--recompute_columns", default="")
# Delta-scan overlap: scan from (bookmark - lag). See finding #5 and
# gold_cdc.lagged_watermark for why re-reads are safe.
_p.add_argument("--watermark_lag_minutes", default="15")
_glue_defaults, _ = _p.parse_known_args(sys.argv[1:])

args        = getResolvedOptions(
    sys.argv, ["gold_id", "CONFIG_TABLE", "METRICS_TABLE", "GOLD_WAREHOUSE"]
)
JOB_NAME    = _glue_defaults.JOB_NAME
RUN_ID      = _glue_defaults.JOB_RUN_ID
GOLD_ID     = args["gold_id"]
if not (_glue_defaults.env or "").strip():
    raise ValueError(
        "--env is required and was not supplied (or was empty). Refusing to "
        "default to 'dev': the value selects the Gold/Silver Glue databases "
        "and the warehouse bucket, so guessing it would silently write to the "
        "wrong environment. Terraform sets it in the Glue job's "
        "default_arguments — check the job definition."
    )
ENV         = _glue_defaults.env.strip()
REBUILD     = gold_cdc.parse_bool_arg(_glue_defaults.rebuild, "rebuild")
REFRESH_COLUMNS = gold_cdc.parse_refresh_columns(_glue_defaults.refresh_columns)
REFRESH_MODE    = (_glue_defaults.refresh_mode or "emit").strip().lower()
gold_cdc.validate_refresh_args(REFRESH_COLUMNS, REBUILD, REFRESH_MODE)
RECOMPUTE_COLUMNS = gold_cdc.parse_refresh_columns(
    _glue_defaults.recompute_columns)
# Refused before any AWS call. A rebuild writes every column and announces
# nothing, so a leg inside it is meaningless; --refresh_columns returns before
# detection ever runs, so the two cannot share a run.
if RECOMPUTE_COLUMNS and (REBUILD or REFRESH_COLUMNS):
    raise ValueError(
        "--recompute_columns cannot be combined with "
        f"{'--rebuild' if REBUILD else '--refresh_columns'}: the recompute leg "
        "runs inside a scheduled incremental, after the maintenance pass, "
        "while both of those return before detection. Drop one.")
WATERMARK_LAG_MINUTES = int(_glue_defaults.watermark_lag_minutes)
REGION      = "eu-west-1"
CONFIG_TABLE  = args["CONFIG_TABLE"]
METRICS_TABLE = args["METRICS_TABLE"]

dynamo     = boto3.resource("dynamodb", region_name=REGION)
s3         = boto3.client("s3", region_name=REGION)
glue       = boto3.client("glue", region_name=REGION)
# Modelled DynamoDB exception for the guarded watermark write (finding #6).
CONDITIONAL_CHECK_FAILED = \
    dynamo.meta.client.exceptions.ConditionalCheckFailedException
started_at = datetime.now(timezone.utc)
# Spark-side timestamp for _gold_updated_at / change-log rows: one value per run.
RUN_TS = started_at.strftime("%Y-%m-%d %H:%M:%S")
gold_cdc.validate_sql_literal(RUN_TS, "timestamp", "run timestamp")
gold_cdc.validate_sql_literal(RUN_ID, "run_id", "JOB_RUN_ID")
RUN_TS_SQL = f"TIMESTAMP '{RUN_TS}'"


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
        Key={"gold_id": GOLD_ID, "version": "latest"}
    )
    if "Item" not in resp:
        raise ValueError(f"No gold config found for gold_id={GOLD_ID} version=latest")
    return resp["Item"]


def s3_read_text(s3_uri: str) -> str:
    p = urlparse(s3_uri)
    if p.scheme != "s3":
        raise ValueError(f"expected s3:// uri, got {s3_uri!r}")
    obj = s3.get_object(Bucket=p.netloc, Key=p.path.lstrip("/"))
    return obj["Body"].read().decode("utf-8")


def _write_skipped_metrics():
    completed_at = datetime.now(timezone.utc)
    dynamo.Table(METRICS_TABLE).put_item(Item={
        "job_name":     JOB_NAME,
        "run_id":       RUN_ID,
        "gold_id":      GOLD_ID,
        "status":       "SKIPPED",
        "started_at":   started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
        "duration_sec": 0,
        "ttl":          int(time.time()) + (90 * 86400),
    })
    gold_telemetry.emit(gold_telemetry.run_document(
        ENV, GOLD_ID, "SKIPPED", job_name=JOB_NAME, run_id=RUN_ID,
        now=completed_at))


def write_metrics(run_status: str, affected_keys: int, rows_rebuilt: int,
                  rows_written: int, gold_table: str, error: str = None):
    completed_at = datetime.now(timezone.utc)
    item = {
        "job_name":      JOB_NAME,
        "run_id":        RUN_ID,
        "gold_id":       GOLD_ID,
        "gold_table":    gold_table,
        "status":        run_status,
        "affected_keys": affected_keys,
        "rows_read":     rows_rebuilt,   # kept for UI/metrics compatibility
        "rows_written":  rows_written,
        "error_message": error,
        "started_at":    started_at.isoformat(),
        "completed_at":  completed_at.isoformat(),
        "duration_sec":  int((completed_at - started_at).total_seconds()),
        "ttl":           int(time.time()) + (90 * 86400),
    }
    dynamo.Table(METRICS_TABLE).put_item(Item=to_dynamo_safe(item))
    gold_telemetry.emit(gold_telemetry.run_document(
        ENV, GOLD_ID, run_status, job_name=JOB_NAME, run_id=RUN_ID,
        rows_read=rows_rebuilt, rows_written=rows_written,
        affected_keys=affected_keys, duration_seconds=item["duration_sec"],
        error=error, now=completed_at))
    print(
        f"[METRICS WRITTEN] status={run_status} affected_keys={affected_keys} "
        f"rows_written={rows_written} duration={item['duration_sec']}s"
    )


def validate_output_columns(df, master_schema: dict):
    """Fail loudly if the SQL output columns don't match the master schema
    properties. Framework metadata columns are excluded (ADR-0003)."""
    expected = set(master_schema.get("properties", {}).keys())
    actual   = set(df.columns) - set(gold_cdc.METADATA_COLUMNS)
    missing  = expected - actual
    extra    = actual - expected
    if missing or extra:
        raise RuntimeError(
            f"Mapping output does not conform to master schema for gold_id={GOLD_ID}. "
            f"missing_in_output={sorted(missing)} extra_in_output={sorted(extra)}. "
            f"Either update the mapping SQL or bump the schema version."
        )


def to_physical(df):
    """Rename the mapping-SQL output from logical (camelCase, as master.json
    and the SQL aliases spell it) to PHYSICAL snake_case columns — struct
    members included — right before it meets the Gold table (N8). One cast
    per column renames nested fields positionally without touching values."""
    target = StructType.fromJson(gold_cdc.rename_schema_json(df.schema.jsonValue()))
    return df.select(*[
        F.col(f"`{src.name}`").cast(dst.dataType).alias(dst.name)
        for src, dst in zip(df.schema.fields, target.fields)
    ])


def update_config_registries(cfg: dict, channels: list = None,
                             properties: list = None,
                             seed_cursors: list = None):
    """Persist channel / materialized-property registries after a
    maintenance step (D7/D12). Whole attributes are written (not nested
    paths) so legacy configs without the parent attribute migrate cleanly.
    seed_cursors: channels whose publish cursor is initialized to the run
    start (new channels consume forward-only — D7)."""
    sets, values = [], {}
    if channels is not None:
        ch_block = dict(cfg.get("channels", {}))
        ch_block["registered"] = sorted(channels)
        ch_block.setdefault("bootstrap_mode", "silent")
        sets.append("channels = :ch")
        values[":ch"] = ch_block
    if properties is not None:
        sets.append("materialized_properties = :mp")
        values[":mp"] = sorted(properties)
    if seed_cursors:
        cursors = dict(cfg.get("publish_cursors", {}))
        for ch in seed_cursors:
            cursors.setdefault(ch, started_at.isoformat())
        sets.append("publish_cursors = :pc")
        values[":pc"] = cursors
    if not sets:
        return
    dynamo.Table(CONFIG_TABLE).update_item(
        Key={"gold_id": GOLD_ID, "version": "latest"},
        UpdateExpression="SET " + ", ".join(sets),
        ExpressionAttributeValues=to_dynamo_safe(values),
    )


# ─────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────
def run():
    status         = "FAILED"
    error_msg      = None
    affected_count = 0
    rows_rebuilt   = 0
    rows_written   = 0
    gold_table_lbl = "unknown"
    spark          = None
    cached_dfs     = []

    cfg = load_config()
    print(f"[CONFIG LOADED] gold_id={GOLD_ID}")

    if not cfg.get("enabled", True):
        print(f"[SKIPPED] gold pipeline {GOLD_ID} is disabled")
        _write_skipped_metrics()
        return

    # ─── Unpack config ────────────────────────────────────────────
    mapping_cfg   = cfg["mapping"]
    schema_cfg    = cfg["schema"]
    target_cfg    = cfg["target"]
    sources_cfg   = cfg["sources"]
    glue_cfg      = cfg.get("glue", {})
    channels_cfg  = cfg.get("channels", {})
    registered    = list(channels_cfg.get("registered", []))
    materialized  = list(cfg.get("materialized_properties", []))
    bootstrap_mode = channels_cfg.get("bootstrap_mode", "silent")
    backfill_mode  = cfg.get("backfill_mode", "silent")
    max_affected_fraction = float(glue_cfg.get("max_affected_fraction", 0.5))

    # {env} tokens keep one Git artifact valid for every environment —
    # EDS Glue databases are env-qualified (uif_echo_dev_gold_product).
    gold_db          = gold_cdc.resolve_env(target_cfg["database"], ENV)
    gold_tbl         = target_cfg["table"]
    gold_s3_path     = target_cfg["s3_path"]
    primary_key_cols = target_cfg["primary_key_cols"]
    gold_table_lbl   = f"{gold_db}.{gold_tbl}"

    if not primary_key_cols:
        raise ValueError(
            f"target.primary_key_cols is empty/missing for {GOLD_ID} — refusing to run"
        )

    # ─── Load mapping + master schema from S3 ─────────────────────
    print(f"[LOAD MAPPING] manifest={mapping_cfg['manifest_s3_path']}")
    manifest      = yaml.safe_load(s3_read_text(mapping_cfg["manifest_s3_path"]))
    sql_text      = s3_read_text(mapping_cfg["sql_s3_path"])
    # config-supplied {token} values (per-env URL bases etc.); the manifest
    # declares which tokens its SQL cannot run without
    sql_text      = gold_cdc.apply_sql_vars(
        sql_text, cfg.get("sql_vars") or {},
        required=manifest.get("required_sql_vars") or [])
    master_schema = json.loads(s3_read_text(schema_cfg["master_s3_path"]))
    column_types  = manifest.get("column_types") or {}
    print(f"[LOAD SCHEMA] version={schema_cfg.get('version')} "
          f"properties={len(master_schema.get('properties', {}))}")
    # Declared placeholder values (UIFPE-268) become NULL on every Silver view
    # the mapping SQL reads — see source_view() below.
    null_sentinels = gold_cdc.validate_null_sentinels(manifest)
    if null_sentinels:
        print(f"[NULL SENTINELS] string={null_sentinels['string']} "
              f"numeric={null_sentinels['numeric']} "
              f"exclude={null_sentinels['exclude_columns']}")

    # key_mapping lives in the manifest (Git, atomic with the SQL — ADR-3);
    # config `sources` carries silver_table/watermark_col. Join them by alias.
    manifest_sources = {s["alias"]: s for s in manifest.get("sources", [])}
    sources = []
    for src in sources_cfg:
        m = manifest_sources.get(src["alias"], {})
        merged = dict(src)
        merged["silver_table"] = gold_cdc.resolve_env(merged["silver_table"], ENV)
        merged.setdefault("key_mapping", m.get("key_mapping"))
        merged.setdefault("sot_pk_mapping", m.get("sot_pk_mapping"))
        # lookup_only is a manifest property (it describes the TABLE, not the
        # env), so it travels with key_mapping rather than with the pipeline
        # config. Phase 1 skips these; register_full_views still registers
        # them so the mapping SQL can join them.
        merged.setdefault("lookup_only", m.get("lookup_only", False))
        sources.append(merged)

    gold_cdc.validate_key_mappings(sources, primary_key_cols)
    gold_cdc.validate_array_sort_keys(master_schema, manifest)
    gold_cdc.validate_nested_channels(master_schema)

    channels      = gold_cdc.discover_channels(master_schema)
    hash_map_expr = gold_cdc.build_hash_map_expr(master_schema, channels)
    driving       = next(s for s in sources if s.get("is_source_of_truth"))

    # ─── Snapshot-gate state (F1): capture BEFORE the first Silver read ──
    # Every code path below reads Silver AFTER this point (maintenance
    # backfill registers views, Phase 1 reads directly, CTAS registers views)
    # — capturing here guarantees the stored map is never NEWER than the data
    # this run actually read. That property is what makes it safe to persist
    # the map even on the no-change path: a commit that lands between this
    # capture and the reads shows up as a map mismatch next tick and gets
    # dispatched. Capture failures degrade to "no entry" — the dispatcher
    # treats a missing alias as "must dispatch", so the gate fails open.
    silver_snapshots = {}
    for src in sources:
        try:
            db, tbl = src["silver_table"].split(".", 1)
            meta = glue.get_table(DatabaseName=db, Name=tbl)["Table"]
            loc = (meta.get("Parameters") or {}).get("metadata_location")
            if loc:
                silver_snapshots[src["alias"]] = loc
        except Exception as e:
            print(f"[SNAPSHOT CAPTURE SKIPPED] alias={src['alias']} "
                  f"({type(e).__name__}: {e}) — gate will dispatch this "
                  f"source unconditionally until a run captures it")
    print(f"[SNAPSHOTS CAPTURED] {len(silver_snapshots)}/{len(sources)} sources")

    # ─── Init Spark + Iceberg ─────────────────────────────────────
    sc      = SparkContext()
    glueCtx = GlueContext(sc)
    spark   = glueCtx.spark_session
    job     = Job(glueCtx)
    job.init(JOB_NAME, args)

    spark.sparkContext.setLogLevel("WARN")
    spark.conf.set("spark.sql.shuffle.partitions",
                   str(glue_cfg.get("shuffle_partitions", 8)))
    spark.conf.set("spark.hadoop.fs.s3a.endpoint.region", REGION)

    spark.conf.set("spark.sql.catalog.glue_catalog",
        "org.apache.iceberg.spark.SparkCatalog")
    spark.conf.set("spark.sql.catalog.glue_catalog.catalog-impl",
        "org.apache.iceberg.aws.glue.GlueCatalog")
    spark.conf.set("spark.sql.catalog.glue_catalog.io-impl",
        "org.apache.iceberg.aws.s3.S3FileIO")
    # Iceberg warehouse root, passed by Terraform from the stack's own bucket
    # (Echo's name-drift rule: never derived from the environment name).
    # Every table passes an explicit LOCATION, so this only names the default.
    spark.conf.set("spark.sql.catalog.glue_catalog.warehouse",
        args["GOLD_WAREHOUSE"])

    print("[SPARK + ICEBERG INITIALISED]")

    catalog_db  = f"glue_catalog.`{gold_db}`"
    table_full  = f"{catalog_db}.`{gold_tbl}`"
    changes_full = f"{catalog_db}.`{gold_cdc.changes_table_name(gold_tbl)}`"

    registered_views = {}

    def source_view(src: dict):
        """The Silver table as the mapping SQL should see it. With
        null_sentinels declared, every string / numeric value column is
        re-projected so the declared placeholders read as NULL (UIFPE-268);
        keys, discriminators and the watermark column pass through untouched.
        Phase 1 key detection reads the tables directly and is unaffected."""
        df = spark.table(gold_cdc.qualify_table(src["silver_table"]))
        if not null_sentinels:
            return df
        fields = [(f.name, f.dataType.simpleString()) for f in df.schema.fields]
        # key_mapping / sot_pk_mapping / sot_filter columns stay raw: a
        # cleaned key would hide the row from Phase 2 and from reconcile.
        protected = gold_cdc.source_protected_columns(src)
        exprs, cleaned = gold_cdc.sentinel_select_exprs(
            fields, null_sentinels, protected=protected, alias=src["alias"])
        print(f"[NULL SENTINELS] alias={src['alias']} "
              f"cleaned_columns={len(cleaned)}/{len(fields)} "
              f"protected={protected}")
        return df.selectExpr(*exprs)

    def register_full_views(exclude: str = None):
        """Register every Silver source as an UNFILTERED temp view under its
        alias (Phase 2 reads complete tables — the filtered-join bug fix).
        The DataFrames are retained so the initial-materialization watermark
        can be derived from the same registered relations the mapping SQL
        consumes, rather than from a second table read (finding #4)."""
        for src in sources:
            if src["alias"] == exclude:
                continue
            df = source_view(src)
            df.createOrReplaceTempView(src["alias"])
            registered_views[src["alias"]] = df

    def add_metadata_cols(df):
        return (
            df.withColumn("_channel_hashes", F.expr(hash_map_expr))
              .withColumn("_gold_updated_at", F.expr(RUN_TS_SQL))
              .withColumn("_gold_run_id", F.lit(RUN_ID))
        )

    def migrate_physical_names(tbl_full: str, props: dict, types: dict):
        """Move a table written under logical column names to physical ones
        (N8) — Iceberg RENAME COLUMN is metadata-only, so no rebuild. Returns
        True when anything was renamed."""
        try:
            live = spark.table(tbl_full).schema.jsonValue()
        except Exception:
            return False
        stmts = gold_cdc.physical_rename_ddls(tbl_full, {"properties": props}, types, live)
        for stmt in stmts:
            print(f"[PHYSICAL NAMES] {stmt}")
            spark.sql(stmt)
        return bool(stmts)

    def migrate_changes_log():
        """A change log written under logical key names, or under a key the
        master has since renamed, is renamed in place (metadata-only)."""
        pk_props = {c: master_schema["properties"][c] for c in primary_key_cols}
        migrate_physical_names(changes_full, pk_props, {})
        try:
            live_cols = spark.table(changes_full).schema.names
        except Exception:
            return  # no change log yet
        for stmt in gold_cdc.changes_key_rename_ddls(
                changes_full, primary_key_cols, live_cols):
            print(f"[CHANGE LOG KEY] {stmt}")
            spark.sql(stmt)

    def ensure_changes_table():
        pk_types = {
            c: gold_cdc.json_type_to_spark(
                c, master_schema["properties"][c], column_types)
            for c in primary_key_cols
        }
        migrate_changes_log()
        spark.sql(gold_cdc.build_changes_table_ddl(
            catalog_db, gold_tbl, primary_key_cols, pk_types,
            gold_cdc.changes_table_location(gold_s3_path)))

    def append_change_log(diff_view: str):
        """D6 step 1 — persist verdicts BEFORE the MERGE (at-least-once)."""
        pk_select = ", ".join(f"`{gold_cdc.physical_name(c)}`" for c in primary_key_cols)
        spark.sql(f"""
            INSERT INTO {changes_full}
            SELECT {pk_select}, `__op`, `__changed_channels`,
                   '{RUN_ID}', {RUN_TS_SQL}, to_date({RUN_TS_SQL})
            FROM {diff_view}
        """)

    try:
        spark.sql(f"CREATE DATABASE IF NOT EXISTS {catalog_db}")
        try:
            spark.sql(f"SELECT 1 FROM {table_full} LIMIT 0")
            table_exists = True
        except Exception:
            table_exists = False

        # ═══ F3 — --refresh_columns: column-scoped historical repair ═══
        # Recompute the named columns (plus channel hashes) for every row,
        # after a mapping-logic change to EXISTING attributes — the case the
        # maintenance diff cannot see (it tracks column NAMES, not logic).
        if REFRESH_COLUMNS:
            if not table_exists:
                raise ValueError(
                    f"--refresh_columns requires an existing Gold table "
                    f"({gold_db}.{gold_tbl} not found) — a first load is a "
                    f"plain run, not a refresh.")
            gold_cdc.validate_refresh_columns(
                REFRESH_COLUMNS, master_schema, primary_key_cols)

            # The composite row reads un-named columns off the live table, so
            # a table predating a master property must refuse readably rather
            # than die on UNRESOLVED_COLUMN.
            migrate_physical_names(table_full, master_schema["properties"], column_types)
            live_cols = {f.name for f in spark.table(table_full).schema.fields}
            absent = [p for p in master_schema.get("properties", {})
                      if gold_cdc.physical_name(p) not in live_cols]
            if absent:
                raise RuntimeError(
                    f"[REFRESH ABORTED] the Gold table is missing master "
                    f"column(s) {absent} — a refresh composes its row from "
                    f"the stored table and cannot invent them. Let the "
                    f"incremental run (its maintenance pass adds new columns) "
                    f"or run --rebuild, then refresh.")

            # The same hazard one level down, which the check above cannot see
            # because the COLUMN exists and only its struct is stale. This
            # branch returns before the maintenance pass that evolves structs
            # (UIFPE-157), so a widened ARRAY<STRUCT<…>> element would MERGE
            # the new field set into the stored one. Refuse for the same
            # reason, and name the same remedy.
            nested_pending, nested_refused = (
                gold_cdc.diff_nested_field_additions(
                    column_types, spark.table(table_full).schema.jsonValue()))
            if nested_pending or nested_refused:
                paths = [a["path"] for a in nested_pending]
                raise RuntimeError(
                    f"[REFRESH ABORTED] the Gold table's nested struct(s) "
                    f"predate the manifest: pending additions {paths}, "
                    f"non-additive differences {nested_refused}. A refresh "
                    f"does not run the maintenance DDL. Let the incremental "
                    f"run (it evolves the struct) or run --rebuild, then "
                    f"refresh.")

            # COMPOSITE ROW (UIFPE-269): named columns fresh from the mapping
            # SQL, the rest as Gold stores them, hashed over THAT row — so a
            # hash can never claim a value the row does not hold. Replaced the
            # pending-delta guard, which refused every daily refresh once a
            # lookup_only source had one commit. See build_refresh_row_sql.
            register_full_views()
            fresh = spark.sql(sql_text)
            validate_output_columns(fresh, master_schema)
            to_physical(fresh).createOrReplaceTempView("gold_refresh_fresh")
            spark.sql(gold_cdc.build_refresh_row_sql(
                table_full, primary_key_cols, "gold_refresh_fresh",
                REFRESH_COLUMNS, master_schema, channels, RUN_TS_SQL, RUN_ID)
            ).createOrReplaceTempView("gold_refresh")

            # Hash diff vs stored Gold. Only the named columns can differ
            # from the stored row, so __changed_channels is precisely
            # "channels affected by this refresh". INNER join: a refresh
            # never inserts (a key absent from Gold has no stored row to
            # compose with) and never deletes (rows leaving the domain are
            # reconcile's job).
            changed_expr = gold_cdc.build_changed_channels_expr(
                channels, "u", "g")
            pk_join = gold_cdc.build_pk_conditions(
                primary_key_cols, "g", "u")
            diff = spark.sql(f"""
                SELECT u.*, '{gold_cdc.REFRESH_OP}' AS `__op`,
                       {changed_expr} AS `__changed_channels`
                FROM gold_refresh u
                JOIN {table_full} g ON {pk_join}
            """).filter(F.expr("size(`__changed_channels`) > 0"))
            diff.cache()
            cached_dfs.append(diff)
            n = diff.count()
            print(f"[REFRESH DIFF] columns={REFRESH_COLUMNS} "
                  f"mode={REFRESH_MODE} rows_changed={n}")

            if n > 0:
                diff.createOrReplaceTempView("refresh_diff")
                if REFRESH_MODE == "emit":
                    # D6 ordering holds: change log before MERGE.
                    ensure_changes_table()
                    append_change_log("refresh_diff")
                    print(f"[CHANGE LOG APPENDED] op={gold_cdc.REFRESH_OP}")
                spark.sql(gold_cdc.build_refresh_merge_sql(
                    table_full, primary_key_cols, "refresh_diff",
                    REFRESH_COLUMNS,
                    bump_metadata=(REFRESH_MODE == "emit")))
                print(f"[ICEBERG] refreshed {n} rows, "
                      f"{len(REFRESH_COLUMNS)} column(s), into {table_full}")

            # Watermarks and snapshot state are deliberately NOT touched: a
            # refresh is orthogonal to CDC progress. It reads Silver as it is
            # now, but it persists only the named columns and hashes the
            # composite row, so the incremental's view of what it still owes
            # must not move.
            affected_count = rows_rebuilt = rows_written = n
            status = "SUCCEEDED"
            job.commit()
            return

        # Review finding #10: --rebuild used to DROP TABLE … PURGE and then
        # CTAS. Between those two statements the table did not exist — every
        # reader got "table not found", and a CTAS failure left the pipeline
        # with NO Gold table and its data files already purged, recoverable
        # only by a full re-run. Iceberg on Glue 5 / Spark 3.5 supports
        # atomic REPLACE, so the rebuild is now a single
        # CREATE OR REPLACE TABLE … AS SELECT below: readers see the old
        # snapshot until the new one commits, and a failure is a no-op.
        #
        # Two deliberate consequences of REPLACE over DROP … PURGE:
        #   - the table keeps its identity, so the Glue table's grants, its
        #     LOCATION and its TBLPROPERTIES survive (we still restate
        #     format-version=2 and LOCATION so a fresh CREATE is identical);
        #   - old data files are NOT purged immediately. They become
        #     unreferenced snapshots, which is what makes rollback possible;
        #     storage is reclaimed by Iceberg expire_snapshots maintenance
        #     rather than by this job.
        rebuild_replace = REBUILD and table_exists
        if rebuild_replace:
            print(f"[REBUILD] --rebuild=true: atomically replacing "
                  f"{table_full} via CREATE OR REPLACE TABLE … AS SELECT")

        # ═══ MAINTENANCE DIFF (D7 / D12 / UIFPE-157) — before incremental work ═══
        new_channels = gold_cdc.diff_new_channels(master_schema, registered)
        new_props    = gold_cdc.diff_new_properties(master_schema, materialized)

        # Nested (struct) widening is invisible to the property diff — it is
        # a type change on an EXISTING column, so it is detected against the
        # live table schema instead (UIFPE-157: fulfillmentOrgDetails 6 → 8
        # fields failed the MERGE with EXTRA_STRUCT_FIELDS and needed a full
        # --rebuild). Additive nested fields evolve here; anything
        # non-additive refuses BEFORE any write, naming --rebuild.
        nested_adds, nested_refusals = [], []
        if table_exists and not REBUILD:
            # tables written before the physical-name rule are renamed first
            # (metadata-only), so every diff below compares physical to physical;
            # the change log moves with it so consumers joining on the key never
            # see two spellings
            migrate_physical_names(table_full, master_schema["properties"], column_types)
            migrate_changes_log()
            live_schema_json = spark.table(table_full).schema.jsonValue()
            nested_adds, nested_refusals = gold_cdc.diff_nested_field_additions(
                column_types, live_schema_json)
            if nested_refusals:
                raise RuntimeError(
                    "[AUTO-DDL] non-additive nested type change(s) — the "
                    "maintenance path only ADDS nested fields; run a manual "
                    "--rebuild after confirming the change is intended: "
                    + "; ".join(nested_refusals))

        # Skipped on --rebuild: the REPLACE writes every column and every hash
        # from scratch, and the create path re-registers channels/properties.
        deferred_backfill = None
        if table_exists and not REBUILD and (new_props or new_channels
                                             or nested_adds):
            existing_cols = {f["name"] for f in live_schema_json["fields"]}
            cols_to_add   = [p for p in new_props
                             if gold_cdc.physical_name(p) not in existing_cols]
            meta_to_add   = [m for m in gold_cdc.METADATA_COLUMNS
                             if m not in existing_cols]
            nested_parents = sorted({a["column"] for a in nested_adds})
            neecho_hash_backfill = bool(cols_to_add or meta_to_add
                                        or new_channels or nested_adds)

            # Auto-DDL (D12) — new business columns, each positioned BEFORE
            # the metadata columns so those stay last (UIFPE-268: a plain
            # ADD COLUMNS appends, which put `categories` behind them).
            live_order = [f["name"] for f in live_schema_json["fields"]]
            for stmt in gold_cdc.build_add_column_ddls(
                    table_full,
                    [(gold_cdc.physical_name(p),
                      gold_cdc.physical_ddl(gold_cdc.json_type_to_spark(
                          p, master_schema["properties"][p], column_types)))
                     for p in cols_to_add],
                    live_order):
                print(f"[AUTO-DDL] {stmt}")
                spark.sql(stmt)
            # Metadata migration for pre-ADR-0003 tables (first run adds
            # _channel_hashes etc.) — these DO belong at the end.
            meta_types = {
                "_channel_hashes":  "MAP<STRING, STRING>",
                "_gold_updated_at": "TIMESTAMP",
                "_gold_run_id":     "STRING",
            }
            meta_specs = [f"`{m}` {meta_types[m]}" for m in meta_to_add]
            if meta_specs:
                print(f"[AUTO-DDL] ALTER TABLE ADD COLUMNS: {meta_specs}")
                spark.sql(
                    f"ALTER TABLE {table_full} ADD COLUMNS ({', '.join(meta_specs)})")

            # Nested field additions (UIFPE-157) — Iceberg evolves structs
            # natively via dotted paths ('parent.child', 'arr.element.child').
            # One statement per field so a failure names the exact spec.
            for a in nested_adds:
                print(f"[AUTO-DDL] ALTER TABLE ADD COLUMN (nested): "
                      f"{a['path']} {a['ddl']}")
                spark.sql(f"ALTER TABLE {table_full} "
                          f"ADD COLUMN {a['path']} {a['ddl']}")

            # The backfill MERGE runs DEFERRED, after the incremental phases
            # and the watermark advance — never here. Run before them, its
            # hash recompute is computed over CURRENT Silver while Gold still
            # holds pre-delta values, so Phase 2's hash gate later sees
            # "no change" for every pending update and drops it permanently
            # (the refresh path's delta guard names this exact hazard; the
            # daily cadence makes a full day of updates the stake). The DDL
            # above still runs first — the incremental MERGE needs the new
            # shape. A crash between the incremental and this backfill leaves
            # NULL new columns + stale-spec hashes on untouched rows; the
            # repair is `--refresh_columns <parent>`, same as ever.
            def run_maintenance_backfill():
                # Full-key rebuild → silent backfill MERGE: sets new columns +
                # recomputes ALL hashes; deliberately never touches
                # _gold_updated_at and writes no change-log rows (D7/D12).
                # Hash recompute is mandatory — stale hashes would flood the
                # next incremental run with false positives.
                print(f"[BACKFILL] rebuilding all entities "
                      f"(new_props={cols_to_add} new_channels={new_channels} "
                      f"meta_migration={meta_to_add} "
                      f"nested={[a['path'] for a in nested_adds]})")
                register_full_views()
                backfill = spark.sql(sql_text)
                validate_output_columns(backfill, master_schema)
                backfill = to_physical(backfill).withColumn(
                    "_channel_hashes", F.expr(hash_map_expr))
                backfill.createOrReplaceTempView("gold_backfill")

                # nested_parents are live (physical) column names already
                set_cols = [f"t.`{c}` = s.`{c}`" for c in
                            [gold_cdc.physical_name(p) for p in cols_to_add] + nested_parents]
                set_cols.append("t.`_channel_hashes` = s.`_channel_hashes`")
                if backfill_mode == "emit" and (cols_to_add or nested_adds):
                    # emit (D12): change-log rows + timestamp bump, only where
                    # the new column(s) / nested field(s) actually carry a value.
                    ensure_changes_table()
                    non_null_parts = [f"s.`{gold_cdc.physical_name(p)}` IS NOT NULL"
                                      for p in cols_to_add]
                    for a in nested_adds:
                        segs = a["path"].split(".")
                        if "element" not in segs:
                            non_null_parts.append(
                                "s." + ".".join(f"`{sg}`" for sg in segs)
                                + " IS NOT NULL")
                        elif len(segs) == 3 and segs[1] == "element":
                            non_null_parts.append(
                                f"exists(s.`{segs[0]}`, "
                                f"x -> x.`{segs[2]}` IS NOT NULL)")
                        else:  # deeper array nesting — parent presence
                            non_null_parts.append(
                                f"s.`{segs[0]}` IS NOT NULL")
                    non_null = " OR ".join(non_null_parts)
                    emit_channels = sorted(
                        {ch for p in cols_to_add
                         for ch in master_schema["properties"][p].get("x-channels", [])}
                        | {ch for a in nested_adds
                           for ch in gold_cdc.nested_field_channels(
                               master_schema, a["column"], a["field"])}
                        | {gold_cdc.CANONICAL})
                    ch_array = ", ".join(
                        f"'{c}'" for c in
                        gold_cdc.validate_sql_literals(emit_channels, "channel"))
                    pk_select = ", ".join(f"s.`{gold_cdc.physical_name(c)}`"
                                          for c in primary_key_cols)
                    pk_join = gold_cdc.build_pk_conditions(
                        primary_key_cols, "t", "s")
                    spark.sql(f"""
                        INSERT INTO {changes_full}
                        SELECT {pk_select}, 'BACKFILL', array({ch_array}),
                               '{RUN_ID}', {RUN_TS_SQL}, to_date({RUN_TS_SQL})
                        FROM gold_backfill s
                        WHERE ({non_null})
                          AND EXISTS (SELECT 1 FROM {table_full} t WHERE {pk_join})
                    """)
                    spark.sql(f"""
                        MERGE INTO {table_full} t
                        USING gold_backfill s
                        ON {gold_cdc.build_pk_conditions(primary_key_cols)}
                        WHEN MATCHED AND ({non_null}) THEN UPDATE SET
                            {', '.join(set_cols)},
                            t.`_gold_updated_at` = {RUN_TS_SQL},
                            t.`_gold_run_id` = '{RUN_ID}'
                        WHEN MATCHED THEN UPDATE SET {', '.join(set_cols)}
                    """)
                else:
                    spark.sql(f"""
                        MERGE INTO {table_full} t
                        USING gold_backfill s
                        ON {gold_cdc.build_pk_conditions(primary_key_cols)}
                        WHEN MATCHED THEN UPDATE SET {', '.join(set_cols)}
                    """)
                print(f"[BACKFILL DONE] mode={backfill_mode}")

            if neecho_hash_backfill:
                deferred_backfill = run_maintenance_backfill

            update_config_registries(
                cfg,
                channels=channels,
                properties=list(master_schema["properties"].keys()),
                seed_cursors=new_channels,
            )
            registered   = channels
            materialized = list(master_schema["properties"].keys())

        # ═══ INITIAL MATERIALIZATION / REBUILD (CTAS / atomic RTAS) ═══
        if not table_exists or rebuild_replace:
            create_stmt = ("CREATE OR REPLACE TABLE" if rebuild_replace
                           else "CREATE TABLE")
            print(f"[ICEBERG] {create_stmt} {table_full} at {gold_s3_path} "
                  f"(full materialization — change-log silent by design)")
            register_full_views()

            # ADR-0020 decision 3: a full load stores its OWN START as the
            # bookmark, so the next incremental begins where this one began
            # and nothing Silver wrote during it is skipped.

            initial = spark.sql(sql_text)
            validate_output_columns(initial, master_schema)
            initial = add_metadata_cols(to_physical(initial))
            initial.cache()
            cached_dfs.append(initial)
            rows_rebuilt = rows_written = initial.count()
            initial.createOrReplaceTempView("gold_initial")
            spark.sql(f"""
                {create_stmt} {table_full}
                USING iceberg
                LOCATION '{gold_s3_path}'
                TBLPROPERTIES ('format-version' = '2')
                AS SELECT * FROM gold_initial
            """)
            ensure_changes_table()
            # A rebuild recomputes every column from current Silver, which
            # subsumes the leg — reset its clock so the next run does not
            # immediately repeat the work.
            _advance_bookmark(started_at.isoformat(), silver_snapshots,
                              recompute_at=started_at.isoformat())
            update_config_registries(
                cfg,
                channels=channels,
                properties=list(master_schema["properties"].keys()),
                seed_cursors=channels,
            )
            status = "SUCCEEDED"
            job.commit()
            return

        # ═══ PHASE 1 — every source scans from the ONE bookmark (ADR-0020) ═══
        # The bookmark is the start of the last run that processed changes,
        # never a value derived from source data. Finding #5: scan from
        # (bookmark - lag) with `>`, so a row stamped just before that start
        # but committed after it is re-read; Phase 2's hash gate drops
        # anything that did not actually move.
        stored_bookmark = gold_cdc.resolve_bookmark(cfg.get("watermark"))
        bookmark = stored_bookmark or _seed_bookmark()
        scan_from = gold_cdc.lagged_watermark(bookmark, WATERMARK_LAG_MINUTES)
        print(f"[BOOKMARK] {bookmark} scan_from>{scan_from} "
              f"(lag={WATERMARK_LAG_MINUTES}m)")
        stored_snaps = gold_cdc.stored_silver_snapshots(
            cfg.get("watermark"), gate=cfg.get("snapshot_gate", True))
        keysets = []
        for src in sources:
            alias  = src["alias"]
            if src.get("lookup_only"):
                # No Gold PK column on this table => no honest projection from
                # a changed row to affected Gold keys, so there is nothing to
                # scan for. It IS registered FULL in Phase 2 below, and its
                # denormalized values move on the next --refresh_columns run.
                print(f"[PHASE 1] alias={alias} SKIPPED (lookup_only) — "
                      f"registered FULL for the mapping SQL, contributes no "
                      f"affected keys")
                continue
            if gold_cdc.source_snapshot_unchanged(
                    stored_snaps, silver_snapshots, alias):
                # no Silver commits since the last successful run — a scan
                # could only re-detect lag-window rows (UIFPE-256)
                print(f"[PHASE 1] alias={alias} SKIPPED (snapshot unchanged)")
                continue
            wm_col = src.get("watermark_col", "_silver_updated_at")
            changed = spark.table(
                gold_cdc.qualify_table(src["silver_table"])).filter(
                F.col(wm_col) > F.lit(scan_from).cast("timestamp"))
            changed.cache()
            cached_dfs.append(changed)
            cnt = changed.count()
            print(f"[PHASE 1] alias={alias} scan_from>{scan_from} "
                  f"changed_rows={cnt}")
            if cnt == 0:
                continue
            key_cols = sorted(src["key_mapping"].keys())
            keys = changed.selectExpr(*[
                f"{expr} AS `{col}`"
                for col, expr in sorted(src["key_mapping"].items())
            ]).distinct()
            view = f"ak_{alias}"
            keys.createOrReplaceTempView(view)
            keysets.append((view, key_cols))

        # ═══ ADR-0020 decision 5 — the recompute leg ═══
        # Appended AFTER the source loop so no per-source `continue`
        # (lookup_only, unchanged snapshot, zero rows) can skip it, and BEFORE
        # the no-keysets return so the snapshot gate cannot either. Keys come
        # off GOLD: those are the entities that exist and can be repaired, and
        # reading the whole PK means no fan-out. Phase 2's filter is a
        # disjunction of EXISTS, so an entity also selected by detection is
        # rebuilt ONCE — no dedupe needed here.
        if RECOMPUTE_COLUMNS:
            unbound = [c for c in primary_key_cols
                       if c not in driving["key_mapping"]]
            if unbound:
                raise ValueError(
                    f"recompute leg cannot bind {unbound}: the driving source "
                    f"{driving['alias']!r} maps {sorted(driving['key_mapping'])} "
                    "but the leg reads the whole primary key off Gold. Phase 2 "
                    "would fail resolving the keyset, so refusing before any "
                    "write.")
            # Physical -> logical alias is mandatory: Gold stores snake_case
            # (to_physical) while build_driving_filter compares logical names.
            spark.sql(
                "SELECT DISTINCT "
                + ", ".join(f"`{gold_cdc.physical_name(c)}` AS `{c}`"
                            for c in primary_key_cols)
                + f" FROM {table_full}"
            ).createOrReplaceTempView("recompute_keys")
            keysets.append(("recompute_keys", list(primary_key_cols)))
            print(f"[RECOMPUTE] leg attached, declared for "
                  f"{RECOMPUTE_COLUMNS} — every Gold entity joins this run's "
                  f"rebuild set, and whole rows are recomputed")

        if not keysets:
            # Persist the gate state even though nothing changed: Iceberg
            # maintenance commits (compaction, expire_snapshots) move
            # metadata_location without producing watermark rows, and without
            # this write the stored map never catches up — the dispatcher
            # gate would dispatch a full run every tick, forever. Watermarks
            # merge as a no-op; only the snapshot map and run metadata move.
            print("[GOLD] no new Silver records — already up to date")
            # A row with no bookmark yet is a FIRST run, and a first run
            # stores its own start (ADR-0020 decisions 1 and 3) even when
            # idle - the idle scan proved nothing was written up to its
            # read, so its start is a valid bookmark. Only a row that
            # already carries one is left alone ("advances nothing").
            _advance_bookmark(None if stored_bookmark else started_at.isoformat(),
                              silver_snapshots)
            # a maintenance backfill still owes its run — with zero pending
            # deltas this is exactly the safe case the deferral protects
            if deferred_backfill is not None:
                deferred_backfill()
            status = "SUCCEEDED"
            job.commit()
            return

        # ═══ PHASE 2 — rebuild affected entities from FULL Silver (D1) ═══
        register_full_views(exclude=driving["alias"])
        # Driving source: augment with its key projections, then keep only
        # rows matched by ANY keyset (subset keysets fan out naturally).
        # Same sentinel-cleaned projection as the full views (key columns
        # are protected, so the key expressions below see raw identifiers).
        drv = source_view(driving)
        gk_map = {}
        for col, expr in sorted(driving["key_mapping"].items()):
            gk = f"__gk_{col}"
            drv = drv.withColumn(gk, F.expr(expr))
            gk_map[col] = f"d.`{gk}`"
        drv.createOrReplaceTempView("__driving_aug")
        drv_filter = gold_cdc.build_driving_filter(gk_map, keysets)
        filtered = spark.sql(f"SELECT * FROM __driving_aug d WHERE {drv_filter}")
        filtered = filtered.drop(*[f"__gk_{c}" for c in driving["key_mapping"]])
        filtered.createOrReplaceTempView(driving["alias"])

        result = spark.sql(sql_text)
        validate_output_columns(result, master_schema)
        result = add_metadata_cols(to_physical(result))
        result.cache()
        cached_dfs.append(result)
        rows_rebuilt = result.count()
        affected_count = rows_rebuilt
        print(f"[PHASE 2] entities_rebuilt={rows_rebuilt}")

        # Guardrail (D1): a near-full-table incremental deserves a loud note.
        gold_count = spark.sql(
            f"SELECT count(*) AS c FROM {table_full}").collect()[0]["c"]
        # The leg selects every Gold entity by design, so this guardrail would
        # fire on every run that carries it — and one that always fires is one
        # nobody reads. Report the expected shape instead.
        if RECOMPUTE_COLUMNS:
            print(f"[RECOMPUTE] rebuilt {rows_rebuilt} of {gold_count} gold "
                  f"rows: the leg selects all of them, so a high fraction here "
                  f"is expected, not a signal.")
        elif gold_count > 0 and rows_rebuilt > max_affected_fraction * gold_count:
            print(f"[WARN] affected entities ({rows_rebuilt}) exceed "
                  f"{max_affected_fraction:.0%} of gold rows ({gold_count}) — "
                  f"effectively a rebuild; check upstream or worker sizing.")

        result.createOrReplaceTempView("gold_updates")

        # ═══ HASH DIFF vs current Gold (D4/D5) ═══
        pk_join = gold_cdc.build_pk_conditions(primary_key_cols, "g", "u")
        changed_expr = gold_cdc.build_changed_channels_expr(channels, "u", "g")
        first_pk = gold_cdc.physical_name(primary_key_cols[0])
        diff = spark.sql(f"""
            SELECT u.*,
                   CASE WHEN g.`{first_pk}` IS NULL THEN 'INSERT'
                        ELSE 'UPDATE' END AS `__op`,
                   {changed_expr} AS `__changed_channels`
            FROM gold_updates u
            LEFT JOIN {table_full} g ON {pk_join}
        """).filter(F.expr("size(`__changed_channels`) > 0"))
        diff.cache()
        cached_dfs.append(diff)
        rows_written = diff.count()
        print(f"[HASH DIFF] rows_changed={rows_written} "
              f"(no-ops skipped={rows_rebuilt - rows_written})")

        if rows_written > 0:
            diff.createOrReplaceTempView("gold_diff")
            ensure_changes_table()

            # D6 ordering: (1) change log … (2) MERGE … (3) watermarks.
            append_change_log("gold_diff")
            print("[CHANGE LOG APPENDED]")

            diff.drop("__op", "__changed_channels") \
                .createOrReplaceTempView("gold_changed")
            spark.sql(gold_cdc.build_gated_merge_sql(
                table_full, primary_key_cols, "gold_changed"))
            print(f"[ICEBERG] merged {rows_written} rows into {table_full}")

        _advance_bookmark(
            started_at.isoformat(), silver_snapshots,
            recompute_at=started_at.isoformat() if RECOMPUTE_COLUMNS else None)

        # Deferred maintenance backfill (see the definition site): Gold now
        # reflects every delta this run consumed, so the recomputed hashes
        # can no longer mask pending updates from the next run's hash gate.
        if deferred_backfill is not None:
            deferred_backfill()

        status = "SUCCEEDED"
        job.commit()

    except Exception:
        error_msg = traceback.format_exc()
        print(f"[FATAL ERROR]\n{error_msg}")

    finally:
        for df in cached_dfs:
            try:
                df.unpersist()
            except Exception:
                pass
        write_metrics(status, affected_count, rows_rebuilt, rows_written,
                      gold_table_lbl, error_msg)
        if spark is not None:
            spark.stop()

    if status != "SUCCEEDED":
        raise RuntimeError(
            f"Gold pipeline {GOLD_ID} failed — see error_message in metrics table."
        )


def _seed_bookmark() -> str:
    """No bookmark on the row. UIFPE-522 migration (owner decision
    2026-09-23): seed from the newest SUCCEEDED transform run in the metrics
    table, so the first run after the per-source map retires stays
    incremental. A never-run entity, or a metrics read that fails, falls
    back to epoch: a full detection, which is always safe."""
    try:
        kwargs = {
            "KeyConditionExpression": Key("job_name").eq(JOB_NAME),
            "FilterExpression": Attr("gold_id").eq(GOLD_ID),
            "ProjectionExpression": "gold_id, #s, started_at",
            "ExpressionAttributeNames": {"#s": "status"},
        }
        items = []
        while True:
            resp = dynamo.Table(METRICS_TABLE).query(**kwargs)
            items.extend(resp.get("Items", []))
            if "LastEvaluatedKey" not in resp:
                break
            kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
        seed = gold_cdc.latest_successful_start(items, GOLD_ID)
    except Exception as e:
        print(f"[WARN] [BOOKMARK SEED FAILED] {type(e).__name__}: {e} — "
              f"full detection from epoch")
        return gold_cdc.EPOCH_WATERMARK
    if seed is None:
        print("[BOOKMARK] none on the row and no successful run on record — "
              "full detection from epoch")
        return gold_cdc.EPOCH_WATERMARK
    print(f"[BOOKMARK] seeded from the last successful run's start {seed}")
    return seed


def _advance_bookmark(bookmark, snapshots: dict = None,
                      recompute_at: str = None):
    """D6 step 3 / ADR-0020 decision 1: persist this run's own start as the
    bookmark (None = the run processed nothing, so it advances nothing),
    the snapshot map and run metadata, and REMOVE the retired per-source
    map - one guarded update (gold_cdc.build_bookmark_update_args).

    No retry: the condition only fails when a run that started later has
    already finished, and its bookmark covers this window. The change log
    and MERGE are committed, so skipping costs one re-detection the hash
    gate drops - but two runs overlapped on one gold_id, which the
    dispatcher's in-flight guard should prevent."""
    update_args = gold_cdc.build_bookmark_update_args(
        bookmark, RUN_ID, datetime.now(timezone.utc).isoformat(),
        snapshots=to_dynamo_safe(snapshots) if snapshots else None,
        recompute_at=recompute_at)
    try:
        dynamo.Table(CONFIG_TABLE).update_item(
            Key={"gold_id": GOLD_ID, "version": "latest"}, **update_args)
        print(f"[BOOKMARK ADVANCED] {bookmark}" if bookmark
              else "[BOOKMARK UNCHANGED] run processed nothing")
    except CONDITIONAL_CHECK_FAILED:
        print(f"[WARN] [BOOKMARK NOT ADVANCED] gold_id={GOLD_ID}: a later-"
              f"started run already holds a newer bookmark than {bookmark}. "
              f"Safe — the change log and MERGE for {RUN_ID} are committed — "
              f"but two runs overlapped, so the dispatcher in-flight guard "
              f"is not holding. Investigate.")


run()
