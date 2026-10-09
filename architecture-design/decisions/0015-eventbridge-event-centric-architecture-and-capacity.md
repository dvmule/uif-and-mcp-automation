# ADR-0015 — EventBridge event-centric architecture and capacity

**Status:** Proposed  
**Date:** 2026-08-26  
**Scope:** Architecture decision and capacity assessment for event-centric publish on Amazon EventBridge — one bus per domain, subscriber rules, AWS limits, and processing bounds for **product** (and peer domains) at **2.5L–10L** delta/day with **10–300 subscribers**.  

**Related:** [`../event-centric-publish-reference.md`](../event-centric-publish-reference.md) — the end-to-end publish path this ADR sizes

---

## Context

Enterprise Data Connect publishes Gold delta changes through an **event-centric** path:

```text
Gold MERGE → Domain Controller → Event Fabric Processor Glue
  → PutEvents on uif-event-{env}-{domain}-bus
  → EventBridge rules (1 per subscription)
  → SQS / Kinesis / API destination
```

### Design choices

| Item | Choice |
|------|--------|
| Bus naming | `uif-event-{env}-{domain}-bus` — e.g. `uif-event-dev-product-bus`, `uif-event-dev-price-bus` |
| DetailType | `{domain}.{operation}.v{n}` — e.g. `product.updated.v2` |
| Operations | `product.created`, `product.updated`, `product.deleted` (**9 DetailTypes max**: 3 ops × 3 major versions) |
| Apicurio | `{domain}.{domain}.{operation}` (one artifact per operation) |
| Subscriber model | **1 rule per subscription** (customer × detail-type), **1 target per rule** (SQS **or** Kinesis **or** API) |
| Glue scaling | Same processor job; optional **`split_by_operation`** (1–3 runs from `delta_summary`) |
| Account usage | EventBridge also drives **internal** triggers (Glue state, schedulers) |

### PROD volume (product)

| Horizon | Delta rows / day | Bus events (× 2 major versions) |
|---------|-----------------:|-----------------------:|
| Today | **200k – 250k** (2L – 2.5L) | **400k – 500k** |
| Growth | **Up to 1M** (10L) | **Up to 2M** |

| Subscriber scale | Consumers on `uif-event-dev-product-bus` | Rules on the bus (3 detail-types each) |
|------------------|------------------------------------:|--------------------------------------:|
| Initial | **~10** | **~30** |
| Growth | **20 – 30** | **60 – 90** |
| Platform cap | **~100** | **300** (AWS rules/bus quota) |

The AWS quota applies to **rules**, not consumers — one rule per subscription (customer × detail-type), so the consumer ceiling moves with subscription breadth. Three detail-types is the common case: one domain's `created` / `updated` / `deleted` at a single major version. During a version migration a consumer holds both majors — six rules, not three — so the ceiling halves to **~50 consumers** for the length of the window; [ADR-0013](0013-schema-versioning-and-consumer-migration.md) caps live majors at two.

### Questions

1. Should we use **one bus per domain** or multiple buses (per operation, major version, or subscriber)?  
2. Can EventBridge handle **2.5L–10L** delta with **two ACTIVE major versions** and **30 subscribers** without delivery failures?

---

## Decision

**Adopt one custom EventBridge bus per domain.** Route by **`DetailType`** and **per-subscription rules** — not by additional buses.

```text
uif-event-dev-product-bus    — all product create / update / delete events
uif-event-dev-price-bus      — price domain
uif-event-dev-customer-bus   — customer domain
```

**Capacity conclusion:** EventBridge on **eu-west-1** supports **2.5L–10L** delta with **30 subscribers** on a single domain bus, subject to UAT verification. Expected **first bottleneck: Glue publish duration**, not bus ingest.

---

## Subscriber and rule model

Glue publishes **once per logical event**. EventBridge delivers to **each matching rule**:

```text
total_invocations = Σ (events_for_detail_type_d × subscribers_listing_d)
```

### Platform policy (required)

| Rule | Value |
|------|-------|
| Rules per subscription (one customer x detail-type) | **1** |
| Targets per rule | **1** (SQS \| Kinesis \| API) |
| `detail-type` filter | **Required** — never `source`-only |
| Max consumer rules per domain bus | **300** (AWS rules/bus quota) |
| PutEvents batch size | **10** (AWS maximum) |
| Max Detail for SQS targets | **~200 KB** (SQS hard limit **256 KB**) |

### Example — one customer subscribed to two detail-types

One rule per subscription, so a customer on two detail-types has **two** rules, each with its own target. `detail-type` alone is sufficient: the domain bus carries only our events, and recovery republishes under a different `source` than the processor — filtering on `source` would silently drop recovered events.

```json
{ "detail-type": ["product.created.v2"] }
```

```json
{ "detail-type": ["product.updated.v1"] }
```

### Anti-patterns

| Anti-pattern | Why forbidden |
|--------------|----------------|
| One rule with 30 SQS targets | AWS max **5 targets/rule** |
| Rule without `detail-type` | Matches all events → invocation storm |
| 30 rules all on `product.updated.v2` | 300k events → **9M invocations** — allowed but costly |

**Mixed targets across customers:** Customer A → SQS, B → Kinesis, C → API on the **same bus** — each with their own rule.

---

## AWS EventBridge limits (eu-west-1 defaults)

Reference: [EventBridge quotas](https://docs.aws.amazon.com/eventbridge/latest/userguide/eb-quota.html)

### Publishing (Glue → bus)

| Limit | Value | Implication |
|-------|-------|-------------|
| Entries per `PutEvents` call | **10 max** (hard) | `putevents_batch_size: 10` |
| `PutEvents` request size | **< 1 MB** total | Validate batches in processor |
| Entry / `Detail` size | ~**256 KB** | S3 pointer for outliers |
| `PutEvents` throttle | **10,000 calls/sec** | ~**100k events/sec** if batches full |
| `DetailType` length | **128 chars max** | `{domain}.{operation}.v{n}` fits |

### Fan-out (bus → subscribers)

| Limit | Value | Implication |
|-------|-------|-------------|
| Target invocations / sec | **18,750** (account-wide) | Shared with internal EventBridge rules |
| Rules per event bus | **300** | Max ~300 subscriptions — ~100 consumers at 3 detail-types each |
| Targets per rule | **5 max** (hard) | Platform uses **1** |
| Custom buses / account | **100** | One per domain is sufficient |
| Event pattern size | **2,048 bytes** | Customer DetailType lists |
| Ordering | **None** | `idempotency_key` on every event — **Future Scope** |
| Retry / DLQ | Per target (recommend **3** + DLQ) | → `uif-event-{env}-delivery-metrics` |

### Downstream payload limits

| Target | Limit | Notes |
|--------|-------|--------|
| **SQS** | **256 KB**/message | `input_path = "$.detail"`; sample product.updated **~2–5 KB** |
| **Kinesis** | **1 MB**/record | Customer owns shard capacity |
| **API** | Contract-dependent | Highest failure risk; prefer SQS buffer |

---

## Volume model

| Symbol | Meaning |
|--------|---------|
| **R** | Delta rows per run |
| **T** | ACTIVE Apicurio major versions |
| **B** | PutEvents batch size (= 10) |
| **M(d)** | Subscribers whose rule includes DetailType **d** |
| **C** | Total consumer rules on the bus — one per subscription (customer × detail-type) |

```text
bus_events      = R × T
putevents_calls = bus_events / B
invocations     ≈ Σ (events_per_detail_type × M(d))
```

### Bus ingress scenarios

| Scenario | R | T | Bus events | PutEvents calls |
|----------|---:|---:|-----------:|----------------:|
| PROD today | 250k | 2 | **500k** | 50k |
| **5L** | 500k | 2 | **1M** | 100k |
| **10L** | 1M | 2 | **2M** | 200k |
| 10L + 3 major versions | 1M | 3 | **3M** | 300k |
| Post-migration | 250k | 1 | **250k** | 25k |

**Adding subscribers does not increase PutEvents** — only matching-rule **invocations**.

### DetailType distribution (250k delta example)

| DetailType | Events |
|------------|-------:|
| `product.created.v1` / `.v2` | 12k each |
| `product.updated.v1` / `.v2` | 235k each |
| `product.deleted.v1` / `.v2` | 3k each |
| **Total** | **500k** |

### Subscriber scenario matrix (500k bus events)

`C` counts **rules**, not consumers — `C = 30` is ten consumers at three detail-types each.

| Scenario | C (rules) | Pattern | Invocations | Fan-out @ 18,750/sec |
|----------|--:|---------|------------:|---------------------:|
| Minimal overlap | 10 | M(d)=1 | **500k** | ~27 sec |
| Realistic | 10 | M(d)≈2 | **0.8–1.2M** | ~43–64 sec |
| Realistic | 30 | M(d)≈5 | **2–3.5M** | ~2–3 min |
| Worst overlap | 30 | all on `product.updated.v2` | **~7.5M+** | ~7 min |
| Anti-pattern | 30 | catch-all rules | **15M** | ~13 min |

### Growth horizon (30 subscriptions ≈ 10 consumers at 3 detail-types, M≈4)

| Delta | Bus events | PutEvents calls | Invocations | Fan-out | Ingest @ max |
|------:|-----------:|----------------:|------------:|--------:|-------------:|
| 250k | 500k | 50k | ~2M | ~2 min | ~5 sec |
| 500k | 1M | 100k | ~4M | ~3.5 min | ~10 sec |
| **1M (10L)** | **2M** | **200k** | **~8M** | **~7 min** | **~20 sec** |
| **1M (10L), 30 consumers** (90 subs, M≈15) | **2M** | **200k** | **30M** | **~27 min** | **~20 sec** |

---

## Processing time (end-to-end)

| Phase | Owner | 2.5L (500k events) | 10L (2M events) |
|-------|--------|-------------------:|----------------:|
| Domain Controller | Lambda | Seconds | Seconds |
| **Processor publish** | Glue | **Minutes** (bottleneck) | **Tens of min** without split-by-op |
| EventBridge ingest | EventBridge | ~5 sec @ max | ~20 sec @ max |
| EventBridge fan-out | EventBridge | ~1–3 min (30 subs) | ~5–8 min |
| Customer drain | SQS/Kinesis/API | Customer ops | Scale pollers/shards |

At slow Glue publish (~1k events/sec): **~8 min** (500k) / **~33 min** (2M) for PutEvents alone.

### Glue scaling at 10L — `split_by_operation`

Domain Controller starts **1–3 instances** of the **same** processor job, skipping ops with `delta_summary` count **0**:

| Benefit | Effect |
|---------|--------|
| Parallel wall-clock | UPDATE job bounds duration |
| Per-op sizing (`glue.by_operation`) | Fat UPDATE; lean DELETE |
| Same bus volume | No change to EventBridge totals |

---

## Options considered

| Option | Verdict |
|--------|---------|
| **A — One bus per domain + `{domain}.{operation}.v{n}`** | **Chosen** — volume, clarity, 300-rule headroom |
| B — Bus per operation (`.created`, `.updated`, `.deleted`) | Rejected — 3× ops overhead; **zero** capacity gain |
| C — Bus per subscriber | Rejected — burns bus quota; unmanageable |
| D — Major version only in payload (not DetailType) | Rejected — weakens rule-based routing during migration |
| E — Emit latest ACTIVE major version only | Optional migration flag; not default while v1 consumers exist |

**Why one bus per domain:**

| Reason | Explanation |
|--------|-------------|
| Capacity is event-count driven | Volume = **R × T**, not bus count |
| Small routing space | **9 DetailTypes** << **300** rules |
| 1 target/rule AWS constraint | Must use 1 rule per subscription anyway |
| Same invocations regardless of bus layout | Extra buses add Terraform, not throughput |
| Domain alignment | Matches `uif-event-{env}-pipeline-configs` and `uif-event-{env}-{domain}-bus` naming |

---

## Risks and mitigations

| Risk | Mitigation |
|------|------------|
| Glue publish timeout at 10L | `split_by_operation`; parallel PutEvents; `glue.by_operation` |
| PutEvents throttling | Backoff; quota increase; stagger vs internal rules |
| Overlapping subscriptions | Subscription registry; limit overlap on hot DetailTypes |
| SQS 256 KB | `input_path=$.detail`; max ~200 KB Detail |
| No ordering | `idempotency_key` on every event — **Future Scope** |
| Two live major versions double volume | Retire v1 in Apicurio after migration |
| Account invocation quota | Monitor; UAT; quota increase if needed |

---

## Worked examples

### Customer ABC (250k run, 2 major versions)

Subscribes to `product.created.v2` + `product.updated.v1`:

```text
Glue PutEvents:  500,000 total (unchanged by subscriber count)
ABC receives:    247,000 messages (12k + 235k)
```

### 30 customers on `product.updated.v2` (300k update events)

```text
Glue PutEvents:  300,000 (once per event)
Invocations:     9,000,000 (300k × 30)
Per SQS queue:   300,000 messages
```

---

## Verification before PROD

1. UAT soak at **250k** and **1M** delta rows with **30** customer rules (mixed SQS/Kinesis/API).  
2. Measure: Glue duration, PutEvents throttles, `Invocations`/`FailedInvocations`, SQS oldest message age.  
3. Validate per-major-version payload sizes on full run (sample CSV: **~2–5 KB**).  
4. Enable **`split_by_operation`** for runs **≥ 500k** delta rows.  
5. Maintain subscription registry (customer × DetailTypes).  
6. Update status to **Accepted** or **Accepted with quota increase** based on UAT.

---

## Consequences

**Positive:** Single onboarding pattern; one bus per domain; clear capacity formula; supports 30→300 customers.

**Negative:** Fan-out cost grows with subscription overlap; a two-major-version migration doubles events; account quota shared with internal EventBridge usage.

---

## References

- [Amazon EventBridge quotas](https://docs.aws.amazon.com/eventbridge/latest/userguide/eb-quota.html)
- [PutEvents API](https://docs.aws.amazon.com/eventbridge/latest/APIReference/API_PutEvents.html)
- [`../event-centric-publish-reference.md`](../event-centric-publish-reference.md)
- [`0007-five-dynamodb-tables-for-eventing.md`](0007-five-dynamodb-tables-for-eventing.md) — failed-delivery recording (Table 4)

