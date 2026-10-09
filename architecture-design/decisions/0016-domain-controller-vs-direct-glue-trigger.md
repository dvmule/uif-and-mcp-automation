# ADR-0016 — Domain Controller Lambda vs direct Glue / SNS trigger

**Status:** Proposed  
**Date:** 2026-08-26  
**Scope:** Whether event-centric publish requires a **Domain Controller Lambda** between Gold MERGE completion and **Event Fabric Processor Glue**, or whether Gold / SNS / EventBridge can start the processor directly.

**Related:**

- [`../event-centric-publish-reference.md`](../event-centric-publish-reference.md) — full control-plane + publish path  
- Diagram: minimal DC (start Glue only) — **TBD**, not yet in the repo  
- [ADR-0015](0015-eventbridge-event-centric-architecture-and-capacity.md) — bus, capacity, `split_by_operation`

---

## Context

After Gold incremental MERGE, changed rows land in `{domain}_changes` (with `OP`). Something must start **Event Fabric Processor Glue**, which reads the delta, validates against Apicurio, and **PutEvents** to `uif-event-{env}-{domain}-bus` for subscriber EventBridge rules.

Gold emits a completion signal on the **default** bus:

```text
Source:      echo.gold.transform
DetailType:  GoldMergeCompleted
Detail:      gold_run_id, domain
```

### Question

Do we need **Domain Controller (DC) Lambda**, or can we:

1. Call **Event Fabric Processor Glue directly** from Gold Glue?  
2. Publish to **SNS** and let that trigger Glue?  
3. Use **EventBridge → Glue** as a rule target and skip Lambda entirely?

### Prior discussion

- Full design: DC reads **`uif-event-{env}-pipeline-configs`**, applies idempotency and skip logic, counts the run's rows in the delta table, and may start **1–3 processor runs** via **`split_by_operation`** from the per-`OP` counts.  
- Simplified spike: DC only calls `glue.start_job_run` with fixed arguments — no DynamoDB (diagram TBD).

---

## Options considered

| Option | Path | Summary |
|--------|------|---------|
| **A — Domain Controller Lambda** | `GoldMergeCompleted` → EventBridge → **Lambda** → `glue.start_job_run` | Control-plane orchestrator |
| **B — EventBridge → Glue direct** | `GoldMergeCompleted` → EventBridge rule → **Glue job target** + input transformer | No Lambda; fixed args only |
| **C — SNS → Lambda → Glue** | Gold → SNS → Lambda → Glue | Extra hop; still needs Lambda |
| **D — Gold Glue → Glue direct** | End of Gold job → `start_job_run(processor)` | Same process chain, no event |
| **E — Step Functions** | EventBridge → SFN → conditional / parallel Glue starts | Heavier orchestration |

---

## Evaluation

### Option D — Gold calls processor Glue directly

**Rejected** for production.

| Concern | Impact |
|---------|--------|
| Coupling | Gold script must know processor job name, args, eventing lifecycle |
| IAM | Gold role needs `glue:StartJobRun` on processor job |
| Blast radius | Processor start failure muddies Gold run outcome after MERGE |
| Runtime | Extends already heavy MERGE job; timeout risk |
| OP-based fan-out | `split_by_operation` (1–3 runs) bloats Gold |
| Testing | Cannot exercise eventing without full Gold run |

Gold should emit **facts** (`GoldMergeCompleted`), not orchestrate downstream jobs.

---

### Option C — SNS → Lambda → Glue

**Rejected** as primary pattern.

- SNS does **not** start Glue natively — a **Lambda subscriber is still required** (same role as DC).  
- Loses EventBridge **content filtering** on `detail.domain`, `detail-type` without bridging SNS ↔ EventBridge.  
- Adds a hop vs **EventBridge → Lambda** with no capability gain.

Use SNS only if an external system already requires it; not as the default EDS trigger.

---

### Option B — EventBridge → Glue direct

**Accepted for Phase 1 / UAT spike** where orchestration is trivial.

```text
GoldMergeCompleted (default bus)
  → EventBridge rule
  → Target: Event Fabric Processor Glue
  → Input transformer: --domain, --run_id from event detail
```

| Pros | Cons |
|------|------|
| Fewest moving parts | No conditional skip (`total == 0`) without custom Glue logic |
| No Lambda to deploy/monitor | Cannot start **1–3 jobs** from OP counts without Step Functions or Lambda |
| Native AWS integration | Duplicate events → duplicate Glue runs (no idempotency gate) |
| Fine for single-schema spike | Worker sizing / config changes require rule or job redeploy |

**Verdict:** Valid when DC would **only** call `start_job_run` with static parameters (diagram TBD).

---

### Option A — Domain Controller Lambda

**Accepted for production** event-centric publish.

| Capability | Why Lambda |
|------------|------------|
| **Skip logic** | No processor run when the run's delta row count is `0` or no publishable OPs |
| **`split_by_operation`** | Start 1–3 processor runs (`INSERT` / `UPDATE` / `DELETE`) from per-`OP` counts queried from the delta table |
| **Worker profiles** | Choose the `glue.sizing_tiers` entry matching the row count |
| **Idempotency** | Dedupe duplicate `GoldMergeCompleted` via `uif-event-{env}-pipeline-metrics` before expensive Glue |
| **Decoupling** | Gold only `PutEvents`; processor IAM and failures isolated from MERGE |
| **Config-driven** | `uif-event-{env}-pipeline-configs` (or env) without redeploying Gold |
| **REPLAY / manual** | Single entry point for non-MERGE processor triggers |

**Verdict:** Required once orchestration exceeds “always start one fixed job.”

---

### Option E — Step Functions

**Deferred.** Consider if orchestration grows beyond a single Lambda (approval steps, complex branching, long waits). Overkill for current scope.

---

## Decision

### Production (Phase 2+)

**Adopt Domain Controller Lambda** as the **eventing control plane** between Gold and Event Fabric Processor Glue.

```text
Gold MERGE
  → PutEvents GoldMergeCompleted (echo.gold.transform, default bus)
  → EventBridge rule
  → Domain Controller Lambda
       idempotency, skip logic, config (optional)
       glue.start_job_run (1–3 runs when split_by_operation)
  → Event Fabric Processor Glue
  → PutEvents uif-event-{env}-{domain}-bus
  → subscriber rules
```

Gold **must not** call processor Glue directly.

### UAT / spike (Phase 1)

**Permit EventBridge → Glue direct** when all of the following hold:

- Single processor run per merge event  
- Fixed Glue arguments (hard-coded or env on job)  
- No skip / idempotency / OP-based fan-out requirements  
- Accept duplicate runs on duplicate events until idempotency is added in processor or control plane  

Document the spike explicitly; migrate to DC before production volume (2.5L+ delta/day).

### SNS

**Not adopted** as the primary trigger between Gold and processor Glue.

---

## Phased rollout

| Phase | Trigger | When |
|-------|---------|------|
| **1 — Spike** | EventBridge → Glue **or** DC with `start_job_run` only | One schema, prove delta → bus → subscribers |
| **2 — Production product** | **Domain Controller Lambda** | Skip rules, idempotency, delta row counts, config |
| **3 — Scale (2.5L–10L)** | DC + **`split_by_operation`** | OP-based parallel processor runs |
| **4 — Optional** | Step Functions | Orchestration graph outgrows one Lambda |

---

## Consequences

### Positive

- Clear separation: Gold = data plane (MERGE + delta), DC = control plane (when/how to publish)  
- Safe evolution from spike to full [`event-centric-publish-reference.md`](../event-centric-publish-reference.md) without changing Gold contract  
- OP-based Glue fan-out stays out of Gold and out of EventBridge-only wiring  

### Negative

- Additional Lambda to deploy, monitor, and secure  
- Extra hop (typically seconds) before processor starts — acceptable vs MERGE + publish duration  
- Team must implement idempotency and skip logic in DC (not optional at scale)  

### Gold contract (invariant)

Gold always emits **`GoldMergeCompleted`** carrying identity only:

- `gold_run_id`, `domain`  

Row counts are **not** on the event. The DC queries the delta table for the run's total and per-`OP` counts — this is the trade for a minimal, stable Gold contract: Gold never has to know what the eventing side needs, at the cost of one delta query per run before the processor starts.  

Processor and DC never require Gold to invoke Glue for eventing.

---

## Alternatives summary

| Approach | Production | Spike |
|----------|:----------:|:-----:|
| Domain Controller Lambda | **Yes** | Optional |
| EventBridge → Glue direct | No | **Yes** |
| SNS → Lambda → Glue | No | No |
| Gold → Glue direct | **No** | No (dev only) |
| Step Functions | Future | No |

---

## References

- [`../event-centric-publish-reference.md`](../event-centric-publish-reference.md)  
- [`0007-five-dynamodb-tables-for-eventing.md`](0007-five-dynamodb-tables-for-eventing.md) — the tables the DC reads and writes  
- [ADR-0015](0015-eventbridge-event-centric-architecture-and-capacity.md)  
- [Amazon EventBridge targets — AWS Glue](https://docs.aws.amazon.com/eventbridge/latest/userguide/eb-targets.html)
