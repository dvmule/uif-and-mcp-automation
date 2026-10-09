#!/usr/bin/env python3
"""
setup_new_pipeline.py — Interactive pipeline setup orchestrator.

Guides user through: connection selection → table analysis → config recommendation →
approval → deployment.

Usage:
    eval $(aws-runas -e pearson-esb-dev)
    python3 scripts/setup_new_pipeline.py
"""

import argparse
import boto3
import json
import ssl
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from pathlib import Path

# Standalone CLIs get no conftest, so the libs path is set explicitly.
_LIBS = Path(__file__).resolve().parents[2] / "libs"
if str(_LIBS) not in sys.path:
    sys.path.insert(0, str(_LIBS))

import stack_naming as sn  # noqa: E402

REGION = "eu-west-1"
# Was a hand-written map with no uat entry.
API_ENDPOINTS = sn.api_endpoints()

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE


def input_with_default(prompt: str, default: Optional[str] = None) -> str:
    """Prompt with optional default value."""
    if default:
        full_prompt = f"{prompt} [{default}]: "
    else:
        full_prompt = f"{prompt}: "

    result = input(full_prompt).strip()
    if not result and default:
        return default
    return result


def list_secrets() -> Dict[str, str]:
    """List all Oracle secrets in Secrets Manager."""
    sm = boto3.client("secretsmanager", region_name=REGION)
    try:
        resp = sm.list_secrets(Filters=[{"Key": "name", "Values": ["oracle"]}])
        secrets = {s["Name"]: s.get("ARN", "") for s in resp.get("SecretList", [])}
        return secrets
    except Exception as e:
        print(f"ERROR: Failed to list secrets: {e}", file=sys.stderr)
        return {}


def select_connection() -> tuple[str, str]:
    """Prompt user to select or enter a connection (secret ARN)."""
    secrets = list_secrets()

    if not secrets:
        print("No Oracle secrets found in Secrets Manager.")
        secret_arn = input_with_default("Enter secret ARN manually")
        return "custom", secret_arn

    print("\n─── Available connections ───")
    for i, (name, arn) in enumerate(secrets.items(), 1):
        print(f"  [{i}] {name}")
        print(f"      {arn}")

    choice = input_with_default("\nSelect connection (number or ARN)", "1")

    if choice.isdigit():
        idx = int(choice) - 1
        names = list(secrets.keys())
        if 0 <= idx < len(names):
            return names[idx], secrets[names[idx]]

    # Assume it's an ARN
    if choice.startswith("arn:"):
        return "custom", choice

    print("ERROR: Invalid selection", file=sys.stderr)
    sys.exit(1)


def analyze_table(table: str, secret_arn: str, env: str) -> Dict[str, Any]:
    """Call POST /analyze to get table sizing recommendations."""
    base_url = API_ENDPOINTS.get(env, "")
    if not base_url:
        print(f"ERROR: no API endpoint for env={env}", file=sys.stderr)
        sys.exit(1)

    url = f"{base_url}/analyze"
    body = {"secret_arn": secret_arn, "table": table.upper()}
    payload = json.dumps(body).encode()

    print(f"\n→ Analyzing {table.upper()} ...")

    try:
        req = urllib.request.Request(url, data=payload, method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=90, context=_SSL_CTX) as resp:
            result = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body_text = e.read().decode()
        print(f"HTTP {e.code}: {body_text}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Request failed: {e}", file=sys.stderr)
        sys.exit(1)

    if result.get("status") != "COMPLETED":
        error = result.get("error", json.dumps(result, indent=2))
        print(f"Analysis failed: {error}", file=sys.stderr)
        sys.exit(1)

    return result


def format_recommendations(analysis: Dict[str, Any]) -> None:
    """Pretty-print analysis results and recommendations."""
    sep = "─" * 70
    print(f"\n{sep}")
    print("ANALYSIS RESULTS")
    print(sep)

    stats = analysis.get("stats", {})
    rec = analysis.get("recommendations", {})
    cands = analysis.get("partition_candidates", [])

    print(f"\n📊 Table Statistics:")
    print(f"   Row count:        {stats.get('row_count', 0):,}")
    print(f"   Avg row size:     {stats.get('avg_row_bytes', 0)} bytes")
    print(f"   Total size:       {stats.get('total_size_gb', 0):.2f} GiB")
    print(f"   Stats age:        {stats.get('stats_age_days', 'N/A')} days")

    print(f"\n⚙️  Recommended Configuration:")
    print(f"   Strategy:         {rec.get('strategy', 'unknown')}")
    print(f"   Worker type:      {rec.get('worker_type', 'G.1X')}")
    print(f"   Num workers:      {rec.get('num_workers', 2)}")
    print(f"   JDBC partitions:  {rec.get('jdbc_partitions', 1)}")
    print(f"   Partition col:    {rec.get('jdbc_partition_col', 'N/A')}")

    if cands:
        print(f"\n📈 Partition Candidates ({len(cands)} numeric columns):")
        for c in cands[:3]:
            print(f"   {c['column']:<20} min={c['min']:,}  max={c['max']:,}  nulls={c['nulls']}")

    if rec.get("warning"):
        print(f"\n⚠️  {rec['warning']}")

    print(f"\n{sep}\n")


def get_pipeline_config(analysis: Dict[str, Any], table: str, secret_arn: str,
                       env: str) -> Dict[str, Any]:
    """Prompt user for pipeline configuration, using analysis recommendations."""
    rec = analysis.get("recommendations", {})
    schema, table_name = table.upper().split(".")
    table_short = table_name.lower()

    print("─ Pipeline Configuration ─\n")

    pipeline_id = input_with_default(
        "Pipeline ID (e.g., pearson-product-items)",
        f"pearson-{schema.lower()}-{table_short}"
    )

    domain = input_with_default("Domain (e.g., product, finance)", "product")

    owner = input_with_default("Owner email", "raj.karunakaran@pearson.com")

    s3_path = input_with_default(
        "S3 path (s3://bucket/prefix/)",
        f"s3://{sn.load(env).bucket('bronze')}/{domain.lower()}/{table_short}/"
    )

    if not s3_path.endswith("/"):
        s3_path += "/"

    # Extraction config
    print(f"\n─ Extraction Config ─\n")

    mode = input_with_default("Mode (full/incremental)", "incremental")
    watermark_col = input_with_default("Watermark column", "LAST_UPDATE_DATE")
    watermark_fallback_col = input_with_default(
        "Watermark fallback column (when primary is NULL, or blank to skip)",
        "CREATION_DATE" if mode == "incremental" else "",
    ).strip() or None

    partition_col = rec.get("jdbc_partition_col")
    if partition_col:
        use_partition = input_with_default(f"Use {partition_col} for JDBC partitioning? (y/n)", "y")
        if use_partition.lower() != "y":
            partition_col = None

    # Worker config
    print(f"\n─ Worker Config ─\n")

    worker_type = input_with_default(
        "Worker type (G.1X/G.2X/G.4X)",
        rec.get("worker_type", "G.1X")
    )

    num_workers = input_with_default(
        "Number of workers",
        str(rec.get("num_workers", 2))
    )

    # Silver config (optional)
    print(f"\n─ Silver Iceberg Config (optional) ─\n")

    enable_silver = input_with_default("Enable Silver layer? (y/n)", "n")
    silver_config = None

    if enable_silver.lower() == "y":
        silver_db = input_with_default("Silver database name", f"silver_{domain}")
        silver_table = input_with_default("Silver table name", table_short)
        silver_s3 = input_with_default(
            "Silver S3 path",
            f"s3://{sn.load(env).bucket('silver')}/{domain.lower()}/{table_short}/"
        )
        if not silver_s3.endswith("/"):
            silver_s3 += "/"

        silver_config = {
            "enabled": True,
            "database": silver_db,
            "table": silver_table,
            "s3_path": silver_s3,
            "primary_key_cols": [],
        }

    # Bronze → GCS replication (optional)
    print(f"\n─ Bronze → GCS Replication (optional) ─\n")

    enable_gcs = input_with_default("Replicate Bronze to GCS? (y/n)", "n")
    replication_config = None

    if enable_gcs.lower() == "y":
        gcs_bucket = input_with_default("GCS bucket name")
        gcs_prefix = input_with_default(
            "GCS object prefix",
            f"{domain.lower()}/{table_short}/"
        )
        if gcs_prefix and not gcs_prefix.endswith("/"):
            gcs_prefix += "/"
        gcs_secret_arn = input_with_default(
            "Secrets Manager ARN holding the GCP service account JSON"
        )
        replication_config = {
            "gcs": {
                "enabled":                True,
                "bucket":                 gcs_bucket,
                "prefix":                 gcs_prefix,
                "credentials_secret_arn": gcs_secret_arn,
            }
        }

    # Reconciliation config (optional)
    print(f"\n─ Reconciliation Config (optional) ─\n")

    enable_reconcile = input_with_default("Enable reconciliation? (y/n)", "n")
    reconcile_config = None

    if enable_reconcile.lower() == "y":
        reconcile_pk_col = input_with_default(
            "PK partition column",
            partition_col or ""
        )
        reconcile_config = {
            "enabled": True,
            "pk_partition_col": reconcile_pk_col or None,
            "max_deletes_per_run": int(input_with_default("Max deletes per run", "5000")),
            "dry_run": True,  # Always start in dry-run
        }

    config = {
        "pipeline_id": pipeline_id,
        "secret_arn": secret_arn,
        "table": table.upper(),
        "domain": domain,
        "owner": owner,
        "s3_path": s3_path,
        "mode": mode,
        "watermark_col": watermark_col,
        "watermark_fallback_col": watermark_fallback_col,
        "partition_col": partition_col,
        "worker_type": worker_type,
        "num_workers": int(num_workers),
        "env": env,
        "silver": silver_config,
        "reconcile": reconcile_config,
        "replication": replication_config,
    }

    return config


def build_dynamodb_item(config: Dict[str, Any]) -> Dict[str, Any]:
    """Build DynamoDB pipeline config item."""
    columns = []  # Could be configured interactively if needed

    item = {
        "pipeline_id": config["pipeline_id"],
        "version": "latest",
        "enabled": True,
        "source": {
            "type": "oracle",
            "secret_arn": config["secret_arn"],
            "jdbc_extra_options": "",
        },
        "extract": {
            "table": config["table"],
            "mode": config["mode"],
            "watermark_col": config["watermark_col"],
            "watermark_fallback_col": config.get("watermark_fallback_col"),
            "watermark_value": "1970-01-01T00:00:00",
            "jdbc_partitions": 4,  # Sensible default
            "jdbc_partition_col": config["partition_col"],
            "jdbc_lower_bound": 1,
            "jdbc_upper_bound": 10_000_000,
            "columns": columns or None,
            "custom_sql": None,
        },
        "target": {
            "s3_path": config["s3_path"],
            "partition_cols": ["_extract_date"],
            "compression": "snappy",
        },
        "schedule": {
            "frequency": "daily",
        },
        "quality": {
            "not_null_cols": [],
            "min_rows_expected": 0,
            "reject_on_nulls": False,
        },
        "glue": {
            "worker_type": config["worker_type"],
            "num_workers": config["num_workers"],
            "timeout_minutes": 120,
            "max_retries": 2,
        },
        "metadata": {
            "owner": config["owner"],
            "domain": config["domain"],
            "registered_at": datetime.now(timezone.utc).isoformat(),
        },
    }

    if config.get("silver"):
        item["silver"] = config["silver"]

    if config.get("reconcile"):
        item["reconcile"] = config["reconcile"]

    if config.get("replication"):
        item["replication"] = config["replication"]

    return item


def register_pipeline(item: Dict[str, Any], env: str) -> bool:
    """Register pipeline via API."""
    base_url = API_ENDPOINTS.get(env, "")
    if not base_url:
        print(f"ERROR: no API endpoint for env={env}", file=sys.stderr)
        return False

    url = f"{base_url}/pipelines"
    payload = json.dumps(item).encode()

    print(f"\n→ Registering pipeline via API...")
    print(f"POST {url}")

    try:
        req = urllib.request.Request(url, data=payload, method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30, context=_SSL_CTX) as resp:
            result = json.loads(resp.read())
            pipeline_id = result.get("pipeline_id")
            print(f"✓ Registered pipeline: {pipeline_id}")
            return True
    except urllib.error.HTTPError as e:
        body_text = e.read().decode()
        print(f"HTTP {e.code}: {body_text}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"Request failed: {e}", file=sys.stderr)
        return False


def confirm_config(item: Dict[str, Any]) -> bool:
    """Ask user to confirm configuration before deployment."""
    sep = "─" * 70
    print(f"\n{sep}")
    print("CONFIRM DEPLOYMENT")
    print(sep)
    print("\nConfiguration to be deployed:")
    print(json.dumps(item, indent=2, default=str))
    print(f"\n{sep}")

    confirm = input_with_default("Deploy? (y/n)", "n")
    return confirm.lower() == "y"


def main():
    # Box-drawing output cannot encode to cp1252, which is what Windows
    # gives a non-terminal stdout -- declare it, as sync_pipelines.py does.
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    p = argparse.ArgumentParser(description="Interactive new pipeline setup")
    p.add_argument("--env", default="dev", choices=list(sn.ENVIRONMENTS),
                   help="Target environment")
    p.add_argument("--table", default=None, help="Bypass interactive: SCHEMA.TABLE")
    p.add_argument("--secret-arn", default=None, help="Bypass interactive: secret ARN")
    p.add_argument("--auto-confirm", action="store_true",
                   help="Skip confirmation (non-interactive mode)")
    args = p.parse_args()

    print("=" * 70)
    print("NEW PIPELINE SETUP")
    print("=" * 70)

    # Step 1: Select connection
    if args.secret_arn:
        secret_arn = args.secret_arn
        conn_name = "specified"
    else:
        conn_name, secret_arn = select_connection()

    print(f"✓ Connection: {conn_name}")

    # Step 2: Select table
    if args.table:
        table = args.table.upper()
    else:
        table = input_with_default("Oracle table (SCHEMA.TABLE)").upper()
        if "." not in table:
            print("ERROR: Table must be in SCHEMA.TABLE format", file=sys.stderr)
            sys.exit(1)

    print(f"✓ Table: {table}")

    # Step 3: Analyze table
    analysis = analyze_table(table, secret_arn, args.env)
    format_recommendations(analysis)

    # Step 4: Get pipeline config from user
    config = get_pipeline_config(analysis, table, secret_arn, args.env)

    # Step 5: Build DynamoDB item
    item = build_dynamodb_item(config)

    # Step 6: Confirm and deploy
    if args.auto_confirm or confirm_config(item):
        if register_pipeline(item, args.env):
            print(f"\n✓ Pipeline {config['pipeline_id']} registered successfully!")
            print(f"  Next steps:")
            print(f"    1. Run first ingestion: python3 scripts/adhoc_run.py \\")
            print(f"       --pipeline-id {config['pipeline_id']} --env {args.env}")
            print(f"    2. Check results in S3: {config['s3_path']}")
            sys.exit(0)

    print("Deployment cancelled.")
    sys.exit(1)


if __name__ == "__main__":
    main()
