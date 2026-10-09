#!/usr/bin/env python3
"""
analyze_pipeline.py — Pre-registration table analysis CLI.
Calls POST /analyze on the pipeline controller API (synchronous — returns immediately).

Usage:
    eval $(aws-runas -e pearson-esb-dev)
    python3 scripts/analyze_pipeline.py \\
        --secret-arn arn:aws:secretsmanager:eu-west-1:283556840805:secret:oracle-SPEA9I-tuoI9r \\
        --table      APPS.EGO_MTL_SY_ITEMS_EXT_B \\
        --env        dev

Exit codes: 0 = COMPLETED, 1 = FAILED
"""
import argparse
import json
import os
import ssl
import sys
import urllib.request
import urllib.error
from pathlib import Path

# Standalone CLIs get no conftest, so the libs path is set explicitly.
_LIBS = Path(__file__).resolve().parents[2] / "libs"
if str(_LIBS) not in sys.path:
    sys.path.insert(0, str(_LIBS))

import stack_naming as sn  # noqa: E402

# macOS Python doesn't use the system keychain; bypass verification for
# this internal AWS API Gateway endpoint (TLS is still encrypted).
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode    = ssl.CERT_NONE

API_ENDPOINTS = sn.api_endpoints()
# Shown in the copy-pasteable example the script prints on success.
_DEV_BRONZE = sn.load("dev").bucket("bronze")


def main():
    # Box-drawing output cannot encode to cp1252, which is what Windows
    # gives a non-terminal stdout -- declare it, as sync_pipelines.py does.
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    p = argparse.ArgumentParser(description="Analyze an Oracle table and get ingestion sizing recommendations.")
    p.add_argument("--secret-arn",    required=True, help="Secrets Manager ARN for Oracle credentials")
    p.add_argument("--table",         required=True, help="Oracle table: SCHEMA.TABLE")
    p.add_argument("--partition-col", default=None,  help="Partition column hint (skips metadata queries)")
    p.add_argument("--env",           default="dev", choices=list(sn.ENVIRONMENTS))
    args = p.parse_args()

    base_url = API_ENDPOINTS.get(args.env, "")
    if not base_url:
        print(f"ERROR: no API endpoint configured for env={args.env}", file=sys.stderr)
        sys.exit(1)

    url     = f"{base_url}/analyze"
    body = {"secret_arn": args.secret_arn, "table": args.table.upper()}
    if args.partition_col:
        body["partition_col"] = args.partition_col.upper()
    payload = json.dumps(body).encode()

    print(f"Analyzing {args.table.upper()} ...")
    print(f"POST {url}")

    try:
        req  = urllib.request.Request(url, data=payload, method="POST",
                                      headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=90, context=_SSL_CTX) as resp:
            result = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"HTTP {e.code}: {body}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Request failed: {e}", file=sys.stderr)
        sys.exit(1)

    sep = "─" * 64
    print(f"\n{sep}")
    print(f"  Table:   {result.get('table_name', args.table.upper())}")
    print(f"  Status:  {result.get('status', '?')}")

    if result.get("status") == "COMPLETED":
        stats = result.get("stats", {})
        rec   = result.get("recommendations", {})
        cands = result.get("partition_candidates", [])

        print(f"\n  ── Stats ──────────────────────────────────────────────")
        print(f"  {'row_count':<22} {stats.get('row_count', '?'):,}")
        print(f"  {'avg_row_bytes':<22} {stats.get('avg_row_bytes', '?')}")
        print(f"  {'total_size_gb':<22} {stats.get('total_size_gb', '?')} GiB")
        print(f"  {'stats_age_days':<22} {stats.get('stats_age_days', 'N/A')}")

        print(f"\n  ── Recommended config ────────────────────────────────")
        for k, v in rec.items():
            print(f"  {k:<22} {v}")

        if cands:
            print(f"\n  ── Partition candidates ({len(cands)} numeric NOT-NULL cols) ──")
            for c in cands[:5]:
                print(f"  {c['column']:<24} min={c['min']:,}  max={c['max']:,}  nulls={c['nulls']}")

        if rec.get("warning"):
            print(f"\n  WARNING: {rec['warning']}")

        print(f"\n  ── register_pipeline.py command ──────────────────────")
        col   = rec.get("jdbc_partition_col", "")
        parts = rec.get("jdbc_partitions", 1)
        table_short = args.table.split(".")[-1]
        print(f"  python3 scripts/register_pipeline.py \\")
        print(f"    --pipeline-id   pearson-product-{table_short} \\")
        print(f"    --secret-arn    {args.secret_arn} \\")
        print(f"    --table         {args.table.upper()} \\")
        print(f"    --worker-type   {rec.get('worker_type', 'G.1X')} \\")
        print(f"    --num-workers   {rec.get('num_workers', 2)} \\")
        if col:
            print(f"    --partition-col {col} \\")
            print(f"    --jdbc-partitions {parts} \\")
        print(f"    --mode          incremental \\")
        print(f"    --s3-path       s3://{_DEV_BRONZE}/product/{table_short}/ \\")
        print(f"    --domain        product \\")
        print(f"    --owner         raj.karunakaran@pearson.com \\")
        print(f"    --env           {args.env}")

    else:
        print(f"\n  Error: {result.get('error', result)}", file=sys.stderr)

    print(sep)
    sys.exit(0 if result.get("status") == "COMPLETED" else 1)


if __name__ == "__main__":
    main()
