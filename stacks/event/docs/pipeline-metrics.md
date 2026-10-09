# uif-event-{env}-pipeline-metrics — run-summary contract

**One row per eventing run at the `OVERALL` grain**, keyed by
`event_run_id` + `mode`, tracking run status, sizing used, and aggregate
publish counts. Covers all three pipelines — **processor**
(`INCREMENTAL`), **recovery** (`REPLAY`) and **redrive** (`REDRIVE`).
This is a run-level summary table — per-record detail lives in
`uif-event-{env}-publish-metrics` (Table 3, per-event rows).

Provisioned in
[`../infrastructure/terraform/dynamodb.tf`](../infrastructure/terraform/dynamodb.tf)
(`aws_dynamodb_table.pipeline_metrics`). **Writers are wired in a
follow-up** — the Domain Controller (trigger Lambda), the processor's
terminal update, the Event Replayer Lambda, and the Event Handler Lambda;
until that migration lands, today's run summary still goes to
publish-metrics' `OVERALL` row.

## Keys

| Key | Meaning | Example |
|---|---|---|
| `event_run_id` (hash) | The executing run's id — the Glue `JobRunId` for `INCREMENTAL` and `REPLAY` (returned synchronously by `StartJobRun` at trigger time); for `REDRIVE` the `rd_…` id minted by the Event Handler Lambda (no Glue job involved). | `jr_8f3a2c1e…` |
| `mode` (range) | `INCREMENTAL` (processor) \| `REPLAY` (recovery) \| `REDRIVE` (redrive). | `INCREMENTAL` |

## Attributes

| Attribute | Meaning |
|---|---|
| `domain` | Domain the fabric process is running for. |
| `gold_run_id` | The Gold run this eventing run serves. Present on `INCREMENTAL` (written by the Domain Controller when `GoldMergeCompleted` arrives) and `REPLAY` rows (passed in by the Event Replayer, pointing at the run being recovered). **`null` on `REDRIVE`** — a redrive is driven by a customer's delivery failures, not a Gold run. |
| `operation` | `OVERALL`; per-operation breakdown rows (INSERT/UPDATE/DELETE) reserved for future scope. |
| `status` | `IN_PROGRESS` → `STARTED` → `SUCCEEDED` \| `FAILED` \| `PARTIAL_FAILURE`. **Note: a different vocabulary from publish-metrics' current `INPROGRESS/COMPLETED/PARTIAL_FAILED` — writers translate, never mix.** |
| `worker_type`, `num_workers`, `timeout_minutes` | Sizing **actually used**. Processor/recovery: Spark worker type (`G.2X`/`G.4X`) and count. For redrive, `worker_type` doubles as the engine indicator — `"LAMBDA"` or `"PYTHON_SHELL"` — no separate engine column; `num_workers` stays `null` for redrive; `timeout_minutes` populated where meaningful. |
| `reason` | `SCHEMA_DRIFT` \| `OOM` \| `TIMEOUT` \| `TARGET_REJECTED` (redrive-only) \| `null`. |
| `error_msg` | Full Glue job error text, if any. |
| `source_row_count` | Rows expected to publish, from Gold's delta summary (processor/recovery); for redrive, how many items the redrive was asked to cover (live `SELECT COUNT` at trigger time). Same concept — "expected volume for this run" — sourced differently. |
| `published_row_count` / `failed_row_count` | Aggregate counts across all rows/tiers this run. `null` on an unclean `FAILED` (e.g. Glue TIMEOUT kill) unless a mid-run metrics checkpoint is implemented. |
| `active_tiers_processed` | Artifacts discovered in Apicurio for this run's families with state != `DISABLED` (`ENABLED` and `DEPRECATED`), exactly as registered — base artifact has no suffix, e.g. `["product.product.created", "product.product.created.v2"]`. `null` on redrive. ("tiers" predates the versions terminology and is deliberately left as-is — a written attribute, not prose.) |
| `versions_triggered` | Artifacts the job actually emitted for. Normally equals `active_tiers_processed`; differs only if an artifact was skipped (no eligible rows for its operation, or resolution failed after listing). `null` on redrive. |
| `versions_succeeded` | Per-artifact count of events **accepted by the bus**, e.g. `{"product.product.created": 250000, "product.product.created.v2": 248200}`. `sum(values) = published_row_count`. `null` on redrive. |
| `versions_failed` | Per-artifact count of events **rejected by PutEvents**. Only artifacts with failures appear; `null` if none (or on redrive). `sum(values) = failed_row_count`. |
| `started_at` / `completed_at` / `duration_seconds` | Run timing. |
| `replay_count` | How many times this run has been recovered — `null` on redrive rows (redrive tracks replays per-item in Table 3 instead). |
| `last_updated_at` | Set on every write — all status transitions touch it; the one consistent sort/filter field. |
| `ttl` | Epoch seconds — aligned to PRODUCT_DELTA retention (processor/recovery) or `started_at` + a shorter operational window (redrive). |
| `started_by` | Generalized across pipelines: `"SYSTEM"` (processor) \| a steward's identity, e.g. `ops@pearson.com` (recovery) \| the `customer_id` itself (redrive — this is where redrive's customer identity lives; no separate column). The human who triggered a redrive is tracked per-item on delivery-metrics' `redriven_by`, not duplicated here. |

## Indexes

| Index | Key | Used by |
|---|---|---|
| Base | `event_run_id` + `mode` | Status updates — the writer always holds both: the run id from the trigger response, the mode from the pipeline that started it. |
| `gsi_gold_run` (**sparse**) | `gold_run_id` + `mode` | Domain Controller idempotency: `Query gold_run_id = X`, skip if any row is `IN_PROGRESS`/`STARTED`/`SUCCEEDED`. Also answers "what has run for this Gold run" across processor and recovery. |

`gsi_gold_run` is sparse **by construction**: `REDRIVE` rows carry no
`gold_run_id`, so DynamoDB leaves them out entirely — "has this Gold run
been processed?" can never match a redrive, with no filter expression
needed. `mode` as the index sort key narrows further:
`gold_run_id = X AND mode = INCREMENTAL` returns only processor attempts.
Projection (implementation choice): INCLUDE `domain`, `operation`,
`status`, `started_at`, `completed_at`, `started_by` — enough for the
idempotency check and the ops view without dragging error text or the
versions maps into the index.

**Consistency caveat.** GSI reads are eventually consistent — DynamoDB
offers no strongly-consistent read on a GSI. Two `GoldMergeCompleted`
deliveries within the propagation window can both pass the idempotency
check; `event_run_id` is unknown until `StartJobRun` returns, so the row
cannot be written as a pre-flight lock. Glue's own
`ConcurrentRunsExceededException` is the backstop (note: the job allows 5
concurrent runs today, so the backstop only bites at saturation); a
stricter guarantee needs a conditional write on a separate item keyed by
`gold_run_id` — out of scope here.

## Publish-time logging to publish-metrics (Table 3 evolution)

The Glue eventing job records **every event** in
`uif-event-{env}-publish-metrics` immediately after each `PutEvents` call,
from the synchronous per-entry response:

| PutEvents entry result | Status written | Notes |
|---|---|---|
| `EventId` returned | `PUBLISHED` | Accepted by the bus; `EventId` stored as `event_id`. |
| `ErrorCode` returned | `PUBLISH_FAILED` | Never reached the bus. `reason` = ErrorCode, `error_msg` = ErrorMessage. No DLQ message will ever arrive for these. |

Per batch (max 10 entries per PutEvents): `put_events` → map each entry to
`PUBLISHED`/`PUBLISH_FAILED` → `batch_write_item` (≤ 25 per call).
`PUBLISHED` is optimistic — "accepted by the bus", not "received by the
consumer"; later target rejections land in delivery-metrics via the DLQ
Lambda and do not modify Table 3. If the `PutEvents` call itself fails
(exception, not per-entry) the job retries with backoff, then writes all
entries of the batch as `PUBLISH_FAILED` so recovery can pick them up.

On an unclean `FAILED` (e.g. TIMEOUT kill), this table's
`published_row_count` is an aggregate rollup only — what actually published
is Table 3's truth, and the Recovery Glue job
(`uif-event-{env}-fabric-recovery`) re-derives the gap from Table 3, never
from this aggregate.

## Migration state (flagged 2026-09-17)

- **INCREMENTAL writers wired (2026-09-17)**: the trigger Lambda writes
  `IN_PROGRESS` rows here (keyed by the StartJobRun JobRunId) and runs the
  `gsi_gold_run` idempotency check on `GoldMergeCompleted` deliveries
  (manual announcements bypass it — they are the operator replay tool, and
  prior attempts become `replay_count`); the processor resolves its row via
  `gsi_gold_run`, flips it `STARTED`, and writes the terminal status,
  counts, and versions maps.
- **REPLAY rows built (2026-09-24)**: the recovery job
  `uif-event-{env}-fabric-recovery` writes them (worker_type
  `PYTHON_SHELL`, `rp_…` ids when Glue's run id is not passed through) and
  bumps `replay_count` on the failed run's own `INCREMENTAL` row. `REDRIVE`
  rows come from the redrive Lambda (built 2026-09-21). Still to build: the
  Apicurio listing that feeds `active_tiers_processed`/`versions_*`.
- **`worker_type` semantics**: the processor is a python-shell job today,
  so Spark worker values record intent - but since 2026-09-25 each sizing
  tier's `max_capacity` (0.0625 = 1 GB, 1 = 16 GB) is translated to the
  run's REAL python-shell memory by the controller's fallback, closing the
  2026-09-24 OOM (511k rows killed at the 1 GB default, exit 137). The
  processor also ledgers Table 3 in `delta_chunk_size` chunks and streams
  Athena rows, so memory stays flat regardless of run size.
