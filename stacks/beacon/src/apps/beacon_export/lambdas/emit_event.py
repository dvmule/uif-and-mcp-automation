# emit_event.py — Step Functions "send event" stage: publish the batch
# outcome to EventBridge so downstream consumers (notifications, dependent
# pipelines, the future JSM hook) react without polling.
#
# Event (state input):
#   {"job_id": "...", "batch_id": "...", "status": "succeeded" | "failed",
#    "delivered": int?, "error": "..."?}
#
# Env: BEACON_EVENT_BUS — bus name (default "default").

from __future__ import annotations

import json
import os

from beacon_export.lambdas.job_identity import resolve_job_id

SOURCE = "uif.beacon"


def handler(event: dict, context=None, *, events_client=None) -> dict:
    if events_client is None:
        import boto3
        events_client = boto3.client("events")

    status = event.get("status", "unknown")
    detail_type = ("beacon.export.completed" if status == "succeeded"
                   else "beacon.export.failed")
    detail = {k: event[k] for k in
              ("job_id", "batch_id", "status", "delivered", "error")
              if event.get(k) is not None}
    detail["job_id"] = resolve_job_id(event)  # UIFPE-200: never "unknown" when derivable

    response = events_client.put_events(Entries=[{
        "Source": SOURCE,
        "DetailType": detail_type,
        "Detail": json.dumps(detail),
        "EventBusName": os.environ.get("BEACON_EVENT_BUS", "default"),
    }])
    if response.get("FailedEntryCount"):
        raise RuntimeError(f"EventBridge rejected the event: {response['Entries']}")
    return {"emitted": detail_type, "event_id": response["Entries"][0].get("EventId")}
