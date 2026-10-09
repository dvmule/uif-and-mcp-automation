# UIF eventing POC2 — Delivery Fabric (observed outcomes) — UIFPE-453

Option-B prototype: the platform owns the last delivery hop, so every outcome
is **observed from the target API's response**, never inferred. Successor
experiment to `uifpe-453-delivery-outcome-tracking` (POC1), whose live testing
established that per-target delivery verification is impossible with native
EventBridge signals (see UIFPE-453 comment of 2026-09-24).

Everything is namespaced `uif-event-poc2-*` — fully isolated from POC1 and the
live stack. Region `eu-west-1`.

## Architecture

```
test-publisher Lambda  {"gold_run_id":"fr-1","events":10}   (console Test tab)
  ├─► PutEvents ──► uif-event-poc2-product-bus
  └─► ledger rows ──► uif-event-poc2-publish-metrics

bus rules (…-pmc / …-pdz), ONE target each: the SHARED buffer,
input transformer stamps {"rule_arn":"<aws.events.rule-arn>","event":<event>}
        ▼
uif-event-poc2-delivery-buffer (+ -dlq)     ← retries live here (maxReceiveCount 5)
        │  ESM: batch 10 / 5s / ReportBatchItemFailures / maximum_concurrency 5
        ▼
delivery-fabric Lambda
  ├─ rule_arn ──► subscription-configs (gsi_rule_arn) ──► customer + target
  ├─ SQS: send_message ──► uif-event-poc2-pmc-…   (ack: MessageId)
  ├─ KINESIS: put_record ──► uif-event-poc2-pdz-… (ack: SequenceNumber)
  └─ outcome row BORN DELIVERED (confirmed_by=TARGET_API_RESPONSE, ack attached)
        │
        └─ delivery exception ──► batchItemFailures ──► buffer redelivers ──►
           after 5 strikes ──► buffer-dlq ──► failed-writer ──► FAILED row

uif-event-poc2-delivery-metrics: single-writer outcome table.
No ATTEMPTED, no flip, no delivery-writer, no retry-window wait, no audit path.
```

## Deploy

Terraform >= 1.10. State: shared bucket, `delivery-poc2/` prefix.

```
terraform init -backend-config=backends/dev.s3.tfbackend
terraform plan
terraform apply
```

## Test — entirely from the console

**T1 — clean run.** Lambda → `uif-event-poc2-test-publisher` → Test:
`{"gold_run_id": "fr-1", "events": 10}`. Within ~10–30 seconds query
`uif-event-poc2-delivery-metrics` (`gsi_gold_run = fr-1`): **20 rows, all
DELIVERED**, each carrying `confirmed_by=TARGET_API_RESPONSE` and the real
`target_ack` (SQS MessageId / Kinesis SequenceNumber). No writer to run, no
window to wait. Cross-check the acks against the pmc queue (10 messages) and
pdz stream (`IncomingRecords` 10 — or match SequenceNumbers in the Data
Viewer: the rows tell you exactly which shard positions to expect).

**T2 — induced failure.** Edit the pmc row in
`uif-event-poc2-subscription-configs`: change `target.target_arn` to a
nonexistent queue name. Publish `{"gold_run_id":"fr-2","events":2}`. The
fabric raises per attempt (see its logs for the live SDK exception); the
buffer redelivers ~every 6 min; after 5 strikes (~25–30 min) the messages
land on the buffer DLQ and the failed-writer records **FAILED rows** with
`reason=DELIVERY_RETRIES_EXHAUSTED` and the receive count — while pdz's rows
were born DELIVERED within seconds. Restore the target_arn afterwards.

**T3 — duplicate-send semantics.** Rerun T1's payload with the same
gold_run_id: ledger rows are kept (`skipped_existing`), the new bus events are
delivered again (consumers dedupe on event id — platform contract), and the
outcome writes bounce off the conditional put (`outcome: duplicate` in logs).

## Deviations / known POC simplifications

- SQS + Kinesis targets only; API destinations out of scope.
- A non-ACTIVE subscription row makes the fabric retry -> eventually a FAILED
  row. The tiered design's real suspension answer (dedicated buffer, disabled
  ESM, events park) is not modelled with the single shared buffer.
- The failed-writer records that retries were exhausted; the per-attempt SDK
  exception text lives in the fabric's logs, not on the row.
- `.terraform.lock.hcl` not committed — generate with your own `terraform
  init` and commit it.
- Terraform not yet validated on the authoring machine — run
  `init/validate/plan` before first apply.

## Local verification (no AWS needed)

`../local-simulation/run_poc2_local.py` stands up the whole stack in moto and
drives the REAL `lambda_src/` handlers through 5 scenarios / 16 checks (clean
run with ack-to-stream reconciliation, broken-target strikes -> FAILED,
duplicate redelivery dedupe, ledger-less degrade-write, suspend/park/resume).
Run it before any handler change:

```
pip install boto3 moto jsonpath_ng
python local-simulation/run_poc2_local.py
```

It caught a real bug pre-deploy (NULL into the gsi_gold_run key attribute on
the degrade-write path — would have retried forever in AWS).

## Teardown

```
terraform destroy
```
