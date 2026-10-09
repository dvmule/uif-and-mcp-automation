# ADR-0007 — Five DynamoDB tables for eventing

**Status:** Proposed
**Date:** 2026-08-26
**Scope:** Why eventing uses **five separate DynamoDB tables**, what each stores, sample items for reference, and how they interact at run level and event level.

**Explicit policies:**

- **`uif-event-{env}-publish-metrics`** stores **every bus publish attempt** — both `PUBLISHED` and `FAILED` rows, written per record/operation/tier. (Reversed from an earlier "failures only" policy — see [Table 3](#table-3--event-env-publish-metrics) for rationale.)
- **`uif-event-{env}-delivery-metrics`** stores **FAILED subscriber deliveries only** — after 3 retries.
- **`uif-event-{env}-pipeline-metrics`** stores **run-level** status per Glue run, keyed by `{run_id}` (processor/recovery/redrive)  with `operation` identifying which pipeline wrote the row (`OVERALL` / `REPLAY` / `REDRIVE`).
- **`product_changes`** is **not** updated with publish status.

**Related:**

- [ADR-0015](0015-eventbridge-event-centric-architecture-and-capacity.md)
- [ADR-0016](0016-domain-controller-vs-direct-glue-trigger.md)
- [`../eventing/event-centric-publish-reference.md`](../eventing/event-centric-publish-reference.md)

---

## Context

Event-centric publish:

```text
Gold MERGE → product_changes
  → Domain Controller → Event Fabric Processor Glue
  → PutEvents → uif-event-dev-product-bus
  → EventBridge rules → SQS / Kinesis / API
```

**Echo platform** uses `uif-echo-{env}-pipeline-metrics` for medallion Glue. **Eventing** uses the `uif-event-{env}-*` tables below — separate namespace and dashboards. The similarity in naming between `uif-echo-{env}-pipeline-metrics` (Echo platform) and `uif-event-{env}-pipeline-metrics` (eventing, this ADR) is coincidental — they are unrelated tables in unrelated systems; do not confuse them when building dashboards or alerts.

---

## Decision

**Adopt four DynamoDB tables for eventing:**

| # | Table | Grain |
|---|--------|--------|
| 1 | `uif-event-{env}-pipeline-configs` | Per pipeline config (processor and recovery configs are separate items) |
| 2 | `uif-event-{env}-pipeline-metrics` | Per processor Glue **run** |
| 3 | `uif-event-{env}-publish-metrics` | Per bus publish attempt (success and failure) |
| 4 | `uif-event-{env}-delivery-metrics` | Per **failed** subscriber delivery |
| 5 | `uif-event-{env}-subscription-configs` | **customer + domain** |


---

## Table 1 — `uif-event-{env}-pipeline-configs`

### Purpose

Per-pipeline configuration: delta table, event bus, Apicurio patterns, Glue profiles, batch settings. Each pipeline — the regular processor and the recovery job — has its own config item, keyed by `pipelineid`. This lets the recovery job carry independent sizing without embedding a `replay` profile inside the processor's own config block.

### Keys

| Key | Type | Example |
|-----|------|---------|
| `pipelineid` (hash) | String | `uif-event-dev-product-process` |
| `version` (range) | String | `latest` |

`domain` is retained as a plain (non-key) attribute on each item, so both the processor config (`uif-event-dev-product-process`) and the recovery config (`uif-event-dev-product-recovery`) can be tagged to the same `domain: "product"` without colliding on the partition key.

### Attributes

Configuration is nested; the paths below are relative to the item root. The three sample items that follow are worked instances of this shape.

| Attribute | Meaning |
|-----------|---------|
| `domain` | Plain (non-key) attribute — lets processor and recovery configs for the same domain be found together. |
| `enabled` | Master switch. A disabled pipeline is skipped at trigger time; no Glue run and no run row. |
| `processor.glue_job_name` | Glue job this pipeline starts — `uif-event-{env}-fabric-processor` or `-fabric-recovery`. |
| `processor.mode` | `INCREMENTAL` (processor) \| `REPLAY` (recovery). Passed to Glue as `--mode` and written to `uif-event-{env}-pipeline-metrics` as the sort key. |
| `delta.table` | Gold change-log table this pipeline reads, e.g. `product_changes`. |
| `delta.run_id_col` | **Name of the run column in the delta table** — `run_id`. Deliberately *not* `gold_run_id`: the Gold event carries `gold_run_id`, while the column in `product_changes` is `run_id`. Do not "correct" one to match the other. |
| `delta.op_col` | Column holding the CDC operation — `OP`, values `INSERT` \| `UPDATE` \| `DELETE`. |
| `eventing.event_bus_name` | Domain bus to publish on — `uif-event-{env}-{domain}-bus`. Passed to Glue as `--event_bus_name`. |
| `eventing.source` | `Source` stamped on every `PutEvents` entry; differs between processor and recovery, which is why subscriber rules filter on `detail-type` alone. |
| `eventing.detail_type_pattern` | `{domain}.{operation}` — the major suffix (`.v{n}`) is appended per emitted version, so the pattern itself carries no version. |
| `eventing.operation_suffix_by_op` | Maps the delta `OP` to the operation used in DetailTypes and Apicurio artifact names: `INSERT → created`, `UPDATE → updated`, `DELETE → deleted`. |
| `eventing.apicurio_artifact_pattern` | Base artifact name, `{domain}.{domain}.{operation}`. Majors are **discovered** as `.vN` siblings at run time, never configured here. |
| `eventing.emit_all_active_tiers` | When `true`, emit one event per non-`DISABLED` sibling rather than the latest only. The `tiers` in this key name predates the *versions* terminology and means **majors** — it is a live config key written by IaC (`domains/product/infrastructure/terraform/product_events.tf`) and read by the processor, so it is **deliberately not renamed** with the prose. Renaming it is a config-contract change and is tracked separately. |
| `publish_ops` | Delta operations this pipeline publishes. An `OP` absent from this list is read but never emitted. |
| `skip_run_when.total_zero` | Skip the run when the delta holds no rows for it. |
| `skip_run_when.no_publishable_ops` | Skip when every row's `OP` falls outside `publish_ops`. |
| `glue.max_retries` | Glue-level retries for the job run itself, not per-record `PutEvents` retries. |
| `glue.sizing_tiers` | **Ordered list.** The first tier whose `max_rows` is ≥ the run's row count wins; `max_rows: null` is the catch-all. Each entry supplies `name`, `max_rows`, `worker_type`, `num_workers`, `timeout_minutes`. |
| `batch.putevents_batch_size` | Entries per `PutEvents` call — `10`, the AWS maximum. |
| `batch.delta_chunk_size` | Delta rows read per chunk. |
| `batch.apicurio_fetch_once_per_run` | Fetch ACTIVE schemas once per run and cache the emit plan, rather than per row. |

### Sample item — `product` processor pipeline (dev)

```json
{
  "pipelineid": "uif-event-dev-product-process",
  "domain": "product",
  "version": "latest",
  "enabled": true,

  "processor": {
    "glue_job_name": "uif-event-dev-fabric-processor",
    "mode": "INCREMENTAL"
  },

  "delta": {
    "table": "product_changes",
    "run_id_col": "run_id",
    "op_col": "OP"
  },

  "eventing": {
    "event_bus_name": "uif-event-dev-product-bus",
    "source": "uif-event-dev-fabric-processor",
    "detail_type_pattern": "product.{operation}",
    "operation_suffix_by_op": {
      "INSERT": "created",
      "UPDATE": "updated",
      "DELETE": "deleted"
    },
    "apicurio_artifact_pattern": "product.product.{operation}",
    "emit_all_active_tiers": true
  },

  "publish_ops": ["INSERT", "UPDATE", "DELETE"],

  "skip_run_when": {
    "total_zero": true,
    "no_publishable_ops": true
  },

  "glue": {
    "max_retries": 0,
    "sizing_tiers": [{
            "name": "small",
            "max_rows": 100000,
            "worker_type": "G.1X",
            "num_workers": 2,
            "timeout_minutes": 120
        }, {
            "name": "medium",
            "max_rows": 500000,
            "worker_type": "G.2X",
            "num_workers": 4,
            "timeout_minutes": 240
        }, {
            "name": "large",
            "max_rows": null,
            "worker_type": "G.4X",
            "num_workers": 6,
            "timeout_minutes": 600
        }
    ]
},

  "batch": {
    "putevents_batch_size": 10,
    "delta_chunk_size": 5000,
    "apicurio_fetch_once_per_run": true
  }
}
```

### Sample item — `product` recovery pipeline (dev)

```json
{
  "pipelineid": "uif-event-dev-product-recovery",
  "domain": "product",
  "version": "latest",
  "enabled": true,

  "processor": {
    "glue_job_name": "uif-event-dev-fabric-recovery",
    "mode": "INCREMENTAL"
  },

  "delta": {
    "table": "product_changes",
    "run_id_col": "run_id",
    "op_col": "OP"
  },

  "eventing": {
    "event_bus_name": "uif-event-dev-product-bus",
    "source": "uif-event-dev-fabric-recovery",
    "detail_type_pattern": "product.{operation}",
    "operation_suffix_by_op": {
      "INSERT": "created",
      "UPDATE": "updated",
      "DELETE": "deleted"
    },
    "apicurio_artifact_pattern": "product.product.{operation}",
    "emit_all_active_tiers": true
  },

  "publish_ops": ["INSERT", "UPDATE", "DELETE"],

  "skip_run_when": {
    "total_zero": true,
    "no_publishable_ops": true
  },

  "glue": {
    "max_retries": 1,
    "sizing_tiers": [{
            "name": "small",
            "max_rows": 100000,
            "worker_type": "G.1X",
            "num_workers": 2,
            "timeout_minutes": 120
        }, {
            "name": "medium",
            "max_rows": 500000,
            "worker_type": "G.2X",
            "num_workers": 4,
            "timeout_minutes": 240
        }, {
            "name": "large",
            "max_rows": null,
            "worker_type": "G.4X",
            "num_workers": 6,
            "timeout_minutes": 600
        }
    ]
},

  "batch": {
    "putevents_batch_size": 10,
    "delta_chunk_size": 5000,
    "apicurio_fetch_once_per_run": true
  }
}
```

### Sample item — `price` processor pipeline (minimal reference)

```json
{
  "pipelineid": "uif-event-dev-price-process",
  "domain": "price",
  "version": "latest",
  "enabled": true,
  "processor": {
    "glue_job_name": "uif-event-dev-fabric-processor",
    "mode": "INCREMENTAL"
  },
  "delta": {
    "table": "price_changes",
    "run_id_col": "run_id",
    "op_col": "OP"
  },
  "eventing": {
    "event_bus_name": "uif-event-dev-price-bus",
    "source": "uif-event-dev-fabric-processor",
    "detail_type_pattern": "price.{operation}",
    "operation_suffix_by_op": {
      "INSERT": "created",
      "UPDATE": "updated",
      "DELETE": "deleted"
    },
    "apicurio_artifact_pattern": "price.price.{operation}",
    "emit_all_active_tiers": true
  },
  "publish_ops": ["INSERT", "UPDATE", "DELETE"],
"glue": {
    "max_retries": 0,
    "sizing_tiers": [{
            "name": "small",
            "max_rows": 100000,
            "worker_type": "G.1X",
            "num_workers": 2,
            "timeout_minutes": 120
        }, {
            "name": "medium",
            "max_rows": 500000,
            "worker_type": "G.2X",
            "num_workers": 4,
            "timeout_minutes": 240
        }, {
            "name": "large",
            "max_rows": null,
            "worker_type": "G.4X",
            "num_workers": 6,
            "timeout_minutes": 600
        }
    ]
}
}
```
### NOTE

> glue.max_retries is 0 for processor pipelines: a Glue retry re-reads the full delta and would republish rows already accepted by the bus, producing consumer duplicates. Failed processor runs are recovered via gsi_recovery, not re-run. Recovery pipelines may use 1 — their work list shrinks as rows succeed, so a retry is safe.
---

## Table 2 — `uif-event-{env}-pipeline-metrics`

### Purpose

**One row per eventing run at the `OVERALL` grain**, keyed by `event_run_id` and `mode`, tracking run status, sizing used, and aggregate publish counts. Covers all three pipelines — processor (`INCREMENTAL`), recovery (`REPLAY`) and redrive (`REDRIVE`). This is a run-level summary table — per-record detail lives in Table 3.

### Keys

| Key | Type | Example |
|-----|------|---------|
| `event_run_id` (hash) | The executing run's id — the AWS Glue `JobRunId` for `INCREMENTAL` and `REPLAY`, returned synchronously in the `StartJobRun` response at trigger time; for `REDRIVE` the `rd_…` run id minted by the Event Handler Lambda, since no Glue job is involved | `jr_8f3a2c1e9b7d4560a1f2e3d4c5b6a798` |
| `mode` (range) | `INCREMENTAL` (processor) \| `REPLAY` (recovery) \| `REDRIVE` (redrive) | `INCREMENTAL` |

### Attributes

| Attribute | Meaning |
|-----------|---------|
| `domain` | Domain for which the Glue Fabric process is running for |
| `gold_run_id` | The Gold run this eventing run serves. Present on `INCREMENTAL` rows (written by the Domain Controller when `GoldMergeCompleted` arrives) and on `REPLAY` rows (passed in by the Event Replayer Lambda, pointing back at the run being recovered). `null` on `REDRIVE` rows — a redrive is driven by a customer's delivery failures, not by a Gold run. |
| `operation` | `OVERALL`, with per-operation breakdown rows (`INSERT`/`UPDATE`/`DELETE`) reserved for future scope |
| `status` | `IN_PROGRESS` → `STARTED` → `SUCCEEDED` \| `FAILED` \| `PARTIAL_FAILURE` |
| `worker_type`, `num_workers`, `timeout_minutes` | Sizing actually used for this run. For processor/recovery: Spark worker type (`G.2X`/`G.4X`) and count. **For redrive, `worker_type` doubles as the `engine` indicator** — `"LAMBDA"` or `"PYTHON_SHELL"` — since it already means "what compute resource executed this run" for the other pipelines; no separate `engine` column exists. `num_workers` stays `null` for redrive (neither Lambda nor Python Shell has a worker-count concept); `timeout_minutes` is populated for both engines where meaningful. |
| `reason` | `SCHEMA_DRIFT` \| `OOM` \| `TIMEOUT` \| `TARGET_REJECTED` (redrive-only) \| `null` |
| `error_msg` | Full Glue job error text, if any |
| `source_row_count` | Rows expected to publish, from Gold's `delta_summary` (processor/recovery) — **reused for redrive** to hold how many items that redrive was asked to cover. Same concept in both cases: "expected volume for this run," just sourced differently (Gold's delta summary vs. a live `Select: "COUNT"` at trigger time). |
| `published_row_count` | Aggregate counts across all rows/tiers this run |
| `failed_row_count` | Aggregate counts across all rows/tiers this run |
| `active_tiers_processed` | Artifacts discovered in Apicurio for this run's families with `state != DISABLED` (`ENABLED` and `DEPRECATED`), exactly as registered — base artifact has no suffix. e.g. `["product.product.created", "product.product.created.v2"]`. `null` on redrive. Like `emit_all_active_tiers`, the `tiers` in this attribute name predates the *versions* terminology and is deliberately left as-is — it is a written attribute, not prose. |
| `versions_triggered` | Artifacts the job actually emitted for. Normally equals `active_tiers_processed`; differs only if an artifact was skipped (e.g. no eligible rows for its operation, or resolution failed after listing). `null` on redrive.|
| `versions_succeeded` |Per-artifact count of events accepted by the bus, e.g. `{"product.product.created": 250000, "product.product.created.v2": 248200}`. `sum(values) = published_row_count`. `null` on redrive. |
| `versions_failed` |Per-artifact count of events rejected by `PutEvents`, e.g. `{"product.product.created.v2": 1800}`. Only artifacts with failures appear; `null` if none (or on redrive). `sum(values) = failed_row_count`. |
| `started_at`| Run timing |
| `completed_at` | Run timing |
| `duration_seconds` | Run timing |
| `replay_count` | How many times this run_id has been recovered — `null` on redrive rows (redrive tracks `replay_count` per-item in Table 3 instead, not at the run level) |
| `last_updated_at` | Set on every write — status transitions (`IN_PROGRESS`→`STARTED`→terminal) all touch this, giving one consistent field to sort/filter on regardless of which transition happened |
| `ttl` | Epoch seconds, aligned to `PRODUCT_DELTA` retention (processor/recovery) or `started_at` + a shorter operational window (redrive) |
| **`started_by`** *( generalized across all three pipelines)* | `"SYSTEM"` (processor — fully automated, no human) \| a steward's identity, e.g. `"optional@pearson.com"` (recovery) \| the `customer_id` itself, e.g. `"customer-abc"` (redrive) — **this is where redrive's customer identity lives; there is no separate `customer_id` column.** The specific human who triggered a redrive is tracked per-item instead, on `uif-event-{env}-publish-metrics`'/`uif-event-{env}-delivery-metrics`' `replayed_by` field — not duplicated at the run level here. |


### Indexes

| Index | Key | Used by |
|---|---|---|
| Base table | `event_run_id` + `mode` | Status updates — the writer always holds both: the run id from the trigger response, the mode from the pipeline that started it |
| `gsi_gold_run` *(sparse)* | `gold_run_id` + `mode` | Domain Controller idempotency: `Query gold_run_id = X`, skip if any row is `IN_PROGRESS` / `STARTED` / `SUCCEEDED`. Also answers "what has run for this Gold run" across processor and recovery. |

`gsi_gold_run` is **sparse by construction**: `REDRIVE` rows carry no `gold_run_id`, so DynamoDB leaves them out of the index entirely. That is the desired behaviour — "has this Gold run been processed?" must never match a redrive — and it needs no filter expression. `mode` as the index sort key narrows the question further: `gold_run_id = X AND mode = INCREMENTAL` returns only the processor attempts.

> **Consistency caveat.** GSI reads are eventually consistent — DynamoDB offers no strongly-consistent read on a global secondary index. Two `GoldMergeCompleted` deliveries arriving within the propagation window can therefore both pass the idempotency check. `event_run_id` is not known until `StartJobRun` returns, so the row cannot be written as a pre-flight lock. Glue's own `ConcurrentRunsExceededException` is the backstop; a stricter guarantee needs a conditional write on a separate item keyed by `gold_run_id`, which is out of scope here.

### Sample item — SUCCEEDED (250k updates, tiers v1 + v2)

```json
{
  "event_run_id": "jr_8f3a2c1e9b7d4560a1f2e3d4c5b6a798",
  "domain": "product",
  "gold_run_id": "jr_5e7d9a2c4b1f3860d7e8f9a0b1c2d3e4",
  "mode": "INCREMENTAL",
  "operation": "OVERALL",
  "status": "SUCCEEDED",
  "worker_type": "G.2X",
  "num_workers": 4,
  "timeout_minutes": 240,
  "reason": null,
  "error_msg": null,
  "source_row_count": 250000,
  "published_row_count": 500000,
  "failed_row_count": 0,
  "active_tiers_processed": [
    "product.product.created", "product.product.created.v2",
    "product.product.updated", "product.product.updated.v2",
    "product.product.deleted", "product.product.deleted.v2"
  ],
  "versions_triggered": ["product.product.updated", "product.product.updated.v2"],
  "versions_succeeded": { "product.product.updated": 250000, "product.product.updated.v2": 250000 },
  "versions_failed": null,
  "started_at": "2026-09-06T02:15:00Z",
  "completed_at": "2026-09-06T03:02:41Z",
  "duration_seconds": 2861,
  "replay_count": 0,
  "last_updated_at": "2026-09-06T09:30:00Z",
  "ttl": 1789948800,
  "started_by": "SYSTEM"
}
```

### Sample item — PARTIAL_FAILURE 

```json
{
  "event_run_id": "jr_3c7e1a9f2d5b4806e1f2a3b4c5d6e7f8",
  "domain": "product",
  "gold_run_id": "jr_9a4b2c6d1e8f3057a9b0c1d2e3f4a5b6",
  "mode": "INCREMENTAL",
  "operation": "OVERALL",
  "status": "PARTIAL_FAILURE",
  "worker_type": "G.2X",
  "num_workers": 4,
  "timeout_minutes": 240,
  "reason": "SCHEMA_DRIFT",
  "error_msg": "1800 events rejected: field 'course_ref' expected STRING, got STRUCT (product.product.updated, head minor 2)",
  "source_row_count": 150000,
  "published_row_count": 148200,
  "failed_row_count": 1800,
  "active_tiers_processed": ["product.product.created", "product.product.updated", "product.product.deleted"],
  "versions_triggered": ["product.product.updated"],
  "versions_succeeded": { "product.product.updated": 148200 },
  "versions_failed": { "product.product.updated": 1800 },
  "started_at": "2026-09-06T06:00:00Z",
  "completed_at": "2026-09-06T06:14:30Z",
  "duration_seconds": 870,
  "replay_count": 1,
  "last_updated_at": "2026-09-06T09:30:00Z",
  "ttl": 1789948800,
  "started_by": "SYSTEM"
}
```

### Sample item — recovery run, `operation: "REPLAY"` (recovering the one schema-drift item from the run above)

```json
{
  "event_run_id": "jr_7d2f4b8a1c6e3950b7c8d9e0f1a2b3c4",
  "domain": "product",
  "gold_run_id": "jr_9a4b2c6d1e8f3057a9b0c1d2e3f4a5b6",
  "mode": "REPLAY",
  "operation": "OVERALL",
  "status": "SUCCEEDED",
  "worker_type": "G.1X",
  "num_workers": 2,
  "timeout_minutes": 120,
  "reason": null,
  "error_msg": null,
  "source_row_count": 1800,
  "published_row_count": 1800,
  "failed_row_count": 0,
  "active_tiers_processed": ["product.product.created", "product.product.updated", "product.product.deleted"],
  "versions_triggered": ["product.product.updated"],
  "versions_succeeded": { "product.product.updated": 1800 },
  "versions_failed": null,
  "started_at": "2026-09-06T09:30:00Z",
  "completed_at": "2026-09-06T09:36:12Z",
  "duration_seconds": 372,
  "replay_count": 1,
  "last_updated_at": "2026-09-06T09:36:12Z",
  "ttl": 1789948800,
  "started_by": "ops@pearson.com"
}
```

**Note:** a recovery row carries its own `event_run_id` (its Glue `JobRunId`) and points back at the run it is recovering via `gold_run_id` — distinguished from the processor's own row by `mode` (`INCREMENTAL` vs `REPLAY`).

### Sample item — FAILED (Glue timeout — no clean completion)

When Glue is **killed on TIMEOUT**, treat the run as **failed for ops purposes**. Unless a mid-run metrics checkpoint is implemented, assume `published_row_count = 0` in the final metrics row — even if some `PutEvents` calls may have reached the bus before the kill (see note below).

```json
{
  "event_run_id": "jr_b1e6c3d9a4f27085c2d3e4f5a6b7c8d9",
  "domain": "product",
  "gold_run_id": "jr_e5f8a1b4c7d20396d5e6f7a8b9c0d1e2",
  "mode": "INCREMENTAL",
  "operation": "OVERALL",
  "status": "FAILED",
  "worker_type": "G.4X",
  "num_workers": 6,
  "timeout_minutes": 600,
  "reason": "TIMEOUT",
  "error_msg": "Timeout Occurred: job run exceeded configured timeout of 600 minutes (Glue JobRunState=TIMEOUT)",
  "source_row_count": 620000,
  "published_row_count": null,
  "failed_row_count": null,
  "active_tiers_processed": [
    "product.product.created", "product.product.created.v2",
    "product.product.updated", "product.product.updated.v2",
    "product.product.deleted", "product.product.deleted.v2"
  ],
  "versions_triggered": [
    "product.product.created", "product.product.created.v2",
    "product.product.updated", "product.product.updated.v2"
  ],
  "versions_succeeded": null,
  "versions_failed": null,
  "started_at": "2026-09-06T02:15:00Z",
  "completed_at": "2026-09-06T12:15:04Z",
  "duration_seconds": 36004,
  "replay_count": 0,
  "last_updated_at": "2026-09-06T12:15:09Z",
  "ttl": 1789948800,
  "started_by": "SYSTEM"
}
```

> **Partial progress before timeout (optional policy):** If the processor flushes metrics periodically, `events_published_count` may legitimately be non-zero (e.g. `120000`) with `status = FAILED`. Those published events **may already be on `uif-event-dev-product-bus`** — subscribers could have received them. Per-record detail for what actually published belongs in Table 3 (`uif-event-dev-publish-metrics`), not this table — this table's `published_row_count` is an aggregate rollup only. Recovery for the unprocessed tail is handled by the Recovery Glue job (`uif-event-dev-fabric-recovery`), which re-derives the gap from Table 3 rather than from this aggregate count.

### Publish-time logging to `uif-event-{env}-publish-metrics`

The Glue eventing job records every event in `uif-event-{env}-publish-metrics`
**immediately after each `PutEvents` call** to `uif-event-{env}-{domain}-bus`, based on
the synchronous per-entry response:

| `PutEvents` entry result | Status written | Notes |
|---|---|---|
| `EventId` returned | `PUBLISHED` | Event was accepted by the bus. `EventId` is stored as `event_id`. |
| `ErrorCode` returned | `PUBLISH_FAILED` | Event never reached the bus. `reason = ErrorCode`, `error_msg = ErrorMessage`. No DLQ message will ever arrive for these. |

Flow per batch (max 10 entries per `PutEvents`):

1. `put_events(Entries=batch)`
2. For each entry, map the result to `PUBLISHED` or `PUBLISH_FAILED`
3. `batch_write_item` the resulting records (≤ 25 per call)

`PUBLISHED` is **optimistic** — it means "accepted by the bus", not "received by the
consumer". If a customer's target rejects the event after the configured retries,
the DLQ Lambda inserts a row in uif-event-{env}-delivery-metrics; this table is not modified. The top-level `status` remains `PUBLISHED`.

If the `PutEvents` call itself fails (exception, not per-entry), the job retries with
backoff; if it still fails, **all** entries in that batch are written as
`PUBLISH_FAILED` so recovery can pick them up.
---

## Table 3 — `uif-event-{env}-publish-metrics`

### Purpose

**Every bus publish attempt, per record/operation/version — both `PUBLISHED` and `PUBLISH_FAILED`.**

One row per bus publish attempt at the grain **domain × Gold run × record × operation × version**.
Written by the Glue eventing job immediately after each `PutEvents` batch to
`uif-event-{env}-{domain}-bus`, based on the synchronous per-entry response. Updated **in place**
by recovery runs. Never modified by the DLQ Lambda.

### Keys

| Key | Type | Example |
|-----|------|---------|
| `publish_id` (hash) | `{domain}#{gold_run_id}#{primary_key}#{operation}#{version}` — fully unique per publish attempt; spreads writes evenly across partitions | `product#jr_9a4b2c6d1e8f3057#A101703297180#updated#v2` |

**No sort key** — the full composite is the partition key, so the base table serves point reads only. Range access comes from the indexes below.

### Attributes

| Attribute | Meaning |
|-----------|---------|
| `domain` | Domain the record belongs to, e.g. `product`. Kept flat (also in `publish_id`) for filters and GSI projections. |
| `gold_run_id` | Gold Glue `JobRunId` whose `delta_summary` produced this record. Kept flat for the join to the run table and to compose `recovery_scope`. |
| `primary_key` | Business key of the Gold record (e.g. `product_id`), taken from the Gold delta row. Must be stable across runs. Composite Gold keys are concatenated in fixed order with `\|`. Key column(s) per domain come from config. |
| `operation` | `created` \| `updated` \| `deleted` — the CDC operation this row represents, mapped from the delta `OP` (`INSERT`/`UPDATE`/`DELETE`) by `operation_suffix_by_op`. The same value that composes the DetailType and the Apicurio artifact name. |
| `version` | Family major this row was published under (v1, v2, …) — the .vN sibling artifact, matching the DetailType suffix (product.created.v2). Never the inner Apicurio minor. The processor emits one row per sibling whose artifact state != DISABLED (ENABLED and DEPRECATED both emit), so a record eligible for N live majors produces N rows. Recovery groups by this before batching PutEvents. |
| `event_run_id` | Glue `JobRunId` of the run that **most recently** attempted this row — the processor run initially, overwritten by the replay run on recovery. Joins to the run table for `mode`, `started_by`, sizing, etc. |
| `status` | `PUBLISHED` — entry accepted by the bus (`EventId` returned). `PUBLISH_FAILED` — entry rejected by `PutEvents`; never reached the bus. **`PUBLISHED` means accepted by the bus, not received by a consumer** — downstream delivery failures are recorded in `uif-event-{env}-delivery-metrics`. Recovery flips `PUBLISH_FAILED` → `PUBLISHED` on the same row. |
| `recovery_scope` | **Sparse GSI key.** `{domain}#{gold_run_id}`. Present **only while** `status = PUBLISH_FAILED`; `REMOVE`d when the row is published. Rows without it don't exist in `gsi_recovery`, so the index holds only outstanding failures. |
| `event_id` | EventBridge `EventId` from the latest successful `PutEvents` response. `null` while `PUBLISH_FAILED`. Overwritten on recovery — only the latest publish can still produce a DLQ message. **GSI key** for the DLQ Lambda, which resolves the DLQ message's `id` → this row to recover lineage the event payload does not carry. |
| `payload` | The `Detail` object exactly as sent to the bus (`metadata` + `data`), gzip-compressed and stored as Binary. Written once by the processor on **every** row (published and failed) and never modified. Source of truth for `REPLAY`, `FULL_REPLAY`, and redrive re-sends — a re-sent event is byte-identical to the original regardless of subsequent Gold changes. Always under the 256 KB `PutEvents` entry limit, so always inline. **Excluded from all GSI projections.** |
| `payload_size_bytes` | Uncompressed size of `payload`, for monitoring growth per domain. |
| `published_at` | Timestamp of the successful publish. `null` while `PUBLISH_FAILED`. |
| `reason` | `PutEvents` `ErrorCode` for the current failure (`ThrottlingException`, `InternalFailure`, `ValidationException`, …), or `BATCH_CALL_FAILED` when the whole `PutEvents` call errored after in-job retries. `null` once `PUBLISHED`. |
| `error_msg` | `PutEvents` `ErrorMessage` for the current failure. `null` once `PUBLISHED`. |
| `attempt_count` | Number of **Glue runs** that have attempted this row — `1` from the processor, atomic `ADD 1` per recovery run. In-job `PutEvents` retries are **not** counted; the row is written once per run with the final outcome. |
| `last_updated_at` | Set on every write — publish, failure, recovery. |
| `ttl` | Epoch seconds, aligned to the run table's `PRODUCT_DELTA` retention for the same `gold_run_id`. |

#### Indexes

| Index | Key | Projection | Used by |
|-------|-----|------------|---------|
| Base table | `publish_id` = `{domain}#{gold_run_id}#{primary_key}#{operation}#{version}` | — | Point reads by full key — e.g. `GetItem publish_id = product#jr_9a4b2c6d1e8f3057#A101703297180#updated#v2`. No sort key, so no range access on the base table. |
| `gsi_recovery` *(sparse, partition key only)* | `recovery_scope` | `primary_key`, `version`, `operation`, `event_run_id`, `attempt_count` | Recovery: `Query recovery_scope = {domain}#{gold_run_id}` → every outstanding `PUBLISH_FAILED` row for that Gold run across all versions |
| `gsi_event_id` | `event_id` | `domain`, `gold_run_id`, `primary_key`, `version`, `operation`, `event_run_id` | DLQ Lambda: correlate a failed delivery back to its lineage and write `uif-event-{env}-delivery-metrics` |

#### Write rules

- **Processor** — one `BatchWriteItem` (≤ 25 items) per `PutEvents` batch. Accepted entries → `PUBLISHED` with `event_id` and `published_at`; rejected entries → `PUBLISH_FAILED` with `recovery_scope`, `reason`, `error_msg`. Both are written in the **same** batch, so a rejected entry is never silently dropped. Any `UnprocessedItems` returned are retried with exponential backoff until empty.
- **Recovery (success)** — `UpdateItem`, `ConditionExpression status = :failed`:
  `SET status=:published, event_run_id=:run, event_id=:id, published_at=:now, reason=:null, error_msg=:null, last_updated_at=:now REMOVE recovery_scope ADD attempt_count :one`
- **Recovery (failed again)** — same condition; `SET event_run_id, reason, error_msg, last_updated_at ADD attempt_count :one`; `recovery_scope` left in place.
- **DLQ Lambda** — read-only on this table (via `gsi_event_id`); writes go to `uif-event-{env}-delivery-metrics`.
- **Separator** — `#` everywhere in composed keys; `primary_key` values must not contain it.

#### Recovery flow — replaying a `PUBLISH_FAILED` row

Recovery re-publishes only rows that never reached the bus. It works on the **same row**
written by the processor; no new row is created.

```
Steward triggers recovery for Gold run  ──►  Recovery Glue run starts
                                                      │
        ┌─────────────────────────────────────────────┘
        ▼
  1. Query gsi_recovery
     recovery_scope = "{domain}#{gold_run_id}"
     → every PUBLISH_FAILED row for the run (all versions)
        │
        ▼
  2. For each row: decompress the stored `payload`
     (never re-read Gold), PutEvents (batched, max 10)
        │
        ├── EventId returned ────────►  3a. UpdateItem  (success)
        │                                   SET status=PUBLISHED, event_run_id, event_id,
        │                                       published_at, reason=null, error_msg=null
        │                                   REMOVE recovery_scope      ← row leaves gsi_recovery
        │                                   ADD attempt_count 1
        │
        └── ErrorCode returned ───────►  3b. UpdateItem  (failed again)
                                            SET event_run_id, reason, error_msg
                                            ADD attempt_count 1
                                            (status and recovery_scope unchanged → stays queued)
```

Both updates use `ConditionExpression status = PUBLISH_FAILED`, so two replays racing for
the same row cannot both apply.

#### Example — successful publish

```json
{
  "publish_id": "product#jr_9a4b2c6d1e8f3057#A101703297180#updated#v1",
  "domain": "product",
  "gold_run_id": "jr_9a4b2c6d1e8f3057",
  "primary_key": "A101703297180",
  "operation": "updated",
  "version": "v1",
  "event_run_id": "jr_3c7e1a9f2d5b4806",
  "status": "PUBLISHED",
  "event_id": "a1b2c3d4-5e6f-4a7b-8c9d-0e1f2a3b4c5d",
  "payload": "H4sIAAAAAAAA/6tWKkotLs0pKVayUsopLU4tzsxLzM1MzFOqBQDdC3T8FgAAAA==",
  "payload_size_bytes": 4218,
  "published_at": "2026-09-06T06:03:12Z",
  "reason": null,
  "error_msg": null,
  "attempt_count": 1,
  "last_updated_at": "2026-09-06T06:03:12Z",
  "ttl": 1789948800
}
```

#### Example — failed publish (row is in `gsi_recovery`)
```json
{
  "publish_id": "product#jr_9a4b2c6d1e8f3057#A101703297180#updated#v2",
  "domain": "product",
  "gold_run_id": "jr_9a4b2c6d1e8f3057",
  "primary_key": "A101703297180",
  "operation": "updated",
  "version": "v2",
  "event_run_id": "jr_3c7e1a9f2d5b4806",
  "status": "PUBLISH_FAILED",
  "recovery_scope": "product#jr_9a4b2c6d1e8f3057",
  "event_id": null,
  "payload": "H4sIAAAAAAAA/6tWKkotLs0pKVayUsopLU4tzsxLzM1MzFOqBQDdC3T8FgAAAA==",
  "payload_size_bytes": 4462,
  "published_at": null,
  "reason": "ThrottlingException",
  "error_msg": "Rate exceeded",
  "attempt_count": 1,
  "last_updated_at": "2026-09-06T06:03:12Z",
  "ttl": 1789948800
}
```
`payload` is written even though the publish failed — recovery re-sends **this** payload,
not a fresh read of Gold. Same record as the successful `v1` row above, shaped to schema `v2`
(a few extra fields), hence the slightly larger `payload_size_bytes`.
#### Sample item — PUBLISHED via replay (after steward fix + Recovery Glue republish)

**Before — written by processor run `jr_3c7e…`, throttled**

```json
{
  "publish_id": "product#jr_9a4b2c6d1e8f3057#A101703297180#updated#v2",
  "domain": "product",
  "gold_run_id": "jr_9a4b2c6d1e8f3057",
  "primary_key": "A101703297180",
  "operation": "updated",
  "version": "v2",
  "event_run_id": "jr_3c7e1a9f2d5b4806",
  "status": "PUBLISH_FAILED",
  "recovery_scope": "product#jr_9a4b2c6d1e8f3057",
  "event_id": null,
  "payload": "H4sIAAAAAAAA/6tWKkotLs0pKVayUsopLU4tzsxLzM1MzFOqBQDdC3T8FgAAAA==",
  "payload_size_bytes": 4462,
  "published_at": null,
  "reason": "ThrottlingException",
  "error_msg": "Rate exceeded",
  "attempt_count": 1,
  "last_updated_at": "2026-09-06T06:03:12Z",
  "ttl": 1789948800
}
```

**After — replayed successfully by recovery run `jr_7d2f…`**

```json
{
  "publish_id": "product#jr_9a4b2c6d1e8f3057#A101703297180#updated#v2",
  "domain": "product",
  "gold_run_id": "jr_9a4b2c6d1e8f3057",
  "primary_key": "A101703297180",
  "operation": "updated",
  "version": "v2",
  "event_run_id": "jr_7d2f4b8a1c6e3950",
  "status": "PUBLISHED",
  "event_id": "e5f6a7b8-9c0d-4e1f-a2b3-c4d5e6f7a8b9",
  "payload": "H4sIAAAAAAAA/6tWKkotLs0pKVayUsopLU4tzsxLzM1MzFOqBQDdC3T8FgAAAA==",
  "payload_size_bytes": 4462,
  "published_at": "2026-09-06T09:33:05Z",
  "reason": null,
  "error_msg": null,
  "attempt_count": 2,
  "last_updated_at": "2026-09-06T09:33:05Z",
  "ttl": 1789948800
}
```

| Attribute | Before | After |
|-----------|--------|-------|
| `status` | `PUBLISH_FAILED` | `PUBLISHED` |
| `event_run_id` | processor run | replay run |
| `event_id` | `null` | new `EventId` |
| `published_at` | `null` | replay time |
| `reason` / `error_msg` | failure details | `null` |
| `recovery_scope` | present | **removed** |
| `attempt_count` | 1 | 2 |
| `payload` / `payload_size_bytes` | — | **unchanged** — recovery re-sent this exact payload |

*(`domain`, `gold_run_id`, `primary_key`, `operation`, `version`, `ttl` omitted — unchanged.)*

#### NOTE

| Row | Change |
|-----|--------|
| Processor row `jr_3c7e…` | `replay_count` → 1, `last_updated_at` bumped at replay trigger |
| New `REPLAY` row `jr_7d2f…` | `gold_run_id` = same as processor, `source_row_count` = rows returned by the recovery query, `versions_triggered` = versions seen in those rows, `started_by` = steward identity, `replay_count` = 1 (attempt number) |

- A row that already reached the bus is **never** re-sent — it has no `recovery_scope`, so the query never returns it.
- Consumers see the replayed event as a first delivery; there is no duplicate.
- A row that fails again stays in `gsi_recovery` with an incremented `attempt_count`, ready for the next recovery.

> **Storing `payload` (gzip → Binary)**
>
> Compress the `Detail` JSON with `gzip` and wrap it in `boto3.dynamodb.types.Binary` so the
> resource API writes it as DynamoDB type `B` (not a string). Read it back with `bytes(...)` and
> `gzip.decompress`. The console shows `B` attributes as base64 only — use the CLI snippet below
> to inspect a row.

**Replay path:** Event Replayer Lambda triggers the Recovery Glue job (`uif-event-dev-fabric-recovery`) → job re-derives the failed set from this table → re-fetches fresh rows from `product_changes` → validates → `PutEvents` to `uif-event-dev-product-bus` → all matching subscriber rules fire. **Not** `uif-event-dev-delivery-metrics` — that table is for post-bus target delivery failures only.

---

## Table 4 — `uif-event-{env}-delivery-metrics`

### Purpose

**FAILED subscriber deliveries only.** Event **was on the bus**; EventBridge target failed after **3 retries**.

> **Policy:** No success rows. No processor/schema failures (those belong in `uif-event-{env}-publish-metrics`).
### Shape

One row per **failed delivery** at the grain **customer × record × operation × version × Gold run**.
Inserted by the DLQ Lambda when a customer's target rejects an event after EventBridge's configured
retries; updated by the redrive Lambda. A successful first-time delivery never creates a row.

Each row is **self-contained**: `payload` is copied in at DLQ time, so redrive works regardless of
`publish-metrics` retention. `publish_id` is kept for lineage only — redrive must read `payload`
from **this** row.

> **Two-phase TTL.** `ttl` is **omitted** while `status = FAILED` — an unresolved failure never
> silently expires. It is written once, on transition to `REPLAYED` or `ABANDONED` ( *Future Scope ), as
> `replayed_at` + 90 days. (or `abandoned_at`) - *Future Scope

### Keys

| Key | Type | Example |
|-----|------|---------|
| `domain_customer` (hash) | `{domain}#{customer_id}` — one `Query` returns everything outstanding for a customer in a domain | `product#customer-abc` |
| `delivery_id` (range) | `{failed_at}#{event_id}` — time-ordered, so oldest failures come back first | `2026-09-06T06:03:12Z#a1b2c3d4-5e6f-4a7b-8c9d-0e1f2a3b4c5d` |

### Attributes

| Attribute | UI | Meaning |
|-----------|:--:|---------|
| `customer_id` | — | Consumer whose target rejected the event; derived from the DLQ message's `TARGET_ARN`. |
| `domain` | ✓ | Domain of the record. |
| `primary_key` | ✓ | Gold business key. Composite keys joined with `\|`; UI renders the parts. |
| `operation` | ✓ | `created` \| `updated` \| `deleted` — mapped from the delta `OP` by `operation_suffix_by_op`; the same value that composes `detail_type`. |
| `detail_type` | ✓ | EventBridge detail-type of the failed event, e.g. `product.updated.v2`. Carries the version; used to rebuild the envelope on redrive. |
| `event_id` | ✓ | EventBridge `EventId` of the publish that failed (`body["id"]` on the DLQ message). Overwritten with the new envelope id on redrive. |
| `status` | ✓ | `FAILED` — outstanding. `REPLAYED` — redrive succeeded. `ABANDONED` — *Future Scope steward closed it without redrive. Terminal states keep the row until `ttl`. |
| `reason` | ✓ | DLQ `ERROR_CODE` (`SQS_QUEUE_ACCESS_DENIED`, `NO_PERMISSIONS`, `SDK_CLIENT_ERROR`, …). On redrive failure, the `SendMessage` error code. |
| `error_msg` | ✓* | DLQ `ERROR_MESSAGE`, or `SendMessage` error text on redrive failure. *Sanitised in the read path before display.* |
| `exhausted_by` | ✓ | DLQ `EXHAUSTED_RETRY_CONDITION` — `MaximumRetryAttempts` (target rejecting) \| `MaximumEventAgeInSeconds` (target unreachable too long). |
| `target_arn` | ✓ | Customer target that rejected the event **at the time of failure** — kept for audit and UI. Not used for routing: redrive resolves the current target from `subscription-configs`. |
| `failed_at` | ✓ | Event time (`body["time"]`) — when it was published. |
| `redrive_count` | ✓ | Redrive attempts — `0` on insert, atomic `ADD 1` per redrive. |
| `redriven_by` | ✓ | Identity that triggered the most recent redrive. `null` until first redrive. |
| `last_redriven_at` | ✓ | Timestamp of the most recent redrive attempt. `null` until first redrive. |
| `replayed_at` | ✓ | When `status` became `REPLAYED`. `null` otherwise. |
| `abandoned_at` / `abandoned_by` / `abandon_reason` | ✓ | Set only on `ABANDONED`. `null` otherwise. *Future Scope |
| `payload` | — | `Detail` as originally published, copied from `publish-metrics` at DLQ time (gzip → Binary). Source for redrive. Never modified. |
| `gold_run_id` | — | Gold Glue `JobRunId`. |
| `event_run_id` | — | Glue run that published the failed event — processor or recovery. |
| `publish_id` | — | `{domain}#{gold_run_id}#{primary_key}#{operation}#{version}` — source row in `publish-metrics`. Lineage and internal idempotency key; **not** a runtime dependency. |
| `rule_arn` | — | EventBridge rule that matched the event. |
| `redrive_run_id` | — | `rd_<uuid>` of the most recent redrive run on the run table (`mode = REDRIVE`, `worker_type = LAMBDA`). `null` until first redrive. |
| `dlq_received_at` | — | When the DLQ Lambda processed the message. |
| `last_updated_at` | — | Set on every write. |
| `ttl` | — | **Absent while `FAILED`.** Set once on `REPLAYED` / `ABANDONED` (Future Scope): terminal timestamp + 90 days. |

#### Indexes

| Index | Key | Used by |
|-------|-----|---------|
| Base | `domain_customer` + `delivery_id` | Redrive / UI: `Query domain_customer = {domain}#{customer_id}`, filter `status = FAILED`, oldest first |
| `gsi_gold_run` | `gold_run_id` + `customer_id` | Ops: which customers are still owed events from Gold run X |
| `gsi_event_id` | `event_id` | Idempotent DLQ processing — skip if a row already exists |

`payload` is excluded from all GSI projections.

#### Write rules

- **DLQ Lambda (insert)** — `body["id"]` → `publish-metrics.gsi_event_id` → lineage + `payload` → `PutItem`, `ConditionExpression attribute_not_exists(pk)`. **No `ttl`.**
- **Redrive Lambda (success)** — `UpdateItem`, `ConditionExpression status = :failed`:
  `SET status=:replayed, event_id=:new_id, redrive_run_id=:run, redriven_by=:who, last_redriven_at=:now, replayed_at=:now, ttl=:now_plus_90d, last_updated_at=:now ADD redrive_count :one`
- **Redrive Lambda (failed again)** — same condition, **no `ttl`**:
  `SET reason=:code, error_msg=:msg, redrive_run_id=:run, redriven_by=:who, last_redriven_at=:now, last_updated_at=:now ADD redrive_count :one`
- **Steward abandon - Future Scope** — `ConditionExpression status = :failed`:
  `SET status=:abandoned, abandoned_at=:now, abandoned_by=:who, abandon_reason=:why, ttl=:now_plus_90d, last_updated_at=:now`
- **Redrive dispatch** — resolve the current target: `GetItem(subscription-configs, pk = customer_id, sk = {domain}#{detail_type})`; require `status ∈ {ACTIVE, SUSPENDED}`, else fail the redrive with `reason = SUBSCRIPTION_NOT_ACTIVE`. Decompress **this row's** `payload` → wrap in an EventBridge-shaped envelope (`id` = new uuid, `detail-type` = `detail_type`, `time` = now) → `SendMessageBatch` (10) to the subscription's **current** `target.target_arn`. No `PutEvents`; no fan-out.
- **UI read path** — returns only the ✓ attributes; `error_msg` sanitised.

#### Example — `FAILED` (inserted by DLQ Lambda)

```json
{
  "domain_customer": "product#customer-abc",
  "delivery_id": "2026-09-06T06:03:12Z#a1b2c3d4-5e6f-4a7b-8c9d-0e1f2a3b4c5d",
  "customer_id": "customer-abc",
  "domain": "product",
  "primary_key": "A101703297180|03B",
  "operation": "updated",
  "detail_type": "product.updated.v2",
  "event_id": "a1b2c3d4-5e6f-4a7b-8c9d-0e1f2a3b4c5d",
  "status": "FAILED",
  "reason": "SQS_QUEUE_ACCESS_DENIED",
  "error_msg": "Access to the resource https://sqs.eu-west-1.amazonaws.com/283556840805/customer-abc-product is denied.",
  "exhausted_by": "MaximumRetryAttempts",
  "target_arn": "arn:aws:sqs:eu-west-1:283556840805:customer-abc-product",
  "failed_at": "2026-09-06T06:03:12Z",
  "redrive_count": 0,
  "redriven_by": null,
  "last_redriven_at": null,
  "replayed_at": null,
  "abandoned_at": null,
  "abandoned_by": null,
  "abandon_reason": null,
  "payload": "H4sIAAAAAAAA/6tWKkotLs0pKVayUsopLU4tzsxLzM1MzFOqBQDdC3T8FgAAAA==",
  "gold_run_id": "jr_9a4b2c6d1e8f3057",
  "event_run_id": "jr_3c7e1a9f2d5b4806",
  "publish_id": "product#jr_9a4b2c6d1e8f3057#A101703297180|03B#updated#v2",
  "rule_arn": "arn:aws:events:eu-west-1:283556840805:rule/uif-event-dev-product-bus/uif-event-dev-on-product-deliver-customer-abc",
  "redrive_run_id": null,
  "dlq_received_at": "2026-09-06T06:05:41Z",
  "last_updated_at": "2026-09-06T06:05:41Z"
}
```
*(No `ttl` attribute — the row does not expire while `FAILED`.)*

#### Example — same row after successful redrive

```json
{
  "domain_customer": "product#customer-abc",
  "delivery_id": "2026-09-06T06:03:12Z#a1b2c3d4-5e6f-4a7b-8c9d-0e1f2a3b4c5d",
  "customer_id": "customer-abc",
  "domain": "product",
  "primary_key": "A101703297180|03B",
  "operation": "updated",
  "detail_type": "product.updated.v2",
  "event_id": "9f8e7d6c-5b4a-4392-8170-6e5d4c3b2a19",
  "status": "REPLAYED",
  "reason": "SQS_QUEUE_ACCESS_DENIED",
  "error_msg": "Access to the resource https://sqs.eu-west-1.amazonaws.com/283556840805/customer-abc-product is denied.",
  "exhausted_by": "MaximumRetryAttempts",
  "target_arn": "arn:aws:sqs:eu-west-1:283556840805:customer-abc-product",
  "failed_at": "2026-09-06T06:03:12Z",
  "redrive_count": 1,
  "redriven_by": "ops@customer-abc.com",
  "last_redriven_at": "2026-09-06T14:00:00Z",
  "replayed_at": "2026-09-06T14:00:00Z",
  "abandoned_at": null,
  "abandoned_by": null,
  "abandon_reason": null,
  "payload": "H4sIAAAAAAAA/6tWKkotLs0pKVayUsopLU4tzsxLzM1MzFOqBQDdC3T8FgAAAA==",
  "gold_run_id": "jr_9a4b2c6d1e8f3057",
  "event_run_id": "jr_3c7e1a9f2d5b4806",
  "publish_id": "product#jr_9a4b2c6d1e8f3057#A101703297180|03B#updated#v2",
  "rule_arn": "arn:aws:events:eu-west-1:283556840805:rule/uif-event-dev-product-bus/uif-event-dev-on-product-deliver-customer-abc",
  "redrive_run_id": "rd_0d9e7c2a-4b1f-4e6a-9c3d-8f2a1b7e5c04",
  "dlq_received_at": "2026-09-06T06:05:41Z",
  "last_updated_at": "2026-09-06T14:00:00Z",
  "ttl": 1796169600
}
```

| Attribute | Before | After |
|-----------|--------|-------|
| `status` | `FAILED` | `REPLAYED` |
| `event_id` | original `EventId` | new envelope id minted by redrive |
| `redrive_count` | 0 | 1 |
| `redriven_by` / `last_redriven_at` / `replayed_at` | `null` | set |
| `redrive_run_id` | `null` | `rd_…` |
| `ttl` | **absent** | `replayed_at` + 90 days — first and only time it is written |
| `reason` / `error_msg` / `exhausted_by` | — | **unchanged** — original failure kept for audit |
| `payload` | — | **unchanged** — this is what was re-sent |
---

## Cross-table example — Gold run `jr_8b2f6e4a…` (10 UPDATE rows)

Gold run `jr_8b2f6e4a91c3d7e05f4a6b8c2d1e9f7a3b5c4d6e8f0a1b2c3d4e5f6a7b8c9d0e` produces a delta of
**10 `UPDATE` rows** for domain `product` (`A101703297180` … `A101703297189`). One ACTIVE schema
version (`v2`). Two consumers subscribed: `customer-abc`, `customer-xyz`.

| Id | Value |
|---|---|
| `gold_run_id` | `jr_8b2f6e4a91c3d7e05f4a6b8c2d1e9f7a3b5c4d6e8f0a1b2c3d4e5f6a7b8c9d0e` |
| Processor `event_run_id` | `jr_d4cdbda26c230f416ea76d524507130fb28701a1862b4a449447198c6a565f5a` |
| Recovery `event_run_id` | `jr_3e9a1c7f52b8d4067a1e2f3c4b5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d` |
| Redrive `event_run_id` | `rd_0d9e7c2a-4b1f-4e6a-9c3d-8f2a1b7e5c04` |

```
Gold delta (10 rows)
      │
      ▼  processor run  jr_d4cdbda2…
   PutEvents ×10 ──► 9 accepted, 1 throttled (A101703297186)
      │
      ├─► publish-metrics: 9 PUBLISHED, 1 PUBLISH_FAILED
      │
      ▼  bus → rules
   customer-xyz: 9/9 delivered
   customer-abc: 8/9 delivered, A101703297182 rejected (queue access denied) ──► DLQ
      │
      ├─► delivery-metrics: 1 FAILED row (customer-abc / A101703297182)
      │
      ▼  ops: recovery run  jr_3e9a1c7f…         (re-publishes A101703297186 from payload)
   PutEvents ×1 ──► accepted → both customers receive A101703297186
      │
      ▼  customer-abc fixes queue policy, triggers redrive  rd_0d9e7c2a…   (A101703297182, direct to their queue)
   SendMessage ×1 ──► accepted
```

---

### 1. Run table — 3 rows

| `event_run_id` | `mode` | `gold_run_id` | `status` | `source_row_count` | `published_row_count` | `failed_row_count` | `versions_failed` | `replay_count` | `started_by` | `worker_type` |
|---|---|---|---|---|---|---|---|---|---|---|
| `jr_d4cdbda2…6a565f5a` | `INCREMENTAL` | `jr_8b2f6e4a…7b8c9d0e` | `PARTIAL_FAILURE` | 10 | 9 | 1 | `{"product.product.updated.v2": 1}` | **1** | `SYSTEM` | `G.1X` |
| `jr_3e9a1c7f…0a1b2c3d` | `REPLAY` | `jr_8b2f6e4a…7b8c9d0e` | `SUCCEEDED` | 1 | 1 | 0 | `null` | 1 | `ops@pearson.com` | `G.1X` |
| `rd_0d9e7c2a-4b1f-4e6a-9c3d-8f2a1b7e5c04` | `REDRIVE` | `null` | `SUCCEEDED` | 1 | 1 | 0 | `null` | `null` | `customer-abc` | `LAMBDA` |

- Processor row's `replay_count` was `0` at completion, bumped to `1` when the recovery was triggered.
- Redrive row has no `gold_run_id` — it's customer-facing, not Gold lineage.
- `failed_row_count = 1` on the processor row counts the **publish** failure only. The delivery failure to `customer-abc` is not a run-level failure — the bus accepted `A101703297182`.

---

### 2. `publish-metrics` — 10 rows, state after recovery

`publish_id` = `product#jr_8b2f6e4a…7b8c9d0e#{primary_key}#updated#v2` (gold run id abbreviated below).

| `primary_key` | `status` | `event_run_id` | `event_id` | `attempt_count` | `recovery_scope` |
|---|---|---|---|---|---|
| `A101703297180` | `PUBLISHED` | `jr_d4cdbda2…` | `7c4d765e-a614-41d0-bf02-3c82d9213ef2` | 1 | — |
| `A101703297181` | `PUBLISHED` | `jr_d4cdbda2…` | `1a2f9c3e-8b47-4d05-9e61-2f7c8d4a5b30` | 1 | — |
| `A101703297182` | `PUBLISHED` | `jr_d4cdbda2…` | `5e8b2d7a-c391-4f16-a204-9d3e6f1b7c48` | 1 | — |
| `A101703297183` | `PUBLISHED` | `jr_d4cdbda2…` | `9f13c6e2-4a7d-4b58-8c0e-1e5a2d7f9b63` | 1 | — |
| `A101703297184` | `PUBLISHED` | `jr_d4cdbda2…` | `2b7e4d9c-6f18-4a3e-b571-8c0d3e9f2a14` | 1 | — |
| `A101703297185` | `PUBLISHED` | `jr_d4cdbda2…` | `c4a8e1f6-3d92-4c07-9b25-6e1f8a4d7c39` | 1 | — |
| `A101703297186` | `PUBLISHED` | **`jr_3e9a1c7f…`** | **`e3d6b9a2-7c15-4e48-a0f3-2d8b5c6e9f71`** | **2** | *(removed)* |
| `A101703297187` | `PUBLISHED` | `jr_d4cdbda2…` | `6d1c8f3b-a2e7-4d90-b846-3f9e2c5a7d18` | 1 | — |
| `A101703297188` | `PUBLISHED` | `jr_d4cdbda2…` | `b8f2a5d1-9e64-4c23-a7b0-5d1e8f3c6a92` | 1 | — |
| `A101703297189` | `PUBLISHED` | `jr_d4cdbda2…` | `4e7c1b9f-d385-4a61-9c2d-7b0e5f8a3d46` | 1 | — |

**Between processor and recovery**, `A101703297186` looked like this — the only row in `gsi_recovery`:

```json
{
  "publish_id": "product#jr_8b2f6e4a91c3d7e05f4a6b8c2d1e9f7a3b5c4d6e8f0a1b2c3d4e5f6a7b8c9d0e#A101703297186#updated#v2",
  "status": "PUBLISH_FAILED",
  "recovery_scope": "product#jr_8b2f6e4a91c3d7e05f4a6b8c2d1e9f7a3b5c4d6e8f0a1b2c3d4e5f6a7b8c9d0e",
  "event_run_id": "jr_d4cdbda26c230f416ea76d524507130fb28701a1862b4a449447198c6a565f5a",
  "event_id": null,
  "reason": "ThrottlingException",
  "error_msg": "Rate exceeded",
  "attempt_count": 1,
  "payload": "<gzip>"
}
```

- **`A101703297182` is `PUBLISHED` and stays `PUBLISHED`.** Its delivery failure to `customer-abc` never touches this table.
- **`A101703297186`** was recovered from its stored `payload`, not from Gold.
- `gsi_recovery` is now empty for this Gold run.

---

### 3. `delivery-metrics` — 1 row, state after redrive

| `domain_customer` | `delivery_id` | `status` | `reason` | `event_id` | `redrive_count` | `redriven_by` | `redrive_run_id` | `ttl` |
|---|---|---|---|---|---|---|---|---|
| `product#customer-abc` | `2026-09-06T06:03:12Z#5e8b2d7a-c391-4f16-a204-9d3e6f1b7c48` | `REPLAYED` | `SQS_QUEUE_ACCESS_DENIED` | **`f1a4c7e0-2b9d-4e63-8a15-c6d3b9e2f507`** | 1 | `ops@customer-abc.com` | `rd_0d9e7c2a-4b1f-4e6a-9c3d-8f2a1b7e5c04` | `replayed_at` + 90d |

**Between DLQ and redrive** the row was `FAILED`, `redrive_count = 0`,
`event_id = 5e8b2d7a-c391-4f16-a204-9d3e6f1b7c48`, **no `ttl`**, `payload` copied from
`publish-metrics`, `publish_id = product#jr_8b2f6e4a…7b8c9d0e#A101703297182#updated#v2`.

- `customer-xyz` has **no rows** here — all 9 events were delivered first time.
- `A101703297186`'s late delivery (via recovery) created no row either — it reached both customers on first attempt.
- The redrive sent `A101703297182`'s stored `payload` directly to `customer-abc`'s queue; `customer-xyz` was not touched.

---

### What each consumer received

| Record | `customer-xyz` | `customer-abc` |
|---|---|---|
| `A101703297180`–`181`, `183`–`185`, `187`–`189` | 1× via processor | 1× via processor |
| `A101703297182` | 1× via processor | 1× via **redrive** (direct) |
| `A101703297186` | 1× via **recovery** | 1× via **recovery** |

Every record delivered exactly once to each consumer. No duplicates.

## Table 5 — `uif-event-{env}-subscription-configs`

### Purpose

One row per **subscription**: customer × detail-type (operation + version) × target. This is
the single source of truth for what a customer is subscribed to, at what version, and where
events are delivered. A customer subscribed to three detail-types has three rows, each with its
own EventBridge rule and its own target.

### Keys

| Key | Type | Example |
|-----|------|---------|
| `customer_id` (hash) | `{customer_id}` — the consumer organisation's code | `pmc` |
| `domain_detail_type` (range) | `{domain}#{detail_type}` — one subscription per detail-type per customer; `begins_with` filters to one domain or one operation | `product#product.created.v1` |

The partition key holds the customer code directly, so no separate flat copy is stored.

### Attributes

| Attribute | Type | Notes |
|---|---|---|
| `subscription_id` | String (UUID) | Stable id for the subscription; used on unsubscribe and in audit |
| `policy_sid` | String | `uif` + UUID hex without hyphens — the `Sid` **prefix** for the statements we add to the target's resource policy: `{policy_sid}` (EventBridge delivery, conditioned on `rule_arn`), `{policy_sid}r` (direct `SendMessage` for the redrive Lambda) and `{policy_sid}c` (consume actions for `consumer_principal_arn`). Hyphens are not accepted in all `Sid` contexts. |
| `domain` | String | e.g. `product` |
| `operation` | String (enum) | `created` \| `updated` \| `deleted` |
| `version` | String | Family major this subscription is bound to (`v1`, `v2`, …) — the `.vN` sibling artifact, matching the DetailType suffix. Never the inner Apicurio minor. |
| `detail_type` | String | `{domain}.{operation}.{version}` — the rule's `detail-type` filter |
| `rule_name` | String | `uif-event-{env}-on-{domain}-{detail_type, dots→dashes}-{customer_id}` — must be ≤ 64 chars |
| `rule_arn` | String | `arn:aws:events:{region}:{account}:rule/uif-event-{env}-{domain}-bus/{rule_name}`. **GSI key** — the DLQ Lambda resolves `RULE_ARN` → this row. |
| `target` | Map | `target_type`, `target_arn`, `target_config` — see below |
| `target_arn` | String | Flat duplicate of `target.target_arn` — required because a GSI key must be a top-level scalar attribute, so the nested `target.target_arn` cannot be indexed directly. **GSI key** — fallback when a DLQ message lacks `RULE_ARN`. Always written in the same `UpdateItem` as `target.target_arn`: if the two diverge, `gsi_target_arn` resolves a DLQ message to a stale target and redrive delivers to the wrong queue, with no error, since both values are well-formed ARNs. |
| `status` | String (enum) | `PENDING` \| `ACTIVE` \| `SUSPENDED` \| `FAILED` \| `DELETED` |
| `status_reason` | String | Why `FAILED` / `SUSPENDED` (e.g. `RULE_QUOTA`, `NAME_TOO_LONG`, `POLICY_WRITE_FAILED`, `CUSTOMER_REQUEST`). `null` otherwise. |
| `record_version` | Number | Optimistic locking — every edit uses `ConditionExpression record_version = :expected` |
| `created_at` / `created_by` | String | Set on subscribe |
| `updated_at` / `updated_by` | String | Bumped on every edit (target change, suspend/resume) |
| `deleted_at` / `deleted_by` | String | Set on unsubscribe |

### `target` map by `target_type`

| `target_type` | `target_arn` | `target_config` |
|---|---|---|
| `SQS` | Queue ARN | `consumer_principal_arn` (**required** — the customer's IAM role or account root that will consume the queue), `dlq_arn`, `retry_policy { max_attempts, max_age_seconds }`, `message_group_id` (FIFO only, else `null`) |
| `KINESIS` | Stream ARN | `dlq_arn`, `retry_policy`, `role_arn` (EventBridge assumes it — Kinesis has no resource policy), `partition_key_path` (e.g. `$.detail.data.product_id`) |
| `API` | API-destination ARN | `dlq_arn`, `retry_policy`, `role_arn`, `connection_arn` (holds auth), `http_parameters { path, query, header }` |

### Naming (env `dev`, customer `pmc`, detail-type `product.created.v1`)

| Resource | Name |
|---|---|
| Rule | `uif-event-dev-on-product-product-created-v1-pmc` |
| Subscription queue | `uif-event-dev-pmc-product-product-created-v1` |
| Subscription DLQ | `uif-event-dev-pmc-product-product-created-v1-dlq` |

### Example

```json
{
  "customer_id": "pmc",
  "domain_detail_type": "product#product.created.v1",
  "subscription_id": "8f3c1e2a-4b7d-4c19-9a55-2e6f0d1b7c34",
  "policy_sid": "uif8f3c1e2a4b7d4c199a552e6f0d1b7c34",
  "customer_id": "pmc",
  "domain": "product",
  "operation": "created",
  "version": "v1",
  "detail_type": "product.created.v1",
  "rule_name": "uif-event-dev-on-product-product-created-v1-pmc",
  "rule_arn": "arn:aws:events:eu-west-1:444455556666:rule/uif-event-dev-product-bus/uif-event-dev-on-product-product-created-v1-pmc",
  "target": {
    "target_type": "SQS",
    "target_arn": "arn:aws:sqs:eu-west-1:444455556666:uif-event-dev-pmc-product-product-created-v1",
    "target_config": {
      "consumer_principal_arn": "arn:aws:iam::111122223333:role/pmc-event-consumer",
      "dlq_arn": "arn:aws:sqs:eu-west-1:444455556666:uif-event-dev-pmc-product-product-created-v1-dlq",
      "retry_policy": { "max_attempts": 3, "max_age_seconds": 900 },
      "message_group_id": null
    }
  },
  "target_arn": "arn:aws:sqs:eu-west-1:444455556666:uif-event-dev-pmc-product-product-created-v1",
  "status": "ACTIVE",
  "status_reason": null,
  "record_version": 2,
  "created_at": "2026-09-03T09:14:02Z",
  "created_by": "ops@pmc.com",
  "updated_at": "2026-09-06T11:20:45Z",
  "updated_by": "ops@pmc.com",
  "deleted_at": null,
  "deleted_by": null
}
```

### Indexes

| Index | Key | Used by |
|---|---|---|
| Base | `customer_id` + `domain_detail_type` | Customer UI / API: all subscriptions for a customer; `begins_with(domain_detail_type, "product#")` for one domain |
| `gsi_rule_arn` | `rule_arn` | DLQ Lambda: `RULE_ARN` → `customer_id`, `target`, `detail_type` |
| `gsi_target_arn` | `target_arn` | DLQ Lambda fallback when `RULE_ARN` is absent |
| `gsi_detail_type` | `domain_detail_type` + `customer_id` | Ops: "who is subscribed to `product.created.v1`" — deprecation notices, redrive fan-out checks |

### Key design notes

- **`status = PENDING` is written before any AWS mutation** (`PutRule` / `PutTargets` / queue + DLQ creation / policy write). If the Lambda crashes mid-subscribe, the row is a visible, resumable or rollback-able intent — never an invisible live rule with no record. Pre-flight checks (rule quota, name length) fail the row to `FAILED` with a `status_reason` **before** touching AWS.
- **One rule per subscription, suffixed with `{customer_id}`.** Several customers subscribe to the same detail-type, and rule names must be unique per bus; without the suffix the second subscriber's `PutRule` would overwrite the first's rule. This is the N × M model — request the per-bus rule quota increase (default 300) before onboarding.
- **Version migration = new row + old row `DELETED`.** Moving `pmc` from `product.updated.v1` to `v2` creates a `PENDING → ACTIVE` row for v2, then marks the v1 row `DELETED`. Do them in that order so the customer never has a gap; brief overlap is safe because payloads are versioned.
- **Redrive reads the current target from this table**, not from `delivery-metrics.target_arn`. The latter is "what rejected the event"; this table is "where to send it now". If a customer changed queues after a failure, redrive goes to the new one.
- **Redrive needs its own grant on every target.** The redrive Lambda sends directly (`SendMessage`), bypassing EventBridge, so the EventBridge-only statement is not enough. Managed queues get the redrive-role statement at subscribe time (step 4). Bring-your-own targets must add it themselves; if they don't, `target_config.redrive_enabled = false` and the UI does not offer redrive for that subscription.
- **`SUSPENDED` = `DisableRule`**, rule and target intact; `ACTIVE` = `EnableRule`. Lets a customer pause without losing the subscription.
- **`DELETED` rows stay as tombstones.** Re-subscribing to the same detail-type overwrites the row with a new `subscription_id`; history lives in the table stream / audit log.
- **Subscription queues are managed** — created in the eventing account at subscribe time, `target_arn` derived from the naming convention. The customer consumes **cross-account**: they supply `consumer_principal_arn` at subscribe time, step 4 grants it the consume actions (`ReceiveMessage`, `DeleteMessage`, `ChangeMessageVisibility`, `GetQueueAttributes`, `GetQueueUrl` — never `SendMessage`), and their own IAM policy must allow those actions on the queue ARN (onboarding contract). Queues use SQS-managed SSE so no KMS key-policy grant is needed. Pre-flight validates the principal's account against the customer's onboarding record (`status_reason = CONSUMER_PRINCIPAL_INVALID` on mismatch). **Bring-your-own target is future scope** — when added, `target_arn` is the customer's, the naming convention does not apply, the consume statement is not written, and the customer must add the redrive-role grant themselves (`target_config.redrive_enabled`)..

### NOTE

> **Why both `subscription_id` and `policy_sid`?**
> They are one identity in two encodings. `subscription_id` (UUID) is how *our* systems refer to the
> subscription — the DynamoDB row, the `PutTargets` target `Id`, API responses, and unsubscribe.
> `policy_sid` is the same UUID with hyphens stripped and a `uif` prefix, and it appears in exactly one place: as the `Sid` of the statement we add to the target queue's resource policy. Policy `Sid`s must be alphanumeric, so the raw UUID can't be used directly.
> On unsubscribe or target
> change we remove **only** the statements whose `Sid` starts with `policy_sid` — the EventBridge grant
> the redrive-Lambda grant and consume grant — leaving the customer's own statements, and any other subscription's
> grants on the same queue, untouched.

## Subscription lifecycle — worked examples (customer `pmc`, domain `product`, env `dev`)

Detail-type is `{domain}.{operation}.{version}` → `product.created.v1`; the row's `domain_detail_type` prefixes it
with the domain → `product#product.created.v1`.

---

### Case 1 — Subscribe to `product.created.v1`, SQS target

**Names derived at subscribe time**

| Resource | Name |
|---|---|
| Rule | `uif-event-dev-on-product-product-created-v1-pmc` |
| Queue | `uif-event-dev-pmc-product-product-created-v1` |
| DLQ | `uif-event-dev-pmc-product-product-created-v1-dlq` |

**Step 1 — intent written, nothing in AWS yet**

```json
{
  "customer_id": "pmc",
  "domain_detail_type": "product#product.created.v1",
  "subscription_id": "8f3c1e2a-4b7d-4c19-9a55-2e6f0d1b7c34",
  "policy_sid": "uif8f3c1e2a4b7d4c199a552e6f0d1b7c34",
  "customer_id": "pmc",
  "domain": "product",
  "operation": "created",
  "version": "v1",
  "detail_type": "product.created.v1",
  "rule_name": "uif-event-dev-on-product-product-created-v1-pmc",
  "rule_arn": null,
  "target": {
    "target_type": "SQS",
    "target_arn": "arn:aws:sqs:eu-west-1:444455556666:uif-event-dev-pmc-product-product-created-v1",
    "target_config": {
      "consumer_principal_arn": "arn:aws:iam::111122223333:role/pmc-event-consumer",
      "dlq_arn": "arn:aws:sqs:eu-west-1:444455556666:uif-event-dev-pmc-product-product-created-v1-dlq",
      "retry_policy": { "max_attempts": 3, "max_age_seconds": 900 },
      "message_group_id": null
    }
  },
  "target_arn": "arn:aws:sqs:eu-west-1:444455556666:uif-event-dev-pmc-product-product-created-v1",
  "status": "PENDING",
  "status_reason": null,
  "record_version": 1,
  "created_at": "2026-09-03T09:14:02Z",
  "created_by": "ops@pmc.com",
  "updated_at": "2026-09-03T09:14:02Z",
  "updated_by": "ops@pmc.com",
  "deleted_at": null,
  "deleted_by": null
}
```

**Steps 2–5 — AWS mutations** (each idempotent; resume from `PENDING` on crash)

```
2. CreateQueue  uif-event-dev-pmc-product-product-created-v1-dlq
3. CreateQueue  uif-event-dev-pmc-product-product-created-v1   (RedrivePolicy → DLQ)
4. SetQueueAttributes  Policy += [
     { Sid: policy_sid,        Principal: events.amazonaws.com,
       Action: sqs:SendMessage, Condition: aws:SourceArn = rule_arn },            ← EventBridge delivery
     { Sid: policy_sid + "r",  Principal: arn:aws:iam::444455556666:role/uif-event-dev-redrive-lambda,
       Action: sqs:SendMessage },                                                   ← direct redrive
     { Sid: policy_sid + "c",  Principal: target_config.consumer_principal_arn,
       Action: [sqs:ReceiveMessage, sqs:DeleteMessage, sqs:ChangeMessageVisibility,
                sqs:GetQueueAttributes, sqs:GetQueueUrl] }                          ← customer consume (never SendMessage)
   ]
5. PutRule     name = rule_name, bus = uif-event-dev-product-bus,
               pattern = { "detail-type": ["product.created.v1"] }
   PutTargets  rule, Id = subscription_id, Arn = queue, DeadLetterConfig = dlq_arn,
               RetryPolicy = { MaximumRetryAttempts: 3, MaximumEventAgeInSeconds: 900 }
```

**Step 6 — activated**

```json
{
  "rule_arn": "arn:aws:events:eu-west-1:444455556666:rule/uif-event-dev-product-bus/uif-event-dev-on-product-product-created-v1-pmc",
  "status": "ACTIVE",
  "record_version": 2,
  "updated_at": "2026-09-03T09:14:09Z"
}
```

*(other attributes unchanged)*

From here, every `product.created.v1` event on `uif-event-dev-product-bus` lands on `pmc`'s v1 queue.

---

### Case 2 — Transition `v1 → v2`, both active for a period

**Step 1 — subscribe to v2 (Case 1 again, new row)**

| `domain_detail_type` | `rule_name` | queue | `status` |
|---|---|---|---|
| `product#product.created.v1` | `…-product-created-v1-pmc` | `uif-event-dev-pmc-product-product-created-v1` | `ACTIVE` |
| `product#product.created.v2` | `…-product-created-v2-pmc` | `uif-event-dev-pmc-product-product-created-v2` | `PENDING → ACTIVE` |

`Query customer_id = pmc AND begins_with(domain_detail_type, "product#product.created.")` returns both.

**During overlap** — one Gold `CREATE` produces two bus events (the processor emits every
Apicurio-ACTIVE version). Both rules match:

| Bus event | Rule matched | Delivered to |
|---|---|---|
| `product.created.v1` | `…-v1-pmc` | `uif-event-dev-pmc-product-product-created-v1` |
| `product.created.v2` | `…-v2-pmc` | `uif-event-dev-pmc-product-product-created-v2` |

`pmc` runs old and new consumers side by side. `publish-metrics` has two rows per record (one per
version); `delivery-metrics` failures are attributed per version.

**Step 2 — cut-over day (`2026-10-01`): suspend v1**

```json
{
  "domain_detail_type": "product#product.created.v1",
  "status": "SUSPENDED",
  "status_reason": "VERSION_MIGRATION",
  "record_version": 3,
  "updated_at": "2026-10-01T00:00:00Z",
  "updated_by": "scheduler"
}
```

AWS: `DisableRule(…-v1-pmc)`. No new v1 events arrive; queue, DLQ and rule are intact —
`EnableRule` is the rollback. Redrive of outstanding v1 failures still works (direct `SendMessage`
to the queue; a disabled rule doesn't block it).

**Step 3 — v1 retired for this customer (`2026-10-08`)**

Pre-condition: no `FAILED` rows in `delivery-metrics` for `product#pmc` with
`detail_type = product.created.v1`. Any remaining → redrive now, or `ABANDONED` with
`abandon_reason = VERSION_RETIRED`.

```
DELETING → RemoveTargets → DeleteRule → remove policy statements (Sid prefix = policy_sid) → schedule queue + DLQ deletion (+7d)
```

```json
{
  "domain_detail_type": "product#product.created.v1",
  "status": "DELETED",
  "status_reason": "VERSION_MIGRATION",
  "record_version": 5,
  "deleted_at": "2026-10-08T00:00:00Z",
  "deleted_by": "scheduler"
}
```

**End state**

| `domain_detail_type` | `status` |
|---|---|
| `product#product.created.v1` | `DELETED` (tombstone) |
| `product#product.created.v2` | `ACTIVE` |

**Deprecating v1 on the publish side** is a separate, later decision: when
`gsi_detail_type` shows **no** `ACTIVE`/`SUSPENDED` rows for `product#product.created.v1` across
all customers, v1 can be set INACTIVE in Apicurio and the processor stops emitting it. Until then it
keeps publishing v1 — other customers may still depend on it.

---

### Case 3 — Unsubscribe from `product.created.v2`

**Step 0 — pre-check**

```
Query delivery-metrics  domain_customer = product#pmc, filter detail_type = product.created.v2 AND status = FAILED
→ 2 rows outstanding
```

Caller passes `abandon = true` → both rows:

```json
{ "status": "ABANDONED", "abandoned_at": "2026-11-15T10:00:00Z",
  "abandoned_by": "ops@pmc.com", "abandon_reason": "UNSUBSCRIBED", "ttl": 1771156800 }
```

**Step 1 — intent**

```json
{
  "domain_detail_type": "product#product.created.v2",
  "status": "DELETING",
  "status_reason": null,
  "deleted_by": "ops@pmc.com",
  "record_version": 4,
  "updated_at": "2026-11-15T10:00:00Z"
}
```

UI treats `DELETING` as gone.

**Steps 2–5 — teardown** (idempotent; resume from `DELETING` on crash)

```
2. RemoveTargets  rule …-v2-pmc, Ids = [8f3c1e2a-…]        ← events stop here
3. DeleteRule     …-v2-pmc
4. SetQueueAttributes  remove Policy statements where Sid starts with policy_sid   (delivery + redrive + consume)
5. Tag queue + DLQ  delete_after = 2026-11-22T10:00:00Z      (grace period; customer drains)
```

**Step 6 — done**

```json
{
  "domain_detail_type": "product#product.created.v2",
  "status": "DELETED",
  "deleted_at": "2026-11-15T10:00:04Z",
  "deleted_by": "ops@pmc.com",
  "record_version": 5
}
```

**Effects**

| Table | Effect |
|---|---|
| `subscription-configs` | Tombstone. Re-subscribing later overwrites with a new `subscription_id`. |
| `delivery-metrics` | The 2 failures are `ABANDONED`, expire after 90 days. |
| `publish-metrics` / run table | Untouched — the processor still publishes `product.created.v2` for other subscribers. |
| Queue | Readable for 7 days, then swept. |

## References

- [`../eventing/event-centric-publish-reference.md`](../eventing/event-centric-publish-reference.md)
- [`../eventing/event-subscription-lifecycle.md`](../eventing/event-subscription-lifecycle.md)
- [ADR-0016](0016-domain-controller-vs-direct-glue-trigger.md)
