> **Naming note (2026-08-09, UIFPE-103):** resource names written here as `eds-{env}-*` / `eds_{env}_*` are historical — the platform convention is now `echo-{env}-*` / `echo_{env}_*`, and the environment set is dev/uat/prod (uat replaced staging). Names below are preserved as written at decision time.

# ADR-0005 — Gold Layer Shape: Canonical Entity with Channel Tiers, or `api_*_view` Projections

**Status:** Accepted (2026-08-20) — Option B as recommended; see the [Acceptance record](#acceptance-record-2026-08-20) for the six confirmations and the evidence each rests on
**Date:** 2026-08-05 (proposed) · 2026-08-20 (accepted)
**Author:** reyasahamed.habibrahman@pearson.com
**Deciders:** Rajnivas Karunakaran (reporter, UIFPE-58 / UIFPE-64) and Satyanarayana Gunavanthan (owner, UIFPE-25 — required, because the principle this ADR revisits is stated in his epic)
**Scope:** The shape of the Gold layer and where conformance logic lives. Settles the divergence between UIFPE-58/64 as written and the implementation running in dev, plus the subsidiary conventions that hang off it.
**Supersedes:** none. Builds on ADR-0001 (gold foundations) and ADR-0003 (incremental CDC). Tracked as UIFPE-67.
**Numbering:** 0005. (0002 was never used in the source repo; the gap is inherited.) **Citation note:** cite this document as **ADR-0005**. "ADR-5" already refers to the tier-expression decision *inside* ADR-0001, which is also about tiers — do not conflate them.
**Disclosure:** this ADR recommends amending UIFPE-58 and UIFPE-64, both of which are assigned to its author. Amending them changes the author's own acceptance criteria.

---

## Context

UIFPE-58 and UIFPE-64 specify a Gold layer that is a **projection of a conformed Silver layer**. The implementation running in dev is a **canonical entity table with channel tier subsets**. Both are coherent. They are not the same design, and the gap is load-bearing enough to decide rather than drift into.

| The tickets specify | What is built and running in dev |
|---|---|
| `gold.api_*_view` naming at base / variant / hierarchy granularity (**UIFPE-64, prescriptive**); `api_product_view` / `api_sku_view` / `api_category_tree` (**UIFPE-58, illustrative — "e.g."**) | One wide canonical entity per domain: `eds_{env}_gold_product.product`, 50 attributes, PK `(inventoryItemId, organizationId)` |
| Multiple Gold projections per domain, all derived from one conformed Silver | Channel tier subsets — event ⊂ message ⊂ api (42 / 44 / 48 properties) generated from `x-channels` tags, published as registry artifacts |
| Source: **a conformed Silver layer** (the tickets do not name tables) | Source: raw Oracle PDH — `mtl_system_items_b`, `ego_mtl_sy_items_ext_b`, `mtl_system_items_tl`, and `mtl_parameters` as an org dimension (added 2026-08-06, recommendation item 3) |
| **"Projection, not re-derivation — no business logic in Silver → Gold beyond shaping/denormalise/filter"**, with a guardrail check as an acceptance criterion | 395 lines of mapping SQL across 31 CTEs doing attribute-group resolution, branch selection and proliferation |
| Step Functions orchestration | EventBridge schedules + dispatcher Lambda |
| `relay-gold-{env}` bucket | `eds-{env}-gold` |
| Iceberg with partition conventions | Iceberg format-version 2, **unpartitioned** (per ADR-0003) |

### The crux

The projection-only guardrail presupposes a **conformed** Silver layer. That layer does not exist. Silver here is close to raw Oracle PDH: an entity-attribute-value model where one product's attributes are spread across attribute groups in `EGO_MTL_SY_ITEMS_EXT_B` keyed by `ATTR_GROUP_ID` — 28 distinct groups are read by the Product mapping alone — with different groups supplying the same logical attribute depending on whether the item is a Digital Catalog item.

The conformance work has to happen somewhere. Today it happens in Gold. No naming discipline changes that; only moving the work does.

### Evidence: what the 395 lines actually do

Counts below are **code only** — comment lines excluded, verified by stripping comments before matching. This classification is the evidence the decision turns on, so it should survive an owner re-running it. Counts restated 2026-08-06 after recommendation item 3 landed (previously 384 lines / 30 CTEs / 30 `LEFT JOIN`s / 66 `max()` / 3 `CASE`); the org dimension join adds one CTE, one join and one `max()`, and removes one `CASE`. The line total also carries three comment lines from the same day's `webConsumingSystem` source-column correction, which changed no count below.

| Class | Evidence | Assessment |
|---|---|---|
| **Structural conformance (EAV pivoting)** | 29 `ATTR_GROUP_ID` filters across 28 distinct groups, 31 `LEFT JOIN`s, 71 aggregations (67 `max()` collapsing EAV rows to one value per key, 4 `collect_list`) across 31 CTEs | Not business logic in any meaningful sense — mechanical pivoting of Oracle's attribute-group model into columns |
| **Genuine business rules** | 21 `ITEM_CATALOG_GROUP_ID` discriminator gates; 25 `COALESCE` branch reunifications; UK vs non-UK proliferation (`ag1408` vs `ag1417`); 2 `CASE` expressions | Few in count, **structural in effect** — see below |
| **Reference data standing in for a missing Silver dimension** | **Repaid 2026-08-06 — this class is now empty.** It was a 27-branch literal `CASE` deriving `organizationCode` from `ORGANIZATION_ID`, plus the UK org-id list duplicated in two further `CASE`s, all generated from `manifest.yaml` `org_groups`. `MTL_PARAMETERS` was registered in Silver dev on 2026-08-05, so `organizationCode` and the UK org group are now a dimension join on the `org_params` source and `org_groups` is deleted | Was ~12 lines and the clearest single case of Gold doing Silver's job — a missing dimension join substituted by a literal map. See recommendation item 3 for what the repayment cost and did not buy |
| **Pure shaping** | camelCase renaming, struct and array assembly, 4 `COALESCE(x, array())` null-to-empty defaults | Uncontroversial under any option |

Two corrections to how this is easy to misread:

**The `COALESCE`s are not fallbacks.** `product.sql` lines 5–9 state it: *"Each CTE filters on BOTH attr group and catalog side, so for any item exactly one branch CTE can have rows — `COALESCE(digital, legacy)` is therefore a **strict branch pick, not a fallback** (business rule: no cross-branch fallback)."*

**The business rules are few but not separable.** The discriminator sits inside **21 of the 30 CTEs**, not in a liftable block. **15 of those CTEs exist only as legacy/digital pairs because of the rule** (`ag1162`/`ag1403`, `ag821`/`ag1408`, `ag741`/`ag1401`+`ag1418`, `ag820`/`ag1381`, `ag818`/`ag1406`, `ag1168`/`ag1405`, `ag800`/`ag1403_org`, `ag1173`/`ag1411`, `ag1171`/`ag1408`+`ag1417`), and the 25 branch-pick `COALESCE`s exist solely to reunify those pairs. Remove the rule and half the pivot collapses.

**So "move the rules to Silver later" means restructuring the pivot, not trimming it.** What Option B buys is not cheap relocation later — it is **deferral**. The owners are entitled to choose knowing that.

### Why deciding now is cheaper than deciding later

The pipeline **has never produced data** — the Gold table has never been written and the bucket is empty, so no consumer can be reading Gold *data*.

Be precise about why, because the reason changed on 2026-08-06 and the earlier framing (UIFPE-68: two of three Silver sources not ingested) no longer holds. **All four sources are now ingested in dev** — `mtl-system-items-b` (6.87M rows, 1.60M distinct items), `ego_mtl_sy_items_ext_b` (193.66M rows, 1.59M items), `mtl_system_items_tl` (13.79M rows, 1.63M items), and `mtl_parameters` (62 orgs, registered 2026-08-05 and taken as a source on 2026-08-06 per recommendation item 3), all Iceberg, all carrying `_silver_updated_at`, all readable by `eds-dev-glue-role`. **And the pipeline has run** — the dev dispatcher has been firing daily at 06:30 UTC and failing in ~8 seconds, `rows_read=0`, at `register_full_views()`:

> `AnalysisException: [TABLE_OR_VIEW_NOT_FOUND] … uif_echo_dev_silver_mdm.mtl_system_items_b cannot be found`

The source of truth is registered as `mtl-system-items-b` with hyphens — the only hyphenated name among the 25 tables in that database. So the remaining blocker is a one-table naming defect in Silver, being fixed at source, not an ingestion gap and not a design question. **What this does not change:** no Gold data has ever existed, so the argument in this section stands on its own terms. If anything it sharpens — the window closes the day the naming fix lands, not whenever ingestion is scheduled.

The **contract** position is weaker and should not be overstated: `product.gold-{event,message,api}-value` **have been published** to the Pearson Schema Registry from the retired repo — the publish workflow's own latch exists so exactly one repo publishes at a time — and `gold/product/v1/README.md` records a 2026-06-01 v1 reset that was *"a breaking change vs. the then-published artifacts."* Confirm with the registry owners before treating the shape as unbound.

Either way the window narrows once Price and Stock are compiled.

---

## Options

### Option A — Build the conformed Silver layer, keep Gold projection-only

**For:** highest fidelity to the blueprint. The pivoting is genuinely reusable, so any second EAV-sourced domain gets it free. Makes the guardrail true rather than relocated. Puts conformance where UIFPE-25 says it belongs. **And it is the only option under which the framework's own principle stops being false** — UIFPE-25 states "conformance and business logic derived a single time in the conformed Silver layer; Gold views are projections, never independent re-derivations", and under any other option that sentence stays contradicted at epic level. (The `organizationCode` literal map was A's cheapest illustration of this; it has since been retired under item 3 without taking A, so it is no longer available as an argument for A.)

**Against:** the largest amount of new work, and it does not remove the logic — it moves it, plus a layer to operate. Because the rules are structural (above), this is a restructure of the pivot rather than a lift. Deepening Silver's scope extends the critical path for a pipeline that is one upstream table rename away from its first successful run — and it does so in the layer owned by another team, which is where the current blocker already sits. The CDC, watermark and tier machinery would need rework against conformed sources.

### Option B — Amend the tickets to the canonical-entity-plus-tiers model already running

**For:** built and tested — 186 tests — and **deployed to dev but latched**: `PRODUCT_DEPLOY_ENABLED` is unset and registry publish is gated behind the same latch. *(Status update 2026-08-11: `PRODUCT_DEPLOY_ENABLED` is now SET for dev and dev deploys run green on merge to `develop`; the ADR-0004 §CP-5 cutover completed in dev on 2026-08-04. Registry publish remains latched. UAT and prod are still unprovisioned — their Terraform backends are `UPDATE-ME-` placeholders and only `overlays/dev` exists.)* The pipeline has run daily in dev and failed before reading a row (see Context), so it is exercised end to end as far as the Silver naming defect allows, but has produced no data. The tier mechanism gives three consumer contracts from one authored definition with zero re-derivation between them, mechanically enforced. Retains the CDC/change-log/auto-DDL capabilities any reshape would rebuild. Avoids `api_*_view` proliferation, itself a fragmentation risk.

**Against:** the projection-only guardrail becomes false as written and must be explicitly restated — and restated in UIFPE-25, not only in its children. Tiers are **not** a substitute for views (below), so `api_category_tree` and `api_sku_view` remain unreachable and UIFPE-58's "second consumer-specific Gold projection" is, on a strict reading, **not met**. A second EAV-sourced domain duplicates the pivot. And the event data schema consumers actually bind to — `src/schemas/events/product-created-data.json` — is **hand-authored and kept aligned by a drift test rather than generated**; closing that gap is work Option B should own.

### Option C — Hybrid: canonical entity as the conformance point, plus thin `api_*_view` projections

**For:** satisfies both principles honestly — conformance once, everything above the entity provably projection-only. Gives `api_category_tree` / `api_sku_view` a home and addresses the unmet serving-projection criterion.

**Against:** another layer to build and operate for consumers who have not asked. Risks carrying two designs' cost. Overlaps tiers, and that ambiguity must be settled first.

### What the tier mechanism can and cannot do

The ADR's case for B depends on this, so state the limit plainly. `generate_tier_subsets.py` deep-copies `master.json` and **removes** properties whose `x-channels` lack the tier, recursing into arrays and objects.

- **A subset can only subtract.** No grain change, no join, no denormalisation, no hierarchy. `api_category_tree` (category grain, hierarchical) and `api_sku_view` (variant grain) are **unreachable by construction**, not merely unbuilt.
- **The concentric invariant makes it strictly weaker than arbitrary projections.** `_assert_concentric` *forces* event ⊂ message ⊂ api, so two **incomparable** consumer projections cannot be expressed at all — which is precisely the case UIFPE-58 asks for.
- **Nothing is materialised.** Tiers are JSON Schema artifacts and channel-hash inputs. No consumer can `SELECT` from a tier. An `api_product_view` is a queryable object; a tier is a contract.

Tiers satisfy the *contract* half of "model once, project many". They do not satisfy the *view* half.

---

## Recommendation

**Adopt Option B now — explicitly as deferral, not as a cheaper equivalent — with the guardrail restated at both boundaries, named incremental repayments of Silver debt, and a falsifiable trigger for Option A.**

1. **Amend UIFPE-58, UIFPE-64 and UIFPE-25.** The projection-only rule lives in the epic; amending only the children leaves the framework contradicting the running system, which is the failure mode this ADR exists to end. Either amend UIFPE-25's "Model once, project many" principle and its canonical-first scope line, or state that this ADR supersedes them for PDH-sourced domains.

2. **Restate the guardrail at *both* boundaries.** Above the entity: *no logic between the canonical entity and its projections* — true today and mechanically enforced. That alone is not enough, because it relocates the rule to a boundary where nothing contentious happens. Add the source→entity rule: **business logic in the mapping SQL must be declared in the manifest (discriminator and branches, per the `conditional` kind) and traceable to a business-provided mapping row. Undeclared derivations — status computations, aggregations, hard-coded reference data — are a review failure.** This matters immediately: Stock's intake already specifies `stockStatus` as `CASE WHEN qty > 0 THEN 'In Stock'…` plus `SUM`/`MAX` aggregations, from a flat staging table where no pivot excuse applies. Without the second rule, the restated guardrail constrains nothing.

3. **Repay Silver debt incrementally, starting with the piece already scheduled.** ✅ **Done 2026-08-06** — and deliberately done before this ADR is accepted, because it is the one item that is correct under A, B *and* C: it moves reference data to the layer that owns it, so no reading of the A/B/C question is prejudged by having taken it.

   `uif_echo_dev_silver_mdm.mtl_parameters` was registered on 2026-08-05. The 27-branch `organizationCode` `CASE` and the two duplicated UK org-id `IN`-lists are replaced by a join on a fourth source, `org_params`; `manifest.yaml` `org_groups` is deleted, and UK membership is now the business rule itself — `ORGANIZATION_CODE LIKE '01%'` — rather than a list of ids standing in for it.

   **Verified behaviour-preserving before the swap**, against the full dev source of truth (`uif_echo_dev_silver_mdm.mtl_system_items_b`, 6,865,916 rows over 27 distinct orgs, 2026-08-06): the dimension carries all 27 orgs that have item rows, with codes byte-identical to the retired map; `LIKE '01%'` selects exactly the 7 ids the retired `uk_orgs` listed; and a row-level comparison of old expression versus new join returned **0 code mismatches, 0 UK-group mismatches, 0 new nulls, and a joined row count equal to the input** — the dimension is unique on `ORGANIZATION_ID` (62 rows, 62 distinct ids), so the join cannot fan out and cannot change the `(item, org)` grain.

   **What it cost, stated because it is not free.** The org dimension is not item-grained, so its `key_mapping` can only bind `organizationId` — the widest PK subset in the pipeline. One changed org row therefore invalidates every Gold row for that org (up to 1.6M for org 82), which is correct but far wider than the item-level sources. Worse, Silver stamps `_silver_updated_at` uniformly across the whole `mtl_parameters` snapshot (one distinct value across 62 rows), so a re-ingest that changes nothing still presents 62 changed rows and provokes a full-table rebuild. The canonical hash gate (ADR-0003 D3) absorbs the write side — unchanged rows produce no write, no change-log row, no `_gold_updated_at` bump — so the cost is wasted scan and rebuild, not churn or a wrong result. `max_affected_fraction` will log a WARN on any org change; that is expected, and it is a print rather than a stop. The alternative, watermarking on `last_update_date` (62 distinct real Oracle change times), would suppress the spurious rebuild but would also miss a Silver-side correction that left the Oracle date untouched, so it was not taken. **Revisit if the rebuild cost bites before Silver ingests the org table incrementally.**

   **What it did not buy.** This is the *reference-data* class only. The 21 discriminator gates and 25 branch-pick `COALESCE`s — the class the A/B/C decision actually turns on — are untouched, and per the analysis above they are not separable without restructuring the pivot. Item 3 being done does not make Option B cheaper than it was; it removes one clean instance, not the argument.

4. **Add `api_*_view` projections when a consumer needs one**, and settle the tier/view relationship now so this is a path rather than an aspiration: **a view is a SQL projection of the canonical entity; tiers remain the contract and channel mechanism; a view may be shaped to a tier's field set but never re-derives.** Tiers and views are therefore complementary. Decide the first view on consumer demand, with the requesting consumer named.

5. **Revisit Option A when a second domain requires the `EGO_MTL_SY_ITEMS_EXT_B` attribute-group pivot** — a second **EAV-sourced** entity, not merely a second PDH-sourced one. Price (`XXPSO_QP_VAT_PRICE_STG`) and Stock (`XXPSO_INV_STOCK_ATP_STG`) read flat staging tables with no `ATTR_GROUP_ID`, so they do **not** trigger it. Note this trigger is deliberately earlier than the repo's "extract after the third repetition" principle; the justification is that the duplicated unit is roughly 225 lines of pivot, not a helper function.

### What would change this recommendation

- **A second EAV-sourced domain arriving sooner than expected** — then A's shared-pivot argument dominates and the move should precede the duplication.
- **Consumers requiring REST views broadly** rather than case by case — then C becomes the target.
- **Conformed Silver being built anyway** for reasons beyond Gold — then A is nearly free and should be taken.
- **The owners reading the business-rule surface as substantial rather than structural-but-contained.** The classification above is mine; the rules are entangled with the pivot, and a reasonable reader could conclude that entanglement argues for fixing it now rather than later.

---

## Subsidiary conventions settled by the same decision

| Convention | Recommendation | Reasoning |
|---|---|---|
| **Orchestration** | Keep EventBridge + dispatcher Lambda | Step Functions buys retry/branching a two-job pipeline does not need. The dispatcher already handles frequency routing, cross-job in-flight guarding, and an alarmable fail-open path. Revisit if the DAG grows beyond transform + reconcile |
| **Bucket naming** | Keep `eds-{env}-gold` | It is the live warehouse location, it is registered with Lake Formation (platform repo `lakeformation.tf`) with data-location, database and table permissions built on it, and it matches every other EDS resource. Renaming buys nothing and costs a re-registration plus state churn. *(Not a data-loss argument — the bucket is empty today.)* |
| **Table naming** | `eds_{env}_gold_product.<entity>` for canonical entities; reserve `api_*_view` for projections under item 4 | Matches what is actually deployed (`pipelines/base/product.yaml`, and the platform Terraform that creates the database) |
| **Partitioning** | Retain **ADR-0003's** trigger; do not override it | ADR-0003 already decided this with a better-targeted condition: unpartitioned at current volume, introduce `bucket(N, inventoryItemId)` **if MERGE duration grows** — the right trigger for a `merge-on-read`, MERGE-heavy table. Volume is no longer an estimate on the input side (measured in dev, 2026-08-06): **1.60M distinct items over 6.87M `(item, org)` rows** in the source of truth, so Gold lands at ~6.9M rows — the same order as UIFPE-25's "low millions", but from 1.6M items, not the 398K that figure assumed. **No Gold table has been written, so MERGE duration is still unmeasured** and ADR-0003's trigger remains the right one. Note separately that the table the 30-CTE pivot scans, `ego_mtl_sy_items_ext_b`, holds **193.66M rows** — that governs transform runtime and worker sizing, not Gold partitioning, and it is why `glue.timeout_minutes` is 180 rather than 60. Add a scan-side trigger only if a consumer demonstrates one |
| **Serving projections (UIFPE-58 DoD 5)** | Unmet. Satisfy on the canonical entity once data flows | Athena over the Iceberg table is the cheapest first demonstration. **Partner-scoped row/column filtering is served by Lake Formation LF-Tags on the canonical entity** — the gold bucket is already an LF resource with database and table permissions, including `analyst_gold_product_*` — not by separate partner views. This is a genuine answer to "how do partner projections work without `api_*_view`". DynamoDB, OpenSearch and ElastiCache stay deferred to consumer demand. Beacon export templating is out of scope per UIFPE-58 |
| **API/Event foundation consumability (UIFPE-58 DoD 4)** | Unmet; settle as part of accepting this ADR | Registry publish is latched behind `PRODUCT_DEPLOY_ENABLED` pending the cutover. `master.json` is **draft-07** while the event artifacts and the central meta-schema are **2020-12** — the two foundations would consume both. And **no tier exposes the Gold PK**: `inventoryItemId` and `organizationId` carry `x-channels: []`, so all three tiers omit them and `required` reduces to `[ppid]`. `ppid` + `organizationCode` are present, so a consumer key exists — but it should be stated, not inferred |

---

## Consequences

**Becomes easier**

- Price, Stock, Program and Order can be specified against a settled shape using the proven intake → mapping → artifacts chain
- New attributes stay config-only — auto-DDL and backfill handle the table change
- Tiers keep consumer payloads honest by construction: an api-tier-only change cannot ping the event channel
- The framework documentation stops contradicting the running system, which is what makes it trustworthy

**Becomes harder**

- A second EAV-sourced domain duplicates the pivot until A is taken — a deliberate, dated debt
- `api_category_tree` and variant-grain views remain unreachable until item 4 is exercised; consumers wanting hierarchies query the entity directly
- The canonical entity is wide (50 attributes and growing), making it a schema-contention point across consumers; tiers mitigate but do not remove this

**Accepted**

- Gold performs conformance, not merely projection, stated plainly rather than implied
- The guardrail moves to two boundaries, one of which (source→entity) is a review rule rather than a mechanical check, and is therefore only as good as the review

---

## Implications for the entities not yet built

- **Price (UIFPE-60)** — business mapping received as a workbook; **not yet converted to the standard JSON intake** (the JSON mapping is the submission format per `gold-attribute-mapping.md`). One row per product with an eight-field `priceSpecifications` array. Separately, the ticket specifies `api_offer_price_view` with pricing models; the source is a price-list staging table with no offer concept. **A separable scope question needing its own answer.**
- **Stock (UIFPE-61)** — workbook received, not yet converted. Models warehouse-level inventory keyed `(ppid, organizationCode)`; the ticket describes `api_sku_view` as product *variants* with availability. **Different entities at different grains, not two descriptions of one.** Possibly both are wanted. Also the first test of the source→entity guardrail: its intake contains a derived status and two aggregations.
- **Program (UIFPE-62)** — ~~`programPpid` is currently an array attribute inside Product. Under this recommendation an attribute is the cheaper default; a separate entity is justified only if programs carry attributes of their own.~~ **RESOLVED 2026-08-11 (framework owner): a separate entity, in its own `program` domain — the eighth.** The test this ADR set is met: programs carry twelve attributes of their own, ten scalar (description, longDescription, relevanceValue, pfCatalog, coverImageLarge, programOwner, inventoryItemStatus, plus the key columns and ppid) and two arrays (`associatedProducts`, `relatedPrograms`), none of which is expressible as an attribute of Product. `programPpid` stays on Product as the reference from product → program; the new entity is `gold_program.program`, grain one row per (inventoryItemId, organizationId) over PDH items with `SEGMENT1 LIKE 'P2%'` at `ORGANIZATION_ID = 82`. Delivery: [UIFPE-116](https://pearson.atlassian.net/browse/UIFPE-116).
- **Order (UIFPE-63)** — not started; no Silver order tables exist in dev. Blocked on ingestion before shape matters.

---

## Decision Required

To move from Proposed to Accepted, the framework owners need to confirm:

1. **Which option** — B as deferral with the four supporting items (recommended), A, or C now
2. **The two-boundary guardrail**, including the source→entity review rule — and that it applies to Stock's derived status and aggregations
3. **Whether UIFPE-25, UIFPE-58 and UIFPE-64 get amended**, or this ADR supersedes the relevant parts
4. **The six subsidiary conventions** — orchestration, bucket naming, table naming, partitioning (retaining ADR-0003's trigger), serving projections, and API/Event consumability including the **draft-07 vs 2020-12 dialect split** and the **consumer key**
5. **The Option A trigger** — is "a second EAV-sourced domain" the right condition, or is conformed Silver already planned for other reasons?
6. **The two adjacent scope questions** — Price as price-list versus offer/pricing-model; Stock as warehouse inventory versus SKU variants
7. **The one open shape question already parked for an ADR** (`gold/product/v1/README.md` delta 3): whether `fulfillmentOrgDetails.*` is represented as dotted columns or a struct. Recommendation: the compiled artifact's **struct**, with the intake's dotted names treated as a documentation convention per `gold-attribute-mapping.md`

---

## Acceptance record (2026-08-20)

Between Proposed (2026-08-05) and this record, the framework owners did not
answer the six questions on paper — they answered them **in the repository**,
by directing and merging the work that only makes sense under one reading.
This section makes that ratification explicit, per question, with the
evidence each answer rests on. Tracked as UIFPE-67; the merge of this record,
reviewed by the deciders named in the header, is the acceptance.

**1. Which option — Option B, enacted as the deferral this ADR recommended.**
Every entity specified since is a canonical entity with channel tiers: price
and stock shipped as draft domains on the entity shape (PR #49, 2026-08-17,
framework-owner directed), program was approved as the eighth domain against
this ADR's own test (2026-08-11) and is live in dev since 2026-08-20
(UIFPE-121: 14,239 rows, A1–A8 accepted). The control plane the owner
directed — the intake contract amendments (UIFPE-104 joins, UIFPE-105
structured transforms, UIFPE-107 entityKey), the delta compiler (UIFPE-100)
and the Transform wizard — is built on the canonical-entity contract; none
of it exists in an `api_*_view` world.

**2. The two-boundary guardrail — enacted, and mechanised beyond the review
rule this ADR asked for.** The source→entity rule is now structural: business
logic must arrive as declared intake rules (conditional discriminators and
branches, structured `transforms` replacing every prose transform —
UIFPE-105), and the mapping↔SQL predicate parity gate plus the AUTO/PLAN
census fail CI when the SQL and the declared mapping diverge. Stock's derived
status and aggregations — the immediate test named here — went in as declared
intake rules on the draft entity. Above the entity, tier projection remains
mechanically enforced (`generate_tier_subsets.py`, concentric invariant).

**3. Amend UIFPE-25/58/64 or supersede — supersession, recorded on the
tickets.** This ADR supersedes the projection-only wording and the
`api_*_view` prescriptions **for PDH-sourced domains**; UIFPE-58 and
UIFPE-64 carry a superseded-by banner pointing here (original text
preserved), and UIFPE-25 carries the same note as a comment for its owner.
`api_*_view` remains reserved for consumer-demanded projections per
Recommendation item 4 — a path, not an aspiration.

**4. The six subsidiary conventions — five held exactly as recommended; one
was overtaken by a platform-wide event that preserved its principle.**
Orchestration stayed EventBridge + dispatcher and was extended, not replaced
(UIFPE-149: slot-based dispatch, 11 cadences, per-pipeline start times — the
"revisit if the DAG grows" condition never fired). Table naming held
(`gold_program.program` followed it). Partitioning: ADR-0003's
MERGE-duration trigger retained and never hit — entities remain
unpartitioned. Serving projections: Athena over the canonical entities is in
routine use (program's A1–A8 and product's first-load QA, 2026-08-20, ran
entirely on it) and Lake Formation grants govern access per entity — the
partner-scoping answer given above stands. Bucket naming: the "keep
`eds-{env}-gold`" recommendation was overtaken by the platform-wide
eds→echo rename (UIFPE-103) — the principle the recommendation encoded
(follow the platform's live convention; never rename for cosmetics) is
exactly what the rename honoured. **API/Event consumability (UIFPE-58 DoD 4)
remains the one open residual**: registry publish is still deferred
(`lifecycle: draft` on the newer entities), the draft-07 vs 2020-12 dialect
split is unresolved, and the consumer key is still inferred rather than
stated. That residual transfers to the registry-publishing enablement work —
it is not a condition of this acceptance, and closing UIFPE-58 must not
silently drop it.

**5. The Option A trigger — fired in letter, waived in substance, restated.**
Program is a second EAV-sourced domain, but its pivot reads 4 attribute
groups (~40 lines) against product's 28 (~225): the duplicated unit the
trigger contemplated did not materialise, and the owner proceeded without
Option A (2026-08-11), accepting the small duplication as dated debt. The
trigger is restated accordingly: **revisit Option A when a second LARGE
attribute-group pivot (order-of-20 groups) or a third EAV-sourced consumer
arrives.** Conformed Silver is not otherwise planned, so the third
condition under "What would change this recommendation" stays unmet.

**6. The two scope questions — answered by what shipped.** Price shipped as
the price-list model (one row per `ppid`, `priceSpecifications` array); the
offer/pricing-model reading of UIFPE-60 was not pursued and waits for a
commerce consumer to name offers. Stock shipped as warehouse inventory
(grain `ppid` + `organizationCode`); UIFPE-61's SKU-variant reading is a
different entity at a different grain, recorded for future demand rather
than conflated.

---

## Amendment (2026-09-11) — physical column names

The camelCase column convention this ADR recorded as settled ("camelCase renaming" in the pure-shaping
row, and the 2026-07-22 decision it rests on) is **superseded** by the UIF Schema Standards, Common Rules
N7 and N8 (Confluence, UIF space): the camelCase name is the *logical* name every contract carries;
the physical lakehouse column is its snake_case rendering, generated by a deterministic, lossless
transform and never hand-written. Implemented under UIFPE-437 (stage 2): the shared Gold runtime
renames the mapping-SQL output at the write boundary, addresses the table by physical names in every
DDL and MERGE, and migrates tables written before the rule with metadata-only `RENAME COLUMN`
statements. Top-level hash keys remain the logical names, but a nested column is hashed whole, member
names included, so the rename moved the channel hashes of every row with nested data once (re-baselined
silently on 2026-09-12; see the gold README). Everything else in this ADR stands.
