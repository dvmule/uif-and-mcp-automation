#!/usr/bin/env python3
"""Copy legacy echo/dev secrets to UIF standard paths (identity-secrets.md).

Creates (or updates) Secrets Manager entries at uif/echo/dev/... from the
live echo/dev/gip/... and echo/dev/ui/login sources. Tags are preserved so
GET /connections (app=echo) continues to discover them.

Usage (requires AWS credentials with secretsmanager read+write):
  python copy_uif_echo_secrets.py              # dry-run
  python copy_uif_echo_secrets.py --apply      # create/update targets

After --apply, run patch_uif_echo_pipeline_secret_arns.py to repoint pipeline
configs in uif-echo-dev-pipeline-config.
"""

from __future__ import annotations

import argparse
import json
import sys

import boto3
from botocore.exceptions import ClientError

REGION = "eu-west-1"

# legacy path → UIF standard path (no gip segment)
MIGRATIONS: list[tuple[str, str]] = [
    ("echo/dev/gip/db/tep", "uif/echo/dev/db/tep"),
    ("echo/dev/gip/db/mdm", "uif/echo/dev/db/mdm"),
    ("echo/dev/gip/cloud/gcp-rp-pdh", "uif/echo/dev/cloud/gcp-rp-pdh"),
    ("echo/dev/ui/login", "uif/echo/dev/ui/login"),
]

# Ensure new secrets remain visible to list_connections (tag app=echo).
REQUIRED_TAGS = {"app": "echo"}


def _tags(client, secret_id: str) -> dict[str, str]:
    resp = client.describe_secret(SecretId=secret_id)
    return {t["Key"]: t["Value"] for t in resp.get("Tags") or []}


def _copy_one(client, src: str, dst: str, apply: bool) -> dict:
    try:
        value = client.get_secret_value(SecretId=src)["SecretString"]
    except ClientError as exc:
        return {"src": src, "dst": dst, "status": "error", "detail": str(exc)}

    tags = _tags(client, src)
    for k, v in REQUIRED_TAGS.items():
        tags.setdefault(k, v)

    tag_list = [{"Key": k, "Value": v} for k, v in sorted(tags.items())]

    if not apply:
        return {"src": src, "dst": dst, "status": "would_copy", "tags": tags}

    try:
        client.create_secret(
            Name=dst,
            Description=f"UIF path for {src}",
            SecretString=value,
            Tags=tag_list,
        )
        return {"src": src, "dst": dst, "status": "created"}
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ResourceExistsException":
            return {"src": src, "dst": dst, "status": "error", "detail": str(exc)}
        client.put_secret_value(SecretId=dst, SecretString=value)
        return {"src": src, "dst": dst, "status": "updated"}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="Write secrets (default is dry-run)")
    ap.add_argument("--region", default=REGION)
    args = ap.parse_args()

    client = boto3.client("secretsmanager", region_name=args.region)
    results = [_copy_one(client, src, dst, args.apply) for src, dst in MIGRATIONS]

    for row in results:
        print(json.dumps(row, sort_keys=True))

    errors = [r for r in results if r.get("status") == "error"]
    if errors:
        return 1
    if not args.apply:
        print("\nDry-run only — re-run with --apply to create/update secrets.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
