"""Thin trigger: start the Event Fabric Processor for a completed gold run.

Trigger contract (2026-09-03 requirement change): the caller announces only
the run -

    { "run_id": "jr_...", "changedBy": "ops", "goldId": "product" }

- ("domain" is accepted as a legacy alias for the entity when goldId is
  absent; the two named the same thing for every pre-UIFPE-474 publisher)

- and every remaining attribute is DERIVED, not trusted from the payload:

  * the pipeline's master config comes from the uif-event-<env>-pipeline-configs
    DynamoDB table (key: pipelineid = "<PIPELINE_ID_PREFIX>-<goldId>",
    version = "latest"): processor job, delta table + columns, publish ops,
    skip rules, worker sizing;
  * the run's operation counts (insert/update/delete/total) come from the
    gold change log itself (config delta.table, e.g. product_changes),
    queried per run_id via Athena - the counts that drive the skip rules,
    the sizing tier, and the metrics record's gold_delta_count/ops_triggered.

The gold transform's GoldMergeCompleted EventBridge event funnels into the
SAME flow (2026-09-16 contract: Source = the gold Glue job's name, bus
uif-event-<env>-bus, Detail {"goldRunId", "goldId", "domain", "status"}
- goldId names the gold ENTITY and is not the domain (marketing_copy's
domain is product); this handler routes on goldId for exactly that
reason (UIFPE-474) - a domain key would hand a second entity in an
already-publishing domain the FIRST entity's tier, job and skip rules,
silently): goldRunId/goldId/domain come from detail, changedBy from the event
source, non-Success merges are skipped, and the delta counts are derived
from the change log - the event carries no counts at all.

A bare {"run_id": ...} payload WITHOUT goldId or domain remains the raw operator
tool: it starts the default job directly - no config, no Athena, no
metrics record.

The hand-off is asynchronous end to end: the caller does not wait for the
Glue job - StartJobRun returns the JobRunId immediately, the run's
IN_PROGRESS row is written to uif-event-<env>-pipeline-metrics (Table 2:
key event_run_id = the JobRunId, mode = INCREMENTAL), and the function
returns. The processor flips it STARTED and terminal
(SUCCEEDED/PARTIAL_FAILURE/FAILED) and logs per-event rows to
uif-event-<env>-publish-metrics (Table 3). GoldMergeCompleted deliveries
are idempotency-checked against Table 2's sparse gsi_gold_run (skip when
the gold run already has an IN_PROGRESS/STARTED/SUCCEEDED processor row);
manual announcements bypass the check deliberately - re-announcing a run
is the operator replay tool, and counts prior attempts as replay_count.

PLANNED: this Lambda will also invoke the API registry; the call site is
marked below.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

glue = boto3.client("glue")
athena = boto3.client("athena")
_dynamodb = boto3.resource("dynamodb")

EVENT_FABRIC_GLUE_JOB = os.environ["EVENT_FABRIC_GLUE_JOB"]
PIPELINE_CONFIGS_TABLE = os.environ.get("PIPELINE_CONFIGS_TABLE", "")
PIPELINE_METRICS_TABLE = os.environ.get("PIPELINE_METRICS_TABLE", "")
PIPELINE_ID_PREFIX = os.environ.get("PIPELINE_ID_PREFIX", "")
GLUE_DATABASE = os.environ.get("GLUE_DATABASE", "")
ATHENA_WORKGROUP = os.environ.get("ATHENA_WORKGROUP", "primary")
ATHENA_OUTPUT = os.environ.get("ATHENA_OUTPUT", "")
METRICS_TTL_DAYS = int(os.environ.get("METRICS_TTL_DAYS", "90"))

configs_table = _dynamodb.Table(PIPELINE_CONFIGS_TABLE) if PIPELINE_CONFIGS_TABLE else None
metrics_table = _dynamodb.Table(PIPELINE_METRICS_TABLE) if PIPELINE_METRICS_TABLE else None

GOLD_MERGE_DETAIL_TYPE = "GoldMergeCompleted"
CONFIG_VERSION = "latest"
PIPELINE_MODE = "INCREMENTAL"
# Table 2 statuses that mean a gold run is already handled (idempotency).
ACTIVE_OR_DONE = {"IN_PROGRESS", "STARTED", "SUCCEEDED"}
# publish_ops values -> the derived-summary key carrying that op's row count.
# REFRESH belongs here: a targeted column refresh in `emit` mode appends real
# change-log rows (generic_gold_transform, the REFRESH branch), and for an
# entity whose columns are only detectable that way it is the ONLY source of
# change. Without an entry here `no_publishable_ops` skipped every such run,
# so those changes reached no channel. A domain still opts in by listing
# REFRESH in its config's publish_ops.
OP_SUMMARY_KEYS = {"INSERT": "insert", "UPDATE": "update",
                   "DELETE": "delete", "REFRESH": "refresh"}
# The Lambda has 30s; leave headroom for StartJobRun + DynamoDB after the query.
ATHENA_POLL_TIMEOUT_SECONDS = 20


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    if not isinstance(event, dict):
        raise ValueError("Expected a JSON object payload")

    if event.get("detail-type") == GOLD_MERGE_DETAIL_TYPE:
        detail = event.get("detail") or {}
        if isinstance(detail, str):  # defensive: detail should arrive parsed
            detail = json.loads(detail)
        # 2026-09-16 contract: detail.goldRunId; legacy detail.run_id kept
        # accepted so an older publisher or a replay does not error.
        run_id = detail.get("goldRunId") or detail.get("run_id")
        if not run_id:
            raise ValueError("goldRunId missing in GoldMergeCompleted detail")
        # Only successful merges produce events; anything else is skipped
        # loudly, not failed - the merge job owns its own failure handling.
        status = str(detail.get("status") or "")
        if status.lower() != "success":
            return _skip(run_id, f"merge status is {status or '(missing)'}, not Success")
        domain = detail.get("domain")
        if not domain:
            raise ValueError("domain missing in GoldMergeCompleted detail")
        # UIFPE-474: config is keyed by the gold ENTITY, never the domain.
        # The announcer's contract carries goldId; a legacy event without
        # it falls back to domain, which named the same entity for every
        # publisher that existed before this change.
        gold_id = detail.get("goldId")
        if not gold_id:
            logger.warning("GoldMergeCompleted without goldId - using domain "
                           "%r as the entity key (legacy event)", domain)
            gold_id = domain
        # Source IS the gold Glue job's name in the new contract; the old
        # detail.glue_job_name remains the fallback for legacy events.
        changed_by = event.get("source") or detail.get("glue_job_name") or "gold-merge"
        return _process_run(str(run_id), str(gold_id), str(domain),
                            str(changed_by), from_event=True)

    # Run announcement: {"run_id": ..., "changedBy": ..., "domain": ...}.
    run_id = event.get("run_id") or event.get("runId")
    if not run_id:
        raise ValueError(
            "Payload is neither a GoldMergeCompleted EventBridge event nor a "
            "run announcement ({\"run_id\": ..., \"domain\": ...})"
        )
    changed_by = event.get("changedBy") or event.get("changed_by") or "manual"
    gold_id = event.get("goldId") or event.get("gold_id")
    domain = event.get("domain")
    if gold_id or domain:
        # goldId wins when both are given; a domain-only payload keeps
        # working because it named the entity for every pre-474 publisher.
        return _process_run(str(run_id), str(gold_id or domain),
                            str(domain or gold_id), str(changed_by),
                            from_event=False)

    # No entity at all: the raw operator tool - start the default job directly,
    # bypassing config, Athena, and metrics. Kept for emergency reprocessing.
    arguments = {"--run_id": str(run_id), "--changed_by": str(changed_by)}
    response = glue.start_job_run(JobName=EVENT_FABRIC_GLUE_JOB, Arguments=arguments)
    logger.info("Started %s jobRunId=%s for raw runId=%s (no entity - config "
                "and metrics skipped)", EVENT_FABRIC_GLUE_JOB, response["JobRunId"], run_id)
    return {"runId": run_id, "glueJob": EVENT_FABRIC_GLUE_JOB,
            "jobRunId": response["JobRunId"], "status": "started"}


def _process_run(run_id: str, gold_id: str, domain: str, changed_by: str, *,
                 from_event: bool) -> dict[str, Any]:
    """The config-driven path: resolve config, derive the run's op counts
    from the change log, apply skip rules, size, start, record IN_PROGRESS
    in pipeline-metrics (Table 2, keyed by the started JobRunId)."""
    # Prior attempts for this gold run (Table 2's sparse gsi_gold_run).
    # Event deliveries are idempotency-checked - EventBridge retries and
    # duplicate GoldMergeCompleted publishes must not double-process a run
    # (eventually-consistent read; Glue concurrency is the backstop).
    # Manual announcements bypass the check: re-announcing IS the operator
    # replay tool, and prior attempts become the new row's replay_count.
    prior_attempts = _prior_pipeline_rows(run_id)
    if from_event:
        active = [r for r in prior_attempts if r.get("status") in ACTIVE_OR_DONE]
        if active:
            return _skip(run_id, f"gold run already has a {active[-1]['status']} "
                                 f"processor run (event_run_id "
                                 f"{active[-1].get('event_run_id')}) - duplicate "
                                 "GoldMergeCompleted delivery skipped")
    try:
        config = _load_pipeline_config(gold_id)
    except ValueError as exc:
        if from_event:
            # The bus rule matches every onboarded-or-planned domain
            # (product/price/customer); a domain whose pipeline config is not
            # seeded yet must skip cleanly - erroring would burn EventBridge's
            # 5 retries and dead-letter the trigger into the pipeline DLQ as
            # junk. Manual announcements still raise: there the missing
            # config is an operator error that should be loud.
            logger.warning("GoldMergeCompleted for gold entity %r skipped: %s",
                           gold_id, exc)
            return _skip(run_id, f"no pipeline config for gold entity {gold_id!r} "
                                 "- seed it to enable publishing")
        raise
    if not config.get("enabled", False):
        return _skip(run_id, f"pipeline {config['pipelineid']} is disabled in "
                             f"{PIPELINE_CONFIGS_TABLE}")

    delta = config.get("delta") or {}
    # Identifiers lowercased: Athena resolves quoted identifiers
    # case-sensitively and Glue stores lowercase (config spells it
    # PRODUCT_CHANGES).
    changes_table = str(delta.get("table") or "").lower()
    if not changes_table:
        raise ValueError(f"delta.table missing in pipeline config "
                         f"{config['pipelineid']}/{CONFIG_VERSION}")
    run_id_col = str(delta.get("run_id_col") or "run_id").lower()
    op_col = str(delta.get("op_col") or "op").lower()

    # Per-domain catalog settings live in the config record (the stack is
    # shared across domains, so nothing per-domain may come from this
    # function's own environment). 2026-09-24 reshape: the database sits
    # under `glue.database` (beside the sizing tiers) and the Athena result
    # location under `eventing.athena_output`; the retired top-level `gold`
    # section and the env vars remain fallbacks so config rows and this
    # code can deploy in either order.
    legacy_gold = config.get("gold") or {}
    glue_database = str((config.get("glue") or {}).get("database")
                        or legacy_gold.get("glue_database") or GLUE_DATABASE)
    athena_output = str((config.get("eventing") or {}).get("athena_output")
                        or legacy_gold.get("athena_output") or ATHENA_OUTPUT)

    # The remaining run attributes come from the change log itself, not the
    # trigger payload: count the run's rows per operation via Athena.
    summary = _derive_delta_summary(changes_table, run_id_col, op_col, run_id,
                                    glue_database=glue_database,
                                    athena_output=athena_output)
    total = summary["total"]
    publish_ops = [str(op) for op in config.get("publish_ops", [])]
    ops_triggered = [op for op in publish_ops
                     if summary.get(OP_SUMMARY_KEYS.get(op, ""), 0) > 0]

    skip_when = config.get("skip_run_when") or {}
    if skip_when.get("total_zero") and total == 0:
        return _skip(run_id, f"no change rows for this run in {changes_table} "
                             "(skip_run_when.total_zero)")
    if skip_when.get("no_publishable_ops") and not ops_triggered:
        return _skip(run_id, f"no publishable operations in {changes_table} "
                             f"for this run (publish_ops={publish_ops})")

    sizing_tier, sizing = _choose_sizing(config.get("glue") or {}, total)
    processor = config.get("processor") or {}
    job_name = str(processor.get("glue_job_name") or EVENT_FABRIC_GLUE_JOB)
    mode = str(processor.get("mode_default") or "INCREMENTAL")

    # Domain-specific job arguments ride in the config record
    # (processor.arguments: schema_name, glue_database, mapping_key,
    # event_type, join_key, ...), so ONE shared Glue job serves every
    # domain and onboarding a domain never touches the stack. The
    # run-specific keys below are set last - config can never override them.
    arguments: dict[str, str] = {}
    for key, value in (processor.get("arguments") or {}).items():
        arguments[key if str(key).startswith("--") else f"--{key}"] = str(value)
    arguments.update({
        "--run_id": run_id,
        # Provenance on the run-registry record: who announced the run.
        "--changed_by": changed_by,
        "--changes_table": changes_table,
    })

    # Async hand-off: StartJobRun only queues the run and returns its
    # JobRunId - nothing here waits for the processor.
    job_run_id = _start_processor(job_name, arguments, sizing)
    logger.info(
        "Started %s jobRunId=%s for runId=%s goldId=%s domain=%s tier=%s mode=%s "
        "derived_summary=%s", job_name, job_run_id, run_id, gold_id, domain,
        sizing_tier, mode, json.dumps(summary),
    )

    metrics_recorded = _record_pipeline_run(
        event_run_id=job_run_id, domain=domain, gold_run_id=run_id,
        sizing=sizing, total=total,
        started_by="SYSTEM" if from_event else changed_by,
        replay_count=len(prior_attempts),
    )

    # PLANNED: API registry invocation goes here.

    return {
        "runId": run_id,
        "goldId": gold_id,
        "domain": domain,
        "deltaTable": changes_table,
        "deltaSummary": summary,
        "glueJob": job_name,
        "jobRunId": job_run_id,
        "sizingTier": sizing_tier,
        "metricsRecorded": metrics_recorded,
        "status": "started",
    }


def _skip(run_id: Any, reason: str) -> dict[str, Any]:
    logger.warning("Skipping runId=%s: %s", run_id, reason)
    return {"runId": run_id, "status": "skipped", "reason": reason}


def _load_pipeline_config(gold_id: str) -> dict[str, Any]:
    """Fetch the pipeline's master config document (version 'latest')."""
    if configs_table is None:
        raise RuntimeError("PIPELINE_CONFIGS_TABLE is not configured")
    # Keyed by the gold ENTITY (UIFPE-474). No row migration was needed:
    # goldId == domain for every row that existed when the key changed.
    pipeline_id = f"{PIPELINE_ID_PREFIX}-{gold_id}"
    item = configs_table.get_item(
        Key={"pipelineid": pipeline_id, "version": CONFIG_VERSION}
    ).get("Item")
    if not item:
        raise ValueError(
            f"No pipeline config {pipeline_id}/{CONFIG_VERSION} in "
            f"{PIPELINE_CONFIGS_TABLE} - seed it before announcing runs for "
            f"gold entity {gold_id!r}"
        )
    return item


def _derive_delta_summary(changes_table: str, run_id_col: str, op_col: str,
                          run_id: str, *, glue_database: str = "",
                          athena_output: str = "") -> dict[str, int]:
    """Count the run's change-log rows per operation via Athena.

    The run id is inlined as a single-quote-escaped literal (matching the
    Event Fabric Processor - see its docstring for why not
    ExecutionParameters). Returns
    {"total", "insert", "update", "delete", "refresh"}; any op with no
    OP_SUMMARY_KEYS entry counts toward total only.
    """
    glue_database = glue_database or GLUE_DATABASE
    athena_output = athena_output or ATHENA_OUTPUT
    if not glue_database or not athena_output:
        raise RuntimeError("gold.glue_database / gold.athena_output are not "
                           "configured (pipeline config or environment)")
    escaped = run_id.replace("'", "''")
    query = (
        f'SELECT "{op_col}" AS op, count(*) AS n '
        f'FROM "{glue_database}"."{changes_table}" '
        f"WHERE \"{run_id_col}\" = '{escaped}' GROUP BY 1"
    )
    query_id = athena.start_query_execution(
        QueryString=query,
        WorkGroup=ATHENA_WORKGROUP,
        ResultConfiguration={"OutputLocation": athena_output},
    )["QueryExecutionId"]

    deadline = time.time() + ATHENA_POLL_TIMEOUT_SECONDS
    while True:
        state = athena.get_query_execution(QueryExecutionId=query_id)
        status = state["QueryExecution"]["Status"]
        if status["State"] in ("SUCCEEDED", "FAILED", "CANCELLED"):
            break
        if time.time() > deadline:
            athena.stop_query_execution(QueryExecutionId=query_id)
            raise TimeoutError(
                f"Athena query {query_id} still {status['State']} after "
                f"{ATHENA_POLL_TIMEOUT_SECONDS}s deriving the delta summary"
            )
        time.sleep(0.5)
    if status["State"] != "SUCCEEDED":
        raise RuntimeError(
            f"Athena query {query_id} {status['State']}: "
            f"{status.get('StateChangeReason', '')}"
        )

    rows = athena.get_query_results(QueryExecutionId=query_id)["ResultSet"]["Rows"]
    summary = {"total": 0, "insert": 0, "update": 0, "delete": 0,
               "refresh": 0}
    for row in rows[1:]:  # row 0 is the header
        data = row.get("Data", [])
        op = (data[0].get("VarCharValue") or "").upper() if data else ""
        n = int(data[1].get("VarCharValue") or 0) if len(data) > 1 else 0
        summary["total"] += n
        key = OP_SUMMARY_KEYS.get(op)
        if key:
            summary[key] += n
    return summary


def _choose_sizing(glue_config: dict[str, Any], total: int) -> tuple[str, dict[str, Any]]:
    """Pick the run's worker tier from the derived row count.

    2026-09-22 contract: `glue.sizing_tiers` is an ORDERED list, smallest
    first; the run takes the FIRST tier whose max_rows covers the count, and
    a null max_rows is the unbounded catch-all (the list's safety net - a
    config whose last tier is bounded falls back to that last tier rather
    than failing the run). The legacy two-tier shape
    (incremental/large_run + large_run_threshold_rows) still resolves so
    domains migrate their config rows on their own schedule.
    """
    tiers = glue_config.get("sizing_tiers")
    if isinstance(tiers, list) and tiers:
        chosen = tiers[-1]
        for tier_cfg in tiers:
            max_rows = tier_cfg.get("max_rows")
            if max_rows is None or total <= int(max_rows):
                chosen = tier_cfg
                break
        return str(chosen.get("name") or "unnamed"), _tier_sizing(chosen)

    # Legacy shape (pre sizing_tiers).
    threshold = int(glue_config.get("large_run_threshold_rows") or 0)
    tier = "large_run" if threshold and total >= threshold else "incremental"
    return tier, _tier_sizing(glue_config.get(tier) or {})


def _tier_sizing(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "worker_type": str(raw["worker_type"]) if raw.get("worker_type") else None,
        "num_workers": int(raw["num_workers"]) if raw.get("num_workers") else None,
        "timeout_minutes": int(raw["timeout_minutes"]) if raw.get("timeout_minutes") else None,
        # Python-shell translation of the tier (Option A, 2026-09-25): the
        # only memory knob a pythonshell job has is MaxCapacity (0.0625 = 1GB
        # or 1 = 16GB); worker_type/num_workers are Spark-only and get
        # rejected. A tier that declares max_capacity makes its sizing REAL
        # on the deployed job - the OOM of 2026-09-24 (511k rows on the 1GB
        # default) is exactly what this closes.
        "max_capacity": float(raw["max_capacity"]) if raw.get("max_capacity") else None,
    }


def _start_processor(job_name: str, arguments: dict[str, str],
                     sizing: dict[str, Any]) -> str:
    """StartJobRun with the config's sizing, degrading for python-shell jobs.

    Worker overrides (WorkerType/NumberOfWorkers) are only valid for Spark
    jobs; the deployed Event Fabric Processor is a Python shell job, which
    rejects them with InvalidInputException. Retry without them so the same
    config works against both, and the sizing intent still lands in the
    metrics record either way.
    """
    kwargs: dict[str, Any] = {"JobName": job_name, "Arguments": arguments}
    if sizing.get("timeout_minutes"):
        kwargs["Timeout"] = sizing["timeout_minutes"]
    if sizing.get("worker_type") and sizing.get("num_workers"):
        kwargs["WorkerType"] = sizing["worker_type"]
        kwargs["NumberOfWorkers"] = sizing["num_workers"]
    try:
        return glue.start_job_run(**kwargs)["JobRunId"]
    except ClientError as exc:
        if (exc.response.get("Error", {}).get("Code") != "InvalidInputException"
                or "WorkerType" not in kwargs):
            raise
        if sizing.get("max_capacity"):
            # The tier's python-shell translation: real memory for the run.
            kwargs["MaxCapacity"] = sizing["max_capacity"]
        logger.warning(
            "%s rejected worker sizing (%s x%s) - python-shell job. Retrying "
            "with Timeout%s", job_name,
            kwargs.pop("WorkerType"), kwargs.pop("NumberOfWorkers"),
            f" + MaxCapacity {kwargs['MaxCapacity']}" if "MaxCapacity" in kwargs
            else " only",
        )
        return glue.start_job_run(**kwargs)["JobRunId"]


def _prior_pipeline_rows(gold_run_id: str) -> list[dict[str, Any]]:
    """This gold run's existing INCREMENTAL rows from Table 2's sparse
    gsi_gold_run - the idempotency input and the replay counter. Best-effort:
    an unreadable index must not block a run, so errors return []."""
    if metrics_table is None:
        return []
    try:
        response = metrics_table.query(
            IndexName="gsi_gold_run",
            KeyConditionExpression=(
                Key("gold_run_id").eq(gold_run_id) & Key("mode").eq(PIPELINE_MODE)
            ),
        )
        return response.get("Items", [])
    except Exception:  # noqa: BLE001 - see docstring
        logger.exception("gsi_gold_run lookup failed for gold run %s", gold_run_id)
        return []


def _record_pipeline_run(*, event_run_id: str, domain: str, gold_run_id: str,
                         sizing: dict[str, Any], total: int, started_by: str,
                         replay_count: int) -> bool:
    """Write the run's IN_PROGRESS row to pipeline-metrics (Table 2), keyed
    by the JobRunId StartJobRun just returned. The processor flips it to
    STARTED and terminal, and fills the counts and versions maps.

    Best-effort by design: the Glue job is already running when this
    executes, so a metrics failure must not fail the invocation (an async
    retry would double-start the run). It logs loudly and flags the response
    with metricsRecorded=false instead.
    """
    if metrics_table is None:
        logger.error("PIPELINE_METRICS_TABLE not configured - run %s has no "
                     "pipeline-metrics row", gold_run_id)
        return False

    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    try:
        metrics_table.put_item(Item={
            "event_run_id": event_run_id,
            "mode": PIPELINE_MODE,
            "domain": domain,
            "gold_run_id": gold_run_id,
            "operation": "OVERALL",
            "status": "IN_PROGRESS",
            "worker_type": sizing.get("worker_type"),
            "num_workers": sizing.get("num_workers"),
            "timeout_minutes": sizing.get("timeout_minutes"),
            "reason": None,
            "error_msg": None,
            "source_row_count": total,
            # The processor owns everything from STARTED on.
            "published_row_count": None,
            "failed_row_count": None,
            "active_tiers_processed": None,
            "versions_triggered": None,
            "versions_succeeded": None,
            "versions_failed": None,
            "started_at": now,
            "completed_at": None,
            "duration_seconds": None,
            "replay_count": replay_count,
            "last_updated_at": now,
            "ttl": int(time.time()) + METRICS_TTL_DAYS * 86400,
            "started_by": started_by,
        })
        return True
    except Exception:  # noqa: BLE001 - see docstring: never fail the started run
        logger.exception("Failed to record IN_PROGRESS pipeline-metrics for "
                         "%s (event_run_id=%s) in %s", gold_run_id,
                         event_run_id, PIPELINE_METRICS_TABLE)
        return False
