---
paths:
  - "**/infra/**/*.tf"
  - "**/infrastructure/**/*.tf"
  - "stacks/*/infrastructure/**/*.tf"
  - "domains/*/infrastructure/**/*.tf"
---
# Eventing and messaging names

Vocabulary, cadence set and length limits live in `00-core.md`.

| Resource | Terraform type | Form | Example |
|---|---|---|---|
| EventBridge bus | `aws_cloudwatch_event_bus` | `uif-{stack}-{env}-[{domain}-]bus` | `uif-event-dev-bus`, `uif-event-dev-product-bus` |
| Rule — schedule | `aws_cloudwatch_event_rule` | `uif-{stack}-{env}-schedule-{layer}-{operation}-{cadence}[-slot-NN]` | `uif-echo-dev-schedule-bronze-ingest-daily-slot-00` |
| Rule — event | `aws_cloudwatch_event_rule` | `uif-{stack}-{env}-on-{source}-{event}-{operation}[-{target}]` | `uif-echo-dev-on-silver-succeeded-replicate-bq` |
| Rule — subscription | `aws_cloudwatch_event_rule` | `uif-{stack}-{env}-on-{domain}-{subdomain}-{operation}-{version}` | `uif-event-dev-on-product-product-created-v1` |
| SQS queue | `aws_sqs_queue` | `uif-{stack}-{env}-{operation}[.{type}]` | `uif-echo-dev-dispatch`, `uif-echo-dev-dispatch.fifo` |
| SQS queue — subscription | `aws_sqs_queue` | `uif-{stack}-{env}-{consumer}-{domain}-{subdomain}-{operation}-{version}[.{type}]` | `uif-event-dev-pmc-product-product-created-v1` |
| SQS dead-letter queue | `aws_sqs_queue` | `{queue-it-backs}-dlq` | `uif-echo-dev-dispatch-dlq` |
| SNS topic | `aws_sns_topic` | `uif-{stack}-{env}-{subject}-alert` | `uif-echo-dev-pipeline-alert` |

## A rule declares its trigger type

`schedule-` sends whoever is debugging to the cron expression; `on-` to the event pattern
and the emitting service. Use exactly one.

❌ `uif-echo-dev-reconcile-slot-0` — no layer, no cadence, no trigger type
❌ `uif-echo-dev-silver-succeeded-bq` — a destination sitting in the operation slot

Token breakdown of `uif-echo-dev-on-silver-succeeded-replicate-bq`: `echo` stack, `dev`
env, `on` trigger, `silver` **source** — the emitting job, not the layer slot —
`succeeded` event, `replicate` operation, `bq` target.

## The account bus stays `default`

An EventBridge bus takes a `{domain}` token only once domains need isolating. The
account's own bus is named `default` by AWS and cannot be renamed; do not try.

## FIFO and dead-letter suffixes

Standard queues are unmarked. FIFO takes AWS's mandatory `.fifo` suffix — it is a
platform requirement, not a UIF token. A dead-letter queue appends `-dlq` to the name of
the queue it backs, so the pairing is readable at a glance:
`uif-event-dev-pmc-product-product-created-v1-dlq`.

## Version tokens are allowed here

`00-core.md` bans version words. Subscription rules and subscription queues are the
exception: their `{version}` token matches the registered event contract's version, so a
`-v1` there is a contract reference, not a phase marker.
