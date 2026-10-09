"""Simulate the Event Fabric Processor against the LIVE poc stack.

  python publish_test_run.py --gold-run-id gr-live-1 --events 10 [--profile x] [--region eu-west-1]

Publishes N product.created.v1 events to uif-event-poc-product-bus in batches
of 10 and writes one PUBLISHED row per event to uif-event-poc-publish-metrics.
Then wait ~retry window and invoke the delivery-writer Lambda:

  aws lambda invoke --function-name uif-event-poc-delivery-writer \
      --cli-binary-format raw-in-base64-out \
      --payload '{"gold_run_id":"gr-live-1"}' out.json
"""
import argparse
import json
import uuid
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

PREFIX = "uif-event-poc"
DOMAIN = "product"
DETAIL_TYPE = "product.created.v1"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold-run-id", default=f"gr-live-{uuid.uuid4().hex[:8]}")
    ap.add_argument("--events", type=int, default=10)
    ap.add_argument("--profile", default=None)
    ap.add_argument("--region", default="eu-west-1")
    args = ap.parse_args()

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    events = session.client("events")
    table = session.resource("dynamodb").Table(f"{PREFIX}-publish-metrics")
    event_run_id = f"er-live-{uuid.uuid4().hex[:8]}"

    entries, ppids = [], []
    for i in range(1, args.events + 1):
        ppid = f"A10170329{i:04d}"
        ppids.append(ppid)
        entries.append({
            "Source": f"{PREFIX}-fabric-processor",
            "DetailType": DETAIL_TYPE,
            "EventBusName": f"{PREFIX}-{DOMAIN}-bus",
            "Detail": json.dumps({"metadata": {"operation": "created"},
                                   "data": {"ppid": ppid}}),
        })

    published = skipped = 0
    for start in range(0, len(entries), 10):
        batch, batch_ppids = entries[start:start + 10], ppids[start:start + 10]
        resp = events.put_events(Entries=batch)
        for entry_resp, ppid in zip(resp["Entries"], batch_ppids):
            if "ErrorCode" in entry_resp and entry_resp.get("ErrorCode"):
                print(f"PUBLISH_FAILED {ppid}: {entry_resp['ErrorCode']}")
                continue
            # Conditional: re-running with the same --gold-run-id must not
            # overwrite ledger rows from the earlier run (the event WAS still
            # re-published to the bus above — use a fresh run id for clean tests).
            try:
                table.put_item(
                    ConditionExpression="attribute_not_exists(publish_id)",
                    Item={
                        "publish_id": f"{DOMAIN}#{args.gold_run_id}#{ppid}#created#v1",
                        "domain": DOMAIN, "gold_run_id": args.gold_run_id,
                        "primary_key": ppid, "operation": "created", "version": "v1",
                        "event_run_id": event_run_id, "status": "PUBLISHED",
                        "event_id": entry_resp["EventId"],
                        "published_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                        "detail_type": DETAIL_TYPE,
                    })
                published += 1
            except ClientError as e:
                if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
                    raise
                skipped += 1

    print(f"gold_run_id={args.gold_run_id} event_run_id={event_run_id} "
          f"published={published} skipped_existing={skipped}")
    if skipped:
        print("WARNING: ledger rows already existed for this gold-run-id; "
              "they were kept (this rerun's bus events are NOT in the ledger).")
    print(f"Now wait for the retry window, then invoke {PREFIX}-delivery-writer "
          f"with {{\"gold_run_id\": \"{args.gold_run_id}\"}}")


if __name__ == "__main__":
    main()
