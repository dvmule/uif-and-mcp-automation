#!/usr/bin/env python3
"""Repoint uif-echo-dev pipeline configs from legacy secret ARNs to UIF paths.

Scans uif-echo-dev-pipeline-config and replaces source.secret_arn (and GCP
destination credential ARNs) when the secret name matches a known legacy path.

Usage:
  python patch_uif_echo_pipeline_secret_arns.py              # dry-run
  python patch_uif_echo_pipeline_secret_arns.py --apply
"""

from __future__ import annotations

import argparse
import json
import sys

import boto3

REGION = "eu-west-1"
CONFIG_TABLE = "uif-echo-dev-pipeline-config"

LEGACY_TO_UIF = {
    "echo/dev/gip/db/tep": "uif/echo/dev/db/tep",
    "echo/dev/gip/db/mdm": "uif/echo/dev/db/mdm",
    "echo/dev/gip/cloud/gcp-rp-pdh": "uif/echo/dev/cloud/gcp-rp-pdh",
}


def _resolve_arn(sm, name: str) -> str:
    return sm.describe_secret(SecretId=name)["ARN"]


def _legacy_name(sm, arn: str) -> str | None:
    try:
        return sm.describe_secret(SecretId=arn)["Name"]
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--table", default=CONFIG_TABLE)
    ap.add_argument("--region", default=REGION)
    args = ap.parse_args()

    sm = boto3.client("secretsmanager", region_name=args.region)
    ddb = boto3.resource("dynamodb", region_name=args.region).Table(args.table)

    uif_arns = {legacy: _resolve_arn(sm, uif) for legacy, uif in LEGACY_TO_UIF.items()}

    scan = ddb.scan()
    items = scan.get("Items", [])
    changed = 0

    for item in items:
        pid = item.get("pipeline_id")
        updates: dict = {}

        src_arn = (item.get("source") or {}).get("secret_arn")
        if src_arn:
            legacy_name = _legacy_name(sm, src_arn)
            if legacy_name in uif_arns:
                updates["source"] = dict(item["source"])
                updates["source"]["secret_arn"] = uif_arns[legacy_name]

        dest = item.get("destination") or {}
        gcp_arn = dest.get("gcp_credentials_secret_arn")
        if gcp_arn:
            legacy_name = _legacy_name(sm, gcp_arn)
            if legacy_name in uif_arns:
                updates["destination"] = dict(dest)
                updates["destination"]["gcp_credentials_secret_arn"] = uif_arns[legacy_name]

        if not updates:
            continue

        print(json.dumps({"pipeline_id": pid, "updates": updates}, default=str))
        changed += 1
        if args.apply:
            merged = dict(item)
            merged.update(updates)
            ddb.put_item(Item=merged)

    if not args.apply and changed:
        print(f"\nDry-run: {changed} pipeline(s) would update — re-run with --apply", file=sys.stderr)
    elif args.apply:
        print(f"\nUpdated {changed} pipeline(s).", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
