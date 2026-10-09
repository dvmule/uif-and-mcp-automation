> Migrated from uif-dal-gold@cba9af0 (2026-07-31). Numbering inherited from the source repo (0002 was never used).
> **Naming note (2026-08-09, UIFPE-103):** resource names written here as `eds-{env}-*` / `eds_{env}_*` are historical — the platform convention is now `echo-{env}-*` / `echo_{env}_*`, and the environment set is dev/uat/prod (uat replaced staging). Names below are preserved as written at decision time.

# ADR-0003 — Gold Incremental Materialization & Channel-Aware CDC

**Status:** Draft (for review)
**Date:** 2026-07-16
**Deciders:** reyasahamed.habibrahman@pearson.com, raj.karunakaran@pearson.com
**Scope:** Silver → Gold incremental flow, change detection, channel model, and outbound consumption contract for Gold entities (Product first).
**Supersedes:** ADR-0001 §ADR-10 (change detection — previously deferred, now designed). Amends the incremental algorithm implied by `generic_gold_transform.py` and relaxes ADR-0001 §ADR-5's concentric-tier invariant.
**Superseded by:** none

---

## Context

ADR-0001 shipped Gold v1 as "materialization only": a config-driven Glue job joins N Silver Iceberg tables per a Git-versioned SQL mapping and MERGEs into a Gold Iceberg table. Change detection (ADR-10) and publishing (ADR-9) were deferred.

Two forces now require the incremental design to be settled:

1. **The current incremental algorithm is incorrect for multi-table joins.**
   `generic_gold_transform.py` filters *each* Silver source to `_silver_updated_at > watermark` **before** joining. With ≥2 sources this fails two ways:
   - **Lost changes** — if only `EGO_MTL_SY_ITEMS_EXT_B` changed (e.g. an ISBN correction), the filtered `items` view is empty, the join emits zero rows, and the change never reaches Gold.
   - **Clobbered data** — if only `MTL_SYSTEM_ITEMS_B` changed, the filtered `items_ext` view is empty, the LEFT JOIN yields NULL ISBNs, and the MERGE overwrites good Gold values with NULL.

   It has only appeared to work because runs to date were effectively full rebuilds (epoch watermark).

2. **The consumption model is now concrete.** Gold will be consumed through multiple *channels* (API, eventing, data feeds), each carrying a different attribute subset of the entity. Each channel must be able to detect "did anything **I** carry change?" independently, be added without code or DDL changes, and be replayed (full load or from-a-date) without affecting other channels.

Constraints confirmed during design review:

- Gold lives on **AWS only** (Iceberg + Glue + Athena). BigQuery remains a Silver-layer reporting replica; the UIF platform layer is AWS.
- **No history rows in the main entity table** (point-in-time grain per ADR-0001 §ADR-8 stands). Change capture must live beside the load path without slowing it.
- Adding an entity attribute or a channel must be a **config change** (Git PR touching schema/mapping), never a Glue code or table-structure change.
- Introducing a new channel must **not** disturb existing rows' change history: no timestamp movement, no change-log noise, no outbound emission storm.

---

## Summary of Decisions

| # | Decision | Choice |
|---|---|---|
| D1 | Incremental strategy | **Two-phase: detect affected keys → rebuild full entities** (replaces filtered-join) |
| D2 | CDC mechanism (Silver→Gold) | **Per-source watermark on `_silver_updated_at`**; deletes stay with nightly reconcile |
| D3 | Write granularity | **Full entity record, hash-gated MERGE** (changed-columns-only rejected) |
| D4 | Change-detection unit | **Per-channel hashes, schema-derived from `x-channels`, stored as one `MAP` column** |
| D5 | Change history | **Append-only `{entity}_changes` Iceberg table**; no history in the entity table |
| D6 | Failure ordering | **Log first → MERGE → advance watermark** (at-least-once + dedupe, never at-most-once) |
| D7 | Channel lifecycle | **Registry in DynamoDB config + silent hash backfill** (no timestamp bump, no log rows) |
| D8 | Outbound contract | **Per-channel cursors over the change log**; replay = cursor reset; full load = snapshot read |
| D9 | Retention | **Change-log retention decoupled from Iceberg snapshot expiry** (long vs short) |
| D10 | Tier invariant | **Relax concentric `event ⊂ message ⊂ api` to arbitrary per-channel subsets** |
| D11 | 1:many attributes | **Nested `ARRAY<STRUCT>` columns with mandatory declared sort keys** |
| D12 | Attribute lifecycle | **Schema-diff-triggered auto-DDL + one-time backfill with mandatory hash recompute** |

---

## D1 — Incremental strategy: two-phase "detect keys, then rebuild entities"

**Options:**
- **(a) Filtered-join (status quo)** — filter every source by watermark, join the filtered views. *Incorrect* (see Context).
- **(b) Full rebuild every run** — correct, but cost scales with table size, not change volume; makes Iceberg snapshots enormous; blocks meaningful change detection.
- **(c) Two-phase** — Phase 1 detects *which entity keys* were affected by any source change; Phase 2 rebuilds those entities from **full, unfiltered** Silver and MERGEs.

**Chosen: (c).**

**Phase 1 — key detection (cheap).** For each source, scan rows where `watermark_col > last_watermark` and project each changed row to the Gold natural key(s) it affects, using a new per-source `key_mapping` block in the manifest (see *Manifest additions*). Union across sources → `affected_keys` (deduplicated).

**Phase 2 — entity rebuild (targeted).** Register each source as an **unfiltered** temp view. Semi-join the driving source to `affected_keys`, then run the mapping SQL unchanged. Every output row is a complete, internally consistent entity — no NULL-clobbering, no lost changes. MERGE on the natural key as today.

**Key fan-out rule:** a source's `key_mapping` may bind a *subset* of the Gold PK columns. Example: `EGO_MTL_SY_ITEMS_EXT_B` is item-level, so it maps only `inventory_item_id`; the semi-join on that column alone naturally fans the change out to every `(item, org)` row for that item. A changed `BOM_COMPONENTS_B` row maps to its *parent* item's key.

**Lookup-only sources (amendment, 2026-08-31 — the `categories` column).** D1 as originally written assumes every source carries at least one Gold PK column. A *pure dimension* does not: `MTL_CATEGORIES_B` and `MTL_CATEGORY_SETS` have neither an item nor an org, so the honest `key_mapping` for them is *no mapping at all* — there is no expression over a changed row that names the Gold rows it affects. Such a source declares **`lookup_only: true`** in place of `key_mapping`. It is registered as a FULL temp view in Phase 2 like any other source and is **skipped by Phase 1**.

The consequence is deliberate and must be stated wherever it is used: **an edit confined to a lookup-only source rebuilds nothing.** Values denormalized from it — a category label on ~6.9M product rows — move only on the next `--refresh_columns` run. Changes in the tables that *do* carry a PK column stay fully incremental, so for `categories` the split is: adding, moving or removing an item's assignment is incremental (it lands in `MTL_ITEM_CATEGORIES`); *relabelling* a category is deferred.

`lookup_only` together with `key_mapping` is refused rather than resolved by precedence. The failure this prevents is specific: a mapping of `{organizationId: "CAST(NULL AS BIGINT)"}` satisfies the PK-membership lint, advances the watermark every run, and detects nothing — working CDC to read, a permanent no-op in operation. Where per-column freshness matters more than this trade-off, the alternative is to model the dimension as its own Gold entity, which has a natural PK and needs no exception.

**Guardrail (replaces the old anomaly guard):** the `rows_read vs rows_written` guard is meaningless across a join and is removed. New guard: if `affected_keys` exceeds a configurable fraction of the current Gold row count (`glue.max_affected_fraction`, default 0.5), log loudly — the run is effectively a rebuild and worker sizing should be revisited. The run still proceeds (correctness is unaffected).

**Tradeoff accepted:** two passes over the changed slice of Silver (one to detect keys, one inside the rebuild join). Change volume is small relative to table size, so this is cheap; correctness is non-negotiable.

---

## D2 — CDC mechanism: per-source watermarks; deletes stay with reconcile

**Options:**
- **(a) Watermark on `_silver_updated_at` per source** — the framework's existing idiom (Bronze→Silver uses the same shape). No new state; debuggable in Athena.
- **(b) Iceberg incremental reads / changelog views** — precise, sees deletes, but requires per-source snapshot bookkeeping in config, and the Glue 4.0/5.1 version split complicates changelog support.
- **(c) Republish everything** — rejected with D1(b).

**Chosen: (a).** Consistency with the rest of the framework wins; one mental model for on-call.

**Per-source watermarks (normative — replaces the current single global watermark).** The existing job stores one watermark: `max(_silver_updated_at)` across *all* sources. That has a miss window: if a Silver run for source B stamps rows at T1 but its Iceberg commit lands after Gold has already read B and advanced the global watermark to T2 > T1 (driven by source A's newer stamps), the T1 rows are filtered out on every subsequent run — silently and permanently. The fix: the config stores **one watermark per manifest alias**, each advanced only to the max `_silver_updated_at` that source itself showed in this run. Late-committing rows then still exceed *their own source's* watermark next run. This is safe because Silver runs for the same table never overlap (dispatcher in-flight guard), so a source's stamps cannot leapfrog its own committed history. Delta scans and watermark advancement are per-alias; a run's success advances all aliases atomically in one DynamoDB update (D6 step 3). Migration: on first run after deploy, a legacy scalar watermark is expanded to the per-alias map using its value for every alias (safe: at worst it re-detects rows the hash gate discards).

**Deletes:** watermarks cannot see hard-deletes in Silver. That remains the job of the nightly `generic_gold_reconcile.py` anti-join (unchanged), which additionally appends `DELETE` rows to the change log (D5).

**Revisit trigger:** if a Silver source ever stops stamping `_silver_updated_at` reliably, or sub-hourly latency is demanded, re-evaluate (b).

---

## D3 — Write granularity: full record, hash-gated

**Question:** push only changed columns to Gold, or the full rebuilt record?

**Chosen: full record, with the MERGE gated on a hash comparison.**

**Why changed-columns-only is rejected:** Parquet files are immutable — an Iceberg MERGE physically rewrites the affected row whether 1 or 30 columns change, so partial updates save nothing. They would cost dynamically generated `UPDATE SET` clauses per changed-column combination and risk assembling composite records that never existed in the source. The expensive step is *finding* changed rows, not writing them.

**The gate:** a row is written only when its full-record hash differs:

```sql
WHEN MATCHED AND t._channel_hashes['canonical'] != s._channel_hashes['canonical']
  THEN UPDATE SET *
WHEN NOT MATCHED THEN INSERT *
```

No-op rebuilds (an upstream touch that changed nothing material) produce zero writes, zero snapshot noise, zero change-log rows.

---

## D4 — Per-channel hashes, schema-derived, stored as a map

**Question:** consumers per channel carry different attribute subsets (API might carry 10 of 30 attributes; eventing 15). How does each channel know whether *its* view of the entity changed?

**Options:**
- **(a) One hash per channel as physical columns** (`_hash_event`, `_hash_api`, …) — works, but adding a channel = `ALTER TABLE` + code change. Violates the config-only requirement.
- **(b) One hash per channel in a single `MAP<STRING,STRING>` column, channels discovered at runtime from `x-channels` tags in `master.json`** — adding a channel is a schema-tag edit; no DDL, no code.
- **(c) Column-level change tracking** (bitmap of changed attributes) — maximum precision, but explodes log width and publisher complexity for no current requirement.

**Chosen: (b).**

The transform reads `master.json`, takes the union of all `x-channels` tags across properties, and computes one hash per discovered channel over exactly the properties tagged with it. A reserved channel **`canonical`** always covers *all* properties and serves as the D3 MERGE gate.

**Hash specification** (normative — both the transform and any backfill must implement identically):

- Algorithm: `SHA-256`, hex-encoded, via Spark `sha2(payload, 256)`.
- Payload: JSON serialization of a struct containing the channel's properties **in sorted property-name order** (so reordering `master.json` never shifts hashes).
- Nulls serialized explicitly (`null`), timestamps as ISO-8601 UTC, decimals as plain strings, arrays pre-sorted per D11.
- The config carries `hash_spec_version: 1`; any change to these rules bumps it and triggers a silent re-backfill (D7 machinery reused).

**Documented caveat:** re-tiering an attribute (editing its `x-channels`) changes that channel's hash inputs, producing a one-time wave of detected changes on next run for rows where the added/removed attribute has data. This is *expected behavior* and should be scheduled consciously; if silence is required, use the D7 silent backfill with the new definition instead.

---

## D5 — Change history: append-only change-log table, not history rows

**Options:**
- **(a) SCD2 in the entity table** — rejected; ADR-0001 §ADR-8 point-in-time grain stands, and the deciders explicitly do not want history in the main table.
- **(b) Iceberg snapshot diffing at publish time** — no extra writes, but pushes compute and complexity onto every publisher, and history horizon = snapshot retention (short).
- **(c) Append-only `{entity}_changes` table written by the same transform run** — the changed rows and their old/new hashes are *already in hand* for the D3 gate; persisting the verdict is a fast partition append (no MERGE, no lookup).

**Chosen: (c).** Load-path impact is a single append of narrow rows (keys + channel list — **no payloads**); measured against the MERGE it is noise.

Rows record `(natural key, op ∈ {INSERT, UPDATE, DELETE, BOOTSTRAP}, changed_channels, run_id, timestamp)`. `changed_channels` is computed in one pre-MERGE join (see *Runtime flow*). The reconcile job appends `DELETE` rows with `changed_channels` = all registered channels. Full historical row *images* are not duplicated here — Iceberg time-travel on the entity table covers that while snapshots are retained.

**Rebuilds are silent:** a `--rebuild` run re-materializes the table (CTAS) and writes **no** change-log rows — pre-drop hashes are gone, so "what changed" is unknowable, and a rebuild is re-materialization, not business change. Consumers needing certainty after a logic-changing rebuild use the D8 full-load/replay path.

---

## D6 — Failure ordering: log → MERGE → watermark

If the MERGE commits but the process dies before the change-log append, a rerun rebuilds the same entities, finds hashes now **matching** (the MERGE landed), and logs nothing — those change events are silently lost. Therefore the run order is fixed:

1. Append to `{entity}_changes`.
2. MERGE into the entity table.
3. Advance the watermark in DynamoDB.

A crash between 1 and 2 means the rerun regenerates the same log rows (hashes still differ) → **duplicates, never gaps**. Publishers dedupe on `(natural key, run_id)` or are idempotent. At-least-once with dedupe beats at-most-once with silent loss for every consumer type in scope (API cache refresh, eventing, feeds).

---

## D7 — Channel lifecycle: registry + silent bootstrap

**Question:** introducing a new channel must populate its hash for *all existing rows* without bumping `_gold_updated_at`, without change-log rows, and without any outbound emission.

**Mechanism:**

1. The gold config holds a **channel registry** — the list of channels already bootstrapped.
2. At run start, the transform diffs the registry against channels discovered in `master.json`. A new tag triggers the **bootstrap step before** any incremental work:

   ```sql
   UPDATE glue_catalog.gold_product.product
   SET _channel_hashes = map_concat(_channel_hashes, map('datafeed', <hash per D4 spec>))
   -- _gold_updated_at deliberately absent from SET: it does not move
   ```

3. Zero rows are appended to the change log. The registry is updated; the new channel's publish cursor is seeded to *now*.
4. The channel's initial dataset comes from the D8 full-load pattern (snapshot export projected to the channel's attributes) — never from a synthetic event storm.

Because the backfill precedes the incremental phase, the comparison logic never encounters a missing hash entry.

**Escape hatch:** `bootstrap_mode: silent | emit` per introduction, default `silent`. `emit` appends `op = 'BOOTSTRAP'` rows (`changed_channels = [new channel]`) for consumers that *want* to be seeded through the channel itself.

**Cost note:** Iceberg is copy-on-write — the backfill rewrites the table's files once per new channel. Logically only the map entry changes; the Iceberg snapshot log records the operation (infrastructure audit trail: yes; business change history: no).

**`_gold_updated_at` semantics (normative):** it moves **only** when the canonical hash changes — i.e. when business data changed. Framework maintenance (bootstraps, hash-spec migrations, rebuilds) never touches it.

---

## D8 — Outbound contract: per-channel cursors, isolated replay

Publishers (future: API feed, Autobahn eventing per ADR-0001 §ADR-9, partner data feeds) never compare hashes — the verdict is already materialized in the change log. The contract:

- **Incremental consumption:**

  ```sql
  SELECT c.*
  FROM   gold_product.product_changes c
  WHERE  array_contains(c.changed_channels, 'event')
    AND  c._gold_updated_at > :publish_cursor_event
  ```

  then join back to `product` for the current payload, project the channel's attributes (from the published tier subset schema), emit, advance the cursor. Cursors live per channel in the gold config (`publish_cursors` map) — a new channel is a new map entry.

- **Full load / new-consumer bootstrap:** read `product` directly (it *is* current state), projected to the channel's attributes; set the cursor to the read snapshot's timestamp. Iceberg reads are snapshot-isolated — zero impact on the transform or on other channels.
- **Replay from a date:** reset that channel's cursor to the date and let it re-consume. Safe by construction under D6's at-least-once + dedupe.
- **Point-in-time entity state:** Iceberg time-travel (`FOR TIMESTAMP AS OF`) on `product`, bounded by snapshot retention (D9).

No shared state exists between channels; no replay, full load, or new channel can affect another channel or the load job.

---

## D9 — Retention: two explicit, decoupled knobs

| Knob | Governs | Default |
|---|---|---|
| `retention.changes_days` | how far back **replay-from-date** works (change-log partition expiry) | 365 |
| `retention.snapshot_days` | how far back **time-travel full images** work (Iceberg snapshot expiry on `product`) | 14 |

Change-log rows are narrow (no payloads), so long retention is cheap. Snapshots are heavy, so they expire aggressively. Splitting the knobs gives long replay windows without hoarding snapshots. Both are config, surfaced in the gold pipeline item.

---

## D10 — Relax the concentric tier invariant

ADR-0001 §ADR-5 enforced `event ⊂ message ⊂ api` in CI. A fourth channel (e.g. a partner data feed) may need an arbitrary attribute mix that fits no strict hierarchy — and the D4 hash mechanism works on arbitrary subsets anyway.

**New CI rule:** every channel's attribute set is any subset of the master; the reserved `canonical` channel always means *all properties*. The `gold-schemas.yml` concentric check is replaced by: (i) every `x-channels` value is a known channel name or a new one being introduced, (ii) `canonical` must not be tagged explicitly (it is implicit), (iii) each published tier subset regenerates from the master (unchanged machinery).

---

## D11 — 1:many attributes: sorted nested arrays

The Product entity is a **single table**: scalars (`STRING`, `BIGINT`, …) for 1:1 attributes; `ARRAY<STRUCT<…>>` columns for 1:many associations (e.g. `bom_components`). Iceberg and Athena both support nested types.

**Normative rule:** every array attribute **must declare a sort key** in the manifest (`array_sort_keys`), and the mapping SQL must apply it (`sort_array` / `ORDER BY` inside collect). Without deterministic ordering, identical logical data can serialize differently and D4 hashes would see phantom changes. CI lints that every `ARRAY`-typed master property has a declared sort key.

Adding a column of either kind remains one PR: `master.json` property (+ `x-channels` tags) + `canonical_lineage` entry + `output_columns` + SQL expression. No Glue code, no manual DDL (the MERGE schema-evolves additively; the runtime conformance check enforces agreement).

---

## D12 — Attribute lifecycle: config-only model change + one-time backfill

**Question:** business requests an additional Product attribute. It must be (a) a pure configuration change to the data model and (b) a one-time load of that column for **all existing rows** — without disturbing incremental flows or outbound channels.

**Why a backfill is structurally mandatory, not optional:** two reasons. First, watermark CDC only rebuilds entities whose Silver rows change — an untouched entity would carry `NULL` in the new column indefinitely. Second, a new attribute changes the hash inputs of every channel tagged on it (D4); if stored hashes are not recomputed for **all** rows at introduction time, the next incremental run detects every row as falsely changed and floods the change log. Therefore any change to the materialized property set triggers the backfill, and the backfill always recomputes hashes table-wide.

**Authoring (one PR, no code):**
1. `master.json` — new property with type, description, `x-channels` tags.
2. `canonical_lineage` — the PDH table/column contract (ADR-13, CI-enforced).
3. `manifest.yaml` — `output_columns` entry; if the attribute comes from a **new** Silver source, also a `sources[]` entry with `key_mapping` (the Silver pipeline for that table must already exist — registered through the normal pipeline API); for complex types, an `array_sort_keys` / `column_types` entry.
4. `sql/{entity}.sql` — the expression producing the column.

CI validates conformance + lineage coverage; merge syncs artifacts to S3 as today.

**Detection (mirrors D7):** the gold config holds a **materialized-properties registry** — the list of master properties already present in the Gold table. At run start, the transform diffs `master.json` properties against the registry; a difference triggers the backfill step *before* any incremental work.

**Backfill step:**
1. **Auto-DDL:** `ALTER TABLE product ADD COLUMN <name> <type> AFTER <last business column>` (positioned so the framework metadata columns stay last — UIFPE-268) — type derived from the JSON Schema type (`string→STRING`, `integer→BIGINT`, `number→DECIMAL(38,10)` unless overridden, `array/object→` from manifest `column_types`). Automated DDL from config is still "no structural work by hand".
2. **Full-key rebuild:** run Phase 2 with `affected_keys = all current entity keys` — the mapping SQL (which now includes the new expression) produces complete entities including the new column's values.
3. **Single UPDATE pass:** set the new column **and** the recomputed `_channel_hashes` for every row. Watermarks are untouched.
4. **Registry update:** add the property to `materialized_properties`.

**Timestamp / change-log semantics — `backfill_mode: silent | emit`, default `silent` (mirrors D7):**
- `silent` — no `_gold_updated_at` movement, no change-log rows. The attribute's arrival is announced to consumers by the **schema version bump** in the registry (new tier subsets published by CI); consumers who want the new values proactively use the D8 full-load path.
- `emit` — appends `op = 'BACKFILL'` rows with `changed_channels` = the channels tagged on the new attribute, **only for rows where the new column is non-null** (a NULL backfill is noise), and bumps `_gold_updated_at` on those rows. For consumers who want the new values pushed through their normal channel.

**The same machinery also covers:**
- **Attribute removal** — column is retained physically (Iceberg drop is cheap but consumers may lag; retire in a later cleanup), removed from hash inputs → silent hash recompute.
- **Re-tiering** (`x-channels` edit) — the D4 caveat's "wave of false changes" is avoided by running this as a silent backfill when silence is wanted.
- **`hash_spec_version` bumps** (D4) — same recompute path.

**Cost:** one full-entity rebuild pass + one full-table UPDATE per introduction — the honest price of a backfill, paid once, isolated from incremental runs. Batch multiple new attributes in one PR to pay it once.

---

## Table designs

### `gold_product.product` — the entity table (v-next)

```sql
CREATE TABLE glue_catalog.gold_product.product (
  -- ── business attributes (governed by master.json; v1 set shown, grows by PR) ──
  inventory_item_id  BIGINT       COMMENT 'MTL_SYSTEM_ITEMS_B.INVENTORY_ITEM_ID — natural key pt 1',
  org_id             BIGINT       COMMENT 'MTL_SYSTEM_ITEMS_B.ORGANIZATION_ID — natural key pt 2',
  product_id         STRING       COMMENT 'MTL_SYSTEM_ITEMS_B.SEGMENT1',
  isbn10             STRING,
  isbn13             STRING,
  -- example v2 1:many attribute (shape, not commitment):
  -- bom_components  ARRAY<STRUCT<component_item_id: BIGINT,
  --                              quantity:          DECIMAL(20,5),
  --                              effective_from:    TIMESTAMP>>
  --                 COMMENT 'sorted by component_item_id (D11)',

  -- ── framework metadata (never in master.json; excluded from conformance check) ──
  _channel_hashes    MAP<STRING, STRING>
                     COMMENT 'channel → SHA-256 per D4; key "canonical" = all attrs, gates MERGE',
  _gold_updated_at   TIMESTAMP
                     COMMENT 'moves ONLY on canonical-hash change (D7); business freshness',
  _gold_run_id       STRING
                     COMMENT 'Glue run that last materially changed this row'
)
USING iceberg
LOCATION 's3://eds-{env}-gold/product/'
TBLPROPERTIES (
  'format-version'                = '2',
  'write.merge.mode'              = 'merge-on-read',   -- MERGE-heavy table; compact async
  'write.parquet.compression-codec' = 'snappy'
);
-- Grain: 1 row per (inventory_item_id, org_id). Unpartitioned at current volume
-- (~low millions of rows); introduce bucket(N, inventory_item_id) if MERGE times grow.
```

### `gold_product.product_changes` — append-only change log

```sql
CREATE TABLE glue_catalog.gold_product.product_changes (
  inventory_item_id  BIGINT,
  org_id             BIGINT,
  op                 STRING         COMMENT 'INSERT | UPDATE | DELETE | BOOTSTRAP',
  changed_channels   ARRAY<STRING>  COMMENT 'channels whose hash moved this run (D4/D5)',
  run_id             STRING         COMMENT 'Glue JOB_RUN_ID — dedupe key with the PK (D6)',
  _gold_updated_at   TIMESTAMP      COMMENT 'when the change was materialized',
  change_date        DATE           COMMENT 'partition column = date(_gold_updated_at)'
)
USING iceberg
LOCATION 's3://eds-{env}-gold/product_changes/'
PARTITIONED BY (change_date)
TBLPROPERTIES ('format-version' = '2');
-- Append-only; no payloads (join to product for current state; time-travel for images).
-- Expired by retention.changes_days via partition drop (D9).
```

### Gold pipeline config — DynamoDB additions (delta over the existing item)

```jsonc
{
  // ── existing fields unchanged: gold_id, schema, mapping, sources, target, reconcile ──

  "watermark": {                             // D2 — per-source, keyed by manifest alias
    "last_processed_silver_updated_at": {    // (replaces the legacy scalar; see D2 migration note)
      "items":     "2026-07-15T22:00:11Z",
      "items_ext": "2026-07-15T21:30:47Z"
    }
  },
  "channels": {                              // D7 registry — channels already bootstrapped
    "registered": ["event", "message", "api"],
    "bootstrap_mode": "silent"               // silent | emit — applies to next introduction
  },
  "materialized_properties": [               // D12 registry — master properties already in the table
    "inventory_item_id", "product_id", "org_id", "isbn10", "isbn13"
  ],
  "backfill_mode": "silent",                 // D12 — silent | emit, applies to next attribute intro
  "publish_cursors": {                       // D8 — one entry per channel, ISO-8601
    "event":   "2026-07-16T00:00:00Z",
    "message": "2026-07-16T00:00:00Z",
    "api":     "2026-07-16T00:00:00Z"
  },
  "hash_spec_version": 1,                    // D4 — bump triggers silent re-backfill
  "retention": {                             // D9
    "changes_days":  365,
    "snapshot_days": 14
  },
  "glue": {
    "shuffle_partitions": 8,
    "max_affected_fraction": 0.5             // D1 guardrail
  }
}
```

### Mapping manifest — additions (delta over `schemas/mapping/product/v1/manifest.yaml`)

```yaml
sources:
  - alias: items
    silver_table: silver_product.mtl_system_items_b
    watermark_col: _silver_updated_at
    is_source_of_truth: true
    sot_pk_mapping:                  # unchanged — reconcile contract
      inventory_item_id: "INVENTORY_ITEM_ID"
      org_id:            "ORGANIZATION_ID"
    key_mapping:                     # NEW (D1) — changed row → affected Gold key(s)
      inventory_item_id: "INVENTORY_ITEM_ID"
      org_id:            "ORGANIZATION_ID"

  - alias: items_ext
    silver_table: silver_product.ego_mtl_sy_items_ext_b
    watermark_col: _silver_updated_at
    key_mapping:                     # item-level source: binds a PK SUBSET →
      inventory_item_id: "INVENTORY_ITEM_ID"   # fans out to all orgs for the item (D1)

array_sort_keys: {}                  # NEW (D11) — required entry per ARRAY-typed
                                     # master property, e.g. bom_components: [component_item_id]
```

---

## Runtime flow (one transform run)

```
0. Load config + master.json + manifest + SQL from DynamoDB/S3
1. Maintenance diff (D7/D12), before any incremental work:
      ├─ channels: x-channels ∪ {canonical}  vs  channels.registered
      │     └─ new channel? → silent hash backfill (map_concat), register, seed cursor
      └─ attributes: master.json properties  vs  materialized_properties
            └─ new attribute? → auto-DDL ADD COLUMN, full-key rebuild,
               UPDATE new column + recompute ALL hashes, register
               (timestamp/log per backfill_mode; watermarks untouched)
2. Phase 1 (D1): per source, scan watermark_col > watermark[alias]   (D2: per-source)
      └─ project via key_mapping → affected_keys (union, dedup)
      └─ affected_keys empty? → SUCCEEDED (no-op), stop
3. Phase 2 (D1): unfiltered temp views; driving source semi-joined to affected_keys
      └─ run mapping SQL → rebuilt entities
      └─ validate columns vs master (existing conformance check)
      └─ compute _channel_hashes per D4 spec
4. Diff (D4/D5): LEFT JOIN rebuilt → current gold on PK
      └─ changed_channels per row; drop rows where canonical hash unchanged
5. Append surviving rows' verdicts to product_changes            (D6 step 1)
6. MERGE INTO product gated on canonical hash                     (D6 step 2)
7. Advance per-source watermarks + last_run metadata in DynamoDB  (D6 step 3, one update)
8. Metrics: affected_keys, rows_rebuilt, rows_written, per-channel change counts
```

---

## CI changes (`gold-schemas.yml`)

- Replace the concentric-invariant check with the D10 subset rules.
- Lint: every `ARRAY`-typed master property has an `array_sort_keys` entry (D11).
- Lint: every source in the manifest declares `key_mapping` **or** `lookup_only: true` (never both, and never `lookup_only` on the source-of-truth); every `key_mapping` key ∈ `target.primary_key_cols` (D1).
- Existing checks unchanged: lineage coverage (ADR-13), conformance fixtures, subset generation.
- *(Pre-existing, tracked elsewhere: the workflow is keyed on `main`, which does not exist on origin — the branch mismatch must be fixed for any of this to gate.)*

---

## Explicitly out of scope (unchanged deferrals)

- Autobahn publisher implementation (ADR-0001 §ADR-9) — this ADR defines the *contract* it will consume (D8), not the publisher.
- Pull API and self-service RETL surfaces.
- Expanding the Product attribute set / additional source tables (ADR-0001 §ADR-12 v2) — the model here is attribute-agnostic; scope of attributes is a separate, parallel PR stream.
- SCD2 (rejected again per D5; revisit trigger unchanged from ADR-0001 §ADR-8).

---

## Revisit triggers

| Trigger | Affected decision |
|---|---|
| A Silver source cannot guarantee `_silver_updated_at` fidelity, or sub-hourly Gold latency is required | D2 (Iceberg changelog) |
| A channel needs attribute-level change payloads ("which fields changed"), not row-level | D4 (option c) |
| Change-log volume or publisher lag makes cursor scans slow at 365-day retention | D9 (tiered log storage) |
| A consumer requires exactly-once delivery end-to-end | D6 (transactional outbox pattern) |
| ≥3 Gold entities exist | Generalize table DDLs into a `create_gold_entity` helper in the transform |
