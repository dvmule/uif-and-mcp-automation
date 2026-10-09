"""Event Fabric Processor - Glue Python shell job.

Owns the run-processing flow that previously lived in the Lambda
(2026-08-24 requirement change). The Lambda is now a thin trigger that
starts this job with --run_id; this job:

  1. Inserts the run into the DynamoDB run registry (status STARTED) -
     the record of every announced run, kept for later reprocessing.
  2. Fetches the run's change verdicts from the gold CHANGE LOG
     ({gold_table}_changes, ADR-0003) LEFT JOINed to the master table for
     current attributes, via Athena. DELETE verdicts are counted in the run
     registry but not yet published (no routing attribute exists for a
     deleted row - client delete routing is a pending decision).
  3. Reads the gold attribute mapping from S3 (channel gating lives HERE
     now, not in the Lambda).
  4. Builds one Pearson CloudEvents 1.0 envelope per product per channel
     (projected to that channel's approved attributes) and publishes each
     to the domain event bus, where the generated client-subscription
     rules route them.
  5. Updates the DynamoDB record to COMPLETED with the publish counts
     (or FAILED with the error, then re-raises so the job run fails
     visibly - never silent).

Event contract: ../../asyncapi.yaml. Standards: UIF
capabilities/event/{guidelines.md,streaming.md}. Plain boto3 + stdlib only,
so the same file runs as a Glue Python shell job and imports cleanly in
unit tests (tests/unit/test_event_fabric_processor.py).
"""

from __future__ import annotations

import gzip
import json
import re
import logging
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("event-fabric-processor")

# Publish retries: exponential backoff with jitter on throttles and transient
# failures. The envelope is built once per occurrence, so a retried PutEvents
# reuses the same CloudEvents `id` (D6 - idempotent retry).
_RETRY_CONFIG = Config(retries={"mode": "standard", "max_attempts": 5})

s3 = boto3.client("s3")
events = boto3.client("events", config=_RETRY_CONFIG)
athena = boto3.client("athena")
dynamodb = boto3.client("dynamodb")
# Catalog metadata only (which master columns exist, with what type) - the
# same Glue read the Athena engine performs with this role's credentials, so
# it needs no new grant.
glue = boto3.client("glue")

# gold-mapping channel flag -> bus/clients.json channel tag
# Delivery channels - the gold master's x-channels vocabulary. One copy of
# each event is published per channel with a non-empty projection; the same
# record reaches every channel it is tagged for. The DetailType is
# per-operation ({domain}.{operation}.{version}), so the channel is
# discriminated by the envelope's `channel` field, which subscription rules
# filter on (target_type SQS -> event, KINESIS -> message, API -> api).
CHANNELS = ("event", "message", "api")

ATHENA_POLL_TIMEOUT_SECONDS = 120

REQUIRED_ARGS = [
    "run_id", "data_bucket", "mapping_key", "dynamo_table", "event_bus_name",
    "schema_name", "glue_database", "glue_table", "run_id_column",
    "athena_workgroup", "athena_output", "event_type", "event_source",
    "event_dataschema", "data_classification", "pii_present",
]
OPTIONAL_ARGS = ["changed_by", "routing_attribute", "changes_table", "join_key",
                 "pipeline_metrics_table", "publish_metrics_table",
                 "metrics_ttl_days", "subscription_configs_table",
                 "bootstrap_subscriptions_key", "business_key_columns",
                 "delta_chunk_size",
                 # S3 key template ({operation} placeholder) of the domain's
                 # PUBLISHED per-operation data schemas: when set, payloads
                 # are strictly projected to each schema's declared shape
                 # before publish. Unset = no filtering (today's behavior).
                 "event_schema_key"]

# Records ledgered to Table 3 (and released from memory) every this-many
# rows. THE memory ceiling: the 2026-09-24 OOM (exit 137) was 511k full
# master rows + their envelopes materialised at once on a 1GB python shell.
DEFAULT_DELTA_CHUNK_SIZE = 5000

# The gold change log (ADR-0003): {gold_table}_changes, one row per change
# verdict with op INSERT/UPDATE/DELETE/REFRESH, run_id, and the entity key.
# The run's events are DRIVEN from it (UIFPE-184 follow-up): the master
# table alone can never see DELETE verdicts, because deleted rows are gone.
DEFAULT_JOIN_KEY = "product_id"
DELETE_OPS = {"DELETE"}

# The client-subscription rules filter on data.<ROUTING_ATTRIBUTE>; it must
# therefore be mapping-enabled on EVERY channel, or events silently stop
# matching any rule. Validated per run - fail closed, never silent-drop.
DEFAULT_ROUTING_ATTRIBUTE = "organizationCode"

# PutEvents accepts at most 10 entries per call.
PUT_EVENTS_BATCH_SIZE = 10

# delta OP -> the CDC operation used in DetailTypes, artifact names, and
# Table 3 publish_ids (mirrors the config's operation_suffix_by_op).
OP_TO_OPERATION = {"INSERT": "created", "UPDATE": "updated", "DELETE": "deleted"}

# Family major this processor emits today. Multi-major fan-out (v2 siblings
# from Apicurio) is future scope; v1 is the base artifact - no suffix in
# artifact names, per the tiers/versions convention.
PUBLISH_VERSION = "v1"

# Table 2 (pipeline-metrics) row identity for processor runs.
PIPELINE_MODE = "INCREMENTAL"

# BatchWriteItem accepts at most 25 items per call.
BATCH_WRITE_SIZE = 25

# Bootstrap subscriptions are DOMAIN data, not stack code: when
# subscription-configs holds NO rows for the domain, the seed items are read
# from s3://{data_bucket}/{--bootstrap_subscriptions_key} (a JSON list of
# DynamoDB-typed items, owned and uploaded by the domain) and inserted with
# conditional puts (never overwrites). No key configured = no bootstrap -
# the run counts every record no_subscribers and publishes nothing.


def parse_args(argv: list[str]) -> dict[str, str]:
    """Glue passes job arguments as --key value pairs. Parsed by hand (no
    awsglue import) so this module works identically in tests and in the
    Python shell runtime."""
    raw: dict[str, str] = {}
    i = 0
    while i < len(argv):
        token = argv[i]
        if token.startswith("--"):
            key = token[2:]
            value = argv[i + 1] if i + 1 < len(argv) and not argv[i + 1].startswith("--") else ""
            raw[key] = value
            i += 2 if value != "" or (i + 1 < len(argv) and not argv[i + 1].startswith("--")) else 1
        else:
            i += 1
    missing = [a for a in REQUIRED_ARGS if not raw.get(a)]
    if missing:
        raise ValueError(f"Missing required job arguments: {missing}")
    return raw


class _RunAlreadyRecorded(RuntimeError):
    """Raised after the registry and metrics records are already terminal,
    so the generic failure handler must not overwrite them."""


def main(argv: list[str]) -> dict[str, Any]:
    args = parse_args(argv)
    run_id = args["run_id"]
    run_started = time.time()

    # 1. Register the run FIRST (the reprocessing hook): even a run that
    # subsequently fails is on record and can be re-driven by re-invoking
    # with the same run_id.
    _register_run(args, run_id)

    # Table 2 (pipeline-metrics) row for this run - written IN_PROGRESS by
    # the trigger Lambda, keyed by THIS run's JobRunId. Glue passes no run
    # id to python-shell scripts, so it is resolved via the sparse
    # gsi_gold_run, then flipped to STARTED. A raw operator run has no row;
    # every pipeline-metrics touch is best-effort and skipped for it.
    event_run_id = _resolve_event_run_id(args, run_id)
    _pipeline_update(args, event_run_id,
                     "SET #s = :s, last_updated_at = :now",
                     {":s": {"S": "STARTED"}, ":now": {"S": _utcnow()}})

    try:
        # 2. The run's change verdicts from the gold CHANGE LOG, joined to
        # the master table for current attributes, via Athena - STREAMED,
        # never materialised whole (2026-09-24 OOM). Zero change rows for an
        # announced run is a data error - failed closed after the sweep.
        # Nested mapping columns are read first: the query CASTs them to
        # JSON, so payloads carry real arrays/objects instead of the Hive
        # text blob the first price deliveries shipped (2026-09-25).
        nested_specs = _load_nested_specs(args)
        changes = _load_run_changes(args, run_id, nested_specs)

        # 3. Channel gating from the gold attribute mapping (read here, in
        # the Glue job - the Lambda no longer touches it).
        channel_sets = _load_channel_sets(args)
        _validate_routing_attribute(args, channel_sets)

        # 3b. EMIT VALIDATION from subscription-configs (the runtime source
        # of truth - subscriptions.json is retired): an operation with no
        # ACTIVE/SUSPENDED subscription for {domain}.{op}.v1 is not emitted
        # (the spec's publish-side deprecation rule). An entirely empty
        # domain bootstraps the default subscriptions first. None = no table
        # configured (sandbox) = validation off, everything emits.
        subscribed_ops = _subscribed_operations(args)

        # 4. Project + publish, one envelope per product per channel,
        # batched onto the bus (PutEvents takes up to 10 entries per call).
        correlation_id = str(uuid.uuid4())
        published = {channel: 0 for channel in CHANNELS}
        failed = {channel: 0 for channel in CHANNELS}
        skipped = {channel: 0 for channel in CHANNELS}
        deletes = 0
        missing_master = 0
        no_subscribers = 0
        # One LOGICAL record per change verdict x version (v1 today) - the
        # Table 3 (publish-metrics) row. Channel entries reference their
        # record by index so outcomes aggregate per record even when a
        # record's entries span flush boundaries.
        records: list[dict[str, Any]] = []
        pending: list[dict[str, Any]] = []
        pending_meta: list[tuple[int, str]] = []  # (record index, channel tag)
        # Per-operation published data schemas (--event_schema_key), loaded
        # once per operation seen; None entries mean unfiltered.
        event_schemas: dict[str, Any] = {}

        def _flush() -> None:
            nonlocal pending, pending_meta
            if not pending:
                return
            results = _publish_batch(pending)
            for (rec_idx, tag), (ok, code_or_id, msg) in zip(pending_meta, results):
                rec = records[rec_idx]
                if ok:
                    published[tag] += 1
                    rec["entries_ok"] += 1
                    if rec["event_id"] is None:
                        # The ledgered event_id is the ENVELOPE id, not the
                        # bus-assigned EventId: rule targets deliver with
                        # InputPath = $.detail, so a dead letter carries only
                        # the envelope - the envelope id is the one value the
                        # DLQ recorder (and consumers deduping) can correlate
                        # on. Found live 2026-09-21; the PutEvents EventId is
                        # still logged per entry by _publish_batch.
                        rec["event_id"] = rec["envelope_id"]
                else:
                    failed[tag] += 1
                    rec["entries_failed"] += 1
                    if rec["reason"] is None:
                        rec["reason"], rec["error_msg"] = code_or_id, msg
            pending, pending_meta = [], []

        # Chunked ledgering: every chunk's Table 3 rows are written and the
        # records (with their envelopes) RELEASED, so memory stays flat at
        # one chunk regardless of run size.
        chunk_size = max(1, int(args.get("delta_chunk_size")
                                or DEFAULT_DELTA_CHUNK_SIZE))
        total_changes = 0
        rows_ok = rows_failed = 0
        versions_ok: dict[str, int] = {}
        versions_failed: dict[str, int] = {}

        def _ledger_records() -> None:
            nonlocal rows_ok, rows_failed
            if not records:
                return
            ok, bad, vok, vbad = _write_publish_metrics(
                args, run_id, event_run_id, records)
            rows_ok += ok
            rows_failed += bad
            for artifact, n in vok.items():
                versions_ok[artifact] = versions_ok.get(artifact, 0) + n
            for artifact, n in vbad.items():
                versions_failed[artifact] = versions_failed.get(artifact, 0) + n
            records.clear()
        for change in changes:
            total_changes += 1
            op = str(change.pop("_change_op", "")).upper()
            change_key = change.pop("_change_key", None)
            # Which channels this row's hash actually moved. A channel whose
            # hash did not move must not publish: without this, a change to an
            # attribute tagged message+api only (price's
            # priceLastModifiedTimestamp, product's affiliation2/3) still
            # emitted an event, so "an event" stopped meaning "the event
            # payload changed". Absent column -> True, so a fixture or an older
            # change log that predates the projection still publishes as before.
            row_channels = {ch for ch in CHANNELS
                            if change.pop(f"_ch_{ch}", True)}
            product = change  # remaining columns are the master row (may be empty)

            if op in DELETE_OPS:
                # A DELETE verdict has no master row and no attributes - in
                # particular no routing attribute, so no client rule could
                # match a tombstone today. Counted (registry + summary + log),
                # NOT published, until delete routing is decided with clients.
                deletes += 1
                logger.info("Run %s: DELETE verdict for %s (not published)", run_id, change_key)
                continue
            if not product:
                # Non-DELETE verdict whose master row is gone (e.g. deleted
                # by a later run). Counted visibly, skipped.
                missing_master += 1
                logger.warning(
                    "Run %s: %s verdict for %s has no master row - skipped", run_id, op, change_key
                )
                continue

            # Nested columns: inner keys become the mapping's canonical
            # (camelCase) field names and declared timestamps ISO-8601, so
            # the payload matches the mapping contract, not the physical
            # snake_case row shape.
            if nested_specs:
                _canonicalize_nested(product, nested_specs)

            # The business key prefers the master row's product_id (gold is
            # snake_case since 2026-09); the composite change_key ('|'-joined)
            # is the fallback for rows whose master lacks a product id.
            # The domain's business key: candidate columns from config
            # (--business_key_columns, comma-separated, first hit wins); the
            # legacy product chain is the fallback for configs predating it.
            # A candidate may itself be COMPOSITE ('|'-separated columns,
            # e.g. product_id|organization_code for stock, whose grain is one
            # row per product per organisation): it hits only when every
            # column is populated and renders '|'-joined, the Table 3
            # primary_key contract. Without this, two organisations' rows for
            # one product shared a publish_id and the BatchWriteItem rejected
            # the whole chunk ("item keys contains duplicates", 2026-09-30).
            candidates = [c.strip() for c in
                          (args.get("business_key_columns") or
                           "product_id,productId,ppid").split(",") if c.strip()]
            product_id = _business_key(product, candidates) or change_key
            if not product_id:
                raise ValueError(f"business key ({candidates}) missing on a "
                                 f"change row of runId={run_id}")
            operation = OP_TO_OPERATION.get(op, "updated")
            if subscribed_ops is not None and operation not in subscribed_ops:
                # No ACTIVE/SUSPENDED subscription for this detail-type -
                # counted visibly, not published (insert a subscription row
                # to turn the operation back on).
                no_subscribers += 1
                logger.info("Run %s: no active subscription for %s.%s.%s - "
                            "%s not emitted", run_id, args["schema_name"],
                            operation, PUBLISH_VERSION, change_key)
                continue
            # Same occurrence -> same CloudEvents id on every channel, so
            # cross-channel consumers can correlate and dedupe.
            event_id = str(uuid.uuid4())
            rec_idx: int | None = None
            for mapping_channel in CHANNELS:
                if mapping_channel not in row_channels:
                    # this channel's hash did not move for this row
                    skipped[mapping_channel] += 1
                    continue
                projected = _project(product, channel_sets[mapping_channel])
                if not projected:
                    skipped[mapping_channel] += 1
                    continue
                # The published data schema is the FINAL gate on payload
                # shape: fields the contract does not declare never leave,
                # whatever gold or the mapping carry.
                schema = _event_schema(args, operation, event_schemas)
                if schema:
                    projected = _apply_event_schema(projected, schema)
                envelope = _build_cloud_event(args, event_id, correlation_id, product_id, projected,
                                              operation=operation, channel=mapping_channel)
                if rec_idx is None:
                    rec_idx = len(records)
                    records.append({
                        "primary_key": str(product_id), "operation": operation,
                        "payload": envelope, "entries_ok": 0, "entries_failed": 0,
                        # envelope_id fills event_id on first bus acceptance.
                        "event_id": None, "envelope_id": event_id,
                        "reason": None, "error_msg": None,
                    })
                elif mapping_channel == "message":
                    # The message-channel projection is the fullest - keep it
                    # as the stored replay payload for this record.
                    records[rec_idx]["payload"] = envelope
                pending.append({
                    "EventBusName": args["event_bus_name"],
                    "Source": args["event_source"],
                    "DetailType": f"{args['schema_name']}.{operation}.{PUBLISH_VERSION}",
                    "Detail": json.dumps(envelope),
                })
                pending_meta.append((rec_idx, mapping_channel))
                if len(pending) == PUT_EVENTS_BATCH_SIZE:
                    _flush()
            if len(records) >= chunk_size:
                # pending references records by index: settle it first, then
                # ledger + release the chunk (fresh records list from 0).
                _flush()
                _ledger_records()
        _flush()
        if total_changes == 0:
            raise ValueError(
                f"No change rows with run_id={run_id} in "
                f"{args['glue_database']}.{_changes_table(args)}"
            )

        # 5. Table 3 first: one row per record x operation x version,
        # PUBLISHED and PUBLISH_FAILED both, each carrying the sent payload
        # as the replay source. Then the terminal Table 2 status, in its
        # vocabulary: every entry landed -> SUCCEEDED; some -> PARTIAL_FAILURE
        # (run completes; failures sit in gsi_recovery for the recovery job);
        # none -> FAILED (job run fails visibly).
        total_ok = sum(published.values())
        total_failed = sum(failed.values())
        if total_failed == 0:
            status = "SUCCEEDED"
        elif total_ok == 0:
            status = "FAILED"
        else:
            status = "PARTIAL_FAILURE"

        _ledger_records()  # the final partial chunk

        error_msg = (f"{total_failed} of {total_ok + total_failed} bus entries "
                     f"failed ({rows_failed} of {rows_ok + rows_failed} records)"
                     if total_failed else None)

        summary = {
            "runId": run_id, "changes": total_changes,
            "published": published, "failed": failed, "skipped": skipped,
            "recordsPublished": rows_ok, "recordsFailed": rows_failed,
            "deletes": deletes, "missingMaster": missing_master,
            "noSubscribers": no_subscribers,
            "correlationId": correlation_id, "status": status,
        }
        _pipeline_terminal(
            args, event_run_id, status=status,
            published_rows=rows_ok, failed_rows=rows_failed,
            versions_ok=versions_ok, versions_failed=versions_failed,
            run_started=run_started, error_msg=error_msg,
        )
        if status == "FAILED":
            _fail_run(args, run_id, error_msg)
            raise _RunAlreadyRecorded(error_msg)
        # Close out the registry record (PARTIAL_FAILURE runs complete: the
        # failed records are queued in gsi_recovery, and the recovery job -
        # or a deliberate replay of the run - is the recovery path).
        _complete_run(args, run_id, summary)
        if status == "PARTIAL_FAILURE":
            logger.warning("Run %s PARTIAL_FAILURE: %s", run_id, error_msg)
        logger.info("Run %s %s: %s", run_id, status, json.dumps(summary))
        return summary
    except Exception as exc:
        if not isinstance(exc, _RunAlreadyRecorded):
            _fail_run(args, run_id, str(exc))
            _pipeline_terminal(args, event_run_id, status="FAILED",
                               run_started=run_started, error_msg=str(exc))
        raise  # fail the job run visibly - never silent


# ---------- Emit validation (deployed bus rules) + bootstrap ----------

def _subscribed_operations(args: dict[str, str]) -> set[str] | None:
    """Operations with at least one LIVE delivery route on the domain bus.

    2026-09-24 requirement: validate against the DEPLOYED EventBridge state
    - bus name, rule, target - not subscription-configs rows. A table row
    can claim ACTIVE while its rule was never provisioned (the seeds), and
    a rule without a target delivers nothing; the bus is the truth about
    deliverability. An op is subscribed when some rule on the domain bus
    matches its detail-type {domain}.{op}.v1 AND carries at least one
    target. A DISABLED rule still counts - mirroring the previous
    SUSPENDED-still-emits semantics, unchanged.

    Everything around the check is unchanged: returns None when no
    subscription table is configured (sandbox - validation off), and a
    domain with NO matching rules at all still bootstraps the seed
    subscription ROWS (conditional puts - never overwrites) before
    re-checking the bus once; the provisioner materialises those rows into
    rules asynchronously, so the bootstrap run itself typically counts
    no_subscribers and the NEXT run emits."""
    table = args.get("subscription_configs_table")
    if not table:
        return None
    schema = args["schema_name"]
    bus = args["event_bus_name"]
    detail_types = {op: f"{schema}.{op}.{PUBLISH_VERSION}"
                    for op in OP_TO_OPERATION.values()}

    def counts() -> tuple[set[str], int]:
        subscribed: set[str] = set()
        matching_rules = 0
        kwargs: dict[str, str] = {"EventBusName": bus}
        while True:
            page = events.list_rules(**kwargs)
            for rule in page.get("Rules", []):
                try:
                    pattern = json.loads(rule.get("EventPattern") or "{}")
                except ValueError:
                    continue
                rule_dts = pattern.get("detail-type") or []
                ops = [op for op, dt in detail_types.items() if dt in rule_dts]
                if not ops:
                    continue
                matching_rules += 1
                if _rule_has_target(bus, rule.get("Name", "")):
                    subscribed.update(ops)
            if "NextToken" not in page:
                break
            kwargs["NextToken"] = page["NextToken"]
        return subscribed, matching_rules

    subscribed, total = counts()
    if total == 0:
        logger.warning("Bus %s carries no %s subscription rules - "
                       "bootstrapping the domain's seed subscription rows",
                       bus, schema)
        _bootstrap_subscriptions(args, table)
        subscribed, _ = counts()
    logger.info("Emit validation for %s (bus %s): subscribed operations = %s",
                schema, bus, sorted(subscribed) or "none")
    return subscribed


def _rule_has_target(bus: str, rule_name: str) -> bool:
    """A rule with no target delivers nothing - it does not count."""
    if not rule_name:
        return False
    try:
        return bool(events.list_targets_by_rule(
            Rule=rule_name, EventBusName=bus).get("Targets"))
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ResourceNotFoundException":
            return False
        raise


def _bootstrap_subscriptions(args: dict[str, str], table: str) -> None:
    """Insert the domain's seed subscriptions when the table is empty for
    the domain. The seeds are domain-owned data in S3
    (--bootstrap_subscriptions_key, a JSON list of DynamoDB-typed items) -
    the shared stack carries no per-domain rows. Conditional on the key not
    existing, so a concurrent run or a later real subscription is never
    overwritten."""
    key = args.get("bootstrap_subscriptions_key") or ""
    if not key:
        logger.warning("No bootstrap_subscriptions_key configured for %s - "
                       "nothing seeded; records will count no_subscribers "
                       "until subscriptions exist", args["schema_name"])
        return
    items = _read_json(args["data_bucket"], key)
    if not isinstance(items, list):
        raise ValueError(f"bootstrap seed s3://{args['data_bucket']}/{key} "
                         "must be a JSON list of DynamoDB-typed items")
    for item in items:
        try:
            dynamodb.put_item(
                TableName=table,
                Item=item,
                ConditionExpression="attribute_not_exists(customer_id)",
            )
            logger.info("Bootstrapped subscription %s / %s",
                        item["customer_id"]["S"],
                        item["domain_detail_type"]["S"])
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                continue
            raise


# ---------- DynamoDB run registry ----------

def _register_run(args: dict[str, str], run_id: str) -> None:
    now = _utcnow()
    # Upsert with attempt increment: a fresh run starts at attempts=1, a
    # reprocess of the same run_id bumps attempts and resets status/timestamps.
    dynamodb.update_item(
        TableName=args["dynamo_table"],
        Key={"run_id": {"S": run_id}},
        UpdateExpression=(
            "SET #s = :started, received_at = :now, changed_by = :by, "
            "attempts = if_not_exists(attempts, :zero) + :one "
            "REMOVE completed_at, error_message"
        ),
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={
            ":started": {"S": "STARTED"},
            ":now": {"S": now},
            ":by": {"S": args.get("changed_by") or "unknown"},
            ":zero": {"N": "0"},
            ":one": {"N": "1"},
        },
    )
    logger.info("Registered run %s in %s (STARTED)", run_id, args["dynamo_table"])


def _complete_run(args: dict[str, str], run_id: str, summary: dict[str, Any]) -> None:
    dynamodb.update_item(
        TableName=args["dynamo_table"],
        Key={"run_id": {"S": run_id}},
        UpdateExpression=(
            "SET #s = :done, completed_at = :now, changes = :changes, "
            "published = :published, skipped = :skipped, deletes = :deletes, "
            "missing_master = :mm, no_subscribers = :ns, correlation_id = :cid"
        ),
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={
            ":done": {"S": "COMPLETED"},
            ":now": {"S": _utcnow()},
            ":changes": {"N": str(summary["changes"])},
            ":published": {"S": json.dumps(summary["published"])},
            ":skipped": {"S": json.dumps(summary["skipped"])},
            ":deletes": {"N": str(summary["deletes"])},
            ":mm": {"N": str(summary["missingMaster"])},
            ":ns": {"N": str(summary.get("noSubscribers", 0))},
            ":cid": {"S": summary["correlationId"]},
        },
    )


def _resolve_event_run_id(args: dict[str, str], run_id: str) -> str | None:
    """This run's own Glue JobRunId, which keys the Table 2 row the trigger
    Lambda wrote. Glue does not hand a python-shell script its run id, so:
    prefer a JOB_RUN_ID argument if the runtime supplied one, else look the
    row up on gsi_gold_run (gold_run_id + mode), retrying briefly - the
    Lambda writes the row right after StartJobRun, so it can lag a cold
    start by moments. None (raw operator run, or lookup miss) simply skips
    every pipeline-metrics touch."""
    supplied = args.get("JOB_RUN_ID") or args.get("job_run_id")
    if supplied:
        return supplied
    table = args.get("pipeline_metrics_table")
    if not table:
        return None
    for attempt in range(6):
        try:
            items = dynamodb.query(
                TableName=table,
                IndexName="gsi_gold_run",
                KeyConditionExpression="gold_run_id = :g AND #m = :mode",
                ExpressionAttributeNames={"#m": "mode"},
                ExpressionAttributeValues={
                    ":g": {"S": run_id},
                    ":mode": {"S": PIPELINE_MODE},
                },
            ).get("Items", [])
            waiting = [i for i in items
                       if i.get("status", {}).get("S") == "IN_PROGRESS"]
            if waiting:
                waiting.sort(key=lambda i: i.get("started_at", {}).get("S", ""))
                return waiting[-1]["event_run_id"]["S"]
        except Exception:  # noqa: BLE001 - metrics never fail the run
            logger.exception("gsi_gold_run lookup failed for run %s", run_id)
            return None
        time.sleep(2)
    logger.warning("No IN_PROGRESS pipeline-metrics row found for gold run %s "
                   "- raw run, or the trigger write lagged; Table 2 updates "
                   "skipped", run_id)
    return None


def _pipeline_update(args: dict[str, str], event_run_id: str | None,
                     expression: str, values: dict[str, Any],
                     names: dict[str, str] | None = None) -> None:
    """Best-effort update of this run's Table 2 (pipeline-metrics) row.
    Metrics must never change a run's outcome: every problem is logged, not
    raised, and the update is conditional on the row existing."""
    table = args.get("pipeline_metrics_table")
    if not table or not event_run_id:
        return
    try:
        dynamodb.update_item(
            TableName=table,
            Key={"event_run_id": {"S": event_run_id},
                 "mode": {"S": PIPELINE_MODE}},
            UpdateExpression=expression,
            ConditionExpression="attribute_exists(event_run_id)",
            ExpressionAttributeNames={"#s": "status", **(names or {})},
            ExpressionAttributeValues=values,
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            logger.info("No pipeline-metrics row %s/%s - update skipped",
                        event_run_id, PIPELINE_MODE)
        else:
            logger.exception("Could not update pipeline-metrics row %s", event_run_id)
    except Exception:  # noqa: BLE001 - metrics never fail the run
        logger.exception("Could not update pipeline-metrics row %s", event_run_id)


def _artifact_name(args: dict[str, str], operation: str) -> str:
    """Apicurio artifact / detail-type family name for an operation at the
    base major: {domain}.{entity}.{operation} - v1 carries no suffix."""
    return f"{args['schema_name']}.{args['schema_name']}.{operation}"


def _pipeline_terminal(args: dict[str, str], event_run_id: str | None, *,
                       status: str, published_rows: int | None = None,
                       failed_rows: int | None = None,
                       versions_ok: dict[str, int] | None = None,
                       versions_failed: dict[str, int] | None = None,
                       run_started: float | None = None,
                       error_msg: str | None = None) -> None:
    """Terminal Table 2 update: SUCCEEDED / PARTIAL_FAILURE / FAILED with
    aggregate counts and the per-artifact versions maps."""
    now = _utcnow()
    expr = "SET #s = :s, completed_at = :now, last_updated_at = :now"
    values: dict[str, Any] = {":s": {"S": status}, ":now": {"S": now}}
    if published_rows is not None:
        expr += ", published_row_count = :ok, failed_row_count = :bad"
        values[":ok"] = {"N": str(published_rows)}
        values[":bad"] = {"N": str(failed_rows or 0)}
    if versions_ok is not None:
        triggered = sorted(set(versions_ok) | set(versions_failed or {}))
        expr += ", versions_triggered = :vt, versions_succeeded = :vs, versions_failed = :vf"
        values[":vt"] = {"L": [{"S": a} for a in triggered]}
        values[":vs"] = {"M": {a: {"N": str(n)} for a, n in versions_ok.items()}}
        values[":vf"] = ({"M": {a: {"N": str(n)} for a, n in versions_failed.items()}}
                         if versions_failed else {"NULL": True})
    if run_started is not None:
        expr += ", duration_seconds = :dur"
        values[":dur"] = {"N": str(int(time.time() - run_started))}
    if error_msg:
        expr += ", error_msg = :err"
        values[":err"] = {"S": error_msg[:1000]}
    _pipeline_update(args, event_run_id, expr, values)


def _write_publish_metrics(args: dict[str, str], run_id: str,
                           event_run_id: str | None,
                           records: list[dict[str, Any]],
                           ) -> tuple[int, int, dict[str, int], dict[str, int]]:
    """Table 3: one row per record x operation x version, PUBLISHED and
    PUBLISH_FAILED both, written in BatchWriteItem chunks with the sent
    payload gzip'd as the replay source. Returns (published records, failed
    records, versions_succeeded, versions_failed).

    A record is PUBLISHED only when EVERY channel entry was accepted;
    otherwise PUBLISH_FAILED with recovery_scope set, so the whole record
    re-sends on recovery (consumers dedupe on the CloudEvents id).
    Best-effort by design - a metrics write problem is logged, never fails
    the run.
    """
    domain = args["schema_name"]
    ttl_days = int(args.get("metrics_ttl_days") or "90")
    ttl = int(time.time()) + ttl_days * 86400
    now = _utcnow()
    rows_ok = 0
    rows_failed = 0
    versions_ok: dict[str, int] = {}
    versions_failed: dict[str, int] = {}
    items: list[dict[str, Any]] = []

    for rec in records:
        if rec["entries_ok"] + rec["entries_failed"] == 0:
            continue  # every channel projection was empty - nothing attempted
        artifact = _artifact_name(args, rec["operation"])
        ok = rec["entries_failed"] == 0
        if ok:
            rows_ok += 1
            versions_ok[artifact] = versions_ok.get(artifact, 0) + 1
        else:
            rows_failed += 1
            versions_failed[artifact] = versions_failed.get(artifact, 0) + 1

        payload_json = json.dumps(rec["payload"])
        item: dict[str, Any] = {
            "publish_id": {"S": f"{domain}#{run_id}#{rec['primary_key']}"
                                f"#{rec['operation']}#{PUBLISH_VERSION}"},
            "domain": {"S": domain},
            "gold_run_id": {"S": run_id},
            "primary_key": {"S": rec["primary_key"]},
            "operation": {"S": rec["operation"]},
            "version": {"S": PUBLISH_VERSION},
            "event_run_id": {"S": event_run_id or "unknown"},
            "status": {"S": "PUBLISHED" if ok else "PUBLISH_FAILED"},
            "payload": {"B": gzip.compress(payload_json.encode("utf-8"))},
            "payload_size_bytes": {"N": str(len(payload_json))},
            "published_at": {"S": now} if ok else {"NULL": True},
            "reason": {"NULL": True} if ok else {"S": str(rec["reason"])},
            "error_msg": ({"NULL": True} if ok
                          else {"S": str(rec["error_msg"] or "")[:1000]}),
            "attempt_count": {"N": "1"},
            "last_updated_at": {"S": now},
            "ttl": {"N": str(ttl)},
        }
        # GSI key attributes are OMITTED (not NULL) when absent, so the
        # sparse indexes hold exactly what they should.
        if ok:
            item["event_id"] = {"S": str(rec["event_id"])}
        else:
            item["recovery_scope"] = {"S": f"{domain}#{run_id}"}
        items.append(item)

    table = args.get("publish_metrics_table")
    if not table:
        if items:
            logger.warning("publish_metrics_table not configured - %d per-event "
                           "rows not recorded", len(items))
        return rows_ok, rows_failed, versions_ok, versions_failed

    for start in range(0, len(items), BATCH_WRITE_SIZE):
        chunk = items[start:start + BATCH_WRITE_SIZE]
        request = {table: [{"PutRequest": {"Item": i}} for i in chunk]}
        try:
            for attempt in range(5):
                response = dynamodb.batch_write_item(RequestItems=request)
                request = response.get("UnprocessedItems") or {}
                if not request:
                    break
                time.sleep(2 ** attempt * 0.2)
            if request:
                logger.error("Publish-metrics batch still has %d unprocessed "
                             "items after retries (run %s)",
                             sum(len(v) for v in request.values()), run_id)
        except Exception:  # noqa: BLE001 - metrics never fail the run
            logger.exception("Publish-metrics batch write failed for run %s", run_id)
    return rows_ok, rows_failed, versions_ok, versions_failed


def _business_key(product: dict[str, Any], candidates: list[str]) -> str | None:
    """First candidate whose column(s) are all populated on the master row,
    rendered as the record's business key. A candidate is one column or a
    '|'-separated composite; a composite joins its values with '|' in the
    declared order (never '#', the publish_id separator)."""
    for candidate in candidates:
        columns = [c.strip() for c in candidate.split("|") if c.strip()]
        values = [product.get(c) for c in columns]
        if columns and all(v not in (None, "") for v in values):
            return "|".join(str(v) for v in values)
    return None


def _fail_run(args: dict[str, str], run_id: str, message: str) -> None:
    try:
        dynamodb.update_item(
            TableName=args["dynamo_table"],
            Key={"run_id": {"S": run_id}},
            UpdateExpression="SET #s = :failed, completed_at = :now, error_message = :err",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":failed": {"S": "FAILED"},
                ":now": {"S": _utcnow()},
                ":err": {"S": message[:1000]},
            },
        )
    except Exception:  # noqa: BLE001 - never mask the original failure
        logger.exception("Could not mark run %s FAILED in the registry", run_id)


# ---------- Gold table via Athena ----------

def _changes_table(args: dict[str, str]) -> str:
    return args.get("changes_table") or f"{args['glue_table']}_changes"


# Marks the JSON-cast companion of a nested column in the Athena SELECT;
# private contract between _load_run_changes and _iter_query_rows.
_JSON_CAST_PREFIX = "_json_"


# Glue field types Trino casts to JSON directly. timestamp/date are NOT here:
# the gold tables are Iceberg, their timestamps surface as timestamp with
# time zone, and Trino cannot cast that to JSON (TYPE_MISMATCH, live failure
# jr_04b4401c 2026-09-25) - those fields are stringified in SQL instead.
_JSON_SAFE_PREFIXES = ("boolean", "tinyint", "smallint", "int", "integer",
                       "bigint", "float", "real", "double", "decimal",
                       "string", "varchar", "char")


def _is_json_safe(glue_type: str) -> bool:
    return glue_type.startswith(_JSON_SAFE_PREFIXES)


def _trino_type(glue_type: str) -> str:
    """Glue catalog spelling -> the Trino spelling a ROW declaration takes."""
    if glue_type == "string" or glue_type.startswith("char"):
        return "varchar"
    if glue_type == "int":
        return "integer"
    if glue_type == "float":
        return "real"
    return glue_type


def _split_struct_fields(spec: str) -> list[tuple[str, str]] | None:
    """"a:bigint,b:decimal(38,2),c:timestamp" -> [(name, type), ...], commas
    split only at depth 0 so parameterized types survive. None on anything
    that does not parse - the caller skips the column."""
    fields, depth, current = [], 0, ""
    for ch in spec:
        if ch in "<(":
            depth += 1
        elif ch in ">)":
            depth -= 1
        if ch == "," and depth == 0:
            fields.append(current)
            current = ""
        else:
            current += ch
    if current:
        fields.append(current)
    out: list[tuple[str, str]] = []
    for field in fields:
        name, sep, ftype = field.partition(":")
        if not sep or not name.strip():
            return None
        out.append((name.strip(), ftype.strip().lower()))
    return out


def _trino_json_select(col: str, glue_type: str) -> str | None:
    """The SQL expression selecting `col` as JSON, or None when its type
    cannot be safely serialized (the Hive blob then ships as before).
    array<struct<scalars>> becomes a typed transform that stringifies
    timestamp/date fields BEFORE the JSON cast; an array of JSON-safe
    primitives casts directly; anything deeper (nested structs/arrays/maps
    inside) is skipped rather than risked."""
    t = glue_type.strip().lower()
    struct = re.fullmatch(r"array<struct<(.+)>>", t, re.S)
    if not struct:
        flat = re.fullmatch(r"array<([^<>]+)>", t)
        if flat and _is_json_safe(flat.group(1).strip()):
            return f'CAST(m."{col}" AS JSON)'
        return None
    fields = _split_struct_fields(struct.group(1))
    if not fields:
        return None
    exprs, decls = [], []
    for name, ftype in fields:
        if _is_json_safe(ftype):
            exprs.append(f'x."{name}"')
            decls.append(f'"{name}" {_trino_type(ftype)}')
        elif ftype.startswith(("timestamp", "date")):
            exprs.append(f'CAST(x."{name}" AS varchar)')
            decls.append(f'"{name}" varchar')
        else:
            return None
    return (f'CAST(transform(m."{col}", x -> CAST(ROW({", ".join(exprs)}) '
            f'AS ROW({", ".join(decls)}))) AS JSON)')


def _nested_json_selects(args: dict[str, str],
                         nested_specs: dict[str, Any]) -> dict[str, str]:
    """physical column -> JSON select expression, for the nested mapping
    columns that EXIST on the master table with a serializable type - so a
    mapping column the table does not (yet) carry can never fail the whole
    query with COLUMN_NOT_FOUND. Fail-OPEN: if the catalog read fails, no
    casts - the run proceeds with today's behavior (text blobs beat a dead
    run)."""
    if not nested_specs:
        return {}
    try:
        table = glue.get_table(DatabaseName=args["glue_database"],
                               Name=args["glue_table"])["Table"]
        types = {c["Name"].lower(): (c.get("Type") or "").lower()
                 for c in table.get("StorageDescriptor", {}).get("Columns", [])}
    except Exception:  # noqa: BLE001 - degraded payloads, never a dead run
        logger.warning("Glue catalog read failed for %s.%s - nested columns "
                       "ship without JSON casts this run",
                       args["glue_database"], args["glue_table"], exc_info=True)
        return {}
    selects: dict[str, str] = {}
    for phys in sorted(nested_specs):
        if phys not in types:
            continue
        select = _trino_json_select(phys, types[phys])
        if select:
            selects[phys] = select
        else:
            logger.warning("Nested column %s has unserializable type %s - "
                           "shipping without a JSON cast", phys, types[phys])
    return selects


def _load_run_changes(args: dict[str, str], run_id: str,
                      nested_specs: dict[str, Any] | None = None):
    """The run's change verdicts from {table}_changes (ADR-0003), LEFT JOINed
    to the master table for current attributes: c-side columns come back as
    _change_op/_change_key; m.* is the master row, absent for DELETE verdicts.
    Nested mapping columns are ALSO selected as CAST(... AS JSON): Athena
    renders an untyped array/row column as Hive text ("[{price_list_id=...")
    which is not JSON, so the payload shipped an unparseable blob (first seen
    live in the price deliveries, 2026-09-25); the JSON cast comes back
    typed `json` and replaces the blob in _iter_query_rows.

    The run id is inlined as a single-quote-escaped literal rather than
    passed via ExecutionParameters: the Glue Python shell runtime ships a
    botocore too old to know that Athena parameter, and escaping '' is the
    SQL-standard quoting Athena applies to parameters anyway.
    """
    nested_casts = "".join(
        f', {select} AS "{_JSON_CAST_PREFIX}{col}"'
        for col, select in _nested_json_selects(args, nested_specs or {}).items())
    escaped = str(run_id).replace("'", "''")
    # --join_key may be composite (comma-separated): the dev gold change log
    # keys on inventoryitemid,organizationid (both bigint), not a single
    # entity id. _change_key is the values joined with '#' for logging and
    # the missing_master counters.
    join_keys = [k.strip() for k in
                 (args.get("join_key") or DEFAULT_JOIN_KEY).split(",") if k.strip()]
    # Joined with '|' per the Table 3 contract: '#' is the publish_id
    # separator and must never appear inside a primary_key value.
    change_key = " || '|' || ".join(f'CAST(c."{k}" AS varchar)' for k in join_keys)
    on_clause = " AND ".join(f'm."{k}" = c."{k}"' for k in join_keys)
    query = (
        # One boolean per channel rather than the raw changed_channels array:
        # Athena renders an array as "[api, canonical, message]", which is not
        # valid JSON, so _convert would hand us that string to parse. Testing
        # membership in SQL keeps it typed. contains() is the Trino function -
        # array_contains() is Spark and does not exist here, whatever
        # ADR-0003:210 and lld-silver-to-gold-module.md:141 show.
        f'SELECT c."op" AS "_change_op", {change_key} AS "_change_key", '
        f'contains(c."changed_channels", \'event\') AS "_ch_event", '
        f'contains(c."changed_channels", \'message\') AS "_ch_message", '
        f'contains(c."changed_channels", \'api\') AS "_ch_api", m.*{nested_casts} '
        f'FROM "{args["glue_database"]}"."{_changes_table(args)}" c '
        f'LEFT JOIN "{args["glue_database"]}"."{args["glue_table"]}" m '
        f'ON {on_clause} '
        f"WHERE c.\"{args['run_id_column']}\" = '{escaped}'"
    )
    query_id = athena.start_query_execution(
        QueryString=query,
        WorkGroup=args["athena_workgroup"],
        ResultConfiguration={"OutputLocation": args["athena_output"]},
    )["QueryExecutionId"]
    _wait_for_query(query_id)
    return _iter_query_rows(query_id)


def _wait_for_query(query_id: str) -> None:
    deadline = time.monotonic() + ATHENA_POLL_TIMEOUT_SECONDS
    delay = 0.25
    while True:
        status = athena.get_query_execution(QueryExecutionId=query_id)["QueryExecution"]["Status"]
        state = status["State"]
        if state == "SUCCEEDED":
            return
        if state in {"FAILED", "CANCELLED"}:
            raise RuntimeError(
                f"Athena query {query_id} {state}: "
                f"{status.get('StateChangeReason', 'no reason given')}"
            )
        if time.monotonic() >= deadline:
            athena.stop_query_execution(QueryExecutionId=query_id)
            raise RuntimeError(
                f"Athena query {query_id} still {state} after "
                f"{ATHENA_POLL_TIMEOUT_SECONDS}s - stopped and failing the run"
            )
        time.sleep(delay)
        delay = min(delay * 2, 2.0)


def _iter_query_rows(query_id: str):
    """Rows as dicts keyed by column name, typed from ColumnInfo metadata,
    YIELDED page by page - the run is never materialised whole (the
    2026-09-24 OOM held 511k joined rows in one list). NULL columns are
    omitted so projection never emits null-only payloads."""
    columns: list[dict[str, str]] = []
    next_token = None
    first_page = True
    while True:
        kwargs: dict[str, Any] = {"QueryExecutionId": query_id, "MaxResults": 1000}
        if next_token:
            kwargs["NextToken"] = next_token
        page = athena.get_query_results(**kwargs)
        if first_page:
            columns = [
                {"name": c["Name"], "type": c["Type"].lower()}
                for c in page["ResultSet"]["ResultSetMetadata"]["ColumnInfo"]
            ]
        data_rows = page["ResultSet"]["Rows"]
        if first_page:
            data_rows = data_rows[1:]  # first row of the first page is the header
            first_page = False
        for raw in data_rows:
            record: dict[str, Any] = {}
            for col, datum in zip(columns, raw["Data"]):
                if "VarCharValue" not in datum:  # NULL - omit the attribute
                    continue
                record[col["name"]] = _convert(datum["VarCharValue"], col["type"])
            # A "_json_<col>" companion (CAST ... AS JSON, typed json so
            # _convert parsed it) REPLACES the column's Hive-text rendering.
            for key in [k for k in record if k.startswith(_JSON_CAST_PREFIX)]:
                record[key[len(_JSON_CAST_PREFIX):]] = record.pop(key)
            yield record
        next_token = page.get("NextToken")
        if not next_token:
            return


def _convert(value: str, athena_type: str) -> Any:
    if athena_type in {"tinyint", "smallint", "integer", "int", "bigint"}:
        return int(value)
    if athena_type in {"double", "float", "real", "decimal"}:
        return float(value)
    if athena_type == "boolean":
        return value.lower() == "true"
    if athena_type == "json":
        return json.loads(value)
    if athena_type.startswith(("array", "map", "row", "struct")):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


# ---------- Channel gating from the gold attribute mapping ----------

def _load_channel_sets(args: dict[str, str]) -> dict[str, set[str]]:
    """{'event': {...}, 'message': {...}, 'api': {...}} - goldColumnNames with
    channels.<name> == true. Fails closed: a missing/malformed mapping raises
    rather than publishing unvetted attributes."""
    mapping = _read_json(args["data_bucket"], args["mapping_key"])
    columns = mapping.get("columns") if isinstance(mapping, dict) else None
    if not isinstance(columns, list):
        raise ValueError(
            f"'columns' missing in mapping s3://{args['data_bucket']}/{args['mapping_key']}"
        )
    channel_sets: dict[str, set[str]] = {name: set() for name in CHANNELS}
    for column in columns:
        channels = column.get("channels")
        if not isinstance(channels, dict):
            continue
        for name in channel_sets:
            if channels.get(name) is True:
                channel_sets[name].add(column["goldColumnName"])
    logger.info(
        "Loaded channel attribute sets: event=%d message=%d api=%d",
        len(channel_sets["event"]), len(channel_sets["message"]), len(channel_sets["api"]),
    )
    return channel_sets


_PHYSICAL_RE = re.compile(r"(?<=[a-z0-9])([A-Z])")


def _physical(name: str) -> str:
    """camelCase logical name -> snake_case gold column (Schema Standards N8)."""
    return _PHYSICAL_RE.sub(r"_\1", name).lower()


_CAMEL_RE = re.compile(r"_([a-z0-9])")


def _camel(name: str) -> str:
    """snake_case physical name -> camelCase logical name (inverse of
    _physical) - the fallback for a nested field the mapping does not list."""
    return _CAMEL_RE.sub(lambda m: m.group(1).upper(), name.lower())


# Trino's row/timestamp-to-JSON rendering: "2017-08-01 00:00:00.000",
# occasionally suffixed " UTC". Anything else passes through untouched.
_TRINO_TS_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}(?:\.\d+)?)(?: UTC)?$")


def _iso8601(value: str) -> str:
    m = _TRINO_TS_RE.match(value)
    return f"{m.group(1)}T{m.group(2)}Z" if m else value


def _load_nested_specs(args: dict[str, str]) -> dict[str, dict[str, Any]]:
    """physical gold column -> {canonical, fields} for mapping columns of
    kind "nested": fields keys the PHYSICAL rendering of each declared field
    name to its canonical name + type, so payload elements can be renamed to
    the contract's camelCase and declared timestamps converted to ISO-8601.
    Dotted goldColumnNames (struct sub-paths) are skipped - they never
    project as flat columns. Fails closed on a missing/malformed mapping,
    exactly like _load_channel_sets."""
    mapping = _read_json(args["data_bucket"], args["mapping_key"])
    columns = mapping.get("columns") if isinstance(mapping, dict) else None
    if not isinstance(columns, list):
        raise ValueError(
            f"'columns' missing in mapping s3://{args['data_bucket']}/{args['mapping_key']}"
        )
    specs: dict[str, dict[str, Any]] = {}
    for column in columns:
        if column.get("kind") != "nested":
            continue
        name = column.get("goldColumnName") or ""
        if not name or "." in name:
            continue
        fields: dict[str, dict[str, Any]] = {}
        for field in column.get("fields") or []:
            fname = field.get("name")
            if fname:
                fields[_physical(fname)] = {"name": fname,
                                            "type": field.get("type")}
        specs[_physical(name)] = {"canonical": name, "fields": fields}
    return specs


def _canonicalize_nested(product: dict[str, Any],
                         nested_specs: dict[str, dict[str, Any]]) -> None:
    """In place: each nested column's parsed JSON elements get the mapping's
    canonical field names and ISO-8601 timestamps. A value that is not a
    parsed list (NULL, or a run where the JSON cast was unavailable and the
    Hive text blob came through) is left untouched - degraded, never lost."""
    def _element(element: Any, fields: dict[str, dict[str, Any]]) -> Any:
        if not isinstance(element, dict):
            return element
        out: dict[str, Any] = {}
        for key, value in element.items():
            spec = fields.get(key.lower())
            if spec:
                if spec.get("type") == "timestamp" and isinstance(value, str):
                    value = _iso8601(value)
                out[spec["name"]] = value
            else:
                out[_camel(key)] = value
        return out

    for phys, spec in nested_specs.items():
        value = product.get(phys)
        if isinstance(value, list):
            product[phys] = [_element(el, spec["fields"]) for el in value]


def _event_schema(args: dict[str, str], operation: str,
                  cache: dict[str, Any]) -> dict[str, Any] | None:
    """The domain's PUBLISHED data schema for this operation, from
    s3://{data_bucket}/{--event_schema_key with {operation} filled} - the
    contract _apply_event_schema enforces. None (with a warning when one was
    configured but unreadable) means payloads ship unfiltered: degraded and
    visible, never a dead run. Cached per operation for the whole run."""
    template = args.get("event_schema_key")
    if not template:
        return None
    if operation in cache:
        return cache[operation]
    key = template.replace("{operation}", operation)
    schema: dict[str, Any] | None = None
    try:
        loaded = _read_json(args["data_bucket"], key)
        if isinstance(loaded, dict) and isinstance(loaded.get("properties"), dict):
            schema = loaded
        else:
            logger.warning("Event data schema s3://%s/%s has no properties "
                           "object - %s payloads ship UNFILTERED",
                           args["data_bucket"], key, operation)
    except Exception:  # noqa: BLE001 - unfiltered beats a dead run
        logger.warning("Event data schema s3://%s/%s unreadable - %s "
                       "payloads ship UNFILTERED", args["data_bucket"], key,
                       operation, exc_info=True)
    if schema:
        routing = args.get("routing_attribute") or DEFAULT_ROUTING_ATTRIBUTE
        if routing not in schema["properties"]:
            logger.warning("Routing attribute %s is not declared in %s - "
                           "filtered payloads will degrade partitionkey to "
                           "the bare business key", routing, key)
    cache[operation] = schema
    return schema


def _apply_event_schema(payload: dict[str, Any],
                        schema: dict[str, Any]) -> dict[str, Any]:
    """Strict projection to the published data schema: at every object level
    that declares properties WITH additionalProperties false, undeclared
    keys are dropped - recursively through nested properties and array
    items. A level without additionalProperties false passes through
    untouched: the schema, not this code, decides strictness. Closes the
    payload-vs-contract gap found live 2026-09-26 (priceListId and
    priceLastModifiedTimestamp shipped although price's schemas declare
    neither and set additionalProperties: false)."""
    def _filter(value: Any, node: Any) -> Any:
        if not isinstance(node, dict):
            return value
        if isinstance(value, dict):
            props = node.get("properties")
            if not isinstance(props, dict):
                return value
            if node.get("additionalProperties") is False:
                return {k: _filter(v, props.get(k, {}))
                        for k, v in value.items() if k in props}
            return {k: _filter(v, props[k]) if k in props else v
                    for k, v in value.items()}
        if isinstance(value, list) and isinstance(node.get("items"), dict):
            return [_filter(element, node["items"]) for element in value]
        return value
    return _filter(payload, schema)


def _project(product: dict[str, Any], allowed: set[str]) -> dict[str, Any]:
    """Only the channel's approved attributes leave on it - nothing is
    synthesized. Case-insensitive match (gold columns are lowercase, the
    mapping camelCase); the output key is the mapping's canonical name."""
    # gold columns are the PHYSICAL snake_case rendering of the mapping's
    # camelCase name (N8); the payload key is the canonical name
    canonical_by_lower = {_physical(name).lower(): name for name in allowed}
    canonical_by_lower.update({name.lower(): name for name in allowed})
    projected: dict[str, Any] = {}
    for field, value in product.items():
        canonical = canonical_by_lower.get(field.lower())
        if canonical is not None:
            projected[canonical] = value
    return projected


# ---------- CloudEvents envelope + bus publish ----------

def _build_cloud_event(
    args: dict[str, str],
    event_id: str,
    correlation_id: str,
    product_id: str,
    product_details: dict[str, Any],
    operation: str = "updated",
    channel: str = "message",
) -> dict[str, Any]:
    # event_type / event_dataschema may carry an {operation} placeholder
    # (com.pearson.product.product.{operation}.v1) — one contract per verdict.
    routing_attribute = args.get("routing_attribute") or DEFAULT_ROUTING_ATTRIBUTE
    return {
        "specversion": "1.0",
        "id": event_id,
        "source": args["event_source"],
        "type": args["event_type"].replace("{operation}", operation),
        "time": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "datacontenttype": "application/json",
        "dataschema": args["event_dataschema"].replace("{operation}", operation),
        # The envelope schema constrains subject to [A-Za-z0-9._-], so a
        # composite business key renders '.'-joined here (A…|07B -> A….07B);
        # '|' stays the separator everywhere else (primary_key, partitionkey).
        "subject": f"{args['schema_name']}/{str(product_id).replace('|', '.')}",
        "correlationid": correlation_id,
        # Business key + routing attribute, '|'-joined (A101708520216|MDM):
        # one product exists per organization, so partition ordering must be
        # per (product, org) - the routing attribute is mapping-enabled on
        # every channel by the fail-closed validation, so it is always in
        # product_details; a null VALUE degrades to the bare business key.
        "partitionkey": (f"{product_id}|{product_details[routing_attribute]}"
                         if product_details.get(routing_attribute)
                         else str(product_id)),
        # The delivery-channel discriminator (x-channels vocabulary):
        # subscription rules match detail-type + detail.channel, so one
        # record's per-channel copies each reach only their own subscribers.
        "channel": channel,
        "dataclassification": args["data_classification"],
        "piipresent": args["pii_present"].lower() == "true",
        "data": product_details,
    }


def _publish_batch(entries: list[dict[str, Any]]) -> list[tuple[bool, str | None, str | None]]:
    """One PutEvents call for up to 10 envelopes. Returns one
    (accepted, EventId-or-ErrorCode, error message) per entry, in order, so
    the caller can settle each Table 3 record - every rejection is logged
    with its event id and error, never silent.

    If the CALL itself fails (exception after the client's own retries),
    every entry in the batch is reported failed with reason
    BATCH_CALL_FAILED - written to Table 3 so recovery can pick them up -
    instead of failing the whole run.
    """
    try:
        response = events.put_events(Entries=entries)
    except Exception as exc:  # noqa: BLE001 - the batch outcome carries the error
        logger.exception("PutEvents call failed for a batch of %d entries "
                         "after retries", len(entries))
        return [(False, "BATCH_CALL_FAILED", str(exc)[:500])] * len(entries)

    results: list[tuple[bool, str | None, str | None]] = []
    for entry, result in zip(entries, response.get("Entries", [])):
        if result.get("ErrorCode"):
            event_id = json.loads(entry["Detail"]).get("id")
            logger.error(
                "PutEvents rejected %s eventId=%s: %s %s",
                entry["DetailType"], event_id,
                result.get("ErrorCode"), result.get("ErrorMessage"),
            )
            results.append((False, result["ErrorCode"], result.get("ErrorMessage")))
        else:
            results.append((True, result.get("EventId"), None))
    return results


def _validate_routing_attribute(args: dict[str, str], channel_sets: dict[str, set[str]]) -> None:
    """The client-subscription rules filter on data.<routing_attribute>.
    If any channel's mapping drops it, every event on that channel silently
    stops matching every rule - so broken gating fails the run instead."""
    routing = args.get("routing_attribute") or DEFAULT_ROUTING_ATTRIBUTE
    missing = [name for name, attrs in channel_sets.items() if routing not in attrs]
    if missing:
        raise ValueError(
            f"Routing attribute '{routing}' is not mapping-enabled on "
            f"channel(s) {missing} - client rules filter on data.{routing}, so "
            f"events on those channels would match no rule and be silently "
            f"dropped. Fix product.mapping.json (channels.<name>: true) or "
            f"change --routing_attribute."
        )


# ---------- helpers ----------

def _read_json(bucket: str, key: str) -> Any:
    try:
        response = s3.get_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        if exc.response["Error"]["Code"] in {"404", "NoSuchKey", "NotFound"}:
            raise ValueError(f"Required object missing: s3://{bucket}/{key}") from exc
        raise
    return json.loads(response["Body"].read().decode("utf-8"))


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    main(sys.argv[1:])
