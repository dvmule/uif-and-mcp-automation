# Event stack — the shared product-change eventing platform

The domain-agnostic half of UIFPE-184: **one** pipeline controller, **one**
publish Glue job, **one** subscription provisioner, **one** DLQ recorder,
**one** redrive Lambda, **one** recovery Glue job and the six shared DynamoDB tables, serving every domain (product, price,
program, …). Split out of `domains/product` on 2026-09-21 (migration
runbook: [MIGRATION.md](MIGRATION.md)).

## The invariant

**Onboarding a new domain changes NOTHING in this stack.** Everything
per-domain is data or a naming convention:

| Domain fact | Where it lives |
|---|---|
| Gold database / delta table / sizing / skip rules | the domain's row in `uif-event-{env}-pipeline-configs` (`glue.database`, `delta`, `glue.sizing_tiers`, `skip_run_when`, `eventing.athena_output`) |
| Publish-job arguments (schema name, mapping key, event type, business keys, bus, …) | same row, `processor.arguments` — passed through per run by the controller |
| Event bus, delivery DLQ (+ alarm), DLQ-recorder hook, mapping/seed uploads | the domain's instance of [`modules/event-domain`](infrastructure/terraform/modules/event-domain), applied in the DOMAIN's state |
| Bootstrap subscription seeds | domain-owned JSON in S3 (`event-pipeline/domains/{domain}/…`), inserted by the publish job only when the table has no rows for the domain |
| Which domains the GoldMergeCompleted trigger serves | the config table itself — the rule has no domain filter; an unseeded domain skips cleanly in the controller |

IAM here is wildcarded on the conventions (`uif_echo_{env}_gold_*` catalogs,
`uif-event-{env}-*` buses/queues/rules, the `event-pipeline/` S3 prefix), so
a new domain's resources are covered the day its module instance creates
them.

## Onboarding a domain (all of it, none of it here)

In `domains/{d}/infrastructure/terraform`:

1. Instantiate `modules/event-domain` (domain, mapping path, optional seed
   path) — gives the bus `uif-event-{env}-{d}`, the DLQ + recorder hook, and
   the uploads.
2. Seed the domain's `pipeline-configs` row (copy product's shape in
   `domains/product/infrastructure/terraform/product_events.tf`).
3. Ship the domain's gold master schema + attribute mapping as usual.

Then a `GoldMergeCompleted` for that domain flows end to end. Grant the
one-time Lake Formation permissions to the two shared roles
(`…-pipeline-controller-role`, `…-publish-role`) on the new gold database.

## Layout

- `src/` — controller (`handler.py`), publish job (`glue/event_fabric_processor.py`),
  `glue/event_fabric_recovery.py`,
  `subscription_provisioner.py`, `dlq_recorder.py`, `event_redrive.py`;
  unit tests in `qe/`
  (run: `./qe/ci.sh`).
- `docs/` — the four table contracts (pipeline/publish/delivery metrics,
  subscription-configs).
- Channel data schemas are NOT generated here. One generator derives every
  tier from a domain's gold master: `stacks/echo/src/apps/cli/generate_tier_subsets.py`
  (UIFPE-523 retired this stack's duplicate copy of it).
- `infrastructure/terraform/` — flat files per component; per-env tfvars in
  `envs/`, partial backends in `backends/` (state key `uif-event/{env}/…`).
