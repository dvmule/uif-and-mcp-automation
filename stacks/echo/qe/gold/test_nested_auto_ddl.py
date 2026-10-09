# test_nested_auto_ddl.py
# UIFPE-157: the maintenance diff must EVOLVE additive struct widenings
# (nested ADD COLUMN) and REFUSE everything non-additive, naming --rebuild.
# The live incident this guards: fulfillmentOrgDetails went 6 -> 8 fields
# (UIFPE-156) and the incremental died at the MERGE with
# INCOMPATIBLE_DATA_FOR_TABLE.EXTRA_STRUCT_FIELDS, recoverable only by a
# --rebuild that writes no change-log rows.
#
# Everything here exercises the PURE functions in gold_cdc — the live side is
# the plain dict StructType.jsonValue() produces, so no Spark is needed.

from pathlib import Path

import pytest
import yaml

import gold_cdc

MANIFEST = (Path(__file__).resolve().parents[4] / "domains" / "product"
            / "src" / "schemas" / "mapping" / "product" / "v1"
            / "manifest.yaml")


def _struct_json(fields):
    return {"type": "struct",
            "fields": [{"name": n, "type": t, "nullable": True,
                        "metadata": {}} for n, t in fields]}


def _table_json(cols):
    return {"type": "struct",
            "fields": [{"name": n, "type": t, "nullable": True,
                        "metadata": {}} for n, t in cols]}


# ── the live incident, replayed against the REAL manifest ───────────────────

def test_fulfillment_org_details_widening_is_detected():
    """The exact 2026-08-18 failure: the table still carries the 6-field
    struct while the manifest declares 8. The diff must produce ADD COLUMN
    specs for exactly the two new fields, and no refusals."""
    column_types = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))[
        "column_types"]
    # the live table carries PHYSICAL names (N8); the manifest declares logical
    narrow = _struct_json([
        ("selling_restriction_code", "string"), ("discount_code", "string"),
        ("title_status_code", "string"), ("regional_publication_date", "date"),
        ("return_class_code", "string"), ("imprint_name", "string"),
    ])
    live = _table_json([("fulfillment_org_details", narrow)])
    adds, refusals = gold_cdc.diff_nested_field_additions(column_types, live)
    assert refusals == []
    got = {(a["column"], a["field"], a["ddl"]) for a in adds}
    # + marketingInitiative, the array member added 2026-09-07 (UIFPE-270);
    # + boFlag / defaultSoSourceType, the org-level members added 2026-09-09
    assert {(c, f) for c, f, _ in got} == {
        ("fulfillment_org_details", "product_tax_classification_code"),
        ("fulfillment_org_details", "relevance_value"),
        ("fulfillment_org_details", "back_order_indicator"),
        ("fulfillment_org_details", "default_sales_order_source_type_code"),
        ("fulfillment_org_details", "marketing_initiative"),
        # + readyForManufactureCode, the org-level member added 2026-09-15
        ("fulfillment_org_details", "ready_for_manufacture_code"),
    }
    ddl = {f: d for _, f, d in got}
    assert ddl["product_tax_classification_code"] == "STRING"
    assert ddl["relevance_value"] == "STRING"
    assert ddl["ready_for_manufacture_code"] == "STRING"
    assert ddl["marketing_initiative"].upper().startswith("ARRAY<STRUCT<MARKETING_INITIATIVE_NAME")
    paths = {a["path"] for a in adds}
    assert "fulfillment_org_details.product_tax_classification_code" in paths
    # emit-mode channel lookup resolves the physical spelling back to master
    assert gold_cdc.nested_field_channels(
        {"properties": {"fulfillmentOrgDetails": {"type": "object", "x-channels": ["api"],
                        "properties": {"backOrderIndicator": {"x-channels": ["api"]}}}}},
        "fulfillment_org_details", "back_order_indicator") == ["api"]


def test_manifest_in_sync_with_itself_is_a_noop():
    """A live schema built FROM the declared types diffs to nothing — the
    everyday case must stay a no-op (no spurious DDL, no refusals)."""
    column_types = {"fulfillmentOrgDetails":
                    "STRUCT<a: STRING, b: BIGINT, c: DECIMAL(38,10)>"}
    live = _table_json([("fulfillmentOrgDetails", _struct_json(
        [("a", "string"), ("b", "long"), ("c", "decimal(38,10)")]))])
    assert gold_cdc.diff_nested_field_additions(column_types, live) == ([], [])


# ── additive detection shapes ────────────────────────────────────────────────

def test_array_struct_element_widening_uses_element_path():
    column_types = {"marketingCopy":
                    "ARRAY<STRUCT<contentId: STRING, audience: STRING>>"}
    live = _table_json([("marketing_copy", {
        "type": "array", "containsNull": True,
        "elementType": _struct_json([("content_id", "string")])})])
    adds, refusals = gold_cdc.diff_nested_field_additions(column_types, live)
    assert refusals == []
    assert adds == [{"column": "marketing_copy",
                     "path": "marketing_copy.element.audience",
                     "field": "audience", "ddl": "STRING"}]


# ── physical names (Schema Standards N8) ─────────────────────────────────────

def test_physical_name_is_the_n8_transform():
    assert gold_cdc.physical_name("inventoryItemId") == "inventory_item_id"
    assert gold_cdc.physical_name("disciplineLevel1") == "discipline_level1"
    assert gold_cdc.physical_name("isbn13") == "isbn13"
    assert gold_cdc.physical_name("coverImageHighResolutionUrl") == "cover_image_high_resolution_url"
    # metadata and already-physical names pass through
    assert gold_cdc.physical_name("_channel_hashes") == "_channel_hashes"
    assert gold_cdc.physical_name("product_id") == "product_id"
    assert gold_cdc.physical_path("contributors.element.contributorId") == "contributors.element.contributor_id"


def test_physical_ddl_renames_struct_members_only():
    assert gold_cdc.physical_ddl("ARRAY<STRUCT<contributorId: STRING, rank: INT>>") == \
        "ARRAY<STRUCT<contributor_id: STRING, rank: INT>>"
    assert gold_cdc.physical_ddl("STRUCT<a: STRING, inner: ARRAY<STRUCT<startDate: DATE>>>") == \
        "STRUCT<a: STRING, inner: ARRAY<STRUCT<start_date: DATE>>>"
    assert gold_cdc.physical_ddl("DECIMAL(38,2)") == "DECIMAL(38,2)"


def test_rename_schema_json_walks_structs_arrays_and_maps():
    schema = _table_json([
        ("inventoryItemId", "long"),
        ("contributors", {"type": "array", "containsNull": True,
                          "elementType": _struct_json([("givenName", "string")])}),
        ("_channel_hashes", {"type": "map", "keyType": "string",
                             "valueType": "string", "valueContainsNull": True}),
    ])
    out = gold_cdc.rename_schema_json(schema)
    assert [f["name"] for f in out["fields"]] == ["inventory_item_id", "contributors", "_channel_hashes"]
    assert out["fields"][1]["type"]["elementType"]["fields"][0]["name"] == "given_name"
    assert schema["fields"][0]["name"] == "inventoryItemId"  # input untouched


def test_physical_rename_ddls_move_a_logical_table_in_place():
    """A table written before the rule is renamed column by column, parents
    first so nested paths address the parent by its new name."""
    master = {"properties": {"inventoryItemId": {"type": "integer"},
                             "isbn13": {"type": "string"},
                             "fulfillmentOrgDetails": {"type": "object"}}}
    column_types = {"fulfillmentOrgDetails":
                    "STRUCT<returnClassCode: STRING, marketingInitiative: "
                    "ARRAY<STRUCT<startDate: DATE>>>"}
    live = _table_json([
        ("inventoryItemId", "long"), ("isbn13", "string"),
        ("fulfillmentOrgDetails", _struct_json([
            ("returnClassCode", "string"),
            ("marketingInitiative", {"type": "array", "containsNull": True,
                                     "elementType": _struct_json([("startDate", "date")])})])),
    ])
    stmts = gold_cdc.physical_rename_ddls("t", master, column_types, live)
    assert stmts == [
        "ALTER TABLE t RENAME COLUMN `inventoryItemId` TO `inventory_item_id`",
        "ALTER TABLE t RENAME COLUMN `fulfillmentOrgDetails` TO `fulfillment_org_details`",
        "ALTER TABLE t RENAME COLUMN fulfillment_org_details.returnClassCode TO return_class_code",
        "ALTER TABLE t RENAME COLUMN fulfillment_org_details.marketingInitiative TO marketing_initiative",
        "ALTER TABLE t RENAME COLUMN fulfillment_org_details.marketing_initiative.element.startDate TO start_date",
    ]
    # already physical → nothing to do
    live_phys = gold_cdc.rename_schema_json(live)
    assert gold_cdc.physical_rename_ddls("t", master, column_types, live_phys) == []


def test_changes_key_rename_follows_a_primary_key_rename():
    """The change log is created once, so a master PK rename leaves it on
    the old key; stray keys pair with missing ones by position."""
    fixed = list(gold_cdc.CHANGES_FIXED_COLUMNS)
    stmts = gold_cdc.changes_key_rename_ddls(
        "c", ["productId", "organizationCode"],
        ["ppid", "organization_code"] + fixed)
    assert stmts == ["ALTER TABLE c RENAME COLUMN `ppid` TO `product_id`"]
    # already right → nothing to do
    assert gold_cdc.changes_key_rename_ddls(
        "c", ["productId", "organizationCode"],
        ["product_id", "organization_code"] + fixed) == []
    # shapes that do not line up are refused, never guessed
    with pytest.raises(ValueError):
        gold_cdc.changes_key_rename_ddls(
            "c", ["productId"], ["ppid", "organization_code"] + fixed)


def test_added_field_with_complex_type_renders_full_ddl():
    column_types = {"col": "STRUCT<a: STRING, "
                           "extra: STRUCT<x: STRING, y: BIGINT>>"}
    live = _table_json([("col", _struct_json([("a", "string")]))])
    adds, refusals = gold_cdc.diff_nested_field_additions(column_types, live)
    assert refusals == []
    assert adds[0]["ddl"] == "STRUCT<x: STRING, y: BIGINT>"


def test_missing_top_level_column_is_ignored():
    """Columns absent from the table belong to the top-level auto-DDL path,
    not the nested differ."""
    column_types = {"brandNew": "STRUCT<a: STRING>"}
    live = _table_json([("other", "string")])
    assert gold_cdc.diff_nested_field_additions(column_types, live) == ([], [])


# ── fail-closed refusals ─────────────────────────────────────────────────────

def test_nested_type_change_refuses():
    column_types = {"col": "STRUCT<a: BIGINT>"}
    live = _table_json([("col", _struct_json([("a", "string")]))])
    adds, refusals = gold_cdc.diff_nested_field_additions(column_types, live)
    assert adds == []
    assert len(refusals) == 1
    assert "col.a" in refusals[0] and "not additive" in refusals[0]


def test_nested_field_removal_refuses():
    column_types = {"col": "STRUCT<a: STRING>"}
    live = _table_json([("col", _struct_json(
        [("a", "string"), ("gone", "string")]))])
    adds, refusals = gold_cdc.diff_nested_field_additions(column_types, live)
    assert adds == []
    assert any("col.gone" in r and "remov" in r for r in refusals)


def test_kind_mismatch_refuses():
    column_types = {"col": "ARRAY<STRING>"}
    live = _table_json([("col", _struct_json([("a", "string")]))])
    adds, refusals = gold_cdc.diff_nested_field_additions(column_types, live)
    assert adds == [] and len(refusals) == 1


def test_addition_and_refusal_can_coexist_across_columns():
    """One column widens while another drifted non-additively — the refusal
    must surface even though an addition exists (the caller fails closed)."""
    column_types = {"good": "STRUCT<a: STRING, b: STRING>",
                    "bad": "STRUCT<a: BIGINT>"}
    live = _table_json([
        ("good", _struct_json([("a", "string")])),
        ("bad", _struct_json([("a", "string")]))])
    adds, refusals = gold_cdc.diff_nested_field_additions(column_types, live)
    assert len(adds) == 1 and len(refusals) == 1


# ── type-alias tolerance (the false-refusal trap) ────────────────────────────

def test_long_vs_bigint_and_case_do_not_refuse():
    column_types = {"col": "struct<a: bigint, b: Timestamp, c: int>"}
    live = _table_json([("col", _struct_json(
        [("a", "long"), ("b", "timestamp"), ("c", "integer")]))])
    assert gold_cdc.diff_nested_field_additions(column_types, live) == ([], [])


def test_primitive_declared_types_only_compare():
    """Non-complex column_types entries (DATE etc.) still diff as primitives
    and refuse on real divergence."""
    column_types = {"d": "DATE"}
    live = _table_json([("d", "string")])
    adds, refusals = gold_cdc.diff_nested_field_additions(column_types, live)
    assert adds == [] and len(refusals) == 1


# ── emit-mode channel lookup ─────────────────────────────────────────────────

def test_nested_field_channels_struct_and_array():
    master = {"properties": {
        "st": {"type": "object", "x-channels": ["api", "message"],
               "properties": {"f": {"x-channels": ["api"]}}},
        "ar": {"type": "array", "x-channels": ["api"],
               "items": {"properties": {"g": {"x-channels": ["api"]}}}},
    }}
    assert gold_cdc.nested_field_channels(master, "st", "f") == ["api"]
    assert gold_cdc.nested_field_channels(master, "ar", "g") == ["api"]
    # unknown field falls back to the parent's tags
    assert gold_cdc.nested_field_channels(master, "st", "nope") == [
        "api", "message"]

# ── the REFRESH path must refuse a stale struct, not MERGE into it ──────────
# generic_gold_transform's refresh branch RETURNS before the maintenance diff
# that evolves structs, so it can never apply the nested ADD COLUMN itself.
# Left unchecked it composes rows in the NEW shape and merges them into the
# OLD one - the same EXTRA_STRUCT_FIELDS failure UIFPE-157 was raised for,
# except reached by a path the top-level `absent` guard cannot see, because
# the COLUMN exists and only its struct is stale.
#
# No test can import generic_gold_transform (it needs Spark and Glue), so this
# is a source-structure guard: it fails if the pre-flight is removed or moved
# after the write.

TRANSFORM_SRC = (Path(__file__).resolve().parents[2] / "src" / "apps"
                 / "pipeline" / "generic_gold_transform.py")


def _refresh_branch_source():
    text = TRANSFORM_SRC.read_text(encoding="utf-8")
    start = text.index("if REFRESH_COLUMNS:")
    end = text.index("MAINTENANCE DIFF", start)
    return text[start:end]


def test_the_refresh_branch_exists_where_this_test_expects_it():
    """Fail loudly rather than vacuously if the branch is restructured."""
    body = _refresh_branch_source()
    assert "REFRESH ABORTED" in body, (
        "the refresh branch no longer carries its pre-flight refusals - "
        "re-derive this test against the new structure")


def test_the_refresh_branch_preflights_nested_struct_drift():
    body = _refresh_branch_source()
    assert "diff_nested_field_additions" in body, (
        "the refresh branch does not check for nested struct drift. A widened "
        "ARRAY<STRUCT<...>> would be merged into the stored narrower struct, "
        "because this branch returns before the maintenance DDL that evolves "
        "it and the top-level column check cannot see one level down.")


def test_the_nested_preflight_runs_before_any_write():
    body = _refresh_branch_source()
    check = body.index("diff_nested_field_additions")
    # the first thing in the branch that actually writes
    writes = [body.index(m) for m in ("build_refresh_row_sql",
                                      "build_refresh_merge_sql",
                                      "append_change_log") if m in body]
    assert writes, "the refresh branch no longer writes - re-derive this test"
    assert check < min(writes), (
        "the nested-struct pre-flight must run BEFORE the refresh composes or "
        "merges anything; refusing after a write defeats the point")
