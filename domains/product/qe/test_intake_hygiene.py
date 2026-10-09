"""Intake hygiene — the review gate for free-text in product.mapping.json.

The intake is a COMPILER CONTRACT: conditions, scopes, joins, and transforms
must be structured data the compiler renders deterministically. Prose crept
in twice (lookup-join sentences, '10002 (Digital) — UK orgs (01B)'
qualifiers) and had to be dug out by hand. These tests make the third time a
CI failure instead:

- branch conditions carry a value and at most one (label) — no qualifiers;
- every Proliferate scope references a NAMED rowGroups entry (single source
  of truth — no inline predicates or free-text group labels in the
  registered mapping);
- complement groups reference an existing predicate group;
- transforms are STRUCTURED (UIFPE-105): prose strings are forbidden; every
  transform is a {"rule": ...} reference to the mapping's transforms
  registry (or an inline def); the sqlExpr escape hatch must reference {x}
  and is frozen to a reviewed allowlist — extending it here is the review;
- mapping↔SQL predicate parity (S5 of the SQL-reproduction review): every
  items_ext CTE predicate beyond the attribute-group/discriminator shape
  must be DECLARED in the intake — four SQL-only IS NOT NULL filters
  drifted in unseen because the old parity gate stopped at mapping↔master.
"""
import json
import re
from pathlib import Path

import pytest

MAPPING_PATH = (
    Path(__file__).resolve().parents[1] / "src" / "schemas" / "mapping"
    / "product" / "v1" / "product.mapping.json"
)
SQL_PATH = (
    Path(__file__).resolve().parents[1] / "src" / "schemas" / "mapping"
    / "product" / "v1" / "sql" / "product.sql"
)

# value | value,value | either + one trailing "(label)" | ELSE — nothing else.
CANONICAL_CONDITION = re.compile(r"^(ELSE|[\w.,\s-]+?(\s*\([^()]*\))?)$")

# Every sqlExpr escape hatch in the mapping, reviewed by engineering. Adding
# one? Verify the fragment, then extend this list in the same PR — that is
# the review. (Structured steps/cases need no entry here; the compiler
# renders them deterministically.)
REVIEWED_SQL_EXPRS: set = {
    # marketingCopy.contentUrl (UIFPE-253): pointer to the content service;
    # {content_url_base} is a per-env config sql_var, null contentId -> null
    "CASE WHEN {x} IS NOT NULL THEN "
    "concat('{content_url_base}/marketing-copy/', {x}) END",
}


@pytest.fixture(scope="module")
def mapping():
    return json.loads(MAPPING_PATH.read_text(encoding="utf-8"))


def _branches(mapping):
    for col in mapping["columns"]:
        if col.get("kind") == "conditional":
            for branch in col["branches"]:
                yield col["goldColumnName"], branch


def test_branch_conditions_are_canonical(mapping):
    bad = [
        f"{name}: {branch['condition']!r}"
        for name, branch in _branches(mapping)
        if not CANONICAL_CONDITION.match(branch["condition"])
    ]
    assert not bad, f"prose crept into branch conditions: {bad}"


def test_join_grain_is_retired(mapping):
    # UIFPE-104: attachment is explicit sourceRef.join; proliferation is
    # branch scope + rowGroups. joinGrain must not reappear.
    assert "joinGrain" not in json.dumps(mapping), \
        "joinGrain is retired (UIFPE-104) — declare the join explicitly"


def test_non_driving_sources_declare_their_join(mapping):
    driving = (mapping.get("drivingTable") or "").split(".")[-1].upper()
    bad = []

    def check(owner, src):
        table = (src.get("table") or "").split(".")[-1].upper()
        if table != driving and not (src.get("join") or {}).get("on"):
            bad.append(f"{owner}: {table} attaches with no join")

    for col in mapping["columns"]:
        if col.get("kind") == "scalar":
            check(col["goldColumnName"], col["source"])
        for branch in col.get("branches") or []:
            check(col["goldColumnName"], branch["source"])
    assert not bad, bad


def test_scopes_reference_named_row_groups(mapping):
    groups = mapping.get("rowGroups", {})
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


def test_complement_groups_reference_predicate_groups(mapping):
    groups = mapping.get("rowGroups", {})
    for name, group in groups.items():
        if "complementOf" in group:
            base = groups.get(group["complementOf"])
            assert base is not None, f"{name}: complement of unknown group {group['complementOf']!r}"
            assert "complementOf" not in base, f"{name}: complement of a complement"


def _transforms(mapping):
    """Every transform carried by a column, branch, or nested field."""
    for col in mapping["columns"]:
        if col.get("transform") is not None:
            yield col["goldColumnName"], col["transform"]
        for branch in col.get("branches") or []:
            if branch.get("transform") is not None:
                yield col["goldColumnName"], branch["transform"]
        for field in col.get("fields") or []:
            if field.get("transform") is not None:
                yield f"{col['goldColumnName']}.{field['name']}", field["transform"]


def test_transforms_are_structured_never_prose(mapping):
    # UIFPE-105: a transform is null, a {"rule": ...} reference, or an inline
    # structured def. Free-text strings do not compile and must not reappear.
    prose = [f"{name}: {t!r}" for name, t in _transforms(mapping)
             if isinstance(t, str)]
    assert not prose, f"prose transforms are retired (UIFPE-105) — structure them: {prose}"


def test_transform_rules_are_defined(mapping):
    registry = mapping.get("transforms", {})
    bad = [f"{name}: rule {t['rule']!r} not in transforms registry"
           for name, t in _transforms(mapping)
           if isinstance(t, dict) and "rule" in t and t["rule"] not in registry]
    assert not bad, bad


def test_transform_defs_are_complete(mapping):
    # Registry entries and inline defs alike must carry something to compile.
    defs = list(mapping.get("transforms", {}).items()) + [
        (name, t) for name, t in _transforms(mapping)
        if isinstance(t, dict) and "rule" not in t
    ]
    bad = [f"{name}: transform has no steps, cases, or sqlExpr"
           for name, d in defs
           if not (d.get("steps") or d.get("cases") or d.get("sqlExpr"))]
    assert not bad, bad


def _declared_filters(mapping):
    """Every filter string the intake declares, anywhere a source can sit."""
    out = set()
    for col in mapping["columns"]:
        if col.get("filter"):
            out.add(col["filter"])
        for holder in ([col.get("source")] +
                       [b.get("source") for b in col.get("branches") or []] +
                       [f.get("source") for f in col.get("fields") or []]):
            if holder and holder.get("filter"):
                out.add(holder["filter"])
    return out


# The discriminator/group shape every EAV CTE legitimately carries without a
# per-column declaration: the attribute-group pivot filter and the Digital
# Catalog discriminator the conditional branches encode structurally.
EAV_STRUCTURAL_PREDICATE = re.compile(
    r"^(ATTR_GROUP_ID = \d+|ITEM_CATALOG_GROUP_ID (=|<>) \d+)$")


def test_eav_cte_predicates_are_declared_in_the_intake(mapping):
    """S5 (review 2026-08-10): four IS NOT NULL filters existed only in the
    SQL — regenerating from intake would have produced different DATA and
    different channel hashes, and the wizard showed business users the
    predicate-free version. The mapping owner removed them (D1/D3); this
    gate makes the drift class a CI failure instead of an archaeology find:
    any items_ext predicate beyond the structural shape must appear verbatim
    in a declared intake filter."""
    sql = SQL_PATH.read_text(encoding="utf-8")
    declared = _declared_filters(mapping)
    bad = []
    for m in re.finditer(r"FROM items_ext\s+WHERE\s+(.+?)\s+GROUP BY",
                         sql, re.S):
        for pred in re.split(r"\s+AND\s+", m.group(1)):
            pred = " ".join(pred.split())
            if EAV_STRUCTURAL_PREDICATE.match(pred):
                continue
            if not any(pred in f for f in declared):
                bad.append(pred)
    assert not bad, (
        "SQL-only predicates — declare them in the intake (source filter) "
        f"or remove them from product.sql: {sorted(set(bad))}"
    )


def test_sql_expr_escape_hatch_is_reviewed(mapping):
    # Custom SQL fragments are the one place free-form logic survives — each
    # must reference {x} and be signed off by adding it to the allowlist.
    exprs = {d["sqlExpr"] for _, d in list(mapping.get("transforms", {}).items())
             + [(n, t) for n, t in _transforms(mapping)
                if isinstance(t, dict) and "rule" not in t]
             if isinstance(d, dict) and d.get("sqlExpr")}
    missing_x = [e for e in exprs if "{x}" not in e]
    assert not missing_x, f"sqlExpr must reference the source value as {{x}}: {missing_x}"
    unreviewed = exprs - REVIEWED_SQL_EXPRS
    assert not unreviewed, (
        "custom SQL transforms need engineering review — verify the fragment, "
        f"then add it to REVIEWED_SQL_EXPRS: {sorted(unreviewed)}"
    )
