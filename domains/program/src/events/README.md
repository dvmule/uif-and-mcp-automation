# program / events — config-only onboarding (UIFPE-401)

Program rides the shared event stack (`stacks/event`) with **zero Terraform
and zero new AWS resources**: the whole onboarding is configuration,
following the same path as [price](../../../price/src/events/README.md).

## What to apply (dev)

1. **The pipeline-configs row** —
   [`config/program.pipeline-config.dev.json`](config/program.pipeline-config.dev.json)
   is the committed source of truth, applied with a conditional put (never
   overwrites):

   ```bash
   aws dynamodb put-item \
     --table-name uif-event-dev-pipeline-configs \
     --item file://domains/program/src/events/config/program.pipeline-config.dev.json \
     --condition-expression 'attribute_not_exists(pipelineid)'
   ```

   Facts derived from the program gold contract
   ([manifest.yaml](../../schemas/mapping/program/v1/manifest.yaml)):
   database `uif_echo_dev_gold_program`, delta `PROGRAM_CHANGES` keyed on
   the **composite** natural key `inventory_item_id,organization_id` (the
   join key — gold's physical snake_case form of `inventoryItemId`,
   `organizationId`), business key `program_id` (`programId`), routing
   attribute `programId` (the only attribute mapping-enabled on every
   channel), event type `com.pearson.program.program.{operation}.v1`, the
   standard three sizing tiers with their python-shell `max_capacity`
   translations. Confirm the physical column names against the live
   catalog (`aws glue get-table --database-name uif_echo_dev_gold_program
   --name program_changes`) before the first put.

2. **The channel-gating mapping** uploaded to the stack's working prefix
   (the publish role's existing wildcard covers it — no IAM change):

   ```bash
   aws s3 cp domains/program/src/schemas/mapping/program/v1/program.mapping.json \
     s3://uif-echo-dev-gold-artifacts/event-pipeline/domains/program/program.mapping.json
   ```

3. **The published data schemas** — the config's
   `event_schema_key` (`event-pipeline/domains/program/program-{operation}-data.json`)
   makes the publish job strictly project every payload to the operation's
   schema before publish, so gold columns the contract does not declare
   (e.g. the `inventoryItemId`/`organizationId` key columns, carried by no
   channel) never leave:

   ```bash
   for op in created updated; do
     aws s3 cp domains/program/src/schemas/events/program-$op-data.json \
       s3://uif-echo-dev-gold-artifacts/event-pipeline/domains/program/program-$op-data.json
   done
   ```

   Re-upload whenever the schemas change. A missing/unreadable schema logs
   a warning and ships the payload unfiltered — degraded and visible, never
   a dead run.

From this point every program `GoldMergeCompleted` is processed instead of
skipped: config resolved, delta counted, publish job run. With no program
subscriptions yet, emit validation counts every record `no_subscribers`
and **emits nothing** — the correct dormant state.

## Subscriptions: pmc on the SQS channel

pmc receives program events. Config-as-code, same lifecycle as every
subscriber: the committed rows under
[`config/subscriptions/`](config/subscriptions/) are seeded `PENDING`
(conditional put, never overwrites) and the provisioner materialises them —
managed queue + DLQ, policy Sids, rule + target — then flips them `ACTIVE`
with the real ARNs:

```bash
for f in domains/program/src/events/config/subscriptions/pmc.program.*.dev.json; do
  aws dynamodb put-item \
    --table-name uif-event-dev-subscription-configs \
    --item file://$f \
    --condition-expression 'attribute_not_exists(customer_id)'
done
```

The rows deliberately OMIT `rule_arn` (it keys the sparse `gsi_rule_arn`;
a NULL-typed index key fails the whole put — see the price README) and
carry the account root as the interim consumer principal, so same-account
consumption works from day one; swap in pmc's real consumer role when they
supply it.

**Where the rules land**: the provisioner resolves each domain's bus from
its pipeline-configs row (`processor.arguments.event_bus_name` — exactly
where the publish job emits), falling back to the naming convention. So
pmc's program rules live on the interim bus (below) today and need **no
change on flip day**: existing rules keep working from the bus recorded in
their `rule_arn`; re-provisioning after the flip creates them on the program
bus.

## Subscriptions: pdz on the Kinesis channel

pdz receives program events on a managed Kinesis stream — same lifecycle,
same seed command with the `pdz.` prefix. The provisioner creates one
ON_DEMAND stream per detail-type
(`uif-event-dev-pdz-program-program-{operation}-v1`), grants the consumer
principal read-side actions through the stream's resource policy
(`{policy_sid}c`), and puts a rule filtered on the **`message`** channel
(SQS subscribers get `event`) with the stack's shared Kinesis delivery
role. Records are sharded on `$.detail.partitionkey`
(`{programId}|{programId}`, see below).

```bash
for f in domains/program/src/events/config/subscriptions/pdz.program.*.dev.json; do
  aws dynamodb put-item \
    --table-name uif-event-dev-subscription-configs \
    --item file://$f \
    --condition-expression 'attribute_not_exists(customer_id)'
done
```

A subscription is one row per customer per detail-type (the table key is
`customer_id` + `domain_detail_type`, and the rule name ends in the
customer), so one customer cannot take the same event on both SQS and
Kinesis — Kinesis rows go to a different customer. The rows carry the
account root as the interim consumer principal, as pmc's do; if the
provisioner Lambda has no `KINESIS_DELIVERY_ROLE_ARN`, the row goes
`FAILED` / `KINESIS_DELIVERY_ROLE_MISSING` rather than half-provisioning.

## Deliberate interim choices

- **`event_bus_name` = `uif-event-dev-program`** (own bus, flipped
  2026-10-01; was `uif-event-dev-product` at onboarding). The bus, its
  delivery DLQ and the recorder hook are the `event-domain` module instance
  that `stacks/event/infrastructure/terraform/domain-buses.tf` derives for
  every domain committing a `pipeline-config.{env}.json` — program has no
  Terraform root of its own (gold-control-plane §2), so nothing under
  `domains/program` changes for it. Order of operations on flip day: stack
  apply creates the bus → `update-item` the live pipeline-configs row to
  this value → re-provision the program subscriptions (`SUSPENDED` →
  `DELETING` → re-seed `PENDING`) so their rules move off the product bus.
  Rules still on the product bus keep delivering from the bus in their
  `rule_arn` until that re-provision; events emitted to the new bus before
  it match no rule.
- **`partitionkey` = `{programId}|{programId}`** (e.g.
  `P2100245|P2100245`): the routing attribute is `programId`. Program has
  no `isbn13`, and the publish job fails the run if the routing attribute
  is not enabled on every channel of the mapping — `programId` is the only
  attribute that is. It duplicates the business key (the shape price moved
  away from); revisit if program gains a second all-channel attribute.
- **No bootstrap seeds**: program has no default subscribers; pmc's and pdz's rows
  above are explicit config-as-code, and later subscribers arrive the same
  way.

## One-time ops prerequisite (NOT Terraform)

Lake Formation grants on the program gold catalog, exactly as product got:

| Principal | Grant |
|---|---|
| `uif-event-dev-pipeline-controller-role` | `DESCRIBE` on database `uif_echo_dev_gold_program`; `SELECT`/`DESCRIBE` on `program_changes` |
| `uif-event-dev-publish-role` | `DESCRIBE` on the database; `SELECT`/`DESCRIBE` on `program` and `program_changes` |

Until these land, a program trigger fails visibly in the controller
(`Insufficient Lake Formation permission(s): Required Describe on
uif_echo_dev_gold_program`), as price's did.
