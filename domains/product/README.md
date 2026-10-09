# Domain: product

A deployable, ownable business unit of UIF. Standards for each capability live
in `../../capabilities/<capability>/`; see `CLAUDE.md` for working context and
`DOMAIN-CHECKLIST.md` for the definition of done.

> **Interim state, per [ADR-0009](../../architecture-design/decisions/0009-gold-placement.md).**
> This domain currently hosts Gold's control-plane tooling and runtime under
> the steward model (`../../capabilities/data/gold-control-plane.md`,
> UIFPE-122) — code that `program` (and soon `price`/`stock`) already invoke
> as a shared platform, not this domain's private logic. ADR-0009 names
> `stacks/gold/` as the target home; the physical move is separate, gated
> follow-up work, not yet scheduled. Don't use this domain's `src/` shape as
> a template for a schema-owning domain — it isn't one.

## What this domain owns

**Silver → Gold materialization for the Product entity** — the Gold layer of
the UIF data lake. Business entities are materialized from Silver Iceberg
tables into `gold_product.product` by config-driven Glue jobs, with
channel-aware change detection and an append-only `product_changes` log.

Migrated from the standalone `uif-dal-gold` repo at commit `cba9af0`
(2026-07-31). Bronze/Silver ingestion, the pipeline API, the scheduler, and
the UI remain in the platform repo (`uif-relay-echo`) and are **read-only,
another team's scope**. Per ADR-0004 there is no runtime dependency on their
code or API — the only touchpoint is reading Silver Iceberg via the Glue
catalog. Shared primitives reused by reference: the standard estate's
`uif-echo-{env}-glue-role` execution role and its `uif-echo-{env}-data` bucket (our objects live under
the `gold/` prefix).

```
Silver (Iceberg, owned by platform repo)
   │  per-source watermark scan → affected entity keys (manifest key_mapping)
   ▼
generic_gold_transform.py  ──►  gold_product.product           (Iceberg)
   │   full-entity rebuild,     + _channel_hashes / _gold_updated_at
   │   hash-gated MERGE         gold_product.product_changes   (append-only)
   ▼
generic_gold_reconcile.py  — nightly hard-delete sync (+ DELETE log rows)
```

## Architecture

- [ADR-0001 — Gold Layer](../../architecture-design/decisions/0001-gold-layer.md)
  — the 14 foundational decisions (materialize as Iceberg, SQL+manifest
  mapping, schema registry publishing, tier subsets).
- [ADR-0003 — Gold Incremental CDC](../../architecture-design/decisions/0003-gold-incremental-cdc.md)
  — incremental CDC design (two-phase rebuild, per-channel hashes,
  `{entity}_changes` log, channel/attribute lifecycle). **Read before touching
  the transform.**
- [ADR-0004 — Independent Gold Control Plane](../../architecture-design/decisions/0004-independent-gold-control-plane.md)
  — repo independence, control plane, cutover runbook.
- [LLD — Silver to Gold module](docs/lld-silver-to-gold-module.md) *(provisional
  location — see open decisions below).*
- [Review — can the Transform UI regenerate `product.sql`?](docs/review-transform-ui-sql-reproduction.md)
  — 2026-08-10 findings: four delta-compiler gaps (now `xfail(strict=True)`
  tests in `qe/`), nine `product.sql` review items, and five decisions awaiting
  a business or architecture owner.

## Key components

| Path | What |
|---|---|
| `stacks/echo/src/apps/pipeline/` | the gold runtime — `generic_gold_transform.py`, `generic_gold_reconcile.py`, `gold_cdc.py` — moved to Echo at UIFPE-358 phase 2 (ADR-0018); deployed by `stacks/echo/infrastructure/terraform-gold` since step 2 (2026-09-14) |
| `stacks/echo/src/apps/cli/`, `lambdas/`, `transform-ui/` | the control plane, the gold dispatcher and Transform UI Lambdas, and the Transform UI — Echo's |
| `src/apps/pipelines/` | **Config-as-code**: `base/{gold_id}.yaml` + `overlays/{env}/{gold_id}.yaml` → synced to DynamoDB |
| `stacks/echo/src/apps/environments.json` | The environment picklist — single source of truth for valid envs |
| `src/schemas/gold/product/v1/` | `master.json` — master JSON Schema with `x-channels` tier tags (compiled runtime artifact) |
| `src/schemas/mapping/product/v1/` | Mapping manifest + `sql/product.sql` (compiled runtime artifacts) |
| `src/schemas/mapping/product/v1/product.mapping.json` | The **authored intake** business mapping (62 columns) — not yet fully reconciled with `master.json`; see `src/schemas/gold/product/v1/README.md` |
| `qe/` | Unit tests (no Spark/AWS needed) + `fixtures/` + `requirements-dev.txt` |
| `infrastructure/terraform/` | This domain's Terraform: event delivery (`product_events*.tf`), the marketing-copy service, the product API. The gold runtime's Terraform is `stacks/echo/infrastructure/terraform-gold` (ADR-0018 step 2) |

## Infra ownership

The gold runtime's Terraform is `stacks/echo/infrastructure/terraform-gold`
([ADR-0018](../../architecture-design/decisions/0018-gold-runtime-is-a-stack-concern.md)).
This root's state (`echo-gold/{env}/`) holds event delivery, marketing-copy and
product-API resources only.

## Common workflows

Run from the monorepo root. `py` is the Windows launcher; use `python3` on
Linux/macOS.

| Task | Command |
|------|---------|
| **Install test deps** | `pip install -r domains/product/qe/requirements-dev.txt` |
| **Test** | `py -m pytest domains/product/qe -v` |
| **Sync pipeline configs (plan)** | `py stacks/echo/src/apps/cli/sync_pipelines.py --env dev` |
| **Sync pipeline configs (apply)** | `py stacks/echo/src/apps/cli/sync_pipelines.py --env dev --apply` — the dev deploy already does this on merge; by hand only for an out-of-band change or for uat/prod |
| **Register Gold pipeline (ad-hoc/dev)** | `py stacks/echo/src/apps/cli/register_gold_pipeline.py --gold-id product --domain product --schema-dir domains/product/src/schemas/gold/product/v1 --mapping-dir domains/product/src/schemas/mapping/product/v1 --gold-database gold_product --gold-table product --owner you@pearson.com --env dev` |
| **Trigger Gold transform** | `aws lambda invoke --function-name uif-echo-dev-gold-dispatch --payload $(echo -n '{"frequency":"gold-tick"}' \| base64) /tmp/r.json && jq . /tmp/r.json` |
| **Trigger Gold reconcile** | Same with `{"frequency":"gold-reconcile"}` |
| **Generate tier subsets locally** | `py stacks/echo/src/apps/cli/generate_tier_subsets.py --master domains/product/src/schemas/gold/product/v1/master.json --entity product --version v1 --out-dir /tmp/subsets` |
| **Build mapping artifact** | `py stacks/echo/src/apps/cli/build_mapping_artifact.py --manifest domains/product/src/schemas/mapping/product/v1/manifest.yaml --entity product --version v1 --out /tmp/mapping.json` |
| **Upload Glue scripts (dev)** | `aws s3 cp stacks/echo/src/apps/pipeline/ s3://uif-echo-dev-data/code/gold/ --recursive --exclude "*" --include "*.py"` |
| **Infra (plan)** | `cd domains/product/infrastructure/terraform && make init ENV=dev && make plan ENV=dev` — in dev, `apply` is run FOR you by `deploy-product-dev.yml` on merge to `develop`, unattended. Reviewing this plan before you merge is the only gate; a manual `make apply ENV=dev` races the workflow. UAT/prod remain apply-by-hand, and not before the ADR-0004 cutover. |

## Adding an attribute or channel (config only — ADR-0003)

- **Attribute**: one PR — property in `master.json` (+ `x-channels`),
  `canonical_lineage` entry, `output_columns`, SQL expression. The transform
  auto-DDLs the column and backfills all rows on its next run
  (`backfill_mode: silent|emit`).
- **Channel**: tag properties with the new channel in `master.json`. The
  transform backfills its hash for every row silently and seeds its publish
  cursor.

## Provisional placements & open decisions

This migration deliberately leaves several things unsettled. Nothing here is a
final UIF placement.

- **Non-containerized workload vs. the one-container rule.** The domain rule is
  one container image from `src/Dockerfile`. This workload is AWS Glue jobs +
  a Lambda — neither is containerized, and `src/Dockerfile` does not build or
  ship them. **An ADR is needed** to reconcile serverless data-plane workloads
  with the one-container build/deploy contract.
- **Pipelines YAML, mapping SQL, and operator CLI homes are provisional.**
  `src/apps/pipelines/` and `src/schemas/mapping/product/v1/sql/` were placed to keep the code's existing relative-path
  resolution working unchanged, not because the framework has ruled on where
  config-as-code, SQL, or operator tooling belong.
- **Packaging deferred.** `pyproject.toml` was NOT migrated — domain-root
  packaging files are blocked on a framework decision. Pytest works via
  `qe/conftest.py` alone. `qe/requirements-dev.txt` is likewise a provisional
  home.
- **The LLD sits in `docs/`**, which is outside the contractual 3-file domain
  root (`README.md`, `CLAUDE.md`, `DOMAIN-CHECKLIST.md`). Provisional.
- **Deploy workflows are ported but latched off.** The source repo's five
  GitHub Actions workflows now live at the monorepo root
  (`.github/workflows/product-ci.yml`, `product-gold-schemas.yml`,
  `deploy-product-{dev,uat,prod}.yml`), scoped to this domain via
  `domains/product/**` path filters. Deploys are gated by the
  `PRODUCT_DEPLOY_ENABLED` repository variable. **Dev is live** (2026-08-11):
  the variable is set, `AWS_ROLE_ARN_DEV` is configured, the role's OIDC trust
  policy carries this repo's **ID-QUALIFIED** enterprise subjects (Pearson
  GHEC issues `repo:pearson-dnt-coe@183024320/universal-Integration-framework@1308482856:environment:dev`
  — the standard unqualified form never matches), the ADR-0004 §CP-5 cutover
  completed in dev on 2026-08-04, and dev deploys run green on merge to
  `develop`. **UAT and prod remain latched** and need: their
  `AWS_ROLE_ARN_{UAT,PROD}` roles trusted the same ID-qualified way and the
  §CP-5 cutover run for those envs. Two blockers cleared for **uat** on
  2026-09-28 — a real Terraform backend and an `overlays/uat` set, so
  `sync_pipelines.py --env uat` validates instead of failing. **prod** still
  has a placeholder backend and no overlay. CD for the
  rest of UIF runs through Jenkins (`infrastructure/Jenkinsfile`); the
  Jenkins path for this Glue/Lambda workload remains undefined and is
  unaffected by this latch.
- **Terraform naming/state exemption pending ratification.** Resource names,
  tags, and the state key (`echo-{env}-*`, `echo-gold/{env}`) are inherited
  verbatim from `uif-dal-gold` rather than aligned to UIF conventions. This is
  deliberate — do not "fix" it before the exemption is ratified.

