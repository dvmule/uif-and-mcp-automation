---
name: generate-event-schema
description: Generate Pearson UIF-compliant event schemas from a gold attribute mapping file and/or an existing event schema. Produces a data schema (JSON Schema 2020-12) and a worked example event (Pearson CloudEvents envelope), writes both to disk under domains/<domain>/src/schemas/events/, and echoes them. Does NOT invoke publish-schema — APICURIO registration is a separate step.
---

# Generate Event Schema

## When to invoke

Invoke when the user asks to:
- Generate an event schema for a domain
- Create a new event from a gold mapping file
- Produce a `<domain>.<entity>.<action>` event schema
- Convert gold attribute mapping columns to an event data schema

## Inputs — detect which path applies

| Input | Detection |
|---|---|
| **Gold mapping file** | User provides `domains/<domain>/src/schemas/mapping/<entity>/v1/<domain>.mapping.json` or says "use the gold mapping" |
| **Existing event schema** | User pastes JSON or gives a file path to an existing JSON Schema |
| **Both** | User provides both — use existing schema as base, merge gold `event:true` columns in |

**Before generating, confirm with the user if not already stated:**
1. The **event action** — past-tense verb (`created`, `updated`, `posted`, `activated`). Ask if not provided.
2. `dataclassification` — default `internal`
3. `piipresent` — default `false`
4. `causationid` — default: omit (only include if this event is triggered by another event or command)

---

## Step 1 — Read the gold mapping file

If input is a gold mapping file, read it and extract:
- `domain` field (e.g. `"product"`)
- `entity` — take the segment **after the last `.`** in the `table` field  
  e.g. `"gold_product.product"` → `"product"`, `"gold_finance.journal_entry"` → `"journal_entry"` → normalise to `"journal-entry"` (hyphens, lowercase)
- All columns where `"event": true`

---

## Step 2 — Map columns to JSON Schema properties

For each `event: true` column, apply these rules:

### dataType → JSON Schema type

| Gold `dataType` | JSON Schema property |
|---|---|
| `string` | `{ "type": "string" }` |
| `integer` | `{ "type": "integer" }` |
| `number` | `{ "type": "number" }` |
| `float` | `{ "type": "number" }` |
| `boolean` | `{ "type": "boolean" }` |
| `date` | `{ "type": "string", "format": "date" }` |
| `timestamp` | `{ "type": "string", "format": "date-time" }` |

Always add `"description": "<businessDescription value>"` to the property.

### nullable → required

- `nullable: false` → include the field name in the `required` array
- `nullable: true` → omit from `required`; change the type to `["<type>", "null"]` e.g. `{ "type": ["string", "null"] }`

### Column kind → schema shape

**`scalar`** — single property, `goldColumnName` as the key:
```json
"ppid": { "type": "string", "description": "Pearson Product Identifier" }
```

**`conditional`** — treat as scalar. The conditional logic is a source-side concern (which Oracle table to read from); in the event the resolved value is a single field:
```json
"<goldColumnName>": { "type": "<mappedType>", "description": "<businessDescription>" }
```

**`nested`** — array of objects. Each field in `fields[]` uses the same dataType → JSON Schema mapping as scalar. The field with `sortKey: true` gets `"description": "sort key"`:
```json
"contributors": {
  "type": "array",
  "description": "<businessDescription>",
  "items": {
    "type": "object",
    "properties": {
      "givenName": { "type": "string", "description": "Given name" },
      "familyName": { "type": "string", "description": "sort key" }
    },
    "required": ["familyName"]
  }
}
```

`goldColumnName` is already camelCase — use it as-is as the property key.

---

## Step 3 — Assemble the data schema

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://schemas.pearson.com/<domain>/<entity>-<action>-data/v1",
  "title": "<EntityPascalCase><ActionPascalCase> data payload",
  "type": "object",
  "properties": {
    // one entry per event:true column, mapped per Step 2
  },
  "required": [
    // field names where nullable: false
  ],
  "additionalProperties": false
}
```

Example for `product.created` with columns `ppid` (string, not nullable) and `organizationCode` (string, not nullable):

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://schemas.pearson.com/product/product-created-data/v1",
  "title": "ProductCreated data payload",
  "type": "object",
  "properties": {
    "ppid": {
      "type": "string",
      "description": "Pearson Product Identifier"
    },
    "organizationCode": {
      "type": "string",
      "description": "Pearson organisation from Oracle inventory organisations."
    }
  },
  "required": ["ppid", "organizationCode"],
  "additionalProperties": false
}
```

---

## Step 4 — Assemble the worked example event

### Naming rules

| Field | Rule | Example |
|---|---|---|
| `type` | `com.pearson.<domain>.<entity>.<action>.v<major>` | `com.pearson.product.product.created.v1` |
| `source` | `urn:pearson:svc:<service-name>` — user-supplied or default `urn:pearson:svc:<domain>-service` | `urn:pearson:svc:product-service` |
| `subject` | `<entity>/<business-key-placeholder>` | `product/SKU-100245` |
| `dataschema` | `https://schemas.pearson.com/<domain>/<entity>-<action>-data/v<major>` | `https://schemas.pearson.com/product/product-created-data/v1` |
| `id` | UUID v4 | `550e8400-e29b-41d4-a716-446655440000` |
| `correlationid` | UUID v4 | `c1f5d8f2-2f44-4d9a-9c5f-8b4a5a1d91f7` |
| `partitionkey` | business key from `subject` | `SKU-100245` |
| `channel` | the delivery channel this copy is published for — one of `event` (SQS), `message` (Kinesis), `api` (webhook). One envelope per channel the record is tagged for | `event` |
| `time` | RFC 3339 UTC | `2026-07-31T12:00:00Z` |

### Tier rules

**Always include — Tier 1 (CloudEvents core):** `specversion`, `id`, `source`, `type`, `data`

**Always include — Tier 2 (Pearson-required):** `time`, `datacontenttype`, `dataschema`, `subject`, `correlationid`, `partitionkey`, `channel`

**Include only if confirmed — Tier 3 (optional governance):** `causationid`, `tenantid`, `producerapp`, `dataclassification`, `piipresent`, `traceparent`/`tracestate`, `sequence`

Default: include `dataclassification` and `piipresent` (both are commonly needed and their defaults are safe).

### Example worked event (product.created)

```json
{
  "specversion": "1.0",
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "source": "urn:pearson:svc:product-service",
  "type": "com.pearson.product.product.created.v1",
  "time": "2026-07-31T12:00:00Z",
  "datacontenttype": "application/json",
  "dataschema": "https://schemas.pearson.com/product/product-created-data/v1",
  "subject": "product/SKU-100245",
  "correlationid": "c1f5d8f2-2f44-4d9a-9c5f-8b4a5a1d91f7",
  "partitionkey": "SKU-100245",
  "channel": "event",
  "dataclassification": "internal",
  "piipresent": false,
  "data": {
    "ppid": "SKU-100245",
    "organizationCode": "GB01"
  }
}
```

For `data`, populate each field with a realistic placeholder value matching its type.

---

## Step 5 — Write files to disk and echo

### Write

Create `domains/<domain>/src/schemas/events/` if it does not exist, then write:

| File | Path |
|---|---|
| Data schema | `domains/<domain>/src/schemas/events/<entity>-<action>-data.json` |
| Worked example | `domains/<domain>/src/schemas/events/<entity>-<action>-example.json` |

> **Do NOT write the envelope schema to disk.** It is the shared registered artifact `uif/pearson-event-envelope` in APICURIO. It is embedded here as a reference only.

### Echo

After writing, print both files as fenced JSON code blocks:

```json
// domains/<domain>/src/schemas/events/<entity>-<action>-data.json
{ ... }
```

```json
// domains/<domain>/src/schemas/events/<entity>-<action>-example.json
{ ... }
```

Then print the next step:

> **Next step:** Register the data schema with APICURIO using the `publish-schema` skill:
> - Artifact ID: `<domain>/<entity>-<action>-data`
> - The shared envelope artifact `uif/pearson-event-envelope` is already registered.

---

## Envelope schema reference — DO NOT write this to disk

This is the canonical `uif/pearson-event-envelope` v1.0.0 schema, embedded here so you can validate the worked example without a network call.

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://schemas.pearson.com/uif/pearson-event-envelope/v1",
  "title": "Pearson CloudEvents Envelope",
  "type": "object",
  "properties": {
    "specversion":        { "const": "1.0" },
    "id":                 { "type": "string", "minLength": 1 },
    "source":             { "type": "string", "format": "uri-reference",
                            "pattern": "^urn:pearson:(svc|domain):[a-z0-9-]+$" },
    "type":               { "type": "string",
                            "pattern": "^com\\.pearson\\.[a-z0-9]+\\.[a-z0-9-]+\\.[a-z0-9]+\\.v[0-9]+$" },
    "time":               { "type": "string", "format": "date-time" },
    "datacontenttype":    { "const": "application/json" },
    "dataschema":         { "type": "string", "format": "uri" },
    "subject":            { "type": "string", "pattern": "^[a-z-]+/[A-Za-z0-9._-]+$" },
    "correlationid":      { "type": "string", "format": "uuid" },
    "partitionkey":       { "type": "string", "minLength": 1, "maxLength": 256 },
    "channel":            { "enum": ["event","message","api"] },
    "data":               { "type": "object" },
    "causationid":        { "type": "string" },
    "tenantid":           { "type": "string" },
    "producerapp":        { "type": "string" },
    "dataclassification": { "enum": ["public","internal","confidential","restricted"] },
    "piipresent":         { "type": "boolean" },
    "traceparent":        { "type": "string",
                            "pattern": "^[0-9]{2}-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}$" },
    "tracestate":         { "type": "string" },
    "sequence":           { "type": "string" }
  },
  "required": [
    "specversion","id","source","type","time",
    "datacontenttype","dataschema","subject",
    "correlationid","partitionkey","channel","data"
  ]
}
```
