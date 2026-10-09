# Generate Event Schema Skill — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create `~/.claude/skills/generate-event-schema/SKILL.md` — a personal Claude Code skill that generates Pearson UIF-compliant event schemas from a gold attribute mapping file and/or an existing event schema.

**Architecture:** Single `SKILL.md` file (Approach A — reference guide with embedded templates). All rules, naming conventions, field mapping tables, and the verbatim envelope schema live in one file. No companion files needed. The skill is invoked interactively; Claude reads the mapping file from disk, applies the mapping rules, writes two output files to `domains/<domain>/src/schemas/events/`, and echoes them.

**Tech Stack:** Markdown skill file (`SKILL.md`). No code — the skill is process documentation that guides Claude. Tested via subagent pressure scenarios.

## Global Constraints

- Skill location: `~/.claude/skills/generate-event-schema/SKILL.md`
- Frontmatter `name` must be `generate-event-schema`
- Event `type` format: `com.pearson.<domain>.<entity>.<action>.v<major>` (D5)
- Source URN pattern: `^urn:pearson:(svc|domain):[a-z0-9-]+$`
- Envelope schema version: `uif/pearson-event-envelope` v1.0.0 — embedded verbatim, never written to disk
- JSON Schema dialect: 2020-12 (`https://json-schema.org/draft/2020-12/schema`)
- Output files: `domains/<domain>/src/schemas/events/<entity>-<action>-data.json` and `<entity>-<action>-example.json`
- No `publish-schema` invocation — registration is a separate step
- Spec: `docs/superpowers/specs/2026-07-31-generate-event-schema-design.md`

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `~/.claude/skills/generate-event-schema/SKILL.md` | **Create** | The complete skill: trigger conditions, input detection, field mapping rules, naming conventions, output structure, envelope schema reference |
| `domains/<domain>/src/schemas/events/<entity>-<action>-data.json` | **Generated at runtime** | Data schema produced by the skill |
| `domains/<domain>/src/schemas/events/<entity>-<action>-example.json` | **Generated at runtime** | Worked example event produced by the skill |

---

## Task 1: Baseline test — document what Claude does without the skill

Run a fresh subagent without the skill and give it the product mapping file. Document every violation. This is the RED step — it tells you what the skill must fix.

**Files:**
- Read: `domains/product/src/schemas/product.mapping.json`
- No files created

- [ ] **Step 1: Open a fresh Claude Code session (or spawn a subagent) with no generate-event-schema skill loaded**

Verify the skill does not exist yet:
```bash
ls ~/.claude/skills/generate-event-schema/ 2>/dev/null || echo "skill not found — good"
```
Expected: `skill not found — good`

- [ ] **Step 2: Give the subagent this exact prompt and record its output**

```
Generate an event schema for the product domain using the gold mapping at
domains/product/src/schemas/product.mapping.json with action "created".
```

- [ ] **Step 3: Check the output against the Pearson standard and record every violation**

Check each of these — tick if correct, note the violation if wrong:

| Check | Expected | Pass/Fail |
|---|---|---|
| `type` format | `com.pearson.product.product.created.v1` | ? |
| `source` format | `urn:pearson:svc:product-service` | ? |
| `subject` format | `product/<business-key>` | ? |
| `dataschema` URI | `https://schemas.pearson.com/product/product-created-data/v1` | ? |
| `correlationid` present | UUID field in envelope | ? |
| `partitionkey` present | Pearson-required Tier 2 field | ? |
| `datacontenttype` | constant `application/json` | ? |
| Data schema `$id` | `https://schemas.pearson.com/product/product-created-data/v1` | ? |
| Only `event:true` columns in data | only `ppid`, `organizationCode` | ? |
| Output files written to correct paths | `domains/product/src/schemas/events/` | ? |

- [ ] **Step 4: Record violations as comments in this plan under the task**

<!-- Baseline violations recorded here after running: -->

---

## Task 2: Write the SKILL.md

Create `~/.claude/skills/generate-event-schema/SKILL.md` with the complete content below. Copy it verbatim — every rule, table, and code block is load-bearing.

**Files:**
- Create: `~/.claude/skills/generate-event-schema/SKILL.md`

- [ ] **Step 1: Create the skills directory**

```bash
mkdir -p ~/.claude/skills/generate-event-schema
```

- [ ] **Step 2: Write SKILL.md with this exact content**

Create `~/.claude/skills/generate-event-schema/SKILL.md`:

````markdown
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
| **Gold mapping file** | User provides `domains/<domain>/src/schemas/<domain>.mapping.json` or says "use the gold mapping" |
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
| `time` | RFC 3339 UTC | `2026-07-31T12:00:00Z` |

### Tier rules

**Always include — Tier 1 (CloudEvents core):** `specversion`, `id`, `source`, `type`, `data`

**Always include — Tier 2 (Pearson-required):** `time`, `datacontenttype`, `dataschema`, `subject`, `correlationid`, `partitionkey`

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

````json
// domains/<domain>/src/schemas/events/<entity>-<action>-data.json
{ ... }
````

````json
// domains/<domain>/src/schemas/events/<entity>-<action>-example.json
{ ... }
````

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
````

- [ ] **Step 3: Verify the file was written**

```bash
ls -la ~/.claude/skills/generate-event-schema/
wc -l ~/.claude/skills/generate-event-schema/SKILL.md
```

Expected: `SKILL.md` exists and is > 100 lines.

- [ ] **Step 4: Confirm the frontmatter name is correct**

```bash
head -5 ~/.claude/skills/generate-event-schema/SKILL.md
```

Expected first 4 lines:
```
---
name: generate-event-schema
description: Generate Pearson UIF-compliant event schemas...
---
```

- [ ] **Step 5: Commit the skill file**

```bash
git add ~/.claude/skills/generate-event-schema/SKILL.md
git commit -m "feat: add generate-event-schema skill"
```

Note: `~/.claude/skills/` is outside the UIF repo. If it is not a git repo, skip this step and note the file location.

---

## Task 3: Verification test — confirm the skill produces correct output

Run a fresh subagent **with the skill loaded** using the product mapping file. All checks from Task 1 must now pass.

**Files:**
- Read: `domains/product/src/schemas/product.mapping.json`
- Verify created: `domains/product/src/schemas/events/product-created-data.json`
- Verify created: `domains/product/src/schemas/events/product-created-example.json`

- [ ] **Step 1: Invoke the skill with this prompt**

In a fresh Claude Code session (so the skill is freshly loaded):

```
Generate an event schema for the product domain using the gold mapping at
domains/product/src/schemas/product.mapping.json with action "created".
dataclassification: internal, piipresent: false.
```

- [ ] **Step 2: Verify the data schema written to disk**

```bash
cat domains/product/src/schemas/events/product-created-data.json
```

Expected output — must match exactly on these points:

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

Checks:
- [ ] `$schema` is `https://json-schema.org/draft/2020-12/schema`
- [ ] `$id` is `https://schemas.pearson.com/product/product-created-data/v1`
- [ ] Only `ppid` and `organizationCode` in properties (no `inventoryItemId`, `organizationid`, etc. — those have `event: false`)
- [ ] Both fields in `required` (both have `nullable: false`)
- [ ] `additionalProperties: false` present

- [ ] **Step 3: Verify the worked example event written to disk**

```bash
cat domains/product/src/schemas/events/product-created-example.json
```

Checks:
- [ ] `specversion` = `"1.0"`
- [ ] `type` = `"com.pearson.product.product.created.v1"` (not `product.product.created`)
- [ ] `source` matches `^urn:pearson:(svc|domain):[a-z0-9-]+$`
- [ ] `subject` matches `^[a-z-]+/[A-Za-z0-9._-]+$` (e.g. `product/SKU-100245`)
- [ ] `dataschema` = `"https://schemas.pearson.com/product/product-created-data/v1"`
- [ ] `correlationid` present (UUID)
- [ ] `partitionkey` present (matches business key in `subject`)
- [ ] `datacontenttype` = `"application/json"`
- [ ] `data` contains only `ppid` and `organizationCode` with realistic placeholder values
- [ ] No envelope schema written to disk (only the two output files exist)

- [ ] **Step 4: If any check fails, identify which SKILL.md rule was unclear and fix it**

Edit `~/.claude/skills/generate-event-schema/SKILL.md` to close the gap, then re-run Step 1–3.

- [ ] **Step 5: Commit the output files to the UIF repo**

```bash
git add domains/product/src/schemas/events/
git commit -m "Schema: add product.created event schemas (generated)"
```

---

## Self-Review Notes

**Spec coverage check:**

| Spec requirement | Covered in task |
|---|---|
| Gold mapping input (`event:true` columns) | Task 2 Step 2 — Step 1 of SKILL.md |
| Existing event schema input | Task 2 Step 2 — Inputs table |
| Both inputs | Task 2 Step 2 — Inputs table |
| Ask for action verb if missing | Task 2 Step 2 — Inputs confirmation block |
| dataType → JSON Schema mapping | Task 2 Step 2 — Step 2 table |
| nullable → required | Task 2 Step 2 — Step 2 nullable section |
| scalar/conditional/nested kinds | Task 2 Step 2 — Step 2 kind section |
| Event type `com.pearson.*` format | Task 2 Step 2 — Step 4 naming table |
| Source URN `urn:pearson:svc:*` | Task 2 Step 2 — Step 4 naming table |
| APICURIO artifact IDs | Task 2 Step 2 — Step 5 next step prompt |
| Write data schema to disk | Task 2 Step 2 — Step 5 write section |
| Write worked example to disk | Task 2 Step 2 — Step 5 write section |
| Echo both files | Task 2 Step 2 — Step 5 echo section |
| Envelope schema embedded verbatim | Task 2 Step 2 — Envelope reference section |
| No publish-schema invocation | Task 2 Step 2 — frontmatter description + Step 5 note |
| Baseline RED test | Task 1 |
| Verification GREEN test | Task 3 |
