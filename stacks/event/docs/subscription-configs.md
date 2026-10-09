# uif-event-{env}-subscription-configs — subscription contract

**One row per subscription: customer × detail-type (operation + version) ×
target.** This is the single source of truth for what a customer is
subscribed to, at what version, and where events are delivered. A customer
subscribed to three detail-types has three rows, each with its own
EventBridge rule and its own target. Provisioned in
[`../infrastructure/terraform/dynamodb.tf`](../infrastructure/terraform/dynamodb.tf)
(`aws_dynamodb_table.subscription_configs`).

**Fully table-driven (2026-09-17): `clients.json` and `subscriptions.json`
are both removed**, along with the rule generator and the generated channel
rules. The processor performs **emit validation** against the DEPLOYED
delivery state, not this table (2026-09-24): an operation is emitted only
when the domain bus carries a rule matching `{domain}.{op}.v1` **with at
least one target** (a DISABLED rule still counts - the SUSPENDED-emits
semantics). Table rows alone do not gate emission any more - a seeded
ACTIVE row whose rule was never provisioned no longer claims
deliverability. When the bus carries NO matching rules, the processor still
**bootstraps** the default `pmc`/`platinum` `product.created.v1` rows here
(conditional inserts, never overwrites) and re-checks the bus once; the
provisioner materialises the rows into rules asynchronously, so the
bootstrap run itself counts `no_subscribers` and the next run emits. Delivery rules are created by the
**provisioner** (`uif-event-{env}-subscription-provisioner`,
`src/subscription_provisioner.py`, built 2026-09-17): the table's DynamoDB
Stream drives it — `PENDING` rows are provisioned (managed queue + DLQ,
policy Sids, rule + target, real ARNs written back, row → `ACTIVE`),
`DELETING` rows torn down to `DELETED` tombstones, `SUSPENDED`/resume
toggles the rule — and a manual `{}` invoke runs the crash-recovery sweep
over `PENDING`/`DELETING`. Two implementation notes: the `{policy_sid}r`
redrive statement is written only once `REDRIVE_ROLE_ARN` is configured (a
nonexistent principal would fail the policy write), and an unresolvable
`consumer_principal_arn` does NOT fail the row — the policy is written
without the consume Sid and the row goes `ACTIVE` with
`status_reason = CONSUMER_PRINCIPAL_INVALID`, so delivery works while the
grant waits for a real principal. `KINESIS`/`API` targets are
bring-your-own for now (`FAILED` / `TARGET_TYPE_UNSUPPORTED`).

**Which bus a rule lands on (2026-09-25):** a provision resolves the
domain's bus from its **pipeline-configs row**
(`processor.arguments.event_bus_name` — the exact value the shared publish
job emits with), falling back to the `uif-event-{env}-{domain}` naming
convention when the row is missing or unreadable. Rules therefore always
land where the domain's events actually flow — including a domain riding
another domain's bus while its own is pending (price on the product bus,
UIFPE-401) — and the delivery `DeadLetterConfig` follows the bus's OWNER
(`uif-event-{env}-{owner}-dlq`, the queue that bus's recorder consumes; the
recorder derives the true domain from each envelope, so lineage stays
correct). Teardown and suspend/resume use the bus parsed from the row's
**stored `rule_arn`**, never a fresh resolution, so flipping a domain's
configured bus strands nothing; after a flip, move a subscription by
re-provisioning it (`DELETING` → re-seed `PENDING`).

## Keys

| Key | Shape | Notes |
|---|---|---|
| `customer_id` (hash) | `{customer_id}` | The consumer organisation's code, e.g. `pmc` — the partition key holds it directly, so no separate flat copy is stored. |
| `domain_detail_type` (range) | `{domain}#{detail_type}` | e.g. `product#product.created.v1` — one subscription per detail-type per customer; `begins_with` filters to one domain or one operation. |

## Attributes

| Attribute | Type | Notes |
|---|---|---|
| `subscription_id` | String (UUID) | Stable id for the subscription; used on unsubscribe and in audit. |
| `policy_sid` | String | `uif` + UUID hex without hyphens — the Sid prefix for the statements added to the target's resource policy: `{policy_sid}` (EventBridge delivery, conditioned on `rule_arn`), `{policy_sid}r` (direct SendMessage for the redrive Lambda) and `{policy_sid}c` (consume actions for `consumer_principal_arn`). Hyphens are not accepted in all Sid contexts. |
| `domain` | String | e.g. `product`. |
| `operation` | String (enum) | `created` \| `updated` \| `deleted`. |
| `version` | String | Family major this subscription is bound to (`v1`, `v2`, …) — the `.vN` sibling artifact, matching the DetailType suffix. Never the inner Apicurio minor. |
| `detail_type` | String | `{domain}.{operation}.{version}` — the rule's detail-type filter. The rule also matches `detail.channel` for the subscription's channel (`target_type` SQS→`event`, KINESIS→`message`, API→`api`), so a subscriber receives only its own channel's projection of each record. |
| `rule_name` | String | `uif-event-{env}-on-{domain}-{detail_type, dots→dashes}-{customer_id}` — must be ≤ 64 chars. |
| `rule_arn` | String | `arn:aws:events:{region}:{account}:rule/uif-event-{env}-{domain}-bus/{rule_name}`. **GSI key** — the DLQ Lambda resolves `RULE_ARN` → this row. `null` while `PENDING` (written at activation). |
| `target` | Map | `target_type`, `target_arn`, `target_config` — see below. |
| `target_arn` | String | Flat duplicate of `target.target_arn` — required because a GSI key must be a top-level scalar, so the nested value cannot be indexed. **Always written in the same UpdateItem** as `target.target_arn`: if the two diverge, `gsi_target_arn` resolves a DLQ message to a stale target and redrive delivers to the wrong queue with no error. **GSI key** — fallback when a DLQ message lacks `RULE_ARN`. |
| `status` | String (enum) | `PENDING` \| `ACTIVE` \| `SUSPENDED` \| `FAILED` \| `DELETING` \| `DELETED`. `PENDING` is written **before any AWS mutation** — a crash mid-subscribe leaves a visible, resumable intent, never an invisible live rule. `SUSPENDED` = `DisableRule` (rule/target intact; `EnableRule` is the rollback). `DELETING` is the teardown intent (UI treats it as gone); `DELETED` rows stay as tombstones. |
| `status_reason` | String | Why `FAILED` / `SUSPENDED` (e.g. `RULE_QUOTA`, `NAME_TOO_LONG`, `POLICY_WRITE_FAILED`, `CUSTOMER_REQUEST`). `null` otherwise. |
| `record_version` | Number | Optimistic locking — every edit uses `ConditionExpression record_version = :expected`. |
| `created_at` / `created_by` | String | Set on subscribe. |
| `updated_at` / `updated_by` | String | Bumped on every edit (target change, suspend/resume). |
| `deleted_at` / `deleted_by` | String | Set on unsubscribe. |

## `target` map by `target_type`

| `target_type` | `target_arn` | `target_config` |
|---|---|---|
| `SQS` | Queue ARN | `consumer_principal_arn` (**required** — the customer's IAM role or account root that will consume the queue), `dlq_arn`, `retry_policy { max_attempts, max_age_seconds }`, `message_group_id` (FIFO only, else `null`) |
| `KINESIS` | Stream ARN (managed: `uif-event-{env}-{customer}-{domain}-{detail_type, dots→dashes}`, ON_DEMAND, created by the provisioner — 2026-09-24) | `partition_key_path` (default `$.detail.partitionkey` — evaluated on the pre-transform event, so it keys shards by `{businessKey}\|{org}`), `retry_policy`, `consumer_principal_arn` (granted read-side actions via the stream's resource policy, `{sid}c`; invalid principal = the CONSUMER_PRINCIPAL_INVALID deviation). Delivery uses the ONE stack-owned `events-to-kinesis` role, never per-subscription roles. |
| `API` | API-destination ARN | `dlq_arn`, `retry_policy`, `role_arn`, `connection_arn` (holds auth), `http_parameters { path, query, header }` |

## Indexes

| Index | Key | Used by |
|---|---|---|
| Base | `customer_id` + `domain_detail_type` | Customer UI / API: all subscriptions for a customer; `begins_with(domain_detail_type, "product#")` for one domain; redrive dispatch (`GetItem` by both keys). |
| `gsi_rule_arn` | `rule_arn` | DLQ Lambda: `RULE_ARN` → `customer_id`, `target`, `detail_type`. |
| `gsi_target_arn` | `target_arn` | DLQ Lambda fallback when `RULE_ARN` is absent. |
| `gsi_detail_type` | `domain_detail_type` + `customer_id` | Ops: "who is subscribed to `product.created.v1`" — deprecation notices, redrive fan-out checks. |

## Naming (env `dev`, customer `pmc`, detail-type `product.created.v1`)

| Resource | Name |
|---|---|
| Rule | `uif-event-dev-on-product-product-created-v1-pmc` |
| Subscription queue | `uif-event-dev-pmc-product-product-created-v1` |
| Subscription DLQ (SQS targets) | `uif-event-dev-pmc-product-product-created-v1-dlq` |
| Subscription stream (KINESIS targets) | `uif-event-dev-pdz-product-product-updated-v1` |

## Status and write discipline

- Every edit is optimistic-locked on `record_version` and bumps
  `updated_at`/`updated_by`.
- Redrive dispatch requires `status ∈ {ACTIVE, SUSPENDED}` — anything else
  fails the redrive with `reason = SUBSCRIPTION_NOT_ACTIVE`
  (see [delivery-metrics.md](delivery-metrics.md)). Redrive reads the
  **current** target from this table, never delivery-metrics' `target_arn`
  ("what rejected the event" vs "where to send it now").
- Redrive needs its own grant on every target (it sends directly,
  bypassing EventBridge). Managed queues get the redrive-role statement at
  subscribe time; bring-your-own targets must add it themselves, else
  `target_config.redrive_enabled = false` and the UI offers no redrive.
- Unsubscribe: `DELETING` intent → `RemoveTargets` → `DeleteRule` → remove
  policy statements whose Sid starts with `policy_sid` → tag queue + DLQ
  `delete_after` (+7d grace, customer drains) → `DELETED` tombstone.
  Re-subscribing overwrites the row with a new `subscription_id`; history
  lives in the table stream / audit log.

## Subscription lifecycle

- **Subscribe** (managed SQS): write the `PENDING` row (nothing in AWS
  yet; pre-flight — rule quota, name length, consumer-principal account
  check — fails the row `FAILED` with a `status_reason` before touching
  AWS) → `CreateQueue` DLQ → `CreateQueue` (RedrivePolicy → DLQ) →
  `SetQueueAttributes` policy += three Sids: `{policy_sid}` (EventBridge
  `sqs:SendMessage`, conditioned on `rule_arn`), `{policy_sid}r` (redrive
  Lambda `sqs:SendMessage`), `{policy_sid}c` (consumer principal:
  Receive/Delete/ChangeVisibility/GetQueueAttributes/GetQueueUrl — never
  SendMessage) → `PutRule` (detail-type + `detail.channel` filter) + `PutTargets`
  (Id = `subscription_id`, `InputPath = $.detail` — the consumer receives
  ONLY the CloudEvents envelope, never EventBridge's wrapper; dedupe on the
  envelope `id`; exhausted deliveries dead-letter to the DOMAIN DLQ, the recorder's queue — the per-subscription DLQ is only the queue's own RedrivePolicy target — plus retry policy) → row `ACTIVE` with
  `rule_arn` + `record_version` bumped. Each step idempotent; resume from
  `PENDING` on crash. Queues are managed (created in the eventing account,
  names from the convention); the customer consumes cross-account via
  `consumer_principal_arn` (their own IAM policy must allow the consume
  actions — onboarding contract). SQS-managed SSE, so no KMS grant.
- **One rule per subscription**, suffixed `{customer_id}` — rule names are
  unique per bus, so without the suffix a second subscriber's `PutRule`
  would overwrite the first's (N × M model; request the per-bus rule quota
  increase — default 300 — before onboarding).
- **Version migration = new row + old row DELETED**, in that order (new
  `v2` row `PENDING → ACTIVE`, then `v1` `SUSPENDED`
  (`status_reason = VERSION_MIGRATION`, `DisableRule` — redrive of
  outstanding v1 failures still works), then retire: pre-condition **no
  FAILED delivery-metrics rows** for that customer + detail-type (redrive
  or abandon them first) → `DELETING` teardown → `DELETED`). Brief overlap
  is safe: the processor emits every live major, so during overlap one
  Gold change produces one event per version, each matching its own rule.
  Deprecating a version on the PUBLISH side is separate: only when
  `gsi_detail_type` shows no `ACTIVE`/`SUSPENDED` rows for it across ALL
  customers can it go `DISABLED` in Apicurio.
- **Unsubscribe pre-check**: outstanding `FAILED` delivery-metrics rows for
  the customer + detail-type must be redriven or `ABANDONED`
  (`abandon_reason = UNSUBSCRIBED`) first.

## Why both `subscription_id` and `policy_sid`

One identity in two encodings. `subscription_id` (UUID) is how systems
refer to the subscription — the row, the `PutTargets` target Id, API
responses, unsubscribe. `policy_sid` is the same UUID with hyphens stripped
plus a `uif` prefix, used in exactly one place: the Sids of the statements
added to the target's resource policy (Sids must be alphanumeric). On
unsubscribe or target change, only statements whose Sid starts with
`policy_sid` are removed — the customer's own statements and other
subscriptions' grants on the same queue stay untouched.

## Seeds and migration state (2026-09-03)

Seeding is now runtime bootstrap-if-empty (processor, conditional puts):
the default `pmc` and `platinum` `product.created.v1` rows, inserted
verbatim as supplied — note both share a `subscription_id`/`policy_sid`
and reference example-account targets; real onboarding replaces them. Placeholders to resolve before a row can go
`ACTIVE`: `consumer_principal_arn` is seeded as the account root (must
become each customer's real consumer role), `dlq_arn` is `null` (the
per-subscription DLQs don't exist yet), and the seeded `rule_arn` names the
**actual deployed domain bus** (currently `uif-event-{env}-product`; the
spec's convention says `uif-event-{env}-{domain}-bus` — the bus rename is
an open decision) with per-subscription rules that the provisioner will
create. Not carried over from clients.json (one target per detail-type
now): ELL's product stream + API channels and HED's product stream — those
need their own subscription decisions with the customers.
