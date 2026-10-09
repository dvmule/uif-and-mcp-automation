# gold_ops_summarize.py
# Nightly: one row per gold entity per day into uif_echo_{env}_gold_ops.daily_stats.
#
# WHY A TABLE AND NOT A DASHBOARD. The metrics rows expire after 90 days and the
# change logs are per entity, so "how did the fleet do last month" needs an
# engineer with Athena today. This job writes the answer down once a night, in
# the catalog, where the Overview page and the monthly pack both read it
# (UIFPE-544).
#
# All of the arithmetic - which runs count, how a percentile is taken, when an
# entity was fresh - lives in gold_stats.py, which is pure and unit-tested. This
# script only fetches, calls it, and writes.
#
# Job arguments (beyond Glue's own JOB_NAME / JOB_RUN_ID):
#   --env            (required) environment token; NO default (finding #15)
#   --day            YYYY-MM-DD to summarise; default yesterday (UTC)
#   --backfill_days  how many days ending at --day; default 1. The initial
#                    back-fill runs 90, the metrics table's whole retention.
#
# Idempotent: each day is DELETEd and rewritten, so a re-run or an overlapping
# back-fill can never double-count.
#
# After the days are written it also refreshes ONE small JSON file - the
# Overview page's 30-day half - in the gold artifacts bucket, because a Lambda
# cannot read an Iceberg table.

import json
import sys
import traceback
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.conditions import Key

from awsglue.utils import getResolvedOptions
from awsglue.context import GlueContext
from awsglue.job import Job
from pyspark.context import SparkContext

import gold_stats
import gold_telemetry  # EMF run document, like every other gold job

# ─────────────────────────────────────────────────────────────────
# BOOTSTRAP
# ─────────────────────────────────────────────────────────────────
import argparse as _argparse
_p = _argparse.ArgumentParser(add_help=False)
_p.add_argument("--JOB_NAME",   default="unknown")
_p.add_argument("--JOB_RUN_ID", default="unknown")
_p.add_argument("--env",        default=None)
_p.add_argument("--day",            default="")
_p.add_argument("--backfill_days",  default="1")
_glue_defaults, _ = _p.parse_known_args(sys.argv[1:])

args = getResolvedOptions(sys.argv, [
    "CONFIG_TABLE", "METRICS_TABLE", "GOLD_WAREHOUSE",
    "TRANSFORM_JOB_NAME", "RECONCILE_JOB_NAME",
    "STATS_DATABASE", "STATS_TABLE", "STATS_LOCATION",
    "SUMMARY_BUCKET",
])
JOB_NAME = _glue_defaults.JOB_NAME
RUN_ID   = _glue_defaults.JOB_RUN_ID
if not (_glue_defaults.env or "").strip():
    raise ValueError(
        "--env is required and was not supplied. Refusing to default to 'dev': "
        "the value selects the catalog this job writes to, so guessing it would "
        "publish one environment's statistics into another. Terraform sets it in "
        "the job's default_arguments.")
ENV = _glue_defaults.env.strip()
REGION = "eu-west-1"

CONFIG_TABLE   = args["CONFIG_TABLE"]
METRICS_TABLE  = args["METRICS_TABLE"]
TRANSFORM_JOB  = args["TRANSFORM_JOB_NAME"]
RECONCILE_JOB  = args["RECONCILE_JOB_NAME"]
STATS_TABLE    = f"glue_catalog.`{args['STATS_DATABASE']}`.`{args['STATS_TABLE']}`"
STATS_LOCATION = args["STATS_LOCATION"]
SUMMARY_BUCKET = args["SUMMARY_BUCKET"]

started_at = datetime.now(timezone.utc)
dynamo = boto3.resource("dynamodb", region_name=REGION)

day = gold_stats.parse_day(_glue_defaults.day) if _glue_defaults.day.strip() \
    else gold_stats.previous_day(started_at)
if day is None:
    raise ValueError(f"--day must be YYYY-MM-DD, got {_glue_defaults.day!r}")
try:
    backfill = int(_glue_defaults.backfill_days)
except (TypeError, ValueError):
    raise ValueError(f"--backfill_days must be an integer, got "
                     f"{_glue_defaults.backfill_days!r}")
DAYS = gold_stats.days_back(day, backfill)
print(f"[SUMMARISING] {len(DAYS)} day(s) {DAYS[0]} .. {DAYS[-1]} env={ENV}")


# ─────────────────────────────────────────────────────────────────
# FETCH (DynamoDB)
# ─────────────────────────────────────────────────────────────────

def scan_all(table, **kwargs) -> list:
    items, start = [], None
    while True:
        if start:
            kwargs["ExclusiveStartKey"] = start
        resp = table.scan(**kwargs)
        items.extend(resp.get("Items", []))
        start = resp.get("LastEvaluatedKey")
        if not start:
            break
    return items


def query_all(table, **kwargs) -> list:
    items, start = [], None
    while True:
        if start:
            kwargs["ExclusiveStartKey"] = start
        resp = table.query(**kwargs)
        items.extend(resp.get("Items", []))
        start = resp.get("LastEvaluatedKey")
        if not start:
            break
    return items


def gold_configs() -> list:
    """Every entity, enabled or not: a disabled entity still gets a row, with
    zeros, so the fleet's denominator does not change when someone pauses one."""
    return scan_all(dynamo.Table(CONFIG_TABLE),
                    FilterExpression=Key("version").eq("latest"))


def runs_for_job(job_name: str) -> list:
    """Every run of this job the metrics table still holds (90-day TTL). One
    query on the partition key; the day filter is applied in gold_stats, which
    is where the definition of "in this day" belongs."""
    if not job_name:
        return []
    return query_all(dynamo.Table(METRICS_TABLE),
                     KeyConditionExpression=Key("job_name").eq(job_name),
                     ProjectionExpression=("gold_id, #s, started_at, completed_at, "
                                           "duration_sec, rows_read, rows_written, "
                                           "affected_keys, rows_deleted, orphan_count"),
                     ExpressionAttributeNames={"#s": "status"})


# ─────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────
status, error_msg, rows_written = "FAILED", None, 0
spark = None
try:
    configs = gold_configs()
    transform_runs = runs_for_job(TRANSFORM_JOB)
    reconcile_runs = runs_for_job(RECONCILE_JOB)
    print(f"[FETCHED] entities={len(configs)} transform_runs={len(transform_runs)} "
          f"reconcile_runs={len(reconcile_runs)}")

    sc = SparkContext.getOrCreate()
    glueCtx = GlueContext(sc)
    spark = glueCtx.spark_session
    job = Job(glueCtx)
    job.init(JOB_NAME, args)
    spark.sparkContext.setLogLevel("WARN")
    spark.conf.set("spark.sql.shuffle.partitions", "8")
    spark.conf.set("spark.hadoop.fs.s3a.endpoint.region", REGION)
    spark.conf.set("spark.sql.catalog.glue_catalog",
                   "org.apache.iceberg.spark.SparkCatalog")
    spark.conf.set("spark.sql.catalog.glue_catalog.catalog-impl",
                   "org.apache.iceberg.aws.glue.GlueCatalog")
    spark.conf.set("spark.sql.catalog.glue_catalog.io-impl",
                   "org.apache.iceberg.aws.s3.S3FileIO")
    spark.conf.set("spark.sql.catalog.glue_catalog.warehouse",
                   args["GOLD_WAREHOUSE"])
    print("[SPARK + ICEBERG INITIALISED]")

    spark.sql(gold_stats.create_table_ddl(STATS_TABLE, STATS_LOCATION))

    # A column added to COLUMNS after the table was first built: CREATE IF NOT
    # EXISTS no-ops, so without this the first write fails on a schema
    # mismatch. Driven off the catalog's own column list.
    existing = [f.name for f in spark.table(STATS_TABLE).schema.fields]
    for statement in gold_stats.migrate_table_sql(STATS_TABLE, existing):
        print(f"[MIGRATING] {statement}")
        spark.sql(statement)

    def changes_counts(cfg, one_day):
        """This entity's change log for the day, by op and by channel. A table
        that does not exist yet (an entity that has never written a change) is
        not an error - it is zero."""
        target = cfg.get("target") or {}
        db, tbl = target.get("database"), target.get("table")
        if not db or not tbl:
            return {}, {}
        changes = f"glue_catalog.`{db.replace('{env}', ENV)}`.`{tbl}_changes`"
        try:
            by_op = spark.sql(gold_stats.changes_by_op_sql(changes, one_day)).collect()
            by_ch = spark.sql(gold_stats.changes_by_channel_sql(changes, one_day)).collect()
        except Exception as e:                                  # noqa: BLE001
            print(f"[CHANGES UNAVAILABLE] gold_id={cfg.get('gold_id')} "
                  f"table={changes} {type(e).__name__}: {e}")
            return {}, {}
        return (gold_stats.counts_from_rows([r.asDict() for r in by_op], "op"),
                gold_stats.counts_from_rows([r.asDict() for r in by_ch], "channel"))

    from gold_frequencies import interval_minutes, normalize

    for one_day in DAYS:
        rows = []
        for cfg in configs:
            gold_id = str(cfg.get("gold_id") or "")
            if not gold_id:
                continue
            freq = normalize((cfg.get("schedule") or {}).get("frequency") or "")
            enriched = dict(cfg)
            enriched["_interval_minutes"] = interval_minutes(freq)
            by_op, by_channel = changes_counts(cfg, one_day)
            rows.append(gold_stats.stats_row(
                gold_id=gold_id, day=one_day, cfg=enriched,
                runs=gold_stats.summarise_runs(transform_runs, gold_id, one_day),
                reconcile=gold_stats.summarise_reconcile(reconcile_runs, gold_id, one_day),
                changes_by_op=by_op, changes_by_channel=by_channel,
                computed_at=datetime.now(timezone.utc), source_run_id=RUN_ID))

        # Replace, never append: the job is idempotent by construction.
        spark.sql(gold_stats.delete_day_sql(STATS_TABLE, one_day))
        frame = spark.createDataFrame([gold_stats.as_tuple(r) for r in rows],
                                      schema=gold_stats.spark_schema())
        frame.writeTo(STATS_TABLE).append()
        rows_written += len(rows)
        print(f"[DAY WRITTEN] day={one_day} entities={len(rows)}")

    # The Overview's 30-day half, pre-computed once. A Lambda cannot read an
    # Iceberg table, and granting the controller Athena for a ~400-row query
    # would buy seconds of latency and a wider blast radius for nothing.
    #
    # NOT wrapped in a try/except on purpose: an Overview that renders
    # yesterday's numbers as today's is the decoy failure UIFPE-543 found on the
    # Alerts page. The table write is idempotent, so failing loudly costs one
    # re-run; failing quietly costs trust in every number on the page.
    newest = DAYS[-1]
    summary_rows = [r.asDict() for r in spark.sql(
        gold_stats.recent_days_sql(STATS_TABLE, newest,
                                   gold_stats.SUMMARY_DAYS)).collect()]
    summary = gold_stats.overview_summary(summary_rows, gold_stats.SUMMARY_DAYS,
                                          datetime.now(timezone.utc))
    boto3.client("s3", region_name=REGION).put_object(
        Bucket=SUMMARY_BUCKET, Key=gold_stats.SUMMARY_KEY,
        Body=json.dumps(summary, default=str).encode("utf-8"),
        ContentType="application/json")
    print(f"[SUMMARY WRITTEN] {gold_stats.summary_s3_uri(SUMMARY_BUCKET)} "
          f"days={len(summary['fleet'])} entities={len(summary['entities'])}")

    status = "SUCCEEDED"
    job.commit()

except Exception:
    error_msg = traceback.format_exc()
    print(f"[FATAL ERROR]\n{error_msg}")

finally:
    completed_at = datetime.now(timezone.utc)
    gold_telemetry.emit(gold_telemetry.run_document(
        ENV, "_ops_stats", status, job_name=JOB_NAME, run_id=RUN_ID,
        rows_written=rows_written,
        duration_seconds=int((completed_at - started_at).total_seconds()),
        error=error_msg, now=completed_at))
    if spark is not None:
        spark.stop()

if status != "SUCCEEDED":
    raise RuntimeError("gold ops statistics failed - see [FATAL ERROR] above")
