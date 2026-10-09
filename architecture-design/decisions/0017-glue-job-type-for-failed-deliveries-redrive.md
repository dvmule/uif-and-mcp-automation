# ADR-0017 — Glue Python Shell vs PySpark for `event-dev-fabric-redrive`

**Status:** Proposed
**Date:** 2026-08-29
**Scope:** Which AWS Glue job type to use for the bulk redrive engine that resends failed subscriber deliveries from `event-{env}-failed-deliveries`, and the sizing/timeout/concurrency settings that follow from that choice.

**Related:**
- [ADR-0015](0015-eventbridge-event-centric-architecture-and-capacity.md) (event-centric architecture and capacity)
- [ADR-0007](0007-five-dynamodb-tables-for-eventing.md) (five eventing DynamoDB tables, including Table 4 `uif-event-{env}-delivery-metrics`)
- [`replay-failed-deliveries.md`](../replay-failed-deliveries.md) (full redrive flow, request/response shapes, sequence diagrams)

---

## Context

`event-{env}-failed-deliveries` holds subscriber-delivery failures — events that reached the domain bus, matched a customer rule, but the customer's own target (SQS, Kinesis, or an API destination) rejected delivery after 3 EventBridge retries. Customers self-serve a redrive of these failures from their own UI, either selecting specific items or requesting "redrive all."

When the actual item count is large (customers can have anywhere from 1 to 1,00,000+ outstanding failures), a synchronous Lambda cannot safely process the batch — Lambda's 15-minute execution ceiling and, more restrictively, API Gateway's 29-second synchronous timeout rule it out for anything beyond a small handful of items. The large-batch path needs an engine with no such ceiling: AWS Glue.

The redrive workload has a specific shape that needs to be checked against Glue's two job types before picking one:

- **Strictly sequential, ordering-constrained.** Events must be redelivered in the same order they originally failed (oldest `failed_at` first), one at a time, never in parallel — sending a later `deleted` event before an earlier `updated` event for the same record could leave the customer's own system in an inconsistent state. If one item fails to redeliver, the whole sequence halts rather than skipping ahead.
- **I/O-bound, not compute-bound.** Each unit of work is a single network call (`sqs.send_message`, `kinesis.put_record`, or an HTTP POST to a customer endpoint) reading `target_arn`/`event_detail` already stored on the row — no transformation, no aggregation, no join against another dataset.
- **No domain-specific processing logic.** Unlike `event-{env}-fabric-processor`/`event-{env}-fabric-recovery`, this job never reads `PRODUCT_DELTA`, never validates against Apicurio, and never constructs a `DetailType` — it re-sends an already-validated payload verbatim.

### Why two execution engines exist — Lambda and Glue, not just one

The response to every redrive request is always asynchronous, regardless of item count — the customer gets an immediate acknowledgment, and the actual send-loop runs afterward. That still leaves an open question: what actually executes the send-loop? Neither Lambda alone nor Glue alone covers the full range of realistic batch sizes well:

- **Lambda alone would fail for large batches.** Lambda has a hard 15-minute execution ceiling, with no exception. A customer with a large backlog against a slow target (e.g. 1,00,000 items at ~1 second each ≈ 27.8 hours) cannot be processed by a single Lambda invocation — the invocation would be killed mid-sequence well before completion, with no way to resume cleanly from inside that same execution.
- **Glue alone would be wasteful for small batches.** Even Python Shell (fast relative to Spark) still pays real provisioning latency — commonly 10–30+ seconds before the script even starts — plus Glue's 1-minute billing minimum. Routing a 1–2 item redrive through Glue means the customer waits longer and the platform pays a billing floor, purely to redeliver a handful of items that a Lambda invocation would finish in a couple of seconds for a fraction of a cent.

**Resolution:** route by the *actual* item count (known directly for a selected-items request, or obtained via a cheap `Select: "COUNT"` query for a "redrive all" request), against a configurable threshold, per `domain` and per `target_type` (SQS/Kinesis tolerate a much higher Lambda-handled ceiling than an unpredictable customer-owned HTTP endpoint):

- **At or under the threshold** → an async-invoked Lambda (`InvocationType='Event'`) runs the send-loop directly. Fast start, cheap, no Glue provisioning cost.
- **Over the threshold** → `event-{env}-fabric-redrive` (this ADR's subject) runs the send-loop instead, since it has no comparable execution-time ceiling (up to 7 days).

**Both engines execute the identical ordering/halt logic**, factored into one shared Python library imported by both the Lambda handler and the Glue script, so behavior (sort by `failed_at` ascending, process one item at a time, halt on first failure, update the row in place) does not diverge between the two — the engine choice is purely an infrastructure/cost/latency optimization, not a behavioral fork. Neither engine writes to any dedicated progress-tracking table; both only ever update rows in `event-{env}-failed-deliveries` itself, and progress is derived afterward from a live remaining-`FAILED`-count query (plus, for the Glue engine specifically, Glue's own `get_job_run()` state).

This ADR concerns the second half of that split only — once a batch is routed to Glue, which Glue job **type** should run it.

---

## Decision

**Use AWS Glue Python Shell, not PySpark (Apache Spark ETL), for `event-{env}-fabric-redrive`.**

### Comparison

| Dimension | PySpark (Apache Spark ETL) | Python Shell |
|---|---|---|
| **Execution model** | Distributes work across partitions, parallel by design | Single plain Python process, no cluster |
| **Fit for a strictly sequential, ordering-constrained loop** | Fights the engine — requires forcing single-partition execution (`.repartition(1)`) or collecting to the driver and looping in plain Python, none of which uses Spark's actual value | Native fit — this is exactly the workload Python Shell is for |
| **Startup latency** | Cluster provisioning (driver + executors) before any user code runs, typically 1–2+ minutes | Plain process start, no cluster, seconds |
| **DPU rate** | $0.44 / DPU-hour | $0.44 / DPU-hour — **same rate**, the cost difference below is entirely about minimum allocation, not price |
| **Minimum/allowed `MaxCapacity`** | Minimum 2 DPU, defaults to 10 if unspecified; range 2–100 DPU | Exactly two allowed values: `0.0625` DPU (default) or `1` DPU — no range, no tiering possible |
| **Cost, 5-minute run (illustrative)** | 2 DPU minimum: $0.073. Default 10 DPU (if not explicitly right-sized): $0.367 | 0.0625 DPU: $0.0023 — roughly **30–160x cheaper** than the Spark minimum/default respectively |
| **Cost, 1,00,000-item run against a slow customer target (~27.8 hrs)** | Even at the 2-DPU floor: ~$24.50 | At 0.0625 DPU: ~$0.77 |
| **Scaling behavior for larger batches** | More DPU/workers → more parallel partitions → genuinely faster for data-parallel workloads | No scaling lever exists — and none is needed, since the bottleneck is network wait time per sequential item, not local compute; adding DPU capacity would not speed up a task that's blocked on I/O one call at a time |
| **Max job timeout** | Up to 7 days (10,080 minutes); default 2,880 min (Glue ≤4.0) or 480 min (Glue 5.0+) | Same platform-wide timeout ceiling and defaults apply |

### Why NOT PySpark 

Spark's entire value proposition — distributing a dataset across partitions for parallel processing — is the opposite of what this workload needs. The ordering guarantee requires strict single-threaded sequential execution; running that on Spark means either fighting the framework (forcing single-partition execution) or collecting everything to the driver and looping in plain Python, in which case Spark's distributed engine is doing nothing except adding cluster-provisioning latency and cost for capacity that goes unused. Spark's minimum 2-DPU floor (16 vCPUs / 64 GB total capacity) would be paid on every run regardless of how small the actual batch is, for a workload that never uses more than a fraction of one DPU's compute at any point.

### Why Python Shell was chosen

- Matches the workload's actual shape: a sequential loop of small network calls, one item in memory at a time.
- No cluster to provision — starts in seconds, not minutes.
- Cost scales with actual DPU allocation (`0.0625` DPU default), not a forced minimum far larger than the workload needs.
- Reuses the same Glue tooling, IAM patterns, and `glue.start_job_run()` triggering convention already used for `event-{env}-fabric-processor` and `event-{env}-fabric-recovery` — no new AWS service introduced, only a different job **type** within the same service.

---

## Sizing decisions that follow from this choice

### `MaxCapacity` — flat, no tiering

Unlike `event-{env}-fabric-recovery`'s Spark-based `incremental`/`large_run` tiers (which scale sizing with **delta row volume processed in parallel**), this job has no equivalent tiering, because there is nothing to tier: Python Shell only accepts `0.0625` or `1` DPU, and neither compute nor memory demand changes with item count, since the workload never holds more than one row's payload in memory at a time and is never CPU-bound.

**Decision: `MaxCapacity: 0.0625`** (the default) for all target types and all volumes, including 50k/1,00,000-item redrives — the bottleneck at any scale is network wait time per sequential call, not local compute, so paying 16x more for `1` DPU buys no speed and only adds cost.

### `Timeout` — tiered by target type, set explicitly

Glue's *default* timeout depends on Glue version (2,880 min for ≤4.0, only 480 min / 8 hrs for 5.0+) — left unset, a large redrive against a slow customer HTTP target could be killed by that default well before completion, even though the platform's actual ceiling (7 days) would allow it to finish. Timeout must therefore be set explicitly, not left to the version default, and tiered by `target_type` since SQS/Kinesis latency is predictable and fast while a customer-controlled HTTP endpoint is not:

```json
"timeout_minutes_by_target_type": {
  "sqs": 360,
  "kinesis": 360,
  "http": 2880
},
"timeout_minutes_default": 720
```

### `MaxConcurrentRuns` — must be raised above the default

`ExecutionProperty.MaxConcurrentRuns` defaults to `1` for any Glue job. Since `event-{env}-fabric-redrive` is a single shared job invoked by any customer redriving failures at any time, leaving this at the default would cause a second customer's simultaneous redrive request to fail outright (`ConcurrentRunsExceededException`) rather than run alongside the first. This must be explicitly raised (e.g. `20`, tuned against expected concurrent redrive volume and account-level Glue service limits).

---

## Naming standards applied in this design

Exact names, following the same conventions established for the processor/recovery pipelines — no deviation, including for the newly introduced Lambda functions:

| Category | Pattern | Exact name(s) used here |
|---|---|---|
| Glue job — redrive | `event-{env}-fabric-{purpose}` | `event-dev-fabric-redrive` |
| Glue job — processor (existing, for contrast) | `event-{env}-fabric-{purpose}` | `event-dev-fabric-processor` |
| Glue job — recovery (existing, for contrast) | `event-{env}-fabric-{purpose}` | `event-dev-fabric-recovery` |
| Config `pipelineid` — redrive | `event-{env}-{domain}-redrive` | `event-dev-product-redrive` |
| Config `pipelineid` — processor (existing, for contrast) | `event-{env}-{domain}` | `event-dev-product` |
| Config `pipelineid` — recovery (existing, for contrast) | `event-{env}-{domain}-recovery` | `event-dev-product-recovery` |
| Lambda — request handler / router | `event-{env}-{purpose}-trigger` | `event-dev-redrive-trigger` |
| Lambda — small-batch execution engine | `event-{env}-{purpose}-worker` | `event-dev-redrive-worker` |
| Lambda — Glue argument bridge (unpacks the EventBridge `detail` into `start_job_run` `Arguments`, since EventBridge's native Glue target cannot map event fields into job arguments) | `event-{env}-{purpose}-glue-starter` | `event-dev-redrive-glue-starter` |
| DynamoDB — domain config table (existing, for contrast) | `event-{env}-glue-configs` | `event-dev-glue-configs` |
| DynamoDB — failed deliveries table (existing, for contrast) | `event-{env}-failed-deliveries` | `event-dev-failed-deliveries` |
| Control-plane event, routing decision | PascalCase, `{Subject}{PastTenseVerb}` | `RedriveRequested` |
| Control-plane event source (pending confirmation, same open item as elsewhere in this system) | — | `event-dev-redrive-trigger` (proposed; not yet finalized against the platform's broader `Source` string convention) |

No new naming pattern was introduced for this design — the Lambda names above extend the exact same `event-{env}-{purpose}[-role]` shape already used for every Glue job, table, and config entry in this system, rather than adopting a separate convention for Lambda functions specifically.

---

## Consequences

**Positive:**
- Lower cost at every observed scale (single item through 1,00,000+), by roughly 16–160x depending on comparison point.
- Faster start — no cluster provisioning delay, which matters for a customer-facing, if asynchronous, action.
- Code matches the workload's actual execution model — no fighting Spark's parallelism to force sequential behavior.
- No new AWS service introduced — same Glue platform, IAM role structure, and Terraform module family as the processor/recovery jobs.

**Negative:**
- **Job type differs from `event-{env}-fabric-processor`/`-recovery`**, which are both PySpark. This is a deliberate, documented exception — not an oversight — but it does mean the platform now has two Glue job types to operate, monitor, and reference in runbooks instead of one. Anyone extending or debugging this job should not "fix" it back to Spark without reading this ADR.
- Python Shell jobs cannot use job bookmarks — not a limitation for this specific use case (progress is tracked via row `status`, not bookmarks), but worth remembering if this job is ever repurposed for something that would want bookmark-based resumption.
- `MaxCapacity` has no scaling range (only two fixed values) — if a future requirement for this job turns out to be genuinely CPU- or memory-bound rather than I/O-bound, Python Shell's ceiling (`1` DPU) may prove insufficient, and a re-evaluation of job type would be needed at that point.

---

## References

- [`replay-failed-deliveries.md`](../replay-failed-deliveries.md) — full redrive flow, request/response shapes, sequence diagrams, and the `event-{env}-{domain}-redrive` config entry shape
- [AWS Glue pricing](https://aws.amazon.com/glue/pricing/) — DPU-hour rate, billing minimums
- [AWS Glue Job API reference](https://docs.aws.amazon.com/glue/latest/webapi/API_Job.html) — `MaxCapacity` allowed values per job type, `Timeout` limits and defaults, `ExecutionProperty.MaxConcurrentRuns`
