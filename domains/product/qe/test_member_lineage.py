# test_member_lineage.py
# UIFPE-269 PR review: a struct MEMBER's lineage row is emitted as a YAML
# flow mapping, and a nested member's column list carries commas
# ("C_EXT_ATTR1 (wcsName), D_EXT_ATTR1 (endDate)"). Unquoted, the value split
# into bogus null-valued keys — the manifest still PARSED, the lint only
# checks when/pdh_table/pdh_column are truthy, and the published
# canonical_lineage silently lost two of three field lineages. There was no
# test over _member_lineage_row at all; this is it.

import yaml

import compile_intake_delta as cid

EXT = "EGO_MTL_SY_ITEMS_EXT_B"


def _row_to_obj(row: str) -> dict:
    """Parse one emitted `      - { ... }` row as the YAML it has to be."""
    return yaml.safe_load(row.strip()[2:])


def test_a_multi_column_member_row_parses_to_one_lineage_entry():
    src = {"table": EXT,
           "column": "C_EXT_ATTR1 (marketingInitiative), D_EXT_ATTR1 (startDate), "
                     "D_EXT_ATTR2 (endDate)",
           "filter": "ATTR_GROUP_ID = 1141"}
    obj = _row_to_obj(cid._member_lineage_row("marketingInitiative[]", src))
    assert set(obj) == {"when", "pdh_table", "pdh_column", "pdh_filter",
                        "pdh_grain"}, obj
    assert obj["pdh_column"] == src["column"]
    assert obj["pdh_table"] == EXT
    assert obj["pdh_filter"] == "ATTR_GROUP_ID = 1141"
    assert None not in obj.values()


def test_a_single_column_member_row_is_unchanged_in_meaning():
    src = {"table": EXT, "column": "C_EXT_ATTR8", "filter": "ATTR_GROUP_ID = 822"}
    obj = _row_to_obj(cid._member_lineage_row("returnClass (scalar — not conditional)", src))
    assert obj["pdh_column"] == "C_EXT_ATTR8"
    assert obj["when"].startswith("returnClass")


def test_a_double_quote_in_the_column_text_cannot_break_the_row():
    src = {"table": EXT, "column": 'C_EXT_ATTR1 ("odd" name)'}
    obj = _row_to_obj(cid._member_lineage_row("x", src))
    assert obj["pdh_column"] == "C_EXT_ATTR1 ('odd' name)"


def test_the_registered_manifest_keeps_every_member_lineage_intact():
    """The end state on this branch: the struct's conditional list carries a
    row per member, and marketingInitiative's three source columns survive as
    ONE value rather than as two null keys."""
    from pathlib import Path
    manifest = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / "src/schemas/mapping/product/v1"
         / "manifest.yaml").read_text(encoding="utf-8"))
    rows = manifest["canonical_lineage"]["fulfillmentOrgDetails"]["conditional"]
    for row in rows:
        assert None not in row.values(), row
        assert set(row) <= {"when", "pdh_table", "pdh_column", "pdh_filter",
                            "pdh_grain"}, row
    mi = [r for r in rows if r["when"].startswith("marketingInitiative")]
    assert len(mi) == 1, mi
    for col in ("C_EXT_ATTR1", "D_EXT_ATTR1", "D_EXT_ATTR2"):
        assert col in mi[0]["pdh_column"]
