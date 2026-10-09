# ADR-0025 — Delivery outcomes: from inference to observed delivery (Delivery Fabric)

**Status:** Decision pending (was Proposed)
**Date:** 2026-09-24, reopened 2026-09-30, last revised 2026-10-01
**Scope:** How per-event, per-consumer delivery outcomes are produced for
`uif-event-{env}-delivery-metrics` (ADR-0007 Table 4), and consequently how
the last delivery hop to subscriber targets is performed.
**Jira:** UIFPE-453

**Related:** ADR-0007 (five eventing tables), ADR-0015 (event-centric
architecture), ADR-0017 (redrive engine), ADR-0024 (domain eventing is
config, not terraform), `event-centric-publish-reference.md`

---

## 0. Why the decision is pending (reopened 2026-09-30)

A sixth option exists that this ADR did not consider, and its omission rests
on an overstatement in §1.

**EventBridge event-bus logging** reached GA in July 2025. Configured at
`level = INFO` with `include_detail = FULL`, a bus emits one
`INVOCATION_SUCCESS` record per (event × rule × target), carrying `event_id`,
`details.rule_arn`, `details.target_arn` and `details.target_input`. That is a
**per-event, per-consumer delivery-success signal** — the thing §1 asserts does
not exist.

§1 is not wrong about what it actually measured. CloudWatch `AWS/Events
Invocations` genuinely returns no datapoints when scoped to `RuleName` plus
`TargetId`, which was verified live on 2026-09-22. The error was generalising a
finding about *metrics* into a claim about the platform, and then never
revisiting it. Bus logging is a different mechanism and it was never examined.

This does not invalidate the POC1 or POC2 evidence in §3–§5, which stands as
recorded. It does mean §6's decision was taken from an options set that was
missing a candidate, so the decision is pending rather than reversed until
option 6 has been tested to the same standard as options 1 and 5 — that is,
live, with rows in a real table.

### What option 6's live test settled (2026-09-30)

**`target_input` is real.** The one property §0 originally flagged as assumed
is confirmed: 10 events to 2 subscribers produced 20 `INVOCATION_SUCCESS`
records, every one carrying `details.target_input`, which drove 20
correctly-keyed `DELIVERED` rows in Table 4. So option 6 needs **no** bus
`EventId` in Table 3 and **no** change to `event_fabric_processor.py`. That
removes the escalation path §0 warned about.

**The delivery mechanism changed, and the measurement is the reason.** The
first build pushed records to a writer with a CloudWatch Logs subscription
filter. At 10,000 events × 2 consumers that cost **7,666 Lambda invocations
for 20,020 records — an average batch of 2.6**, because a subscription filter
flushes as records arrive rather than accumulating; extrapolated to a 1M-event
run × 10 consumers it is ~3.85M invocations. More seriously it was push-only:
a log record that never arrived was never asked for again, so a missing row
was undetectable. Option 6 now reads the log group on a schedule against a
persisted watermark, which makes any past window re-readable and a lost
record recoverable. That is a materially better answer to the best-effort
objection than the first build had.

### What the test also exposed, and what it costs

**Table 4's sort key is `{envelope.time}#{event_id}`, not
`{published_at}#{event_id}`.** Production's `dlq_recorder.py` keys on the
envelope's own time — `delivery-metrics.md` defines that field as "Event time
(`body["time"]`) — when it was published", despite calling it `failed_at`. The
two values differ by **4.73 seconds** on a live record. Any option-6 writer
must use the same derivation or its `DELIVERED` rows land on keys that can
never collide with the recorder's `FAILED` rows: one delivery, two rows, and
the conditional write meant to let failure evidence win never fires. The POC
had this wrong until 2026-10-01 and its own tests could not detect it, because
both of its writers shared the error.

**Option 6 therefore requires two changes to `dlq_recorder.py`**, neither of
which the original framing anticipated:

1. Its insert is conditional on `attribute_not_exists(domain_customer)`, which
   was safe while Table 4 held failures only. Once `DELIVERED` rows share the
   key, a `DELIVERED` row written first **rejects the `FAILED` insert and the
   failure is lost.**
2. Its `gsi_event_id` idempotency probe keys on `event_id` alone, and one
   `event_id` spans every consumer — so the first recorded failure suppresses
   the failure row for every other consumer on that event. That contradicts
   this ADR's own requirement of one row per subscribed consumer, and is a
   defect today independent of option 6.

So option 6 is **not** the zero-touch option the first framing suggested. It
leaves the delivery path alone, which is its real advantage, but it modifies
the failure writer.

### The open question is now cost, not feasibility

`include_detail = FULL` is what puts `target_input` on the record, and it
embeds the **entire** CloudEvents envelope in every one. A live product
envelope measures 2,753 bytes, so a record is roughly 3.3 KB; 1M events × 10
consumers is of the order of **33 GB of CloudWatch Logs ingestion per run, per
bus** — and `domain-buses.tf` now gives price, program and stock their own
buses, so that multiplies. `include_detail = NONE` cuts it by roughly 5× but
removes `target_input`, which puts the bus `EventId` back into Table 3 and
re-opens the invasiveness §0 thought it had closed.

**This figure is unmeasured.** It is the one number that decides whether
option 6 is affordable at run scale, and it should be measured before the
decision in §6 is resolved.

## 1. Problem statement (UIFPE-453)

UIFPE-453 requires: *for every event published to a domain bus, one outcome
row per subscribed consumer — `DELIVERED` or `FAILED` — individually
queryable* ("did customer Y receive event X, and when?").

> **Corrected 2026-09-30 — see §0.** The paragraph below overstates its
> evidence. What was measured is that CloudWatch *metrics* carry no per-target
> success signal; what it claims is that the platform carries none at all.
> EventBridge event-bus logging does, via `INVOCATION_SUCCESS`. The rest of
> this section holds; the "no signal exists" framing does not.

The obstacle is an AWS platform fact: **EventBridge emits no per-event
delivery-success signal for SQS or Kinesis targets.** Failure is observable
(retry exhaustion → DLQ, with error attributes); success is silent. Nor does
an independent witness exist after the fact: CloudWatch `AWS/Events
Invocations` cannot be scoped to a single target (the `RuleName`+`TargetId`
dimension pair returns no datapoints), SQS queues cannot be read without
consuming the customer's messages, and API destinations expose nothing.
Only Kinesis streams are non-destructively readable.

Any design that keeps EventBridge as the delivery engine must therefore
**infer** success ("no failure evidence appeared within the retry window"),
and that inference is unverifiable in principle: UIFPE-453's AC 5 ("mark
UNVERIFIED on cross-check mismatch") has no signal to mismatch against. The
question this ADR decides: **infer harder, or restructure so the outcome is
observed.**

## 2. Options explored

| # | Option | Assessment |
|---|---|---|
| 1 | **Inference + hardening** — keep EventBridge delivery; audit-copy rows, DLQ evidence, post-window reconciliation; harden with target-side aggregate metrics (`NumberOfMessagesSent` / `IncomingRecords`) as witness | Built and live-tested as **POC1**. Works for failure; success remains unwitnessed (reports `NOT_CROSS_CHECKED`). Aggregate witnesses can flag a discrepancy but cannot name the missing event. |
| 2 | Positive confirmers where physics allow — Kinesis-stream reader Lambdas confirm per record | Fixes streams only; SQS/API stay inference. Asymmetric contract; rejected as a basis. |
| 3 | Rules target a delivery Lambda directly (no queue in between) | Observed outcomes, minimal resources — but one async invoke per event (unbounded concurrency spikes at 250k-event runs), Lambda-async retry limits including a drop channel, and lossy suspension. Acceptable only as a stepping stone. |
| 4 | Delivery fabric with **one buffer queue per subscription** | Observed outcomes with full per-customer isolation; costs two queues per subscription from day one and heavier runtime provisioning. |
| 5 | Delivery fabric with a **tiered shared buffer** — one shared buffer by default; a subscription graduates to a dedicated buffer by re-targeting its rule | Observed outcomes; day-one footprint of one buffer + DLQ; isolation available where earned. Built and live-tested as **POC2**. **Was chosen; pending re-comparison against option 6 — see §0.** |
| 6 | **Observed outcomes from event-bus logs** — EventBridge keeps delivering; the bus logs at `INFO` / `include_detail = FULL`, and a **scheduled poller** reads `INVOCATION_SUCCESS` records against a persisted watermark and records `DELIVERED`, while the domain DLQ keeps owning `FAILED` | **Not considered in the original ADR (added 2026-09-30).** Outcomes are observed, not inferred, with **no change to the delivery path** — consumers receive exactly as today, no buffer, no Lambda in the hot path, API destinations covered for free. Correlation needs no schema change and `target_input` is confirmed live (§0). Delivery by **poll, not by subscription filter**: the filter cost 7,666 invocations for 20,020 records (batch 2.6) and could not re-read a lost record, where a timestamp window can. **Evidence still weaker than option 5**: bus log delivery is best-effort, so a missing row remains ambiguous — re-reading recovers anything lost downstream of the log group but cannot conjure a record that was never written. **Two costs the first framing missed:** it requires two fixes to `dlq_recorder.py` (§0), and `include_detail = FULL` ingestion at run scale is **unmeasured**. Built; **not yet deployed in this form** — dev still runs the superseded push build. |

## 3. POC1 — inference design (`uif-event-poc-*`)

`delivery-lab/reference-implementations/uifpe-453-delivery-outcome-tracking`

```text
publisher ──► PutEvents ──► product bus ──► ledger row (publish-metrics)

bus rule (per customer × detail-type)
  ├─ TARGET 1 ──► customer target (SQS queue / Kinesis stream)   ← success is SILENT
  └─ TARGET 2 ──► shared audit queue ──► audit-consumer Lambda
                     └──► outcome row born "ATTEMPTED" (+ sparse pending index)

delivery FAILS ──► EventBridge retries ──► DLQ ──► event-handler Lambda
                     └──► UPSERT row = "FAILED" (reason, origin)  ← overwrites ATTEMPTED

after the 900s retry window ──► Delivery Writer (invoked per run)
  ├─ FLIP     ATTEMPTED ──► DELIVERED   (conditional; skips FAILED)
  ├─ GAP-FILL (ledger × subscribers) − FAILED ──► DELIVERED
  └─ VERIFY   per-target CloudWatch … no datapoints ──► "NOT_CROSS_CHECKED"
```

**Worked example** — 10 events published, consumers PMC (SQS) and PDZ
(Kinesis): the bus fans each event to both rules; within seconds the audit
path writes 20 `ATTEMPTED` rows keyed
`{domain}#{customer}` / `{published_at}#{event_id}`. If event 7's PMC
delivery is rejected, EventBridge dead-letters it and the event-handler flips
that one row to `FAILED` with the error code. Twenty minutes later the Writer
flips the surviving 19 to `DELIVERED` and reports per-customer summaries —
`pmc PARTIAL_DELIVERY 9/10, pdz DELIVERED 10/10` — each carrying
`observed_source: none / NOT_CROSS_CHECKED`, because no independent signal
exists to confirm any of the 19.

**Design properties established by POC1:** the sort key must be a pure
function of the publish ledger (all three writers derive it identically);
failure evidence must outrank derived success (the FAILED upsert wins every
race); the shared consumer DLQ needs `failure_origin` disambiguation; and —
decisive — `DELIVERED` is asserted, never witnessed.

## 4. POC2 — delivery fabric (`uif-event-poc2-*`)

`delivery-lab/reference-implementations/uifpe-453-poc2-delivery-fabric`

```text
test-publisher Lambda ──► PutEvents ──► product bus ──► ledger rows

bus rules (per customer × detail-type), ONE target each:
        the SHARED buffer — input transformer stamps
        {"rule_arn": "<aws.events.rule-arn>", "event": <envelope>}
                  ▼
delivery buffer (+ DLQ)          ← retries live here (visibility backoff,
                  │                 maxReceiveCount 5); events PARK, never vanish
                  │  ← SQS trigger (batch 10 / ReportBatchItemFailures / max-concurrency 5)
                  ▼
Delivery Fabric Lambda
  ├─ rule_arn ──► subscription-configs (gsi_rule_arn) ──► customer + target
  ├─ sqs:SendMessage / kinesis:PutRecord — the platform performs the delivery
  ├─ API response received ──► outcome row BORN "DELIVERED"
  │      (confirmed_by = TARGET_API_RESPONSE; MessageId / SequenceNumber attached)
  └─ exception ──► buffer redelivers ──► 5 strikes ──► buffer DLQ
                     ──► failed-writer Lambda ──► row = "FAILED" (retries exhausted)
```

**Worked example** — the same 10-events-to-PMC-and-PDZ run: each rule sends
its copy to the shared buffer with its rule ARN stamped on. The fabric
resolves pmc's copy to the SQS queue, calls `send_message`, receives a
`MessageId`, and writes the outcome row **born `DELIVERED` with that
MessageId attached** — roughly 30 seconds after publish. The pdz copy takes
the Kinesis branch and records the returned `SequenceNumber`, which can be
matched byte-for-byte against the record in the stream. If pdz's target were
unreachable, the fabric's exception returns the record to the buffer; it
retries on the visibility cadence and, on exhaustion, the failed-writer
records `FAILED (DELIVERY_RETRIES_EXHAUSTED)` — the per-attempt SDK error in
the fabric's logs. There is no `ATTEMPTED` state, no reconciler, no waiting
window: Table 4 has a single writer and every row carries its own proof.

## 5. Test evidence

### POC1 — live (dev, 2026-09-22 → 24)

| Scenario | Result |
|---|---|
| Clean run (3 events × 2 consumers) | 6 ATTEMPTED → Writer flipped 6 DELIVERED; deliveries hand-verified on both targets |
| Induced delivery failure (broken queue policy) | Immediate dead-letter; FAILED rows with `failure_origin=EVENTBRIDGE`, real error code; split verdict `pmc PARTIAL_DELIVERY 0/2, pdz DELIVERED 2/2`; FAILED rows immune to the flip |
| Audit race + recovery | Late ledger rows parked 4 audit records in the audit DLQ; console redrive recovered all 4 |
| Failure-evidence precedence | An event failed under a broken policy stayed FAILED through the flip while its other-consumer sibling delivered |
| Verification | Every Writer run reported `NOT_CROSS_CHECKED` — no independent witness exists; a suspected silent Kinesis loss was disproven only by a full manual per-shard reconciliation of all 9 deliveries, itself demonstrating the observability gap |

### POC2 — local suite + live (dev, 2026-09-24)

Local suite (moto driving the **real** handler modules, 16/16): clean run
with ack-to-stream reconciliation; broken-target strikes → FAILED; duplicate
redelivery deduped by conditional put; ledger-less degrade-write with sparse
lineage; SUSPENDED subscription parks in the buffer and delivers on resume.

| Live scenario | Result |
|---|---|
| T1 — clean run (`fr-live-1`, 10 events) | **20 rows born DELIVERED in ~30 s**, each with `confirmed_by=TARGET_API_RESPONSE` and the target's acknowledgment (SQS MessageId / Kinesis SequenceNumber); one console click end to end |
| T2 — induced failure (`fr-live-2`, pdz target nonexistent) | 2 pmc rows born DELIVERED within seconds; after 5 strikes (~6-min intervals, live `ResourceNotFoundException` per attempt in the fabric logs) 2 pdz rows recorded `FAILED (DELIVERY_RETRIES_EXHAUSTED)` — a 2+2 split verdict, every outcome observed |

## 6. Decision — PENDING (reopened 2026-09-30)

> **This decision is under revision and must not be treated as settled.** It
> was taken from an options set that did not include option 6, for the reason
> §0 records. Both POC1 and POC2 have since been destroyed and replaced by a
> single POC implementing option 6, so the recommendation below currently has
> no running implementation behind it.
>
> The comparison that decides it is narrow. Option 5 buys **stronger
> evidence** — the outcome row is written by the same Lambda execution that
> received the target's acknowledgment, so a missing row means the delivery
> did not happen. Option 6 buys a **far smaller change** — EventBridge keeps
> delivering, consumers are untouched, there is no buffer and nothing in the
> hot path, and API destinations come for free — at the cost of a best-effort
> signal, so a missing row is ambiguous.
>
> So the question is not which is better in the abstract but what evidence
> standard Table 4 has to meet. If a `DELIVERED` row is a record of fact that
> someone may rely on in a dispute, option 5's guarantee is worth its
> intrusiveness. If it is operational telemetry, option 6's cost is hard to
> justify against. That call is the owner's and has not been made.
>
> **What has changed since this was first suspended.** The `target_input`
> question is answered — it is real, confirmed live, so option 6 needs no
> schema change and no processor change. The best-effort objection is weaker
> than it was: a scheduled poll can re-read any window inside log retention,
> so everything lost downstream of the log group is recoverable and only a
> record that was never written stays ambiguous.
>
> **Two new facts cut the other way.** Option 6 is not zero-touch: it needs
> two corrections to `dlq_recorder.py`, one of which (the insert-only
> condition) would otherwise lose failures outright. And the `include_detail
> = FULL` ingestion cost — of the order of 33 GB per 1M-event run per bus,
> across four domain buses now — is **unmeasured**.
>
> **Resolve when that cost is measured.** It is the last input the comparison
> needs; feasibility is no longer in question on either side.

The original decision, retained for the record:

Adopt the **Delivery Fabric with a tiered shared buffer** (option 5).
Subscription rules deliver only to platform-owned buffers; the fabric Lambda
performs the last hop and writes outcome rows from the target API's
response. One shared buffer by default; a subscription graduates to a
dedicated buffer (own ESM, own concurrency cap, own retry policy) by
re-targeting its rule — no code change, mirroring the platform's sizing-tier
and hybrid-engine patterns.

## 7. Consequences

**Retired:** the audit path (queue, consumer, `ATTEMPTED`,
`pending_scope`/`gsi_pending`), the Delivery Writer (flip, gap-fill,
cross-check), the retry-window wait, per-customer EventBridge delivery DLQs,
and the `failure_origin` ambiguity. Table 4 statuses reduce to
`DELIVERED | FAILED`; the table becomes single-writer. Most of POC1's race
scenarios become unconstructible rather than handled.

**Assumed:** delivery throughput engineering (ESM concurrency, batching,
per-tier caps); at-least-once duplicates on crash-between-send-and-write —
consumer-side dedup on the event id becomes load-bearing (already the
platform contract); the platform holds scoped write access to customer
targets; EventBridge demotes from delivery engine to router.

**Improved as side effects:** suspension parks events in the buffer instead
of silently losing them; retry cadence is inspectable and tunable
(visibility timeout × `maxReceiveCount`, per tier); per-customer ordering
becomes possible (FIFO buffer with `MessageGroupId = subscription_id`);
first delivery and redrive unify onto the same direct-delivery mechanism the
redrive Lambda already uses.

## 8. Open questions / follow-ups

1. **API destination targets** — HTTP delivery from the fabric (auth via
   connection secrets) is designed-for but unimplemented.
2. **FIFO tier** — offer ordered delivery as a subscription option, with its
   throughput and poison-message trade-offs made explicit to the customer.
3. **Retry shape** — fixed visibility spacing today; per-message exponential
   backoff via `ChangeMessageVisibility` if fixed spacing proves too blunt.
4. **Migration** — per domain: provision buffer + fabric alongside the
   existing design, re-target rules one subscription at a time (identity is
   rule-ARN-based, so coexistence is clean), retire audit/writer components
   once all rules are moved.
5. **ADR-0007 Table 4 amendment and UIFPE-453 AC rewrite** — statuses reduce
   to `DELIVERED | FAILED`; ACs 2–6 rewrite around the fabric design.
