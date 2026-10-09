# UIFPE-453 — observed delivery outcomes from event-bus logs

A production-faithful POC for "did customer Y receive event X, and when?".
Namespace `uif-event-poc-*`, own Terraform state (`delivery-poc/dev`).

## What changed on 2026-09-30, and why it matters

This replaced two earlier POCs. Both worked, and neither told us anything
reliable about production, for two reasons found while trying to make one of
them faithful:

**The tables diverged.** Both projected `ALL` on every GSI and invented two
indexes production does not have. Concretely, `audit_consumer` and
`event_handler` both read `published_at` out of a `gsi_event_id` query — and
production projects that index `INCLUDE` *without* `published_at`. Against the
real table one handler raises `KeyError` and the other silently falls back to
the envelope's own time, writing the `FAILED` row at a **different sort key**
than the `ATTEMPTED` row: two rows per event instead of one upsert. The old
`delivery_writer` also queried `gsi_pending` and `publish-metrics.gsi_gold_run`,
neither of which exists in production — so that design silently assumed two
schema additions nobody had costed.

**The publishers diverged.** Both POC publishers ledgered the **bus-assigned
`EventId`** as `event_id`. The real `event_fabric_processor.py` deliberately
does the opposite — it ledgers the **envelope id** and discards the bus id,
because rules deliver with `InputPath = $.detail` so a dead letter carries only
the envelope, making the envelope id the one value the failure path can
correlate on.

So the tables here are **verbatim copies** of
`stacks/event/infrastructure/terraform/dynamodb.tf` — same keys, same indexes,
same `projection_type` and `non_key_attributes`, same TTL, same stream. Only
`name` differs. Do not tidy a projection to `ALL`: the projections are the
feature.

## The design under test

EventBridge stays the delivery engine — consumers receive exactly as they do
today. Nothing sits in the delivery path.

```
publisher ──► PutEvents ──► product bus ──► ledger row (publish-metrics)
                              │  (logging: level INFO, include_detail FULL)
                              │
  rule (per customer) ────────┴──► consumer target (SQS / Kinesis)   InputPath=$.detail
                                      │
                    delivery FAILS ──► EventBridge retries ──► DLQ
                                                                │
                                                     event-handler ──► FAILED
                              │
  bus logs ──► CloudWatch Logs ──► delivery-poller ──► DELIVERED
               (INVOCATION_SUCCESS)   ▲  every 15 min, FilterLogEvents over
                                      │  [watermark, now − 10 min]
                                      └── watermark in poller-state
```

`INVOCATION_SUCCESS` is emitted at **INFO** (not just TRACE) and carries
`event_id`, `details.rule_arn`, `details.target_arn` and — because
`include_detail = FULL` — `details.target_input`.

**The join, which is why no new column is needed.** The log's `event_id` is the
bus GUID, which we never persist. But `target_input` is what EventBridge sent
the target, and with `InputPath = $.detail` that is the CloudEvents envelope —
whose `id` is the `event_id` already in Table 3. So the poller parses the
envelope out of `target_input` and joins on an existing column. No new
attribute, no new GSI, **no change to the real processor.** With
`include_detail = NONE` there is no `target_input` and the design would instead
need the bus `EventId` persisted; that is the recorded trade, not the build.

## Why a scheduled poll and not a subscription filter

The first build pushed records to the writer with a CloudWatch Logs
subscription filter. Measured live on **2026-09-30 at 10,000 events × 2
consumers**:

| | |
|---|---|
| Records delivered to the writer | 20,020 |
| Lambda invocations | 7,666 |
| Average batch | **2.6** (min 1, max 13) |

A subscription filter flushes as records arrive rather than accumulating, so
the batching that would have made push viable never appears. Extrapolated to a
1M-event run × 10 consumers that is ~3.85M invocations and ~40M DynamoDB calls.
A 15-minute poll is a fixed ~96 invocations a day whatever the volume.

**The decisive reason is not cost, it is recovery.** The filter was push-only:
a log record that never arrived was never asked for again, so a missing
`DELIVERED` row was undetectable. The poller reads by timestamp against a
persisted watermark, so rewinding it — or calling the poller with an explicit
`{"start_iso", "end_iso"}` window — re-reads and re-writes idempotently.
**Retention on the bus log group is therefore the recovery horizon.**

Three things about it are load-bearing rather than incidental:

- **`FilterLogEvents`, not Logs Insights.** Insights bills per GB scanned and
  caps a result set at 10,000 rows — one busy window exceeds that silently.
- **The window trails live by `poll_lag_seconds`.** A record is only visible
  once *ingested*, so reading up to `now` would permanently skip anything
  ingested later. The poller reports the max ingestion lag it actually observed
  on every run; set the variable from that number.
- **Conditional `PutItem`, not `BatchWriteItem`.** `BatchWriteItem` cannot
  carry a `ConditionExpression`, and that condition is the invariant that
  failure evidence outranks derived success. The writes are parallelised with a
  thread pool instead.

## The key derivation — corrected 2026-10-01, read this before changing it

`delivery_id` is **`{envelope["time"]}#{event_id}`**, from the CloudEvents
envelope. Not the ledger's `published_at`.

That is what production already does ([dlq_recorder.py:144](../../../../stacks/event/src/dlq_recorder.py:144)),
even though the attribute it stores the value in is called `failed_at`, and
even though [delivery-metrics.md](../../../../stacks/event/docs/delivery-metrics.md)
defines that field as "Event time (`body["time"]`) — when it was published".

This POC keyed on `published_at` until 2026-10-01 and that was wrong. The two
values differ — the envelope is stamped at construction, `published_at` is
written after `PutEvents` returns — so DELIVERED rows would have landed on keys
that could never collide with production's FAILED rows: one delivery, two rows,
and the conditional write that is meant to let failure evidence win would have
evaluated an empty slot and never fired. Both POC writers shared the wrong
derivation, so the suite passed. S10b and S10c exist so it cannot happen again.

Two consequences worth keeping in mind:

- **The key needs no ledger read.** Both halves come out of the envelope, which
  reaches the poller via `target_input` and the handler via the DLQ body. A
  record whose ledger row is missing is still written at the right key, with
  `lineage_missing` set, and the enrichment columns back-fill on a later
  re-read (S12, S15).
- **`published_at` is enrichment only.** It is still recorded; it is not the key.

## Division of authority

| Outcome | Written by | Evidence | Durable? |
|---|---|---|---|
| `FAILED` | `event-handler` (mirrors production's `dlq-recorder`) | **domain DLQ** message | **yes** |
| `DELIVERED` | `delivery-poller` | `INVOCATION_SUCCESS` log record | **no — best effort, but re-readable** |

### Two DLQs per subscription, only one of which is a failure signal

| Queue | Receives | Drained by |
|---|---|---|
| **domain DLQ**, one per bus | EventBridge delivery exhaustion, from every rule | the shared recorder |
| per-subscription `-dlq` | the subscriber queue's own poison messages | nobody, by design |

Pointing a rule's `DeadLetterConfig` at the per-subscription queue was a real
bug, found live on 2026-09-21 — nothing consumed it, so failed deliveries were
invisible ([subscription_provisioner.py:331](../../../../stacks/event/src/subscription_provisioner.py:331)).
This POC reproduced that topology until 2026-10-01, and carried a
`failure_origin` attribute to tell the two failure kinds apart. Production needs
no such attribute, because consumer poison never reaches the domain DLQ, so it
is gone.

AWS states plainly that event-bus log records are delivered on a best-effort
basis. So the DLQ path is authoritative: a success log **cannot** overwrite a
`FAILED` row, and `INVOCATION_FAILURE` / `INVOCATION_DLQ` records are counted
as corroboration only.

The honest limit is unchanged in kind but reduced in consequence: a **missing**
`DELIVERED` row is still ambiguous between "not delivered" and "log record
never arrived", because no amount of re-reading conjures a record that was
never written. What changed is that every other cause of a missing row — a
failed run, a run that timed out, a record processed before its ledger row —
is now recoverable by re-reading the window. Decide what evidence standard you
need before adopting it.

Both writers import `outcome_row.py`, which owns the single sort-key
derivation. That is structural, not stylistic: it is what stops the two paths
disagreeing about `delivery_id`.

## Running it

```bash
# local — moto drives the real handlers
pip install -r ../local-simulation/requirements.txt
python ../local-simulation/run_poc_local.py

# terraform
terraform init -reconfigure -backend-config=backends/dev.s3.tfbackend
terraform plan
```

Then invoke `uif-event-poc-publisher` and check
`uif-event-poc-delivery-metrics`:

```json
{"count": 10}
```

That generates `jr_`-shaped Glue run ids for both `gold_run_id` and
`event_run_id`, matching production — where the two are different run ids, the
gold job's and the eventing job's. Both are echoed in the response, so query
`gsi_gold_run` with the `gold_run_id` it returns. Pass `{"gold_run_id":
"gr-live-1", "count": 10}` instead when you want a readable handle for a test
run; only the shape is defaulted, not enforced.

## Known constraints

- **Provider `~> 6.6` is mandatory**: `log_config` on `aws_cloudwatch_event_bus`
  arrived in AWS provider 6.6.0. On `~> 5.0` it fails at validate.
- **`hash_key`/`range_key` raise deprecation warnings on 6.x.** They stay, to
  match `stacks/event`. Migrate both together or neither.
- **IAM**: `echo-dev-github-actions` covers DynamoDB `table/*dev*` and events
  `rule/*dev*` only, with no SQS, Kinesis, Lambda-management or IAM statements.
  `uif-event-poc-*` matches none of it, so apply and destroy need a policy
  extension or a manual run by someone with PowerUser.
- **The local suite cannot cover log EMISSION.** moto does not implement bus
  logging or vended delivery, so the suite writes the records AWS would emit
  into a moto log group and lets the poller read them back with its real
  `FilterLogEvents` call. Windowing, pagination, the watermark, correlation and
  row writing are tested; that EventBridge actually emits what we expect is
  only settled by the live run. `POLL_FILTER_PATTERN` is empty in the suite
  because moto's filter-pattern support is partial and a silently-unmatched
  pattern would make the suite pass for the wrong reason.
- **A run that cannot finish its window commits nothing.** Records interleave
  across log streams, so the last page of a window is not guaranteed to hold
  the window's latest timestamp and a mid-window checkpoint could skip
  records. The next run therefore redoes the window — safe, but it will keep
  failing the same way until the work fits, so the run report says to lower
  `poll_max_window_seconds`. Production sizing for this is **unmeasured**.
- **`poller-state` is a new production resource** if this option is adopted.
  The real stack has no watermark table because the push design needed no
  cursor. Cost it.

## Two production changes option 6 requires

Neither is optional, and neither is in this POC's scope to make.

1. **The recorder's insert-only condition must be relaxed.**
   `dlq_recorder.py` writes with
   `ConditionExpression="attribute_not_exists(domain_customer)"`, which was
   safe while Table 4 held failures only. Once DELIVERED rows share the key, a
   DELIVERED row written first would **reject the FAILED insert and lose the
   failure**. It needs to become "insert, or overwrite anything not already
   FAILED". This POC's `event_handler` models the corrected behaviour.
2. **Do NOT rename `failed_at`** — the earlier advice in this file said to, and
   that was wrong. It is a misnomer (it holds the publish time) but it is one
   of `gsi_gold_run`'s `non_key_attributes`, and **DynamoDB does not allow a
   GSI projection to be modified** — renaming means recreating the index and
   backfilling it on a live table. The POC therefore writes **both** on the
   FAILED path: `failed_at` unchanged, so the projection and the ops UI keep
   working, plus `event_time` as the honest name. DELIVERED rows write only
   `event_time`; `failed_at` would be nonsense there, and nothing needs it,
   because a GSI query always returns the base keys and `delivery_id` already
   carries the value.

Also unresolved upstream: [delivery-metrics.md](../../../../stacks/event/docs/delivery-metrics.md)
says a dead letter arrives as the bare envelope, while
[subscription_provisioner.py:329](../../../../stacks/event/src/subscription_provisioner.py:329)
says it keeps the EventBridge wrapper. The real recorder reads both
(`detail.get("time") or body.get("time")`) and so does this POC, but one of the
two comments is wrong and somebody should settle which.
- **Unverified until the first live run**: whether `partition_key_path` is
  evaluated against the full event or the `InputPath`-filtered payload. If
  Kinesis delivery fails or single-shards, try `$.partitionkey`.
- **ADR-0025** records the POC2 delivery fabric as the accepted decision. It
  needs marking "under revision" to add this third option.
