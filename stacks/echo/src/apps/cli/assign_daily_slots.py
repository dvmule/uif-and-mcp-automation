#!/usr/bin/env python3
"""Assign schedule.daily_slot to all enabled daily Bronze pipelines (round-robin)."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import boto3
from boto3.dynamodb.conditions import Attr

# The migration into stacks/echo moved libs from apps/lambda/ to src/libs/.
# This shim pointed at src/apps/lambda, which does not exist, so the import
# below raised ModuleNotFoundError and this script could not start at all.
_LIBS = Path(__file__).resolve().parents[2] / "libs"
if str(_LIBS) not in sys.path:
    sys.path.insert(0, str(_LIBS))

import stack_naming as sn  # noqa: E402

from daily_schedule import DAILY_SLOT_COUNT, slot_utc_hhmm  # noqa: E402

REGION = "eu-west-1"
# Was src/tasks/, which does not exist either. The stack root does.
_STACK_ROOT = Path(__file__).resolve().parents[3]
OUT = _STACK_ROOT / "tasks" / "_daily_slot_assignment.json"


def to_dynamo(obj):
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, int) and not isinstance(obj, bool):
        return Decimal(str(obj))
    if isinstance(obj, float):
        return Decimal(str(obj))
    if isinstance(obj, dict):
        return {k: to_dynamo(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [to_dynamo(v) for v in obj]
    return obj


def main() -> int:
    p = argparse.ArgumentParser(description="Assign daily_slot to daily pipelines")
    p.add_argument("--env", default="dev", choices=list(sn.ENVIRONMENTS))
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    table_name = sn.load(args.env).config_table
    table = boto3.resource("dynamodb", region_name=REGION).Table(table_name)

    items: list[dict] = []
    scan: dict = {"FilterExpression": Attr("version").eq("latest")}
    while True:
        resp = table.scan(**scan)
        items.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            break
        scan["ExclusiveStartKey"] = resp["LastEvaluatedKey"]

    daily = [
        it for it in items
        if it.get("enabled", True)
        and (it.get("schedule") or {}).get("frequency") == "daily"
        and not (it.get("metadata") or {}).get("deleted_at")
    ]
    daily.sort(key=lambda x: x["pipeline_id"])

    assignments = []
    slot_counts: Counter[int] = Counter()
    for i, item in enumerate(daily):
        pid = item["pipeline_id"]
        slot = i % DAILY_SLOT_COUNT
        slot_counts[slot] += 1
        h, m = slot_utc_hhmm(slot)
        assignments.append({
            "pipeline_id": pid,
            "daily_slot": slot,
            "utc_time": f"{h:02d}:{m:02d}",
        })
        if args.dry_run:
            print(f"DRY {pid} -> slot {slot} ({h:02d}:{m:02d} UTC)")
            continue

        cfg = json.loads(json.dumps(item, default=str))
        cfg.setdefault("schedule", {})["daily_slot"] = slot
        table.put_item(Item=to_dynamo(cfg))

    summary = {
        "assigned_at": datetime.now(timezone.utc).isoformat(),
        "table": table_name,
        "dry_run": args.dry_run,
        "daily_pipeline_count": len(daily),
        "slot_count": DAILY_SLOT_COUNT,
        "pipelines_per_slot": dict(sorted(slot_counts.items())),
        "assignments": assignments,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "daily": len(daily),
        "per_slot": dict(sorted(slot_counts.items())),
        "out": str(OUT),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
