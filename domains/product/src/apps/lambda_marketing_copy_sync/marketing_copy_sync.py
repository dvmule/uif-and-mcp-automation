"""Marketing-copy serving-copy sync (UIFPE-254) — the materializer half of
the content service.

Keeps the DynamoDB serving table (`uif-{env}-marketing-copy-serving`) equal
to the gold library `uif_echo_{env}_gold_product.marketing_copy`, driven by the
entity's CHANGE LOG so every update is delta-only. Content is stored GZIPPED
(Binary) with an md5 CONTENT HASH alongside — the hash is the API's ETag,
because the platform's silent-maintenance invariant means `_gold_updated_at`
deliberately does NOT move when values are repaired silently, so a
timestamp ETag would let a revalidating cache serve stale content forever.

Triggers (one code path out):
  * EventBridge "Glue Job State Change" SUCCEEDED for the gold TRANSFORM
    **and the RECONCILE job** — reconcile is the one producer of gold
    DELETIONS (it writes DELETE change rows under its own run id), so a
    transform-only trigger would never remove a serving item. The handler
    verifies the run was --gold_id marketing_copy before doing anything.
  * {"mode": "baseline"} — manual full fill (first load, or after a
    --rebuild, which writes no change-log rows). A completed baseline
    chains into {"mode": "prune"}: a paged scan deleting items this
    baseline did not write AND no later delta has touched — orphans left
    by a rebuild that removed rows.
  * {"mode": "process"/"prune"} — SELF-CHAIN continuations. The chain can
    break MID-FILE (line offset carried in the payload) because Athena
    decides UNLOAD file sizes, not us, and a single file can exceed one
    invocation's budget.

Write semantics: per-item CONDITIONAL puts (`goldUpdatedAt` may only move
forward; equal allowed for idempotent replay and silent repairs) — an
interleaved baseline continuation can therefore never regress a fresher
delta write. Rows are streamed straight from the UNLOAD files into small
parallel batches; nothing accumulates a whole file in memory. Items whose
content exceeds MAX_ITEM_BYTES even compressed are REFUSED loudly (the
"REFUSED contentId=" log line carries a metric filter + alarm in
Terraform) — a truncated write would be silent corruption.

Failure posture: every UNLOAD goes to a fresh attempt-unique prefix (a
retry can never trip over "target location must be empty"), processed files
are deleted as they finish, Athena polls respect the invocation's real
remaining time, and the function carries an on-failure destination (SQS +
alarm in Terraform) so a dropped event is visible, never silent.

Import-time AWS-free; every client is injectable for tests.
"""
from __future__ import annotations

import datetime
import gzip
import hashlib
import io
import json
import os
import re
import time
import urllib.parse
import uuid
from concurrent.futures import ThreadPoolExecutor

ENV_NAME = os.environ.get("ENV_NAME", "dev")
TABLE_NAME = os.environ.get("TABLE_NAME", f"uif-{ENV_NAME}-marketing-copy-serving")
GOLD_DB = os.environ.get("GOLD_DB", f"uif_echo_{ENV_NAME}_gold_product")
GOLD_TABLE = "marketing_copy"
CHANGES_TABLE = f"{GOLD_TABLE}_changes"
ATHENA_WORKGROUP = os.environ.get("ATHENA_WORKGROUP", "primary")
OUTPUT_S3 = os.environ.get("OUTPUT_S3", "")          # Athena query results
UNLOAD_S3 = os.environ.get("UNLOAD_S3", "")          # UNLOAD data prefix
TRANSFORM_JOB = os.environ.get(
    "TRANSFORM_JOB", f"uif-echo-{ENV_NAME}-gold-transform")
RECONCILE_JOB = os.environ.get(
    "RECONCILE_JOB", f"uif-echo-{ENV_NAME}-gold-reconcile")
SELF_FUNCTION_NAME = os.environ.get("SELF_FUNCTION_NAME", "")

# The serving-item ceiling, applied AFTER compression (DynamoDB's hard limit
# is 400KB including attribute names; headroom left for the metadata).
MAX_ITEM_BYTES = int(os.environ.get("MAX_ITEM_BYTES", "380000"))

# Chain when less than this remains; enough for one more batch + the invoke.
SELF_CHAIN_FLOOR_MS = 120_000
# Streamed write granularity: lines pulled per parallel put round.
BATCH_LINES = 200
PUT_THREADS = 8

# Same fence gold_cdc applies to run_id before splicing it into SQL.
RUN_ID_RE = re.compile(r"^[A-Za-z0-9_.:+-]{1,128}$", re.ASCII)

_PAYLOAD_SELECT = (
    "SELECT m.content_id, m.marketing_copy_type_code, m.owning_market_code, m.audience_code, "
    "m.marketing_copy_text, m.marketing_copy_content_text, "
    "CAST(m._gold_updated_at AS VARCHAR) AS updated "
    f"FROM {GOLD_DB}.{GOLD_TABLE} m"
)


def _clients(athena=None, glue=None, ddb=None, lam=None, s3=None):
    import boto3
    return (athena or boto3.client("athena"),
            glue or boto3.client("glue"),
            ddb or boto3.client("dynamodb"),
            lam or boto3.client("lambda"),
            s3 or boto3.client("s3"))


def _await_query(athena, qid: str, remaining_ms) -> None:
    """Poll to success, bounded by the INVOCATION's real remaining time (a
    fixed cap larger than the budget would let one query eat the whole
    function and turn the failure into an opaque Lambda timeout)."""
    while True:
        state = athena.get_query_execution(QueryExecutionId=qid)[
            "QueryExecution"]["Status"]
        if state["State"] == "SUCCEEDED":
            return
        if state["State"] in ("FAILED", "CANCELLED"):
            raise RuntimeError(
                f"Athena {qid} {state['State']}: "
                f"{state.get('StateChangeReason', '')}")
        if remaining_ms() < 90_000:
            athena.stop_query_execution(QueryExecutionId=qid)
            raise RuntimeError(
                f"Athena {qid} cancelled — invocation budget nearly spent")
        time.sleep(2)


def _run_query(athena, sql: str, remaining_ms) -> str:
    qid = athena.start_query_execution(
        QueryString=sql, WorkGroup=ATHENA_WORKGROUP,
        ResultConfiguration={"OutputLocation": OUTPUT_S3},
    )["QueryExecutionId"]
    _await_query(athena, qid, remaining_ms)
    return qid


def unload_sql(run_id: str | None, dest: str) -> str:
    """The one payload query, delta (run_id given) or baseline (None)."""
    body = _PAYLOAD_SELECT
    if run_id is not None:
        body += (
            " WHERE m.content_id IN (SELECT DISTINCT content_id "
            f"FROM {GOLD_DB}.{CHANGES_TABLE} WHERE run_id = '{run_id}')"
        )
    return (f"UNLOAD ({body}) TO '{dest}' "
            "WITH (format = 'JSON', compression = 'GZIP')")


def deletes_sql(run_id: str) -> str:
    """contentIds the run touched that no longer exist in gold — deletions
    detected by ABSENCE rather than by op vocabulary. Reconcile (the one
    producer of gold deletions) writes DELETE change rows under its own
    run id, so its SUCCEEDED event lands here like any other run's."""
    return (
        "SELECT c.content_id FROM "
        f"(SELECT DISTINCT content_id FROM {GOLD_DB}.{CHANGES_TABLE} "
        f"WHERE run_id = '{run_id}') c "
        f"LEFT JOIN {GOLD_DB}.{GOLD_TABLE} m ON m.content_id = c.content_id "
        "WHERE m.content_id IS NULL"
    )


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.%f")


def build_item(row: dict, sync_tag: str) -> dict | None:
    """One UNLOAD JSON line -> a DynamoDB item, or None when the item must
    be REFUSED (no key, or too large even compressed). The content hash is
    the API's ETag; syncTag/syncedAt drive the post-baseline prune."""
    cid = row.get("content_id")
    if not cid:
        print("[WARN] row with no contentid skipped")
        return None
    item: dict = {"contentId": {"S": str(cid)},
                  "syncTag": {"S": sync_tag},
                  "syncedAt": {"S": _now_iso()}}
    for src, attr in (("marketing_copy_type_code", "marketingCopyTypeCode"), ("owning_market_code", "owningMarketCode"),
                      ("audience_code", "audienceCode"), ("marketing_copy_text", "marketingCopyText")):
        v = row.get(src)
        if v is not None and v != "":
            item[attr] = {"S": str(v)}
    updated = row.get("updated")
    if updated:
        item["goldUpdatedAt"] = {"S": str(updated)}
    content = row.get("marketing_copy_content_text")
    if content is not None:
        raw = str(content).encode("utf-8")
        gz = gzip.compress(raw, 6)
        if len(gz) > MAX_ITEM_BYTES:
            print(f"[ERROR] REFUSED contentId={cid}: {len(gz)} compressed "
                  f"bytes exceeds the {MAX_ITEM_BYTES} serving-item ceiling "
                  "— content NOT served; raise the ceiling or move this "
                  "content class to an S3 pointer (design doc, revisit list)")
            return None
        item["contentGz"] = {"B": gz}
        item["contentHash"] = {"S": hashlib.md5(raw).hexdigest()}
    return item


def _put_conditional(ddb, item: dict) -> bool:
    """Upsert that can only move goldUpdatedAt FORWARD (equal allowed —
    idempotent replay, and silent repairs deliberately keep the timestamp).
    A stale baseline row can therefore never overwrite a fresher delta
    write. Returns False when suppressed as stale."""
    kwargs = {"TableName": TABLE_NAME, "Item": item}
    if "goldUpdatedAt" in item:
        kwargs["ConditionExpression"] = (
            "attribute_not_exists(goldUpdatedAt) OR goldUpdatedAt <= :u")
        kwargs["ExpressionAttributeValues"] = {":u": item["goldUpdatedAt"]}
    try:
        ddb.put_item(**kwargs)
        return True
    except Exception as e:  # noqa: BLE001 - narrow below
        if "ConditionalCheckFailed" in type(e).__name__ + str(e):
            return False
        raise


def _put_batch(ddb, items: list) -> tuple[int, int]:
    """Parallel conditional puts. Returns (written, suppressed_stale)."""
    if not items:
        return 0, 0
    with ThreadPoolExecutor(max_workers=PUT_THREADS) as pool:
        outcomes = list(pool.map(lambda it: _put_conditional(ddb, it), items))
    return sum(outcomes), len(outcomes) - sum(outcomes)


def _batch_delete(ddb, ids: list) -> None:
    for i in range(0, len(ids), 25):
        chunk = [{"DeleteRequest": {"Key": {"contentId": {"S": x}}}}
                 for x in ids[i:i + 25]]
        for attempt in range(6):
            resp = ddb.batch_write_item(RequestItems={TABLE_NAME: chunk})
            chunk = resp.get("UnprocessedItems", {}).get(TABLE_NAME, [])
            if not chunk:
                break
            time.sleep(0.2 * (attempt + 1))
        if chunk:
            raise RuntimeError(
                f"{len(chunk)} deletes still unprocessed after retries")


def _split_s3(uri: str) -> tuple[str, str]:
    bucket, key = uri.replace("s3://", "").split("/", 1)
    return bucket, key


def _list_unload_files(s3, dest: str) -> list:
    bucket, prefix = _split_s3(dest)
    keys, token = [], None
    while True:
        kw = {"Bucket": bucket, "Prefix": prefix}
        if token:
            kw["ContinuationToken"] = token
        resp = s3.list_objects_v2(**kw)
        keys += [o["Key"] for o in resp.get("Contents", []) if o["Size"] > 0]
        token = resp.get("NextContinuationToken")
        if not token:
            return [f"s3://{bucket}/{k}" for k in sorted(keys)]


def _process_files(s3, ddb, state: dict, remaining_ms) -> dict:
    """Stream rows from UNLOAD files into conditional puts. `state` carries
    files / line_offset / counters and comes back updated; when the time
    budget runs low the caller self-chains with it. Files are DELETED once
    fully processed (retries then re-see only unfinished work, and the
    prefix never accumulates)."""
    files = list(state.get("files") or [])
    offset = int(state.get("line_offset") or 0)
    tag = state["sync_tag"]

    def flush(batch: list) -> None:
        written, stale = _put_batch(ddb, batch)
        state["written"] = state.get("written", 0) + written
        state["suppressed_stale"] = state.get("suppressed_stale", 0) + stale

    while files:
        uri = files[0]
        bucket, key = _split_s3(uri)
        body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        batch: list = []
        paused_at = None
        with gzip.open(io.BytesIO(body), "rt", encoding="utf-8") as fh:
            for i, line in enumerate(fh):
                if i < offset or not line.strip():
                    continue
                item = build_item(json.loads(line), tag)
                if item:
                    batch.append(item)
                if len(batch) >= BATCH_LINES:
                    flush(batch)
                    batch = []
                    if remaining_ms() < SELF_CHAIN_FLOOR_MS:
                        paused_at = i + 1
                        break
        flush(batch)
        if paused_at is not None:
            state["files"] = files
            state["line_offset"] = paused_at
            return state
        s3.delete_object(Bucket=bucket, Key=key)
        files.pop(0)
        offset = 0
        if files and remaining_ms() < SELF_CHAIN_FLOOR_MS:
            break
    state["files"] = files
    state["line_offset"] = 0
    return state


def _prune(ddb, state: dict, remaining_ms) -> dict:
    """Post-baseline sweep: delete items this baseline did not write AND no
    later delta touched — the orphans a --rebuild leaves (rebuilds write no
    change rows, so nothing else ever removes them). Paged scan, chained on
    the same time budget."""
    tag, start = state["sync_tag"], state["baseline_start"]
    kwargs = {
        "TableName": TABLE_NAME,
        "ProjectionExpression": "contentId",
        "FilterExpression": (
            "(attribute_not_exists(syncTag) OR syncTag <> :tag) AND "
            "(attribute_not_exists(syncedAt) OR syncedAt < :start)"),
        "ExpressionAttributeValues": {":tag": {"S": tag},
                                      ":start": {"S": start}},
    }
    if state.get("scan_key"):
        kwargs["ExclusiveStartKey"] = {
            "contentId": {"S": state["scan_key"]}}
    while True:
        resp = ddb.scan(**kwargs)
        ids = [it["contentId"]["S"] for it in resp.get("Items", [])]
        if ids:
            _batch_delete(ddb, ids)
            state["pruned"] = state.get("pruned", 0) + len(ids)
        last = resp.get("LastEvaluatedKey")
        if not last:
            state["scan_key"] = None
            return state
        kwargs["ExclusiveStartKey"] = last
        state["scan_key"] = last["contentId"]["S"]
        if remaining_ms() < SELF_CHAIN_FLOOR_MS:
            return state


def _self_chain(lam, payload: dict) -> None:
    lam.invoke(FunctionName=SELF_FUNCTION_NAME, InvocationType="Event",
               Payload=json.dumps(payload).encode())


def _continue_or_finish(lam, state: dict) -> dict:
    if state.get("files"):
        _self_chain(lam, {"mode": "process", **state})
        return {"continued": len(state["files"]), **_summary(state)}
    if state.get("prune_after") and not state.get("prune_started"):
        # baseline fully written -> sweep the orphans a rebuild left behind
        _self_chain(lam, {"mode": "prune", **state, "prune_started": True})
        return {"continued": 1, **_summary(state)}
    print(f"[INFO] sync complete: {_summary(state)}")
    return {"continued": 0, **_summary(state)}


def _summary(state: dict) -> dict:
    return {"synced": state.get("written", 0),
            "deleted": state.get("deleted", 0),
            "suppressed_stale": state.get("suppressed_stale", 0),
            "pruned": state.get("pruned", 0)}


def _rebuild_flag(args: dict) -> bool:
    v = args.get("--rebuild")
    return v is not None and str(v).strip().lower() not in ("false", "0", "no", "")


def handler(event: dict, context=None, athena=None, glue=None, ddb=None,
            lam=None, s3=None) -> dict:
    athena, glue, ddb, lam, s3 = _clients(athena, glue, ddb, lam, s3)

    def remaining_ms():
        return (context.get_remaining_time_in_millis()
                if context is not None else 900_000)

    mode = event.get("mode")

    if mode == "process":
        state = _process_files(s3, ddb, dict(event), remaining_ms)
        return _continue_or_finish(lam, state)

    if mode == "prune":
        state = _prune(ddb, dict(event), remaining_ms)
        if state.get("scan_key"):
            _self_chain(lam, {"mode": "prune", **state})
            return {"continued": 1, **_summary(state)}
        print(f"[INFO] prune complete: {_summary(state)}")
        return {"continued": 0, **_summary(state)}

    if mode == "baseline":
        run_id = None
        sync_tag = f"baseline-{uuid.uuid4().hex[:12]}"
    else:
        # EventBridge Glue state change: only OUR entity's SUCCEEDED runs,
        # from EITHER the transform or the reconcile job (reconcile is the
        # one producer of deletions).
        detail = event.get("detail") or {}
        run_id = detail.get("jobRunId")
        job_name = detail.get("jobName")
        if (not run_id or detail.get("state") != "SUCCEEDED"
                or job_name not in (TRANSFORM_JOB, RECONCILE_JOB)):
            return {"skipped": "not a succeeded gold-job event"}
        run = glue.get_job_run(JobName=job_name, RunId=run_id)["JobRun"]
        args = run.get("Arguments") or {}
        if args.get("--gold_id") != "marketing_copy":
            return {"skipped": f"gold_id {args.get('--gold_id')!r}"}
        if _rebuild_flag(args):
            print("[WARN] rebuild run detected — the change log carries "
                  "nothing for it; run {'mode': 'baseline'} to re-sync")
            return {"skipped": "rebuild — baseline required"}
        if not RUN_ID_RE.fullmatch(run_id):
            raise ValueError(f"run_id {run_id!r} fails the identifier fence")
        sync_tag = run_id

    # ATTEMPT-UNIQUE destination: an async retry after a partial failure
    # must never UNLOAD into the previous attempt's non-empty prefix.
    attempt = uuid.uuid4().hex[:8]
    dest = (UNLOAD_S3.rstrip("/")
            + f"/{urllib.parse.quote(sync_tag, safe='')}-{attempt}/data/")
    _run_query(athena, unload_sql(run_id, dest), remaining_ms)

    state = {"sync_tag": sync_tag, "files": _list_unload_files(s3, dest),
             "line_offset": 0, "written": 0, "suppressed_stale": 0,
             "deleted": 0, "pruned": 0}
    if run_id:
        qid = _run_query(athena, deletes_sql(run_id), remaining_ms)
        ids, token = [], None
        while True:
            kw = {"QueryExecutionId": qid, "MaxResults": 1000}
            if token:
                kw["NextToken"] = token
            resp = athena.get_query_results(**kw)
            rows = resp["ResultSet"]["Rows"]
            for r in rows[1:] if token is None else rows:
                v = r["Data"][0].get("VarCharValue")
                if v:
                    ids.append(v)
            token = resp.get("NextToken")
            if not token:
                break
        if ids:
            _batch_delete(ddb, ids)
            state["deleted"] = len(ids)
            print(f"[INFO] deleted {len(ids)} contentIds no longer in gold")
    else:
        state["prune_after"] = True
        state["baseline_start"] = _now_iso()

    state = _process_files(s3, ddb, state, remaining_ms)
    return _continue_or_finish(lam, state)
