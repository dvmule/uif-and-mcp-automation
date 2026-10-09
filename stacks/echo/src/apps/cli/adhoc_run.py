#!/usr/bin/env python3
# adhoc_run.py
# Triggers a single Glue job run for a given pipeline_id and tails the result.
#
# Usage — run against an existing pipeline:
#   aws-runas ingestion-deploy python scripts/adhoc_run.py --pipeline-id oracle-erp-orders
#
# Usage — register a minimal AWS test config and immediately run it:
#   aws-runas ingestion-deploy python scripts/adhoc_run.py \
#     --pipeline-id  test-run \
#     --secret-arn   arn:aws:secretsmanager:eu-west-1:295439920518:secret/oracle-mydb \
#     --table        APPS.MY_TABLE \
#     --s3-path      s3://eds-dev-bronze/test/my_table/ \
#     --mode         full \
#     --register
#
# Usage — register a GCP-native pipeline (Bronze on GCS, Silver Iceberg on GCS + BigLake):
#   python scripts/adhoc_run.py --register \
#     --pipeline-id      smoke-gcp-mytable \
#     --destination-cloud gcp \
#     --secret-arn       arn:aws:secretsmanager:eu-west-1:...:secret:oracle-... \
#     --table            SCHEMA.MY_TABLE \
#     --gcs-path         gs://my-bronze-bucket/my_table/ \
#     --gcp-secret-arn   arn:aws:secretsmanager:eu-west-1:...:secret:gcp-... \
#     --silver-gcs-path  gs://my-silver-bucket/my_table/ \
#     --silver-table     MY_TABLE \
#     --silver-pk-col    ID \
#     --bq-project       my-gcp-project \
#     --bq-dataset       my_silver_dataset
#
# Exit codes: 0 = SUCCEEDED, 1 = FAILED/ERROR/TIMEOUT

import argparse
import json
import sys
import time
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.conditions import Key
from pathlib import Path

# Standalone CLIs get no conftest, so the libs path is set explicitly.
_LIBS = Path(__file__).resolve().parents[2] / "libs"
if str(_LIBS) not in sys.path:
    sys.path.insert(0, str(_LIBS))

import stack_naming as sn  # noqa: E402

REGION        = "eu-west-1"
POLL_INTERVAL = 15   # seconds between status checks
RUN_TIMEOUT   = 3600 # seconds before the script gives up polling (job itself may still run)

TERMINAL_STATES = {"SUCCEEDED", "FAILED", "ERROR", "STOPPED", "TIMEOUT"}


# ── helpers ───────────────────────────────────────────────────────

def ts() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S")


def print_step(msg: str):
    print(f"[{ts()}] {msg}")


def _cloud(cfg: dict) -> str:
    """destination.cloud lower-cased; defaults to 'aws' when block missing."""
    return ((cfg.get("destination") or {}).get("cloud") or "aws").lower()


# ── register a minimal pipeline config ────────────────────────────

def register_pipeline(args, dynamo):
    item = {
        "pipeline_id": args.pipeline_id,
        "version":     "latest",
        "enabled":     True,
        "source": {
            "type":               "oracle",
            "secret_arn":         args.secret_arn,
            "jdbc_extra_options": "",
        },
        "extract": {
            "table":              args.table,
            "mode":               args.mode,
            "watermark_col":      args.watermark_col,
            "watermark_fallback_col": args.watermark_fallback_col or None,
            "watermark_value":    "1970-01-01T00:00:00",
            "jdbc_partitions":    4,
            "jdbc_partition_col": "ROWID",
            "jdbc_lower_bound":   1,
            "jdbc_upper_bound":   10_000_000,
            "custom_sql":         None,
        },
        "schedule": {
            "frequency": "daily",
        },
        "quality": {
            "not_null_cols":     [],
            "min_rows_expected": 0,
            "reject_on_nulls":   False,
        },
        "glue": {
            "worker_type":     "G.1X",
            "num_workers":     2,
            "timeout_minutes": 120,
            "max_retries":     0,
        },
        "metadata": {
            "owner":         "adhoc-run",
            "domain":        "test",
            "registered_at": datetime.now(timezone.utc).isoformat(),
        },
    }

    if args.destination_cloud == "gcp":
        # GCP overlay: bronze writes to gs://, silver (optional) writes Iceberg
        # on GCS + BigLake external table in BQ. Controller-side validation
        # (pipeline_controller.py:_validate_destination_config) rejects
        # mixed s3_path + gcs_path or missing bq_project/bq_dataset.
        item["destination"] = {
            "cloud":                       "gcp",
            "gcp_credentials_secret_arn":  args.gcp_secret_arn,
        }
        item["target"] = {
            "gcs_path":       args.gcs_path,
            "partition_cols": ["_extract_date"],
            "compression":    "snappy",
        }
        if args.bq_project and args.bq_dataset and args.silver_gcs_path:
            item["silver"] = {
                "enabled":          True,
                "table":            args.silver_table or args.table.split(".")[-1],
                "primary_key_cols": [args.silver_pk_col] if args.silver_pk_col else [],
                "gcs_path":         args.silver_gcs_path,
                "bq_project":       args.bq_project,
                "bq_dataset":       args.bq_dataset,
                "bq_location":      args.bq_location,
            }
    else:
        item["target"] = {
            "s3_path":        args.s3_path,
            "partition_cols": ["_extract_date"],
            "compression":    "snappy",
        }

    dynamo.Table(CONFIG_TABLE).put_item(Item=item)
    print_step(f"Registered pipeline config for '{args.pipeline_id}' (cloud={args.destination_cloud})")


# ── fetch config to confirm it exists ─────────────────────────────

def check_config_exists(pipeline_id: str, dynamo) -> dict:
    resp = dynamo.Table(CONFIG_TABLE).get_item(
        Key={"pipeline_id": pipeline_id, "version": "latest"}
    )
    item = resp.get("Item")
    if not item:
        print_step(f"ERROR: No config found for pipeline_id='{pipeline_id}' in {CONFIG_TABLE}.")
        print_step("       Register it first with --register or via register_pipeline.py")
        sys.exit(1)
    if not item.get("enabled", True):
        print_step(f"WARNING: pipeline '{pipeline_id}' has enabled=false in DynamoDB.")
        print_step("         The Glue job will exit immediately at Step 2. Continuing anyway.")
    return item


# ── start the Glue job ─────────────────────────────────────────────

def start_job(pipeline_id: str, env: str, glue, cfg: dict) -> str:
    glue_cfg = cfg.get("glue", {})
    kwargs = {
        "JobName":  GLUE_JOB_NAME,
        "Arguments": {"--pipeline_id": pipeline_id, "--env": env},
    }
    if glue_cfg.get("worker_type"):
        kwargs["WorkerType"]      = glue_cfg["worker_type"]
        kwargs["NumberOfWorkers"] = int(glue_cfg.get("num_workers", 2))
    if glue_cfg.get("timeout_minutes"):
        kwargs["Timeout"] = int(glue_cfg["timeout_minutes"])

    resp   = glue.start_job_run(**kwargs)
    run_id = resp["JobRunId"]
    print_step(
        f"Started job run: {run_id} "
        f"(worker={glue_cfg.get('worker_type', 'default')} "
        f"x{glue_cfg.get('num_workers', 'default')})"
    )
    return run_id


# ── poll until terminal state ──────────────────────────────────────

def poll_run(run_id: str, glue) -> dict:
    deadline = time.time() + RUN_TIMEOUT
    last_state = None

    while time.time() < deadline:
        run = glue.get_job_run(JobName=GLUE_JOB_NAME, RunId=run_id)["JobRun"]
        state = run["JobRunState"]

        if state != last_state:
            print_step(f"State: {state}")
            last_state = state

        if state in TERMINAL_STATES:
            return run

        time.sleep(POLL_INTERVAL)

    print_step(f"TIMEOUT: exceeded {RUN_TIMEOUT}s polling limit — job is still running.")
    print_step(f"  Check status manually: aws glue get-job-run --job-name {GLUE_JOB_NAME} --run-id {run_id}")
    sys.exit(1)


# ── fetch metrics written by the job ──────────────────────────────

def fetch_metrics(run_id: str, dynamo) -> dict | None:
    resp = dynamo.Table(METRICS_TABLE).get_item(
        Key={"job_name": GLUE_JOB_NAME, "run_id": run_id}
    )
    return resp.get("Item")


# ── print summary ──────────────────────────────────────────────────

def print_summary(run: dict, metrics: dict | None, run_id: str):
    state = run["JobRunState"]
    sep   = "─" * 60

    print(f"\n{sep}")
    print(f"  Job run:   {run_id}")
    print(f"  State:     {state}")

    if run.get("StartedOn") and run.get("CompletedOn"):
        duration = (run["CompletedOn"] - run["StartedOn"]).total_seconds()
        print(f"  Duration:  {duration:.0f}s")

    if run.get("ErrorMessage"):
        print(f"  Error:     {run['ErrorMessage']}")

    if metrics:
        print(f"\n  ── DynamoDB metrics ──────────────────────")
        for field in ("status", "rows_read", "rows_written", "quality_status",
                      "new_watermark", "error_message"):
            val = metrics.get(field)
            if val is not None:
                print(f"  {field:<20} {val}")
    else:
        print(f"\n  No metrics in {METRICS_TABLE} yet (job may have failed before Step 11).")

    print(sep)

    glue_console = (
        f"https://eu-west-1.console.aws.amazon.com/gluestudio/home"
        f"#/job/{GLUE_JOB_NAME}/run/{run_id}"
    )
    cw_group = f"/aws-glue/jobs/output"
    print(f"\n  CloudWatch logs: {cw_group}  (filter: {run_id})")
    print(f"  Glue console:    {glue_console}\n")


# ── main ──────────────────────────────────────────────────────────

def main():
    # Box-drawing output cannot encode to cp1252, which is what Windows
    # gives a non-terminal stdout -- declare it, as sync_pipelines.py does.
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    p = argparse.ArgumentParser(description="Trigger an ad-hoc Glue ingestion run and tail the result.")
    p.add_argument("--pipeline-id",   required=True,  help="pipeline_id to run")
    p.add_argument("--register",      action="store_true",
                   help="Register a minimal pipeline config before running")

    reg = p.add_argument_group("Registration options (only used with --register)")
    reg.add_argument("--secret-arn",    help="Secrets Manager ARN for Oracle credentials")
    reg.add_argument("--table",         help="Oracle table: SCHEMA.TABLE")
    reg.add_argument("--s3-path",       help="Target S3 path (AWS only): s3://bucket/prefix/")
    reg.add_argument("--mode",          default="full", choices=["full", "incremental"])
    reg.add_argument("--watermark-col", default="LAST_UPDATE_DATE")
    reg.add_argument("--watermark-fallback-col", default=None)
    reg.add_argument("--destination-cloud", default="aws", choices=["aws", "gcp"],
                     help="Cloud destination (default: aws)")
    # GCP-only registration args
    reg.add_argument("--gcs-path",        help="(GCP) Bronze target gs:// path")
    reg.add_argument("--gcp-secret-arn",  help="(GCP) Secrets Manager ARN for GCP SA JSON")
    reg.add_argument("--silver-gcs-path", help="(GCP) Silver Iceberg warehouse gs:// path")
    reg.add_argument("--silver-table",    help="(GCP) Silver Iceberg table name (default: source table leaf)")
    reg.add_argument("--silver-pk-col",   help="(GCP) Silver primary key column")
    reg.add_argument("--bq-project",      help="(GCP) BigQuery project for BigLake external table")
    reg.add_argument("--bq-dataset",      help="(GCP) BigQuery dataset for BigLake external table")
    reg.add_argument("--bq-location",     default="EU",
                     help="(GCP) BigQuery dataset location (default: EU)")

    p.add_argument("--env",   default="dev", choices=list(sn.ENVIRONMENTS))
    p.add_argument("--layer", default="bronze", choices=["bronze", "silver"],
                   help="Which layer to trigger (default: bronze)")

    args = p.parse_args()

    if args.register:
        if args.destination_cloud == "gcp":
            missing = [f for f, v in {
                "--secret-arn":      args.secret_arn,
                "--table":           args.table,
                "--gcs-path":        args.gcs_path,
                "--gcp-secret-arn":  args.gcp_secret_arn,
            }.items() if not v]
            if missing:
                p.error(f"--register --destination-cloud gcp requires {', '.join(missing)}")
        else:
            if not all([args.secret_arn, args.table, args.s3_path]):
                p.error("--register (aws) requires --secret-arn, --table, and --s3-path")

    # Resolve env-scoped names. Silver job name is finalised after config is
    # read (GCP pipelines route to the sibling silver-gcp Glue job).
    global CONFIG_TABLE, METRICS_TABLE, GLUE_JOB_NAME
    names         = sn.load(args.env)
    CONFIG_TABLE  = names.config_table
    METRICS_TABLE = names.metrics_table
    GLUE_JOB_NAME = names.resource("bronze-ingest")  # silver set post-config-read

    dynamo = boto3.resource("dynamodb", region_name=REGION)
    glue   = boto3.client("glue",       region_name=REGION)

    if args.register:
        register_pipeline(args, dynamo)

    print_step(f"Checking config for pipeline_id='{args.pipeline_id}' (layer={args.layer})...")
    cfg = check_config_exists(args.pipeline_id, dynamo)
    cloud = _cloud(cfg)
    print_step(f"  Cloud:  {cloud}")
    if args.layer == "silver":
        # Route to the sibling silver-gcp Glue job for cloud=gcp pipelines.
        if cloud == "gcp":
            # Was "-silver-transform-gcp-iceberg": two bugs in one name.
            # The prefix, and the slug — glue.tf names this job
            # "${local.name_prefix}-silver-transform-gcs-iceberg", gcs.
            GLUE_JOB_NAME = names.resource("silver-transform-gcs-iceberg")
        else:
            GLUE_JOB_NAME = names.resource("silver-transform")
        slv = cfg.get("silver", {})
        silver_loc = slv.get("s3_path") or slv.get("gcs_path", "?")
        silver_db  = slv.get("database") or f"{slv.get('bq_project', '?')}.{slv.get('bq_dataset', '?')}"
        print_step(f"  Silver table:  {silver_db}.{slv.get('table', '?')}")
        print_step(f"  Silver path:   {silver_loc}")
        print_step(f"  Last synced:   {slv.get('last_processed_date', '1970-01-01')}")
        print_step(f"  Primary keys:  {slv.get('primary_key_cols', [])}")
    else:
        # Bronze job is the same name for both clouds (flag-branches internally).
        target_path = cfg["target"].get("s3_path") or cfg["target"].get("gcs_path", "?")
        print_step(f"  Table:  {cfg['extract']['table']}")
        print_step(f"  Mode:   {cfg['extract']['mode']}")
        print_step(f"  Target: {target_path}")

    run_id = start_job(args.pipeline_id, args.env, glue, cfg)

    print_step(f"Polling every {POLL_INTERVAL}s (timeout {RUN_TIMEOUT}s)...")
    run = poll_run(run_id, glue)

    metrics = fetch_metrics(run_id, dynamo)
    print_summary(run, metrics, run_id)

    sys.exit(0 if run["JobRunState"] == "SUCCEEDED" else 1)


if __name__ == "__main__":
    main()
