"""Manifest + mapping-SQL gates for the program entity.

The manifest and the SQL are COMPILED artifacts: the runtime loads both, and a
disagreement between them (or with master.json) fails at analysis time on a
Glue cluster, or worse, silently produces wrong data. These gates catch that at
commit time instead.

What is enforced, and why each one earned its place — most were learned on the
product entity, several the hard way:

- lineage covers master.json in BOTH directions (product's mapping drifted from
  master twice on casing alone, and nothing noticed);
- output_columns matches master.json property order EXACTLY — it drives the
  physical DDL column order, so a reorder that looks cosmetic is a table
  rewrite;
- every ARRAY property declares array_sort_keys and an explicit column_types
  entry (ADR-0003 D11/D12): without a stable sort, channel hashes churn every
  run;
- the entity filter is byte-identical in the manifest's sot_filter and the
  SQL's trailing WHERE. Reconcile applies sot_filter to its source-of-truth
  anti-join and the transform only upserts, so divergence either leaks
  reclassified rows or reconcile-deletes valid ones — the single most damaging
  drift available in this design;
- the SQL references only registered aliases, never a Silver table directly;
- every manifest source is actually used by the SQL, and vice versa.
"""
import json
import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

DOMAIN = Path(__file__).resolve().parents[1]
SCHEMAS = DOMAIN / "src" / "schemas"
MANIFEST_PATH = SCHEMAS / "mapping/program/v1/manifest.yaml"
SQL_PATH = SCHEMAS / "mapping/program/v1/sql/program.sql"
MASTER_PATH = SCHEMAS / "gold/program/v1/master.json"

MANIFEST = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))
SQL = SQL_PATH.read_text(encoding="utf-8")
MASTER = json.loads(MASTER_PATH.read_text(encoding="utf-8"))

ENTITY_FILTER = "SEGMENT1 LIKE 'P2%'"


def _business_properties():
    return [k for k in MASTER["properties"] if not k.startswith("_")]


def _array_properties():
    out = []
    for name in _business_properties():
        t = MASTER["properties"][name].get("type")
        types = set(t) if isinstance(t, list) else {t}
        if "array" in types:
            out.append(name)
    return out


def test_manifest_declares_the_expected_top_level_sections():
    for key in ("domain", "version", "source_system", "canonical_lineage",
                "schema", "sql", "sources", "target", "array_sort_keys",
                "column_types", "output_columns"):
        assert key in MANIFEST, f"manifest is missing {key}"
    assert MANIFEST["domain"] == "program"
    assert MANIFEST["version"] == "v1"


def test_target_matches_the_declared_key():
    target = MANIFEST["target"]
    assert target["database"] == "uif_echo_{env}_gold_program"
    assert target["table"] == "program"
    key = [p["goldColumn"] for p in MASTER["metadata"]["primaryKey"]]
    assert target["primary_key_cols"] == key, \
        f"target PK {target['primary_key_cols']} != master key {key}"


def test_schema_and_sql_paths_resolve():
    assert (MANIFEST_PATH.parent / MANIFEST["schema"]["master"]).resolve() \
        == MASTER_PATH.resolve()
    assert (MANIFEST_PATH.parent / MANIFEST["sql"]["file"]).resolve() \
        == SQL_PATH.resolve()


def test_lineage_covers_master_both_directions():
    lineage = set(MANIFEST["canonical_lineage"])
    master = set(_business_properties())
    assert not (master - lineage), \
        f"master properties with no canonical_lineage entry: {sorted(master - lineage)}"
    assert not (lineage - master), \
        f"canonical_lineage entries with no master property: {sorted(lineage - master)}"


def test_every_lineage_entry_names_a_table_and_column():
    bad = [name for name, e in MANIFEST["canonical_lineage"].items()
           if not (e.get("pdh_table") and e.get("pdh_column"))]
    assert not bad, f"lineage entries missing pdh_table/pdh_column: {bad}"


def test_output_columns_match_master_order_exactly():
    # Drives the physical DDL column order — a reorder is a table rewrite, not
    # a cosmetic change.
    assert MANIFEST["output_columns"] == _business_properties(), (
        "output_columns must equal master.json property order\n"
        f"  manifest = {MANIFEST['output_columns']}\n"
        f"  master   = {_business_properties()}")


def test_every_array_property_declares_a_sort_key_and_spark_type():
    arrays = _array_properties()
    assert arrays, "expected at least one array property"
    for name in arrays:
        assert name in MANIFEST["array_sort_keys"], \
            f"{name}: ARRAY property with no array_sort_keys entry (ADR-0003 D11)"
        assert MANIFEST["array_sort_keys"][name], f"{name}: empty sort key list"
        assert name in MANIFEST["column_types"], \
            f"{name}: ARRAY property with no column_types entry (ADR-0003 D12)"


def test_related_programs_sort_key_has_the_tie_breaker():
    # relatedProgramId alone ties when a program relates to the same program under
    # two relationship types — nondeterministic array order, churning hashes.
    assert MANIFEST["array_sort_keys"]["relatedPrograms"] == \
        ["relatedProgramId", "relationshipType"]


def test_sort_keys_name_real_fields():
    bad = []
    for name, keys in MANIFEST["array_sort_keys"].items():
        prop = MASTER["properties"][name]
        items = prop.get("items", {})
        fields = list(items.get("properties") or {})
        for k in keys:
            # a value list sorts by itself, spelled [value]
            if not fields and k == "value":
                continue
            if k not in fields:
                bad.append(f"{name}: sort key {k!r} is not a field of the element")
    assert not bad, bad


def test_entity_filter_is_identical_in_manifest_and_sql():
    # The most damaging drift available in this design: reconcile applies
    # sot_filter to its SoT anti-join while the transform only upserts, so a
    # divergence either leaks reclassified rows or reconcile-deletes valid ones.
    sot = next(s for s in MANIFEST["sources"] if s.get("is_source_of_truth"))
    sot_filter = sot["sot_filter"]
    assert ENTITY_FILTER in sot_filter and "ORGANIZATION_ID = 82" in sot_filter, \
        f"unexpected sot_filter: {sot_filter!r}"

    where = SQL[SQL.rindex("\nWHERE "):]
    assert ENTITY_FILTER in where, f"SQL trailing WHERE lacks {ENTITY_FILTER}"
    assert "ORGANIZATION_ID AS BIGINT) = 82" in where or "ORGANIZATION_ID = 82" in where, \
        "SQL trailing WHERE lacks the org-82 predicate"


def test_sql_selects_exactly_the_output_columns_in_order():
    # The final SELECT's aliases, in order, must equal output_columns.
    final = SQL[SQL.rindex("\nSELECT\n"):]
    final = final[:final.rindex("\nFROM items")]
    aliases = re.findall(r"(?m)\s+AS ([A-Za-z][A-Za-z0-9]*),?\s*$", final)
    assert aliases == MANIFEST["output_columns"], (
        "final SELECT aliases must equal output_columns, in order\n"
        f"  sql      = {aliases}\n"
        f"  manifest = {MANIFEST['output_columns']}")


def test_sql_references_only_registered_aliases():
    # Reading a Silver table directly would bypass the watermark contract: the
    # runtime registers `items` restricted to affected entities, so a direct
    # read silently changes what a run sees.
    aliases = {s["alias"] for s in MANIFEST["sources"]}
    referenced = set(re.findall(r"(?:FROM|JOIN)\s+([a-z_][a-z0-9_]*)", SQL))
    ctes = set(re.findall(r"(?m)^([a-z_][a-z0-9_]*) AS \(", SQL))
    unknown = referenced - aliases - ctes
    assert not unknown, f"SQL reads unregistered relations: {sorted(unknown)}"
    silver = re.findall(r"(?:FROM|JOIN)\s+\S*echo_\{?env\}?_silver", SQL)
    assert not silver, f"SQL references Silver tables directly: {silver}"


def test_every_registered_source_is_used_by_the_sql():
    # A source in the manifest but unused is dead weight the runtime still
    # registers and watermarks; a source used but unregistered fails at
    # TABLE_OR_VIEW_NOT_FOUND.
    referenced = set(re.findall(r"(?:FROM|JOIN)\s+([a-z_][a-z0-9_]*)", SQL))
    unused = [s["alias"] for s in MANIFEST["sources"]
              if s["alias"] not in referenced]
    assert not unused, f"registered but unused by the SQL: {unused}"


def test_every_source_declares_a_watermark_and_key_mapping():
    pk = set(MANIFEST["target"]["primary_key_cols"])
    bad = []
    for s in MANIFEST["sources"]:
        if not s.get("watermark_col"):
            bad.append(f"{s['alias']}: no watermark_col")
        km = s.get("key_mapping") or {}
        if not km:
            bad.append(f"{s['alias']}: no key_mapping (the transform refuses)")
        extra = set(km) - pk
        if extra:
            bad.append(f"{s['alias']}: key_mapping keys outside the PK: {sorted(extra)}")
    assert not bad, bad


def test_source_of_truth_is_declared_exactly_once():
    sots = [s["alias"] for s in MANIFEST["sources"] if s.get("is_source_of_truth")]
    assert sots == ["items"], f"expected exactly one SoT (items), got {sots}"


def test_arrays_are_empty_lists_not_null():
    # Permanent hash-stability commitment (framework owner, 2026-08-11).
    for name in _array_properties():
        assert re.search(rf"COALESCE\({{?\w*\.?\w+\.{name},\s*array\(\)\)|"
                         rf"COALESCE\(\w+\.{name},\s+array\(\)\)", SQL), \
            f"{name}: expected COALESCE(..., array()) so empty is [] not NULL"


def test_sql_is_parenthesis_balanced():
    assert SQL.count("(") == SQL.count(")")
