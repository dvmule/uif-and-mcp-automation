#!/usr/bin/env python3
# Snapshot the Silver layer's Glue catalog (every database whose name contains
# 'silver') into src/lib/silver-catalog.json for the Source step's browser.
#
# This is the MVP bridge to the live read API: the UI needs "what tables exist
# across ALL Silver catalogs" (cross-domain sources and cross-catalog joins
# are expected), and a dev-time snapshot delivers that today. Re-run with
# fresh credentials whenever the catalog moves; the UI displays the capture
# timestamp so staleness is visible, and free-text entry always works for
# tables the snapshot doesn't know.
#
#   py stacks/echo/transform-ui/scripts/snapshot_silver_catalog.py

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import boto3

REGION = "eu-west-1"
OUT = Path(__file__).resolve().parents[1] / "src" / "lib" / "silver-catalog.json"


def main():
    glue = boto3.client("glue", region_name=REGION)
    dbs = []
    paginator = glue.get_paginator("get_databases")
    for page in paginator.paginate():
        for db in page["DatabaseList"]:
            if "silver" in db["Name"].lower():
                dbs.append(db["Name"])

    catalog = []
    for db in sorted(dbs):
        tables = []
        for page in glue.get_paginator("get_tables").paginate(DatabaseName=db):
            for t in page["TableList"]:
                cols = [{"name": c["Name"], "type": c.get("Type", "")}
                        for c in t.get("StorageDescriptor", {}).get("Columns", [])]
                tables.append({"name": t["Name"], "columns": cols})
        catalog.append({"database": db, "tables": tables})
        print(f"{db}: {len(tables)} tables")

    OUT.write_text(json.dumps({
        "capturedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "region": REGION,
        "databases": catalog,
    }, indent=1), encoding="utf-8")
    print(f"written: {OUT} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    sys.exit(main())
