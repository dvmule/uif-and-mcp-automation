#!/usr/bin/env python3
# set_pipeline_frequency.py
# Update schedule.frequency for one or more existing Bronze pipeline configs
# in DynamoDB. Surgical UpdateItem on a single nested field — does NOT touch
# any other fields (avoids the "PUT is full-replace" footgun).
#
# Usage:
#   python set_pipeline_frequency.py \
#     --pipeline-ids mtl_system_items_b,bom_components_b \
#     --frequency 30min \
#     --env dev

import argparse
import sys
from decimal import Decimal
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

# The migration into stacks/echo moved libs from apps/lambda/ to src/libs/.
# Standalone CLIs get no conftest, so they need the path set explicitly.
_LIBS = Path(__file__).resolve().parents[2] / "libs"
if str(_LIBS) not in sys.path:
    sys.path.insert(0, str(_LIBS))

import stack_naming as sn  # noqa: E402

from daily_schedule import DAILY_SLOT_COUNT  # noqa: E402
from schedule_vocabulary import BRONZE_SILVER_FREQUENCIES  # noqa: E402

REGION = "eu-west-1"

# Was a hand-written list that omitted "4hr" — so this tool could not set the
# cadence most enabled pipelines use, nor repair one set to an undispatched
# value. Both lists now come from the vocabulary.
VALID_FREQUENCIES = list(BRONZE_SILVER_FREQUENCIES)


def update_frequency(table, pipeline_id: str, frequency: str) -> dict:
    if frequency == "daily":
        slot = abs(hash(pipeline_id)) % DAILY_SLOT_COUNT
        return table.update_item(
            Key={"pipeline_id": pipeline_id, "version": "latest"},
            UpdateExpression="SET #s.#f = :v, #s.#ds = :slot",
            ConditionExpression="attribute_exists(pipeline_id)",
            ExpressionAttributeNames={
                "#s": "schedule",
                "#f": "frequency",
                "#ds": "daily_slot",
            },
            ExpressionAttributeValues={":v": frequency, ":slot": Decimal(str(slot))},
            ReturnValues="ALL_NEW",
        )
    return table.update_item(
        Key={"pipeline_id": pipeline_id, "version": "latest"},
        UpdateExpression="SET #s.#f = :v REMOVE #s.#ds",
        ConditionExpression="attribute_exists(pipeline_id)",
        ExpressionAttributeNames={
            "#s": "schedule",
            "#f": "frequency",
            "#ds": "daily_slot",
        },
        ExpressionAttributeValues={":v": frequency},
        ReturnValues="ALL_NEW",
    )


def main():
    # Box-drawing output cannot encode to cp1252, which is what Windows
    # gives a non-terminal stdout -- declare it, as sync_pipelines.py does.
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    p = argparse.ArgumentParser(
        description="Update schedule.frequency for existing Bronze pipelines."
    )
    p.add_argument("--pipeline-ids", required=True,
                   help="Comma-separated pipeline_ids to update")
    p.add_argument("--frequency", default="30min", choices=VALID_FREQUENCIES,
                   help="New frequency value (default: 30min)")
    p.add_argument("--env", default="dev", choices=list(sn.ENVIRONMENTS),
                   help="Target environment (default: dev)")
    args = p.parse_args()

    ids = [x.strip() for x in args.pipeline_ids.split(",") if x.strip()]
    if not ids:
        raise SystemExit("No pipeline_ids provided")

    table_name = sn.load(args.env).config_table
    table = boto3.resource("dynamodb", region_name=REGION).Table(table_name)

    print(f"Updating {len(ids)} pipeline(s) in {table_name} → schedule.frequency = {args.frequency!r}")
    ok, failed = [], []
    for pid in ids:
        try:
            resp = update_frequency(table, pid, args.frequency)
            sched = resp["Attributes"].get("schedule", {})
            ok.append(pid)
            print(f"  ✓ {pid}: schedule = {sched}")
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            failed.append((pid, code))
            print(f"  ✗ {pid}: {code} — {e}")

    print(f"\nDone. updated={len(ok)} failed={len(failed)}")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
