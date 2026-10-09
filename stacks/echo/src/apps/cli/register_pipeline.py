#!/usr/bin/env python3
# register_pipeline.py
# CLI bootstrap tool for adding new pipeline configs to DynamoDB.
# Used by engineers before the React UI admin panel is available,
# or for scripted bulk registration of many tables.
#
# Usage:
#   python register_pipeline.py \
#     --pipeline-id   oracle-erp-orders \
#     --secret-arn    arn:aws:secretsmanager:eu-west-1:123:secret/oracle-erp \
#     --table         APPS.RA_CUSTOMER_TRX_ALL \
#     --mode          incremental \
#     --watermark-col LAST_UPDATE_DATE \
#     --s3-path       s3://datalake/bronze/erp/orders/ \
#     --frequency     daily \
#     --not-null-cols CUSTOMER_TRX_ID,BILL_TO_CUSTOMER_ID \
#     --domain        finance \
#     --owner         raj.karunakaran@pearson.com

import argparse
import boto3
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# The migration into stacks/echo moved libs from apps/lambda/ to src/libs/.
# Standalone CLIs get no conftest, so they need the path set explicitly.
_LIBS = Path(__file__).resolve().parents[2] / "libs"
if str(_LIBS) not in sys.path:
    sys.path.insert(0, str(_LIBS))

import stack_naming as sn  # noqa: E402

from daily_schedule import DAILY_SLOT_COUNT  # noqa: E402
from schedule_vocabulary import (  # noqa: E402
    BRONZE_SILVER_FREQUENCIES as SCHEDULABLE_FREQUENCIES,
)

REGION = "eu-west-1"


def build_item(args) -> dict:
    columns = (
        [c.strip() for c in args.columns.split(",") if c.strip()]
        if args.columns else None
    )
    item = {
        "pipeline_id": args.pipeline_id,
        "version":     "latest",
        "enabled":     True,
        "source": {
            "type":               "oracle",
            "secret_arn":         args.secret_arn,
            "jdbc_extra_options": args.jdbc_extra_options or "",
        },
        "extract": {
            "table":              args.table,
            "mode":               args.mode,
            "watermark_col":      args.watermark_col,
            "watermark_fallback_col": args.watermark_fallback_col or None,
            "watermark_value":    args.watermark_value or "1970-01-01T00:00:00",
            "jdbc_partitions":    int(args.jdbc_partitions),
            "jdbc_partition_col": args.partition_col or None,
            "jdbc_lower_bound":   1,
            "jdbc_upper_bound":   10_000_000,
            "columns":            columns,
            "custom_sql":         args.custom_sql or None,
        },
        "target": {
            "s3_path":       args.s3_path,
            "partition_cols": ["_extract_date"],
            "compression":   "snappy",
        },
        "schedule": {
            "frequency": args.frequency,
        },
        "quality": {
            "not_null_cols":     [c.strip() for c in args.not_null_cols.split(",")]
                                  if args.not_null_cols else [],
            "min_rows_expected": int(args.min_rows),
            "reject_on_nulls":   False,
        },
        "glue": {
            "worker_type":      args.worker_type,
            "num_workers":      int(args.num_workers),
            "timeout_minutes":  120,
            "max_retries":      2,
        },
        "metadata": {
            "owner":         args.owner,
            "domain":        args.domain,
            "registered_at": datetime.now(timezone.utc).isoformat(),
        },
    }

    # Add Bronze → GCS replication block if requested
    if args.enable_gcs_replication:
        prefix = args.gcs_prefix or ""
        if prefix and not prefix.endswith("/"):
            prefix = prefix + "/"
        item["replication"] = {
            "gcs": {
                "enabled":                True,
                "bucket":                 args.gcs_bucket,
                "prefix":                 prefix,
                "credentials_secret_arn": args.gcs_secret_arn,
            }
        }

    # Add reconciliation config if enabled
    if args.enable_reconcile:
        item["reconcile"] = {
            "enabled":           True,
            "pk_partition_col":  args.reconcile_pk_col or None,
            "pk_lower_bound":    int(args.reconcile_pk_lower),
            "pk_upper_bound":    int(args.reconcile_pk_upper),
            "pk_partitions":     int(args.reconcile_pk_partitions),
            "max_deletes_per_run": int(args.max_deletes_per_run),
            "dry_run":           args.reconcile_dry_run,
            "last_reconciled_at": None,
            "last_orphans_deleted": 0,
        }

    return item


def register(args):
    config_table = sn.load(args.env).config_table
    dynamo = boto3.resource("dynamodb", region_name=REGION)
    item = build_item(args)
    if args.frequency == "daily":
        item.setdefault("schedule", {})["daily_slot"] = (
            abs(hash(args.pipeline_id)) % DAILY_SLOT_COUNT
        )

    dynamo.Table(config_table).put_item(Item=item)

    print(f"Registered pipeline: {args.pipeline_id} → {config_table}")
    print(json.dumps(item, indent=2, default=str))


def main():
    # Box-drawing output cannot encode to cp1252, which is what Windows
    # gives a non-terminal stdout -- declare it, as sync_pipelines.py does.
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    p = argparse.ArgumentParser(
        description="Register a new Oracle ingestion pipeline in DynamoDB."
    )
    p.add_argument("--pipeline-id",         required=True,  help="Unique pipeline identifier")
    p.add_argument("--secret-arn",           required=True,  help="Secrets Manager ARN for Oracle credentials")
    p.add_argument("--table",               required=True,  help="Oracle table (SCHEMA.TABLE)")
    p.add_argument("--s3-path",             required=True,  help="Target S3 path (s3://bucket/prefix/)")
    p.add_argument("--mode",                default="incremental", choices=["full", "incremental"])
    p.add_argument("--watermark-col",       default="LAST_UPDATE_DATE")
    p.add_argument("--watermark-fallback-col", default=None,
                   help="Fallback column when watermark-col is NULL (e.g. CREATION_DATE)")
    p.add_argument("--watermark-value",     default=None,   help="ISO timestamp to start from (default: epoch)")
    p.add_argument("--jdbc-partitions",     default="4",    help="Number of parallel JDBC read partitions")
    p.add_argument("--partition-col",       default=None,   help="Numeric column for JDBC partition splits (omit for single-partition read; never use ROWID)")
    p.add_argument("--columns",             default=None,   help="Comma-separated column list for projection (omit for SELECT *)")
    p.add_argument("--jdbc-extra-options",  default="",     help="Extra JDBC options (e.g. oracle.net.ssl_version=1.2)")
    p.add_argument("--custom-sql",          default=None,   help="Override extract SQL (subquery without alias)")
    p.add_argument("--frequency",           default="daily", choices=list(SCHEDULABLE_FREQUENCIES),
                   help="Bronze/Silver cadence; must be one an EventBridge rule dispatches")
    p.add_argument("--not-null-cols",       default="",     help="Comma-separated columns to null-check")
    p.add_argument("--min-rows",            default="0",    help="Minimum rows expected per run")
    p.add_argument("--worker-type",         default="G.1X", choices=["G.1X", "G.2X", "G.4X"])
    p.add_argument("--num-workers",         default="2")
    p.add_argument("--owner",               default="")
    p.add_argument("--domain",              default="")
    p.add_argument("--env",                 default="dev", choices=list(sn.ENVIRONMENTS),
                                            help="Target environment (default: dev)")

    # Bronze → GCS replication (optional)
    p.add_argument("--enable-gcs-replication",  action="store_true",
                                                 help="Replicate Bronze Parquet to a GCS bucket after S3 write")
    p.add_argument("--gcs-bucket",              default=None,
                                                 help="GCS bucket name (required if --enable-gcs-replication)")
    p.add_argument("--gcs-prefix",              default=None,
                                                 help="GCS object prefix, e.g. 'erp/orders/' (required if --enable-gcs-replication)")
    p.add_argument("--gcs-secret-arn",          default=None,
                                                 help="Secrets Manager ARN holding the GCP service account JSON")

    # Reconciliation configuration (optional)
    p.add_argument("--enable-reconcile",         action="store_true",
                                                 help="Enable hard-delete reconciliation for this pipeline")
    p.add_argument("--reconcile-pk-col",         default=None,
                                                 help="Numeric column for Oracle PK range partitioning (omit for single-partition scan)")
    p.add_argument("--reconcile-pk-lower",       default="1",
                                                 help="Lower bound for PK range (default: 1)")
    p.add_argument("--reconcile-pk-upper",       default="10000000",
                                                 help="Upper bound for PK range (default: 10000000)")
    p.add_argument("--reconcile-pk-partitions",  default="4",
                                                 help="Number of parallel JDBC partitions for PK scan (default: 4)")
    p.add_argument("--max-deletes-per-run",      default="5000",
                                                 help="Safety cap on orphan deletes per run (default: 5000)")
    p.add_argument("--reconcile-dry-run",        action="store_true",
                                                 help="Start in dry-run mode (log orphans, don't delete)")

    args = p.parse_args()
    if args.enable_gcs_replication:
        missing = [
            flag for flag, val in (
                ("--gcs-bucket",     args.gcs_bucket),
                ("--gcs-prefix",     args.gcs_prefix),
                ("--gcs-secret-arn", args.gcs_secret_arn),
            ) if not val
        ]
        if missing:
            p.error(f"--enable-gcs-replication requires: {', '.join(missing)}")
    register(args)


if __name__ == "__main__":
    main()
