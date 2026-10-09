"""Unit tests for the metadata validator.

Pure tests — no httpx, no real network. Uses MockCmdbClient directly.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from app.clients.cmdb_client import MockCmdbClient
from app.services.metadata_validator import validate

_CMDB = MockCmdbClient()

_GOOD_AVRO = json.dumps({
    "type": "record",
    "name": "ProductCreated",
    "namespace": "product",
    "fields": [
        {"name": "id", "type": "string"},
        {"name": "name", "type": "string"},
        {"name": "email", "type": "string"},
    ],
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
})

_GOOD_PROPERTIES = {
    "owner.service": "CI0093421",
    "domain": "product",
    "dataClassification": "internal",
    "lifecycle": "active",
}


@pytest.mark.asyncio
async def test_valid_schema_passes() -> None:
    result = await validate(
        content=_GOOD_AVRO,
        properties=_GOOD_PROPERTIES,
        cmdb=_CMDB,
        artifact_id="product.product.created-value",
        group_id="default",
        schema_type="AVRO",
    )
    assert result.passed
    assert result.failures == []


@pytest.mark.asyncio
async def test_non_avro_returns_unsupported_failure() -> None:
    result = await validate(
        content="syntax = 'proto3';",
        properties={},
        cmdb=_CMDB,
        artifact_id="product.product.created-value",
        group_id="default",
        schema_type="PROTOBUF",
    )
    assert not result.passed
    rules = [f.rule for f in result.failures]
    assert "schema_type_unsupported" in rules


@pytest.mark.asyncio
async def test_missing_metadata_block() -> None:
    body = json.dumps({"type": "record", "name": "Foo", "fields": []})
    result = await validate(
        content=body,
        properties={},
        cmdb=_CMDB,
        artifact_id="product.product.created-value",
        group_id="default",
        schema_type="AVRO",
    )
    assert not result.passed
    assert any(f.rule == "metadata_block_missing" for f in result.failures)


@pytest.mark.asyncio
async def test_owner_missing() -> None:
    body = json.loads(_GOOD_AVRO)
    body["metadata"]["owner"] = {}
    result = await validate(
        content=json.dumps(body),
        properties={},
        cmdb=_CMDB,
        artifact_id="product.product.created-value",
        group_id="default",
        schema_type="AVRO",
    )
    assert not result.passed
    assert any(f.rule == "owner_missing" for f in result.failures)


@pytest.mark.asyncio
async def test_owner_unresolved() -> None:
    body = json.loads(_GOOD_AVRO)
    body["metadata"]["owner"]["service"] = "CI9999999"
    result = await validate(
        content=json.dumps(body),
        properties={},
        cmdb=_CMDB,
        artifact_id="product.product.created-value",
        group_id="default",
        schema_type="AVRO",
    )
    assert not result.passed
    assert any(f.rule == "owner_unresolved" for f in result.failures)


@pytest.mark.asyncio
async def test_domain_mismatch() -> None:
    body = json.loads(_GOOD_AVRO)
    body["metadata"]["domain"] = "learning"
    result = await validate(
        content=json.dumps(body),
        properties={},
        cmdb=_CMDB,
        artifact_id="product.product.created-value",
        group_id="default",
        schema_type="AVRO",
    )
    assert not result.passed
    assert any(f.rule == "domain_mismatch" for f in result.failures)


@pytest.mark.asyncio
async def test_classification_invalid() -> None:
    body = json.loads(_GOOD_AVRO)
    body["metadata"]["dataClassification"] = "topsecret"
    result = await validate(
        content=json.dumps(body),
        properties={},
        cmdb=_CMDB,
        artifact_id="product.product.created-value",
        group_id="default",
        schema_type="AVRO",
    )
    assert not result.passed
    assert any(f.rule == "classification_invalid" for f in result.failures)


@pytest.mark.asyncio
async def test_lifecycle_invalid() -> None:
    body = json.loads(_GOOD_AVRO)
    body["metadata"]["lifecycle"] = "retired"
    result = await validate(
        content=json.dumps(body),
        properties={},
        cmdb=_CMDB,
        artifact_id="product.product.created-value",
        group_id="default",
        schema_type="AVRO",
    )
    assert not result.passed
    assert any(f.rule == "lifecycle_invalid" for f in result.failures)


@pytest.mark.asyncio
async def test_pii_field_not_found() -> None:
    body = json.loads(_GOOD_AVRO)
    body["metadata"]["piiFields"] = ["ssn"]  # not in fields
    result = await validate(
        content=json.dumps(body),
        properties={},
        cmdb=_CMDB,
        artifact_id="product.product.created-value",
        group_id="default",
        schema_type="AVRO",
    )
    assert not result.passed
    assert any(f.rule == "pii_field_not_found" for f in result.failures)


@pytest.mark.asyncio
async def test_pii_field_present_passes() -> None:
    result = await validate(
        content=_GOOD_AVRO,
        properties=_GOOD_PROPERTIES,
        cmdb=_CMDB,
        artifact_id="product.product.created-value",
        group_id="default",
        schema_type="AVRO",
    )
    assert result.passed
    assert not any(f.rule == "pii_field_not_found" for f in result.failures)


@pytest.mark.asyncio
async def test_properties_mismatch() -> None:
    mismatched = {**_GOOD_PROPERTIES, "domain": "learning"}  # body says "product"
    result = await validate(
        content=_GOOD_AVRO,
        properties=mismatched,
        cmdb=_CMDB,
        artifact_id="product.product.created-value",
        group_id="default",
        schema_type="AVRO",
    )
    assert not result.passed
    assert any(f.rule == "properties_mismatch" for f in result.failures)


@pytest.mark.asyncio
async def test_multiple_failures_accumulate() -> None:
    body = {
        "type": "record",
        "name": "Bad",
        "fields": [],
        "metadata": {
            "owner": {"service": "CI9999999"},
            "domain": "learning",
            "dataClassification": "unknown",
            "lifecycle": "gone",
            "piiFields": [],
        },
    }
    result = await validate(
        content=json.dumps(body),
        properties={},
        cmdb=_CMDB,
        artifact_id="product.product.created-value",
        group_id="default",
        schema_type="AVRO",
    )
    assert not result.passed
    rules = [f.rule for f in result.failures]
    assert "owner_unresolved" in rules
    assert "domain_mismatch" in rules
    assert "classification_invalid" in rules
    assert "lifecycle_invalid" in rules


# ── ADR-0023: subject shape ─────────────────────────────────────────────────
#
# Every case comes from stacks/apicurio/standards/subject-naming.cases.json,
# the same corpus the UI's vitest suite loads. A regex that drifts in either
# language fails that language's tests.

#: parents[2] is stacks/apicurio/ — the standards folder is this stack's own,
#: so the corpus is reached without counting hops to the repository root.
_CASES = json.loads(
    (
        pathlib.Path(__file__).resolve().parents[2]
        / "standards" / "subject-naming.cases.json"
    ).read_text(encoding="utf-8")
)


def _subject_failures(result) -> list[str]:
    return [f.rule for f in result.failures if f.rule == "subject_shape_invalid"]


@pytest.mark.parametrize("case", _CASES["valid"], ids=lambda c: c["subject"])
@pytest.mark.asyncio
async def test_in_scope_subjects_raise_no_shape_failure(case):
    result = await validate(
        content=_GOOD_AVRO,
        properties=_GOOD_PROPERTIES,
        cmdb=_CMDB,
        artifact_id=case["subject"],
        group_id=case["subject"].split(".")[0],
        schema_type="AVRO",
    )
    assert not _subject_failures(result), case["subject"]


_UNCLAIMED = [c for c in _CASES["invalid"] if not c["claimed"]]
_CLAIMED = [c for c in _CASES["invalid"] if c["claimed"]]


@pytest.mark.parametrize("case", _UNCLAIMED, ids=lambda c: c["subject"] or "<empty>")
@pytest.mark.asyncio
async def test_a_subject_outside_this_rules_authority_is_left_alone(case):
    """The load-bearing half.

    The five types share a group with artifacts governed by other standards —
    the normative `<entity>-<action>-data`, `uif/pearson-event-envelope`, the
    OpenAPI contracts, capability artifacts like `legal:vocabulary`. Flagging
    any of those would make the compliance verdict lie about an artifact this
    rule has no authority over.

    Note these subjects ARE rejected by the pattern. Whether the pattern matches
    and whether the rule may judge are different questions; `claimed` in the
    corpus is the second one.
    """
    result = await validate(
        content=_GOOD_AVRO,
        properties=_GOOD_PROPERTIES,
        cmdb=_CMDB,
        artifact_id=case["subject"],
        group_id="product",
        schema_type="AVRO",
    )
    assert not _subject_failures(result), f"{case['subject']!r}: {case['why']}"


@pytest.mark.parametrize("case", _CLAIMED, ids=lambda c: c["subject"] or "<empty>")
@pytest.mark.asyncio
async def test_a_malformed_subject_within_this_rules_authority_fails(case):
    """A subject whose type slot is one of the five tokens is claiming this
    grammar, so its other slots are the rule's business."""
    result = await validate(
        content=_GOOD_AVRO,
        properties=_GOOD_PROPERTIES,
        cmdb=_CMDB,
        artifact_id=case["subject"],
        group_id="product",
        schema_type="AVRO",
    )
    assert _subject_failures(result) == ["subject_shape_invalid"], (
        f"{case['subject']!r}: {case['why']}"
    )


def test_the_validators_pattern_equals_the_shared_corpus():
    """The literal in the validator and the corpus must not drift.

    The pattern is a literal because the standards folder is not shipped in the
    service image; this is the test that makes that safe, and it is the ADR-0014
    precedent — one table, plus a check that the docs and the code agree.
    """
    from app.services.metadata_validator import _SUBJECT_SHAPE, _SUBJECT_TYPES
    assert list(_SUBJECT_TYPES) == _CASES["types"]
    assert _SUBJECT_SHAPE.pattern == _CASES["pattern"], (
        "validator pattern differs from subject-naming.cases.json"
    )


@pytest.mark.parametrize("subject", [
    "product.marketing_copy.gold",   # snake entity; a subject renders it kebab
    "Product.product.gold",          # uppercase domain
    "product.Product.event",         # uppercase entity
    "product.-copy.mapping",         # entity may not start with a hyphen
    "product.copy-.message",         # nor end with one
    "product.mark--copy.api",        # nor carry a double hyphen
    "product.product.gold.v2",       # no version segment: majors are semver
])
@pytest.mark.asyncio
async def test_a_malformed_subject_claiming_a_valid_type_slot_is_rejected(subject):
    """This is what the rule is for, and the boundary is the type slot.

    A subject whose type slot is one of the five tokens is claiming this
    grammar, so its other slots are this rule's business. Getting one wrong
    registers a contract at an address no consumer resolves — and that failure
    is silent, indistinguishable from a contract with no subscribers yet.

    The rule deliberately does NOT judge a subject whose type slot is something
    else. The registry holds artifacts governed by other standards, and a rule
    that flagged those would make the compliance verdict lie about artifacts it
    has no authority over. A typo'd type token like `product.product.messaging`
    is therefore NOT caught here — it is caught by the repository test, which
    knows the full out-of-scope allowlist and can afford to be strict.
    """
    result = await validate(
        content=_GOOD_AVRO,
        properties=_GOOD_PROPERTIES,
        cmdb=_CMDB,
        artifact_id=subject,
        group_id="product",
        schema_type="AVRO",
    )
    assert _subject_failures(result) == ["subject_shape_invalid"], subject
    msg = next(f.message for f in result.failures if f.rule == "subject_shape_invalid")
    assert subject in msg, "the message must quote the offending subject"


@pytest.mark.asyncio
async def test_subject_shape_is_checked_before_the_type_gate():
    """The ordering guarantee, proved on a type the gate still rejects.

    A subject's shape is an addressing concern: it is a property of the
    coordinate, not of the body, so it must be judged without needing the body
    parsed or the type supported. PROTOBUF is the case that proves it — the
    type gate returns early for it, so a subject rule placed after that gate
    would report nothing at all here.
    """
    result = await validate(
        content="syntax = 'proto3';",
        properties={},
        cmdb=_CMDB,
        artifact_id="product.marketing_copy.gold",
        group_id="product",
        schema_type="PROTOBUF",
    )
    assert _subject_failures(result) == ["subject_shape_invalid"]
    assert any(f.rule == "schema_type_unsupported" for f in result.failures), (
        "the unsupported-type failure must still be reported too"
    )


@pytest.mark.asyncio
async def test_subject_shape_is_checked_for_a_json_schema():
    """All five ADR-0023 types are JSON Schema, so the subject rule must fire.

    Before UIFPE-597 this asserted `schema_type_unsupported` alongside the
    subject failure, because the type gate rejected every non-AVRO body. JSON
    is now a supported type, so that failure is deliberately gone and the
    artifact is judged on its metadata like any other.
    """
    result = await validate(
        content="{}",
        properties={},
        cmdb=_CMDB,
        artifact_id="product.marketing_copy.gold",
        group_id="product",
        schema_type="JSON",
    )
    assert _subject_failures(result) == ["subject_shape_invalid"]
    assert not any(f.rule == "schema_type_unsupported" for f in result.failures), (
        "JSON is a supported type since UIFPE-597"
    )


# --- Body-shape helpers (UIFPE-597) -----------------------------------------

from app.services.metadata_validator import (  # noqa: E402
    _is_mapping_body,
    _named_children,
    _pii_path_segments,
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


# --- piiFields resolves the WHOLE path (UIFPE-619) --------------------------
#
# The rule used to check a path's first dot-segment only. Three holes followed:
# a typo below the top level passed, an array subscript was rejected although
# it names a real field, and an entry naming nothing at all passed silently.

_NESTED = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
        "productId": {"type": "string"},
        # product's REAL shape: `type` is a union, so only the sibling `items`
        # identifies this as an array.
        "contributors": {
            "type": ["array", "null"],
            "items": {
                "type": "object",
                "properties": {
                    "contributorGivenName": {"type": "string"},
                    "affiliation1": {"type": "string"},
                },
            },
        },
    },
    "metadata": {
        "owner": {"service": "CI0093421", "team": "TM-PRODUCT-CATALOG",
                  "email": "product-catalog@pearson.com"},
        "domain": "product",
        "dataClassification": "internal",
        "lifecycle": "active",
        "piiFields": [],
    },
}


async def _pii(paths, body=None):
    doc = json.loads(json.dumps(body or _NESTED))
    doc["metadata"]["piiFields"] = paths
    return await validate(
        content=json.dumps(doc), properties={}, cmdb=_CMDB,
        artifact_id="product.product.api-created", group_id="product",
        schema_type="JSON",
    )


@pytest.mark.asyncio
async def test_pii_path_typo_below_the_top_level_is_caught() -> None:
    """The hole that mattered: only the first segment was ever checked.

    A PII declaration naming a field that does not exist is worse than no
    declaration, because the artifact is reported compliant either way.
    """
    result = await _pii(["contributors.contributorGivenNaem"])
    hits = [f for f in result.failures if f.rule == "pii_field_not_found"]
    assert hits, "a typo below the top level went unreported"
    assert "contributorGivenNaem" in hits[0].message


@pytest.mark.asyncio
async def test_pii_path_through_a_real_nested_field_passes() -> None:
    result = await _pii(["contributors.contributorGivenName", "productId"])
    assert not [f for f in result.failures if f.rule.startswith("pii_field")]


@pytest.mark.asyncio
async def test_pii_path_accepts_array_subscripts() -> None:
    """`contributors[].x` and `contributors[0].x` name the same field.

    Rejecting them produced seven false `pii_field_not_found` failures on
    2026-09-25. A subscript picks an element, and every element of an array
    has the same fields, so it carries no information the rule needs.
    """
    for path in ("contributors[].contributorGivenName",
                 "contributors[0].contributorGivenName",
                 "$.contributors[].affiliation1"):
        result = await _pii([path])
        assert not [f for f in result.failures if f.rule.startswith("pii_field")], path


@pytest.mark.asyncio
async def test_pii_path_still_rejects_a_subscripted_field_that_does_not_exist() -> None:
    """Accepting the notation must not mean accepting the path.

    Without this, stripping the subscript could be implemented as "give up and
    pass", which would look identical on the happy path.
    """
    result = await _pii(["contributors[].nopeNotAField"])
    assert any(f.rule == "pii_field_not_found" for f in result.failures)


@pytest.mark.asyncio
async def test_malformed_pii_entries_are_flagged_not_skipped() -> None:
    """`""`, `"$."` and `"..."` used to pass as though they declared something."""
    for path in ("", "$.", "...", "[]"):
        result = await _pii([path])
        assert any(f.rule == "pii_field_malformed" for f in result.failures), repr(path)


@pytest.mark.asyncio
async def test_pii_path_resolves_through_an_avro_union_and_nested_record() -> None:
    """Avro's optional-record shape must resolve like a required one."""
    body = {
        "type": "record",
        "name": "Product",
        "fields": [
            {"name": "id", "type": "string"},
            {"name": "contributors", "type": ["null", {
                "type": "array",
                "items": {"type": "record", "name": "Contributor", "fields": [
                    {"name": "contributorGivenName", "type": "string"},
                ]},
            }]},
        ],
        "metadata": json.loads(json.dumps(_NESTED["metadata"])),
    }
    ok = await _pii(["contributors.contributorGivenName"], body=body)
    assert not [f for f in ok.failures if f.rule.startswith("pii_field")]
    bad = await _pii(["contributors.notThere"], body=body)
    assert any(f.rule == "pii_field_not_found" for f in bad.failures)


def test_pii_path_segments_strips_the_prefix_and_subscripts() -> None:
    assert _pii_path_segments("$.contributors[0].name") == ["contributors", "name"]
    assert _pii_path_segments("a[].b[2].c") == ["a", "b", "c"]
    # Naming nothing must be distinguishable from naming something absent.
    for empty in ("", "$.", "...", "[]", "   "):
        assert _pii_path_segments(empty) == [], repr(empty)


def test_top_level_field_names_agrees_with_named_children() -> None:
    """One definition of "this node's fields", not two that can drift.

    `_top_level_field_names` now delegates, so this pins the delegation rather
    than letting a second implementation grow back.
    """
    for body in (_NESTED, json.loads(_GOOD_AVRO), json.loads(_GOOD_JSON_SCHEMA)):
        assert _top_level_field_names(body) == set(_named_children(body))


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
async def test_mapping_injected_domain_mismatch_is_caught() -> None:
    """A mapping under the wrong owner: its own domain is right, the stamp is not.

    This is the UAT stopgap of 2026-09-25 reproduced. price was registered
    under product's URN, so `inject_metadata` stamped domain 'product' onto a
    mapping whose own domain field correctly said 'price'. The mapping rules
    read that own field, passed it, and the artifact sat mis-owned while its
    `gold` sibling was reported for two days.
    """
    body = json.loads(_GOOD_MAPPING)
    body["domain"] = "price"
    body["registry"]["group"] = "price"      # isolate: keep the mapping's own rules clean
    body["metadata"] = {                     # what registration under product's URN writes
        "owner": {"service": "CI0093421", "team": None, "email": None},
        "domain": "product",
        "dataClassification": "internal",
        "piiFields": [],
        "lifecycle": "active",
    }
    result = await validate(
        content=json.dumps(body),
        properties={},
        cmdb=_CMDB,
        artifact_id="price.price.mapping",
        group_id="price",
        schema_type="JSON",
    )
    assert not result.passed
    assert not any(f.rule == "mapping_domain_mismatch" for f in result.failures), (
        "the mapping's own domain field is correct — a failure here means the "
        "test is no longer isolating the injected-domain rule"
    )
    # Asserted on the message, not the rule id alone: `domain_mismatch` is
    # raised by the metadata path too, so the id alone would still pass if this
    # sub-check were deleted.
    hits = [f for f in result.failures if f.rule == "domain_mismatch"]
    assert hits, "the stamped domain 'product' on a price.* artifact was not flagged"
    assert "stamped at registration" in hits[0].message
    # The message must not leave the reader thinking the file is wrong.
    assert "'price'" in hits[0].message


@pytest.mark.asyncio
async def test_mapping_without_injected_metadata_is_not_flagged() -> None:
    """The source file has no metadata block and must not be flagged for it.

    Guards the new rule from becoming a false positive on every mapping
    validated before registration injects a block.
    """
    result = await validate(
        content=_GOOD_MAPPING,
        properties={},
        cmdb=_CMDB,
        artifact_id="product.product.mapping",
        group_id="product",
        schema_type="JSON",
    )
    assert not any(f.rule == "domain_mismatch" for f in result.failures)
    assert result.passed


@pytest.mark.asyncio
async def test_mapping_with_a_deactivated_owner_is_caught() -> None:
    """The case the domain rule cannot see (UIFPE-619).

    A mapping registered under the WRONG service is caught by comparing the
    stamped domain with the coordinate. A mapping registered under the RIGHT
    service that has since been deactivated keeps the domain it was stamped
    with, so the domain still agrees — only asking the CMDB reveals that the
    owner is gone. `_validate_mapping` asked nothing of the owner at all.
    """
    body = json.loads(_GOOD_MAPPING)
    body["metadata"] = {
        "owner": {"service": "CI0000000-GONE", "team": None, "email": None},
        "domain": "product",                 # agrees with the prefix, deliberately
        "dataClassification": "internal",
        "piiFields": [],
        "lifecycle": "active",
    }
    result = await validate(
        content=json.dumps(body),
        properties={},
        cmdb=_CMDB,
        artifact_id="product.product.mapping",
        group_id="product",
        schema_type="JSON",
    )
    assert not result.passed
    assert any(f.rule == "owner_unresolved" for f in result.failures)
    # The domain agrees, so this must not be reported as a domain problem.
    assert not any(f.rule == "domain_mismatch" for f in result.failures)


@pytest.mark.asyncio
async def test_mapping_with_a_stamp_but_no_owner_service_is_caught() -> None:
    body = json.loads(_GOOD_MAPPING)
    body["metadata"] = {
        "owner": {},
        "domain": "product",
        "dataClassification": "internal",
        "piiFields": [],
        "lifecycle": "active",
    }
    result = await validate(
        content=json.dumps(body),
        properties={},
        cmdb=_CMDB,
        artifact_id="product.product.mapping",
        group_id="product",
        schema_type="JSON",
    )
    assert any(f.rule == "owner_missing" for f in result.failures)


@pytest.mark.asyncio
async def test_mapping_owner_rule_matches_the_metadata_path_exactly() -> None:
    """One rule, two shapes — asserted equal rather than assumed.

    `_check_owner` exists so the mapping path and the metadata path cannot
    drift into disagreeing about the same question. If someone re-inlines
    either copy, the rule ids or messages diverge and this fails.
    """
    stamp = {
        "owner": {"service": "CI0000000-GONE", "team": None, "email": None},
        "domain": "product",
        "dataClassification": "internal",
        "piiFields": [],
        "lifecycle": "active",
    }
    mapping = json.loads(_GOOD_MAPPING)
    mapping["metadata"] = json.loads(json.dumps(stamp))
    schema = json.loads(_GOOD_JSON_SCHEMA)
    schema["metadata"] = json.loads(json.dumps(stamp))

    out = []
    for body, artifact in ((mapping, "product.product.mapping"),
                           (schema, "product.product.gold")):
        r = await validate(
            content=json.dumps(body), properties={}, cmdb=_CMDB,
            artifact_id=artifact, group_id="product", schema_type="JSON",
        )
        out.append([(f.rule, f.message) for f in r.failures
                    if f.rule.startswith("owner_")])
    assert out[0] == out[1], f"the two paths disagree: {out}"
    assert out[0], "neither path reported the unresolved owner"


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
