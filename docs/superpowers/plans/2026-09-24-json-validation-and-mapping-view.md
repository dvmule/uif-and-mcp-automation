# JSON Metadata Validation and the Mapping View — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Open the schema registry's metadata validator to JSON bodies (exempting mapping artifacts, which carry no metadata block), and give a gold attribute mapping a Mapping tab that mirrors the `*-gold-attribute-mapping-template.xlsx` workbook.

**Architecture:** Two independent halves. The backend half splits one type gate in `metadata_validator.py` three ways and adds three mapping-specific rules; no new modules. The frontend half adds one pure parser module (`mapping-body.ts`) plus three presentation components, all reading `SchemaDetailRead.content`, which the detail endpoint already returns — so there is no new endpoint, no new API model and no Apicurio call.

**Tech Stack:** Python 3.12 · FastAPI · pytest + pytest-asyncio · ruff · TypeScript · React 19 · react-router-dom · vitest + @testing-library/react + msw · Tailwind · lucide-react

**Spec:** `docs/superpowers/specs/2026-09-24-json-validation-and-mapping-view-design.md`

**Issues:** [UIFPE-597](https://pearson.atlassian.net/browse/UIFPE-597) (Tasks 1–3) · [UIFPE-598](https://pearson.atlassian.net/browse/UIFPE-598) (Tasks 4–10)

## Global Constraints

- **Working directory is `stacks/apicurio/`** for every command in this plan unless a path says otherwise.
- **Commit message shape is enforced by a git hook** and a commit without it is rejected: a body describing the change, then a line starting `UIFPE-<n> #comment <one line for the Jira issue>`. The issue key must be first on its line. A line starting `#` is stripped by git, so `#comment` must never lead. End every commit with `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`.
- **Run linters as Python modules:** `python3 -m ruff check src/ qe/`. The bare `ruff` binary is not on PATH here; `python3 -m ruff` is CI's exact pin.
- **The full gate is `qe/ci.sh --allow-missing-tools`** — the single definition of this stack's checks, shared by CI and by an FDE before pushing. Never pass `--allow-missing-tools` in CI.
- **Never add a `uses:` to a GitHub Actions workflow.** One disallowed action fails the *whole* workflow at startup and the repo cannot allow-list it (409, org-governed). No task here touches a workflow.
- **`piiFields` resolution stays first-dot-segment only** for both Avro and JSON Schema. Full-path resolution would make the two shapes disagree about the same contract.
- **Mapping detection is by body shape, never by artifact name.** Two naming conventions already exist for the same thing (`product.product.mapping` in the dev registry, `product-gold-attribute-mapping` in the files' `registry.artifact`).
- **The mapping view reports what the artifact declares and does not pass judgement.** An unresolvable `scope.group` or `transform.rule` renders with an unresolved marker; rejecting it is CI's job (`domains/product/qe/test_intake_hygiene.py`).
- **Brand:** Plus Jakarta Sans and the existing Pearson tokens. Amethyst `#512EAB` (`text-accent`) for source identifiers, amber tint (`bg-amber-tint`) for search hits and the `sqlExpr` review flag, Mist `#EDECF6` (`bg-accent-soft`) for the row-detail ground.
- **UI tables in this feature are `role="table"`/`"row"`/`"cell"` divs on CSS grid**, not `<table>` elements. Follow `SchemaFieldsView.tsx`.
- **Typecheck with `cd stacks/apicurio/ui && npx tsc -b`, and never pipe it.** `npm --prefix` sets package resolution, not the working directory, so `npx --prefix ui tsc -b` from `stacks/apicurio/` dies with `TS5083: Cannot read file '.../stacks/apicurio/tsconfig.json'` — and piping it through `head` or `tail` reports exit 0, so a gate that typechecked nothing reads as green. Verified 2026-09-24.
- **Test bodies are built inline with `JSON.stringify`.** There are no fixture files in this UI tree; introducing one would be the odd case out.

## Review Focus

Five input classes the spec implies but which no task's happy-path tests would exercise. Each has its test pinned to the task that owns the code.

1. **A JSON body that is neither a JSON Schema nor a mapping** (a bare `{"a":1}` registered as type `JSON`) — must run the metadata battery and report `metadata_block_missing`, not crash on a missing `properties` key. → Task 2.
2. **A mapping body whose `columns` is present but not a list** (`"columns": {}`) — `_is_mapping_body` must not match it as a mapping, so it falls through to the metadata battery rather than iterating a dict. → Task 1.
3. **A `piiFields` value that is a plain string rather than a list** — the existing code already coerces this for Avro; the JSON path must not regress it. → Task 2.
4. **A conditional column whose `branches` array is empty, and a nested column whose `fields` array is empty** — the flatteners must yield zero rows rather than one row of `undefined`s. → Task 5.
5. **A mapping body with 74 columns and a query matching nothing** — search must render one "nothing matches" line, not three empty sheets or a blank pane. → Task 8.

---

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `src/app/services/metadata_validator.py` (modify) | The type gate splits three ways; three mapping rules; one shared top-level-field-name helper | 1, 2 |
| `qe/tests/test_metadata_validator.py` (modify) | Every rule in that module, per body shape | 1, 2, 3 |
| `ui/src/features/schemas/mapping-body.ts` (create) | Pure parse + resolve. Detect a mapping, extract facts, flatten each column kind to rows, resolve joins/scopes/transforms/silver names. No React, no network. | 4, 5, 6 |
| `ui/src/features/schemas/mapping-body.test.ts` (create) | The bulk of the frontend tests — the logic lives in the parser, so the tests do too | 4, 5, 6 |
| `ui/src/features/schemas/hooks/use-schema-url-state.ts` (modify) | Add the `mapping` detail tab and the `mkind` sheet param to the URL vocabulary | 7 |
| `ui/src/features/schemas/hooks/use-schema-url-state.test.ts` (modify) | Parsing and fallback for both new values | 7 |
| `ui/src/features/schemas/components/SchemaMappingView.tsx` (create) | The tab body: facts band, sheet toggle, search, the three tables, CSV export | 8 |
| `ui/src/features/schemas/components/MappingRowDetail.tsx` (create) | One row's expansion panel: JOIN, SILVER, SCOPE, XFORM, DERIVATION, PICK | 9 |
| `ui/src/features/schemas/components/MappingReferenceSections.tsx` (create) | Collapsible catalogs of the named shared definitions | 9 |
| `ui/src/features/schemas/components/SchemaDetail.tsx` (modify) | Render and gate the tab; suppress Fields/Graph for a mapping body | 10 |
| `ui/src/features/schemas/components/SchemaDetail.test.tsx` (modify) | Tab gating, default landing, view suppression, the deep-link empty state | 10 |
| `ui/src/features/schemas/index.ts` (modify) | Exports | 10 |

The view is three components rather than one because `SchemaDetail.tsx` is already 955 lines and `SchemaTable.tsx` 573 — the largest files in this feature and the hardest to change safely. A single `SchemaMappingView.tsx` carrying tables, expansion and catalogs would join them.

---

## Task 1: Detect a mapping body, and share one field-name helper

**Files:**
- Modify: `src/app/services/metadata_validator.py` (module docstring lines 1–8; the gate at lines 49–61; the `piiFields` rule at lines 152–170)
- Test: `qe/tests/test_metadata_validator.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `_is_mapping_body(body: dict) -> bool`
  - `_top_level_field_names(body: dict) -> set[str]`

Task 2 consumes both.

- [ ] **Step 1: Write the failing tests**

Append to `qe/tests/test_metadata_validator.py`:

```python
# --- Body-shape helpers (UIFPE-597) -----------------------------------------

from app.services.metadata_validator import (  # noqa: E402
    _is_mapping_body,
    _top_level_field_names,
)


def test_is_mapping_body_matches_a_gold_attribute_mapping() -> None:
    body = {
        "domain": "product",
        "table": "gold_product.product",
        "columns": [{"goldColumnName": "isbn13"}],
    }
    assert _is_mapping_body(body)


def test_is_mapping_body_matches_on_schema_ref_alone() -> None:
    # A mapping whose columns have not been authored yet still declares what
    # it is. Recognising it here is what routes it to `mapping_columns_missing`
    # rather than to `metadata_block_missing`, which would be a lie about it.
    body = {"$schema": "../../gold-attribute-mapping.schema.json"}
    assert _is_mapping_body(body)


def test_is_mapping_body_rejects_an_avro_record() -> None:
    assert not _is_mapping_body({"type": "record", "name": "X", "fields": []})


def test_is_mapping_body_rejects_a_json_schema_master() -> None:
    assert not _is_mapping_body({
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {"id": {"type": "string"}},
    })


def test_is_mapping_body_rejects_columns_that_are_not_a_list() -> None:
    # Review Focus 2. `columns: {}` must not be taken for a mapping, or the
    # mapping rules would iterate a dict and read its keys as columns.
    assert not _is_mapping_body({
        "domain": "product",
        "table": "gold_product.product",
        "columns": {},
    })


def test_top_level_field_names_reads_avro_fields() -> None:
    body = {"fields": [{"name": "id"}, {"name": "email"}]}
    assert _top_level_field_names(body) == {"id", "email"}


def test_top_level_field_names_reads_json_schema_properties() -> None:
    body = {"properties": {"id": {"type": "string"}, "email": {"type": "string"}}}
    assert _top_level_field_names(body) == {"id", "email"}


def test_top_level_field_names_is_empty_for_a_body_with_neither() -> None:
    # Review Focus 1's precondition: a bare JSON object must yield an empty
    # set rather than raising, so the piiFields rule can still run over it.
    assert _top_level_field_names({"a": 1}) == set()
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd stacks/apicurio && python3 -m pytest qe/tests/test_metadata_validator.py -q -k "is_mapping_body or top_level_field_names"
```

Expected: FAIL at collection — `ImportError: cannot import name '_is_mapping_body'`.

- [ ] **Step 3: Rewrite the module docstring**

Replace lines 1–8 of `src/app/services/metadata_validator.py`:

```python
"""Metadata block validator for registered schemas.

Reads from the schema body (authoritative per ADR-0001). Verifies that
Apicurio `properties` match the body. A mismatch is a hard failure.

Two body shapes carry the metadata block and both are validated: Avro, which
declares its fields in `fields`, and JSON Schema, which declares them in
`properties`. `_top_level_field_names` is the one place that difference is
resolved, so the two shapes cannot drift into disagreeing about the same
contract.

A third JSON shape is a gold attribute mapping, which carries no metadata
block at all — its contract never asked it to — and is validated against
`standards/gold-attribute-mapping.schema.json` instead. See `_validate_mapping`.

PROTOBUF, GRAPHQL, XSD, OPENAPI and ASYNCAPI remain unimplemented: they emit a
`schema_type_unsupported` failure so the operator is informed rather than
silently skipped.
"""
```

- [ ] **Step 4: Add the two helpers**

Insert immediately after `_VALID_LIFECYCLES = {"active", "deprecated"}`:

```python
def _is_mapping_body(body: dict) -> bool:
    """True when this body is a gold attribute mapping.

    By SHAPE, never by artifact name. The repository carries two naming
    conventions for the same thing — `product.product.mapping` is the dev
    registry's coordinate, `product-gold-attribute-mapping` is what the files'
    own `registry.artifact` declares — so a rule keyed on either one breaks on
    the other. The UI's `isMappingBody` implements this same predicate; the
    design spec §3.1 is their shared definition.

    `columns` must be a LIST. A body carrying `columns: {}` is not a mapping,
    and taking it for one would have the mapping rules read a dict's keys as
    column entries.
    """
    schema_ref = body.get("$schema")
    if isinstance(schema_ref, str) and "gold-attribute-mapping" in schema_ref:
        return True
    return (
        isinstance(body.get("domain"), str)
        and isinstance(body.get("table"), str)
        and isinstance(body.get("columns"), list)
    )


def _top_level_field_names(body: dict) -> set[str]:
    """Top-level field names, whichever shape declares them.

    Avro puts them in `fields[].name`; JSON Schema puts them in `properties`.
    A body is never both, so the union is safe — and one function means the
    `piiFields` rule is literally the same rule for both shapes rather than
    two that can drift.
    """
    names: set[str] = {
        f.get("name", "")
        for f in (body.get("fields") or [])
        if isinstance(f, dict)
    }
    properties = body.get("properties")
    if isinstance(properties, dict):
        names |= set(properties.keys())
    return names - {""}
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
cd stacks/apicurio && python3 -m pytest qe/tests/test_metadata_validator.py -q -k "is_mapping_body or top_level_field_names"
```

Expected: PASS, 8 tests.

- [ ] **Step 6: Run the whole validator suite — nothing may regress**

```bash
cd stacks/apicurio && python3 -m pytest qe/tests/test_metadata_validator.py -q && python3 -m ruff check src/ qe/
```

Expected: all tests PASS (the pre-existing 13 plus the new 8), ruff clean. The helpers are not wired in yet, so the Avro path is untouched.

- [ ] **Step 7: Commit**

```bash
cd /Users/raj9karu/Documents/Claude/Projects/universal-Integration-framework
git add stacks/apicurio/src/app/services/metadata_validator.py stacks/apicurio/qe/tests/test_metadata_validator.py
git commit -F - <<'MSG'
Recognise a mapping body, and read field names from either shape

Two helpers ahead of opening the type gate. `_is_mapping_body` detects a gold
attribute mapping by shape rather than by artifact name, because two naming
conventions already exist for the same artifact and a rule keyed on either one
breaks on the other. It requires `columns` to be a list, so a body carrying
`columns: {}` is not mistaken for a mapping and does not have its dict keys
read as column entries.

`_top_level_field_names` resolves Avro's `fields[].name` and JSON Schema's
`properties` keys in one place, so the piiFields rule is literally the same
rule for both shapes rather than two that can drift apart.

Neither is wired in yet — the Avro path is unchanged.

UIFPE-597 #comment Added `_is_mapping_body` (shape-based mapping detection, rejecting a non-list `columns`) and `_top_level_field_names` (one resolution of Avro `fields` vs JSON Schema `properties`), with 8 tests. Not yet wired into the gate.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
MSG
```

---

## Task 2: Split the type gate, and validate a mapping against its own contract

**Files:**
- Modify: `src/app/services/metadata_validator.py` (the gate at lines 49–61; the `piiFields` rule; add `_validate_mapping`)
- Test: `qe/tests/test_metadata_validator.py`

**Interfaces:**
- Consumes: `_is_mapping_body(body) -> bool` and `_top_level_field_names(body) -> set[str]` from Task 1.
- Produces: `_validate_mapping(body: dict, artifact_id: str, group_id: str, now: datetime) -> ValidationResult`. Nothing later consumes it directly; `validate` is the only caller.

- [ ] **Step 1: Write the failing tests**

Append to `qe/tests/test_metadata_validator.py`:

```python
# --- JSON Schema bodies (UIFPE-597) -----------------------------------------

_GOOD_JSON_SCHEMA = json.dumps({
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "Product",
    "metadata": {
        "owner": {
            "service": "CI0093421",
            "team": "TM-PRODUCT-CATALOG",
            "email": "product-catalog@pearson.com",
        },
        "domain": "product",
        "dataClassification": "internal",
        "lifecycle": "active",
        "piiFields": ["email"],
    },
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "name": {"type": "string"},
        "email": {"type": "string"},
    },
})

_GOOD_MAPPING = json.dumps({
    "$schema": "../../gold-attribute-mapping.schema.json",
    "domain": "product",
    "table": "gold_product.product",
    "registry": {
        "group": "product",
        "artifact": "product-gold-attribute-mapping",
        "compatibility": "BACKWARD",
    },
    "drivingTable": "MTL_SYSTEM_ITEMS_B",
    "columns": [
        {
            "goldColumnName": "inventoryItemId",
            "businessDescription": "Oracle internal surrogate.",
            "kind": "scalar",
            "dataType": "integer",
            "source": {"table": "MTL_SYSTEM_ITEMS_B", "column": "INVENTORY_ITEM_ID"},
            "channels": {"event": False, "message": False, "api": False},
            "nullable": False,
        },
    ],
})


@pytest.mark.asyncio
async def test_json_schema_body_passes() -> None:
    result = await validate(
        content=_GOOD_JSON_SCHEMA,
        properties=_GOOD_PROPERTIES,
        cmdb=_CMDB,
        artifact_id="product.product.master",
        group_id="default",
        schema_type="JSON",
    )
    assert result.passed
    assert result.failures == []


@pytest.mark.asyncio
async def test_json_schema_pii_field_resolves_against_properties() -> None:
    body = json.loads(_GOOD_JSON_SCHEMA)
    body["metadata"]["piiFields"] = ["ssn"]  # not in properties
    result = await validate(
        content=json.dumps(body),
        properties={},
        cmdb=_CMDB,
        artifact_id="product.product.master",
        group_id="default",
        schema_type="JSON",
    )
    assert any(f.rule == "pii_field_not_found" for f in result.failures)


@pytest.mark.asyncio
async def test_dollar_prefixed_pii_path_resolves_for_both_shapes() -> None:
    # `$.customerId` is metadata-block.schema.json's OWN worked example, and it
    # fails today against Avro too: `$` becomes the first dot-segment and
    # matches nothing. Stripping it fixes the documented example for both.
    for content, schema_type, field in (
        (_GOOD_JSON_SCHEMA, "JSON", "email"),
        (_GOOD_AVRO, "AVRO", "email"),
    ):
        body = json.loads(content)
        body["metadata"]["piiFields"] = [f"$.{field}"]
        result = await validate(
            content=json.dumps(body),
            properties={},
            cmdb=_CMDB,
            artifact_id="product.product.master" if schema_type == "JSON"
            else "product.product.created-value",
            group_id="default",
            schema_type=schema_type,
        )
        assert not any(f.rule == "pii_field_not_found" for f in result.failures), schema_type


@pytest.mark.asyncio
async def test_pii_fields_as_a_bare_string_still_coerces_on_the_json_path() -> None:
    # Review Focus 3. The Avro path already coerced this; the JSON path runs
    # the same code and must not regress it.
    body = json.loads(_GOOD_JSON_SCHEMA)
    body["metadata"]["piiFields"] = "email"
    result = await validate(
        content=json.dumps(body),
        properties={},
        cmdb=_CMDB,
        artifact_id="product.product.master",
        group_id="default",
        schema_type="JSON",
    )
    assert not any(f.rule == "pii_field_not_found" for f in result.failures)


@pytest.mark.asyncio
async def test_json_schema_body_still_runs_every_metadata_rule() -> None:
    # The gate opened; the rules did not get skipped. Without this, a gate that
    # returned `passed=True` for every JSON body would look like a fix.
    body = json.loads(_GOOD_JSON_SCHEMA)
    body["metadata"] = {
        "owner": {"service": "CI9999999"},
        "domain": "learning",
        "dataClassification": "unknown",
        "lifecycle": "gone",
        "piiFields": [],
    }
    result = await validate(
        content=json.dumps(body),
        properties={},
        cmdb=_CMDB,
        artifact_id="product.product.master",
        group_id="default",
        schema_type="JSON",
    )
    rules = [f.rule for f in result.failures]
    assert "owner_unresolved" in rules
    assert "domain_mismatch" in rules
    assert "classification_invalid" in rules
    assert "lifecycle_invalid" in rules


@pytest.mark.asyncio
async def test_json_schema_properties_mismatch() -> None:
    result = await validate(
        content=_GOOD_JSON_SCHEMA,
        properties={**_GOOD_PROPERTIES, "domain": "learning"},
        cmdb=_CMDB,
        artifact_id="product.product.master",
        group_id="default",
        schema_type="JSON",
    )
    assert any(f.rule == "properties_mismatch" for f in result.failures)


@pytest.mark.asyncio
async def test_a_bare_json_object_reports_the_missing_metadata_block() -> None:
    # Review Focus 1: neither a JSON Schema nor a mapping. It must reach the
    # metadata battery and say what is actually wrong, not raise on a missing
    # `properties` key.
    result = await validate(
        content=json.dumps({"a": 1}),
        properties={},
        cmdb=_CMDB,
        artifact_id="product.product.master",
        group_id="default",
        schema_type="JSON",
    )
    assert not result.passed
    assert any(f.rule == "metadata_block_missing" for f in result.failures)


# --- Mapping artifacts (UIFPE-597) ------------------------------------------


@pytest.mark.asyncio
async def test_mapping_body_passes_without_a_metadata_block() -> None:
    result = await validate(
        content=_GOOD_MAPPING,
        properties={},
        cmdb=_CMDB,
        artifact_id="product.product.mapping",
        group_id="product",
        schema_type="JSON",
    )
    assert result.passed, [f.rule for f in result.failures]


@pytest.mark.asyncio
async def test_mapping_body_never_reports_a_missing_metadata_block() -> None:
    # The whole point of the exemption: a mapping's contract never asked for
    # the block, so demanding it is a finding about the validator, not the file.
    result = await validate(
        content=_GOOD_MAPPING,
        properties={},
        cmdb=_CMDB,
        artifact_id="product.product.mapping",
        group_id="product",
        schema_type="JSON",
    )
    assert not any(f.rule == "metadata_block_missing" for f in result.failures)


@pytest.mark.asyncio
async def test_mapping_domain_mismatch() -> None:
    body = json.loads(_GOOD_MAPPING)
    body["domain"] = "learning"
    body["registry"]["group"] = "learning"  # isolate the rule under test
    result = await validate(
        content=json.dumps(body),
        properties={},
        cmdb=_CMDB,
        artifact_id="product.product.mapping",
        group_id="product",
        schema_type="JSON",
    )
    assert not result.passed
    assert any(f.rule == "mapping_domain_mismatch" for f in result.failures)


@pytest.mark.asyncio
async def test_mapping_registry_group_mismatch() -> None:
    body = json.loads(_GOOD_MAPPING)
    body["registry"]["group"] = "learning"
    result = await validate(
        content=json.dumps(body),
        properties={},
        cmdb=_CMDB,
        artifact_id="product.product.mapping",
        group_id="product",
        schema_type="JSON",
    )
    assert not result.passed
    assert any(f.rule == "mapping_registry_group_mismatch" for f in result.failures)


@pytest.mark.asyncio
async def test_mapping_columns_missing() -> None:
    result = await validate(
        content=json.dumps({
            "$schema": "../../gold-attribute-mapping.schema.json",
            "domain": "product",
            "table": "gold_product.product",
        }),
        properties={},
        cmdb=_CMDB,
        artifact_id="product.product.mapping",
        group_id="product",
        schema_type="JSON",
    )
    assert not result.passed
    assert any(f.rule == "mapping_columns_missing" for f in result.failures)


@pytest.mark.asyncio
async def test_mapping_with_no_registry_block_does_not_fail_the_group_rule() -> None:
    # `registry` is optional — the standard says a mapping's registration can
    # stay deferred. Absent is not a mismatch.
    body = json.loads(_GOOD_MAPPING)
    del body["registry"]
    result = await validate(
        content=json.dumps(body),
        properties={},
        cmdb=_CMDB,
        artifact_id="product.product.mapping",
        group_id="product",
        schema_type="JSON",
    )
    assert result.passed, [f.rule for f in result.failures]
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd stacks/apicurio && python3 -m pytest qe/tests/test_metadata_validator.py -q -k "json_schema or mapping or dollar_prefixed or bare_json"
```

Expected: FAIL. The `json_schema` and `mapping` cases all report `schema_type_unsupported` instead of the rule each one asserts.

- [ ] **Step 3: Replace the type gate**

In `src/app/services/metadata_validator.py`, replace the gate (currently lines 49–61, the `if schema_type != "AVRO":` block) with:

```python
    if schema_type not in ("AVRO", "JSON"):
        failures.append(ValidationFailure(
            rule="schema_type_unsupported",
            message=f"Metadata validation is only implemented for AVRO and JSON; got {schema_type}.",
        ))
        return ValidationResult(
            artifact_id=artifact_id,
            group_id=group_id,
            passed=False,
            failures=failures,
            validated_at=now,
        )

    # --- Parse body ---
    try:
        body = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        failures.append(ValidationFailure(
            rule="body_unparseable",
            message="Schema body is not valid JSON.",
        ))
        return ValidationResult(
            artifact_id=artifact_id,
            group_id=group_id,
            passed=False,
            failures=failures,
            validated_at=now,
        )

    # A gold attribute mapping carries no metadata block — its contract never
    # asked it to — so it is validated against that contract instead. Checked
    # AFTER the parse, because the shape is what decides, not the name.
    if isinstance(body, dict) and _is_mapping_body(body):
        return _validate_mapping(body, artifact_id, group_id, now)
```

Then **delete** the original `# --- Parse body ---` block that followed the old gate, so the body is parsed once. The next surviving line is `meta_block = body.get("metadata")`.

- [ ] **Step 4: Point the piiFields rule at the shared helper**

Replace the `piiFields` rule block (the `if pii_fields:` section, currently lines 152–170) with:

```python
    # --- Rule: piiFields paths exist as top-level field names in the body ---
    if pii_fields:
        # First dot-segment only, and a leading `$.` stripped — deliberately
        # the same depth for both shapes. `$.customerId` is
        # metadata-block.schema.json's own example and failed against Avro too.
        declared = _top_level_field_names(body)
        for path in pii_fields:
            bare = path[2:] if path.startswith("$.") else path
            top_level = bare.split(".")[0]
            if top_level and top_level not in declared:
                failures.append(ValidationFailure(
                    rule="pii_field_not_found",
                    message=(
                        f"piiField path '{path}' does not resolve to a "
                        "top-level field in the schema body."
                    ),
                ))
```

- [ ] **Step 5: Add `_validate_mapping`**

Insert immediately before `def _check_properties_mismatch(`:

```python
def _validate_mapping(
    body: dict,
    artifact_id: str,
    group_id: str,
    now: datetime,
) -> ValidationResult:
    """Validate a gold attribute mapping against its own contract.

    Three rules, and deliberately only three. `sortKey` coverage,
    `pick.partitionBy` coverage, the prose-transform ban, `{"rule"}` reference
    resolution and the `sqlExpr` allowlist all live in CI —
    `standards/gold-attribute-mapping.md` places them there, in `jsonschema`
    validation plus `domains/product/qe/test_intake_hygiene.py`, and names
    which copy is authoritative. Re-implementing them here would make a second
    copy of the contract to keep in step.

    What is left is what the REGISTRY alone is positioned to check: that the
    artifact's registry coordinate, its declared domain and its own `registry`
    block all agree.
    """
    failures: list[ValidationFailure] = []

    domain = body.get("domain")
    declared_domain = artifact_id.split(".")[0]
    if domain != declared_domain:
        failures.append(ValidationFailure(
            rule="mapping_domain_mismatch",
            message=(
                f"Mapping declares domain '{domain}' but the artifact_id "
                f"prefix is '{declared_domain}'."
            ),
        ))

    registry = body.get("registry") or {}
    registry_group = registry.get("group")
    # Absent is not a mismatch: the standard says a mapping's registration can
    # stay deferred while the coordinate is still declared elsewhere.
    if registry_group is not None and registry_group != domain:
        failures.append(ValidationFailure(
            rule="mapping_registry_group_mismatch",
            message=(
                f"Mapping's registry.group is '{registry_group}' but its "
                f"declared domain is '{domain}'."
            ),
        ))

    if not body.get("columns"):
        failures.append(ValidationFailure(
            rule="mapping_columns_missing",
            message="Mapping declares no columns.",
        ))

    return ValidationResult(
        artifact_id=artifact_id,
        group_id=group_id,
        passed=len(failures) == 0,
        failures=failures,
        validated_at=now,
    )
```

- [ ] **Step 6: Run the new tests to verify they pass**

```bash
cd stacks/apicurio && python3 -m pytest qe/tests/test_metadata_validator.py -q -k "json_schema or mapping or dollar_prefixed or bare_json"
```

Expected: PASS, 13 tests.

- [ ] **Step 7: Run the whole suite and ruff**

```bash
cd stacks/apicurio && python3 -m pytest qe/ -q --ignore=qe/tests/integration && python3 -m ruff check src/ qe/
```

Expected: everything PASS, ruff clean. `test_non_avro_returns_unsupported_failure` uses PROTOBUF and must still pass **unmodified** — it is the guard that this change did not open more than it meant to.

- [ ] **Step 8: Commit**

```bash
cd /Users/raj9karu/Documents/Claude/Projects/universal-Integration-framework
git add stacks/apicurio/src/app/services/metadata_validator.py stacks/apicurio/qe/tests/test_metadata_validator.py
git commit -F - <<'MSG'
Validate metadata on JSON bodies, and exempt mapping artifacts

The gate refused everything that was not Avro, so every gold master.json in
the repository was reported non-compliant for no reason but its type, despite
carrying exactly the metadata block Avro carries. It now splits three ways:
Avro and JSON Schema both get the full battery, and a gold attribute mapping
gets its own three rules.

The mapping exemption is not a hole. A mapping carries no metadata block
because its contract never asked it to, so demanding one was a finding about
the validator. What replaces it is what the registry alone can check — that
the artifact coordinate, the declared domain and the file's own registry block
agree. sortKey coverage, the prose-transform ban and the sqlExpr allowlist
stay in CI, where the mapping standard puts them and names authoritative.

piiFields now resolve through the shared helper and strip a leading `$.`,
which fixes metadata-block.schema.json's own worked example — it was failing
against Avro too.

UIFPE-597 #comment The Avro-only gate now splits three ways: JSON Schema bodies get the full metadata battery with piiFields resolved against `properties`, and mapping artifacts are detected by body shape and validated against their own contract (domain/registry.group/columns) instead of a metadata block they never carry. 13 new tests; the PROTOBUF unsupported test stays green unmodified.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
MSG
```

---

## Task 3: Prove the full gate is green, and record the rules

**Files:**
- Modify: `src/schemas/metadata-block.schema.json` (the `piiFields` description)
- Test: none new — this task runs the stack gate.

**Interfaces:**
- Consumes: the finished validator from Task 2.
- Produces: nothing consumed later.

- [ ] **Step 1: Update the piiFields contract text to match the implementation**

In `src/schemas/metadata-block.schema.json`, replace the `piiFields` `description` string with:

```
Validator check 8 (`pii_field_not_found`): each entry's first dot-segment must name a real top-level field in the schema body — `fields[].name` for Avro, a `properties` key for JSON Schema. A leading `$.` is stripped first. Absent or empty means no PII is declared — which is a claim, not an absence of obligation.
```

- [ ] **Step 2: Verify the file is still valid JSON**

```bash
cd stacks/apicurio && python3 -c "import json; json.load(open('src/schemas/metadata-block.schema.json')); print('valid')"
```

Expected: `valid`.

- [ ] **Step 3: Run the stack's whole gate**

```bash
cd stacks/apicurio && qe/ci.sh --allow-missing-tools
```

Expected: `all gates green`, with skips only for tools this machine lacks. If `ruff`, `pytest`, `eslint`, `vitest` or the tsc build FAIL, fix before committing — those four are the ones this task's changes can break.

- [ ] **Step 4: Commit**

```bash
cd /Users/raj9karu/Documents/Claude/Projects/universal-Integration-framework
git add stacks/apicurio/src/schemas/metadata-block.schema.json
git commit -F - <<'MSG'
State the piiFields rule as the validator now implements it

The contract file said check 8 resolved against "a real top-level field in the
schema body" without saying which key declares one, and said nothing about the
`$.` prefix its own worked example uses. Both are now stated. The file's own
description calls the validator "what actually runs" if the two diverge, so
this closes that divergence rather than leaving a reader to find it.

UIFPE-597 #comment Updated metadata-block.schema.json's piiFields description to name both declaring keys (Avro `fields[].name`, JSON Schema `properties`) and the `$.` strip, matching what the validator now does. Full stack gate green.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
MSG
```

---

## Task 4: Detect a mapping and extract its facts

**Files:**
- Create: `ui/src/features/schemas/mapping-body.ts`
- Test: `ui/src/features/schemas/mapping-body.test.ts`

**Interfaces:**
- Consumes: nothing from earlier tasks (independent of Tasks 1–3).
- Produces, all consumed by Tasks 5, 6, 8, 9 and 10:

```ts
export interface EntityKeyPair { goldColumn: string; drivingColumn: string }
export interface MappingRegistry { group: string; artifact: string; compatibility: string | null }
export interface MappingBatch {
  requestedBy: string; targetDate: string;
  backfillMode: string; newChannel: string | null;
}
export interface MappingFacts {
  domain: string | null;
  table: string | null;
  drivingTable: string | null;
  drivingSilverTable: string | null;
  entityKey: EntityKeyPair[];
  registry: MappingRegistry | null;
  batch: MappingBatch | null;
}
export function isMappingBody(value: unknown): boolean
export function parseMappingFacts(body: Record<string, unknown>): MappingFacts
```

- [ ] **Step 1: Write the failing tests**

Create `ui/src/features/schemas/mapping-body.test.ts`:

```ts
import { describe, it, expect } from 'vitest';
import { isMappingBody, parseMappingFacts } from './mapping-body';

const MAPPING_OBJ = {
  $schema: '../../gold-attribute-mapping.schema.json',
  domain: 'product',
  table: 'gold_product.product',
  registry: {
    group: 'product',
    artifact: 'product-gold-attribute-mapping',
    compatibility: 'BACKWARD',
  },
  drivingTable: 'MTL_SYSTEM_ITEMS_B',
  entityKey: [
    { goldColumn: 'inventoryItemId', drivingColumn: 'INVENTORY_ITEM_ID' },
    { goldColumn: 'organizationId', drivingColumn: 'ORGANIZATION_ID' },
  ],
  batch: {
    requestedBy: 'Reyas Ahamed H',
    targetDate: '2026-07-22',
    backfillMode: 'silent',
    newChannel: null,
  },
  columns: [
    {
      goldColumnName: 'inventoryItemId',
      businessDescription: 'Oracle internal surrogate.',
      kind: 'scalar',
      dataType: 'integer',
      source: { table: 'MTL_SYSTEM_ITEMS_B', column: 'INVENTORY_ITEM_ID', filter: null },
      transform: null,
      channels: { event: false, message: false, api: false },
      nullable: false,
    },
  ],
};

describe('isMappingBody', () => {
  it('matches a body with domain, table and a columns list', () => {
    expect(isMappingBody({ domain: 'p', table: 't', columns: [] })).toBe(true);
  });

  it('matches on a $schema naming the mapping contract alone', () => {
    // A mapping whose columns are not authored yet still declares what it is.
    expect(isMappingBody({ $schema: '../gold-attribute-mapping.schema.json' })).toBe(true);
  });

  it('rejects an Avro record', () => {
    expect(isMappingBody({ type: 'record', name: 'X', fields: [] })).toBe(false);
  });

  it('rejects a JSON Schema master', () => {
    expect(
      isMappingBody({
        $schema: 'https://json-schema.org/draft/2020-12/schema',
        type: 'object',
        properties: { id: { type: 'string' } },
      }),
    ).toBe(false);
  });

  it('rejects an OpenAPI document', () => {
    expect(isMappingBody({ openapi: '3.1.0', paths: {} })).toBe(false);
  });

  it('rejects columns that are not a list', () => {
    // Guards the same case the backend guards: `columns: {}` must not read as
    // a mapping, or the flatteners would iterate a dict.
    expect(isMappingBody({ domain: 'p', table: 't', columns: {} })).toBe(false);
  });

  it('rejects a non-object', () => {
    expect(isMappingBody(null)).toBe(false);
    expect(isMappingBody('a string')).toBe(false);
    expect(isMappingBody([1, 2, 3])).toBe(false);
  });
});

describe('parseMappingFacts', () => {
  it('reads the gold table, domain and driving table', () => {
    const facts = parseMappingFacts(MAPPING_OBJ);
    expect(facts.domain).toBe('product');
    expect(facts.table).toBe('gold_product.product');
    expect(facts.drivingTable).toBe('MTL_SYSTEM_ITEMS_B');
  });

  it('keeps the entity key ordered', () => {
    // It is the gold table's ORDERED primary key — reordering it would
    // misstate the grain.
    const facts = parseMappingFacts(MAPPING_OBJ);
    expect(facts.entityKey).toEqual([
      { goldColumn: 'inventoryItemId', drivingColumn: 'INVENTORY_ITEM_ID' },
      { goldColumn: 'organizationId', drivingColumn: 'ORGANIZATION_ID' },
    ]);
  });

  it('reads the registry coordinate and the batch', () => {
    const facts = parseMappingFacts(MAPPING_OBJ);
    expect(facts.registry).toEqual({
      group: 'product',
      artifact: 'product-gold-attribute-mapping',
      compatibility: 'BACKWARD',
    });
    expect(facts.batch?.requestedBy).toBe('Reyas Ahamed H');
    expect(facts.batch?.newChannel).toBeNull();
  });

  it('defaults compatibility to null rather than inventing BACKWARD', () => {
    // The contract says it defaults to BACKWARD when Apicurio enforces it,
    // but the VIEW must not claim a rule the artifact did not declare — the
    // facts band already shows `not set` honestly for schema compatibility.
    const facts = parseMappingFacts({
      ...MAPPING_OBJ,
      registry: { group: 'product', artifact: 'a' },
    });
    expect(facts.registry?.compatibility).toBeNull();
  });

  it('returns nulls and empties for a mapping declaring only the minimum', () => {
    const facts = parseMappingFacts({ domain: 'p', table: 't', columns: [] });
    expect(facts.drivingTable).toBeNull();
    expect(facts.drivingSilverTable).toBeNull();
    expect(facts.entityKey).toEqual([]);
    expect(facts.registry).toBeNull();
    expect(facts.batch).toBeNull();
  });

  it('reads the templated driving Silver table when one is declared', () => {
    const facts = parseMappingFacts({
      ...MAPPING_OBJ,
      drivingSilverTable: 'uif_echo_{env}_silver_tep.mtl_system_items_b',
    });
    expect(facts.drivingSilverTable).toBe('uif_echo_{env}_silver_tep.mtl_system_items_b');
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd stacks/apicurio && npm --prefix ui run test -- --run mapping-body
```

Expected: FAIL — `Failed to resolve import "./mapping-body"`.

- [ ] **Step 3: Create the module with the detector and the facts parser**

Create `ui/src/features/schemas/mapping-body.ts`:

```ts
/**
 * Reads a registered gold attribute mapping into the shapes the Mapping tab
 * renders: a facts band, three flat sheets — one per column kind — and the
 * named definitions the rows cite.
 *
 * Deliberately shaped like `schema-body.ts`, whose docstring states the
 * governing idea: one parse, several presentations, so a source table named
 * in a row is the same one the Silver reference resolves and a search match
 * counts once.
 *
 * A mapping is a COMPLETELY different body from a schema. It has no `fields`,
 * no `properties` and no metadata block — so `parseSchemaBody` finds nothing
 * in it, the Fields view renders empty and the graph draws its columns as
 * anonymous objects. That is why this module exists rather than an extension
 * of that one.
 *
 * Everything here is pure: no React, no network. The detail endpoint already
 * returns the body (`SchemaDetailRead.content`), so the whole view is a
 * function of a string.
 *
 * The contract is `stacks/apicurio/standards/gold-attribute-mapping.schema.json`;
 * the worked standard beside it explains the parts JSON Schema cannot state.
 */

export interface EntityKeyPair {
  goldColumn: string;
  drivingColumn: string;
}

export interface MappingRegistry {
  group: string;
  artifact: string;
  /** Null when the artifact did not declare one — never defaulted here. */
  compatibility: string | null;
}

export interface MappingBatch {
  requestedBy: string;
  targetDate: string;
  backfillMode: string;
  newChannel: string | null;
}

export interface MappingFacts {
  domain: string | null;
  table: string | null;
  drivingTable: string | null;
  drivingSilverTable: string | null;
  entityKey: EntityKeyPair[];
  registry: MappingRegistry | null;
  batch: MappingBatch | null;
}

/** A plain object, which is the only thing any of this can read. */
function asRecord(value: unknown): Record<string, unknown> | null {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return null;
  return value as Record<string, unknown>;
}

function str(value: unknown): string | null {
  return typeof value === 'string' && value.length > 0 ? value : null;
}

/**
 * True when this body is a gold attribute mapping.
 *
 * By SHAPE, never by artifact name. Two naming conventions already exist for
 * the same artifact — `product.product.mapping` is the dev registry's
 * coordinate, `product-gold-attribute-mapping` is what the files' own
 * `registry.artifact` declares — so a rule keyed on either breaks on the
 * other. `_is_mapping_body` in `app/services/metadata_validator.py` is the
 * same predicate in Python; the design spec §3.1 is their shared definition.
 *
 * `columns` must be an ARRAY. A body carrying `columns: {}` is not a mapping,
 * and taking it for one would have the flatteners read a dict's keys as
 * column entries.
 */
export function isMappingBody(value: unknown): boolean {
  const body = asRecord(value);
  if (!body) return false;
  const schemaRef = body.$schema;
  if (typeof schemaRef === 'string' && schemaRef.includes('gold-attribute-mapping')) {
    return true;
  }
  return (
    typeof body.domain === 'string' &&
    typeof body.table === 'string' &&
    Array.isArray(body.columns)
  );
}

export function parseMappingFacts(body: Record<string, unknown>): MappingFacts {
  const registryRaw = asRecord(body.registry);
  const registry: MappingRegistry | null =
    registryRaw && str(registryRaw.group) && str(registryRaw.artifact)
      ? {
          group: String(registryRaw.group),
          artifact: String(registryRaw.artifact),
          // Not defaulted to BACKWARD. The contract says Apicurio defaults it,
          // but the view must not claim a rule the artifact did not declare —
          // the schema facts band already shows `not set` honestly.
          compatibility: str(registryRaw.compatibility),
        }
      : null;

  const batchRaw = asRecord(body.batch);
  const batch: MappingBatch | null = batchRaw
    ? {
        requestedBy: str(batchRaw.requestedBy) ?? '',
        targetDate: str(batchRaw.targetDate) ?? '',
        backfillMode: str(batchRaw.backfillMode) ?? '',
        newChannel: str(batchRaw.newChannel),
      }
    : null;

  const entityKey: EntityKeyPair[] = (Array.isArray(body.entityKey) ? body.entityKey : [])
    .map(asRecord)
    .filter((p): p is Record<string, unknown> => p !== null)
    .map((p) => ({
      goldColumn: str(p.goldColumn) ?? '',
      drivingColumn: str(p.drivingColumn) ?? '',
    }))
    .filter((p) => p.goldColumn !== '');

  return {
    domain: str(body.domain),
    table: str(body.table),
    drivingTable: str(body.drivingTable),
    drivingSilverTable: str(body.drivingSilverTable),
    entityKey,
    registry,
    batch,
  };
}
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd stacks/apicurio && npm --prefix ui run test -- --run mapping-body
```

Expected: PASS, 13 tests.

- [ ] **Step 5: Lint and typecheck**

```bash
cd stacks/apicurio && npm --prefix ui run lint
cd stacks/apicurio/ui && npx tsc -b
```

Expected: both clean.

- [ ] **Step 6: Commit**

```bash
cd /Users/raj9karu/Documents/Claude/Projects/universal-Integration-framework
git add stacks/apicurio/ui/src/features/schemas/mapping-body.ts stacks/apicurio/ui/src/features/schemas/mapping-body.test.ts
git commit -F - <<'MSG'
Recognise a mapping body and read its facts

First half of the mapping parser. A gold attribute mapping is a completely
different body from a schema — no fields, no properties, no metadata block —
so parseSchemaBody finds nothing in it and the Fields view renders empty.
This module is why that stops being the only reading available.

Detection is by shape rather than by artifact name, matching the predicate
the validator uses in Python, because two naming conventions already exist
for the same artifact and a rule keyed on either breaks on the other. It
requires columns to be an array, so `columns: {}` is not mistaken for a
mapping and does not have its keys read as columns.

Registry compatibility is left null when unset rather than defaulted to
BACKWARD: the contract says Apicurio defaults it, but the view must not claim
a rule the artifact did not declare.

UIFPE-598 #comment Added ui/src/features/schemas/mapping-body.ts with isMappingBody (shape-based detection, rejecting a non-array `columns`) and parseMappingFacts (domain, gold table, driving table, ordered entity key, registry coordinate, batch). 13 tests.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
MSG
```

---

## Task 5: Flatten each column kind to workbook rows

**Files:**
- Modify: `ui/src/features/schemas/mapping-body.ts`
- Test: `ui/src/features/schemas/mapping-body.test.ts`

**Interfaces:**
- Consumes: `asRecord`, `str`, `isMappingBody`, `parseMappingFacts` from Task 4 (`asRecord` and `str` are module-private — use them, do not re-declare).
- Produces, consumed by Tasks 6, 8, 9, 10:

```ts
export type MappingKind = 'scalar' | 'conditional' | 'nested';
export interface Channels { event: boolean; message: boolean; api: boolean }
export interface RawSource {
  table: string | null; column: string | null;
  filter: string | null; join: unknown;
}
export interface MappingRowBase {
  key: string;                 // stable React key, unique within its sheet
  goldColumnName: string;
  /** Empty on a continuation row — the workbook shows it once per group. */
  businessDescription: string;
  /** False on a continuation row. Drives whether the gold cell repeats. */
  isFirstOfGroup: boolean;
  source: RawSource;
  channels: Channels;
  notes: string;
  joinGrain: string | null;
  transform: unknown;
  derivation: unknown;
}
export interface ScalarRow extends MappingRowBase {
  kind: 'scalar'; dataType: string; nullable: boolean;
}
export interface ConditionalRow extends MappingRowBase {
  kind: 'conditional'; dataType: string;
  discriminator: string; condition: string;
  fallback: string; scope: unknown;
}
export interface NestedRow extends MappingRowBase {
  kind: 'nested'; fieldName: string; fieldType: string;
  joinKey: string; sortKey: boolean; pick: unknown;
}
export type MappingRow = ScalarRow | ConditionalRow | NestedRow;
export function flattenScalar(columns: unknown[]): ScalarRow[]
export function flattenConditional(columns: unknown[]): ConditionalRow[]
export function flattenNested(columns: unknown[]): NestedRow[]
```

- [ ] **Step 1: Write the failing tests**

Append to `ui/src/features/schemas/mapping-body.test.ts`:

```ts
import {
  flattenScalar,
  flattenConditional,
  flattenNested,
} from './mapping-body';

const SCALAR_COL = {
  goldColumnName: 'itemStatus',
  businessDescription: 'Lifecycle status of the item.',
  kind: 'scalar',
  dataType: 'string',
  source: {
    table: 'MTL_SYSTEM_ITEMS_B',
    column: 'INVENTORY_ITEM_STATUS_CODE',
    filter: null,
  },
  transform: null,
  channels: { event: true, message: true, api: true },
  nullable: true,
  notes: 'A note.',
};

const CONDITIONAL_COL = {
  goldColumnName: 'isbn13',
  businessDescription: 'ISBN-13.',
  kind: 'conditional',
  dataType: 'string',
  discriminator: { table: 'EGO_MTL_SY_ITEMS_EXT_B', column: 'ITEM_CATALOG_GROUP_ID' },
  channels: { event: true, message: true, api: true },
  branches: [
    {
      condition: 'ELSE (<> 10002)',
      source: {
        table: 'EGO_MTL_SY_ITEMS_EXT_B',
        column: 'C_EXT_ATTR8',
        filter: 'ATTR_GROUP_ID = 741',
      },
      fallback: 'returnNull',
      notes: null,
    },
    {
      condition: '10002 (Digital Catalog)',
      source: {
        table: 'EGO_MTL_SY_ITEMS_EXT_B',
        column: 'C_EXT_ATTR4',
        filter: 'ATTR_GROUP_ID = 1401',
      },
      fallback: 'fallbackToElse',
      notes: 'branch 2',
    },
  ],
};

const NESTED_COL = {
  goldColumnName: 'bomComponents',
  businessDescription: 'Bill-of-material component lines.',
  kind: 'nested',
  joinKey: 'BILL_SEQUENCE_ID -> item',
  filter: 'EXPLOSION_TYPE = 1',
  channels: { event: false, message: false, api: true },
  fields: [
    {
      name: 'componentItemId',
      type: 'integer',
      sortKey: true,
      source: { table: 'BOM_COMPONENTS_B', column: 'COMPONENT_ITEM_ID' },
      transform: null,
    },
    {
      name: 'quantity',
      type: 'number',
      sortKey: false,
      source: { table: 'BOM_COMPONENTS_B', column: 'COMPONENT_QUANTITY' },
      transform: null,
    },
  ],
};

describe('flattenScalar', () => {
  it('produces one row per column, carrying the workbook cells', () => {
    const [row] = flattenScalar([SCALAR_COL]);
    expect(row.goldColumnName).toBe('itemStatus');
    expect(row.businessDescription).toBe('Lifecycle status of the item.');
    expect(row.dataType).toBe('string');
    expect(row.source.table).toBe('MTL_SYSTEM_ITEMS_B');
    expect(row.source.column).toBe('INVENTORY_ITEM_STATUS_CODE');
    expect(row.source.filter).toBeNull();
    expect(row.nullable).toBe(true);
    expect(row.channels).toEqual({ event: true, message: true, api: true });
    expect(row.notes).toBe('A note.');
    expect(row.isFirstOfGroup).toBe(true);
  });

  it('ignores columns of another kind', () => {
    expect(flattenScalar([CONDITIONAL_COL, NESTED_COL])).toEqual([]);
  });

  it('renders a legacy joinGrain and leaves it null otherwise', () => {
    // DEPRECATED per UIFPE-104. Shown when a legacy file still carries one so
    // the reader sees what the artifact says; null otherwise so the column
    // does not teach the deprecated thing.
    expect(flattenScalar([SCALAR_COL])[0].joinGrain).toBeNull();
    expect(flattenScalar([{ ...SCALAR_COL, joinGrain: 'Org Level' }])[0].joinGrain)
      .toBe('Org Level');
  });
});

describe('flattenConditional', () => {
  it('produces one row per branch', () => {
    const rows = flattenConditional([CONDITIONAL_COL]);
    expect(rows).toHaveLength(2);
    expect(rows.map((r) => r.condition)).toEqual([
      'ELSE (<> 10002)',
      '10002 (Digital Catalog)',
    ]);
  });

  it('carries the description on the first branch row only', () => {
    // Exactly what the workbook sheet does: the description is a property of
    // the gold column, not of each branch, and repeating it reads as though
    // the branches had different descriptions.
    const rows = flattenConditional([CONDITIONAL_COL]);
    expect(rows[0].businessDescription).toBe('ISBN-13.');
    expect(rows[0].isFirstOfGroup).toBe(true);
    expect(rows[1].businessDescription).toBe('');
    expect(rows[1].isFirstOfGroup).toBe(false);
  });

  it('renders the discriminator as Table.Column', () => {
    const rows = flattenConditional([CONDITIONAL_COL]);
    expect(rows[0].discriminator).toBe('EGO_MTL_SY_ITEMS_EXT_B.ITEM_CATALOG_GROUP_ID');
  });

  it('carries each branch its own source, filter and fallback', () => {
    const rows = flattenConditional([CONDITIONAL_COL]);
    expect(rows[0].source.column).toBe('C_EXT_ATTR8');
    expect(rows[0].source.filter).toBe('ATTR_GROUP_ID = 741');
    expect(rows[0].fallback).toBe('returnNull');
    expect(rows[1].source.column).toBe('C_EXT_ATTR4');
    expect(rows[1].fallback).toBe('fallbackToElse');
  });

  it('gives each branch row a distinct key', () => {
    // Two branches of one column share a gold column name, so the name alone
    // is not a React key — duplicate keys silently drop rows.
    const rows = flattenConditional([CONDITIONAL_COL]);
    expect(new Set(rows.map((r) => r.key)).size).toBe(2);
  });

  it('yields no rows for a column with an empty branches array', () => {
    // Review Focus 4. One row of undefineds is worse than none — it asserts
    // a branch exists.
    expect(flattenConditional([{ ...CONDITIONAL_COL, branches: [] }])).toEqual([]);
    expect(flattenConditional([{ ...CONDITIONAL_COL, branches: undefined }])).toEqual([]);
  });
});

describe('flattenNested', () => {
  it('produces one row per field', () => {
    const rows = flattenNested([NESTED_COL]);
    expect(rows).toHaveLength(2);
    expect(rows.map((r) => r.fieldName)).toEqual(['componentItemId', 'quantity']);
    expect(rows.map((r) => r.fieldType)).toEqual(['integer', 'number']);
  });

  it('carries the description on the first field row only', () => {
    const rows = flattenNested([NESTED_COL]);
    expect(rows[0].businessDescription).toBe('Bill-of-material component lines.');
    expect(rows[1].businessDescription).toBe('');
  });

  it('surfaces the sort key and the join key', () => {
    const rows = flattenNested([NESTED_COL]);
    expect(rows[0].sortKey).toBe(true);
    expect(rows[1].sortKey).toBe(false);
    expect(rows[0].joinKey).toBe('BILL_SEQUENCE_ID -> item');
  });

  it("falls back to the column's shared filter when a field declares none", () => {
    // The contract puts `filter` on the nested COLUMN, shared by all its
    // fields; the workbook has one Source Filter cell per field row. Showing
    // the shared filter is what makes those agree.
    const rows = flattenNested([NESTED_COL]);
    expect(rows[0].source.filter).toBe('EXPLOSION_TYPE = 1');
  });

  it('prefers a field-level filter over the shared one', () => {
    const col = {
      ...NESTED_COL,
      fields: [
        {
          ...NESTED_COL.fields[0],
          source: { table: 'T', column: 'C', filter: 'ATTR_GROUP_ID = 798' },
        },
      ],
    };
    expect(flattenNested([col])[0].source.filter).toBe('ATTR_GROUP_ID = 798');
  });

  it('handles a field declaring no source at all', () => {
    // The contract allows `source: null` — "declared with no Silver source
    // yet; the column is rendered NULL until a source registers."
    const col = { ...NESTED_COL, fields: [{ name: 'f', type: 'string', sortKey: true, source: null }] };
    const [row] = flattenNested([col]);
    expect(row.source.table).toBeNull();
    expect(row.source.column).toBeNull();
  });

  it('yields no rows for a column with an empty fields array', () => {
    // Review Focus 4, the nested half.
    expect(flattenNested([{ ...NESTED_COL, fields: [] }])).toEqual([]);
    expect(flattenNested([{ ...NESTED_COL, fields: undefined }])).toEqual([]);
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd stacks/apicurio && npm --prefix ui run test -- --run mapping-body
```

Expected: FAIL — `flattenScalar` is not exported.

- [ ] **Step 3: Add the row types and the three flatteners**

Append to `ui/src/features/schemas/mapping-body.ts`:

```ts
export type MappingKind = 'scalar' | 'conditional' | 'nested';

export interface Channels {
  event: boolean;
  message: boolean;
  api: boolean;
}

export interface RawSource {
  table: string | null;
  column: string | null;
  filter: string | null;
  /** The structured join, resolved by `resolveJoin` in Task 6. */
  join: unknown;
}

export interface MappingRowBase {
  /** Stable React key, unique within its sheet. */
  key: string;
  goldColumnName: string;
  /**
   * Empty on a continuation row. The workbook carries the description once
   * per gold column, not once per branch or field — repeating it reads as
   * though the branches had different descriptions.
   */
  businessDescription: string;
  isFirstOfGroup: boolean;
  source: RawSource;
  channels: Channels;
  notes: string;
  /**
   * DEPRECATED (UIFPE-104), superseded by the per-source join. Carried so a
   * legacy file's value is still shown; null means the artifact declared
   * none, and the header says the column is superseded.
   */
  joinGrain: string | null;
  transform: unknown;
  derivation: unknown;
}

export interface ScalarRow extends MappingRowBase {
  kind: 'scalar';
  dataType: string;
  nullable: boolean;
}

export interface ConditionalRow extends MappingRowBase {
  kind: 'conditional';
  dataType: string;
  /** `Table.Column`, as the workbook's Discriminator cell shows it. */
  discriminator: string;
  condition: string;
  fallback: string;
  /** The branch scope, resolved by `resolveScope` in Task 6. */
  scope: unknown;
}

export interface NestedRow extends MappingRowBase {
  kind: 'nested';
  fieldName: string;
  fieldType: string;
  joinKey: string;
  sortKey: boolean;
  /** The ranked pick, resolved by `resolvePick` in Task 6. */
  pick: unknown;
}

export type MappingRow = ScalarRow | ConditionalRow | NestedRow;

const NO_SOURCE: RawSource = { table: null, column: null, filter: null, join: null };

function readSource(value: unknown, sharedFilter: string | null = null): RawSource {
  const src = asRecord(value);
  if (!src) return { ...NO_SOURCE, filter: sharedFilter };
  return {
    table: str(src.table),
    column: str(src.column),
    // A field-level filter wins; the nested column's shared `filter` is the
    // fallback, because the contract puts it on the column while the workbook
    // has one Source Filter cell per field row.
    filter: str(src.filter) ?? sharedFilter,
    join: src.join ?? null,
  };
}

function readChannels(value: unknown): Channels {
  const c = asRecord(value);
  return {
    event: c?.event === true,
    message: c?.message === true,
    api: c?.api === true,
  };
}

function columnsOfKind(columns: unknown[], kind: MappingKind): Record<string, unknown>[] {
  return columns
    .map(asRecord)
    .filter((c): c is Record<string, unknown> => c !== null && c.kind === kind);
}

export function flattenScalar(columns: unknown[]): ScalarRow[] {
  return columnsOfKind(columns, 'scalar').map((col, i) => ({
    kind: 'scalar' as const,
    key: `${str(col.goldColumnName) ?? 'col'}#${i}`,
    goldColumnName: str(col.goldColumnName) ?? '',
    businessDescription: str(col.businessDescription) ?? '',
    isFirstOfGroup: true,
    dataType: str(col.dataType) ?? '',
    source: readSource(col.source),
    channels: readChannels(col.channels),
    nullable: col.nullable === true,
    notes: str(col.notes) ?? '',
    joinGrain: str(col.joinGrain),
    transform: col.transform ?? null,
    derivation: col.derivation ?? null,
  }));
}

export function flattenConditional(columns: unknown[]): ConditionalRow[] {
  const rows: ConditionalRow[] = [];
  columnsOfKind(columns, 'conditional').forEach((col, colIndex) => {
    const branches = Array.isArray(col.branches) ? col.branches : [];
    const disc = asRecord(col.discriminator);
    const discriminator =
      disc && str(disc.table) && str(disc.column)
        ? `${String(disc.table)}.${String(disc.column)}`
        : '';
    const goldColumnName = str(col.goldColumnName) ?? '';

    branches.map(asRecord).forEach((branch, branchIndex) => {
      if (!branch) return;
      rows.push({
        kind: 'conditional',
        // The gold column name alone is not unique — every branch of one
        // column shares it, and duplicate React keys silently drop rows.
        key: `${goldColumnName}#${colIndex}#${branchIndex}`,
        goldColumnName,
        businessDescription: branchIndex === 0 ? str(col.businessDescription) ?? '' : '',
        isFirstOfGroup: branchIndex === 0,
        dataType: branchIndex === 0 ? str(col.dataType) ?? '' : '',
        discriminator: branchIndex === 0 ? discriminator : '',
        condition: str(branch.condition) ?? '',
        source: readSource(branch.source),
        fallback: str(branch.fallback) ?? '',
        scope: branch.scope ?? null,
        channels: readChannels(col.channels),
        notes: str(branch.notes) ?? '',
        joinGrain: str(branch.joinGrain) ?? str(col.joinGrain),
        // A branch's own transform applies BEFORE the pick, to that branch's
        // expression; the column's applies after, to whichever branch won.
        // The row shows the branch's when it has one.
        transform: branch.transform ?? col.transform ?? null,
        derivation: col.derivation ?? null,
      });
    });
  });
  return rows;
}

export function flattenNested(columns: unknown[]): NestedRow[] {
  const rows: NestedRow[] = [];
  columnsOfKind(columns, 'nested').forEach((col, colIndex) => {
    const fields = Array.isArray(col.fields) ? col.fields : [];
    const sharedFilter = str(col.filter);
    const goldColumnName = str(col.goldColumnName) ?? '';
    const joinKey = str(col.joinKey) ?? '';

    fields.map(asRecord).forEach((field, fieldIndex) => {
      if (!field) return;
      rows.push({
        kind: 'nested',
        key: `${goldColumnName}#${colIndex}#${fieldIndex}`,
        goldColumnName,
        businessDescription: fieldIndex === 0 ? str(col.businessDescription) ?? '' : '',
        isFirstOfGroup: fieldIndex === 0,
        fieldName: str(field.name) ?? '',
        fieldType: str(field.type) ?? '',
        joinKey: fieldIndex === 0 ? joinKey : '',
        sortKey: field.sortKey === true,
        source: readSource(field.source, sharedFilter),
        channels: readChannels(col.channels),
        notes: str(field.notes) ?? '',
        joinGrain: str(col.joinGrain),
        transform: field.transform ?? null,
        derivation: col.derivation ?? null,
        pick: col.pick ?? null,
      });
    });
  });
  return rows;
}
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd stacks/apicurio && npm --prefix ui run test -- --run mapping-body
```

Expected: PASS — the 13 from Task 4 plus 16 new.

- [ ] **Step 5: Lint and typecheck**

```bash
cd stacks/apicurio && npm --prefix ui run lint
cd stacks/apicurio/ui && npx tsc -b
```

Expected: both clean.

- [ ] **Step 6: Commit**

```bash
cd /Users/raj9karu/Documents/Claude/Projects/universal-Integration-framework
git add stacks/apicurio/ui/src/features/schemas/mapping-body.ts stacks/apicurio/ui/src/features/schemas/mapping-body.test.ts
git commit -F - <<'MSG'
Flatten mapping columns into the workbook's three sheets

One flattener per column kind, producing exactly the rows the
*-gold-attribute-mapping-template.xlsx sheets show: one row per scalar column,
one per conditional branch, one per nested field. The description lands on the
first row of a group only, which is what the workbook does — repeating it
would read as though the branches had different descriptions.

Two things the shapes do not share and the rows must reconcile. A nested
column's `filter` is declared once for all its fields while the workbook has a
Source Filter cell per field row, so a field-level filter wins and the shared
one is the fallback. And a branch row's React key cannot be the gold column
name, because every branch of one column carries the same name and duplicate
keys silently drop rows.

An empty branches or fields array yields no rows rather than one row of
undefineds — a row that asserts a branch exists when none does.

UIFPE-598 #comment Added flattenScalar/flattenConditional/flattenNested plus the row types, producing the workbook's three sheets from a registered mapping: one row per column, per branch, per field; description on the first row of a group; field-level filter over a nested column's shared one; distinct keys per branch. 16 tests.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
MSG
```

---

## Task 6: Resolve joins, scopes, transforms, Silver names and picks

**Files:**
- Modify: `ui/src/features/schemas/mapping-body.ts`
- Test: `ui/src/features/schemas/mapping-body.test.ts`

**Interfaces:**
- Consumes: everything from Tasks 4 and 5.
- Produces, consumed by Tasks 8, 9, 10:

```ts
export interface ResolvedJoin {
  /** "driving" when the source IS the driving table — there is no join. */
  kind: 'driving' | 'join' | 'none';
  type: 'left' | 'inner';
  table: string | null;
  on: { left: string; right: string }[];
  filter: string | null;
}
export interface ResolvedScope {
  kind: 'group' | 'inline' | 'else' | 'all' | 'unresolved';
  label: string;
  predicate: string | null;
}
export interface TransformStep { op: string; detail: string }
export interface TransformCase { when: string; then: string }
export interface ResolvedTransform {
  kind: 'none' | 'rule' | 'inline' | 'sql' | 'unresolved';
  /** The rule name for `rule`/`unresolved`; '' otherwise. */
  name: string;
  description: string | null;
  steps: TransformStep[];
  cases: TransformCase[];
  fallthrough: string | null;
  sqlExpr: string | null;
  /** True for a sqlExpr — CI flags every use for engineering review. */
  reviewFlagged: boolean;
}
export interface ResolvedPick {
  partitionBy: string[];
  orderBy: { expr: string; direction: string }[];
}
export interface RowGroupEntry {
  name: string; predicate: string;
  description: string | null; complementOf: string | null;
}
export interface MappingReference {
  rowGroups: RowGroupEntry[];
  transforms: { name: string; resolved: ResolvedTransform; usedBy: number }[];
  silverTables: { name: string; qualified: string; driving: boolean }[];
  entityKey: EntityKeyPair[];
}
export interface ParsedMapping {
  isMapping: boolean;
  facts: MappingFacts;
  scalar: ScalarRow[];
  conditional: ConditionalRow[];
  nested: NestedRow[];
  reference: MappingReference;
  value: unknown;
}
export function resolveJoin(source: RawSource, drivingTable: string | null): ResolvedJoin
export function resolveScope(scope: unknown, body: Record<string, unknown>): ResolvedScope
export function resolveTransform(transform: unknown, body: Record<string, unknown>): ResolvedTransform
export function resolveSilverTable(table: string | null, body: Record<string, unknown>): string | null
export function resolvePick(pick: unknown): ResolvedPick | null
export function parseMappingBody(content: string): ParsedMapping
export function rowMatchesQuery(row: MappingRow, query: string): boolean
```

- [ ] **Step 1: Write the failing tests**

Append to `ui/src/features/schemas/mapping-body.test.ts`:

```ts
import {
  resolveJoin,
  resolveScope,
  resolveTransform,
  resolveSilverTable,
  resolvePick,
  parseMappingBody,
  rowMatchesQuery,
} from './mapping-body';

const WITH_DEFS = {
  ...MAPPING_OBJ,
  drivingSilverTable: 'uif_echo_{env}_silver_tep.mtl_system_items_b',
  silverTables: {
    QP_LIST_HEADERS_B: 'uif_echo_{env}_silver_tep.qp_list_headers_b',
  },
  rowGroups: {
    ukOrgs: {
      table: 'MTL_PARAMETERS',
      column: 'ORGANIZATION_CODE',
      op: 'startsWith',
      values: ['01'],
      description: 'UK orgs (GBPE).',
    },
    nonUkOrgs: { complementOf: 'ukOrgs', description: 'Every org NOT in ukOrgs.' },
  },
  transforms: {
    roundTo2dp: { steps: [{ op: 'round', precision: 2 }] },
    pearsonImageUrl: {
      description: 'Normalize Pearson cover-image URLs.',
      cases: [
        { when: { op: 'startsWith', values: ['/A'] },
          then: { op: 'prepend', text: 'https://images.pearson.com/i/79475d28' } },
      ],
      else: 'keep',
    },
    custom: { sqlExpr: "regexp_replace({x}, 'a', 'b')" },
  },
};

describe('resolveJoin', () => {
  it('says driving when the source IS the driving table', () => {
    const j = resolveJoin(
      { table: 'MTL_SYSTEM_ITEMS_B', column: 'C', filter: null, join: null },
      'MTL_SYSTEM_ITEMS_B',
    );
    expect(j.kind).toBe('driving');
  });

  it('renders a single ON pair', () => {
    const j = resolveJoin(
      {
        table: 'EGO_MTL_SY_ITEMS_EXT_B',
        column: 'C_EXT_ATTR8',
        filter: 'ATTR_GROUP_ID = 741',
        join: {
          type: 'left',
          on: [{
            leftTable: 'MTL_SYSTEM_ITEMS_B',
            leftColumn: 'INVENTORY_ITEM_ID',
            rightColumn: 'INVENTORY_ITEM_ID',
          }],
        },
      },
      'MTL_SYSTEM_ITEMS_B',
    );
    expect(j.kind).toBe('join');
    expect(j.type).toBe('left');
    expect(j.table).toBe('EGO_MTL_SY_ITEMS_EXT_B');
    expect(j.on).toEqual([{
      left: 'MTL_SYSTEM_ITEMS_B.INVENTORY_ITEM_ID',
      right: 'EGO_MTL_SY_ITEMS_EXT_B.INVENTORY_ITEM_ID',
    }]);
    expect(j.filter).toBe('ATTR_GROUP_ID = 741');
  });

  it('renders multiple ON pairs in declared order', () => {
    const j = resolveJoin(
      {
        table: 'T', column: 'C', filter: null,
        join: { on: [
          { leftTable: 'D', leftColumn: 'A', rightColumn: 'A2' },
          { leftTable: 'D', leftColumn: 'B', rightColumn: 'B2' },
        ] },
      },
      'D',
    );
    expect(j.on).toEqual([
      { left: 'D.A', right: 'T.A2' },
      { left: 'D.B', right: 'T.B2' },
    ]);
  });

  it('defaults the join type to left, and honours inner', () => {
    const on = [{ leftTable: 'D', leftColumn: 'A', rightColumn: 'A' }];
    expect(resolveJoin({ table: 'T', column: 'C', filter: null, join: { on } }, 'D').type)
      .toBe('left');
    expect(resolveJoin({ table: 'T', column: 'C', filter: null, join: { type: 'inner', on } }, 'D').type)
      .toBe('inner');
  });

  it('keeps a chained leftTable that is another source, not the driving table', () => {
    // Chains compose: A -> B -> C. The ON clause must name the table the
    // contract names, not be rewritten to the driving table.
    const j = resolveJoin(
      { table: 'C_TBL', column: 'X', filter: null,
        join: { on: [{ leftTable: 'B_TBL', leftColumn: 'K', rightColumn: 'K' }] } },
      'A_TBL',
    );
    expect(j.on[0].left).toBe('B_TBL.K');
  });

  it('says none when there is no source table at all', () => {
    expect(resolveJoin({ table: null, column: null, filter: null, join: null }, 'D').kind)
      .toBe('none');
  });

  it('says join with no ON pairs when a non-driving source declares none', () => {
    // The contract REQUIRES a join for every non-driving source. A file
    // missing one is a CI finding; the view shows the gap rather than
    // inventing a key.
    const j = resolveJoin({ table: 'T', column: 'C', filter: null, join: null }, 'D');
    expect(j.kind).toBe('join');
    expect(j.on).toEqual([]);
  });
});

describe('resolveScope', () => {
  it('resolves a named row group to its predicate', () => {
    const s = resolveScope({ group: 'ukOrgs' }, WITH_DEFS);
    expect(s.kind).toBe('group');
    expect(s.label).toBe('ukOrgs');
    expect(s.predicate).toBe("MTL_PARAMETERS.ORGANIZATION_CODE startsWith '01'");
  });

  it('resolves a complement group through its base', () => {
    const s = resolveScope({ group: 'nonUkOrgs' }, WITH_DEFS);
    expect(s.kind).toBe('group');
    expect(s.predicate).toContain('NOT');
    expect(s.predicate).toContain('ukOrgs');
  });

  it('marks a group reference that does not resolve', () => {
    // The view reports what the artifact declares. Rejecting this is CI's job.
    const s = resolveScope({ group: 'ghost' }, WITH_DEFS);
    expect(s.kind).toBe('unresolved');
    expect(s.label).toBe('ghost');
  });

  it('renders an inline predicate', () => {
    const s = resolveScope(
      { table: 'MTL_PARAMETERS', column: 'ORGANIZATION_CODE', op: 'in', values: ['01B', '01C'], label: 'two orgs' },
      WITH_DEFS,
    );
    expect(s.kind).toBe('inline');
    expect(s.label).toBe('two orgs');
    expect(s.predicate).toBe("MTL_PARAMETERS.ORGANIZATION_CODE in '01B', '01C'");
  });

  it('renders op else as the complement of its siblings', () => {
    const s = resolveScope({ op: 'else', column: 'x' }, WITH_DEFS);
    expect(s.kind).toBe('else');
    expect(s.label).toContain('no sibling');
  });

  it('says every row when no scope is declared', () => {
    const s = resolveScope(null, WITH_DEFS);
    expect(s.kind).toBe('all');
    expect(s.label).toContain('every row');
  });
});

describe('resolveTransform', () => {
  it('says none for a straight copy', () => {
    expect(resolveTransform(null, WITH_DEFS).kind).toBe('none');
  });

  it('resolves a rule reference to its steps', () => {
    const t = resolveTransform({ rule: 'roundTo2dp' }, WITH_DEFS);
    expect(t.kind).toBe('rule');
    expect(t.name).toBe('roundTo2dp');
    expect(t.steps).toEqual([{ op: 'round', detail: 'precision 2' }]);
  });

  it('resolves a rule reference to its cases and fallthrough', () => {
    const t = resolveTransform({ rule: 'pearsonImageUrl' }, WITH_DEFS);
    expect(t.description).toBe('Normalize Pearson cover-image URLs.');
    expect(t.cases).toEqual([{
      when: "startsWith '/A'",
      then: "prepend 'https://images.pearson.com/i/79475d28'",
    }]);
    expect(t.fallthrough).toBe('keep');
  });

  it('defaults the cases fallthrough to keep when unstated', () => {
    const t = resolveTransform(
      { cases: [{ when: { op: 'isNull' }, then: { op: 'literal', text: 'x' } }] },
      WITH_DEFS,
    );
    expect(t.fallthrough).toBe('keep');
  });

  it('marks a rule reference that does not resolve', () => {
    const t = resolveTransform({ rule: 'ghost' }, WITH_DEFS);
    expect(t.kind).toBe('unresolved');
    expect(t.name).toBe('ghost');
  });

  it('resolves an inline transform definition', () => {
    const t = resolveTransform({ steps: [{ op: 'cast', to: 'DECIMAL(38,2)' }] }, WITH_DEFS);
    expect(t.kind).toBe('inline');
    expect(t.steps).toEqual([{ op: 'cast', detail: 'DECIMAL(38,2)' }]);
  });

  it('flags a sqlExpr for review', () => {
    // CI flags every use for engineering review, so the row must show it.
    const t = resolveTransform({ rule: 'custom' }, WITH_DEFS);
    expect(t.sqlExpr).toBe("regexp_replace({x}, 'a', 'b')");
    expect(t.reviewFlagged).toBe(true);
  });

  it('treats a prose string as an unresolved rule name', () => {
    // Prose transforms are DEPRECATED (UIFPE-105) but still accepted while
    // old files migrate. Showing the text is better than showing nothing.
    const t = resolveTransform('round to 2dp', WITH_DEFS);
    expect(t.kind).toBe('unresolved');
    expect(t.name).toBe('round to 2dp');
  });
});

describe('resolveSilverTable', () => {
  it('resolves the driving table to drivingSilverTable', () => {
    expect(resolveSilverTable('MTL_SYSTEM_ITEMS_B', WITH_DEFS))
      .toBe('uif_echo_{env}_silver_tep.mtl_system_items_b');
  });

  it('resolves a lookup table from silverTables', () => {
    expect(resolveSilverTable('QP_LIST_HEADERS_B', WITH_DEFS))
      .toBe('uif_echo_{env}_silver_tep.qp_list_headers_b');
  });

  it('returns null for a table with no declared Silver name', () => {
    // Product's mapping declares neither, so this is the common case.
    expect(resolveSilverTable('EGO_MTL_SY_ITEMS_EXT_B', WITH_DEFS)).toBeNull();
    expect(resolveSilverTable('ANY', MAPPING_OBJ)).toBeNull();
    expect(resolveSilverTable(null, WITH_DEFS)).toBeNull();
  });
});

describe('resolvePick', () => {
  it('reads partitionBy and the ordered orderBy expressions', () => {
    const p = resolvePick({
      partitionBy: ['listHeaderId'],
      orderBy: [
        { expr: 'CASE WHEN END_DATE IS NULL THEN 0 ELSE 1 END' },
        { expr: 'START_DATE', direction: 'desc' },
      ],
    });
    expect(p?.partitionBy).toEqual(['listHeaderId']);
    expect(p?.orderBy).toEqual([
      { expr: 'CASE WHEN END_DATE IS NULL THEN 0 ELSE 1 END', direction: 'asc' },
      { expr: 'START_DATE', direction: 'desc' },
    ]);
  });

  it('returns null when no pick is declared', () => {
    expect(resolvePick(null)).toBeNull();
    expect(resolvePick(undefined)).toBeNull();
  });
});

describe('parseMappingBody', () => {
  it('says it is not a mapping for a schema body', () => {
    const parsed = parseMappingBody(JSON.stringify({ type: 'record', name: 'X', fields: [] }));
    expect(parsed.isMapping).toBe(false);
    expect(parsed.scalar).toEqual([]);
  });

  it('says it is not a mapping for unparseable text', () => {
    expect(parseMappingBody('syntax = "proto3";').isMapping).toBe(false);
    expect(parseMappingBody('').isMapping).toBe(false);
  });

  it('parses a mapping into facts, sheets and reference', () => {
    const parsed = parseMappingBody(JSON.stringify(WITH_DEFS));
    expect(parsed.isMapping).toBe(true);
    expect(parsed.facts.table).toBe('gold_product.product');
    expect(parsed.scalar).toHaveLength(1);
    expect(parsed.reference.rowGroups.map((g) => g.name)).toEqual(['ukOrgs', 'nonUkOrgs']);
    expect(parsed.reference.transforms.map((t) => t.name))
      .toEqual(['roundTo2dp', 'pearsonImageUrl', 'custom']);
    expect(parsed.reference.entityKey).toHaveLength(2);
  });

  it('lists the driving Silver table alongside the lookups, marked', () => {
    const parsed = parseMappingBody(JSON.stringify(WITH_DEFS));
    const driving = parsed.reference.silverTables.find((t) => t.driving);
    expect(driving?.name).toBe('MTL_SYSTEM_ITEMS_B');
    expect(parsed.reference.silverTables).toHaveLength(2);
  });

  it('leaves every reference collection empty for a mapping declaring none', () => {
    // Product's real mapping: no silverTables, no drivingSilverTable. Each
    // section hides when empty rather than rendering an empty accordion.
    const parsed = parseMappingBody(JSON.stringify(MAPPING_OBJ));
    expect(parsed.reference.silverTables).toEqual([]);
    expect(parsed.reference.rowGroups).toEqual([]);
    expect(parsed.reference.transforms).toEqual([]);
  });

  it('counts how many columns reference each named transform', () => {
    const body = {
      ...WITH_DEFS,
      columns: [
        { ...SCALAR_COL, goldColumnName: 'a', transform: { rule: 'roundTo2dp' } },
        { ...SCALAR_COL, goldColumnName: 'b', transform: { rule: 'roundTo2dp' } },
        { ...SCALAR_COL, goldColumnName: 'c', transform: null },
      ],
    };
    const parsed = parseMappingBody(JSON.stringify(body));
    const entry = parsed.reference.transforms.find((t) => t.name === 'roundTo2dp');
    expect(entry?.usedBy).toBe(2);
  });

  it('flattens a wide body across all three kinds', () => {
    // The case a hand-built two-column body cannot catch.
    const body = {
      ...WITH_DEFS,
      columns: [
        ...Array.from({ length: 31 }, (_, i) => ({ ...SCALAR_COL, goldColumnName: `s${i}` })),
        ...Array.from({ length: 33 }, (_, i) => ({ ...CONDITIONAL_COL, goldColumnName: `c${i}` })),
        ...Array.from({ length: 10 }, (_, i) => ({ ...NESTED_COL, goldColumnName: `n${i}` })),
      ],
    };
    const parsed = parseMappingBody(JSON.stringify(body));
    expect(parsed.scalar).toHaveLength(31);
    expect(parsed.conditional).toHaveLength(66); // 33 columns x 2 branches
    expect(parsed.nested).toHaveLength(20); // 10 columns x 2 fields
  });
});

describe('rowMatchesQuery', () => {
  const row = flattenScalar([SCALAR_COL])[0];

  it('matches the gold column, description, source table and column', () => {
    expect(rowMatchesQuery(row, 'itemStatus')).toBe(true);
    expect(rowMatchesQuery(row, 'lifecycle')).toBe(true); // description
    expect(rowMatchesQuery(row, 'MTL_SYSTEM')).toBe(true);
    expect(rowMatchesQuery(row, 'STATUS_CODE')).toBe(true);
  });

  it('is case-insensitive, like the rest of the page search', () => {
    expect(rowMatchesQuery(row, 'ITEMSTATUS')).toBe(true);
  });

  it('matches the notes', () => {
    expect(rowMatchesQuery(row, 'A note')).toBe(true);
  });

  it('does not match an unrelated query', () => {
    expect(rowMatchesQuery(row, 'zzzz')).toBe(false);
  });

  it('matches everything on an empty query', () => {
    expect(rowMatchesQuery(row, '')).toBe(true);
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd stacks/apicurio && npm --prefix ui run test -- --run mapping-body
```

Expected: FAIL — `resolveJoin` is not exported.

- [ ] **Step 3: Add the resolvers**

Append to `ui/src/features/schemas/mapping-body.ts`:

```ts
// ── Resolvers ────────────────────────────────────────────────────────────────
//
// Five things the registered JSON carries that the workbook has no column for
// (design spec §2.2). They are resolved here and rendered in the row detail,
// so the table keeps the workbook's shape and nothing is lost.

export interface ResolvedJoin {
  kind: 'driving' | 'join' | 'none';
  type: 'left' | 'inner';
  table: string | null;
  on: { left: string; right: string }[];
  filter: string | null;
}

/**
 * How a source attaches to the entity (UIFPE-104).
 *
 * `leftTable` is taken as declared and never rewritten to the driving table:
 * chains compose (A -> B -> C), so the ON clause must name the table the
 * contract names.
 */
export function resolveJoin(source: RawSource, drivingTable: string | null): ResolvedJoin {
  if (!source.table) {
    return { kind: 'none', type: 'left', table: null, on: [], filter: source.filter };
  }
  if (drivingTable && source.table === drivingTable) {
    return { kind: 'driving', type: 'left', table: source.table, on: [], filter: source.filter };
  }
  const join = asRecord(source.join);
  const pairs = Array.isArray(join?.on) ? join.on : [];
  return {
    kind: 'join',
    type: join?.type === 'inner' ? 'inner' : 'left',
    table: source.table,
    on: pairs
      .map(asRecord)
      .filter((p): p is Record<string, unknown> => p !== null)
      .map((p) => ({
        left: `${str(p.leftTable) ?? '?'}.${str(p.leftColumn) ?? '?'}`,
        right: `${source.table}.${str(p.rightColumn) ?? '?'}`,
      })),
    filter: source.filter,
  };
}

export interface ResolvedScope {
  kind: 'group' | 'inline' | 'else' | 'all' | 'unresolved';
  label: string;
  predicate: string | null;
}

function renderPredicate(spec: Record<string, unknown>): string {
  const table = str(spec.table);
  const column = str(spec.column) ?? '?';
  const qualified = table ? `${table}.${column}` : column;
  const op = str(spec.op) ?? '?';
  const values = Array.isArray(spec.values) ? spec.values : [];
  if (values.length === 0) return `${qualified} ${op}`;
  return `${qualified} ${op} ${values.map((v) => `'${String(v)}'`).join(', ')}`;
}

/**
 * WHICH entity rows a conditional branch's value reaches.
 *
 * Distinct from the branch's `condition`, which is the discriminator value —
 * the contract is explicit that row-group qualifiers do not belong there.
 */
export function resolveScope(scope: unknown, body: Record<string, unknown>): ResolvedScope {
  const spec = asRecord(scope);
  if (!spec) {
    return { kind: 'all', label: 'every row — this branch declares no scope', predicate: null };
  }
  if (spec.op === 'else') {
    return {
      kind: 'else',
      label: "every row no sibling branch's scope claimed",
      predicate: null,
    };
  }
  const groupName = str(spec.group);
  if (groupName) {
    const groups = asRecord(body.rowGroups) ?? {};
    const group = asRecord(groups[groupName]);
    if (!group) {
      // The view reports what the artifact declares; rejecting a dangling
      // reference is CI's job (test_intake_hygiene.py).
      return { kind: 'unresolved', label: groupName, predicate: null };
    }
    const complementOf = str(group.complementOf);
    if (complementOf) {
      const base = asRecord(groups[complementOf]);
      const basePredicate = base ? renderPredicate(base) : complementOf;
      return {
        kind: 'group',
        label: groupName,
        predicate: `NOT (${basePredicate}) — the complement of ${complementOf}`,
      };
    }
    return { kind: 'group', label: groupName, predicate: renderPredicate(group) };
  }
  return {
    kind: 'inline',
    label: str(spec.label) ?? 'inline row group',
    predicate: renderPredicate(spec),
  };
}

export interface TransformStep { op: string; detail: string }
export interface TransformCase { when: string; then: string }

export interface ResolvedTransform {
  kind: 'none' | 'rule' | 'inline' | 'sql' | 'unresolved';
  name: string;
  description: string | null;
  steps: TransformStep[];
  cases: TransformCase[];
  fallthrough: string | null;
  sqlExpr: string | null;
  reviewFlagged: boolean;
}

const NO_TRANSFORM: ResolvedTransform = {
  kind: 'none', name: '', description: null,
  steps: [], cases: [], fallthrough: null, sqlExpr: null, reviewFlagged: false,
};

function renderStep(step: Record<string, unknown>): TransformStep {
  const op = str(step.op) ?? '?';
  const detail =
    step.precision !== undefined ? `precision ${String(step.precision)}`
    : str(step.to) ?? str(step.value) ?? '';
  return { op, detail };
}

function renderWhenThen(spec: Record<string, unknown>): string {
  const op = str(spec.op) ?? '?';
  const values = Array.isArray(spec.values) ? spec.values : [];
  const text = str(spec.text) ?? str(spec.find);
  const replaceWith = str(spec.replaceWith);
  if (values.length > 0) return `${op} ${values.map((v) => `'${String(v)}'`).join(', ')}`;
  if (text && replaceWith) return `${op} '${text}' -> '${replaceWith}'`;
  if (text) return `${op} '${text}'`;
  return op;
}

function renderDefinition(
  def: Record<string, unknown>,
  kind: ResolvedTransform['kind'],
  name: string,
): ResolvedTransform {
  const sqlExpr = str(def.sqlExpr);
  const steps = (Array.isArray(def.steps) ? def.steps : [])
    .map(asRecord)
    .filter((s): s is Record<string, unknown> => s !== null)
    .map(renderStep);
  const cases = (Array.isArray(def.cases) ? def.cases : [])
    .map(asRecord)
    .filter((c): c is Record<string, unknown> => c !== null)
    .map((c) => ({
      when: renderWhenThen(asRecord(c.when) ?? {}),
      then: renderWhenThen(asRecord(c.then) ?? {}),
    }));
  return {
    kind,
    name,
    description: str(def.description),
    steps,
    cases,
    // Default `keep` per the contract, stated rather than left blank so a
    // reader is not left guessing what an unmatched row gets.
    fallthrough: cases.length > 0 ? str(def.else) ?? 'keep' : null,
    sqlExpr,
    // CI flags every sqlExpr for engineering review, so the row shows it.
    reviewFlagged: sqlExpr !== null,
  };
}

export function resolveTransform(
  transform: unknown,
  body: Record<string, unknown>,
): ResolvedTransform {
  if (transform === null || transform === undefined) return NO_TRANSFORM;

  // A prose string. DEPRECATED (UIFPE-105) but still accepted while old files
  // migrate — showing the text beats showing nothing.
  if (typeof transform === 'string') {
    return { ...NO_TRANSFORM, kind: 'unresolved', name: transform };
  }

  const spec = asRecord(transform);
  if (!spec) return NO_TRANSFORM;

  const ruleName = str(spec.rule);
  if (ruleName) {
    const named = asRecord(asRecord(body.transforms)?.[ruleName]);
    if (!named) return { ...NO_TRANSFORM, kind: 'unresolved', name: ruleName };
    return renderDefinition(named, 'rule', ruleName);
  }
  return renderDefinition(spec, 'inline', '');
}

/** The env-templated fully qualified Silver name for a bare source table. */
export function resolveSilverTable(
  table: string | null,
  body: Record<string, unknown>,
): string | null {
  if (!table) return null;
  if (table === str(body.drivingTable)) return str(body.drivingSilverTable);
  return str(asRecord(body.silverTables)?.[table]);
}

export interface ResolvedPick {
  partitionBy: string[];
  orderBy: { expr: string; direction: string }[];
}

export function resolvePick(pick: unknown): ResolvedPick | null {
  const spec = asRecord(pick);
  if (!spec) return null;
  return {
    partitionBy: (Array.isArray(spec.partitionBy) ? spec.partitionBy : []).map(String),
    orderBy: (Array.isArray(spec.orderBy) ? spec.orderBy : [])
      .map(asRecord)
      .filter((o): o is Record<string, unknown> => o !== null)
      .map((o) => ({ expr: str(o.expr) ?? '', direction: str(o.direction) ?? 'asc' })),
  };
}
```

- [ ] **Step 4: Add the reference catalogs, the top-level entry and the search predicate**

Append to `ui/src/features/schemas/mapping-body.ts`:

```ts
// ── Reference catalogs and the entry point ──────────────────────────────────

export interface RowGroupEntry {
  name: string;
  predicate: string;
  description: string | null;
  complementOf: string | null;
}

export interface MappingReference {
  rowGroups: RowGroupEntry[];
  transforms: { name: string; resolved: ResolvedTransform; usedBy: number }[];
  silverTables: { name: string; qualified: string; driving: boolean }[];
  entityKey: EntityKeyPair[];
}

export interface ParsedMapping {
  isMapping: boolean;
  facts: MappingFacts;
  scalar: ScalarRow[];
  conditional: ConditionalRow[];
  nested: NestedRow[];
  reference: MappingReference;
  /** The parsed body, for the code view and for the resolvers. */
  value: unknown;
}

const EMPTY_REFERENCE: MappingReference = {
  rowGroups: [], transforms: [], silverTables: [], entityKey: [],
};

const NOT_A_MAPPING: ParsedMapping = {
  isMapping: false,
  facts: {
    domain: null, table: null, drivingTable: null, drivingSilverTable: null,
    entityKey: [], registry: null, batch: null,
  },
  scalar: [], conditional: [], nested: [],
  reference: EMPTY_REFERENCE,
  value: null,
};

/**
 * Counts how many columns reference each named transform.
 *
 * This is the number that earns the transforms catalog its place: a rule used
 * by twelve columns is resolved inline twelve times and would otherwise never
 * be readable as one definition.
 */
function countTransformUses(columns: unknown[], name: string): number {
  let n = 0;
  for (const raw of columns) {
    const col = asRecord(raw);
    if (!col) continue;
    const refs: unknown[] = [col.transform];
    if (Array.isArray(col.branches)) {
      for (const b of col.branches) refs.push(asRecord(b)?.transform);
    }
    if (Array.isArray(col.fields)) {
      for (const f of col.fields) refs.push(asRecord(f)?.transform);
    }
    if (refs.some((r) => str(asRecord(r)?.rule) === name)) n += 1;
  }
  return n;
}

function buildReference(body: Record<string, unknown>, facts: MappingFacts): MappingReference {
  const columns = Array.isArray(body.columns) ? body.columns : [];

  const groupsRaw = asRecord(body.rowGroups) ?? {};
  const rowGroups: RowGroupEntry[] = Object.entries(groupsRaw).map(([name, raw]) => {
    const group = asRecord(raw) ?? {};
    const complementOf = str(group.complementOf);
    return {
      name,
      predicate: complementOf
        ? resolveScope({ group: name }, body).predicate ?? ''
        : renderPredicate(group),
      description: str(group.description),
      complementOf,
    };
  });

  const transformsRaw = asRecord(body.transforms) ?? {};
  const transforms = Object.keys(transformsRaw).map((name) => ({
    name,
    resolved: resolveTransform({ rule: name }, body),
    usedBy: countTransformUses(columns, name),
  }));

  const silverRaw = asRecord(body.silverTables) ?? {};
  const silverTables: { name: string; qualified: string; driving: boolean }[] = [];
  if (facts.drivingTable && facts.drivingSilverTable) {
    silverTables.push({
      name: facts.drivingTable,
      qualified: facts.drivingSilverTable,
      driving: true,
    });
  }
  for (const [name, qualified] of Object.entries(silverRaw)) {
    const value = str(qualified);
    if (value) silverTables.push({ name, qualified: value, driving: false });
  }

  return { rowGroups, transforms, silverTables, entityKey: facts.entityKey };
}

/**
 * The entry point. One parse, three sheets and a reference section — so a
 * source table named in a row is the same one the Silver catalog resolves.
 */
export function parseMappingBody(content: string): ParsedMapping {
  let parsed: unknown;
  try {
    parsed = JSON.parse(content);
  } catch {
    return NOT_A_MAPPING;
  }
  if (!isMappingBody(parsed)) return { ...NOT_A_MAPPING, value: parsed };

  const body = parsed as Record<string, unknown>;
  const columns = Array.isArray(body.columns) ? body.columns : [];
  const facts = parseMappingFacts(body);

  return {
    isMapping: true,
    facts,
    scalar: flattenScalar(columns),
    conditional: flattenConditional(columns),
    nested: flattenNested(columns),
    reference: buildReference(body, facts),
    value: parsed,
  };
}

/**
 * The search predicate. FILTERS rows rather than only tinting them, following
 * the precedent SchemaFieldsView documents: highlighting alone "was enough
 * for a five-field Avro record and useless on product.gold-schema, where a
 * match can be the fortieth of 63 rows and off-screen". Product's mapping is
 * 74 columns and flattens to more rows than that.
 */
export function rowMatchesQuery(row: MappingRow, query: string): boolean {
  if (!query) return true;
  const needle = query.toLowerCase();
  const haystack = [
    row.goldColumnName,
    row.businessDescription,
    row.source.table ?? '',
    row.source.column ?? '',
    row.notes,
    typeof row.transform === 'object' && row.transform !== null
      ? str((row.transform as Record<string, unknown>).rule) ?? ''
      : typeof row.transform === 'string'
        ? row.transform
        : '',
    row.kind === 'nested' ? row.fieldName : '',
    row.kind === 'conditional' ? row.condition : '',
  ];
  return haystack.some((h) => h.toLowerCase().includes(needle));
}
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
cd stacks/apicurio && npm --prefix ui run test -- --run mapping-body
```

Expected: PASS — 13 + 16 + 40 new.

- [ ] **Step 6: Lint and typecheck**

```bash
cd stacks/apicurio && npm --prefix ui run lint
cd stacks/apicurio/ui && npx tsc -b
```

Expected: both clean.

- [ ] **Step 7: Commit**

```bash
cd /Users/raj9karu/Documents/Claude/Projects/universal-Integration-framework
git add stacks/apicurio/ui/src/features/schemas/mapping-body.ts stacks/apicurio/ui/src/features/schemas/mapping-body.test.ts
git commit -F - <<'MSG'
Resolve the five things the workbook has no column for

The registered mapping is richer than the xlsx it replaces: structured joins
with explicit ON pairs, scopes referencing named row groups, structured
transforms, derivation SQL and ranked picks all postdate the workbook. Each
now resolves to something renderable, so the table can keep the workbook's
shape without losing them.

Three decisions worth stating. A join's leftTable is taken as declared and
never rewritten to the driving table, because chains compose and the ON clause
must name the table the contract names. A dangling scope group or transform
rule resolves to an unresolved marker rather than an error — the view reports
what the artifact declares, and rejecting it is CI's job. And a sqlExpr is
flagged for review on the row, because CI flags every use of one and a reader
should see that without expanding anything.

The transforms catalog counts how many columns reference each rule, which is
what earns it its place: a rule used by twelve columns is resolved inline
twelve times and is otherwise never readable as one definition.

UIFPE-598 #comment Added resolveJoin/resolveScope/resolveTransform/resolveSilverTable/resolvePick, the reference catalogs with per-transform usage counts, parseMappingBody as the entry point, and rowMatchesQuery for the filtering search. 40 tests, including a 74-column body across all three kinds.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
MSG
```

---

## Task 7: Add the Mapping tab and the sheet param to the URL vocabulary

**Files:**
- Modify: `ui/src/features/schemas/hooks/use-schema-url-state.ts`
- Test: `ui/src/features/schemas/hooks/use-schema-url-state.test.ts`

**Interfaces:**
- Consumes: nothing from earlier tasks. Independent of Tasks 1–6 and safe to run alongside them.
- Produces, consumed by Tasks 8 and 10:

```ts
export type MappingKindView = 'scalar' | 'conditional' | 'nested';
export const MAPPING_KIND_LABELS: Record<MappingKindView, string>
export const MAPPING_KIND_ORDER: readonly MappingKindView[]
// DetailTab gains 'mapping'; SchemaUrlState gains `mappingKind: MappingKindView`
// DETAIL_TAB_LABELS gains mapping: 'Mapping'
// The URL params are ?dtab=mapping and ?mkind=<kind>
```

- [ ] **Step 1: Write the failing tests**

Append to `ui/src/features/schemas/hooks/use-schema-url-state.test.ts`:

```ts
describe('useSchemaUrlState — the mapping tab and sheet (UIFPE-598)', () => {
  it('defaults mappingKind to scalar', () => {
    // Scalar is the workbook's first sheet, so the view opens where the
    // reader's own document opens.
    const { result } = renderHook(() => useSchemaUrlState(), { wrapper });
    expect(result.current.state.mappingKind).toBe('scalar');
  });

  it('reads ?dtab=mapping', () => {
    const { result } = renderHook(() => useSchemaUrlState(), {
      wrapper: wrapperWithSearch('?dtab=mapping'),
    });
    expect(result.current.state.detailTab).toBe('mapping');
  });

  it('reads ?mkind for each sheet', () => {
    for (const kind of ['scalar', 'conditional', 'nested'] as const) {
      const { result } = renderHook(() => useSchemaUrlState(), {
        wrapper: wrapperWithSearch(`?mkind=${kind}`),
      });
      expect(result.current.state.mappingKind).toBe(kind);
    }
  });

  it('falls back to scalar for an unknown ?mkind', () => {
    // Same treatment every other enum param gets: a value that is not in the
    // union never reaches the component. A stale deep link that rendered no
    // sheet at all would assert the mapping has no columns.
    const { result } = renderHook(() => useSchemaUrlState(), {
      wrapper: wrapperWithSearch('?mkind=pivot'),
    });
    expect(result.current.state.mappingKind).toBe('scalar');
  });

  it('writes mkind to the URL and drops it when set back to the default', () => {
    const { result } = renderHook(() => useSchemaUrlState(), { wrapper });
    act(() => result.current.set({ mappingKind: 'nested' }));
    expect(result.current.state.mappingKind).toBe('nested');
    act(() => result.current.set({ mappingKind: 'scalar' }));
    expect(result.current.state.mappingKind).toBe('scalar');
  });
});
```

Also extend the existing `it('returns all defaults when no query params', ...)` with one line, so the defaults test covers the new key:

```ts
    expect(result.current.state.mappingKind).toBe('scalar');
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd stacks/apicurio && npm --prefix ui run test -- --run use-schema-url-state
```

Expected: FAIL — `mappingKind` is `undefined`, and `detailTab` resolves to `schema` rather than `mapping`.

- [ ] **Step 3: Add `mapping` to both tab lists and `mkind` to the vocabulary**

In `ui/src/features/schemas/hooks/use-schema-url-state.ts`:

Replace the two tab constants:

```ts
const URL_DETAIL_TABS = ['overview', 'schema', 'mapping', 'compliance', 'services', 'history'] as const;
const DETAIL_TABS = ['schema', 'mapping', 'compliance', 'services', 'history'] as const;
```

Add, immediately after `const SCHEMA_VIEWS = ['fields', 'code', 'graph'] as const;`:

```ts
/**
 * The mapping view's three sheets, in the order the
 * *-gold-attribute-mapping-template.xlsx workbook lists them. A mapping is a
 * different body from a schema, so these are not a fourth `SCHEMA_VIEWS`
 * entry — the two lists never apply to the same artifact.
 */
const MAPPING_KINDS = ['scalar', 'conditional', 'nested'] as const;
```

Add, after `export type SchemaViewMode = ...`:

```ts
export type MappingKindView = (typeof MAPPING_KINDS)[number];
```

Add to `DETAIL_TAB_LABELS`, between `schema` and `compliance`:

```ts
  mapping: 'Mapping',
```

Add, after `SCHEMA_VIEW_ORDER`:

```ts
export const MAPPING_KIND_LABELS: Record<MappingKindView, string> = {
  scalar: 'Scalar',
  conditional: 'Conditional',
  nested: 'Nested',
};

export const MAPPING_KIND_ORDER = MAPPING_KINDS;
```

Add to the `SchemaUrlState` interface, after `schemaView`:

```ts
  /** Which of the mapping's three sheets the Mapping tab is showing. */
  mappingKind: MappingKindView;
```

Add to `DEFAULTS`, after `schemaView: 'code',`:

```ts
  // Scalar is the workbook's first sheet, so the view opens where the
  // reader's own document opens.
  mappingKind: 'scalar',
```

Add to `KEY_MAP`, after `schemaView: 'schemaView',`:

```ts
  mappingKind: 'mkind',
```

Add to the `state` object in `useSchemaUrlState`, after the `schemaView` line:

```ts
    mappingKind: parseEnum(params.get('mkind'), MAPPING_KINDS, DEFAULTS.mappingKind),
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd stacks/apicurio && npm --prefix ui run test -- --run use-schema-url-state
```

Expected: PASS — the existing suite plus 5 new.

- [ ] **Step 5: Run the whole UI suite — the new tab must not break the existing tab row**

```bash
cd stacks/apicurio && npm --prefix ui run test -- --run && npm --prefix ui run lint
cd stacks/apicurio/ui && npx tsc -b
```

Expected: all PASS. `DETAIL_TAB_ORDER` now has five entries, so any test asserting an exact tab count will fail here — if one does, that is this task's job to update, and the count is 5.

- [ ] **Step 6: Commit**

```bash
cd /Users/raj9karu/Documents/Claude/Projects/universal-Integration-framework
git add stacks/apicurio/ui/src/features/schemas/hooks/use-schema-url-state.ts stacks/apicurio/ui/src/features/schemas/hooks/use-schema-url-state.test.ts
git commit -F - <<'MSG'
Put the mapping tab and its sheet in the URL

A link to a mapping should reproduce the sheet you were looking at, for the
same reason the schema view and the version are already URL state: otherwise
the link names an artifact while appearing to name a reading of it.

`mkind` gets the same treatment as every other enum param — a value outside
the union never reaches the component. A stale deep link that rendered no
sheet would assert the mapping has no columns, which is a different and false
statement.

The sheets are a separate list from SCHEMA_VIEWS rather than a fourth entry in
it: a mapping is a different body from a schema, and the two lists never apply
to the same artifact.

UIFPE-598 #comment Added the `mapping` detail tab to both URL tab lists and a `mkind` param (scalar/conditional/nested, defaulting to scalar, unknown values falling back) with labels and order constants. 5 tests.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
MSG
```

---

## Task 8: The Mapping tab — facts band, sheet toggle, search, tables, CSV

**Files:**
- Create: `ui/src/features/schemas/components/SchemaMappingView.tsx`
- Test: `ui/src/features/schemas/components/SchemaMappingView.test.tsx`

**Interfaces:**
- Consumes from Task 6: `ParsedMapping`, `MappingRow`, `ScalarRow`, `ConditionalRow`, `NestedRow`, `rowMatchesQuery`. From Task 7: `MappingKindView`, `MAPPING_KIND_LABELS`, `MAPPING_KIND_ORDER`. From the existing tree: `Highlight` (exported from `./SchemaFieldsView`), `Badge` (`@/components/ui/Badge`), `cn` (`@/lib/utils`), `downloadBlob` (`../utils`).
- Produces, consumed by Tasks 9 and 10:

```ts
export interface SchemaMappingViewProps {
  mapping: ParsedMapping;
  kind: MappingKindView;
  onKindChange: (kind: MappingKindView) => void;
  subject: string;
  version: string | null;
}
export function SchemaMappingView(props: SchemaMappingViewProps): JSX.Element
export const SHEET_HEADERS: Record<MappingKindView, string[]>
export function rowToCsvCells(row: MappingRow): string[]
export function toCsv(headers: string[], rows: MappingRow[]): string
```

**Two corrections already in the parser that this task must respect** (commit `c8ed98c6`):

- Use `filterRows(rows, query)`, never `rows.filter((r) => rowMatchesQuery(r, query))`. The description is on the first row of a group only, so filtering rows independently hides a conditional column's other branches.
- The amber `SQL` chip is driven off the transform's `sqlExpr`, **not** `kind === 'sql'`. `ResolvedTransform.kind` has no `'sql'` member — a `sqlExpr` transform is still a `rule` or an `inline` one, marked by `reviewFlagged`.

Task 9 supplies `MappingRowDetail` and `MappingReferenceSections`, which this component renders. **Build Task 8 against a placeholder for both** — a `null`-returning stub in this file is wrong, because Task 9 replaces it. Instead: Task 8 renders the expansion region as an empty `<div role="region">` with the row's key, and Task 9 fills it. Task 8's own tests assert the region appears and disappears; Task 9's assert its contents.

- [ ] **Step 1: Write the failing tests**

Create `ui/src/features/schemas/components/SchemaMappingView.test.tsx`:

```tsx
import { render, screen, fireEvent, within } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { SchemaMappingView, toCsv, SHEET_HEADERS } from './SchemaMappingView';
import { parseMappingBody } from '../mapping-body';

const BODY = JSON.stringify({
  $schema: '../../gold-attribute-mapping.schema.json',
  domain: 'product',
  table: 'gold_product.product',
  registry: { group: 'product', artifact: 'product-gold-attribute-mapping', compatibility: 'BACKWARD' },
  drivingTable: 'MTL_SYSTEM_ITEMS_B',
  entityKey: [
    { goldColumn: 'inventoryItemId', drivingColumn: 'INVENTORY_ITEM_ID' },
    { goldColumn: 'organizationId', drivingColumn: 'ORGANIZATION_ID' },
  ],
  batch: { requestedBy: 'Reyas Ahamed H', targetDate: '2026-07-22', backfillMode: 'silent', newChannel: null },
  transforms: { roundTo2dp: { steps: [{ op: 'round', precision: 2 }] } },
  columns: [
    {
      goldColumnName: 'itemStatus', businessDescription: 'Lifecycle status.', kind: 'scalar',
      dataType: 'string',
      source: { table: 'MTL_SYSTEM_ITEMS_B', column: 'INVENTORY_ITEM_STATUS_CODE', filter: null },
      transform: null, channels: { event: true, message: true, api: true },
      nullable: true, notes: '',
    },
    {
      goldColumnName: 'listPrice', businessDescription: 'List price.', kind: 'scalar',
      dataType: 'number',
      source: { table: 'QP_LIST_LINES', column: 'OPERAND', filter: null },
      transform: { rule: 'roundTo2dp' }, channels: { event: false, message: true, api: true },
      nullable: true, notes: '',
    },
    {
      goldColumnName: 'isbn13', businessDescription: 'ISBN-13.', kind: 'conditional',
      dataType: 'string',
      discriminator: { table: 'EGO_MTL_SY_ITEMS_EXT_B', column: 'ITEM_CATALOG_GROUP_ID' },
      channels: { event: true, message: true, api: true },
      branches: [
        { condition: 'ELSE (<> 10002)',
          source: { table: 'EGO_MTL_SY_ITEMS_EXT_B', column: 'C_EXT_ATTR8', filter: 'ATTR_GROUP_ID = 741' },
          fallback: 'returnNull', notes: null },
        { condition: '10002 (Digital Catalog)',
          source: { table: 'EGO_MTL_SY_ITEMS_EXT_B', column: 'C_EXT_ATTR4', filter: 'ATTR_GROUP_ID = 1401' },
          fallback: 'fallbackToElse', notes: null },
      ],
    },
    {
      goldColumnName: 'bomComponents', businessDescription: 'BOM lines.', kind: 'nested',
      joinKey: 'BILL_SEQUENCE_ID -> item', channels: { event: false, message: false, api: true },
      fields: [
        { name: 'componentItemId', type: 'integer', sortKey: true,
          source: { table: 'BOM_COMPONENTS_B', column: 'COMPONENT_ITEM_ID' }, transform: null },
      ],
    },
  ],
});

const MAPPING = parseMappingBody(BODY);

function renderView(kind: 'scalar' | 'conditional' | 'nested' = 'scalar', onKindChange = vi.fn()) {
  render(
    <SchemaMappingView
      mapping={MAPPING}
      kind={kind}
      onKindChange={onKindChange}
      subject="product.product.mapping"
      version="1"
    />,
  );
  return { onKindChange };
}

describe('facts band', () => {
  it('names the gold table, domain and driving table', () => {
    renderView();
    expect(screen.getByText('gold_product.product')).toBeInTheDocument();
    expect(screen.getByText('MTL_SYSTEM_ITEMS_B')).toBeInTheDocument();
  });

  it('shows the entity key as ordered goldColumn <- drivingColumn pairs', () => {
    renderView();
    // It is the gold table's ORDERED primary key, and the order is the grain.
    const key = screen.getByTestId('mapping-entity-key');
    expect(key).toHaveTextContent('inventoryItemId');
    expect(key).toHaveTextContent('INVENTORY_ITEM_ID');
    expect(key.textContent?.indexOf('inventoryItemId'))
      .toBeLessThan(key.textContent!.indexOf('organizationId'));
  });

  it('shows the registry coordinate and the batch', () => {
    renderView();
    expect(screen.getByText(/product-gold-attribute-mapping/)).toBeInTheDocument();
    expect(screen.getByText('BACKWARD')).toBeInTheDocument();
    expect(screen.getByText(/Reyas Ahamed H/)).toBeInTheDocument();
  });
});

describe('sheet toggle', () => {
  it('labels each sheet with its row count', () => {
    // The count is on the label so the size of a sheet is known before
    // opening it.
    renderView();
    expect(screen.getByRole('tab', { name: /Scalar/ })).toHaveTextContent('2');
    expect(screen.getByRole('tab', { name: /Conditional/ })).toHaveTextContent('2');
    expect(screen.getByRole('tab', { name: /Nested/ })).toHaveTextContent('1');
  });

  it('marks the active sheet and reports a change', () => {
    const { onKindChange } = renderView('scalar');
    expect(screen.getByRole('tab', { name: /Scalar/ })).toHaveAttribute('aria-selected', 'true');
    fireEvent.click(screen.getByRole('tab', { name: /Nested/ }));
    expect(onKindChange).toHaveBeenCalledWith('nested');
  });
});

describe('the tables', () => {
  it('renders the scalar sheet with the workbook headers in the workbook order', () => {
    renderView('scalar');
    const headers = screen.getAllByRole('columnheader').map((h) => h.textContent);
    expect(headers).toEqual(SHEET_HEADERS.scalar);
  });

  it('renders one row per scalar column', () => {
    renderView('scalar');
    expect(screen.getByText('itemStatus')).toBeInTheDocument();
    expect(screen.getByText('listPrice')).toBeInTheDocument();
    expect(screen.getByText('INVENTORY_ITEM_STATUS_CODE')).toBeInTheDocument();
  });

  it('renders channels as Y and a dash, not true and false', () => {
    renderView('scalar');
    const row = screen.getByTestId('mapping-row-itemStatus#0');
    expect(row).toHaveTextContent('Y');
    expect(row).not.toHaveTextContent('true');
  });

  it('shows a transform rule name as a chip', () => {
    renderView('scalar');
    expect(screen.getByText('roundTo2dp')).toBeInTheDocument();
  });

  it('renders one row per branch, repeating the gold column', () => {
    renderView('conditional');
    expect(screen.getAllByText('isbn13')).toHaveLength(2);
    expect(screen.getByText('ELSE (<> 10002)')).toBeInTheDocument();
    expect(screen.getByText('10002 (Digital Catalog)')).toBeInTheDocument();
  });

  it('shows the description on the first branch row only', () => {
    renderView('conditional');
    expect(screen.getAllByText('ISBN-13.')).toHaveLength(1);
  });

  it('renders one row per nested field with its sort key', () => {
    renderView('nested');
    expect(screen.getByText('componentItemId')).toBeInTheDocument();
    expect(screen.getByText('BILL_SEQUENCE_ID -> item')).toBeInTheDocument();
  });

  it('marks Join Grain as superseded rather than showing it as current', () => {
    // DEPRECATED per UIFPE-104. Rendering it as a live column would teach the
    // deprecated thing to everyone who reads the page.
    renderView('scalar');
    const header = screen.getByRole('columnheader', { name: /Join Grain/ });
    expect(header).toHaveAttribute('title', expect.stringContaining('superseded'));
  });
});

describe('search', () => {
  it('filters rows and reports a count', () => {
    renderView('scalar');
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'listPrice' } });
    expect(screen.getByText('listPrice')).toBeInTheDocument();
    expect(screen.queryByText('itemStatus')).not.toBeInTheDocument();
    expect(screen.getByTestId('mapping-match-count')).toHaveTextContent('1');
  });

  it('is case-insensitive', () => {
    renderView('scalar');
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'LISTPRICE' } });
    expect(screen.getByText('listPrice')).toBeInTheDocument();
  });

  it('shows one line when nothing matches, not an empty pane', () => {
    // Review Focus 5.
    renderView('scalar');
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'zzzz' } });
    expect(screen.getByText(/Nothing in this mapping matches/)).toBeInTheDocument();
    expect(screen.queryByRole('columnheader')).not.toBeInTheDocument();
  });

  it('highlights the matched run', () => {
    renderView('scalar');
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'list' } });
    expect(screen.getByText('list').tagName).toBe('MARK');
  });
});

describe('empty sheets', () => {
  it('names the sheet in its empty state', () => {
    const empty = parseMappingBody(JSON.stringify({
      domain: 'p', table: 't', drivingTable: 'D', columns: [],
    }));
    render(
      <SchemaMappingView
        mapping={empty} kind="conditional" onKindChange={vi.fn()}
        subject="p.p.mapping" version="1"
      />,
    );
    expect(screen.getByText(/No conditional columns/)).toBeInTheDocument();
  });
});

describe('row expansion', () => {
  it('opens and closes a detail region for one row', () => {
    renderView('scalar');
    expect(screen.queryByRole('region', { name: /itemStatus/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Show detail for itemStatus/ }));
    expect(screen.getByRole('region', { name: /itemStatus/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Hide detail for itemStatus/ }));
    expect(screen.queryByRole('region', { name: /itemStatus/ })).not.toBeInTheDocument();
  });

  it('opens at most one row at a time', () => {
    // Two 14-column rows expanded at once pushes the second off-screen; the
    // panel is a reading aid, not a comparison view.
    renderView('scalar');
    fireEvent.click(screen.getByRole('button', { name: /Show detail for itemStatus/ }));
    fireEvent.click(screen.getByRole('button', { name: /Show detail for listPrice/ }));
    expect(screen.queryByRole('region', { name: /itemStatus/ })).not.toBeInTheDocument();
    expect(screen.getByRole('region', { name: /listPrice/ })).toBeInTheDocument();
  });
});

describe('toCsv', () => {
  it('emits the workbook headers then one line per row', () => {
    const csv = toCsv(SHEET_HEADERS.scalar, MAPPING.scalar);
    const lines = csv.split('\n');
    expect(lines[0]).toBe(SHEET_HEADERS.scalar.map((h) => `"${h}"`).join(','));
    expect(lines).toHaveLength(3);
    expect(lines[1]).toContain('itemStatus');
  });

  it('quotes a cell containing a comma or a quote', () => {
    const rows = parseMappingBody(JSON.stringify({
      domain: 'p', table: 't', drivingTable: 'D',
      columns: [{
        goldColumnName: 'x', businessDescription: 'a, b and "c"', kind: 'scalar',
        dataType: 'string', source: { table: 'T', column: 'C', filter: null },
        channels: { event: false, message: false, api: false }, nullable: false,
      }],
    })).scalar;
    const csv = toCsv(SHEET_HEADERS.scalar, rows);
    expect(csv).toContain('"a, b and ""c"""');
  });

  it('renders channels as Y and N, matching the workbook', () => {
    const csv = toCsv(SHEET_HEADERS.scalar, MAPPING.scalar);
    expect(csv.split('\n')[1]).toContain('"Y"');
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd stacks/apicurio && npm --prefix ui run test -- --run SchemaMappingView
```

Expected: FAIL — `Failed to resolve import "./SchemaMappingView"`.

- [ ] **Step 3: Create the component**

Create `ui/src/features/schemas/components/SchemaMappingView.tsx`:

```tsx
import { useState, useMemo } from 'react';
import { ChevronDown, ChevronRight, Download, FileSpreadsheet } from 'lucide-react';
import { Badge } from '@/components/ui/Badge';
import { cn } from '@/lib/utils';
import { downloadBlob } from '../utils';
import { filterRows } from '../mapping-body';
import type {
  ParsedMapping, MappingRow, ScalarRow, ConditionalRow, NestedRow,
} from '../mapping-body';
import {
  MAPPING_KIND_LABELS, MAPPING_KIND_ORDER,
} from '../hooks/use-schema-url-state';
import type { MappingKindView } from '../hooks/use-schema-url-state';
import { Highlight } from './SchemaFieldsView';

/**
 * The Mapping tab: a gold attribute mapping read as the workbook people
 * already read it.
 *
 * Three sheets, one per column kind, carrying the
 * *-gold-attribute-mapping-template.xlsx columns in the workbook's own order.
 * That is not incidental — the three kinds carry genuinely different
 * information, which is why the workbook splits them, and it is already the
 * shape in the readers' heads.
 *
 * What the registered JSON carries and the workbook has no column for —
 * structured joins, scopes, transforms, derivation SQL, picks — lives in the
 * per-row expansion, and the named shared definitions also get their own
 * catalogs below. Design spec §2.2 and §4.4.
 *
 * Search FILTERS rather than only tinting, following the precedent
 * SchemaFieldsView documents: highlighting alone was useless at 63 rows, and
 * product's mapping flattens to more than that.
 */

/** The workbook's column headers, per sheet, in the workbook's order. */
export const SHEET_HEADERS: Record<MappingKindView, string[]> = {
  scalar: [
    'Gold Column Name', 'Business Description', 'Data Type',
    'Source Table', 'Source Column', 'Source Filter', 'Transform Logic',
    'Event?', 'Message?', 'API?', 'Nullable?', 'Notes', 'Join Grain',
  ],
  conditional: [
    'Gold Column Name', 'Business Description', 'Data Type',
    'Discriminator', 'Branch Condition',
    'Source Table', 'Source Column', 'Source Filter', 'Fallback',
    'Event?', 'Message?', 'API?', 'Notes', 'Join Grain',
  ],
  nested: [
    'Gold Column Name', 'Business Description', 'Field Name', 'Field Type',
    'Source Table', 'Source Column', 'Join Key to Item', 'Source Filter',
    'Sort Key?', 'Event?', 'Message?', 'API?', 'Notes', 'Join Grain',
  ],
};

/** Grid templates, one per sheet. First column is the chevron. */
const SHEET_COLS: Record<MappingKindView, string> = {
  scalar:
    '28px minmax(150px,1.2fr) minmax(200px,2fr) 90px minmax(160px,1.2fr) minmax(140px,1fr) ' +
    'minmax(120px,1fr) minmax(120px,1fr) 60px 70px 55px 70px minmax(140px,1fr) 100px',
  conditional:
    '28px minmax(150px,1.2fr) minmax(180px,1.6fr) 90px minmax(200px,1.4fr) ' +
    'minmax(150px,1fr) minmax(160px,1.2fr) minmax(130px,1fr) minmax(130px,1fr) minmax(120px,1fr) ' +
    '60px 70px 55px minmax(120px,1fr) 100px',
  nested:
    '28px minmax(150px,1.2fr) minmax(180px,1.6fr) minmax(130px,1fr) 90px ' +
    'minmax(150px,1fr) minmax(160px,1.2fr) minmax(160px,1.2fr) minmax(120px,1fr) ' +
    '70px 60px 70px 55px minmax(120px,1fr) 100px',
};

/** The Code and Fields views' pane height. All three sit in the same slot. */
const PANE_MAX_H = 570;
const ROW_PAD_X = 14;

const JOIN_GRAIN_NOTE =
  'DEPRECATED (UIFPE-104) — superseded by each source’s own structured join. ' +
  'Shown only where a legacy file still declares one; expand a row for the real join.';

/** `Y` / `–`, as the workbook writes it. */
function Flag({ on }: { on: boolean }) {
  return on ? <span className="text-ink">Y</span> : <span className="text-ink-mute">–</span>;
}

function TransformCell({ row }: { row: MappingRow }) {
  const t = row.transform;
  if (t === null || t === undefined) return <span className="text-ink-mute">—</span>;
  if (typeof t === 'string') {
    // Prose. DEPRECATED (UIFPE-105) but still accepted while files migrate.
    return <Badge tone="warn">{t}</Badge>;
  }
  const spec = t as Record<string, unknown>;
  if (typeof spec.rule === 'string') return <Badge tone="accent">{spec.rule}</Badge>;
  // CI flags every sqlExpr for engineering review, so the row says so without
  // the reader having to expand it.
  if (typeof spec.sqlExpr === 'string') return <Badge tone="warn">SQL</Badge>;
  return <Badge tone="neutral">inline</Badge>;
}

/** The cells of one row, in the sheet's header order, minus the chevron. */
function rowCells(row: MappingRow, query: string): React.ReactNode[] {
  const gold = <Highlight text={row.goldColumnName} query={query} />;
  const desc = <Highlight text={row.businessDescription} query={query} />;
  const table = <Highlight text={row.source.table ?? ''} query={query} />;
  const column = <Highlight text={row.source.column ?? ''} query={query} />;
  const filter = row.source.filter ?? '';
  const notes = <Highlight text={row.notes} query={query} />;
  const grain = row.joinGrain ?? '';
  const chan = [
    <Flag key="e" on={row.channels.event} />,
    <Flag key="m" on={row.channels.message} />,
    <Flag key="a" on={row.channels.api} />,
  ];
  const xform = <TransformCell row={row} />;

  if (row.kind === 'scalar') {
    const r = row as ScalarRow;
    return [gold, desc, r.dataType, table, column, filter, xform,
      ...chan, <Flag key="n" on={r.nullable} />, notes, grain];
  }
  if (row.kind === 'conditional') {
    const r = row as ConditionalRow;
    return [gold, desc, r.dataType, r.discriminator, r.condition,
      table, column, filter, r.fallback, ...chan, notes, grain];
  }
  const r = row as NestedRow;
  return [gold, desc, <Highlight key="f" text={r.fieldName} query={query} />, r.fieldType,
    table, column, r.joinKey, filter,
    <Flag key="s" on={r.sortKey} />, ...chan, notes, grain];
}

// ── CSV ─────────────────────────────────────────────────────────────────────
//
// The view replaces a workbook people open in Excel, and some of what they do
// there — pivot, annotate, send on — the page will not do. Exporting the sheet
// they are looking at, with the headers they know, is the honest answer rather
// than pretending the page subsumes Excel.

function csvCell(value: string): string {
  return `"${value.replace(/"/g, '""')}"`;
}

function flagText(on: boolean): string {
  return on ? 'Y' : 'N';
}

function transformText(row: MappingRow): string {
  const t = row.transform;
  if (t === null || t === undefined) return '';
  if (typeof t === 'string') return t;
  const spec = t as Record<string, unknown>;
  if (typeof spec.rule === 'string') return String(spec.rule);
  if (typeof spec.sqlExpr === 'string') return String(spec.sqlExpr);
  return 'inline';
}

export function rowToCsvCells(row: MappingRow): string[] {
  const common = [
    row.goldColumnName, row.businessDescription,
  ];
  const src = [row.source.table ?? '', row.source.column ?? '', row.source.filter ?? ''];
  const chan = [flagText(row.channels.event), flagText(row.channels.message), flagText(row.channels.api)];

  if (row.kind === 'scalar') {
    const r = row as ScalarRow;
    return [...common, r.dataType, src[0], src[1], src[2], transformText(row),
      ...chan, flagText(r.nullable), row.notes, row.joinGrain ?? ''];
  }
  if (row.kind === 'conditional') {
    const r = row as ConditionalRow;
    return [...common, r.dataType, r.discriminator, r.condition,
      src[0], src[1], src[2], r.fallback, ...chan, row.notes, row.joinGrain ?? ''];
  }
  const r = row as NestedRow;
  return [...common, r.fieldName, r.fieldType, src[0], src[1], r.joinKey, src[2],
    flagText(r.sortKey), ...chan, row.notes, row.joinGrain ?? ''];
}

export function toCsv(headers: string[], rows: MappingRow[]): string {
  return [
    headers.map(csvCell).join(','),
    ...rows.map((r) => rowToCsvCells(r).map(csvCell).join(',')),
  ].join('\n');
}

// ── The view ────────────────────────────────────────────────────────────────

function Fact({ label, children, testId }: {
  label: string; children: React.ReactNode; testId?: string;
}) {
  const empty = children === null || children === undefined || children === '';
  return (
    <div className="min-w-0" data-testid={testId}>
      <dt className="text-xs text-ink-mute">{label}</dt>
      <dd className="mt-0.5 font-mono text-[13px] text-ink break-all">
        {/* An em-dash, not a blank — the same rule the schema facts band
          * follows. A blank reads as "nothing to say here". */}
        {empty ? <span className="text-ink-mute">—</span> : children}
      </dd>
    </div>
  );
}

export interface SchemaMappingViewProps {
  mapping: ParsedMapping;
  kind: MappingKindView;
  onKindChange: (kind: MappingKindView) => void;
  subject: string;
  version: string | null;
}

export function SchemaMappingView({
  mapping, kind, onKindChange, subject, version,
}: SchemaMappingViewProps) {
  const [query, setQuery] = useState('');
  // One row at a time. Two 14-column rows expanded at once pushes the second
  // off-screen; the panel is a reading aid, not a comparison view.
  const [openRow, setOpenRow] = useState<string | null>(null);

  const { facts } = mapping;
  const sheets: Record<MappingKindView, MappingRow[]> = useMemo(
    () => ({ scalar: mapping.scalar, conditional: mapping.conditional, nested: mapping.nested }),
    [mapping],
  );
  const allRows = sheets[kind];
  // `filterRows`, not a bare `.filter(rowMatchesQuery)`. The description
  // sits on the first row of a group only, so filtering rows independently
  // answers "show me this column" by returning one branch of it.
  const rows = useMemo(() => filterRows(allRows, query), [allRows, query]);

  const headers = SHEET_HEADERS[kind];
  const cols = SHEET_COLS[kind];

  const handleCsv = () => {
    const name = `${subject}${version ? `-v${version}` : ''}-${kind}.csv`;
    downloadBlob(new Blob([toCsv(headers, rows)], { type: 'text/csv;charset=utf-8' }), name);
  };

  return (
    <div className="flex flex-col" style={{ gap: 12 }}>
      {/* ---------------------------------------------------- facts band */}
      <dl
        className="grid bg-paper-card border border-paper-line"
        style={{
          gridTemplateColumns: 'repeat(4, minmax(0,1fr))',
          gap: '16px 24px', borderRadius: 16, padding: '16px 20px',
        }}
      >
        <Fact label="Gold table">{facts.table}</Fact>
        <Fact label="Domain">{facts.domain}</Fact>
        <Fact label="Driving table">{facts.drivingTable}</Fact>
        <Fact label="Registry">
          {facts.registry ? (
            <span className="flex flex-col gap-1">
              <span>{facts.registry.group} / {facts.registry.artifact}</span>
              {facts.registry.compatibility
                ? <Badge tone="neutral">{facts.registry.compatibility}</Badge>
                : <Badge tone="warn">not set</Badge>}
            </span>
          ) : null}
        </Fact>
        <Fact label="Entity key" testId="mapping-entity-key">
          {facts.entityKey.length > 0 ? (
            <span className="flex flex-col">
              {/* Declared order, which IS the grain — never sorted. */}
              {facts.entityKey.map((p) => (
                <span key={p.goldColumn}>{p.goldColumn} ← {p.drivingColumn}</span>
              ))}
            </span>
          ) : null}
        </Fact>
        <Fact label="Driving Silver table">{facts.drivingSilverTable}</Fact>
        <Fact label="Batch">
          {facts.batch ? (
            <span className="flex flex-col">
              <span>{facts.batch.requestedBy} · {facts.batch.targetDate}</span>
              <span className="text-ink-mute">
                backfill {facts.batch.backfillMode}
                {facts.batch.newChannel ? ` · new channel ${facts.batch.newChannel}` : ' · no new channel'}
              </span>
            </span>
          ) : null}
        </Fact>
        <Fact label="Columns">
          {`${mapping.scalar.length} scalar · ${mapping.conditional.length} conditional · ${mapping.nested.length} nested`}
        </Fact>
      </dl>

      {/* ------------------------------------- sheet toggle · search · CSV */}
      <div className="flex items-center flex-wrap" style={{ gap: 10 }}>
        <div
          role="tablist"
          aria-label="Which kind of mapping column"
          className="flex shrink-0 bg-paper"
          style={{ borderRadius: 999, padding: 3 }}
        >
          {MAPPING_KIND_ORDER.map((k) => {
            const isActive = k === kind;
            return (
              <button
                key={k}
                type="button"
                role="tab"
                aria-selected={isActive}
                onClick={() => onKindChange(k)}
                className={cn(
                  'flex items-center gap-1.5 text-[13px] whitespace-nowrap',
                  isActive ? 'bg-accent text-white font-semibold'
                           : 'bg-transparent text-ink-soft font-medium',
                )}
                style={{ borderRadius: 999, padding: '7px 15px' }}
              >
                {MAPPING_KIND_LABELS[k]}
                {/* The count on the label, so the size of a sheet is known
                  * before opening it. */}
                <span className={isActive ? 'text-white/80' : 'text-ink-mute'}>
                  {sheets[k].length}
                </span>
              </button>
            );
          })}
        </div>

        <label className="flex items-center border border-light bg-paper-card"
               style={{ height: 34, borderRadius: 4, padding: '0 10px', gap: 6 }}>
          <span className="sr-only">Search this mapping</span>
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search columns, sources, transforms"
            className="bg-transparent text-[13px] text-ink outline-none"
            style={{ width: 240 }}
          />
        </label>

        {query && (
          <span data-testid="mapping-match-count" className="text-[13px] text-ink-mute">
            {rows.length} match{rows.length === 1 ? '' : 'es'}
          </span>
        )}

        <button
          type="button"
          onClick={handleCsv}
          aria-label={`Export the ${MAPPING_KIND_LABELS[kind]} sheet as CSV`}
          title={`Export the ${MAPPING_KIND_LABELS[kind]} sheet as CSV`}
          className="flex items-center gap-1.5 shrink-0 border border-light text-accent text-[13px]"
          style={{ height: 34, borderRadius: 4, padding: '0 10px' }}
        >
          <FileSpreadsheet size={15} />
          CSV
          <Download size={13} />
        </button>
      </div>

      {/* ----------------------------------------------------- the sheet */}
      <div className="bg-paper-card border border-paper-line overflow-hidden"
           style={{ borderRadius: 16 }}>
        {allRows.length === 0 ? (
          <p className="p-5 text-sm text-ink-mute">
            No {MAPPING_KIND_LABELS[kind].toLowerCase()} columns in this mapping.
          </p>
        ) : rows.length === 0 ? (
          // One line for the whole pane, the same sentence the Fields view
          // uses when a query matches nothing.
          <p className="p-5 text-sm text-ink-mute">
            Nothing in this mapping matches “{query}”.
          </p>
        ) : (
          <div className="overflow-auto" style={{ maxHeight: PANE_MAX_H }}>
            <div role="table" aria-label={`${MAPPING_KIND_LABELS[kind]} columns`}
                 style={{ minWidth: 'max-content' }}>
              <div
                role="row"
                className="sticky top-0 z-10 grid items-center border-b border-paper-divider bg-paper-raised text-[11px] font-medium text-accent"
                style={{ gridTemplateColumns: cols, gap: 10, padding: `8px ${ROW_PAD_X}px` }}
              >
                <span aria-hidden />
                {headers.map((h) => (
                  <span
                    key={h}
                    role="columnheader"
                    title={h === 'Join Grain' ? JOIN_GRAIN_NOTE : undefined}
                    className={h === 'Join Grain' ? 'text-ink-mute' : undefined}
                  >
                    {h}
                  </span>
                ))}
              </div>

              {rows.map((row) => {
                const isOpen = openRow === row.key;
                const Chevron = isOpen ? ChevronDown : ChevronRight;
                return (
                  <div key={row.key}>
                    <div
                      role="row"
                      data-testid={`mapping-row-${row.key}`}
                      className="grid items-start border-b border-paper-divider"
                      style={{ gridTemplateColumns: cols, gap: 10, padding: `10px ${ROW_PAD_X}px` }}
                    >
                      <button
                        type="button"
                        aria-expanded={isOpen}
                        aria-label={`${isOpen ? 'Hide' : 'Show'} detail for ${row.goldColumnName}`}
                        onClick={() => setOpenRow(isOpen ? null : row.key)}
                        className="flex items-center justify-center text-ink-mute"
                        style={{ width: 20, height: 20 }}
                      >
                        <Chevron size={14} />
                      </button>
                      {rowCells(row, query).map((cell, i) => (
                        <span
                          key={headers[i]}
                          role="cell"
                          className={cn(
                            'font-mono text-xs break-words',
                            i === 1 ? 'text-ink-soft font-sans' : 'text-ink',
                          )}
                        >
                          {cell}
                        </span>
                      ))}
                    </div>
                    {isOpen && (
                      // Filled by MappingRowDetail in Task 9.
                      <div
                        role="region"
                        aria-label={`Detail for ${row.goldColumnName}`}
                        data-testid={`mapping-detail-${row.key}`}
                        className="border-b border-paper-divider bg-accent-soft"
                        style={{ padding: `12px ${ROW_PAD_X + 20}px` }}
                      />
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd stacks/apicurio && npm --prefix ui run test -- --run SchemaMappingView
```

Expected: PASS, 22 tests.

- [ ] **Step 5: Lint and typecheck**

```bash
cd stacks/apicurio && npm --prefix ui run lint
cd stacks/apicurio/ui && npx tsc -b
```

Expected: both clean. `Highlight` must be exported from `SchemaFieldsView.tsx` — it already is (`export function Highlight`).

- [ ] **Step 6: Commit**

```bash
cd /Users/raj9karu/Documents/Claude/Projects/universal-Integration-framework
git add stacks/apicurio/ui/src/features/schemas/components/SchemaMappingView.tsx stacks/apicurio/ui/src/features/schemas/components/SchemaMappingView.test.tsx
git commit -F - <<'MSG'
Show a mapping as the three sheets people already read

The Mapping tab: a facts band, a sheet per column kind carrying the
*-gold-attribute-mapping-template.xlsx columns in the workbook's own order, a
search that filters, and CSV export of the sheet on screen.

Mirroring the workbook is the design, not a shortcut. The three kinds carry
genuinely different information — which is why the workbook splits them — and
that split is already the shape in the readers' heads.

Join Grain is rendered muted, with a header note saying it is superseded by
each source's own structured join. It is DEPRECATED per UIFPE-104, and
rendering it as a live column would teach the deprecated thing to everyone who
reads the page. A legacy file's value is still shown, because the view reports
what the artifact declares.

CSV export is here because the view replaces something people open in Excel,
and pivoting or annotating is not something this page will do. One row expands
at a time: two 14-column rows open at once pushes the second off-screen.

UIFPE-598 #comment Added SchemaMappingView — facts band (gold table, domain, driving table, ordered entity key, registry, batch, column counts), a sheet toggle with row counts, a filtering search with match count and amber highlight, the three workbook tables with sticky headers, per-row expansion, and CSV export with the workbook's headers. 22 tests.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
MSG
```

---

## Task 9: The row detail panel and the reference catalogs

**Files:**
- Create: `ui/src/features/schemas/components/MappingRowDetail.tsx`
- Create: `ui/src/features/schemas/components/MappingReferenceSections.tsx`
- Modify: `ui/src/features/schemas/components/SchemaMappingView.tsx` (render both)
- Test: `ui/src/features/schemas/components/MappingRowDetail.test.tsx`

**Interfaces:**
- Consumes from Task 6: `resolveJoin`, `resolveScope`, `resolveTransform`, `resolveSilverTable`, `resolvePick`, `ResolvedTransform`, `MappingRow`, `ConditionalRow`, `NestedRow`, `MappingReference`. From Task 8: the expansion `<div role="region">` it fills, and `SchemaMappingView`'s props.
- Produces:

```ts
export interface MappingRowDetailProps { row: MappingRow; body: Record<string, unknown> }
export function MappingRowDetail(props: MappingRowDetailProps): JSX.Element
export interface MappingReferenceSectionsProps { reference: MappingReference }
export function MappingReferenceSections(props: MappingReferenceSectionsProps): JSX.Element | null
```

- [ ] **Step 1: Write the failing tests**

Create `ui/src/features/schemas/components/MappingRowDetail.test.tsx`:

```tsx
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { MappingRowDetail } from './MappingRowDetail';
import { MappingReferenceSections } from './MappingReferenceSections';
import { parseMappingBody } from '../mapping-body';

const BODY_OBJ = {
  domain: 'product',
  table: 'gold_product.product',
  drivingTable: 'MTL_SYSTEM_ITEMS_B',
  drivingSilverTable: 'uif_echo_{env}_silver_tep.mtl_system_items_b',
  silverTables: { QP_LIST_HEADERS_B: 'uif_echo_{env}_silver_tep.qp_list_headers_b' },
  entityKey: [{ goldColumn: 'inventoryItemId', drivingColumn: 'INVENTORY_ITEM_ID' }],
  rowGroups: {
    ukOrgs: {
      table: 'MTL_PARAMETERS', column: 'ORGANIZATION_CODE',
      op: 'startsWith', values: ['01'], description: 'UK orgs (GBPE).',
    },
    nonUkOrgs: { complementOf: 'ukOrgs', description: 'Every org NOT in ukOrgs.' },
  },
  transforms: {
    convertYN: { steps: [{ op: 'upper' }] },
    custom: { sqlExpr: "regexp_replace({x}, 'a', 'b')" },
  },
  columns: [
    {
      goldColumnName: 'onDriving', businessDescription: 'd', kind: 'scalar', dataType: 'string',
      source: { table: 'MTL_SYSTEM_ITEMS_B', column: 'SEGMENT1', filter: null },
      channels: { event: false, message: false, api: true }, nullable: true,
    },
    {
      goldColumnName: 'joined', businessDescription: 'd', kind: 'scalar', dataType: 'string',
      source: {
        table: 'QP_LIST_HEADERS_B', column: 'NAME', filter: 'ATTR_GROUP_ID = 741',
        join: { type: 'inner', on: [{
          leftTable: 'MTL_SYSTEM_ITEMS_B', leftColumn: 'INVENTORY_ITEM_ID',
          rightColumn: 'INVENTORY_ITEM_ID',
        }] },
      },
      transform: { rule: 'convertYN' },
      channels: { event: false, message: false, api: true }, nullable: true,
    },
    {
      goldColumnName: 'reviewed', businessDescription: 'd', kind: 'scalar', dataType: 'string',
      source: { table: 'T', column: 'C', filter: null },
      transform: { rule: 'custom' },
      channels: { event: false, message: false, api: true }, nullable: true,
    },
    {
      goldColumnName: 'derived', businessDescription: 'd', kind: 'scalar', dataType: 'string',
      source: { table: 'T', column: 'C', filter: null },
      derivation: {
        sql: 'SELECT INVENTORY_ITEM_ID, max(X) AS derived FROM items_ext GROUP BY 1',
        keyedBy: [{ queryColumn: 'INVENTORY_ITEM_ID', entityColumn: 'INVENTORY_ITEM_ID' }],
        reads: { items_ext: ['INVENTORY_ITEM_ID', 'X'] },
      },
      channels: { event: false, message: false, api: true }, nullable: true,
    },
    {
      goldColumnName: 'scoped', businessDescription: 'd', kind: 'conditional', dataType: 'string',
      discriminator: { table: 'D', column: 'K' },
      channels: { event: false, message: false, api: true },
      branches: [
        { condition: '1', scope: { group: 'ukOrgs' },
          source: { table: 'T', column: 'C' }, fallback: 'returnNull' },
        { condition: 'ELSE', scope: { op: 'else' },
          source: { table: 'T', column: 'C2' }, fallback: 'returnNull' },
      ],
    },
    {
      goldColumnName: 'picked', businessDescription: 'd', kind: 'nested',
      joinKey: 'K -> item', channels: { event: false, message: false, api: true },
      fields: [{ name: 'f', type: 'string', sortKey: true, source: { table: 'T', column: 'C' } }],
      pick: {
        partitionBy: ['f'],
        orderBy: [{ expr: 'CASE WHEN END_DATE IS NULL THEN 0 ELSE 1 END' }, { expr: 'START_DATE', direction: 'desc' }],
      },
    },
  ],
};

const MAPPING = parseMappingBody(JSON.stringify(BODY_OBJ));
const BODY = MAPPING.value as Record<string, unknown>;
const scalar = (name: string) => MAPPING.scalar.find((r) => r.goldColumnName === name)!;

function renderDetail(row = scalar('joined')) {
  render(<MappingRowDetail row={row} body={BODY} />);
}

describe('the JOIN section', () => {
  it('renders the ON clause with the join type and the filter', () => {
    renderDetail();
    const join = screen.getByTestId('detail-join');
    expect(join).toHaveTextContent('INNER JOIN');
    expect(join).toHaveTextContent('QP_LIST_HEADERS_B');
    expect(join).toHaveTextContent('MTL_SYSTEM_ITEMS_B.INVENTORY_ITEM_ID');
    expect(join).toHaveTextContent('QP_LIST_HEADERS_B.INVENTORY_ITEM_ID');
    expect(join).toHaveTextContent('ATTR_GROUP_ID = 741');
  });

  it('says there is no join when the source IS the driving table', () => {
    renderDetail(scalar('onDriving'));
    expect(screen.getByTestId('detail-join')).toHaveTextContent(/driving table/i);
  });
});

describe('the SILVER section', () => {
  it('shows the env-templated Silver name for the row source', () => {
    renderDetail();
    expect(screen.getByTestId('detail-silver'))
      .toHaveTextContent('uif_echo_{env}_silver_tep.qp_list_headers_b');
  });

  it('is absent when no Silver name is declared for that table', () => {
    // Product's real mapping declares none at all, so this is the common case.
    renderDetail(scalar('reviewed'));
    expect(screen.queryByTestId('detail-silver')).not.toBeInTheDocument();
  });
});

describe('the SCOPE section', () => {
  it('resolves a named row group to its predicate', () => {
    const row = MAPPING.conditional.find((r) => r.condition === '1')!;
    render(<MappingRowDetail row={row} body={BODY} />);
    const scope = screen.getByTestId('detail-scope');
    expect(scope).toHaveTextContent('ukOrgs');
    expect(scope).toHaveTextContent("MTL_PARAMETERS.ORGANIZATION_CODE startsWith '01'");
  });

  it('explains op else rather than printing the word', () => {
    const row = MAPPING.conditional.find((r) => r.condition === 'ELSE')!;
    render(<MappingRowDetail row={row} body={BODY} />);
    expect(screen.getByTestId('detail-scope')).toHaveTextContent(/no sibling branch/);
  });

  it('is absent for a scalar row, which has no branches to scope', () => {
    renderDetail();
    expect(screen.queryByTestId('detail-scope')).not.toBeInTheDocument();
  });
});

describe('the XFORM section', () => {
  it('names the rule and renders its steps', () => {
    renderDetail();
    const x = screen.getByTestId('detail-xform');
    expect(x).toHaveTextContent('convertYN');
    expect(x).toHaveTextContent('upper');
  });

  it('shows a sqlExpr verbatim and flags it for review', () => {
    // CI flags every use for engineering review; the panel must say so.
    renderDetail(scalar('reviewed'));
    const x = screen.getByTestId('detail-xform');
    expect(x).toHaveTextContent("regexp_replace({x}, 'a', 'b')");
    expect(x).toHaveTextContent(/review/i);
  });
});

describe('the DERIVATION section', () => {
  it('shows the SQL, the keyedBy contract and the computed reads', () => {
    renderDetail(scalar('derived'));
    const d = screen.getByTestId('detail-derivation');
    expect(d).toHaveTextContent('SELECT INVENTORY_ITEM_ID');
    expect(d).toHaveTextContent('INVENTORY_ITEM_ID → INVENTORY_ITEM_ID');
    expect(d).toHaveTextContent('items_ext');
  });

  it('is absent when the column declares no derivation', () => {
    renderDetail();
    expect(screen.queryByTestId('detail-derivation')).not.toBeInTheDocument();
  });
});

describe('the PICK section', () => {
  it('shows partitionBy and the ordered orderBy expressions with directions', () => {
    const row = MAPPING.nested.find((r) => r.goldColumnName === 'picked')!;
    render(<MappingRowDetail row={row} body={BODY} />);
    const p = screen.getByTestId('detail-pick');
    expect(p).toHaveTextContent('f');
    expect(p).toHaveTextContent('CASE WHEN END_DATE IS NULL');
    expect(p).toHaveTextContent('desc');
  });
});

describe('MappingReferenceSections', () => {
  function renderRef() {
    render(<MappingReferenceSections reference={MAPPING.reference} />);
  }

  it('renders a collapsed section per non-empty collection, with counts', () => {
    renderRef();
    expect(screen.getByRole('button', { name: /Row groups/ })).toHaveTextContent('2');
    expect(screen.getByRole('button', { name: /Transforms/ })).toHaveTextContent('2');
    expect(screen.getByRole('button', { name: /Silver tables/ })).toHaveTextContent('2');
    expect(screen.getByRole('button', { name: /Entity key/ })).toHaveTextContent('1');
  });

  it('starts collapsed, and expands on click', () => {
    renderRef();
    expect(screen.queryByText('UK orgs (GBPE).')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Row groups/ }));
    expect(screen.getByText('UK orgs (GBPE).')).toBeInTheDocument();
  });

  it('shows a complement group beneath the base it follows', () => {
    renderRef();
    fireEvent.click(screen.getByRole('button', { name: /Row groups/ }));
    const list = screen.getByTestId('reference-row-groups');
    expect(list.textContent!.indexOf('ukOrgs'))
      .toBeLessThan(list.textContent!.indexOf('nonUkOrgs'));
    expect(list).toHaveTextContent(/complement of ukOrgs/);
  });

  it('reports how many columns reference each transform', () => {
    renderRef();
    fireEvent.click(screen.getByRole('button', { name: /Transforms/ }));
    const list = screen.getByTestId('reference-transforms');
    expect(list).toHaveTextContent('convertYN');
    expect(list).toHaveTextContent(/1 column/);
  });

  it('marks which Silver table is the driving one', () => {
    renderRef();
    fireEvent.click(screen.getByRole('button', { name: /Silver tables/ }));
    expect(screen.getByTestId('reference-silver-tables')).toHaveTextContent(/driving/i);
  });

  it('renders nothing at all when every collection is empty', () => {
    // Product's real mapping: no silverTables, no rowGroups... an empty
    // accordion is worse than no accordion.
    const bare = parseMappingBody(JSON.stringify({
      domain: 'p', table: 't', columns: [],
    }));
    const { container } = render(<MappingReferenceSections reference={bare.reference} />);
    expect(container).toBeEmptyDOMElement();
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd stacks/apicurio && npm --prefix ui run test -- --run MappingRowDetail
```

Expected: FAIL — `Failed to resolve import "./MappingRowDetail"`.

- [ ] **Step 3: Create `MappingRowDetail.tsx`**

```tsx
import {
  resolveJoin, resolveScope, resolveTransform, resolveSilverTable, resolvePick,
} from '../mapping-body';
import type { MappingRow, ConditionalRow, NestedRow, ResolvedTransform } from '../mapping-body';
import { Badge } from '@/components/ui/Badge';

/**
 * One row's expansion: the five things the registered mapping carries that
 * the *-gold-attribute-mapping-template.xlsx workbook has no column for.
 *
 * The table keeps the workbook's 13–14 columns; these live here instead, so
 * nothing is lost and nothing is crammed in. Design spec §4.3.
 *
 * Everything is rendered as DECLARED. An unresolved row group or transform
 * rule shows an unresolved marker rather than an error: the registry reports
 * what the artifact says, and rejecting a dangling reference is CI's job
 * (domains/product/qe/test_intake_hygiene.py).
 */

function Row({ label, testId, children }: {
  label: string; testId: string; children: React.ReactNode;
}) {
  return (
    <div className="grid" style={{ gridTemplateColumns: '92px minmax(0,1fr)', gap: 12 }}
         data-testid={testId}>
      <span className="text-[11px] font-semibold text-accent" style={{ paddingTop: 2 }}>
        {label}
      </span>
      <div className="min-w-0 font-mono text-xs text-ink">{children}</div>
    </div>
  );
}

function TransformBody({ t }: { t: ResolvedTransform }) {
  return (
    <div className="flex flex-col" style={{ gap: 4 }}>
      <span className="flex items-center gap-2">
        {t.name ? <Badge tone="accent">{t.name}</Badge> : <Badge tone="neutral">inline</Badge>}
        {t.kind === 'unresolved' && <Badge tone="warn">unresolved</Badge>}
        {/* CI flags every sqlExpr for engineering review, so say it here. */}
        {t.reviewFlagged && <Badge tone="warn">flagged for review</Badge>}
        {t.fallthrough && <span className="text-ink-mute">else {t.fallthrough}</span>}
      </span>
      {t.description && <span className="font-sans text-ink-soft">{t.description}</span>}
      {t.steps.map((s, i) => (
        <span key={`${s.op}-${i}`}>{s.op}{s.detail ? ` ${s.detail}` : ''}</span>
      ))}
      {t.cases.map((c, i) => (
        <span key={`${c.when}-${i}`}>{c.when} → {c.then}</span>
      ))}
      {t.sqlExpr && (
        <pre className="whitespace-pre-wrap break-all text-ink">{t.sqlExpr}</pre>
      )}
    </div>
  );
}

export interface MappingRowDetailProps {
  row: MappingRow;
  /** The parsed mapping body — the named definitions are resolved from it. */
  body: Record<string, unknown>;
}

export function MappingRowDetail({ row, body }: MappingRowDetailProps) {
  const drivingTable = typeof body.drivingTable === 'string' ? body.drivingTable : null;
  const join = resolveJoin(row.source, drivingTable);
  const silver = resolveSilverTable(row.source.table, body);
  const transform = resolveTransform(row.transform, body);
  const derivation = row.derivation as Record<string, unknown> | null;
  const pick = row.kind === 'nested' ? resolvePick((row as NestedRow).pick) : null;
  const scope = row.kind === 'conditional'
    ? resolveScope((row as ConditionalRow).scope, body)
    : null;

  return (
    <div className="flex flex-col" style={{ gap: 8 }}>
      <Row label="JOIN" testId="detail-join">
        {join.kind === 'driving' ? (
          <span className="text-ink-soft">
            {join.table} is the driving table — no join.
            {join.filter ? ` WHERE ${join.filter}` : ''}
          </span>
        ) : join.kind === 'none' ? (
          <span className="text-ink-mute">No source declared.</span>
        ) : (
          <div className="flex flex-col">
            <span>{join.type.toUpperCase()} JOIN {join.table}</span>
            {join.on.length === 0 ? (
              // The contract REQUIRES a join for every non-driving source. A
              // file missing one is a CI finding; showing the gap is honest,
              // inventing a key is not.
              <span className="text-signal-warn">
                declares no ON keys — the contract requires them for a non-driving source
              </span>
            ) : (
              join.on.map((p, i) => (
                <span key={p.left}>{i === 0 ? '  ON ' : ' AND '}{p.left} = {p.right}</span>
              ))
            )}
            {join.filter && <span>{`WHERE ${join.filter}`}</span>}
          </div>
        )}
      </Row>

      {silver && (
        <Row label="SILVER" testId="detail-silver">
          {/* `{env}` is left templated, as the artifact declares it — the
            * registry does not know which environment you are reading for. */}
          {silver}
        </Row>
      )}

      {scope && (
        <Row label="SCOPE" testId="detail-scope">
          <div className="flex flex-col">
            <span className="flex items-center gap-2">
              {scope.label}
              {scope.kind === 'unresolved' && <Badge tone="warn">unresolved</Badge>}
            </span>
            {scope.predicate && <span className="text-ink-soft">{scope.predicate}</span>}
          </div>
        </Row>
      )}

      {transform.kind !== 'none' && (
        <Row label="XFORM" testId="detail-xform"><TransformBody t={transform} /></Row>
      )}

      {derivation && (
        <Row label="DERIVATION" testId="detail-derivation">
          <div className="flex flex-col" style={{ gap: 4 }}>
            {/* ADR-0008: the query is the contract. The compiler wraps it as a
              * CTE and nothing else, so the SQL shown here is the SQL that
              * runs. */}
            <pre className="whitespace-pre-wrap break-all">{String(derivation.sql ?? '')}</pre>
            {Array.isArray(derivation.keyedBy) && (
              <span className="text-ink-soft">
                keyed by{' '}
                {derivation.keyedBy
                  .map((k) => {
                    const pair = k as Record<string, unknown>;
                    return `${String(pair.queryColumn)} → ${String(pair.entityColumn)}`;
                  })
                  .join(', ')}
              </span>
            )}
            {derivation.reads !== undefined && derivation.reads !== null && (
              <span className="text-ink-mute">
                {/* Computed by the validator from the parsed query and
                  * re-checked in CI — never typed by hand. */}
                reads{' '}
                {Object.entries(derivation.reads as Record<string, unknown>)
                  .map(([alias, cols]) =>
                    `${alias} (${(Array.isArray(cols) ? cols : []).join(', ')})`)
                  .join(' · ')}
              </span>
            )}
          </div>
        </Row>
      )}

      {pick && (
        <Row label="PICK" testId="detail-pick">
          <div className="flex flex-col">
            <span>one row per {pick.partitionBy.join(', ')}</span>
            {pick.orderBy.map((o, i) => (
              <span key={`${o.expr}-${i}`} className="text-ink-soft">
                {i === 0 ? 'ordered by ' : '     then '}{o.expr} {o.direction}
              </span>
            ))}
          </div>
        </Row>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Create `MappingReferenceSections.tsx`**

```tsx
import { useState } from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { Badge } from '@/components/ui/Badge';
import type { MappingReference } from '../mapping-body';

/**
 * The named shared definitions, each readable whole.
 *
 * These exist because a named definition is a SINGLE source of truth and the
 * row detail resolves it inline: a transform rule used by twelve columns is
 * shown twelve times and nowhere once. The rows resolve it so you never have
 * to leave the row; these hold it so you can read the set. Design spec §4.4.
 *
 * Every section hides when its collection is empty — product's mapping
 * declares no silverTables at all, and an empty accordion is worse than no
 * accordion.
 */

function Section({ name, count, testId, children }: {
  name: string; count: number; testId: string; children: React.ReactNode;
}) {
  // Collapsed by default: these are a reference, not the thing you came for.
  const [open, setOpen] = useState(false);
  const Chevron = open ? ChevronDown : ChevronRight;
  return (
    <div className="bg-paper-card border border-paper-line overflow-hidden"
         style={{ borderRadius: 12 }}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center gap-2 text-left text-[13px] font-medium text-ink"
        style={{ padding: '10px 14px' }}
      >
        <Chevron size={14} className="text-ink-mute" />
        {name}
        <Badge tone="neutral">{count}</Badge>
      </button>
      {open && (
        <div data-testid={testId} className="border-t border-paper-divider"
             style={{ padding: '10px 14px' }}>
          {children}
        </div>
      )}
    </div>
  );
}

export interface MappingReferenceSectionsProps {
  reference: MappingReference;
}

export function MappingReferenceSections({ reference }: MappingReferenceSectionsProps) {
  const { rowGroups, transforms, silverTables, entityKey } = reference;
  if (
    rowGroups.length === 0 && transforms.length === 0 &&
    silverTables.length === 0 && entityKey.length === 0
  ) {
    return null;
  }

  // A complement follows its base automatically in the contract, so it follows
  // it here too — reading the pair apart is how they drift in someone's head.
  const orderedGroups = [
    ...rowGroups.filter((g) => !g.complementOf),
    ...rowGroups.filter((g) => g.complementOf),
  ];

  return (
    <div className="flex flex-wrap" style={{ gap: 10 }}>
      {rowGroups.length > 0 && (
        <Section name="Row groups" count={rowGroups.length} testId="reference-row-groups">
          <dl className="flex flex-col" style={{ gap: 8 }}>
            {orderedGroups.map((g) => (
              <div key={g.name}>
                <dt className="font-mono text-xs text-accent">{g.name}</dt>
                <dd className="font-mono text-xs text-ink">{g.predicate}</dd>
                {g.description && (
                  <dd className="text-xs text-ink-soft">{g.description}</dd>
                )}
              </div>
            ))}
          </dl>
        </Section>
      )}

      {transforms.length > 0 && (
        <Section name="Transforms" count={transforms.length} testId="reference-transforms">
          <dl className="flex flex-col" style={{ gap: 8 }}>
            {transforms.map((t) => (
              <div key={t.name}>
                <dt className="flex items-center gap-2 font-mono text-xs text-accent">
                  {t.name}
                  {/* The count is what earns this catalog its place. */}
                  <span className="text-ink-mute">
                    {t.usedBy} column{t.usedBy === 1 ? '' : 's'}
                  </span>
                  {t.resolved.reviewFlagged && <Badge tone="warn">review</Badge>}
                </dt>
                {t.resolved.description && (
                  <dd className="text-xs text-ink-soft">{t.resolved.description}</dd>
                )}
                {t.resolved.steps.map((s, i) => (
                  <dd key={`${s.op}-${i}`} className="font-mono text-xs text-ink">
                    {s.op}{s.detail ? ` ${s.detail}` : ''}
                  </dd>
                ))}
                {t.resolved.cases.map((c, i) => (
                  <dd key={`${c.when}-${i}`} className="font-mono text-xs text-ink">
                    {c.when} → {c.then}
                  </dd>
                ))}
                {t.resolved.sqlExpr && (
                  <dd className="font-mono text-xs text-ink break-all">{t.resolved.sqlExpr}</dd>
                )}
              </div>
            ))}
          </dl>
        </Section>
      )}

      {silverTables.length > 0 && (
        <Section name="Silver tables" count={silverTables.length} testId="reference-silver-tables">
          <dl className="flex flex-col" style={{ gap: 6 }}>
            {silverTables.map((t) => (
              <div key={t.name} className="flex items-baseline gap-2">
                <dt className="font-mono text-xs text-accent">{t.name}</dt>
                <dd className="font-mono text-xs text-ink">{t.qualified}</dd>
                {t.driving && <Badge tone="neutral">driving</Badge>}
              </div>
            ))}
          </dl>
        </Section>
      )}

      {entityKey.length > 0 && (
        <Section name="Entity key" count={entityKey.length} testId="reference-entity-key">
          <dl className="flex flex-col" style={{ gap: 6 }}>
            {/* Declared order, which IS the grain. */}
            {entityKey.map((p) => (
              <div key={p.goldColumn} className="flex items-baseline gap-2">
                <dt className="font-mono text-xs text-accent">{p.goldColumn}</dt>
                <dd className="font-mono text-xs text-ink">← {p.drivingColumn}</dd>
              </div>
            ))}
          </dl>
        </Section>
      )}
    </div>
  );
}
```

- [ ] **Step 5: Render both from `SchemaMappingView.tsx`**

Add the imports:

```tsx
import { MappingRowDetail } from './MappingRowDetail';
import { MappingReferenceSections } from './MappingReferenceSections';
```

Replace the empty expansion `<div role="region" ... />` from Task 8 with:

```tsx
                      <div
                        role="region"
                        aria-label={`Detail for ${row.goldColumnName}`}
                        data-testid={`mapping-detail-${row.key}`}
                        className="border-b border-paper-divider bg-accent-soft"
                        style={{ padding: `12px ${ROW_PAD_X + 20}px` }}
                      >
                        <MappingRowDetail
                          row={row}
                          body={(mapping.value ?? {}) as Record<string, unknown>}
                        />
                      </div>
```

And add, immediately before the component's closing `</div>`:

```tsx
      <MappingReferenceSections reference={mapping.reference} />
```

- [ ] **Step 6: Run both test files to verify they pass**

```bash
cd stacks/apicurio && npm --prefix ui run test -- --run MappingRowDetail SchemaMappingView
```

Expected: PASS — 22 from Task 8 plus 18 new.

- [ ] **Step 7: Lint and typecheck**

```bash
cd stacks/apicurio && npm --prefix ui run lint
cd stacks/apicurio/ui && npx tsc -b
```

Expected: both clean.

- [ ] **Step 8: Commit**

```bash
cd /Users/raj9karu/Documents/Claude/Projects/universal-Integration-framework
git add stacks/apicurio/ui/src/features/schemas/components/MappingRowDetail.tsx \
        stacks/apicurio/ui/src/features/schemas/components/MappingReferenceSections.tsx \
        stacks/apicurio/ui/src/features/schemas/components/MappingRowDetail.test.tsx \
        stacks/apicurio/ui/src/features/schemas/components/SchemaMappingView.tsx
git commit -F - <<'MSG'
Expand a mapping row, and make the named definitions readable whole

The row detail carries what the workbook has no column for: the resolved ON
clause, the env-templated Silver name, the branch scope with its row group
resolved, the transform as steps or cases, derivation SQL with its keyedBy
contract and computed reads, and a nested column's ranked pick.

The reference sections exist because resolving a named definition inline shows
it once per row and never once as a definition — a transform rule used by
twelve columns is legible twelve times and unreadable as a set. Each section
carries how many columns cite it, which is the number that earns it its place.
A complement row group follows its base, because the contract makes it follow
automatically and reading the pair apart is how they drift.

Two honesty rules. A non-driving source declaring no ON keys is shown as the
gap it is rather than having a key invented for it. And a sqlExpr says
"flagged for review" on its face, because CI flags every use of one and the
reader should not have to know that.

Every section hides when empty. Product's mapping declares no silverTables at
all, and an empty accordion is worse than no accordion.

UIFPE-598 #comment Added MappingRowDetail (JOIN/SILVER/SCOPE/XFORM/DERIVATION/PICK, each shown only when the artifact declares it) and MappingReferenceSections (row groups with complements after their base, transforms with usage counts, Silver tables, entity key — each hidden when empty), wired into SchemaMappingView. 18 tests.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
MSG
```

---

## Task 10: Wire the tab into the detail page, gated on the body

**Files:**
- Modify: `ui/src/features/schemas/components/SchemaDetail.tsx`
- Modify: `ui/src/features/schemas/components/SchemaDetail.test.tsx`
- Modify: `ui/src/features/schemas/index.ts`
- Test: `ui/src/features/schemas/components/SchemaDetail.test.tsx`

**Interfaces:**
- Consumes: `parseMappingBody`, `ParsedMapping` (Task 6); `MappingKindView` (Task 7); `SchemaMappingView` (Task 8).
- Produces: nothing consumed later. This is the last task.

**Known-failing test on arrival.** Task 7 added `mapping` to `DETAIL_TAB_ORDER`, and `SchemaDetail.tsx:373` maps that array unconditionally, so every artifact currently shows a Mapping tab. That breaks the existing exhaustive assertion at `SchemaDetail.test.tsx` lines 225–235 (`the retired Overview tab > offers Schema, Compliance, Services and History — not Overview`):

```ts
const tabs = screen.getAllByRole('tab').filter((t) => !['Fields', 'Code', 'Graph'].includes(t.textContent ?? ''));
expect(tabs.map((t) => t.textContent)).toEqual(['Schema', 'Compliance', 'Services', 'History']);
```

Do **not** add `'Mapping'` to that list. Its fixture is an Avro body, and Step 3 suppresses the tab for a non-mapping body — so this test goes green again with no edit once Step 3 lands. If it is still red after Step 3, the gating is wrong, not the test.

- [ ] **Step 1: Write the failing tests**

Append to `ui/src/features/schemas/components/SchemaDetail.test.tsx`:

```tsx
const MAPPING_BODY = JSON.stringify({
  $schema: '../../gold-attribute-mapping.schema.json',
  domain: 'commerce',
  table: 'gold_commerce.product',
  drivingTable: 'MTL_SYSTEM_ITEMS_B',
  entityKey: [{ goldColumn: 'inventoryItemId', drivingColumn: 'INVENTORY_ITEM_ID' }],
  columns: [{
    goldColumnName: 'itemStatus', businessDescription: 'Lifecycle status.',
    kind: 'scalar', dataType: 'string',
    source: { table: 'MTL_SYSTEM_ITEMS_B', column: 'INVENTORY_ITEM_STATUS_CODE', filter: null },
    transform: null, channels: { event: true, message: true, api: true },
    nullable: true, notes: '',
  }],
}, null, 2);

const MAPPING_DETAIL = { ...DETAIL, type: 'JSON', content: MAPPING_BODY };

function serveMapping() {
  server.use(
    http.get(`${API}/schemas/default/commerce.product-value`, () =>
      HttpResponse.json(MAPPING_DETAIL),
    ),
  );
}

describe('the mapping tab (UIFPE-598)', () => {
  it('is absent for an Avro body', async () => {
    renderDetail();
    await waitFor(() => expect(screen.getByRole('heading', { level: 1 })).toBeInTheDocument());
    expect(screen.queryByRole('tab', { name: 'Mapping' })).not.toBeInTheDocument();
  });

  it('is absent for a JSON Schema body', async () => {
    // Type JSON is not enough — the body shape decides.
    server.use(
      http.get(`${API}/schemas/default/commerce.product-value`, () =>
        HttpResponse.json({
          ...DETAIL, type: 'JSON',
          content: JSON.stringify({
            $schema: 'https://json-schema.org/draft/2020-12/schema',
            type: 'object', properties: { id: { type: 'string' } },
          }),
        }),
      ),
    );
    renderDetail();
    await waitFor(() => expect(screen.getByRole('heading', { level: 1 })).toBeInTheDocument());
    expect(screen.queryByRole('tab', { name: 'Mapping' })).not.toBeInTheDocument();
  });

  it('appears for a mapping body', async () => {
    serveMapping();
    renderDetail();
    await waitFor(() =>
      expect(screen.getByRole('tab', { name: 'Mapping' })).toBeInTheDocument(),
    );
  });

  it('lands on the Mapping tab for a mapping body', async () => {
    // It is the only reading of a mapping that means anything, so landing on
    // Code and making the reader find it would waste the tab.
    serveMapping();
    renderDetail();
    await waitFor(() =>
      expect(screen.getByRole('tab', { name: 'Mapping' })).toHaveAttribute('aria-selected', 'true'),
    );
    expect(screen.getByText('gold_commerce.product')).toBeInTheDocument();
  });

  it('honours an explicit ?dtab=schema on a mapping', async () => {
    // Landing on Mapping is a default, not a lock — a deep link still wins.
    serveMapping();
    renderDetail('/schemas/default/commerce.product-value?dtab=schema');
    await waitFor(() =>
      expect(screen.getByRole('tab', { name: 'Schema' })).toHaveAttribute('aria-selected', 'true'),
    );
  });

  it('switches sheet and writes mkind to the URL', async () => {
    serveMapping();
    renderDetail();
    await waitFor(() => expect(screen.getByRole('tab', { name: /Scalar/ })).toBeInTheDocument());
    fireEvent.click(screen.getByRole('tab', { name: /Nested/ }));
    await waitFor(() => expect(currentSearch()).toContain('mkind=nested'));
  });
});

describe('the Schema tab on a mapping body', () => {
  it('offers Code only — Fields and Graph would render nothing useful', async () => {
    // Fields finds no fields or properties in a mapping; Graph draws its
    // columns as anonymous objects.
    serveMapping();
    renderDetail('/schemas/default/commerce.product-value?dtab=schema');
    await waitFor(() => expect(screen.getByRole('tab', { name: 'Schema' })).toBeInTheDocument());
    expect(screen.getByRole('tab', { name: /Code/ })).toBeInTheDocument();
    expect(screen.queryByRole('tab', { name: /Fields/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('tab', { name: /Graph/ })).not.toBeInTheDocument();
  });

  it('answers a stale ?schemaView=fields link with an explanation, not a blank table', async () => {
    serveMapping();
    renderDetail('/schemas/default/commerce.product-value?dtab=schema&schemaView=fields');
    await waitFor(() => expect(screen.getByRole('tab', { name: 'Schema' })).toBeInTheDocument());
    expect(screen.getByText(/mapping/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Mapping tab/ })).toBeInTheDocument();
  });

  it('still offers all three views for an Avro body', async () => {
    renderDetail('/schemas/default/commerce.product-value?dtab=schema');
    await waitFor(() => expect(screen.getByRole('tab', { name: /Fields/ })).toBeInTheDocument());
    expect(screen.getByRole('tab', { name: /Code/ })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /Graph/ })).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd stacks/apicurio && npm --prefix ui run test -- --run SchemaDetail
```

Expected: FAIL. The "absent for an Avro body" case fails because the tab currently renders unconditionally, and the mapping cases fail because nothing parses a mapping yet. The pre-existing `offers Schema, Compliance, Services and History` test also fails, for the same reason — see the note above this task.

- [ ] **Step 3: Gate the tab and the views in `SchemaDetail.tsx`**

Add the imports, beside the existing `parseSchemaBody` import:

```tsx
import { parseMappingBody } from '../mapping-body';
import { SchemaMappingView } from './SchemaMappingView';
import { MAPPING_KIND_ORDER } from '../hooks/use-schema-url-state';
```

`MAPPING_KIND_ORDER` is imported only if the lint rule for unused imports complains — the view owns the toggle, so `SchemaDetail` does not need it. **Omit it** unless a later step uses it.

Add, immediately after the existing `const body = useMemo(...)`:

```tsx
  // A mapping is a different body from a schema: no fields, no properties, no
  // metadata block. `parseSchemaBody` finds nothing in one, so the Mapping tab
  // is the only reading of it that means anything. Design spec §3.2.
  const mapping = useMemo(() => parseMappingBody(data?.content ?? ''), [data?.content]);
```

Add, after `const currentVersion = resolveCurrentVersion(...)`:

```tsx
  // The tab is offered ONLY for a mapping. A tab that is empty for twelve of
  // thirteen artifacts is worse than no tab.
  const visibleTabs = DETAIL_TAB_ORDER.filter((t) => t !== 'mapping' || mapping.isMapping);

  // Landing on Mapping is a DEFAULT, not a lock: `?dtab=` still wins. The URL
  // state's default is `schema`, so an untouched URL reads as `schema` here
  // and that is the case being redirected — a deliberate `?dtab=schema` on a
  // mapping is indistinguishable from no param at all, and the design calls
  // the default the more useful of the two readings.
  const hasExplicitTab = search.includes('dtab=');
  const activeTab =
    mapping.isMapping && !hasExplicitTab ? 'mapping' : state.detailTab;

  // Fields finds no fields or properties in a mapping and Graph draws its
  // columns as anonymous objects, so Code is the only honest reading here.
  const visibleSchemaViews = mapping.isMapping
    ? SCHEMA_VIEW_ORDER.filter((v) => v === 'code')
    : SCHEMA_VIEW_ORDER;
  const suppressedView = mapping.isMapping && state.schemaView !== 'code';
```

Then, in the JSX:

1. In the tab row, change `{DETAIL_TAB_ORDER.map((tab) => {` to `{visibleTabs.map((tab) => {`, and change `const isActive = tab === state.detailTab;` to `const isActive = tab === activeTab;`.
2. Change every `state.detailTab === '<x>'` guard to `activeTab === '<x>'` — there are five (`schema`, `compliance`, `services`, `history`, and the graph's second `CompatibilityRail` block).
3. In the view toggle, change `{SCHEMA_VIEW_ORDER.map((mode) => {` to `{visibleSchemaViews.map((mode) => {`.
4. Replace the `{state.schemaView === 'fields' && (...)}` block's opening guard with `{!suppressedView && state.schemaView === 'fields' && (` and leave its body alone; do the same for the `graph` block.
5. Add, immediately after the `graph` block inside the Schema tab:

```tsx
            {suppressedView && (
              <div className="bg-paper-card border border-paper-line p-5"
                   style={{ borderRadius: 16 }}>
                <p className="text-sm text-ink-mute">
                  This artifact is a gold attribute mapping, not a schema — it declares{' '}
                  <code className="font-mono">columns</code>, not{' '}
                  <code className="font-mono">fields</code> or{' '}
                  <code className="font-mono">properties</code>, so there is nothing for this
                  view to tabulate or draw.
                </p>
                <button
                  type="button"
                  onClick={() => set({ detailTab: 'mapping' })}
                  className="mt-3 text-sm font-medium text-accent underline"
                >
                  Open the Mapping tab
                </button>
              </div>
            )}
```

6. Add the Mapping tab's own panel, immediately after the Schema tab's closing `)}`:

```tsx
      {activeTab === 'mapping' && mapping.isMapping && (
        <SchemaMappingView
          mapping={mapping}
          kind={state.mappingKind}
          onKindChange={(kind) => set({ mappingKind: kind })}
          subject={data.artifact_id}
          version={currentVersion}
        />
      )}
```

- [ ] **Step 4: Export the new modules from `index.ts`**

Add:

```ts
export { SchemaMappingView } from './components/SchemaMappingView';
export { MappingRowDetail } from './components/MappingRowDetail';
export { MappingReferenceSections } from './components/MappingReferenceSections';
export {
  isMappingBody,
  parseMappingBody,
  parseMappingFacts,
  flattenScalar,
  flattenConditional,
  flattenNested,
  resolveJoin,
  resolveScope,
  resolveTransform,
  resolveSilverTable,
  resolvePick,
  rowMatchesQuery,
} from './mapping-body';
export type {
  ParsedMapping,
  MappingFacts,
  MappingRow,
  ScalarRow,
  ConditionalRow,
  NestedRow,
  MappingReference,
  ResolvedJoin,
  ResolvedScope,
  ResolvedTransform,
  ResolvedPick,
  EntityKeyPair,
} from './mapping-body';
```

And add `MAPPING_KIND_LABELS`, `MAPPING_KIND_ORDER` to the existing `use-schema-url-state` export block, and `MappingKindView` to its type export block.

- [ ] **Step 5: Run the tests to verify they pass**

```bash
cd stacks/apicurio && npm --prefix ui run test -- --run SchemaDetail
```

Expected: PASS, including the pre-existing `offers Schema, Compliance, Services and History — not Overview` test, which goes green again **without being edited** because its fixture is an Avro body. If it is still red, the gating in Step 3 is wrong.

- [ ] **Step 6: Run the whole stack gate**

```bash
cd stacks/apicurio && qe/ci.sh --allow-missing-tools
```

Expected: `all gates green`, with skips only for tools the machine lacks (Docker, and `terraform validate` on 1.15.0). `eslint`, `vitest` and `npm run build (tsc -b && vite build)` must all pass.

- [ ] **Step 7: Commit**

```bash
cd /Users/raj9karu/Documents/Claude/Projects/universal-Integration-framework
git add stacks/apicurio/ui/src/features/schemas/components/SchemaDetail.tsx \
        stacks/apicurio/ui/src/features/schemas/components/SchemaDetail.test.tsx \
        stacks/apicurio/ui/src/features/schemas/index.ts
git commit -F - <<'MSG'
Offer the Mapping tab only to an artifact that is one

The tab is gated on the body, not on the artifact type: `JSON` covers both a
gold master and a mapping, and only the shape tells them apart. A tab that is
empty for twelve of thirteen artifacts is worse than no tab.

A mapping lands on that tab, because it is the only reading of one that means
anything — but a `?dtab=` link still wins, so the default is a default and not
a lock. The Schema tab keeps Code and drops Fields and Graph for a mapping:
Fields finds no fields or properties in one and Graph draws its columns as
anonymous objects. A stale `?schemaView=fields` link gets the reason and a way to
the Mapping tab rather than a blank table, which would assert the artifact has
no columns.

The pre-existing tab-list assertion is deliberately left alone. Its fixture is
Avro, so gating the tab is what makes it pass again — editing it to expect
Mapping would have hidden the gate not working.

UIFPE-598 #comment Wired SchemaMappingView into the detail page: the Mapping tab renders only for a mapping body, a mapping lands on it unless ?dtab= says otherwise, and the Schema tab suppresses Fields and Graph for one with an explanatory empty state for stale links. Exports added. 9 tests; full stack gate green.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
MSG
```

---

## Self-Review

**1. Spec coverage.** Every section of the design spec maps to a task: §1.1–1.3 → Tasks 1–2; §1.4–1.5 → Tasks 1–2; §2.1–2.2 → Tasks 5, 8; §3.1 → Tasks 4, 6; §3.2 → Tasks 7, 10; §3.3 → all; §4.1–4.2 → Task 8; §4.3 → Task 9; §4.4 → Task 9; §4.5 → Task 8; §4.6 → Tasks 8, 9; §5 → Tasks 6, 8, 9 (error handling is distributed to the module that owns each case); §6 → every task's own test steps; §7 → nothing, correctly, since it is the list of things this work does not do.

**2. Placeholders.** None. Every step carries literal code or a literal command.

**3. Type consistency.** `MappingKindView` (Task 7, the URL union) and `MappingKind` (Task 5, the body's `kind` discriminant) are deliberately two names for the same three strings: one is URL vocabulary validated at the boundary, the other is a body field. Task 8 imports `MappingKindView` and indexes `SHEET_HEADERS`/`SHEET_COLS` with it. `ParsedMapping.value` is `unknown` and Task 9 receives it as `Record<string, unknown>`; Task 10's Step 3 casts it at the call site, which is the one place that cast belongs.

**4. Review Focus.** All five are pinned: 1 → Task 2 (`test_a_bare_json_object_reports_the_missing_metadata_block`); 2 → Task 1 (`test_is_mapping_body_rejects_columns_that_are_not_a_list`); 3 → Task 2 (`test_pii_fields_as_a_bare_string_still_coerces_on_the_json_path`); 4 → Task 5 (the two empty-array cases); 5 → Task 8 (`shows one line when nothing matches, not an empty pane`).
