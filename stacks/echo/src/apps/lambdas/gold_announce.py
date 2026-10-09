# gold_announce.py
# Tells the eventing layer a gold merge finished: one GoldMergeCompleted event
# per successful transform/reconcile run, for entities whose config opts in.

from __future__ import annotations

import json
import logging
import os

import boto3

from gold_resources import (
    event_bus,
    gold_config_table,
    gold_reconcile_job,
    gold_transform_job,
    writes_change_log,
)

logger = logging.getLogger()
logger.setLevel(logging.INFO)

REGION = os.environ.get("REGION", "eu-west-1")

DETAIL_TYPE = "GoldMergeCompleted"
CONFIG_VERSION = "latest"

# An explicit env var (set by Terraform) wins; otherwise gold_resources derives
# the name from ENV and RAISES when ENV is unset rather than guessing "dev".
_NAME_FOR = {
    "GOLD_JOB_NAME":           gold_transform_job,
    "GOLD_RECONCILE_JOB_NAME": gold_reconcile_job,
    "GOLD_CONFIG_TABLE":       gold_config_table,
    "EVENT_BUS_NAME":          event_bus,
}


def resource_name(env_var: str) -> str:
    return os.environ.get(env_var) or _NAME_FOR[env_var]()


def _clients(glue, ddb, events):
    return (glue or boto3.client("glue", region_name=REGION),
            ddb or boto3.resource("dynamodb", region_name=REGION),
            events or boto3.client("events", region_name=REGION))


def _skip(reason: str, **facts) -> dict:
    logger.info("[SKIP] %s %s", reason, facts)
    return {"announced": False, "skipped": reason, **facts}


def _opted_in(value) -> bool:
    """True only for an explicit opt-in; absent means do not announce."""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1", "yes")


def handler(event, context=None, glue=None, ddb=None, events=None) -> dict:
    glue, ddb, events = _clients(glue, ddb, events)

    detail = event.get("detail") or {}
    run_id = detail.get("jobRunId")
    job_name = detail.get("jobName")
    jobs = (resource_name("GOLD_JOB_NAME"),
            resource_name("GOLD_RECONCILE_JOB_NAME"))
    if not run_id or detail.get("state") != "SUCCEEDED" or job_name not in jobs:
        return _skip("not a succeeded gold-job event",
                     job_name=job_name, state=detail.get("state"))

    args = (glue.get_job_run(JobName=job_name, RunId=run_id)["JobRun"]
            .get("Arguments") or {})

    gold_id = args.get("--gold_id")
    if not gold_id:
        # A console-started run carries none, so no entity owns this merge.
        return _skip("run carries no --gold_id", run_id=run_id)

    # A rebuild, and a SILENT refresh, write no change log to publish from.
    # An EMIT refresh does — and skipping those was why a refresh-detected
    # change never reached a consumer on any channel.
    if not writes_change_log(args):
        return _skip("run writes no change log", gold_id=gold_id,
                     refresh_mode=args.get("--refresh_mode"))

    config_table = resource_name("GOLD_CONFIG_TABLE")
    item = ddb.Table(config_table).get_item(
        Key={"gold_id": gold_id, "version": CONFIG_VERSION}).get("Item")
    if not item:
        raise ValueError(
            f"no gold config {gold_id}/{CONFIG_VERSION} in {config_table} - "
            f"cannot resolve the domain for run {run_id}")
    if not _opted_in(item.get("announce")):
        return _skip("config does not opt in to announce", gold_id=gold_id)

    # NEVER fall back to gold_id: the two differ (marketing_copy's domain is
    # product), so guessing would publish an event no consumer rule matches.
    domain = item.get("domain")
    if not domain:
        raise ValueError(
            f"gold config {gold_id}/{CONFIG_VERSION} has no domain field")

    bus = resource_name("EVENT_BUS_NAME")
    announced = {"goldRunId": run_id, "goldId": gold_id,
                 "domain": domain, "status": "Success"}
    response = events.put_events(Entries=[{
        "EventBusName": bus,
        # The publishing job. Consumers must not filter on it: transform and
        # reconcile both announce, so a source filter drops every deletion.
        "Source": job_name,
        "DetailType": DETAIL_TYPE,
        "Detail": json.dumps(announced),
    }])
    if response.get("FailedEntryCount"):
        # Raise so the retry policy and DLQ see it. A swallowed PutEvents
        # failure is a merge the eventing layer never hears about.
        raise RuntimeError(
            f"PutEvents rejected the announcement for {gold_id}/{run_id}: "
            f"{response.get('Entries')}")

    logger.info("[ANNOUNCED] %s %s -> %s", DETAIL_TYPE, announced, bus)
    return {"announced": True, "bus": bus, "detail": announced}
