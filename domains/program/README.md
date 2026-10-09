# Domain: program

The **Program** entity as a gold data product. A program is a Pearson product
family — a PDH item whose `SEGMENT1` starts `P2` — and this domain materializes
one gold row per program per organization from the Silver layer, so consumers
can read a program's descriptions, catalog placement, owner, cover image, the
products associated with it, and its relationships to other programs, without
joining Oracle themselves.

The eighth UIF domain, added 2026-08-11. It exists because programs turned out
to carry attributes of their own rather than being a single reference field on
Product — the test [ADR-0005](../../architecture-design/decisions/0005-gold-layer-shape.md)
set for splitting an entity out. `gold_product.product` keeps `associatedPrograms` as
the pointer from a product to its program; everything about the program itself
lives here.

**Target table:** `gold_program.program` (`uif_echo_{env}_gold_program.program`)
**Grain:** one row per `(inventoryItemId, organizationId)`
**Delivery:** [UIFPE-116](https://pearson.atlassian.net/browse/UIFPE-116)

Read `CLAUDE.md` for the engineering context — invariants, gotchas, and the
open decisions. `DOMAIN-CHECKLIST.md` is the definition of done.

## Component map

| Path | What it is | State |
|---|---|---|
| `src/schemas/gold/program/v1/master.json` | The **primary** artifact — the gold table's structure: 12 properties, types, consumer tiers (`x-channels`), array shapes, and its own key (`metadata.primaryKey`) | ready |
| `src/schemas/mapping/program/v1/program.mapping.json` | The authored intake — **mapping rules only**: driving table, entity key, transform registry, and how each of the 12 columns is populated | ready |
| `src/schemas/mapping/program/v1/manifest.yaml` | Compiled runtime artifact: sources + `key_mapping`, `sot_filter`, lineage, output column order, array sort keys, Spark types | [UIFPE-120](https://pearson.atlassian.net/browse/UIFPE-120) |
| `src/schemas/mapping/program/v1/sql/program.sql` | Compiled runtime artifact: the Silver → Gold query | [UIFPE-120](https://pearson.atlassian.net/browse/UIFPE-120) |
| `src/apps/pipelines/` | Config-as-code: what the Glue transform runs for `gold_id: program` | [UIFPE-121](https://pearson.atlassian.net/browse/UIFPE-121) |
| `qe/` | Contract gates over the two authored artifacts | ready |
| `infrastructure/` | Stamped stubs. Program reuses the shared gold stack rather than owning one — see the open decisions in `CLAUDE.md` | stubs |
| `ui/`, `ux/` | Stamped stubs — program's mapping is authored through product's Transform UI | stubs |

## The 12 attributes

Two key columns (`inventoryItemId`, `organizationId` — stored, carried by no
consumer feed), then `ppid`, `description`, `longDescription`, `relevanceValue`,
`programFamilyCatalogCode`, `coverImageLargeUrl`, `programOwnerName`, `inventoryItemStatus`, and two
arrays: `associatedProducts` (the products in the program) and `relatedPrograms`
(related program + relationship type).

Sources, all already in `uif_echo_{env}_silver_mdm`: `MTL_SYSTEM_ITEMS_B`
(driving), `MTL_SYSTEM_ITEMS_TL`, `EGO_MTL_SY_ITEMS_EXT_B` (attribute groups
1421 / 1422 / 1423 / 1463), `MTL_RELATED_ITEMS`, `FND_LOOKUP_VALUES_VL`.

## How it builds & deploys

- **CI:** `domain-ci.yml` at the repo root already covers this domain — it
  detects domains from changed paths, so `ci (program)` runs automatically and
  enforces the structural definition of done.
- **Schema validation + deploy:** not yet wired. The existing
  `product-gold-schemas.yml` and `deploy-product-*.yml` workflows are
  path-scoped to `domains/product/**`; program's equivalents are
  [UIFPE-121](https://pearson.atlassian.net/browse/UIFPE-121). Until then
  nothing here deploys, which is safe.
- **Runtime:** the shared, entity-agnostic Glue jobs (`uif-echo-{env}-gold-transform`)
  driven by DynamoDB config keyed on `gold_id`. Program adds a config row; it
  does not add a job.
- `src/Dockerfile` builds nothing — this is a serverless data workload. It
  exists because CI makes it an unconditional gate; see `CLAUDE.md`.

## Two things a newcomer should know before touching the pipeline

1. **One source has no honest change-detection key.** `FND_LOOKUP_VALUES_VL`
   (relationship-type decode) has neither key column, so it is declared with a
   constant org fan-out — any change to it invalidates every program row. That
   is correct, not a shortcut, and product does the same for its org dimension.
2. **`associatedProducts` drifts between refreshes.** The link rows are keyed by
   the *product*, and name the program only by PPID string, so the incremental
   pipeline cannot tell which program to recompute. A nightly targeted refresh
   repairs it and notifies consumers. The real fix is a program-keyed Silver
   view — see `CLAUDE.md`.
