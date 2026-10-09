# scheduler_dispatcher.py
# Triggered by EventBridge with {"frequency": ...} — see schedule_vocabulary
# for the authoritative set (that module is drift-tested against scheduler.tf).
# Gold is dispatched by the gold layer's own Lambda (ADR-0018), not here.
# Daily Bronze uses staggered slots: {"frequency": "daily", "daily_slot": 0..11}
# mapping to 05:30–11:00 UTC every 30 minutes.
# Silver reconcile uses the same daily_slot keys at 06:00–11:30 UTC (+30 min).

import boto3
import json
import logging
import os
from boto3.dynamodb.conditions import Attr

from echo_resources import echo, echo_legacy
from pipeline_profiles import resolve_glue, resolve_phase
from sizing_policy import clamp_glue_workers
from daily_schedule import pipeline_matches_daily_slot, pipeline_matches_reconcile_slot
from schedule_vocabulary import DISPATCHABLE_FREQUENCIES

FREQUENCY_HELP = " | ".join(DISPATCHABLE_FREQUENCIES)

logger = logging.getLogger()
logger.setLevel(logging.INFO)

ENV                          = os.environ.get("ENV", os.environ.get("ENVIRONMENT", "dev"))
REGION                       = os.environ.get("REGION",                       "eu-west-1")
GLUE_JOB_NAME                = os.environ.get("GLUE_JOB_NAME",                echo("bronze-ingest"))
RECONCILE_JOB_NAME           = os.environ.get("RECONCILE_JOB_NAME",           echo("silver-reconcile"))
RECONCILE_JOB_NAME_GCP       = os.environ.get("RECONCILE_JOB_NAME_GCP",       echo("silver-reconcile-gcs"))
RECONCILE_JOB_NAME_GCP_BQ    = os.environ.get("RECONCILE_JOB_NAME_GCP_BQ",    echo("silver-reconcile-bq"))
CONFIG_TABLE                 = os.environ.get("CONFIG_TABLE",                 echo_legacy("glue-job-config"))
DQ_CHECK_TABLE               = os.environ.get("DQ_CHECK_TABLE",               echo_legacy("dq-check-config"))
DQ_GLUE_JOB_NAME             = os.environ.get("DQ_GLUE_JOB_NAME",             echo("silver-quality-check"))


def _reconcile_job_name_for(cfg: dict) -> str:
    """Pick the reconcile Glue job by (destination.cloud, silver.write_mode).

    Bronze routing is handled inside the Bronze job itself (single Glue job
    branches on destination.cloud), but reconcile is three separate jobs
    because Silver lives in three different stores:
      - AWS (Iceberg via Glue Catalog)               → eds-{env}-silver-reconcile
      - GCP + iceberg_biglake (default)              → eds-{env}-silver-reconcile-gcp
      - GCP + bq_native                              → eds-{env}-silver-reconcile-gcp-bq
    """
    cloud = ((cfg.get("destination") or {}).get("cloud") or "aws").lower()
    if cloud != "gcp":
        return RECONCILE_JOB_NAME
    write_mode = ((cfg.get("silver") or {}).get("write_mode") or "iceberg_biglake").lower()
    return RECONCILE_JOB_NAME_GCP_BQ if write_mode == "bq_native" else RECONCILE_JOB_NAME_GCP

dynamo = boto3.resource("dynamodb", region_name=REGION)
glue   = boto3.client("glue",       region_name=REGION)


def _in_flight_ids(job_name: str, arg_key: str) -> set:
    """Return the set of identifier values (e.g. --pipeline_id) currently
    RUNNING or STOPPING for `job_name`. Used to skip starts when a prior run
    of the same identifier hasn't finished — prevents the EventBridge-tick-
    faster-than-runtime stacking that drove the SPEA9I Oracle incident.
    """
    in_flight = set()
    paginator = glue.get_paginator("get_job_runs")
    try:
        for page in paginator.paginate(JobName=job_name, MaxResults=100):
            for run in page.get("JobRuns", []):
                if run.get("JobRunState") not in ("RUNNING", "STOPPING"):
                    continue
                val = (run.get("Arguments") or {}).get(arg_key)
                if val:
                    in_flight.add(val)
    except Exception as e:
        # Fail-open: if we can't enumerate, don't block dispatch — log and
        # fall through. The original (pre-guard) behavior continues to apply.
        logger.warning(f"[IN-FLIGHT CHECK FAILED] job={job_name} key={arg_key} error={e}")
    return in_flight


def _dispatch_dq(frequency: str):
    """Scan dq_check_config for enabled checks whose schedule.frequency matches,
    and fire the eds-{env}-silver-quality-check Glue job for each.

    Safe-zero behavior: if the dq table doesn't exist yet (deploy ordering
    quirk), log a warning and return empty. The dispatcher must never fail
    just because the DQ module hasn't shipped.
    """
    triggered: list = []
    skipped:   list = []
    try:
        table = dynamo.Table(DQ_CHECK_TABLE)
        result = table.scan(
            FilterExpression=(
                Attr("enabled").eq(True) &
                Attr("version").eq("latest")
            )
        )
        items = result.get("Items", [])
    except dynamo.meta.client.exceptions.ResourceNotFoundException:
        logger.warning(f"[DQ] table {DQ_CHECK_TABLE} not found — skipping DQ dispatch")
        return triggered, skipped
    except Exception as e:
        logger.warning(f"[DQ] scan failed: {e} — skipping DQ dispatch")
        return triggered, skipped

    active = _in_flight_ids(DQ_GLUE_JOB_NAME, "--check_id")
    for cfg in items:
        check_id   = cfg.get("check_id")
        sched_freq = (cfg.get("schedule") or {}).get("frequency")
        if sched_freq != frequency:
            continue
        if check_id in active:
            logger.info(f"[DQ SKIPPED] {check_id} prior run still in-flight")
            skipped.append({"check_id": check_id, "reason": "in_flight"})
            continue
        try:
            response = glue.start_job_run(
                JobName=DQ_GLUE_JOB_NAME,
                Arguments={"--check_id": check_id},
                WorkerType="G.1X",
                NumberOfWorkers=2,
                Timeout=60,
            )
            triggered.append({"check_id": check_id, "run_id": response["JobRunId"]})
            logger.info(f"[DQ TRIGGERED] {check_id} run_id={response['JobRunId']} ({frequency})")
        except Exception as e:
            logger.error(f"[DQ FAILED TO TRIGGER] {check_id} error={e}")
            skipped.append({"check_id": check_id, "error": str(e)})
    return triggered, skipped


def _scan_config_items(table, filter_expression) -> list:
    """Paginated DynamoDB scan — single-page scans miss pipelines past 1 MB."""
    items: list = []
    kwargs: dict = {"FilterExpression": filter_expression}
    while True:
        result = table.scan(**kwargs)
        items.extend(result.get("Items", []))
        key = result.get("LastEvaluatedKey")
        if not key:
            break
        kwargs["ExclusiveStartKey"] = key
    return items


def _start_bronze_run(cfg: dict, active: set, triggered: list, skipped: list) -> None:
    """Start one Bronze Glue run; update `active` on success."""
    pipeline_id = cfg["pipeline_id"]
    if pipeline_id in active:
        logger.info(
            f"[SKIPPED] pipeline={pipeline_id} prior run still in-flight on {GLUE_JOB_NAME}"
        )
        skipped.append({"pipeline_id": pipeline_id, "reason": "in_flight"})
        return

    phase       = resolve_phase(cfg)
    g_phase     = resolve_glue(cfg, phase)
    worker_type = g_phase.get("worker_type",     "G.1X")
    num_workers = clamp_glue_workers(worker_type, int(g_phase.get("num_workers", 2)))
    timeout_min = int(g_phase.get("timeout_minutes", 60))

    try:
        response = glue.start_job_run(
            JobName=GLUE_JOB_NAME,
            Arguments={
                "--pipeline_id": pipeline_id,
                "--env":         ENV,
            },
            WorkerType=worker_type,
            NumberOfWorkers=num_workers,
            Timeout=timeout_min,
        )
        run_id = response["JobRunId"]
        active.add(pipeline_id)
        triggered.append({"pipeline_id": pipeline_id, "run_id": run_id, "phase": phase})
        logger.info(
            f"[TRIGGERED] pipeline={pipeline_id} run_id={run_id} "
            f"phase={phase} workers={num_workers}/{worker_type}"
        )
    except Exception as e:
        logger.error(f"[FAILED TO TRIGGER] pipeline={pipeline_id} error={e}")
        skipped.append({"pipeline_id": pipeline_id, "error": str(e)})


def handler(event, context):
    frequency = event.get("frequency")
    if not frequency:
        raise ValueError(
            f"Event must contain 'frequency' key ({FREQUENCY_HELP})"
        )

    daily_slot = event.get("daily_slot")
    if daily_slot is not None:
        daily_slot = int(daily_slot)

    logger.info(f"[DISPATCHER] frequency={frequency} daily_slot={daily_slot}")

    table = dynamo.Table(CONFIG_TABLE)
    triggered = []
    skipped   = []

    # ─── Handle reconciliation frequency (separate from schedule-based frequencies) ───
    if frequency == "reconcile":
        event_daily_slot = 0 if daily_slot is None else daily_slot

        items = _scan_config_items(
            table,
            Attr("enabled").eq(True)
            & Attr("version").eq("latest")
            & Attr("reconcile").exists(),
        )

        # Reconcile dispatches to one of two job names depending on
        # destination.cloud; build the in-flight set lazily and cache per job.
        active_by_job: dict[str, set] = {}

        for cfg in items:
            pipeline_id = cfg["pipeline_id"]
            if (cfg.get("metadata") or {}).get("deleted_at"):
                continue
            rec = cfg.get("reconcile", {})

            if not rec.get("enabled", False):
                logger.info(f"[SKIPPED] pipeline={pipeline_id} reconciliation disabled")
                continue

            schedule = cfg.get("schedule", {})
            if not pipeline_matches_reconcile_slot(schedule, event_daily_slot):
                continue

            phase        = resolve_phase(cfg)
            g_phase      = resolve_glue(cfg, phase)
            worker_type  = g_phase.get("worker_type",     "G.1X")
            num_workers  = int(g_phase.get("num_workers", 2))
            timeout_min  = int(g_phase.get("timeout_minutes", 60))

            reconcile_job_name = _reconcile_job_name_for(cfg)
            if reconcile_job_name not in active_by_job:
                active_by_job[reconcile_job_name] = _in_flight_ids(reconcile_job_name, "--pipeline_id")
            if pipeline_id in active_by_job[reconcile_job_name]:
                logger.info(
                    f"[SKIPPED] pipeline={pipeline_id} prior reconcile run still in-flight on {reconcile_job_name}"
                )
                skipped.append({"pipeline_id": pipeline_id, "reason": "in_flight"})
                continue

            try:
                response = glue.start_job_run(
                    JobName=reconcile_job_name,
                    Arguments={
                        "--pipeline_id": pipeline_id,
                        "--env":         ENV,
                    },
                    WorkerType=worker_type,
                    NumberOfWorkers=num_workers,
                    Timeout=timeout_min,
                )
                run_id = response["JobRunId"]
                triggered.append({
                    "pipeline_id": pipeline_id,
                    "run_id":      run_id,
                    "job_name":    reconcile_job_name,
                })
                logger.info(
                    f"[TRIGGERED] pipeline={pipeline_id} run_id={run_id} "
                    f"job={reconcile_job_name} (reconcile) phase={phase} "
                    f"workers={num_workers}/{worker_type}"
                )

            except Exception as e:
                logger.error(f"[FAILED TO TRIGGER] pipeline={pipeline_id} error={e}")
                skipped.append({"pipeline_id": pipeline_id, "error": str(e)})

        summary = {
            "frequency":       frequency,
            "daily_slot":      event_daily_slot,
            "triggered_count": len(triggered),
            "skipped_count":   len(skipped),
            "triggered":       triggered,
            "skipped":         skipped,
        }
        logger.info(f"[SUMMARY] {json.dumps(summary)}")
        return summary

    # ─── Handle schedule-based frequencies (30min / hourly / 4hr / daily / weekly) ───
    items = _scan_config_items(
        table,
        Attr("enabled").eq(True)
        & Attr("version").eq("latest")
        & Attr("schedule").exists(),
    )

    active = _in_flight_ids(GLUE_JOB_NAME, "--pipeline_id")

    # Daily stagger: EventBridge passes daily_slot 0..11. Legacy/manual
    # {"frequency":"daily"} without daily_slot → slot 0 only. Pipelines
    # without schedule.daily_slot are treated as slot 0 (daily_schedule).
    event_daily_slot = 0 if frequency == "daily" and daily_slot is None else daily_slot

    for cfg in items:
        pipeline_id = cfg["pipeline_id"]
        if (cfg.get("metadata") or {}).get("deleted_at"):
            continue
        schedule = cfg.get("schedule", {})

        if schedule.get("frequency") != frequency:
            continue

        if frequency == "daily":
            if event_daily_slot is None:
                logger.warning(
                    "[SKIPPED] pipeline=%s daily tick missing daily_slot in event",
                    pipeline_id,
                )
                skipped.append({"pipeline_id": pipeline_id, "reason": "no_event_slot"})
                continue
            if not pipeline_matches_daily_slot(schedule, event_daily_slot):
                continue

        _start_bronze_run(cfg, active, triggered, skipped)

    # ─── DQ checks fan out alongside Bronze on the same frequency tick ─
    dq_triggered, dq_skipped = _dispatch_dq(frequency)

    summary = {
        "frequency":          frequency,
        "daily_slot":         event_daily_slot if frequency == "daily" else None,
        "triggered_count":    len(triggered),
        "skipped_count":      len(skipped),
        "triggered":          triggered,
        "skipped":            skipped,
        "dq_triggered_count": len(dq_triggered),
        "dq_skipped_count":   len(dq_skipped),
        "dq_triggered":       dq_triggered,
        "dq_skipped":         dq_skipped,
    }
    logger.info(f"[SUMMARY] {json.dumps(summary)}")
    return summary
