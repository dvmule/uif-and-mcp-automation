# uif-event-{env}-delivery-metrics — failed-delivery contract (Table 4)

**FAILED subscriber deliveries only.** The event was on the bus; the
EventBridge target failed after its configured retries. Policy: **no
success rows**, and no processor/schema failures (those belong in
publish-metrics). One row per failed delivery at the grain **customer ×
record × operation × version × Gold run**, inserted by the DLQ Lambda,
updated by the redrive Lambda; a successful first-time delivery never
creates a row. Each row is **self-contained**: the payload is copied in at
DLQ time, so redrive works regardless of publish-metrics retention —
`publish_id` is lineage only, never a runtime dependency.
The table is provisioned in
[`../infrastructure/terraform/dynamodb.tf`](../infrastructure/terraform/dynamodb.tf)
(`aws_dynamodb_table.delivery_metrics`). Both writers are **built**: the
**DLQ Lambda** (`uif-event-{env}-dlq-recorder`, `src/dlq_recorder.py`, SQS
event source on each domain DLQ, partial-batch responses) and the
**redrive Lambda** (`uif-event-{env}-redrive`, `src/event_redrive.py`,
steward-invoked: `{"domain", "customer_id", "redriven_by", optional
"detail_type"/"limit"}`) — this document is the contract for both.

## Keys

| Key | Shape | Why |
|---|---|---|
| `domain_customer` (hash) | `{domain}#{customer_id}` | One `Query` returns everything outstanding for a customer in a domain. |
| `delivery_id` (range) | `{failed_at}#{event_id}` | Time-ordered; oldest failures first. |

## Attributes

UI ✓ = returned by the UI read path (everything else is internal).

| Attribute | UI | Meaning |
|---|---|---|
| `customer_id` | — | Consumer whose target rejected the event; derived from the DLQ message's `TARGET_ARN`. |
| `domain` | ✓ | Domain of the record. |
| `primary_key` | ✓ | Gold business key. Composite keys joined with `\|`; UI renders the parts. |
| `operation` | ✓ | `created` \| `updated` \| `deleted` — mapped from the delta `OP` by `operation_suffix_by_op`; the same value that composes `detail_type`. |
| `detail_type` | ✓ | EventBridge detail-type of the failed event, e.g. `product.updated.v2`. Carries the version; used to rebuild the envelope on redrive. |
| `event_id` | ✓ | The failed publish's **envelope id** (`detail.id`) — a dead letter arrives as the bare envelope because EventBridge applies the target's `InputPath` before dead-lettering (2026-09-21), so the wrapper's bus-assigned id never reaches the recorder; publish-metrics ledgers the same envelope id. **Overwritten with the new envelope id on redrive.** |
| `status` | ✓ | `FAILED` — outstanding. `REPLAYED` — redrive succeeded. `ABANDONED` — steward closed it without redrive (*future scope*). Terminal states keep the row until `ttl`. |
| `reason` | ✓ | DLQ `ERROR_CODE` (`SQS_QUEUE_ACCESS_DENIED`, `NO_PERMISSIONS`, `SDK_CLIENT_ERROR`, …). On redrive failure, the `SendMessage` error code. |
| `error_msg` | ✓* | DLQ `ERROR_MESSAGE`, or `SendMessage` error text on redrive failure. *Sanitised in the read path before display.* |
| `exhausted_by` | ✓ | DLQ `EXHAUSTED_RETRY_CONDITION` — `MaximumRetryAttempts` (target rejecting) \| `MaximumEventAgeInSeconds` (target unreachable too long). |
| `target_arn` | ✓ | Customer target that rejected the event **at the time of failure** — audit and UI only. Not used for routing: redrive resolves the current target from subscription-configs. |
| `failed_at` | ✓ | Event time (`body["time"]`) — when it was published. |
| `redrive_count` | ✓ | Redrive attempts — 0 on insert, atomic `ADD 1` per redrive. |
| `redriven_by` | ✓ | Identity that triggered the most recent redrive. `null` until first redrive. |
| `last_redriven_at` | ✓ | Timestamp of the most recent redrive attempt. `null` until first redrive. |
| `replayed_at` | ✓ | When status became `REPLAYED`. `null` otherwise. |
| `abandoned_at` / `abandoned_by` / `abandon_reason` | ✓ | Set only on `ABANDONED`. `null` otherwise. *Future scope.* |
| `payload` | — | Detail as originally published, copied from publish-metrics at DLQ time (**gzip → Binary**). Source for redrive. Never modified. |
| `gold_run_id` | — | Gold Glue `JobRunId`. |
| `event_run_id` | — | Glue run that published the failed event — processor or recovery. |
| `publish_id` | — | `{domain}#{gold_run_id}#{primary_key}#{operation}#{version}` — source row in publish-metrics. Lineage and internal idempotency key; not a runtime dependency (redrive reads `payload` from THIS row). |
| `rule_arn` | — | EventBridge rule that matched the event. |
| `redrive_run_id` | — | `rd_<uuid>` of the most recent redrive run on the run table (`mode = REDRIVE`, `worker_type = LAMBDA`). `null` until first redrive. |
| `dlq_received_at` | — | When the DLQ Lambda processed the message. |
| `last_updated_at` | — | Set on every write. |
| `ttl` | — | **Absent while `FAILED`.** Set once on `REPLAYED` / `ABANDONED`: terminal timestamp + 90 days. |

## Indexes

| Index | Key | Used by |
|---|---|---|
| Base | `domain_customer` + `delivery_id` | Redrive / UI: `Query domain_customer = {domain}#{customer_id}`, filter `status = FAILED`, oldest first. |
| `gsi_gold_run` | `gold_run_id` + `customer_id` | Ops: which customers are still owed events from Gold run X. |
| `gsi_event_id` | `event_id` | Idempotent DLQ processing — skip if a row already exists. |

`payload` is **excluded from all GSI projections** (`gsi_gold_run` projects
the UI/ops attributes, `gsi_event_id` is keys-only).

## Write rules

1. **DLQ Lambda (insert)** — `body["id"]` → publish-metrics `gsi_event_id` →
   lineage + payload → `PutItem`, `ConditionExpression
   attribute_not_exists(domain_customer)`. No `ttl` (two-phase TTL: an
   unresolved failure never silently expires; `ttl` is written once, on
   transition to `REPLAYED`/`ABANDONED`, as the terminal timestamp + 90d).
2. **Redrive Lambda (success)** — `UpdateItem`, `ConditionExpression
   status = :failed`: `SET status=:replayed, event_id=:new_id,
   redrive_run_id=:run, redriven_by=:who, last_redriven_at=:now,
   replayed_at=:now, ttl=:now_plus_90d, last_updated_at=:now
   ADD redrive_count :one`.
3. **Redrive Lambda (failed again)** — same condition, **no `ttl`**:
   `SET reason=:code, error_msg=:msg, redrive_run_id=:run,
   redriven_by=:who, last_redriven_at=:now, last_updated_at=:now
   ADD redrive_count :one`.
4. **Steward abandon** (*future scope*) — `ConditionExpression
   status = :failed`: `SET status=:abandoned, abandoned_at=:now,
   abandoned_by=:who, abandon_reason=:why, ttl=:now_plus_90d,
   last_updated_at=:now`.

## Redrive dispatch

Resolve the **current** target: `GetItem(subscription-configs,
pk = customer_id, sk = {domain}#{detail_type})`; require `status ∈ {ACTIVE,
SUSPENDED}`, else fail the redrive with `reason = SUBSCRIPTION_NOT_ACTIVE`.
Decompress this row's `payload` — the CloudEvents envelope exactly as
published — and `SendMessageBatch` (10) it to the subscription's current
`target.target_arn` as-is, with a fresh envelope `id`. **No EventBridge
wrapper**: first-time deliveries arrive unwrapped (the rule target's
`InputPath = $.detail` strips the wrapper), so a redriven message must look
identical to a first delivery. **No `PutEvents`; no fan-out.**

## UI read path

Returns only the ✓ attributes; `error_msg` sanitised.

## Dependencies not yet built (updated 2026-09-17)

- **publish-metrics per-event rows**: the processor now writes them (gzip
  payloads, `gsi_event_id`) — the DLQ Lambda's lineage/payload lookup has a
  real source.
- **subscription-configs**: exists ([subscription-configs.md](subscription-configs.md));
  redrive dispatch resolves the current target from
  `customer_id` + `domain_detail_type`.
- **DLQ Lambda built (2026-09-18)**: `uif-event-{env}-dlq-recorder`
  consumes the pipeline DLQ — write rule 1 implemented (gsi_event_id
  idempotency + conditional insert, no ttl), payload from Table 3's base
  row (or the DLQ'd event's own detail when lineage is missing), customer
  via gsi_rule_arn → gsi_target_arn → managed-target name parsing (the
  subscription seeds are still PENDING, so name parsing carries dev for
  now). Non-delivery DLQ messages (no RULE_ARN — e.g. the trigger Lambda's
  async dead letters) are logged in full and dropped, never written here.
  The **redrive Lambda is built (2026-09-21)**: rules 2–3 + dispatch as
  specified — current target from subscription-configs, stored payload
  re-sent directly with a fresh envelope id, optimistic settlements, one
  `mode = REDRIVE` pipeline-metrics row per run. `REDRIVE_ROLE_ARN` is now
  set on the provisioner, so new provisions write the `{policy_sid}r`
  grant; existing subscriptions pick it up on their next re-provision
  (same-account managed queues already authorize via the redrive role's
  own policy).
- **Dead-letter path hardened (2026-09-21, found live)**: every DLQ now
  carries the `events.amazonaws.com` SendMessage grant (SQS denies service
  principals by default — without it EventBridge silently dropped dead
  letters, `InvocationsFailedToBeSentToDlq`), and the provisioner points
  rule targets' `DeadLetterConfig` at the **domain** DLQ the recorder
  consumes, not the per-subscription DLQ (that one is the subscription
  queue's own RedrivePolicy target). The recorder also OMITS absent lineage
  keys instead of writing NULL — `gold_run_id` keys `gsi_gold_run`, and a
  NULL-typed index key fails the whole `PutItem`.
- **Per-operation DetailTypes landed (2026-09-17)**: the bus publishes
  `{domain}.{operation}.{version}` (e.g. `product.updated.v1`) — the
  channel-tag transition is over. The delivery channel is discriminated by
  the envelope's `channel` field (`event`=SQS, `message`=Kinesis,
  `api`=webhook); one record still publishes one copy per channel, and
  each subscription's rule matches detail-type + `detail.channel` for its
  own `target_type`.
- Spec's rule ARN examples use bus `uif-event-{env}-{domain}-bus`; the
  deployed domain bus is `uif-event-{env}-{domain}` — bus rename is an open
  decision.
