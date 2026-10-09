# Gold entity contract — Program v1

`master.json` is the **primary artifact**: it defines what `gold_program.program`
IS — its attributes, their types, and which consumer feeds carry them. The
pipeline consumes it directly at runtime (channel hashes are derived from the
`x-channels` tags, and new attributes trigger auto-DDL), so editing it changes
the table and the contracts published to consumers.

`../../../mapping/program/v1/program.mapping.json` is the companion: **how** each attribute is
populated. Structure lives here; rules live there.

| | |
|---|---|
| Table | `gold_program.program` (`uif_echo_{env}_gold_program.program`) |
| Grain | one row per `(inventoryItemId, organizationId)` |
| Population | PDH items with `SEGMENT1 LIKE 'P2%'` at `ORGANIZATION_ID = 82` |
| Schema dialect | JSON Schema **draft-07** (matching product; the central meta-schema and event artifacts are 2020-12 — a known inconsistency recorded in ADR-0005) |
| Lifecycle | `draft` — registry publishing is deliberately deferred |

## Primary key

Declared **on this artifact**, in `metadata.primaryKey`:

| Gold column | From |
|---|---|
| `inventoryItemId` | `MTL_SYSTEM_ITEMS_B.INVENTORY_ITEM_ID` |
| `organizationId` | `MTL_SYSTEM_ITEMS_B.ORGANIZATION_ID` |

Program is the **first entity to declare its key here** rather than leaving it
to be inferred from the intake mapping. `masterSchema.ts primaryKeyOf()` already
prefers `metadata.primaryKey` and falls back to the mapping's `entityKey`, so
this artifact is now self-describing. Note `required` is a JSON-Schema presence
rule, **not** a key declaration — it lists the key columns plus `ppid`.

## Shape — 13 properties

**Key (2)** — `inventoryItemId`, `organizationId`. Non-nullable integers,
`x-channels: []`: stored on every row, carried by no consumer feed. Same
treatment product gives its key columns.

**Scalar (9)** — `ppid` (non-nullable), `description` (non-nullable),
`longDescription`, `relevanceValue` (integer), `programFamilyCatalogCode`, `coverImageLargeUrl`,
`programOwnerName`, `inventoryItemStatus`, `parentProduct`.

`parentProduct` is the only DERIVED attribute here — three hops from the
program, with the qualifying test switching on the product's catalog group.
See the `parent_prod` CTE in the mapping SQL for what "parent" means, and
the intake mapping's `notes` for why the intake contract cannot express it.

**Arrays (2)**
- `associatedProducts` — value array (`ARRAY<STRING>`), the PPIDs of products in
  the program, sorted by value.
- `relatedPrograms` — struct array of `{programPpid, relationshipType}`, sorted
  by `associatedPrograms`.

Both are **`[]` when empty, never NULL** (`COALESCE(x, array())` in the mapping
SQL). That is a permanent commitment: switching to NULL later changes every
consumer's channel hashes.

## Consumer tiers

All 11 non-key properties carry **event + message + api**. The tiers are
therefore identical in content today, which satisfies the concentric invariant
(`event ⊆ message ⊆ api`) trivially. Two consequences worth stating rather than
discovering:

- No tier exposes the primary key, so a consumer's join key is `ppid` (present
  in every tier), not `inventoryItemId`. Product has the same property, flagged
  in ADR-0005.
- Equal tiers mean tier subsets add no differentiation yet. If a business
  reason emerges to slim the event feed, re-tier here — it is one edit in this
  file, and the subsets regenerate.

## Breaking-change policy

Additive changes — a new property, or widening a tier — are safe: the pipeline
auto-DDLs new columns and backfills them silently (no `_gold_updated_at` bump,
no change-log rows) per ADR-0003.

These are **breaking** and need a consumer conversation plus a version bump:

- removing a property, or renaming one (rename = remove + add);
- narrowing a type, or making a nullable property non-nullable;
- **removing a tier tag** from a property — it disappears from that feed;
- changing an array's element shape, or its sort key (both move channel hashes);
- switching an empty array's representation between `[]` and `NULL`.

Reordering properties is **not** cosmetic: this file's property order drives the
manifest's `output_columns` and therefore the physical column order.

## Changelog

| Date | Change |
|---|---|
| 2026-08-12 | `parentProduct` added (13th property). Derived over three hops with a per-catalog-group parent test; collapsed with `min()` so the value is deterministic and the canonical hash cannot move between identical runs. Added to `schedule.refresh.undetectable_columns` — it walks the same reverse-keyed group-1421 link as `associatedProducts`. |
| 2026-08-11 | v1 created. 12 properties generated from `program-gold-attribute-mapping-template.xlsx`, validated against the intake schema and this domain's `qe/` gates. First entity to declare `metadata.primaryKey`. Registry publish deferred (`lifecycle: draft`). |

## Known gaps

1. ~~**`parentProduct` is not in v1.**~~ **RESOLVED 2026-08-12.** Added after
   the mapping owner confirmed the full query and the business meaning: the
   two UNION branches are two different parent tests, one per catalog group
   (digital = a sibling names it as parentPPID; non-digital = its own
   ParentISBN13 equals its own ISBN13). Needed no new sources — every join
   uses an alias already registered. The open item it leaves behind is a
   FRAMEWORK one, not a program one: the intake contract cannot express a
   multi-hop derivation, so this column is PLAN rather than AUTO and its SQL
   is hand-authored.
2. **`coverImageLargeUrl`'s site-relative branches are unconfirmed.** The `/P` DAM
   prefix and the DAM asset space are **confirmed** (mapping owner, 2026-08-11 —
   program images live in product's asset space, so the inherited host and GUID
   are correct). The `/coverimage%` and `/bigcovers%` branches are inherited
   from product and not yet observed in ATTR_GROUP_ID 1423; the owner expects
   they may occur. They are kept because an unmatched branch is inert while
   dropping them could lose real values — confirm against the first load, since
   product's equivalent rule had to be corrected from row counts.
3. **`associatedProducts` drifts between refreshes** — its change-detection is
   inexpressible in ADR-0003's per-source `key_mapping`. Mitigated by a nightly
   targeted refresh; see the domain `CLAUDE.md`.
