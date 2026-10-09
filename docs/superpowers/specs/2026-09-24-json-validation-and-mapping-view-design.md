# JSON metadata validation and the mapping view — design

**Date:** 2026-09-24
**Status:** Approved for implementation
**Decider:** Rajnivas Karunakaran (repository owner)
**Capabilities touched:** Schema (primary) · Data · Delivery
**Stack:** `stacks/apicurio` (RELAY — ADR-0009, ADR-0010)
**Branch:** `feature/UIFPE-507-schema-registry-ux-redesign`
**Issues:** [UIFPE-597](https://pearson.atlassian.net/browse/UIFPE-597) — §1, JSON metadata
validation (epic UIFPE-304, Governance BFF) · [UIFPE-598](https://pearson.atlassian.net/browse/UIFPE-598)
— §2–§6, the mapping view (epic UIFPE-305, Governance UI)

Both are new stories rather than additions to UIFPE-507, which is already **Ready for QA**;
hanging more work off it would have moved a QA-ready story backwards. The branch stays
`feature/UIFPE-507-…` because that is where the redesign lives.

---

## What this is

Two changes to the schema registry, independent of each other but landing together
because they touch the same pair of ideas: what the registry accepts, and what it shows.

**Feature 1** removes a hard stop. `metadata_validator.py` refuses every schema that
is not Avro, reporting `schema_type_unsupported`. Every gold `master.json` in the
repository — product, price, program, stock, marketing_copy — is a JSON Schema carrying
exactly the metadata block Avro carries, and every one of them is currently reported as
non-compliant for the sole reason that it is not Avro. This opens the gate for JSON.

**Feature 2** adds a reading. A **mapping** artifact (`product.product.mapping`, live in
dev) is a Silver→Gold attribute mapping conforming to
`stacks/apicurio/standards/gold-attribute-mapping.schema.json`. It is a completely
different shape from a schema body: no `fields`, no `properties`, no metadata block —
instead `domain`, `table`, `drivingTable`, `entityKey`, `silverTables`, `rowGroups`,
`transforms`, `batch`, and a `columns[]` array whose entries come in three kinds. The
registry's three existing readings of a body (Fields, Code, Graph) all render it as
meaningless nesting. This gives it the reading it needs: the one people already use,
the `*-gold-attribute-mapping-template.xlsx` workbook, on screen and reading from the
registered artifact.

A PM or UX reader needs §1 and §4 — the rules that decide compliance, and the layout.
§5 and §6 are for the FDE.

---

## 1. Feature 1 — JSON metadata validation

### 1.1 What is there now

`src/app/services/metadata_validator.py:47` gates on schema type and returns early:

```python
if schema_type != "AVRO":
    failures.append(ValidationFailure(
        rule="schema_type_unsupported",
        message=f"Metadata validation is only implemented for AVRO; got {schema_type}.",
    ))
```

Everything downstream of that gate is already body-shape-agnostic — `metadata.owner.service`,
`metadata.domain`, `metadata.dataClassification`, `metadata.lifecycle` and the
body/`properties` agreement check all read the metadata block, which JSON Schema bodies
carry identically. **One rule is not agnostic:** `pii_field_not_found` (line 165) resolves each
`piiFields` path's first dot-segment against `body["fields"][].name`, which is Avro's
field list. A JSON Schema body carries `body["properties"]` instead.

### 1.2 The gate splits three ways

| Body | Behaviour |
|---|---|
| `AVRO` | Unchanged. `piiFields` resolve against `body.fields[].name`. |
| `JSON`, body is a JSON Schema | Full metadata-block battery. `piiFields` resolve against top-level `body.properties` keys. |
| `JSON`, body is a **mapping** | Metadata-block rules skipped. Three mapping rules run instead (§1.4). |
| Anything else | `schema_type_unsupported`, exactly as today. |

`PROTOBUF`, `GRAPHQL`, `XSD`, `OPENAPI` and `ASYNCAPI` keep reporting
`schema_type_unsupported`. Opening those is a separate decision with a separate parser
behind it; this change does not pretend otherwise.

### 1.3 `piiFields` resolution

One function serves both body shapes, so they cannot drift:

```python
def _top_level_field_names(body: dict) -> set[str]:
    """Top-level field names, whichever shape declares them.

    Avro puts them in `fields[].name`; JSON Schema puts them in `properties`.
    A body is never both, so the union is safe and there is one rule, not two.
    """
```

A leading `$.` is stripped from each path before the first dot-segment is taken.
`src/schemas/metadata-block.schema.json` gives `"$.customerId"` as its own worked example
for `piiFields`, and that example fails today against an Avro body as well — the `$`
becomes the first dot-segment and matches nothing. Stripping it fixes the documented
example for both shapes.

The rule stays **first-segment only**, matching Avro's existing depth. Walking the full
dotted path through nested `properties` would make JSON Schema strictly stricter than
Avro for the same declaration, and two rules that disagree about the same contract is
worse than one shallow rule.

### 1.4 Mapping artifacts are exempt from the metadata block

Mapping files carry **no top-level `metadata` block** — verified across
`domains/product`, `domains/price`, `domains/program`, `domains/stock` and
`stacks/echo`. None has one. Once the JSON gate opens, every registered mapping artifact
would fail `metadata_block_missing` for a block its own contract never asked it to carry.

So a mapping body is detected and validated against its own contract instead:

| Rule | Fails when |
|---|---|
| `mapping_domain_mismatch` | `domain` ≠ the artifact id's first dot-segment |
| `mapping_registry_group_mismatch` | `registry.group` is present and ≠ `domain` |
| `mapping_columns_missing` | `columns` is absent or empty |

**Deliberately not here:** `sortKey` coverage on nested columns, `pick.partitionBy`
coverage, the prose-transform ban, `{"rule"}` reference resolution, and the `sqlExpr`
allowlist. `standards/gold-attribute-mapping.md` §"Validating a mapping file" places all
of those in CI — `jsonschema` validation plus
`domains/product/qe/test_intake_hygiene.py`. Re-implementing them in the runtime
validator would create a second copy of the contract to keep in step, and the standard
already names which copy is authoritative. The registry validator checks the three
things it alone is positioned to check: that the artifact's registry coordinate, its
declared domain, and its own `registry` block agree.

### 1.5 Detection

```python
def _is_mapping_body(body: dict) -> bool:
    """A gold attribute mapping, by shape (never by artifact name)."""
```

True when the body has `domain` (str) **and** `table` (str) **and** `columns` (list), or
when `$schema` names `gold-attribute-mapping`. Shape, not name: the repository already
carries two naming conventions for the same thing — `product.product.mapping` in the dev
registry and `product-gold-attribute-mapping` in the mapping files' own `registry.artifact`
— and a rule keyed on either one breaks on the other. The UI uses the same predicate
(§3.1), so the two sides of the app agree on what a mapping is.

---

## 2. Feature 2 — the mapping view

### 2.1 Why the workbook is the design

The people who read mappings read
`domains/product/src/schemas/product-gold-attribute-mapping-template.xlsx`. It has four
sheets: Instructions, and one per column kind. Each column kind is a flat table, and the
three tables have different columns because the three kinds carry different information.
That is not incidental — it is the shape of the data, and it is already the shape in the
readers' heads. The view mirrors it.

| Sheet | Columns |
|---|---|
| **1. Scalar Columns** | Gold Column Name · Business Description · Data Type · Source Oracle/PDH Table · Source Column · Source Filter · Transform Logic · Event? · Message? · API? · Nullable? · Notes · Join Grain |
| **2. Conditional Columns** | Gold Column Name *(repeats per branch)* · Business Description *(first branch row only)* · Data Type *(first branch row only)* · Discriminator Table.Column *(first branch row only)* · Branch Condition · Source Table · Source Column · Source Filter · Fallback if branch value is empty? · Event? · Message? · API? · Notes · Join Grain |
| **3. Nested Columns (1-to-many)** | Gold Column Name *(repeats per field)* · Business Description *(first field row only)* · Field Name · Field Type · Source Table · Source Column · Join Key to Item *(first field row only)* · Source Filter · Sort Key? · Event? · Message? · API? · Notes · Join Grain |

Three of those *first-row-only* annotations were missing from the first draft
of this table and were added after checking the workbook itself: `Data Type`,
`Discriminator Table.Column` and `Join Key to Item` are all blank on the
template's continuation rows, because each is a property of the gold column
rather than of the branch or field. They are listed here so the view's
behaviour is specified rather than inherited.

### 2.2 Where the registered JSON is richer than the workbook

The workbook is older than the contract. Five things exist in the JSON with no workbook
column to put them in:

| In the JSON | Status |
|---|---|
| `source.join` — structured `{type, on: [{leftTable, leftColumn, rightColumn}]}` | Replaced the workbook's `Join Grain` (UIFPE-104) |
| `scope` + `rowGroups` — named, reusable row predicates | Replaced `Join Grain: Proliferate` (UIFPE-104) |
| `transforms` — structured `steps` / `cases` / `sqlExpr`, named and referenced | Replaced prose transform text (UIFPE-105) |
| `derivation` — the author's own SELECT plus a `keyedBy` contract | New (ADR-0008) |
| `pick` — declarative ranked pick before array assembly | New (UIFPE-262) |
| `silverTables` / `drivingSilverTable` — env-templated Silver names | New (UIFPE-260, UIFPE-262) |

None of these fits a 14-column row. They go in a per-row expansion (§4.3) and, for the
named shared definitions, in reference sections (§4.4).

`joinGrain` itself is marked **DEPRECATED** in
`gold-attribute-mapping.schema.json` — "Accepted for backward compatibility while old
files migrate; new entries must not carry it." The column is therefore rendered as a
value when a legacy file still carries one and `—` otherwise, with a header note saying
it is superseded by the per-source join. Rendering it as a live column would teach the
deprecated thing to everyone who reads the page.

---

## 3. Architecture

### 3.1 One parser, no backend work

The schema detail endpoint already returns the artifact body — `SchemaDetailRead.content`,
consumed at `SchemaDetail.tsx` as `data.content`. So the whole of Feature 2 is client-side:
parse the body, render three tables. No new endpoint, no new API model, no Apicurio call.

`ui/src/features/schemas/mapping-body.ts` is the new module. It is pure — no React, no
network — which is what makes it the place the tests live:

```ts
export function isMappingBody(value: unknown): boolean
export function parseMappingBody(content: string): ParsedMapping

export interface ParsedMapping {
  isMapping: boolean;
  facts: MappingFacts;          // domain, table, drivingTable, entityKey, registry, batch
  scalar: ScalarRow[];
  conditional: ConditionalRow[]; // one row per branch
  nested: NestedRow[];           // one row per field
  reference: MappingReference;   // rowGroups, transforms, silverTables, entityKey
  value: unknown;                // the parsed body, for the code view
}
```

plus the resolvers the row detail needs: `resolveJoin`, `resolveScope`, `resolveTransform`,
`resolveSilverTable`.

This deliberately mirrors `schema-body.ts`, whose docstring states the governing idea —
*"One parse, three presentations — so a field that appears in the table is the same field
the graph draws, and a search match counts once."* The same holds here: one parse, three
sheets plus a reference section, so a source table named in a row is the same one the
Silver reference resolves.

`isMappingBody` implements the same predicate as the backend's `_is_mapping_body` (§1.5).
The two are in different languages and cannot share code; the spec is the shared
definition, and both sides carry a comment pointing at this section.

### 3.2 Routing

`Mapping` becomes the second entry in `DETAIL_TABS` in
`hooks/use-schema-url-state.ts`, and is added to `URL_DETAIL_TABS` so `?dtab=mapping`
resolves. A new param `mkind=scalar|conditional|nested` holds the sheet, defaulting to
`scalar` — in the URL for the same reason `schemaView` is: a link to a mapping should
reproduce the sheet you were looking at.

Three behaviours follow from a body being a mapping:

1. The **Mapping tab is rendered only for a mapping body.** A tab that is empty for
   twelve of thirteen artifacts is worse than no tab.
2. A mapping body **lands on the Mapping tab** by default, because that is the reading of
   it that means anything.
3. Landing on Mapping is a **default, not a lock**. A `?dtab=` deep link wins, and so
   does a click on another tab — which needs stating, because the two are not the same
   mechanism: `set({ detailTab: 'schema' })` *deletes* the param, since `schema` is the
   URL state's default, so a click leaves the URL untouched and cannot be detected by
   reading it. The page records the click in local state as well.
4. The Schema tab **suppresses Fields and Graph** for a mapping body. Fields would render
   an empty table — `columns` is neither `fields` nor `properties`, so
   `schema-body.ts` finds nothing. Graph would draw 74 anonymous column objects as a tree.
   Code stays, and is the Schema tab's only segment for a mapping. A deep link to
   `?dtab=schema&schemaView=fields` on a mapping gets an empty state naming the reason and
   linking to the Mapping tab, following the precedent already set for an unparseable
   body.

### 3.3 Files

| File | Change |
|---|---|
| `src/app/services/metadata_validator.py` | Feature 1. Split the gate; add `_is_mapping_body`, `_validate_mapping`, `_top_level_field_names`. Rewrite the module docstring, which currently says "Avro only. Protobuf / JSON Schema are unimplemented". |
| `qe/tests/test_metadata_validator.py` | Feature 1 tests (§6.1). |
| `ui/src/features/schemas/mapping-body.ts` | **New.** Parse + resolve. |
| `ui/src/features/schemas/mapping-body.test.ts` | **New.** The bulk of Feature 2's tests. |
| `ui/src/features/schemas/components/SchemaMappingView.tsx` | **New.** Facts band, sheet toggle, search, table, CSV export. |
| `ui/src/features/schemas/components/MappingRowDetail.tsx` | **New.** The row expansion panel. |
| `ui/src/features/schemas/components/MappingReferenceSections.tsx` | **New.** rowGroups / transforms / silverTables / entityKey, collapsible. |
| `ui/src/features/schemas/hooks/use-schema-url-state.ts` | `mapping` tab, `mkind` param, labels. |
| `ui/src/features/schemas/components/SchemaDetail.tsx` | Render and gate the tab; suppress Fields/Graph for a mapping. |
| `ui/src/features/schemas/index.ts` | Exports. |

The view is split across three components rather than one because
`SchemaDetail.tsx` is already 955 lines and `SchemaTable.tsx` 573 — the largest files in
the feature, and the ones that are hardest to change safely. A single
`SchemaMappingView.tsx` carrying the tables, the expansion and the reference sections
would join them. Each of the three has one job and a stated interface.

---

## 4. Layout

```
┌────────┬─────────┬────────────┬──────────┬─────────┐
│ Schema │ Mapping │ Compliance │ Services │ History │
└────────┴─────────┴────────────┴──────────┴─────────┘

 Gold table              Domain     Driving table         Registry
 gold_product.product    product    MTL_SYSTEM_ITEMS_B    product /
                                                          product-gold-attribute-
                                                          mapping · BACKWARD
 Entity key                             Batch
 inventoryItemId ← INVENTORY_ITEM_ID    Reyas Ahamed H · 2026-07-22
 organizationId  ← ORGANIZATION_ID      backfill silent · no new channel

 (Scalar 31)(Conditional 33)(Nested 10)   [ search ] 3 matches   ⧉ ⤓ 🔗 CSV ⛶
 ──────────────────────────────────────────────────────────────────────────────
  Gold Column │ Business Description │ Type │ Source Table │ Src Col │ Filter │ …
 ▸ inventoryItemId │ Oracle internal surrog… │ integer │ MTL_SYSTEM_ITEMS_B │ … │
 ▾ isbn13          │ International Standard… │ string  │ EGO_MTL_SY_ITEMS_… │ … │
   ╭────────────────────────────────────────────────────────────────────────╮
   │ JOIN     LEFT JOIN EGO_MTL_SY_ITEMS_EXT_B                              │
   │            ON MTL_SYSTEM_ITEMS_B.INVENTORY_ITEM_ID                     │
   │             = EGO_MTL_SY_ITEMS_EXT_B.INVENTORY_ITEM_ID                 │
   │          WHERE ATTR_GROUP_ID = 741                                     │
   │ SCOPE    every row — this branch declares no scope                     │
   │ XFORM    pearsonImageUrl · else keep                                   │
   │            startsWith '/A'          → prepend 'https://images.pear…'   │
   │            like '/coverimage%',…    → prepend 'https://images.pear…'   │
   ╰────────────────────────────────────────────────────────────────────────╯
 ──────────────────────────────────────────────────────────────────────────────
 ▸ Row groups (2)      ▸ Transforms (5)      ▸ Entity key (2)
```

The counts are product's actual mapping. It declares no `silverTables` and no
`drivingSilverTable`, so that reference section and the row detail's `SILVER` line are
absent for it — every reference section hides when its collection is empty, rather than
rendering an empty accordion. The section is not dead code: price's mapping declares six
`silverTables` plus a `drivingSilverTable`, and stock's declares a `drivingSilverTable`
alone. Likewise `derivation` appears on three of program's columns and `pick` on one of
price's, and on none of product's — so product alone is not a sufficient fixture for
those two, which §6.2 accounts for.

### 4.1 Facts band

The page's existing `<dl>` facts-band pattern, reading `domain`, `table`, `drivingTable`,
`entityKey` (as `goldColumn ← drivingColumn` pairs, in declared order — it is the gold
table's ordered primary key), `registry` (group / artifact / compatibility), and `batch`
(requestedBy, targetDate, backfillMode, newChannel). Always visible, like the schema
page's band above it.

### 4.2 Sheet toggle, search, tables

**Sheet toggle** — the same pill component as Fields/Code/Graph, in workbook sheet order,
with counts in the label so you know the size of a sheet before opening it.

**Search** — *filters* rows rather than only tinting matches. This follows the precedent
`SchemaFieldsView.tsx` documents from the redesign: highlighting alone "was enough for a
five-field Avro record and useless on `product.gold-schema`, where a match can be the
fortieth of 63 rows and off-screen: the count said '1 match' and the screen showed no
sign of it." Product's mapping is 74 columns and fans out to more rows than that once
branches and fields are flattened, so the same failure is certain. Matched runs also
carry the amber tint, via the existing `Highlight` component. Searched fields: gold
column, business description, source table, source column, transform rule name, notes.

**Tables** — the workbook's column order verbatim, per sheet. Sticky header row, sticky
gold-column first column, horizontal scroll for the tail. Pane height caps at the
existing `PANE_MAX_H` (570px) and scrolls past it — the same constant Fields and Code
use, because all three sit in the same slot on the same page. Conditional rows repeat the
gold column per branch with the description on the first row only; nested rows repeat per
field. Channels render `Y` / `–`.

Transform Logic renders as: `—` for null · a **rule-name chip** for `{"rule": "x"}` ·
an **`inline`** chip for an inline `transformDef` · an amber **`SQL`** chip for an
`sqlExpr`, because CI flags every use of one for engineering review and the reader should
see that on the row rather than only in the expansion.

### 4.3 Row detail

A chevron on each row expands a panel beneath it:

| Section | Content | Shown for |
|---|---|---|
| `JOIN` | The resolved ON clause — `LEFT JOIN <table> ON <left>.<col> = <table>.<col>`, chained pairs each on a line, plus `WHERE <filter>`. "Driving table — no join" when the source *is* `drivingTable`. | any row with a source |
| `SILVER` | The env-templated fully qualified Silver name for the row's source table, from `silverTables` or `drivingSilverTable`. | when declared |
| `SCOPE` | The branch's `scope`: the named `rowGroups` entry with its predicate resolved, or the inline predicate, or `op: "else"` as "every row no sibling branch's scope claimed", or "every row — this branch declares no scope" when `scope` is absent. Distinct from the branch's `condition`, which is the discriminator value and is already a table column — the schema is explicit that row-group qualifiers do not belong in `condition`. | conditional |
| `XFORM` | The transform resolved: `steps` as an ordered pipeline, `cases` as a guarded when/then list with its `else`, `sqlExpr` verbatim in mono with the review flag. | when present |
| `DERIVATION` | The author's SQL in mono, the `keyedBy` contract as `queryColumn → entityColumn`, and the validator-computed `reads`. | when present |
| `PICK` | `partitionBy` fields and the ordered `orderBy` expressions with directions. | nested, when present |

### 4.4 Reference sections

Below the tables, collapsed by default, each cited by name from the rows:

- **Row groups** — every `rowGroups` entry with its predicate, its description, and
  complements shown as "complement of *base*" beneath the base they follow.
- **Transforms** — every `transforms` entry resolved as in §4.3, with the count of
  columns referencing it.
- **Silver tables** — `drivingSilverTable` and every `silverTables` entry, bare name →
  templated fully qualified name.
- **Entity key** — the ordered `goldColumn ← drivingColumn` pairs. Repeated from the facts
  band deliberately: the band is the summary, this is the reference beside the other
  three.

These exist because a named definition is a *single* source of truth and resolving it
inline on twelve rows shows it twelve times and nowhere once. The rows resolve it so you
never have to leave the row; the sections hold it so you can read the set.

### 4.5 Toolbar

Copy body, download, copy link and fullscreen — the same four the Schema tab carries, so
the two tabs behave alike — plus **CSV** export of the current sheet, with the workbook's
own column headers. The view replaces a workbook people open in Excel, and some of what
they do there (pivot, annotate, send to a non-user) the page will not do. Exporting the
sheet they are looking at, with the headers they know, is the honest answer to that rather
than pretending the page subsumes Excel.

### 4.6 Brand

Plus Jakarta Sans and the Pearson tokens already in the stack. Amethyst `#512EAB` for
source identifiers (table and column names), matching the Monaco theme's key colour so an
identifier reads the same in the table and in the code view. Amber tint for search hits
and for the `sqlExpr` review flag — the tint already means "look here" on this page. Mist
Purple `#EDECF6` for the row-detail panel ground, so an expanded row is visibly inside its
row rather than a new block.

---

## 5. Error handling

| Case | Behaviour |
|---|---|
| Body is not JSON | `parseMappingBody` returns `isMapping: false`. Tab does not render. Existing behaviour for the Schema tab is untouched. |
| Body is JSON but not a mapping | Same. |
| Mapping body missing `columns` | Tab renders; each sheet shows its empty state. The Compliance tab reports `mapping_columns_missing` (§1.4) — the finding belongs there, not as a rendering error. |
| A sheet has no rows | Per-sheet empty state ("No conditional columns in this mapping"), not a blank pane. |
| `scope.group` names a `rowGroups` entry that does not exist | Row detail shows the raw group name with an unresolved marker. The registry shows what the artifact says; CI's intake hygiene suite is what rejects it. |
| `transform.rule` does not resolve | Same treatment. |
| `source.join.leftTable` names an unjoined table | Rendered as declared. The compiler's problem, not the viewer's. |
| A legacy file still carries `joinGrain` | Value rendered in the column, with the deprecation note in the header (§2.2). |

The governing rule: **the view reports what the artifact declares, and does not pass
judgement.** A malformed mapping that reached the registry is a CI failure and a
compliance finding; a viewer that hides it makes it harder to see, not less broken.

---

## 6. Testing

TDD for both features — test first, watch it fail, then implement.

### 6.1 Feature 1 — `qe/tests/test_metadata_validator.py`

Extends the existing 243-line suite, whose fixtures and CMDB stub are reused.

- A JSON Schema body with a complete metadata block passes with `schema_type="JSON"`.
- `piiFields` resolve against `properties` keys — present passes, absent yields
  `pii_field_not_found`.
- A `$.`-prefixed path resolves, for both a JSON Schema body and an Avro body.
- Each of the four metadata-block rule families still fires for a JSON Schema body
  (owner, domain, classification, lifecycle) — the battery is shared, so this proves the
  gate opened rather than the rules being skipped.
- `properties_mismatch` fires for a JSON Schema body.
- A mapping body (no metadata block) passes clean.
- `mapping_domain_mismatch`, `mapping_registry_group_mismatch` and
  `mapping_columns_missing` each fire on a crafted body.
- A mapping body does **not** report `metadata_block_missing`.
- `test_non_avro_returns_unsupported_failure` (PROTOBUF) stays green unmodified — the
  regression guard that this change did not open more than it meant to.

### 6.2 Feature 2 — `ui/src/features/schemas/mapping-body.test.ts`

The bulk of the testing, because the parser is where the logic is. Bodies are built
inline with `JSON.stringify`, following the convention `schema-body.test.ts` already uses
(there are no fixture files in this UI tree, and introducing one for this would be the
odd case out).

- `isMappingBody` — true for `domain`+`table`+`columns`; true for a `$schema` naming
  `gold-attribute-mapping`; false for an Avro record, a JSON Schema master, an OpenAPI
  document, a non-object, and unparseable text.
- Facts extraction, including an `entityKey` of two pairs in declared order.
- Scalar flattening — one row per column.
- Conditional flattening — one row per branch; description on the first row only;
  discriminator rendered `Table.Column`.
- Nested flattening — one row per field; description on the first row only;
  `sortKey` surfaced.
- `resolveJoin` — a single ON pair; multiple pairs; `inner` vs default `left`; a chained
  `leftTable` that is another source; "no join" when the source is the driving table;
  a filter appended.
- `resolveTransform` — `null`; a `{"rule"}` that resolves; a `{"rule"}` that does not;
  an inline `steps` pipeline; `cases` with an explicit `else` and with the default
  `keep`; `sqlExpr` flagged for review.
- `resolveScope` — a `group` reference; a complement group resolved through its base;
  an inline predicate; `op: "else"`.
- `resolveSilverTable` — from `silverTables`; from `drivingSilverTable`; absent.
- One wide body derived from the real `domains/product/src/schemas/mapping/product/v1/product.mapping.json`
  (74 columns — 31 scalar, 33 conditional, 10 nested, with real joins, transforms and row
  groups) asserting the three row counts after flattening. This is the case that catches
  a flattener that works on a hand-built two-column body and not on the artifact that
  actually exists.
- Product's mapping declares **no** `silverTables`, `drivingSilverTable`, `derivation` or
  `pick`, so it cannot be the only wide fixture. `derivation` is exercised against a body
  shaped like program's (three derived columns) and `pick` against one shaped like
  price's (one picked nested column, six `silverTables`, a `drivingSilverTable`).

### 6.3 Feature 2 — component tests

- `SchemaMappingView.test.tsx` — sheet toggle switches tables and updates the URL;
  search filters rows and reports a count; a row expands and collapses; CSV export
  produces the workbook's headers for the current sheet; per-sheet empty states.
- `SchemaDetail.test.tsx` — the Mapping tab appears for a mapping body and not for an
  Avro or JSON Schema body; a mapping body lands on the Mapping tab; Fields and Graph are
  suppressed for a mapping body; `?dtab=schema&schemaView=fields` on a mapping shows the empty
  state and a link to the Mapping tab.
- `use-schema-url-state.test.ts` — `?dtab=mapping` parses; `?mkind=` parses and falls
  back to `scalar` for an unknown value, matching the existing `parseEnum` behaviour.

### 6.4 Gates

`ruff` and `pytest` for the backend, `eslint`/`prettier` and `vitest` for the UI — run as
Python modules where the binary is absent (`python3 -m ruff`), which is CI's exact pin.

---

## 7. What this does not do

- **Does not register the mapping artifacts.** `product.product.mapping` is live in dev;
  nothing in this repository registers mapping files, and no manifest or script references
  `*-gold-attribute-mapping`. The view reads whatever the registry serves. Bringing the
  other domains' mapping files into the registry is separate work.
- **Does not open PROTOBUF, GRAPHQL, XSD, OPENAPI or ASYNCAPI** to metadata validation.
- **Does not duplicate CI's mapping validation.** §1.4 states which checks live where and
  why.
- **Does not show compiled SQL or a source→gold lineage graph.** The `derivation` block's
  SQL is shown because the artifact carries it; nothing is compiled or generated here.
- **Does not touch the `Join Grain` column's deprecation.** The view renders it for
  legacy files and marks it superseded; migrating the files is UIFPE-104's business.

---

## 8. References

- `stacks/apicurio/standards/gold-attribute-mapping.schema.json` — the mapping contract
- `stacks/apicurio/standards/gold-attribute-mapping.md` — the worked standard, incl. which
  validation lives in CI
- `stacks/apicurio/src/schemas/metadata-block.schema.json` — the metadata block contract
- `domains/product/src/schemas/product-gold-attribute-mapping-template.xlsx` — the workbook
  this view mirrors
- `domains/product/src/schemas/mapping/product/v1/product.mapping.json` — the reference
  artifact (74 columns, all three kinds)
- ADR-0001 — the schema body is authoritative; Apicurio labels are a derived index
- ADR-0008 — `derivation`: the query is the contract
- UIFPE-104 — structured joins and row groups replace `joinGrain`
- UIFPE-105 — structured transforms replace prose
- UIFPE-260, UIFPE-262 — `drivingSilverTable`, `silverTables`, `pick`, multi-key sorts
