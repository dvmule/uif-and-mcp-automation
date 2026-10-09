# Gold Attribute Mapping — worked standard

This document defines how a domain declares which source attributes become columns in its Gold table in the medallion architecture (Bronze → Silver → Gold). A domain team writes one JSON mapping file; engineering turns it into master schema, lineage, and mapping SQL, and the pipeline auto-creates the Gold columns and backfills existing rows — no manual DDL. The mapping file replaces the Excel workbook previously used for attribute submissions, so a request that used to be a spreadsheet hand-off is now a reviewable, validated, config-only change.

> **Provenance.** This standard was migrated from `pearson-dnt-coe/gip-schema` (commit `917e3cf`, 2026-07-27). **This repository is now the source of truth**; treat any remaining copy in `gip-schema` as historical. The legacy workbook (`product-gold-attribute-mapping-template.xlsx`) was deliberately not migrated — the JSON mapping is the submission format.

## The contract

| Artifact | Location | Owner |
|---|---|---|
| The reusable JSON Schema (2020-12) | [`gold-attribute-mapping.schema.json`](gold-attribute-mapping.schema.json) — central, shared by all domains | Central (Schema capability) |
| One mapping file per gold-table domain | `domains/<domain>/src/schemas/mapping/<entity>/v1/<domain>.mapping.json` (e.g. [`domains/product/src/schemas/mapping/product/v1/product.mapping.json`](../../domains/product/src/schemas/mapping/product/v1/product.mapping.json) for `gold_product.product`; co-located with the compiled manifest + SQL since 2026-08-17) | Local (domain FDE) |

Adding a new domain means adding a new mapping file against the existing schema; it should not require schema changes unless a genuinely new column shape shows up. Point each mapping file's `$schema` at the central schema (relative path) so editors validate as you type.

## Schema shape

The mapping file is a small header plus a `columns` array. The header names the entity's spine and holds two registries the columns reference by name:

- **`drivingTable`** — the table that defines the Gold table's grain (one Gold row per row of this table). Every other source states how it attaches to it.
- **`rowGroups`** — named row groups for Proliferate fan-out (e.g. `ukOrgs`), defined ONCE and referenced by branch `scope`s. A complement group (`{"complementOf": "ukOrgs"}`) is defined *as* the complement, so the pair can never drift apart.
- **`transforms`** — named transformation rules (e.g. `roundTo2dp`, `pearsonImageUrl`), defined ONCE and referenced by column transforms. Every attribute using a rule compiles literally the same SQL; extending a rule is a one-place change.

Each entry in `columns` is a discriminated union on `kind` (`scalar` | `conditional` | `nested`), enforced via `oneOf` with `additionalProperties: false` on each branch — a column can't mix fields from different kinds:

- **`scalar`** — 1:1 attribute. `source: {table, column, filter, join}` + `dataType` + `transform` + `channels` (event/message/api booleans) + `nullable`.
- **`conditional`** — source depends on a discriminator column's value (e.g. digital vs. physical items read different `ATTR_GROUP`s). Has `discriminator: {table, column}` and a `branches` array (`minItems: 2`); each branch has its own `condition`, `source`, `fallback` (`returnNull` | `fallbackToElse`), optional `scope` (fan-out) and optional `transform`. `dataType`, `channels`, and `businessDescription` live once on the column, not per branch. An optional column-level `transform` applies AFTER the branch pick — it wraps whichever branch won (the coverImage URL normalization pattern).
- **`nested`** — 1-to-many attribute (e.g. `contributors`). Has `joinKey` (how child rows attach to the parent row) and a `fields` array where each field has its own `source` (and optional `transform`) but shares the column-level `joinKey`/`filter`. A field's `source` may be `null` while no Silver source exists yet: the column is declared in the contract and rendered `NULL` until one registers.

### The join — how a source attaches (UIFPE-104)

Every source that is not the driving table declares its attachment explicitly and structurally — EAV extension tables included (their entity-key join is stated, not implied by a grain):

```json
"join": {"type": "left", "on": [{"leftTable": "MTL_SYSTEM_ITEMS_B",
         "leftColumn": "INVENTORY_ITEM_ID", "rightColumn": "INVENTORY_ITEM_ID"}]}
```

`type` is `left` (keep entity rows without a match) or `inner` (drop them); composite keys are multiple `on` pairs; `leftTable` may be any already-attached table, so chains compose (A → B → C). The compiler renders the JOIN from this verbatim.

> **`joinGrain` is deprecated.** The old enum (`Master` | `Org Level` | `Proliferate`) was a product-specific encoding of which keys the join uses: Master = ON `INVENTORY_ITEM_ID`, Org Level = ON `INVENTORY_ITEM_ID + ORGANIZATION_ID`. The explicit join states this directly and domain-agnostically. Proliferate was never a grain — it is fan-out logic, expressed by branch `scope` + the `rowGroups` registry: a branch's `scope` names the row group that receives its value, and a sibling branch scoped to the complement (or to "all remaining rows") catches everything else. The schema still *accepts* `joinGrain` while old files migrate; new entries must not carry it (CI enforces this).

### Transforms — structured, reusable transformation rules (UIFPE-105)

A `transform` is structured data compiled deterministically around the source expression `{x}` — never prose. It is one of: `null` (straight copy), `{"rule": "<name>"}` (a reference into the `transforms` registry — preferred), or an inline definition. A definition carries any of:

- **`steps`** — a pipeline applied in order: `round` (`precision`), `cast` (`to`), `trim`, `upper`, `lower`, `coalesceTo` (`value`). Example — the `unit*` columns' rule: `{"steps": [{"op": "round", "precision": 2}, {"op": "cast", "to": "DECIMAL(38,2)"}]}` renders `CAST(round({x}, 2) AS DECIMAL(38,2))`.
- **`cases`** — a guarded rewrite evaluated in order (first match wins), each case a `when` (`startsWith` | `like` | `equals` | `in` | `isNull` + `values`) and a `then` (`prepend` | `append` | `replaceText` | `literal` | `null` | `keep`), plus an `else` of `keep` | `null`. Example — `pearsonImageUrl` normalizes cover-image URLs: DAM paths (start `/A`) get the DAM host prefix, site-relative paths get `https://images.pearson.com`, everything else passes through.
- **`sqlExpr`** — the gated escape hatch: a SQL fragment containing `{x}`, for logic the structured parts can't express. Every use is frozen to a reviewed allowlist in CI — extending the list is the engineering review.

When `cases` and `steps` combine, cases run first and steps wrap the result. Free-text transform strings are deprecated: the schema tolerates them on unmigrated files, but CI forbids them in registered mappings and the wizard blocks submission until they are restructured.

### `goldColumnName` and dot notation

`goldColumnName` follows camelCase (`^[a-z][a-zA-Z0-9]*$`) for simple fields. Related org-level fields that belong to a logical group may use one level of dot notation to namespace them (e.g. `fulfillmentOrgDetails.sellingRestrictionCode`). The dot has no effect on the physical Gold column name — it is a documentation and grouping convention only.

Two deliberate looseness decisions:

- `source.table` is a free string, not an enum — new Oracle/PDH source tables are expected over time (the pipeline just needs a Bronze→Silver job registered for them first, not a schema change).
- `goldColumnName` and nested field `name`s are pattern-validated to camelCase with optional single-level dot namespace.

## Validating a mapping file

Validate with Python's `jsonschema` package (this belongs in CI — `../delivery/` — as with every registered contract):

```bash
python3 -c "
import glob, json, jsonschema
schema = json.load(open('stacks/apicurio/standards/gold-attribute-mapping.schema.json'))
for path in glob.glob('domains/*/src/schemas/mapping/*/v*/*.mapping.json'):
    data = json.load(open(path))
    jsonschema.Draft202012Validator(schema).validate(data)
    print('OK', path)
"
```

**One invariant the schema cannot enforce and must be checked separately:** every `nested`-kind column's `fields` array must have at least one entry with `sortKey: true`, and when the column declares a `pick`, the sort keys must cover `pick.partitionBy` (after the pick, elements are unique per partition, so that is what makes the array order total). Multi-key sorts are the norm — the compiled price entity sorts by three — and the pre-UIFPE-262 "exactly one" rule predated them. Script-side check:

```python
for col in data["columns"]:
    if col.get("kind") == "nested":
        keys = [f["name"] for f in col["fields"] if f["sortKey"]]
        assert keys, col["goldColumnName"]
        assert all(p in keys for p in (col.get("pick") or {}).get("partitionBy", [])), col["goldColumnName"]
```

Beyond schema validation, the product domain's intake hygiene suite (`domains/product/qe/test_intake_hygiene.py`) gates the parts of the contract JSON Schema can't see: branch conditions stay canonical (value + optional label, no prose qualifiers), scopes reference named `rowGroups` entries, complements reference predicate groups, `joinGrain` does not reappear, transforms are structured (prose forbidden), `{"rule"}` references resolve, and every `sqlExpr` contains `{x}` and sits on the reviewed allowlist. A new domain's mapping should copy these gates alongside the file.
