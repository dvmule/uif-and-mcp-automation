"""Gold serving sync — ONE Lambda, every entity (UIFPE-518).

Fills the shared serving table from gold, change-log driven and delta-only.
Gold is Iceberg on S3 with no point lookup, so a synchronous GET needs a
serving copy.

Nothing here is per-entity. The entity arrives in the event, its spec is
derived from the live pipeline config, and serving it is a `serving.enabled`
line in that config — so adding an entity touches no infrastructure. That is
the whole reason this is one function and one table rather than one of each per
entity.

Triggers:
  * EventBridge "Glue Job State Change", SUCCEEDED, for the gold TRANSFORM and
    the RECONCILE job. Reconcile is the one producer of gold deletions, so a
    transform-only trigger would never remove a serving item.
  * {"mode": "baseline", "gold_id": "<entity>"} — a manual full fill: first
    load, or after a --rebuild, which writes no change-log rows to read.
  * {"mode": "process" | "prune", "gold_id": …} — self-chain continuations.

Bare sibling import: the deployment artifact is a flat zip, so gold_serving.py
sits next to this module at the zip root. Keep it in serving.tf's archive
source list.

Import-time AWS-free; clients are injectable for tests.
"""
from __future__ import annotations

import gzip
import os

from gold_serving import ServingRuntime, clients, load_spec, sync

ENV_NAME = os.environ.get("ENV_NAME", "dev")
CONFIG_TABLE = os.environ.get(
    "CONFIG_TABLE", f"uif-echo-{ENV_NAME}-gold-pipeline-config")
#: One table for every entity, keyed {entity}#{principal} / {sub-grain}.
TABLE_NAME = os.environ.get(
    "TABLE_NAME", f"uif-echo-{ENV_NAME}-gold-serving")
ATHENA_OUTPUT = os.environ.get(
    "ATHENA_OUTPUT",
    f"s3://uif-echo-{ENV_NAME}-gold-artifacts/athena-results/")
UNLOAD_S3 = os.environ.get(
    "UNLOAD_S3",
    f"s3://uif-echo-{ENV_NAME}-gold-artifacts/serving-sync/")
TRANSFORM_JOB = os.environ.get(
    "TRANSFORM_JOB", f"uif-echo-{ENV_NAME}-gold-transform")
RECONCILE_JOB = os.environ.get(
    "RECONCILE_JOB", f"uif-echo-{ENV_NAME}-gold-reconcile")
SELF_FUNCTION_NAME = os.environ.get("SELF_FUNCTION_NAME", "")
ATHENA_WORKGROUP = os.environ.get("ATHENA_WORKGROUP", "primary")


def put_threads(env=None) -> int:
    """Concurrent DynamoDB writers. The client's connection pool is sized from
    this, so the two cannot drift apart.

    FLOORED AT 1: a zero or negative value would build a thread pool that
    writes nothing while the run still reports success. A function rather than
    an inline expression so the parsing and the floor are testable without
    reimporting the module.
    """
    raw = (env if env is not None else os.environ).get("PUT_THREADS", "16")
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        print(f"[WARN] PUT_THREADS={raw!r} is not an integer — using 16")
        return 16


PUT_THREADS = put_threads()


def runtime() -> ServingRuntime:
    return ServingRuntime(
        table_name=TABLE_NAME,
        output_s3=ATHENA_OUTPUT,
        athena_workgroup=ATHENA_WORKGROUP,
        self_function_name=SELF_FUNCTION_NAME,
        put_threads=PUT_THREADS,
    )


def resolve_gold_id(event: dict, glue) -> str | None:
    """Which entity is this invocation about?

    A manual call and a self-chain continuation carry `gold_id` outright. An
    EventBridge event does not: the gold jobs are shared, so the job name says
    nothing about the entity and only the run's own `--gold_id` argument does.
    """
    if event.get("gold_id"):
        return str(event["gold_id"])

    detail = event.get("detail") or {}
    run_id, job_name = detail.get("jobRunId"), detail.get("jobName")
    if not run_id or job_name not in (TRANSFORM_JOB, RECONCILE_JOB):
        return None
    run = glue.get_job_run(JobName=job_name, RunId=run_id)["JobRun"]
    return (run.get("Arguments") or {}).get("--gold_id")


def handler(event: dict, context=None, athena=None, glue=None, ddb=None,
            lam=None, s3=None) -> dict:
    # The runtime is built first so the DynamoDB client's connection pool can
    # be sized to put_threads; botocore's default of 10 throttles 16 writers.
    rt = runtime()
    athena, glue, ddb, lam, s3 = clients(athena, glue, ddb, lam, s3,
                                         ddb_pool=rt.put_threads)

    def remaining_ms():
        return (context.get_remaining_time_in_millis()
                if context is not None else 900_000)

    gold_id = resolve_gold_id(event, glue)
    if not gold_id:
        return {"skipped": "no gold_id in the event"}

    # Loaded per invocation, deliberately: a continuation must see the same
    # config its first leg did, and a cached module-level spec would both go
    # stale against a config the transform rewrites every run and leak one
    # entity's spec into the next entity's invocation on a warm container.
    spec = load_spec(ddb, s3, gold_id, CONFIG_TABLE, ENV_NAME, channel="api")
    if not spec.enabled:
        return {"skipped": f"{gold_id} is not served (serving.enabled)"}

    # gzip.compress, always. The engine only calls it for an entity whose
    # config declares compress_prop, and passing it unconditionally means the
    # caller cannot be the thing that forgets. It was missing until UIFPE-767:
    # the engine's compression had no supplier at all, so the first entity to
    # declare a compressed property died on its first row with
    # "'NoneType' object is not callable" -- after a 2.97 GB UNLOAD had
    # already run. stdlib, so it costs the zip nothing.
    return sync(event, spec, rt,
                transform_job=TRANSFORM_JOB,
                reconcile_job=RECONCILE_JOB,
                unload_s3=UNLOAD_S3,
                remaining_ms=remaining_ms,
                compress=gzip.compress,
                athena=athena, glue=glue, ddb=ddb, lam=lam, s3=s3)
