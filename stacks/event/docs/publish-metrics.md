# uif-event-{env}-publish-metrics — per-event publish contract (Table 3)

**Every bus publish attempt, per record/operation/version — both
`PUBLISHED` and `PUBLISH_FAILED`.** One row per bus publish attempt at the
grain **domain × Gold run × record × operation × version**, written by the
Glue eventing job immediately after each `PutEvents` batch to
`uif-event-{env}-{domain}-bus`, based on the synchronous per-entry
response. Updated in place by recovery runs. **Never modified by the DLQ
Lambda.**

Provisioned in
[`../infrastructure/terraform/dynamodb.tf`](../infrastructure/terraform/dynamodb.tf)
(`aws_dynamodb_table.glue_metrics` — reshaped 2026-09-17 from the previous
run-level OVERALL table; run summaries now belong to
[pipeline-metrics](pipeline-metrics.md), Table 2). The processor writes
this table after every PutEvents batch; recovery (rules 2–3) is the
`uif-event-{env}-fabric-recovery` job (built 2026-09-24,
`src/glue/event_fabric_recovery.py` — steward-triggered per Gold run with
`--domain --gold_run_id --started_by`).

## Key

| Key | Shape | Notes |
|---|---|---|
| `publish_id` (hash) | `{domain}#{gold_run_id}#{primary_key}#{operation}#{version}` | Fully unique per publish attempt; spreads writes evenly across partitions. **No sort key** — the base table serves point reads only; range access comes from the GSIs. |

Separator: `#` everywhere in composed keys; `primary_key` values must not
contain it.

## Attributes

| Attribute | Meaning |
|---|---|
| `domain` | Domain of the record, e.g. `product`. Kept flat (also in `publish_id`) for filters and GSI projections. |
| `gold_run_id` | Gold Glue `JobRunId` whose delta produced this record. Flat for the join to the run table and to compose `recovery_scope`. |
| `primary_key` | Business key of the Gold record (e.g. `product_id`), from the Gold delta row; stable across runs. Composite Gold keys concatenated in fixed order with `\|`. Key column(s) per domain come from config: `processor.arguments.business_key_columns`, comma-separated candidates (first populated wins), where a candidate is one column or a `\|`-separated composite — `product_id\|organization_code` for stock, whose grain is (product, organisation). A scalar key on a multi-organisation grain makes rows share a `publish_id` and the `BatchWriteItem` chunk is rejected whole (found 2026-09-30). |
| `operation` | `created` \| `updated` \| `deleted` — the CDC operation, mapped from the delta `OP` by `operation_suffix_by_op`. The same value that composes the DetailType and the Apicurio artifact name. |
| `version` | Family major this row was published under (`v1`, `v2`, …) — the `.vN` sibling artifact, matching the DetailType suffix. Never the inner Apicurio minor. One row per sibling with artifact state != `DISABLED` (`ENABLED` and `DEPRECATED` both emit): a record eligible for N live majors produces N rows. Recovery groups by this before batching PutEvents. |
| `event_run_id` | Glue `JobRunId` of the run that most recently attempted this row — processor initially, **overwritten by the replay run** on recovery. Joins to pipeline-metrics for mode/started_by/sizing. |
| `status` | `PUBLISHED` — accepted by the bus (`EventId` returned). `PUBLISH_FAILED` — rejected by PutEvents; never reached the bus. `PUBLISHED` means accepted, **not** received by a consumer — downstream delivery failures live in [delivery-metrics](delivery-metrics.md). Recovery flips `PUBLISH_FAILED → PUBLISHED` on the **same row**. |
| `recovery_scope` | **Sparse GSI key.** `{domain}#{gold_run_id}`. Present only while `PUBLISH_FAILED`; `REMOVE`d on publish — so `gsi_recovery` holds only outstanding failures. |
| `event_id` | The **envelope id** (`detail.id`) of the latest successful publish — NOT the bus-assigned PutEvents `EventId` (changed 2026-09-21): rule targets deliver with `InputPath = $.detail`, and EventBridge applies the transform **before dead-lettering**, so the envelope id is the only correlator a dead letter carries — and it is what consumers dedupe on. `null` while `PUBLISH_FAILED`; overwritten on recovery (only the latest publish can still produce a DLQ message). GSI key for the DLQ Lambda's lineage lookup. |
| `payload` | The `Detail` object exactly as sent to the bus (metadata + data), **gzip → Binary**. Written once by the processor on every row (published and failed), never modified. Source of truth for REPLAY / FULL_REPLAY / redrive re-sends — a re-sent event is byte-identical to the original regardless of later Gold changes. Always under the 256 KB PutEvents limit, so always inline. **Excluded from all GSI projections.** |
| `payload_size_bytes` | Uncompressed payload size, for growth monitoring per domain. |
| `published_at` | Timestamp of the successful publish. `null` while `PUBLISH_FAILED`. |
| `reason` | PutEvents `ErrorCode` for the current failure (`ThrottlingException`, `InternalFailure`, `ValidationException`, …) or `BATCH_CALL_FAILED` when the whole PutEvents call errored after in-job retries. `null` once `PUBLISHED`. |
| `error_msg` | PutEvents `ErrorMessage` for the current failure. `null` once `PUBLISHED`. |
| `attempt_count` | Glue runs that attempted this row — 1 from the processor, atomic `ADD 1` per recovery run. In-job PutEvents retries are **not** counted; the row is written once per run with the final outcome. |
| `last_updated_at` | Set on every write — publish, failure, recovery. |
| `ttl` | Epoch seconds, aligned to pipeline-metrics' PRODUCT_DELTA retention for the same `gold_run_id`. |

## Indexes

| Index | Key | Projection | Used by |
|---|---|---|---|
| Base | `publish_id` | — | Point reads by full key (`GetItem`). No range access. |
| `gsi_recovery` (sparse, partition-key only) | `recovery_scope` | INCLUDE `primary_key`, `version`, `operation`, `event_run_id`, `attempt_count` | Recovery: `Query recovery_scope = {domain}#{gold_run_id}` → every outstanding `PUBLISH_FAILED` row for that Gold run, all versions. |
| `gsi_event_id` | `event_id` | INCLUDE `domain`, `gold_run_id`, `primary_key`, `version`, `operation`, `event_run_id` | DLQ Lambda: correlate a failed delivery back to its lineage and write delivery-metrics. |

## Write rules

1. **Processor** — one `BatchWriteItem` (≤ 25 items) per PutEvents batch.
   Accepted entries → `PUBLISHED` with `event_id` + `published_at`;
   rejected entries → `PUBLISH_FAILED` with `recovery_scope`, `reason`,
   `error_msg`. Both written in the same batch — a rejected entry is never
   silently dropped. `UnprocessedItems` retried with exponential backoff
   until empty.
2. **Recovery (success)** — `UpdateItem`, `ConditionExpression
   status = :failed`: `SET status=:published, event_run_id=:run,
   event_id=:id, published_at=:now, reason=:null, error_msg=:null,
   last_updated_at=:now REMOVE recovery_scope ADD attempt_count :one`.
3. **Recovery (failed again)** — same condition: `SET event_run_id,
   reason, error_msg, last_updated_at ADD attempt_count :one`;
   `recovery_scope` left in place (stays queued).
4. **DLQ Lambda** — read-only here (via `gsi_event_id`); its writes go to
   delivery-metrics.

Both recovery updates condition on `status = PUBLISH_FAILED`, so two
replays racing for the same row cannot both apply.

## Recovery flow — replaying a PUBLISH_FAILED row

Recovery re-publishes only rows that never reached the bus, working on the
same row the processor wrote — no new Table-3 row:

1. `Query gsi_recovery, recovery_scope = "{domain}#{gold_run_id}"` → every
   outstanding failure for the run, all versions.
2. Per row: decompress the stored `payload`, `PutEvents` (batched, max 10).
3. `EventId` returned → rule 2 (row leaves `gsi_recovery`). `ErrorCode`
   returned → rule 3 (stays queued, `attempt_count` incremented).

Run-level accounting: the processor's pipeline-metrics row gets
`replay_count + 1` at replay trigger; the recovery run writes its own
pipeline-metrics row (`mode = REPLAY`, same `gold_run_id`,
`source_row_count` = rows the recovery query returned, `started_by` = the
steward). A row that already reached the bus has no `recovery_scope`, so
the query never returns it — consumers see a replayed event as a first
delivery, never a duplicate.

## Storing payload (gzip → Binary)

Compress the Detail JSON with gzip and wrap in
`boto3.dynamodb.types.Binary` so the resource API writes DynamoDB type `B`
(not a string). Read back with `bytes(...)` + `gzip.decompress`. The
console shows `B` as base64 only — inspect via CLI.

## Open point + migration state (flagged 2026-09-17)

- **Replay data source — RESOLVED (2026-09-24)**: the recovery job
  re-sends the stored `payload` byte-identical — never re-read from Gold —
  so the envelope id (this table's `event_id`) is unchanged on replay and
  consumers deduping on it are safe either way.
- **Processor writer wired (2026-09-17)**: per-event `BatchWriteItem`
  logging lands here after every PutEvents batch (PUBLISHED/PUBLISH_FAILED
  with gzip'd payloads; a whole-call failure writes the batch as
  `BATCH_CALL_FAILED`). During the channel-fanout transition a record's
  row is `PUBLISHED` only when EVERY channel entry was accepted — a partial
  channel failure queues the whole record for recovery (consumers dedupe
  on the CloudEvents id). The recovery job implements rules 2-3
  (built 2026-09-24); a dedicated Event Replayer trigger Lambda/UI remains
  optional sugar over `glue start-job-run`.
- **IAM**: Query on the GSIs needs `.../index/*` grants for the recovery
  and DLQ Lambdas when they land; the processor role needs
  `dynamodb:BatchWriteItem` here.
- Hot-partition note: all of one gold run's outstanding failures share one
  `gsi_recovery` partition key; a mass-failure run concentrates index
  writes — accepted, since recovery drains the scope.
