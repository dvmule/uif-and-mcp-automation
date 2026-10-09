# silver-quality-check.py
# Glue job that loads a dq_check_config item by check_id and dispatches to
# the matching dq-kinds handler. v1 implements reconcile + null-rate;
# the remaining kinds (row-count, freshness, drift, uniqueness, custom-sql)
# write a NOT_IMPLEMENTED metric and exit SKIPPED so the EventBridge tick
# stays observable while the implementations land.

import sys
import time
import traceback
import boto3
from datetime import datetime, timezone
from decimal import Decimal


# ─── BOOTSTRAP ──────────────────────────────────────────────────────
def _bootstrap_args():
    from awsglue.utils import getResolvedOptions
    return getResolvedOptions(sys.argv, ["check_id", "CONFIG_TABLE", "METRICS_TABLE"])


# ─── DYNAMO HELPERS ─────────────────────────────────────────────────
def _to_dynamo_safe(obj):
    if isinstance(obj, float):
        return Decimal(str(obj))
    if isinstance(obj, dict):
        return {k: _to_dynamo_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_dynamo_safe(i) for i in obj]
    return obj


def load_check_config(dynamo, config_table, check_id):
    resp = dynamo.Table(config_table).get_item(Key={"check_id": check_id, "version": "latest"})
    if "Item" not in resp:
        raise ValueError(f"No DQ check config found for check_id={check_id}")
    return resp["Item"]


def write_metrics(dynamo, metrics_table, *, job_name, run_id, check_id, result, started_at):
    completed_at = datetime.now(timezone.utc)
    item = {
        "job_name":     job_name,
        "run_id":       run_id,
        "pipeline_id": f"dq:{check_id}",
        "check_id":     check_id,
        "status":       result.get("status", "FAILED"),
        "summary":      result.get("message", ""),
        "details":      _to_dynamo_safe({k: v for k, v in result.items()
                                          if k not in ("status", "message")}),
        "started_at":   started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
        "duration_sec": int((completed_at - started_at).total_seconds()),
        "ttl":          int(time.time()) + (90 * 86400),
    }
    dynamo.Table(metrics_table).put_item(Item=_to_dynamo_safe(item))


def update_check_last_run(dynamo, config_table, check_id, result, run_id):
    last_run = {
        "ts":      datetime.now(timezone.utc).isoformat(),
        "status":  result.get("status", "FAILED"),
        "summary": result.get("message", ""),
        "run_id":  run_id,
    }
    try:
        dynamo.Table(config_table).update_item(
            Key={"check_id": check_id, "version": "latest"},
            UpdateExpression="SET last_run = :lr",
            ExpressionAttributeValues={":lr": _to_dynamo_safe(last_run)},
        )
    except Exception as e:
        print(f"[WARN] failed to update last_run for {check_id}: {e}")


# ─── DISPATCH ───────────────────────────────────────────────────────
_UNIMPLEMENTED_KINDS = {"row-count", "freshness", "drift", "uniqueness", "custom-sql"}


def dispatch_check(kind, spark, config, **loaders):
    """Route a check to its kind module."""
    from _module_loader import load_dq_kind_module

    if kind == "reconcile":
        return load_dq_kind_module("reconcile").run_reconcile(spark, config, **loaders)
    if kind == "null-rate":
        return load_dq_kind_module("null-rate").run_null_rate(spark, config)
    if kind in _UNIMPLEMENTED_KINDS:
        return {
            "status": "NOT_IMPLEMENTED",
            "message": f"kind '{kind}' is reserved for v2 — config is valid, "
                       f"runtime handler ships in a later release.",
        }
    return {"status": "FAILED", "message": f"unknown DQ check kind '{kind}'"}


# ─── MAIN ───────────────────────────────────────────────────────────
def main():
    from awsglue.context import GlueContext
    from awsglue.job import Job
    from pyspark.context import SparkContext

    args     = _bootstrap_args()
    region   = "eu-west-1"
    dynamo   = boto3.resource("dynamodb", region_name=region)
    cfg      = load_check_config(dynamo, args["CONFIG_TABLE"], args["check_id"])

    import argparse as _argparse
    _p = _argparse.ArgumentParser(add_help=False)
    _p.add_argument("--env", default="dev")
    _env_args, _ = _p.parse_known_args(sys.argv[1:])
    env = _env_args.env

    sc       = SparkContext()
    glue_ctx = GlueContext(sc)
    spark    = glue_ctx.spark_session
    job      = Job(glue_ctx)
    job_name = args.get(
        "JOB_NAME", f'{args["RESOURCE_PREFIX"]}-silver-quality-check'
    )
    job.init(job_name, args)
    spark.sparkContext.setLogLevel("WARN")

    spark.conf.set("spark.sql.catalog.glue_catalog",
                   "org.apache.iceberg.spark.SparkCatalog")
    spark.conf.set("spark.sql.catalog.glue_catalog.catalog-impl",
                   "org.apache.iceberg.aws.glue.GlueCatalog")
    spark.conf.set("spark.sql.catalog.glue_catalog.io-impl",
                   "org.apache.iceberg.aws.s3.S3FileIO")
    spark.conf.set("spark.sql.catalog.glue_catalog.warehouse",
                   args["SILVER_WAREHOUSE"])

    started_at = datetime.now(timezone.utc)
    run_id     = args.get("JOB_RUN_ID", "unknown")
    check_id   = args["check_id"]

    try:
        result = dispatch_check(cfg["kind"], spark, cfg)
    except Exception:
        result = {"status": "FAILED", "message": traceback.format_exc()}

    write_metrics(dynamo, args["METRICS_TABLE"],
                  job_name=job_name, run_id=run_id, check_id=check_id,
                  result=result, started_at=started_at)
    update_check_last_run(dynamo, args["CONFIG_TABLE"], check_id, result, run_id)

    if result.get("status") == "FAILED":
        raise RuntimeError(f"DQ check {check_id} FAILED: {result.get('message')}")

    job.commit()
    spark.stop()


if __name__ == "__main__":
    main()
