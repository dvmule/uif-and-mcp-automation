# test_manifest_upsert_bounds.py
# UIFPE-268: the intake compiler edits manifest.yaml by TEXT position. Two
# anchors could misfire once a manifest carries a trailing null_sentinels
# block — the output_columns append used to scan to end-of-file for the last
# "  - item" line, and lineage entries land just before `schema:`. This pins
# the bounded append and the placement rule the CI lint enforces.

import compile_intake_delta as cid

MANIFEST = """domain: product
version: v1

source_system:
  name: "Oracle EBS PDH"

canonical_lineage:
  ppid:
    pdh_table:  MTL_SYSTEM_ITEMS_B
    pdh_column: SEGMENT1

schema:
  master: ../../../gold/product/v1/master.json

sql:
  file: sql/product.sql

sources:
  - alias: items
    silver_table: uif_echo_{env}_silver_mdm.mtl_system_items_b
    watermark_col: _silver_updated_at
    is_source_of_truth: true
    key_mapping:
      inventoryItemId: "CAST(INVENTORY_ITEM_ID AS BIGINT)"

target:
  database: uif_echo_{env}_gold_product
  table: product
  primary_key_cols:
    - inventoryItemId

output_columns:
  - inventoryItemId
  - ppid

null_sentinels:
  string:
  - TBD
  - N/A
  numeric:
  - 99998
"""


def _col(name="brandNew"):
    return {"goldColumnName": name, "kind": "scalar", "dataType": "string",
            "source": {"table": "EGO_MTL_SY_ITEMS_EXT_B", "column": "C_EXT_ATTR7",
                       "filter": "ATTR_GROUP_ID = 802"}}


def test_new_column_lands_in_output_columns_not_in_a_later_block_list():
    out = cid.manifest_upsert(MANIFEST, _col(), "add")
    oc = out.index("output_columns:")
    ns = out.index("null_sentinels:")
    assert oc < out.index("  - brandNew") < ns, out
    # the sentinel lists are untouched
    assert out[ns:].count("  - ") == 3
    assert "  - TBD\n  - N/A\n" in out


def test_lineage_entry_still_lands_at_the_end_of_canonical_lineage():
    out = cid.manifest_upsert(MANIFEST, _col(), "add")
    lineage_end = out.index("\nschema:")
    assert out.index("  brandNew:") < lineage_end
