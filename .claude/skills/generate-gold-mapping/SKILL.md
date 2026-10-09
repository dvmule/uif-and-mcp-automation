---
name: generate-gold-mapping
description: Generate a UIF-compliant gold attribute mapping JSON file from a domain's product-gold-attribute-mapping-template.xlsx workbook. Extracts all three sheets (Scalar, Conditional, Nested), applies transformation rules, validates against gold-attribute-mapping.schema.json, and writes domains/<domain>/src/schemas/mapping/<entity>/v1/<domain>.mapping.json. Can also update an existing mapping file with new columns from an updated Excel.
---

# Generate Gold Mapping

## When to invoke

Invoke when the user asks to:
- Generate or refresh a gold attribute mapping JSON from an Excel workbook
- Convert a `*-gold-attribute-mapping-template.xlsx` to `<domain>.mapping.json`
- Add new columns from an updated Excel to an existing mapping file
- Validate or fix an existing mapping file against the schema

---

## Inputs — detect which path applies

| Input | Detection |
|---|---|
| **Excel only** | User provides path to `*-gold-attribute-mapping-template.xlsx` |
| **Excel + existing mapping** | User provides both — merge: add new columns, preserve existing columns, update changed ones |
| **Existing mapping only** | User asks to validate or fix — run validation only (Step 5), no extraction needed |

**Before generating, confirm with the user if not already stated:**
1. The **domain name** — e.g. `product`, `customer` (used in output path and `domain` field)
2. The **gold table name** — e.g. `gold_product.product` (used in `table` field)

If both are derivable from the file path or an existing mapping, use those and confirm only if ambiguous.

---

## Step 1 — Extract Excel data using Python

The Excel file is binary — always use Python (openpyxl) to extract it. Write and run the following extraction script:

```python
import openpyxl, json

wb = openpyxl.load_workbook('<path-to-xlsx>', data_only=True)

# --- Batch settings from Instructions sheet ---
instr = wb['Instructions']
batch_rows = {r[1]: r[2] for r in instr.iter_rows(values_only=True) if r[1] is not None}
# Keys contain: 'Requested by', 'Target date', 'Backfill mode', 'Any brand-new channel'

# --- Helper: detect EXAMPLE rows ---
def is_example(notes):
    return notes and 'EXAMPLE' in str(notes).upper()

# --- Sheet 1: Scalar Columns ---
scalar_rows = []
ws = wb['1. Scalar Columns']
rows = list(ws.iter_rows(values_only=True))
# Row 0 is header; data starts at row 1
for row in rows[1:]:
    if not any(v is not None for v in row):
        continue
    name, desc, dtype, src_table, src_col, src_filter, transform, ev, msg, api, nullable, notes, join_grain = row
    if is_example(notes) or name is None:
        continue
    scalar_rows.append({
        'goldColumnName': name,
        'businessDescription': desc,
        'dataType': dtype,
        'source': {'table': src_table, 'column': src_col.strip() if src_col else src_col, 'filter': src_filter},
        'transform': transform,
        'event': ev, 'message': msg, 'api': api,
        'nullable': nullable,
        'joinGrain': join_grain,
        'notes': notes
    })

# --- Sheet 2: Conditional Columns ---
cond_rows = []
ws = wb['2. Conditional Columns']
rows = list(ws.iter_rows(values_only=True))
for row in rows[1:]:
    if not any(v is not None for v in row):
        continue
    name, desc, dtype, discriminator, condition, src_table, src_col, src_filter, fallback, ev, msg, api, notes, join_grain = row
    if is_example(notes):
        continue
    if name is None and cond_rows:
        # continuation branch — inherit name from last row
        name = cond_rows[-1]['goldColumnName']
    if name is None:
        continue
    cond_rows.append({
        'goldColumnName': name,
        'businessDescription': desc,
        'dataType': dtype,
        'discriminator': discriminator,
        'condition': condition,
        'source': {'table': src_table, 'column': src_col, 'filter': src_filter},
        'fallback': fallback,
        'event': ev, 'message': msg, 'api': api,
        'joinGrain': join_grain,
        'notes': notes
    })

# --- Sheet 3: Nested Columns ---
nested_rows = []
ws = wb['3. Nested Columns (1-to-many)']
rows = list(ws.iter_rows(values_only=True))
for row in rows[1:]:
    if not any(v is not None for v in row):
        continue
    col_name, col_desc, field_name, field_type, src_table, src_col, join_key, src_filter, sort_key, ev, msg, api, notes, join_grain = row
    if is_example(notes):
        continue
    if col_name is None and nested_rows:
        col_name = nested_rows[-1]['goldColumnName']
    if col_name is None or field_name is None:
        continue
    nested_rows.append({
        'goldColumnName': col_name,
        'colDescription': col_desc,
        'fieldName': field_name,
        'fieldType': field_type,
        'source': {'table': src_table, 'column': src_col, 'filter': src_filter},
        'joinKey': join_key,
        'sortKey': sort_key,
        'event': ev, 'message': msg, 'api': api,
        'joinGrain': join_grain,
        'notes': notes
    })

print(json.dumps({'batch': dict(list(batch_rows.items())[:4]),
                  'scalar': scalar_rows,
                  'conditional': cond_rows,
                  'nested': nested_rows}, default=str, indent=2))
```

Capture the JSON output. This is the raw data for Steps 2–4.

---

## Step 2 — Transformation rules

Apply these rules to every raw row before writing the mapping file.

### Data type normalization

| Excel value | JSON Schema / mapping type |
|---|---|
| `bigint` | `integer` |
| `string` | `string` |
| `integer` | `integer` |
| `number` | `number` |
| `float` | `float` |
| `boolean` | `boolean` |
| `date` | `date` |
| `timestamp` | `timestamp` |

Any unrecognised type: keep as-is and add a `notes` entry: `"Data type '<value>' not in enum — normalized to 'string'; verify"`.

### Channel flag normalization

| Excel value | JSON boolean |
|---|---|
| `Y` (any case) | `true` |
| `N`, `None`, blank | `false` |

Channels on continuation branch rows (where `event`/`message`/`api` are blank) are inherited from the first branch row of that column.

### Nullable normalization

| Excel value | JSON boolean |
|---|---|
| `N` | `false` |
| `Y` | `true` |
| blank / `None` | `true` (safe default — add note) |

### Fallback normalization

| Excel value | JSON value |
|---|---|
| `NULL` (any case) | `"returnNull"` |
| `fall back to ELSE branch` (any case) | `"fallbackToElse"` |

### Discriminator parsing

Excel format: `TableName.ColumnName` (e.g. `EGO_MTL_SY_ITEMS_EXT_B.ITEM_CATALOG_GROUP_ID`)

Split on the last `.`:
```
table = "EGO_MTL_SY_ITEMS_EXT_B"
column = "ITEM_CATALOG_GROUP_ID"
```

### joinGrain placement

- `scalar` column: `joinGrain` is a top-level field on the column object
- `nested` column: `joinGrain` is a top-level field on the column object (shared by all fields)
- `conditional` column: `joinGrain` is a **per-branch** field — different branches can have different grains

If `joinGrain` is blank in the Excel, default to `"Master"` and add a note: `"joinGrain not specified — defaulted to Master; verify"`.

### EXAMPLE row detection

Skip any row where the `notes` field contains the substring `EXAMPLE` (case-insensitive).

### Source column whitespace

Trim leading/trailing whitespace from `source.column` values — the Excel often has trailing spaces (e.g. `"ORGANIZATION_ID "`).

---

## Step 3 — Assemble scalar columns

For each raw scalar row, produce:

```json
{
  "goldColumnName": "<goldColumnName>",
  "businessDescription": "<businessDescription>",
  "kind": "scalar",
  "dataType": "<normalized dataType>",
  "source": { "table": "<src_table>", "column": "<src_col trimmed>", "filter": <src_filter or null> },
  "transform": <transform or null>,
  "channels": { "event": <bool>, "message": <bool>, "api": <bool> },
  "nullable": <bool>,
  "joinGrain": "<Master|Org Level|Proliferate>",
  "notes": <notes or null>
}
```

---

## Step 4 — Assemble conditional columns

Group raw conditional rows by `goldColumnName`. The first row in each group carries `businessDescription`, `dataType`, `discriminator`, and channel flags. Build:

```json
{
  "goldColumnName": "<goldColumnName>",
  "businessDescription": "<first-row businessDescription>",
  "kind": "conditional",
  "dataType": "<normalized dataType>",
  "discriminator": { "table": "<parsed table>", "column": "<parsed column>" },
  "channels": { "event": <bool>, "message": <bool>, "api": <bool> },
  "branches": [
    {
      "condition": "<branch condition string>",
      "source": { "table": "<src_table>", "column": "<src_col>", "filter": <filter or null> },
      "fallback": "<returnNull|fallbackToElse>",
      "joinGrain": "<Master|Org Level|Proliferate>",
      "notes": <notes or null>
    }
  ],
  "notes": <first-row notes if present, else null>
}
```

**Branch ordering:** preserve the order from the Excel (ELSE branch is typically first; named branches follow). Do not reorder.

---

## Step 5 — Assemble nested columns

Group raw nested rows by `goldColumnName`. The first row in each group carries `colDescription`, `joinKey`, `filter` (if any), channel flags, and `joinGrain`. Build:

```json
{
  "goldColumnName": "<goldColumnName>",
  "businessDescription": "<first-row colDescription>",
  "kind": "nested",
  "joinKey": "<joinKey>",
  "filter": <shared filter or null>,
  "channels": { "event": <bool>, "message": <bool>, "api": <bool> },
  "joinGrain": "<Master|Org Level|Proliferate>",
  "fields": [
    {
      "name": "<fieldName>",
      "type": "<normalized fieldType>",
      "sortKey": <bool — Y→true, N/blank→false>,
      "source": { "table": "<src_table>", "column": "<src_col>", "filter": <filter or null> }
    }
  ],
  "notes": <first-row notes or null>
}
```

**sortKey invariant:** exactly one field per nested column must have `sortKey: true`. If zero or more than one are marked `Y`, flag it: `"⚠ sortKey: <count> fields marked — must be exactly 1"`. Do not write the file until this is resolved.

---

## Step 6 — Assemble the mapping file

```json
{
  "$schema": "../../../../stacks/apicurio/standards/gold-attribute-mapping.schema.json",
  "domain": "<domain>",
  "table": "<gold table>",
  "batch": {
    "requestedBy": "<from Instructions sheet>",
    "targetDate": "<YYYY-MM-DD>",
    "backfillMode": "<silent|emit>",
    "newChannel": <string or null>
  },
  "columns": [
    <all scalar columns>,
    <all conditional columns>,
    <all nested columns>
  ]
}
```

Column order within each group follows the Excel row order. Scalars first, then conditionals, then nested — unless the domain team requests a different order.

---

## Step 7 — Validate

Run both checks. Do not commit until both pass.

### JSON Schema validation

```bash
python3 -c "
import json, jsonschema
schema = json.load(open('stacks/apicurio/standards/gold-attribute-mapping.schema.json'))
data = json.load(open('domains/<domain>/src/schemas/mapping/<entity>/v1/<domain>.mapping.json'))
jsonschema.Draft202012Validator(schema).validate(data)
print('Schema OK')
"
```

### sortKey invariant

```bash
python3 -c "
import json
data = json.load(open('domains/<domain>/src/schemas/mapping/<entity>/v1/<domain>.mapping.json'))
for col in data['columns']:
    if col.get('kind') == 'nested':
        count = len([f for f in col['fields'] if f['sortKey']])
        assert count == 1, f\"{col['goldColumnName']}: {count} sort keys (expected 1)\"
print('sortKey OK')
"
```

Fix any failures, then re-validate before writing the output.

---

## Step 8 — Write and echo

Write the validated JSON to:

```
domains/<domain>/src/schemas/mapping/<entity>/v1/<domain>.mapping.json
```

Then echo a summary to the conversation:

```
Written: domains/<domain>/src/schemas/mapping/<entity>/v1/<domain>.mapping.json
  Scalar columns:      <N>
  Conditional columns: <N>
  Nested columns:      <N>
  event:true columns:  <N>
  Validation:          schema OK · sortKey OK
```

Then print the next step:

> **Next step:** Validate the mapping in CI:
> ```bash
> python3 -c "
> import glob, json, jsonschema
> schema = json.load(open('stacks/apicurio/standards/gold-attribute-mapping.schema.json'))
> for path in glob.glob('domains/*/src/schemas/mapping/*/v*/*.mapping.json'):
>     jsonschema.Draft202012Validator(schema).validate(json.load(open(path)))
>     print('OK', path)
> "
> ```
> Then run `generate-event-schema` to produce the event data schema for any `event:true` columns.

---

## Normative references

| Source | Used for |
|---|---|
| `stacks/apicurio/standards/gold-attribute-mapping.schema.json` | The authoritative JSON Schema — validate every output against this |
| `stacks/apicurio/standards/gold-attribute-mapping.md` | Column kinds, joinGrain semantics, dot-notation naming, sortKey invariant |
| `domains/product/src/schemas/mapping/product/v1/product.mapping.json` | Worked reference for a complete mapping (all three kinds, all joinGrain values) |
| `domains/product/src/schemas/product-gold-attribute-mapping-template.xlsx` | Example Excel workbook structure |
