# gold_ops_export.py
# Monthly: the gold leadership pack, as one CSV, with no hand assembly.
#
# WHY THIS EXISTS. daily_stats answers "what happened on this day to this
# table". Once a month leadership asks a different question per domain - how
# many tables did we serve, how many rows, did they meet their freshness
# target, how often did a run fail, how much compute - and answering it today
# means an engineer writing Athena queries by hand (UIFPE-544).
#
# All of the arithmetic lives in gold_report.py, which is pure and unit-tested.
# This script only reads the month, calls it, and writes the file.
#
# Job arguments (beyond Glue's own JOB_NAME / JOB_RUN_ID):
#   --env    (required) environment token; NO default
#   --month  YYYY-MM to report; default the month that just ended
#
# NOTE on --month: it is ABSENT from default_arguments, never set to "". Glue
# drops an empty value instead of passing it as an argv token, which leaves the
# flag adjacent to the orphaned value of a Glue-consumed --enable-* directive
# and makes argparse bind that. That cost the nightly job every one of its
# runs; a test now forbids an empty default on any argument.
#
# Idempotent: the month is written to one key, so a re-run replaces it.

import json
import sys
import traceback
from datetime import datetime, timezone

import boto3

from awsglue.utils import getResolvedOptions
from awsglue.context import GlueContext
from awsglue.job import Job
from pyspark.context import SparkContext

import gold_report
import gold_telemetry  # EMF run document, like every other gold job

# ─────────────────────────────────────────────────────────────────
# BOOTSTRAP
# ─────────────────────────────────────────────────────────────────
import argparse as _argparse
_p = _argparse.ArgumentParser(add_help=False)
_p.add_argument("--JOB_NAME",   default="unknown")
_p.add_argument("--JOB_RUN_ID", default="unknown")
_p.add_argument("--env",        default=None)
_p.add_argument("--month",      default="")
_glue_defaults, _ = _p.parse_known_args(sys.argv[1:])

args = getResolvedOptions(sys.argv, [
    "GOLD_WAREHOUSE", "STATS_DATABASE", "STATS_TABLE", "REPORT_BUCKET",
])
JOB_NAME = _glue_defaults.JOB_NAME
RUN_ID   = _glue_defaults.JOB_RUN_ID
if not (_glue_defaults.env or "").strip():
    raise ValueError(
        "--env is required and was not supplied. Refusing to default to 'dev': "
        "the value selects the catalog this job reads and the bucket it writes, "
        "so guessing it would publish one environment's numbers as another's. "
        "Terraform sets it in the job's default_arguments.")
ENV = _glue_defaults.env.strip()
REGION = "eu-west-1"

STATS_TABLE   = f"glue_catalog.`{args['STATS_DATABASE']}`.`{args['STATS_TABLE']}`"
REPORT_BUCKET = args["REPORT_BUCKET"]

started_at = datetime.now(timezone.utc)

MONTH = (gold_report.parse_month(_glue_defaults.month)
         if _glue_defaults.month.strip()
         else gold_report.previous_month(started_at))
if MONTH is None:
    raise ValueError(f"--month must be YYYY-MM, got {_glue_defaults.month!r}")
print(f"[REPORTING] month={MONTH} env={ENV}")


# ─────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────
status, error_msg, rows_written = "FAILED", None, 0
spark = None
try:
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

    daily = [r.asDict() for r in
             spark.sql(gold_report.month_rows_sql(STATS_TABLE, MONTH)).collect()]
    print(f"[READ] {len(daily)} daily row(s) for {MONTH}")

    pack = gold_report.roll_up(daily, MONTH, datetime.now(timezone.utc))
    csv_text = gold_report.render_csv(pack)
    rows_written = len(pack)

    # A month with no daily rows still writes a header-only file. An empty
    # report that exists says "the export ran and the month is empty"; a
    # missing file says nothing, and somebody has to go and find out which.
    if not pack:
        print(f"[EMPTY MONTH] {MONTH} has no rows in daily_stats; "
              f"writing a header-only pack so the absence is visible")

    body = csv_text.encode("utf-8")
    s3 = boto3.client("s3", region_name=REGION)
    for key in (gold_report.report_key(MONTH), gold_report.latest_key()):
        s3.put_object(Bucket=REPORT_BUCKET, Key=key, Body=body,
                      ContentType="text/csv")
        print(f"[PACK WRITTEN] s3://{REPORT_BUCKET}/{key} rows={len(pack)}")

    # The domain rows are the sum of their entity rows by construction. Checked
    # anyway, and loudly, because the whole point of the file is that nobody
    # re-derives it by hand - so nobody would notice if they disagreed.
    #
    # Over EVERY column the library declares additive, not a list kept here:
    # the hand-kept list is how compute_minutes went unchecked until a reader
    # added the column up and got a different total.
    problems = gold_report.reconcile(pack)
    if problems:
        raise ValueError("the pack does not add up: " + "; ".join(problems))
    domains = [r for r in pack if r["grain"] == gold_report.DOMAIN_GRAIN]
    print(f"[RECONCILED] {len(domains)} domain row(s) equal their entity rows "
          f"across {len(gold_report.ADDITIVE_COLUMNS)} additive columns")

    status = "SUCCEEDED"
    job.commit()

except Exception:
    error_msg = traceback.format_exc()
    print(f"[FATAL ERROR]\n{error_msg}")

finally:
    completed_at = datetime.now(timezone.utc)
    gold_telemetry.emit(gold_telemetry.run_document(
        ENV, "_ops_report", status, job_name=JOB_NAME, run_id=RUN_ID,
        rows_written=rows_written,
        duration_seconds=int((completed_at - started_at).total_seconds()),
        error=error_msg, now=completed_at))
    if spark is not None:
        spark.stop()

if status != "SUCCEEDED":
    raise RuntimeError("gold monthly pack failed - see [FATAL ERROR] above")
