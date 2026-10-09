# Design Spec: `generate-event-schema` skill

**Date:** 2026-07-31
**Author:** Rajnivas Karunakaran
**Status:** Approved — ready for implementation

---

## Purpose

A personal Claude Code skill (`~/.claude/skills/generate-event-schema/SKILL.md`) that generates Pearson UIF-compliant event schemas from a domain's gold attribute mapping file and/or an existing event schema. The skill produces a data schema (JSON Schema 2020-12), a worked example event JSON (CloudEvents envelope with data schema referenced), writes both to disk, and echoes them to the conversation.

It does not invoke `publish-schema` — APICURIO registration is a separate, deliberate step.

---

## Inputs

Three supported input paths:

| Input | What Claude reads |
|---|---|
| **Gold mapping file** | `domains/<domain>/src/schemas/<domain>.mapping.json` — extracts `domain`, `entity` (from `table` field, e.g. `gold_product.product` → `product`), and all columns where `"event": true` |
| **Existing event schema** | A JSON Schema file path or pasted JSON — used as the base `data` schema, normalised to Pearson standards |
| **Both** | Existing schema as base; gold mapping's `event: true` columns merged in / validated against it |

**Required user input regardless of source:**
- The **event action** — past-tense verb (`created`, `updated`, `posted`, `activated`). Claude asks if not provided.
- Optionally: `causationid`, `tenantid`, `piipresent` applicability. Claude defaults to omitting Tier 3 optional attributes unless told otherwise.

---

## Field Mapping Rules

### Gold `dataType` → JSON Schema type

| Gold `dataType` | JSON Schema |
|---|---|
| `string` | `{ "type": "string" }` |
| `integer` | `{ "type": "integer" }` |
| `number` / `float` | `{ "type": "number" }` |
| `boolean` | `{ "type": "boolean" }` |
| `date` | `{ "type": "string", "format": "date" }` |
| `timestamp` | `{ "type": "string", "format": "date-time" }` |

### `nullable` → `required`

- `nullable: false` → field included in `required` array
- `nullable: true` → field omitted from `required`; type becomes `["<type>", "null"]`

### Column `kind` → schema shape

| Kind | Data schema shape |
|---|---|
| `scalar` | Single property: `goldColumnName` with mapped type. `businessDescription` → `"description"`. |
| `conditional` | Single property — same as scalar. Conditional logic is a source-side concern; in the event the resolved value is one field. |
| `nested` | `"type": "array"`, `"items": { "type": "object", "properties": { <each field in fields[]> } }`. Each nested field uses the same dataType → JSON Schema mapping as scalar fields. Field with `sortKey: true` gets `"description": "sort key"`. |

`goldColumnName` is already camelCase — use as-is.

---

## Naming Conventions

### Event `type`
```
com.pearson.<domain>.<entity>.<action>.v<major>
```
- `domain` — from `mapping.json` `"domain"` field, lowercase
- `entity` — take the segment after the last `.` in `"table"` (e.g. `gold_product.product` → `product`), lowercase, hyphens for multi-word
- `action` — user-supplied past-tense verb
- `major` — starts at `1`; increments only on breaking change

### Source URN
```
urn:pearson:svc:<service-name>
```
User-supplied, or defaulted to `urn:pearson:svc:<domain>-service`.

### Subject
```
<entity>/<business-key-placeholder>
```
e.g. `product/SKU-100245`

### APICURIO artifact IDs

| Artifact | ID |
|---|---|
| Envelope (shared, never re-created) | `uif/pearson-event-envelope` |
| Data schema | `<domain>/<entity>-<action>-data` e.g. `catalog/product-created-data` |
| `dataschema` URI | `https://schemas.pearson.com/<domain>/<entity>-<action>-data/v<major>` |

---

## Output Structure

### Files written to disk

| File | Path |
|---|---|
| Data schema | `domains/<domain>/src/schemas/events/<entity>-<action>-data.json` |
| Worked example event | `domains/<domain>/src/schemas/events/<entity>-<action>-example.json` |

The worked example event is the full Pearson CloudEvents envelope with realistic placeholder values and `dataschema` pointing to the APICURIO URI. It is not a copy of the envelope schema — the canonical envelope schema lives in APICURIO as `uif/pearson-event-envelope`.

### Echo to conversation

After writing, print both the data schema and the worked example event as fenced JSON code blocks.

---

## Envelope Schema Reference (embedded in SKILL.md)

The skill embeds the full `uif/pearson-event-envelope` v1.0.0 schema verbatim so Claude can construct a correctly-shaped worked example without runtime lookups.

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
    "correlationid","partitionkey","data"
  ]
}
```

**Tier annotations (for SKILL.md reference, not in schema):**
- **Tier 1** (CloudEvents core): `specversion`, `id`, `source`, `type`, `data`
- **Tier 2** (Pearson-required): `time`, `datacontenttype`, `dataschema`, `subject`, `correlationid`, `partitionkey`
- **Tier 3** (optional governance): `causationid`, `tenantid`, `producerapp`, `dataclassification`, `piipresent`, `traceparent`/`tracestate`, `sequence`

---

## Normative References

| Source | Used for |
|---|---|
| Confluence: [Eventing Schema Standards & Guidelines](https://pearson.atlassian.net/wiki/spaces/ITSOC/pages/1276215560) | D1–D15 design decisions, producer/consumer rules |
| Confluence: [Event Envelope & Naming Standards](https://pearson.atlassian.net/wiki/spaces/ITSOC/pages/1277100355) | Three-tier attribute spec, naming conventions, APICURIO artifact naming |
| Confluence: [Reference Schemas & Worked Examples](https://pearson.atlassian.net/wiki/spaces/ITSOC/pages/1276838281) | Envelope JSON Schema v1.0.0, worked data schemas |
| `capabilities/event/guidelines.md` | UIF event standards (AsyncAPI 3.0, CloudEvents 1.0, JSON Schema 2020-12) |
| `stacks/apicurio/standards/gold-attribute-mapping.md` | Gold mapping schema, column kinds, `channels.event` flag |
