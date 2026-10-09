> Migrated from uif-dal-gold@cba9af0 (2026-07-31). Numbering inherited from the source repo (0002 was never used).

# ADR-0001 — Gold Layer Architecture

**Status:** Accepted
**Date:** 2026-05-30
**Deciders:** raj.karunakaran@pearson.com (engineering owner)
**Scope:** Bronze → Silver → **Gold** data lake architecture for Oracle EBS source systems.
**Supersedes:** none
**Superseded by:** none
**Amended:** 2026-08-31 — see "Amendment" below (Silver does not meet the Medallion Architecture standard; UIFPE-242, Critical).

---

## Context

The data lake has two layers today:
- **Bronze** — raw Parquet, partitioned by `_extract_date`, written by `generic_bronze_ingest.py`.
- **Silver** — Apache Iceberg, deduplicated by primary key, `_silver_updated_at` watermark, written by `generic_silver_transform.py`.

Silver is a faithful, deduplicated mirror of source tables. It is **not** safe to expose to business consumers — column names are source-system specific (`INVENTORY_ITEM_ID`, `ORGANIZATION_ID`), there is no entity semantics (an Item is spread across 4+ Silver tables), and the table grain matches Oracle's, not the consumer's. *(This description is accurate as written, but is itself the subject of the 2026-08-31 Amendment below — this is a plainer definition of Silver than the Medallion Architecture standard the term is borrowed from actually calls for.)*

We need a **Gold layer** that:
- Models business **entities** (Product, then Customer/Supplier/etc.) by joining multiple Silver tables.
- Publishes a **versioned, machine-readable schema contract** so external consumers (reverse-ETL, eventing, messaging, pull-API) can reliably consume the entity shape.
- Is **incrementally maintained** from Silver — same scheduling/watermark model that Bronze→Silver uses today.

This ADR documents the 12 architectural decisions that shape the v1 Gold layer. Each decision records the options considered, what was chosen, the rationale, and the tradeoff accepted. Future engineers should read this to understand *what* was picked **and** *what was rejected*.

---

## Summary

| # | Decision | Choice |
|---|---|---|
| ADR-1 | Storage pattern | **Materialize** Gold as Iceberg (not virtualize on-demand) |
| ADR-2 | Public schema registry | **Pearson Schema Registry BFF** (`/api/v1/publish`), which writes to Apicurio. Consumer-facing only. |
| ADR-3 | Mapping (join recipe) location | **Git**, atomic with Glue code |
| ADR-4 | Master schema authoring | **Git** (engineer PR-driven), CI publishes subsets to Apicurio |
| ADR-5 | Tier expression | **Pattern γ** — one master with `x-channels` tags + CI-generated subsets |
| ADR-6 | Tier metadata location | On Gold **schema** (not mapping) |
| ADR-7 | Mapping format | **SQL + YAML manifest** (not a homegrown DSL) |
| ADR-8 | Row grain | **Point-in-time**: 1 row per `(natural_key, org_id)`. History via Iceberg time-travel. |
| ADR-9 | Publishing target | **Autobahn** (Pearson Python SDK) — deferred to a later phase |
| ADR-10 | Change detection | **Deferred** — design when publishing is built |
| ADR-11 | v1 scope | **Materialization only** — no publishing, no API, no RETL surface |
| ADR-12 | v1 source tables (Product) | **Core 4 only** — `MTL_SYSTEM_ITEMS_B` + `BOM_COMPONENTS_B` + `EGO_MTL_SY_ITEMS_EXT_B` + `MTL_SYSTEM_ITEMS_TL` |
| ADR-13 | Source-system lineage in manifest | **`source_system` + `canonical_lineage` blocks** declaring PDH→canonical contract alongside the physical Silver bindings |
| ADR-14 | Registry holds canonical + manifest | **Mirror master + manifest into the registry** under group `engineering` with `metadata.audience`; Git remains authoring SoT |

---

## ADR-1 — Materialize Gold or Virtualize on-demand?

**Question:** Should Gold be a stored Iceberg table, or a virtual layer computed on every API request?

**Options:**
- **(X) Virtualize on-demand** — No persistent Gold storage. API joins Silver in real-time per the schema, returns shaped JSON each request.
- **(Y) Materialize as Iceberg** — Gold lives as a persistent Iceberg table. Refreshed incrementally from Silver. Change detection over Gold drives event emission.

**Chosen: Y — Materialize as Iceberg.**

**Why:**
- Eventing requires **change detection** ("Item 123 changed") — impossible without a *moment of truth* to diff against. Virtualization has nothing to diff.
- Reverse-ETL tools (Hightouch/Census/custom) expect a queryable warehouse target; Iceberg via Athena fits naturally. Virtualization would hammer Silver on every sync.
- **Replayability** — if a downstream consumer breaks, materialized Gold can replay historical states via Iceberg time-travel. Virtualization needs to re-query historical Silver — more expensive, more brittle.
- Schema drift is detected loudly (failed job) rather than silently (failed per-request).

**Tradeoff accepted:** duplicate storage (Silver + Gold for the same logical data) and a refresh-window staleness. Both are acceptable for our consumer mix.

---

## ADR-2 — Where does the schema contract live?

**Question:** Where is the **Gold schema** (consumer-facing contract) authored, versioned, and served?

**Options:**
- **(a) Git → S3 → Lambda API serves** — Schemas in this repo, deployed to S3, fronted by a thin Lambda.
- **(b) Schema Registry service (DynamoDB-backed, extension of `pipeline_controller`)** — Schemas in DynamoDB, UI/API for non-dev authors.
- **(c) Hybrid** — Gold schema in registry (UI), mapping in Git.
- **(d) Apicurio** — Pearson's existing schema registry, with UI + API.

**Chosen: (d) Pearson Schema Registry — consumer-facing destination only.** Authoring of the master schema lives in Git (see ADR-4).

Pearson's Schema Registry is a BFF service (`https://cmkimj77g5.execute-api.eu-west-1.amazonaws.com/api/v1/publish`) that wraps Apicurio with org-wide metadata enforcement (service URN ↔ CMDB CI mapping, `dataClassification`, `pii_fields`, `lifecycle`, 9-rule validation). All gold publishes go through the BFF, not Apicurio directly.

**Why:**
- Don't build what already exists at the org level. The BFF is the standard schema-publish surface across Pearson.
- BFF is the *consumer-facing* contract surface — it enforces ownership and classification metadata that downstream tooling depends on.
- Supports JSON Schema, Avro, Protobuf, OpenAPI, etc. — flexible for future channels (e.g. Kafka with Avro).
- The BFF auto-detects new artifact vs. new version, runs a compatibility check, and picks `bump=minor|major` — versioning discipline that Apicurio-direct would force us to implement ourselves.

**Publishing path:**
```
Git → CI → ingestion-framework/scripts/publish.sh → POST /api/v1/publish[/group/artifact]
                                                  → BFF validates + injects metadata + writes Apicurio
                                                  → consumers read from Apicurio
```

**Tradeoff accepted:**
- BFF requires every property in `pii_fields` to resolve as a **top-level field** in the published schema, so PII tagging is per-tier (an event-tier subset can only tag fields that survived the tier filter).
- The `service_urn` must already exist as a CMDB CI; first-time URNs need CMDB registration before they can be used as authors.

**See also:** ADR-4 (master in Git), ADR-5 (subsets vs composition).

---

## ADR-3 — Where does the mapping (join recipe) live?

**Question:** Mapping schema = the internal recipe that says how to produce Gold rows from Silver tables. Where does it live?

**Options:**
- **(a) Apicurio (alongside the Gold schema)** — Treat mapping as another artifact type.
- **(b) Git (in this repo, alongside Glue code)** — Mapping ships in the same PR as the Glue job that consumes it.

**Chosen: (b) Git.**

**Why:**
- **Atomicity:** changing the join logic almost always requires updating Glue code. Git keeps them in one commit; Apicurio would let them ship at different times and break prod.
- **Audiences differ:** Gold schema is for *external* consumers (other teams, systems). Mapping is the *pipeline's* recipe. Putting mapping in Apicurio creates noise for stewards browsing schemas.
- **Apicurio's strengths don't apply to mapping:** there are no "consumers" of mapping besides the Glue job that owns it. Versioning, compatibility checks, governance — none of those map to mapping.
- **CI gates are simpler:** "Does this mapping produce gold-schema-compliant output?" is a unit/integration test. CI runs on Git, not Apicurio.

**Tradeoff accepted:** non-dev authors can't edit mapping via a UI. v1 has zero such users, and a Git-fronted UI (Backstage plugin, custom Lambda opening PRs) is straightforward to add later.

---

## ADR-4 — Master schema authoring: Apicurio or Git?

**Question:** Given Pattern γ (one master schema + CI-generated tier subsets to Apicurio — see ADR-5), where does the *master* live?

**Options:**
- **Git (engineer PR-driven)** — Master in repo. CI generates 3 subsets and publishes to Apicurio on merge. Apicurio = consumer destination only.
- **Apicurio (steward UI-driven)** — Master in Apicurio. Lifecycle hook or scheduled Lambda generates subsets and re-publishes.
- **Hybrid** — Master in Apicurio, CI mirrors to Git for audit.

**Chosen: Git (engineer PR-driven).**

**Why:**
- **Atomic with mapping:** master schema + mapping + Glue code change together in one PR. No drift window.
- **PR review = governance.** Breaking schema changes deserve human review, not a button click in a UI.
- **CI gate is non-negotiable** and runs on Git — replicating the conformance test in Apicurio lifecycle hooks would duplicate test infra into production code paths.
- **Immutability:** Git tag + S3 object version = immutable past versions of the master. Apicurio version pinning works too but is a discipline, not a system property.

**Tradeoff accepted:** stewards can't author master schemas in the Apicurio UI. v1 has zero non-dev authors; revisit if that changes.

---

## ADR-5 — How to express channel-tier membership in the schema?

**Question:** Eventing, messaging, and pull-API need different attribute subsets (event ⊂ message ⊂ api). How is tier membership encoded?

**Options:**
- **(α) Three independent schemas in Apicurio** — `gold/product/event/v1`, `gold/product/message/v1`, `gold/product/api/v1`, each standalone.
- **(β) Composable schemas via JSON Schema `allOf` + Apicurio references** — `message/v1 allOf [event/v1, M_extras]`; `api/v1 allOf [message/v1, A_extras]`. Subset relationship enforced by schema structure.
- **(γ) One master schema with `x-channels` annotations + CI-generated subsets** — Master in Git tags each property with channels; CI emits 3 subset schemas to Apicurio.

**Chosen: γ.**

**Why:**
- **Single source of truth + zero drift:** subsets are *derived* from the master, never hand-maintained. α has drift risk by construction.
- **Tier metadata lives next to the property it describes** — most readable for authors. `description` and `x-channels` sit on the same property block in master.
- **β is elegant but couples us to Apicurio reference resolution quality.** Some Apicurio configs require consumer-side reference resolution, which is real friction for downstream teams. γ produces self-contained subsets that work in any JSON Schema consumer.
- **CI generator is small** — a ~50-line Python script that walks properties, filters by `x-channels`, emits 3 files. Low-maintenance.

**Tradeoff accepted:** the `x-channels` extension field is non-standard JSON Schema (though `x-*` is conventional). Tooling needs to understand it. Our CI script + Glue runtime are the only readers; both ours to control.

**Example:**
```jsonc
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "properties": {
    "product_id":    { "type": "string", "x-channels": ["event", "message", "api"] },
    "description":   { "type": "string", "x-channels": ["event", "message", "api"] },
    "supplier_info": { "type": "object", "x-channels": ["message", "api"] },
    "bom_components":{ "type": "array",  "x-channels": ["api"] }
  }
}
```

---

## ADR-6 — Where does tier metadata live: on the schema or on the mapping?

**Question:** Tier annotations (`x-channels`) — on the Gold schema, or on the mapping?

**Options:**
- **(i) On the Gold schema** — Mapping produces all attributes; publishing layer projects per tier using schema metadata.
- **(ii) On the mapping** — Each mapped attribute declares its tier; mapping drives both production and projection.

**Chosen: (i) On the schema.**

**Why:**
- Tier is a **consumer-facing concept** — it lives where consumers can see it (Apicurio).
- Mapping's concern is *how to produce values*; not *who reads them*. Keeping them orthogonal means re-tiering an attribute (e.g. move "category" from api-only to event) requires *only* a schema edit — no mapping or Glue change.
- Gold Iceberg table holds the full superset regardless of tier. Storage stays canonical; projection happens at publish time.

**Tradeoff accepted:** the publishing layer (when built) must read the schema to know what to project. That's fine — it already reads the schema to validate payload conformance.

---

## ADR-7 — Mapping format: declarative DSL or SQL + manifest?

**Question:** How is the mapping expressed?

**Options:**
- **YAML/JSON declarative DSL** — A custom language describing joins, expressions, transforms. Interpreted by a generic Glue job.
- **SQL templates + thin YAML manifest** — `mapping.yaml` is a small manifest (source tables, output columns, schema reference); the actual transform is in a `.sql` file the manifest points at.

**Chosen: SQL templates + manifest.**

**Why:**
- **SQL is the lingua franca** — every data engineer reads it. A DSL is something new to learn.
- **Athena is right there** for debugging. Paste the SQL, run it, iterate. With a DSL, you debug the DSL interpreter as well as the logic.
- **DSLs almost always re-invent SQL badly.** Joins, CASE, window functions, CTEs — at some point you need them, and the DSL grows worse than SQL.
- **No interpreter to maintain.** Spark SQL is the engine; manifest is just metadata.

**Tradeoff accepted:** SQL is more verbose than a perfectly-fit DSL, and harder to manipulate programmatically (e.g., auto-generating mappings from a schema). We don't need auto-generation in v1.

**Layout:**
```
schemas/mapping/product/v1/
  manifest.yaml          # sources, output cols, schema ref, watermark cols
  sql/
    product.sql          # canonical join — readable, debuggable in Athena
  fixtures/              # silver input + expected gold output (for CI)
```

---

## ADR-8 — Row grain in Gold

**Question:** What's the natural key + grain of a Gold row for the Product domain?

**Options:**
- **Point-in-time:** 1 row per `(product_id, org_id)`. Current state only. History via Iceberg time-travel.
- **SCD Type 2:** 1 row per `(product_id, org_id, effective_from)`. `effective_to` closed on update. History in normal SQL.
- **Oracle EBS-native effectivity:** Use EBS's `START_DATE_ACTIVE`/`END_DATE_ACTIVE` as the effectivity bounds.

**Chosen: Point-in-time.**

**Why:**
- Matches what reverse-ETL/eventing consumers want — the **current state** of the entity.
- Iceberg's snapshot-based time-travel covers the "as-of-date" use case (`SELECT ... FOR TIMESTAMP AS OF '2026-01-01'`) without polluting the row model with `effective_from`/`effective_to`.
- Simpler MERGE logic: `UPSERT ON (product_id, org_id)`. No row-closing semantics.
- 2 rows per product (one per org) is bounded; SCD2 grows unbounded.

**Tradeoff accepted:** consumers who need "as-of-date in normal SQL" must use Iceberg time-travel syntax (or query a snapshot table). Revisit this ADR if a specific consumer pushes back on this.

---

## ADR-9 — Publishing target

**Question:** Where do Gold events/messages go?

**Options considered:**
- MSK / Kafka
- EventBridge + SQS
- SNS + SQS
- Kinesis
- **Autobahn** — Pearson's homegrown eventing platform with Python SDK

**Chosen: Autobahn — deferred to a later phase.**

**Why:**
- Autobahn is the standard Pearson eventing platform; don't introduce a parallel infrastructure.
- Python SDK fits Glue Python jobs naturally.
- **Deferred from v1** because change detection design (ADR-10) is also deferred. Building publishing without change detection means republishing every row on every run — wasteful and noisy for downstream.

**Tradeoff accepted:** v1 has no live publishing surface; consumers query Athena directly as the escape hatch.

---

## ADR-10 — Change detection

**Question:** How does the Gold pipeline decide what to publish?

**Options:**
- **Iceberg snapshot diff** — Diff current snapshot vs. previous snapshot. Emit one event per inserted/updated row.
- **Publish everything in the batch** — Emit events for every row written/touched in each Gold MERGE.
- **Row-level CDC table** — Separate `_gold_changes` log table; publisher reads from it.

**Chosen: Deferred** until publishing is designed.

**Why deferred:** without a publishing target wired up (ADR-9 deferred), change detection has no consumer to optimize for. Better to design these two together later, when consumer requirements are concrete.

**Expected default when un-deferred:** Iceberg snapshot diff — precise, native to Iceberg, no extra state.

---

## ADR-11 — v1 scope

**Question:** What ships in v1?

**Options:**
- Materialization only
- Materialization + Pull API
- Materialization + Pull API + read-only RETL
- Materialization + Autobahn publishing (no change detection)

**Chosen: Materialization only.**

**Why:**
- Smallest deliverable that validates the framework end-to-end (schema + mapping + Glue + Iceberg + reconcile + CI).
- Consumer surfaces (Pull API, RETL, eventing) are each substantial projects in their own right; bundling them dilutes v1.
- Athena is the escape hatch — internal consumers can query Gold directly until purpose-built surfaces ship.

**Tradeoff accepted:** v1 doesn't onboard external consumers. Acceptable because consumers depend on schemas being stable, and v1 is the first time the schema exists at all.

---

## ADR-12 — v1 source tables for Product domain

**Question:** Which Silver tables feed the v1 Product Gold entity?

**Options:**
- Core 4: `MTL_SYSTEM_ITEMS_B` + `BOM_COMPONENTS_B` + `EGO_MTL_SY_ITEMS_EXT_B` + `MTL_SYSTEM_ITEMS_TL`
- Add `MTL_ITEM_CATEGORIES` + `MTL_CATEGORIES` (category enrichment)
- Add `MTL_ITEM_STATUS` (status enrichment)
- Add organization tables (`MTL_PARAMETERS` / `HR_ORGANIZATION_INFORMATION`)

**Chosen: Core 4 only.**

**Why:**
- All 4 tables are already in Silver (or registered) — no new Silver pipelines needed first.
- Validates the framework end-to-end with a meaningful but bounded join graph (4 tables → 1 entity).
- Categorization and status are real consumer needs but **defer to v2** to keep v1 ship-able.
- Adding tables incrementally lets us learn what the mapping/schema framework feels like in practice before scaling out.

**Tradeoff accepted:** v1 Product entity is missing common reverse-ETL fields (category, status, org name). Athena queries can join those manually until v2 lands them.

---

## ADR-13 — Source-system lineage as first-class manifest metadata

**Question:** How do we express that the Product canonical entity originates from Oracle EBS PDH, independent of which lake tier physically delivers it today?

**Options:**
- **(a) Status quo** — Leave it as prose in `master.json` `description` fields ("Maps to MTL_SYSTEM_ITEMS_B.SEGMENT1").
- **(b) Structured manifest metadata** — Promote PDH→canonical lineage to top-level `source_system` + `canonical_lineage` blocks in `manifest.yaml`. Physical Silver bindings (`sources[]`, `sot_pk_mapping`) stay where they are.
- **(c) Separate lineage registry service** — Standalone API/UI for cross-domain lineage.

**Chosen: (b).**

**Why:**
- **Atomicity with mapping SQL** — same PR review covers schema, lineage, and physical join. No drift window.
- **Lint-checkable against `master.json`** — CI fails when a canonical property has no lineage entry (or vice-versa). Drift is caught before merge, not at runtime.
- **Decouples semantic contract from physical binding** — `silver_table` and `_silver_updated_at` are *how* PDH data lands in our lake. `pdh_table` and `pdh_column` are *what the attribute means*. Today they're 1:1; tomorrow the physical layer may change (direct CDC, federated query) without touching the semantic contract.
- **Aligns with ADR-3** — mapping (internal recipe) lives in Git, atomic with Glue code. The semantic block follows the same locality argument.
- **(c) is over-engineered** for one entity. Revisit if ≥3 entities and ≥2 source systems.

**Tradeoff accepted:** the `canonical_lineage` block adds verbosity and duplicates information already implied by the SQL. Accepted because the duplication is the point — SQL is the *implementation*, lineage is the *contract*. Reviewers reading either should be able to verify they agree.

**Manifest shape (additive):**

```yaml
source_system:
  name: "Oracle EBS PDH"
  modules: ["Inventory", "Item Catalog"]
  contact: "ebs-platform@pearson.com"

canonical_lineage:
  inventory_item_id:
    pdh_table:  MTL_SYSTEM_ITEMS_B
    pdh_column: INVENTORY_ITEM_ID
    role:       pk
  isbn13:
    pdh_table:  EGO_MTL_SY_ITEMS_EXT_B
    pdh_column: C_EXT_ATTR8
    pdh_filter: "ATTR_GROUP_ID = 741"
    pdh_grain:  item-level
```

Required per entry: `pdh_table`, `pdh_column`. Optional: `role` (`pk` if part of natural key), `pdh_filter` (WHERE fragment in PDH semantics), `pdh_grain` (free text when grain differs from join key), `transform` (reserved).

**CI enforcement** (`.github/workflows/gold-schemas.yml`):
- Every property in `master.json` has a `canonical_lineage` entry, and vice-versa.
- Every `role: pk` entry matches `target.primary_key_cols` exactly.
- `source_system.name` is non-empty.

**Glue jobs unaffected.** The transform and reconcile jobs ignore both blocks — they remain governed by `sources[]`, `sot_pk_mapping`, and `output_columns`. The semantic block is documentation-and-lint-only on day one.

**Revisit trigger:** a canonical entity gains ≥2 source systems. At that point, either (i) split into per-source manifests with a downstream merge layer, or (ii) make `canonical_lineage` keyed by source-system alias (`canonical_lineage.pdh.<col>`, `canonical_lineage.step.<col>`).

---

## ADR-14 — Registry as single read source-of-truth for Gold artifacts

**Question:** ADR-2 made the registry consumer-facing only — tier subsets in, master and mapping kept in Git+S3. Should the canonical master and the mapping manifest also live in the registry so consumers and engineers have one place to discover Gold artifacts?

**Options:**
- **(a) Status quo** — Registry = consumer tier subsets only; master + mapping in Git+S3.
- **(b) Mirror everything to the registry, Git stays authoring SoT** — CI publishes master and manifest alongside subsets, distinguished by group + `metadata.audience`.
- **(c) Full reversal — registry becomes authoring SoT** — Master and mapping authored in registry UI; Git becomes a downstream mirror for code generation.

**Chosen: (b).**

**Why:**
- Solves the real complaint with (a): there is no single place to discover what a Gold entity looks like — consumers query Apicurio, engineers grep Git. Mirroring closes that gap.
- (c) breaks the atomic-PR property paid for by ADR-3 and ADR-4. `master.json` ↔ `manifest.yaml` ↔ `Glue code` would no longer change in one reviewable commit; the registry could hold a manifest that disagrees with the deployed SQL. (b) preserves atomicity because CI publishes from a single merged Git commit — registry is always strictly downstream.
- (b) is reversible. If/when registry-first authoring becomes a real requirement (non-engineer authors, governance workflows in the UI), the migration is additive: introduce a registry→Git sync, deprecate the Git→registry push. No data loss, no consumer break.

**Visibility model — group + `metadata.audience`:**

| Artifact | Group | `metadata.audience` |
|---|---|---|
| `{entity}.gold-event-value` | `default` | `consumer` |
| `{entity}.gold-message-value` | `default` | `consumer` |
| `{entity}.gold-api-value` | `default` | `consumer` |
| `{entity}.gold-canonical-value` | `engineering` | `engineering` |
| `{entity}.gold-mapping-manifest` | `engineering` | `engineering` |

Group is the audience boundary; `metadata.audience` is the human-readable marker downstream UIs filter on. `dataClassification` continues to mean *data sensitivity* (`internal`, `confidential`, etc.) and is orthogonal — every Gold artifact today is `dataClassification: internal` regardless of audience.

**Mapping artifact shape:** `manifest.yaml` is serialized to JSON by `scripts/build_mapping_artifact.py` at publish time. The published JSON inherits `owner` and `domain` from the master (single source of truth) and carries `source_system`, `canonical_lineage`, `sources`, `target`, `output_columns`, `sql_relative_path`, and `null_sentinels` (the declared placeholders the table renders as NULL — UIFPE-268; null when the manifest declares none). Published with `--type-override JSON` so the BFF treats it as opaque JSON, not as a JSON Schema to validate.

**CI invariant:** the publish step iterates each master in the same workflow run that publishes its subsets and its manifest. There is no point at which the registry holds one without the others from a given commit. If the BFF rejects any of the three publishes, the workflow fails and main is not advanced.

**Tradeoff accepted:**
- Registry now carries two artifact "shapes" (JSON Schema for tier subsets and canonical; opaque JSON for manifest). Tooling that walks the registry must handle both.
- BFF behavior for the `engineering` group and for `--type-override JSON` is exercised for the first time by this change. First merge to main is the real test; if the BFF rejects, fall back is to wrap the manifest JSON inside a no-op JSON Schema envelope.
- Authoring still requires a Git PR. Non-engineer authors cannot edit the registry-mirrored master or manifest directly — those writes would be overwritten by the next CI run. Acceptable while v1 has zero non-engineer authors.

**Revisit trigger:** non-engineer authors need to edit master or manifest in the registry UI without going through a Git PR. At that point, evaluate option (c).


These are intentionally deferred — captured here so they don't accidentally creep into v1:

- **Autobahn publishing** (ADR-9 deferred) — eventing/messaging via Pearson's Autobahn Python SDK.
- **Change detection** (ADR-10 deferred) — Iceberg snapshot diff vs CDC table vs batch republish.
- **Pull API** — consumer-facing API Gateway → Lambda → Athena/Iceberg passthrough returning schema-conformant JSON.
- **Self-service Reverse ETL** — UI for consumers to pick attribute subset + format (CSV/Parquet) + delivery destination + schedule.
- **Additional source tables** — categories, status, org info, suppliers, pricing (ADR-12 deferred).
- **Additional Gold domains** — Customer, Supplier, etc. v1 is Product only; framework will support more via the same Git-schema + Glue-mapping pattern.
- **Steward UI for schema authoring** (ADR-4 deferred) — A Backstage plugin or Apicurio-UI-with-tier-tags layer can come later if non-dev authors join.
- **SCD2 / time-versioned rows** (ADR-8 deferred) — v1 is point-in-time; Iceberg time-travel is the history mechanism.

---

## Architecture (v1)

```
                  Git Repo (source of truth)
  ┌──────────────────────────────────────────────────────────┐
  │  schemas/gold/product/v1/master.json    (JSON Schema)    │
  │  schemas/mapping/product/v1/                             │
  │      manifest.yaml      (joins, output cols, validation) │
  │      sql/product.sql    (canonical join query)           │
  │      fixtures/          (sample silver rows + expected)  │
  │  glue_jobs/generic_gold_transform.py                     │
  │  glue_jobs/generic_gold_reconcile.py                     │
  └──────────────────────────────────────────────────────────┘
            │                                       │
            │ CI on merge to main                   │
            ▼                                       ▼
   Generate event/message/api      Upload mapping + sql + master
   subsets from master              to s3://gold-artifacts-{env}/
            │
            ▼
   Publish 3 subsets to Apicurio:
     gold/product/event/v1
     gold/product/message/v1
     gold/product/api/v1     (= full master, all attrs)

                  ┌─── Runtime ───┐
                  ▼               ▼
  EventBridge (gold cadence)  Glue Job: generic_gold_transform.py
       │                       │
       └──> scheduler_dispatcher (route gold pipelines)
                                │
                                ▼
                        Read mapping from S3
                        Load 4 Silver tables (since last_processed_silver_updated_at)
                        Run SQL join
                        Validate output rows against master schema (Draft-07)
                        MERGE INTO Iceberg gold_product.product
                                ON (product_id, org_id)
                        Advance watermark in DynamoDB
                                │
                                ▼
            s3://dal-gold-{env}/product/
            glue_catalog.gold_product.product   ← consumers query via Athena

  Reconcile (nightly): anti-join Silver natural keys vs Gold → orphan delete
                       (mirrors generic_silver_reconcile.py)
```

---

## Revisit Triggers

This ADR should be re-opened (and superseded) if any of the following become true:

| Trigger | Affected ADR |
|---|---|
| A consumer needs "as-of-date" history in normal SQL (not Iceberg time-travel) | ADR-8 |
| Non-engineer authors need to publish/edit Gold schemas | ADR-4 |
| ≥5 Gold entities exist and adding a new one feels like boilerplate | ADR-7 (consider DSL) |
| Apicurio reference resolution improves materially | ADR-5 (consider β) |
| Publishing requirements solidify (Autobahn integration scoped) | ADR-9, ADR-10 |
| Pull API or self-service RETL is greenlit | ADR-11 |
| A canonical entity gains ≥2 source systems (e.g. PDH + Stibo STEP) | ADR-13 |
| Non-engineer authors need to edit master/manifest in the registry UI | ADR-14 |
| Silver-layer cleansing/conformance is actually built (closes the 2026-08-31 Amendment) | Context (Silver definition), ADR-7 (mapping `transform`/`sqlExpr` blocks would simplify) |

---

## Amendment (2026-08-31) — Silver does not meet the Medallion Architecture standard

**Raised as:** [UIFPE-242](https://pearson.atlassian.net/browse/UIFPE-242), Critical. This section is an addition to the record, not a rewrite of it — the Context above is left as originally written; this amendment says what's changed about how to read it.

**The gap.** The Context section's own words — *"Silver is a faithful, deduplicated mirror of source tables... not safe to expose to business consumers"* — describe mechanical processing only: dedup by primary key, watermark, Parquet→Iceberg conversion. Checked against the term's own primary source, Databricks: Silver is supposed to be where data *"is matched, merged, conformed and cleansed ('just-enough') so that the Silver layer can provide an 'Enterprise view' of all its key business entities, concepts and transactions."* A Qlik whitepaper ("Building Scalable Data Pipelines with Medallion Architecture") is concrete about what that means in practice — Join, Union, Aggregate, Filter, null-handling (`CASE WHEN ... IS NULL THEN 'UNKNOWN'`), standardization (`UPPER(TRIM(...))`) — summarized as *"Cleaned, filtered, and enriched data ready for downstream processing."* This repo's Silver does none of that.

**Why this ADR, not a different one.** ADR-1 through ADR-14 above all assume Silver as *given* and design Gold on top of it. That assumption is where the gap actually bites: because Silver skips the cleanse/conform step, every one of those Gold-side decisions — the mapping SQL (ADR-7), the `transform`/`sqlExpr`/`cases` blocks a mapping manifest carries — ends up doing **two jobs at once**: the cleanse/conform work Silver should have done, and the aggregate-for-consumption work Gold is actually meant to do. Concrete, compounding cost: a raw-column data-quality quirk has to be independently discovered and handled in *every* domain's own Gold mapping that reads it (`product`'s, `program`'s, any future one), instead of being fixed once, centrally, at Silver.

**Not resolved by this amendment — recorded as open.** Bronze→Silver is owned by `uif-relay-echo`, an external repo outside this monorepo ([ADR-0004](0004-independent-gold-control-plane.md)). Closing this gap is a decision for whoever owns that repo as much as for this one; this amendment records that the gap is real and names it, it does not — and cannot, from inside this repo alone — fix it.

**What exists toward a fix.** [`architecture-design/reference-architectures/medallion-agentic-consumption.md`](../reference-architectures/medallion-agentic-consumption.md) — a target-state design (explicitly labeled as such, not yet implemented) showing what a real cleansed Silver contract looks like: a worked `price` domain example (`price-list-entry-cleansed.avsc`) doing the Databricks/Qlik-style operations — decimal casting, currency/country normalization, explicit null handling — at Silver, not deferred to Gold.

**Sources:** Databricks, [What is the Medallion Lakehouse Architecture?](https://www.databricks.com/blog/what-is-medallion-architecture); Google Cloud, [What is medallion architecture?](https://cloud.google.com/discover/what-is-medallion-architecture); Qlik, "Building Scalable Data Pipelines with Medallion Architecture" (whitepaper).
