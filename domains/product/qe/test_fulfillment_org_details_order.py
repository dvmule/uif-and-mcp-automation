# test_fulfillment_org_details_order.py — nested struct field ORDER parity.
#
# Iceberg's nested ADD COLUMN APPENDS and cannot position: the AFTER clause
# exists only on the top-level auto-DDL path. So a member declared mid-struct
# lands at the END of the live table anyway, and declared order silently stops
# matching physical order. Nothing catches that on its own —
# gold_cdc._diff_trees matches struct fields by NAME, so a reorder is neither
# an addition nor a refusal.
#
# It matters because nested field order is HASHED. Top-level order is
# normalised by sorting (channel_properties); nested order is not, and
# build_channel_hash_expr is sha2(to_json(named_struct(...))), which follows
# the struct's own field order. The incremental hashes the mapping-SQL
# DataFrame (declared order) while a targeted recompute composes the row
# from the STORED table (physical order) — so the two writers disagree on
# every row, forever, and product's recompute leg exercises that path.
#
# marketingCopy has carried this gate since UIFPE-253
# (test_content_url_field.py::TestContentUrlParity). fulfillmentOrgDetails did
# not, which is how UIFPE-354 first declared its two new members BEFORE the
# marketingInitiative array with 837 python + 294 vitest green. This module is
# that gate, for the struct that lacked it.

import json
import re
from pathlib import Path

import pytest
import yaml

DOMAIN = Path(__file__).resolve().parents[1]
BASE = DOMAIN / "src" / "schemas"
MASTER = json.loads((BASE / "gold/product/v1/master.json").read_text(encoding="utf-8"))
MANIFEST = yaml.safe_load((BASE / "mapping/product/v1/manifest.yaml").read_text(encoding="utf-8"))
INTAKE = json.loads((BASE / "mapping/product/v1/product.mapping.json").read_text(encoding="utf-8"))
SQL = (BASE / "mapping/product/v1/sql/product.sql").read_text(encoding="utf-8")

STRUCT = "fulfillmentOrgDetails"

# The shipped order. The array is NOT last: boFlag and defaultSoSourceType were
# added after it (UIFPE-354) precisely because a nested add appends.
EXPECTED = [
    "sellingRestrictionCode", "discountCode", "titleStatusCode",
    "regionalPublicationDate", "returnClassCode", "imprintName",
    "productTaxClassificationCode", "relevanceValue", "marketingInitiative",
    "backOrderIndicator", "defaultSalesOrderSourceTypeCode",
    "readyForManufactureCode",
]


def _master_members():
    return list(MASTER["properties"][STRUCT]["properties"].keys())


def _sql_struct_members():
    """The named_struct members of the final SELECT's fulfillmentOrgDetails."""
    m = re.search(rf"(?ms)ELSE named_struct\((.*?)\n  \) END\s+AS {STRUCT},", SQL)
    assert m, f"{STRUCT} named_struct not found in the final SELECT"
    return re.findall(r"^\s*'(\w+)',", m.group(1), re.M)


def _column_types_members():
    ct = " ".join(MANIFEST["column_types"][STRUCT].split())
    # strip the nested ARRAY<STRUCT<...>> payload so its members do not leak in
    inner = re.search(r"marketingInitiative: ARRAY<STRUCT<(.*?)>>", ct)
    assert inner, "marketingInitiative array type not found"
    ct = ct.replace(inner.group(1), "")
    return re.findall(r"(\w+):\s*(?:STRING|DATE|ARRAY|BOOLEAN)", ct)


def test_master_declares_the_expected_order():
    assert _master_members() == EXPECTED


def test_column_types_matches_master_order():
    assert _column_types_members() == _master_members()


def test_sql_named_struct_matches_master_order():
    assert _sql_struct_members() == _master_members()


def test_intake_declares_the_same_member_SET():
    # SET, not order: the intake's dotted columns have never been in master's
    # member order (returnClass is authored first, declared fifth) and nothing
    # depends on it — the compiler places a member from the struct, not from
    # the column list. What must hold is that no member is missing on either
    # side, and that the post-array additions are authored last.
    members = [c["goldColumnName"].split(".", 1)[1]
               for c in INTAKE["columns"]
               if c["goldColumnName"].startswith(f"{STRUCT}.")]
    assert set(members) == set(_master_members())
    assert members[-3:] == ["backOrderIndicator", "defaultSalesOrderSourceTypeCode",
                            "readyForManufactureCode"]


def test_event_data_schemas_match_master_order():
    # Set-equality is already covered elsewhere; ORDER is what this module is
    # for, and the published payload's key order follows the schema.
    for name in ("product-created", "product-updated"):
        data = json.loads((BASE / f"events/{name}-data.json").read_text(encoding="utf-8"))
        got = list(data["properties"][STRUCT]["properties"].keys())
        assert got == _master_members(), name


def _struct_expressions():
    """member -> the expression the named_struct emits for it."""
    m = re.search(rf"(?ms)ELSE named_struct\((.*?)\n  \) END\s+AS {STRUCT},", SQL)
    assert m, "named_struct not found"
    # strip line comments first: they sit between members and would otherwise
    # be swallowed into the preceding expression
    body = re.sub(r"--[^\n]*", "", m.group(1))
    pairs = re.split(r"^\s*'(\w+)',", body, flags=re.M)[1:]
    return {pairs[i]: " ".join(pairs[i + 1].split()).rstrip(",")
            for i in range(0, len(pairs), 2)}


def test_the_all_null_gate_covers_every_member():
    """The struct collapses to NULL only when EVERY member is null, so the gate
    must carry a conjunct for each — a member missing from it means a product
    carrying only that value publishes NULL instead of a struct.

    Compared by EXPRESSION, not by gold member name: the gate names the source
    expression (`items.DEFAULT_SO_SOURCE_TYPE`), never the member. That is also
    the invariant that matters — the gate must test what the struct emits.
    """
    head = SQL[:SQL.index(f") END                                        AS {STRUCT},")]
    gates = re.findall(r"(?ms)CASE WHEN (.*?)\n\s+THEN NULL\n\s+ELSE named_struct\(", head)
    assert gates, "all-null gate not found"
    gate = " ".join(gates[-1].split())
    # Compare COLUMN REFERENCES, not whole expressions. The gate deliberately
    # tests the null-bearing source (`ag1171.boFlag IS NULL`) while the struct
    # may wrap it — CAST, COALESCE, or a transform's CASE — and a transform of
    # NULL is NULL either way. Whole-expression matching would break on every
    # transform added later; a member with NO reference in the gate is the real
    # defect this catches.
    REF = re.compile(r"\b(?:ag\d+\w*|items|orgs|mkinit|ag\d+_org)\.\w+\b")
    missing = []
    for member, expr in _struct_expressions().items():
        refs = set(REF.findall(expr))
        if not refs:
            missing.append(f"{member}: no source column reference found in {expr}")
        elif not (refs & set(REF.findall(gate))):
            missing.append(f"{member}: none of {sorted(refs)} appears in the all-null gate")
    assert not missing, f"not covered by the all-null gate: {missing}"


@pytest.mark.parametrize("member", ["backOrderIndicator", "defaultSalesOrderSourceTypeCode",
                                    "readyForManufactureCode"])
def test_the_uifpe354_members_sit_after_the_array(member):
    members = _master_members()
    assert members.index(member) > members.index("marketingInitiative"), (
        f"{member} must sit AFTER marketingInitiative: a nested ADD COLUMN "
        "appends, so declaring it earlier makes declared and physical order "
        "diverge and every channel hash churn")
