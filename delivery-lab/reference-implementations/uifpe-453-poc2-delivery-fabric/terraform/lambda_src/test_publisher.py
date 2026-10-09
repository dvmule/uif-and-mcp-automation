"""Test publisher — console-invokable event generator (POC2).

The lesson of POC1's testing (GitHub-Actions deploys, console-only humans):
the test tooling must live IN AWS. Invoke from the Lambda Test tab:

    {"gold_run_id": "fr-1", "events": 10}

Publishes N product events to the domain bus in batches of 10 and writes one
PUBLISHED ledger row per accepted event — the same two-step the real fabric
processor performs, ledger write immediately after the PutEvents response.
"""
import json
import os
import uuid
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

BUS = os.environ["BUS_NAME"]
DOMAIN = os.environ["DOMAIN"]
DETAIL_TYPE = os.environ["DETAIL_TYPE"]
SOURCE = f"{os.environ['PREFIX']}-test-publisher"

events_client = boto3.client("events")
ddb = boto3.resource("dynamodb")
t_publish = ddb.Table(os.environ["T_PUBLISH"])


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def handler(event, context):
    gold_run_id = event.get("gold_run_id") or f"fr-{uuid.uuid4().hex[:8]}"
    n = int(event.get("events", 1))
    event_run_id = f"er-{uuid.uuid4().hex[:8]}"

    published, skipped, failed = [], 0, []
    entries, ppids = [], []
    for i in range(1, n + 1):
        ppid = f"P2{i:07d}"
        ppids.append(ppid)
        entries.append({
            "Source": SOURCE,
            "DetailType": DETAIL_TYPE,
            "EventBusName": BUS,
            "Detail": json.dumps({"metadata": {"operation": "created"},
                                   "data": {"ppid": ppid}}),
        })

    for start in range(0, len(entries), 10):
        batch, batch_ppids = entries[start:start + 10], ppids[start:start + 10]
        resp = events_client.put_events(Entries=batch)
        for entry_resp, ppid in zip(resp["Entries"], batch_ppids):
            if entry_resp.get("ErrorCode"):
                failed.append({"ppid": ppid, "error": entry_resp["ErrorCode"]})
                continue
            eid = entry_resp["EventId"]
            try:
                t_publish.put_item(
                    ConditionExpression="attribute_not_exists(publish_id)",
                    Item={
                        "publish_id": f"{DOMAIN}#{gold_run_id}#{ppid}#created#v1",
                        "domain": DOMAIN, "gold_run_id": gold_run_id,
                        "primary_key": ppid, "operation": "created", "version": "v1",
                        "event_run_id": event_run_id, "status": "PUBLISHED",
                        "event_id": eid, "published_at": now_iso(),
                        "detail_type": DETAIL_TYPE,
                    })
                published.append({"ppid": ppid, "event_id": eid})
            except ClientError as e:
                if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
                    raise
                skipped += 1  # rerun with a reused gold_run_id — ledger kept

    out = {"gold_run_id": gold_run_id, "event_run_id": event_run_id,
           "published": len(published), "skipped_existing": skipped,
           "publish_failed": failed, "events": published}
    print(json.dumps(out))
    return out
