# product / src/events — moved to the shared event stack

The pipeline **code, tests, table contracts and generator moved to
[`stacks/event/`](../../../../stacks/event/README.md)** (UIFPE-184 stack
split, 2026-09-21): the controller, the publish Glue job, the subscription
provisioner, the DLQ recorder and the six shared DynamoDB tables were
domain-agnostic by design and now serve every domain from one place.
Migration runbook: [`stacks/event/MIGRATION.md`](../../../../stacks/event/MIGRATION.md).

What remains product-owned:

- **[`asyncapi.yaml`](asyncapi.yaml)** — the product event contract
  (`com.pearson.product.product.{created,updated}.v1`, per-operation
  DetailTypes with the envelope `channel` discriminator).
- **[`config/product.subscriptions.seed.json`](config/product.subscriptions.seed.json)**
  — the bootstrap subscriptions (pmc/platinum `product.created.v1`),
  uploaded by the domain's `event-domain` module instance and inserted by
  the shared publish job only when `uif-event-{env}-subscription-configs`
  holds no product rows.
- **`scripts/align_terraform.py`** — domain Terraform formatting helper.
- The domain's eventing infrastructure — bus, delivery DLQ, DLQ-recorder
  hook, mapping/seed uploads and the pipeline-configs seed row — lives in
  [`../../infrastructure/terraform/product_events.tf`](../../infrastructure/terraform/product_events.tf)
  (the `event-domain` module instance + the config item).

Schemas stay where they were: the gold master + `x-channels` under
[`../schemas/gold/product/v1/`](../schemas/gold/product/v1/), the
channel-projected data schemas: `event`, `message` and `api` all under
[`../schemas/channels/product/`](../schemas/channels/product/). The OpenAPI
contract stays in the API capability's folder at
[`../api/product/`](../api/product/) and references its projection from there.
That placement is ADR-0022 decision 4, not a local convention. (That contract does
not `$ref` the projection yet — UIFPE-524 makes it, which is why the api tier
needs a stable path.)
All three come from ONE generator (UIFPE-523):

```bash
python3 stacks/echo/src/apps/cli/generate_tier_subsets.py \n  --master domains/product/src/schemas/gold/product/v1/master.json \n  --entity product --version v1 --out-dir /tmp/subsets
```

A committed copy is transitional — the schema registry is where consumers
will read these from.

> `template.yaml`, `samconfig.toml` and `scripts/deploy.ps1` were the legacy
> gps-sandbox SAM path; they reference the moved sources and are **stale**
> pending that account's retirement — Terraform (domain + stack) is the only
> deploy path.
