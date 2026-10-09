"""Parity between program's two authored artifacts.

master.json is the PRIMARY artifact — what the gold table IS (attributes,
types, consumer tiers). program.mapping.json is HOW each attribute is
populated. They are authored separately, so they can drift, and on the product
entity they did: 'organizationid' vs 'organizationId' casing, and
'dimentionUomCode' vs 'dimensionUomCode'. Nothing caught either automatically.

These gates make that drift a hard failure, in both directions and on every
axis the two files share:

  coverage   every mapping column is a master property, and every master
             property has a mapping column
  ORDER      the two agree on order — master.json property order drives the
             physical DDL column order via the manifest's output_columns, so a
             reordering that looks cosmetic is a table rewrite
  types      dataType maps to the declared JSON type, and nullable agrees with
             the type's null union
  channels   the mapping's channels equal master's x-channels, and the
             concentric tier invariant holds (event subset-of message subset-of api)
  key        master.json's metadata.primaryKey agrees with the mapping's
             entityKey — program is the first entity to declare its key on the
             primary artifact, so this is the gate that keeps that honest

Self-contained by design: no imports from another domain's tree.
"""
import json
from pathlib import Path

import pytest

DOMAIN = Path(__file__).resolve().parents[1]
SCHEMAS = DOMAIN / "src" / "schemas"
MASTER = json.loads(
    (SCHEMAS / "gold/program/v1/master.json").read_text(encoding="utf-8"))
MAPPING = json.loads(
    (SCHEMAS / "mapping/program/v1/program.mapping.json").read_text(encoding="utf-8"))

# intake dataType -> the JSON Schema type master.json must declare
JSON_TYPE = {"string": "string", "integer": "integer", "number": "number",
             "float": "number", "boolean": "boolean", "date": "string",
             "timestamp": "string"}
TIERS = ("event", "message", "api")


def _types(fragment: dict) -> set:
    t = fragment.get("type")
    if t is None:
        return set()
    return set(t) if isinstance(t, list) else {t}


def _business_properties() -> list:
    """master.json properties excluding framework metadata (_-prefixed)."""
    return [k for k in MASTER["properties"] if not k.startswith("_")]


def test_master_is_a_valid_draft07_schema():
    jsonschema = pytest.importorskip("jsonschema")
    jsonschema.Draft7Validator.check_schema(MASTER)


def test_coverage_and_order_match_exactly():
    mapped = [c["goldColumnName"] for c in MAPPING["columns"]]
    declared = _business_properties()
    # One assertion, both directions, plus order — a set comparison would pass
    # on a reordering that silently rewrites the physical table.
    assert mapped == declared, (
        "mapping columns and master properties must match in content AND "
        f"order\n  mapping = {mapped}\n  master  = {declared}")


def test_types_and_nullability_agree():
    bad = []
    for col in MAPPING["columns"]:
        name = col["goldColumnName"]
        prop = MASTER["properties"][name]
        types = _types(prop)
        if col["kind"] == "nested":
            if "array" not in types:
                bad.append(f"{name}: nested in mapping, {sorted(types)} in master")
                continue
            fields = col["fields"]
            if len(fields) == 1:
                # value-array encoding: mapping declares one field, master
                # encodes ARRAY<scalar> (product's webConsumingSystem shape)
                want = JSON_TYPE[fields[0]["type"]]
                got = prop.get("items", {}).get("type")
                if got != want:
                    bad.append(f"{name}: value-array item {got!r} != {want!r}")
            else:
                got = list(prop.get("items", {}).get("properties") or {})
                want = [f["name"] for f in fields]
                if got != want:
                    bad.append(f"{name}: struct fields {got} != {want}")
        else:
            want = JSON_TYPE[col["dataType"]]
            if want not in types:
                bad.append(f"{name}: {want!r} not in master {sorted(types)}")
            if col["nullable"] and "null" not in types:
                bad.append(f"{name}: nullable, but master forbids null")
            if not col["nullable"] and "null" in types:
                bad.append(f"{name}: non-nullable, but master allows null")
    assert not bad, bad


def test_channels_agree_between_the_artifacts():
    bad = []
    for col in MAPPING["columns"]:
        name = col["goldColumnName"]
        want = [t for t in TIERS if col["channels"][t]]
        got = MASTER["properties"][name].get("x-channels")
        if want != got:
            bad.append(f"{name}: channels {want} != x-channels {got}")
    assert not bad, bad


def test_concentric_tier_invariant_holds():
    # ADR-0003 D10 still enforces event subset-of message subset-of api;
    # generate_tier_subsets.py asserts the same when it builds the subsets.
    bad = []
    for col in MAPPING["columns"]:
        ch = col["channels"]
        if ch["event"] and not (ch["message"] and ch["api"]):
            bad.append(f"{col['goldColumnName']}: event without message+api")
        if ch["message"] and not ch["api"]:
            bad.append(f"{col['goldColumnName']}: message without api")
    assert not bad, bad


def test_primary_key_agrees_across_both_artifacts():
    # UIFPE-107: program is the first entity to declare its key on master.json
    # (metadata.primaryKey), the slot masterSchema.ts primaryKeyOf() prefers.
    declared = MASTER["metadata"].get("primaryKey") or []
    intake = MAPPING.get("entityKey") or []
    assert declared, "master.json must declare metadata.primaryKey"
    assert [p["goldColumn"] for p in declared] == [p["goldColumn"] for p in intake]
    assert [p["drivingColumn"] for p in declared] == [p["drivingColumn"] for p in intake]


def test_required_is_a_subset_of_properties_and_covers_the_key():
    missing = [r for r in MASTER["required"] if r not in MASTER["properties"]]
    assert not missing, f"required names absent from properties: {missing}"
    key = [p["goldColumn"] for p in MASTER["metadata"]["primaryKey"]]
    absent = [k for k in key if k not in MASTER["required"]]
    assert not absent, f"key columns must be required: {absent}"


def test_key_columns_carry_no_consumer_tiers():
    # Matching product: PK columns are stored on every row but carried by no
    # feed, so x-channels is []. If that changes it is a contract decision,
    # not an edit.
    for pair in MASTER["metadata"]["primaryKey"]:
        prop = MASTER["properties"][pair["goldColumn"]]
        assert prop.get("x-channels") == [], \
            f"{pair['goldColumn']}: expected x-channels [], got {prop.get('x-channels')}"


def test_every_property_carries_a_business_description():
    bad = [k for k in _business_properties()
           if not (MASTER["properties"][k].get("description") or "").strip()]
    assert not bad, f"properties with no description: {bad}"


def test_master_descriptions_are_the_business_description():
    # Framework-owner convention (2026-08-17, tightened same day): master.json
    # is THE master schema and its description field carries the ACTUAL
    # business description — the intake's businessDescription VERBATIM, no
    # technical lineage appended (that lives in the mapping/manifest). Dotted
    # columns resolve to their struct field.
    bad = []
    for col in MAPPING["columns"]:
        name = col["goldColumnName"]
        bd = (col.get("businessDescription") or "").strip()
        if not bd:
            continue
        if "." in name:
            parent, field = name.split(".", 1)
            desc = MASTER["properties"][parent]["properties"][field].get("description") or ""
        else:
            desc = MASTER["properties"][name].get("description") or ""
        if desc != bd:
            bad.append(f"{name}: master description must equal the business "
                       f"description {bd[:50]!r}, got {desc[:50]!r}")
    assert not bad, bad


def test_nested_fields_carry_explicit_channels_and_business_descriptions():
    # Framework-owner convention (2026-08-17): master is self-describing —
    # every nested/struct field carries EXPLICIT x-channels (a subset of the
    # parent) and a business description; nothing relies on the untagged-
    # inherits-parent rule.
    bad = []
    for pname, prop in MASTER["properties"].items():
        t = prop.get("type")
        types = t if isinstance(t, list) else [t]
        if "array" in types and isinstance(prop.get("items"), dict)                 and prop["items"].get("properties"):
            container = prop["items"]["properties"]
        elif "object" in types and prop.get("properties"):
            container = prop["properties"]
        else:
            continue
        parent = set(prop.get("x-channels") or [])
        for fname, fprop in container.items():
            tags = fprop.get("x-channels")
            if tags is None:
                bad.append(f"{pname}.{fname}: no explicit x-channels")
            elif not set(tags) <= parent:
                bad.append(f"{pname}.{fname}: x-channels {tags} exceed the "
                           f"parent's {sorted(parent)}")
            if not (fprop.get("description") or "").strip():
                bad.append(f"{pname}.{fname}: no business description")
    assert not bad, bad
