"""Intake hygiene for program.mapping.json — the contract gates.

Nothing validated this file when it was generated: every product qe test
anchors to its own domain root, so a new domain's mapping is unguarded until
it copies these gates alongside it (as stacks/apicurio/standards/gold-attribute-mapping.md
instructs). This module is that copy, deliberately SELF-CONTAINED — no imports
from another domain's tree, which is the cross-domain reach-in UIFPE-122 exists
to settle.

The intake is a COMPILER CONTRACT. What these gates enforce, and why each one
earned its place on the product entity first:

- the file validates against the shared schema (the only mechanical truth);
- `joinGrain` stays retired (UIFPE-104) — attachment is explicit ON pairs;
- every non-driving source declares its join, EAV sources included;
- transforms are STRUCTURED (UIFPE-105): prose strings do not compile, named
  rules must resolve, and the sqlExpr escape hatch is frozen to a reviewed
  allowlist;
- the entity key is declared and drawn from the driving table (UIFPE-107);
- exactly one sortKey per array (not expressible in JSON Schema);
- branch conditions carry no prose qualifiers, and Proliferate scopes
  reference a NAMED rowGroups entry.

The last gate has nothing to bite on today (program declares no conditional
columns), and that is deliberate: it is here so the first conditional column
added to program cannot introduce the prose that had to be dug out of product
by hand twice.
"""
import json
import re
from pathlib import Path

import pytest

DOMAIN = Path(__file__).resolve().parents[1]
REPO = DOMAIN.parents[1]
MAPPING_PATH = (DOMAIN / "src" / "schemas" / "mapping" / "program" / "v1"
                / "program.mapping.json")
SCHEMA_PATH = (REPO / "stacks" / "apicurio" / "standards"
               / "gold-attribute-mapping.schema.json")

# value | value,value | either + one trailing "(label)" | ELSE — nothing else.
CANONICAL_CONDITION = re.compile(r"^(ELSE|[\w.,\s-]+?(\s*\([^()]*\))?)$")

# Custom SQL fragments reviewed by engineering. Adding one? Verify the
# fragment, then extend this list in the same PR — that is the review.
REVIEWED_SQL_EXPRS: set = set()

# The one driving table every non-driving source must attach to.
DRIVING = "MTL_SYSTEM_ITEMS_B"


@pytest.fixture(scope="module")
def mapping():
    return json.loads(MAPPING_PATH.read_text(encoding="utf-8"))


def _sources(mapping):
    """(owner, sourceRef) for every place a source can sit."""
    for col in mapping["columns"]:
        if col.get("kind") == "scalar":
            yield col["goldColumnName"], col["source"]
        for branch in col.get("branches") or []:
            yield col["goldColumnName"], branch["source"]
        for field in col.get("fields") or []:
            yield f"{col['goldColumnName']}.{field['name']}", field["source"]


def _transforms(mapping):
    """(owner, transformRef) for every transform carried anywhere."""
    for col in mapping["columns"]:
        if col.get("transform") is not None:
            yield col["goldColumnName"], col["transform"]
        for branch in col.get("branches") or []:
            if branch.get("transform") is not None:
                yield col["goldColumnName"], branch["transform"]
        for field in col.get("fields") or []:
            if field.get("transform") is not None:
                yield f"{col['goldColumnName']}.{field['name']}", field["transform"]


def _branches(mapping):
    for col in mapping["columns"]:
        if col.get("kind") == "conditional":
            for branch in col["branches"]:
                yield col["goldColumnName"], branch


def test_validates_against_the_shared_schema(mapping):
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(schema).validate(mapping)


def test_targets_the_expected_entity(mapping):
    assert mapping["domain"] == "program"
    assert mapping["table"] == "gold_program.program"
    assert mapping["drivingTable"].split(".")[-1].upper() == DRIVING


def test_join_grain_is_retired(mapping):
    # UIFPE-104: attachment is explicit sourceRef.join; proliferation is
    # branch scope + rowGroups. joinGrain must not reappear.
    assert "joinGrain" not in json.dumps(mapping), \
        "joinGrain is retired (UIFPE-104) — declare the join explicitly"


def test_non_driving_sources_declare_their_join(mapping):
    bad = []
    for owner, src in _sources(mapping):
        table = (src.get("table") or "").split(".")[-1].upper()
        if table != DRIVING and not (src.get("join") or {}).get("on"):
            bad.append(f"{owner}: {table} attaches with no join")
    assert not bad, bad


def test_declared_joins_are_complete(mapping):
    bad = []
    for owner, src in _sources(mapping):
        for i, pair in enumerate((src.get("join") or {}).get("on") or [], 1):
            if not (pair.get("leftTable") and pair.get("leftColumn")
                    and pair.get("rightColumn")):
                bad.append(f"{owner}: ON pair {i} is incomplete")
    assert not bad, bad


def test_transforms_are_structured_never_prose(mapping):
    # UIFPE-105: a transform is null, a {"rule": ...} reference, or an inline
    # structured def. Free text does not compile.
    prose = [f"{owner}: {t!r}" for owner, t in _transforms(mapping)
             if isinstance(t, str)]
    assert not prose, f"prose transforms are retired (UIFPE-105): {prose}"


def test_transform_rules_resolve(mapping):
    registry = mapping.get("transforms") or {}
    bad = [f"{owner}: rule {t['rule']!r} not in the transforms registry"
           for owner, t in _transforms(mapping)
           if isinstance(t, dict) and "rule" in t and t["rule"] not in registry]
    assert not bad, bad


def test_transform_defs_are_complete(mapping):
    defs = list((mapping.get("transforms") or {}).items()) + [
        (owner, t) for owner, t in _transforms(mapping)
        if isinstance(t, dict) and "rule" not in t]
    bad = [f"{name}: no steps, cases or sqlExpr" for name, d in defs
           if not (d.get("steps") or d.get("cases") or d.get("sqlExpr"))]
    assert not bad, bad


def test_sql_expr_escape_hatch_is_reviewed(mapping):
    exprs = {d["sqlExpr"] for _, d in
             list((mapping.get("transforms") or {}).items())
             + [(o, t) for o, t in _transforms(mapping)
                if isinstance(t, dict) and "rule" not in t]
             if isinstance(d, dict) and d.get("sqlExpr")}
    missing_x = [e for e in exprs if "{x}" not in e]
    assert not missing_x, \
        f"sqlExpr must reference the source value as {{x}}: {missing_x}"
    unreviewed = exprs - REVIEWED_SQL_EXPRS
    assert not unreviewed, (
        "custom SQL transforms need engineering review — verify the fragment, "
        f"then add it to REVIEWED_SQL_EXPRS: {sorted(unreviewed)}")


def test_entity_key_is_declared_and_from_the_driving_table(mapping):
    # UIFPE-107. The runtime maps each source's changed rows to gold PK values
    # via key_mapping, so a key column outside the driving table has no
    # expressible mapping — a grain spanning two tables is a grain-defining
    # join, an architecture case, not an intake input.
    key = mapping.get("entityKey") or []
    assert key, "entityKey must be declared"
    names = [p["goldColumn"] for p in key]
    assert names == ["inventoryItemId", "organizationId"], names
    for pair in key:
        assert pair["drivingColumn"], f"{pair['goldColumn']}: no drivingColumn"
        # every key column must also exist as an authored column
        assert any(c["goldColumnName"] == pair["goldColumn"]
                   for c in mapping["columns"]), \
            f"{pair['goldColumn']} is an entity key with no column entry"


def test_exactly_one_sort_key_per_array(mapping):
    # ADR-0003: ARRAY properties need a stable order or channel hashes churn.
    # Not expressible in JSON Schema, so it is asserted here.
    bad = []
    for col in mapping["columns"]:
        if col.get("kind") == "nested":
            n = sum(1 for f in col["fields"] if f["sortKey"])
            if n != 1:
                bad.append(f"{col['goldColumnName']}: {n} sort keys (expected 1)")
    assert not bad, bad


def test_gold_column_names_are_camel_case(mapping):
    pattern = re.compile(r"^[a-z][a-zA-Z0-9]*(\.[a-z][a-zA-Z0-9]*)?$")
    bad = [c["goldColumnName"] for c in mapping["columns"]
           if not pattern.match(c["goldColumnName"])]
    assert not bad, f"not camelCase: {bad}"


def test_no_duplicate_column_names(mapping):
    names = [c["goldColumnName"] for c in mapping["columns"]]
    dupes = {n for n in names if names.count(n) > 1}
    assert not dupes, f"duplicate gold column names: {sorted(dupes)}"


def test_branch_conditions_are_canonical(mapping):
    # No conditional columns today — the gate stands so the first one added
    # cannot bring back the prose qualifiers product had to be cleaned of.
    bad = [f"{name}: {branch['condition']!r}"
           for name, branch in _branches(mapping)
           if not CANONICAL_CONDITION.match(branch["condition"])]
    assert not bad, f"prose crept into branch conditions: {bad}"


def test_scopes_reference_named_row_groups(mapping):
    groups = mapping.get("rowGroups") or {}
    bad = []
    for name, branch in _branches(mapping):
        scope = branch.get("scope")
        if not scope:
            continue
        if not scope.get("group"):
            bad.append(f"{name}: scope is inline — register it in rowGroups")
        elif scope["group"] not in groups:
            bad.append(f"{name}: unknown row group {scope['group']!r}")
    assert not bad, bad
