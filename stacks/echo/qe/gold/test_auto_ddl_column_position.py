# test_auto_ddl_column_position.py
# UIFPE-268: a business column added by the maintenance auto-DDL must land
# BEFORE the framework metadata columns, so those stay last however many
# columns are added after the first load. The live incident: product's
# `categories` (2026-08-31) sat at position 59 behind `_channel_hashes` /
# `_gold_updated_at` / `_gold_run_id` at 56-58.

import gold_cdc

T = "glue_catalog.`uif_echo_dev_gold_product`.`product`"
LIVE = ["inventoryItemId", "organizationId", "ppid", "marketingCopy",
        "_channel_hashes", "_gold_updated_at", "_gold_run_id"]


def test_new_column_is_placed_after_the_last_business_column():
    stmts = gold_cdc.build_add_column_ddls(
        T, [("categories", "ARRAY<STRUCT<categoryId: BIGINT>>")], LIVE)
    assert stmts == [
        f"ALTER TABLE {T} ADD COLUMN `categories` "
        f"ARRAY<STRUCT<categoryId: BIGINT>> AFTER `marketingCopy`"]


def test_several_new_columns_chain_and_keep_their_order():
    stmts = gold_cdc.build_add_column_ddls(
        T, [("url", "STRING"), ("courseDurationValue", "STRING")], LIVE)
    assert stmts == [
        f"ALTER TABLE {T} ADD COLUMN `url` STRING AFTER `marketingCopy`",
        f"ALTER TABLE {T} ADD COLUMN `courseDurationValue` STRING AFTER `url`",
    ]


def test_metadata_already_in_the_middle_anchors_before_it():
    """product's live shape: categories was appended behind the metadata.
    A new column goes before the metadata block; the stray column is left
    for the rebuild to put right (this path never moves existing columns)."""
    live = LIVE + ["categories"]
    stmts = gold_cdc.build_add_column_ddls(T, [("url", "STRING")], live)
    assert stmts == [
        f"ALTER TABLE {T} ADD COLUMN `url` STRING AFTER `marketingCopy`"]


def test_pre_adr_0003_table_without_metadata_just_appends():
    live = ["inventoryItemId", "organizationId", "ppid"]
    stmts = gold_cdc.build_add_column_ddls(T, [("url", "STRING")], live)
    assert stmts == [f"ALTER TABLE {T} ADD COLUMN `url` STRING"]


def test_metadata_leading_the_table_uses_first():
    live = ["_channel_hashes", "_gold_updated_at", "_gold_run_id"]
    stmts = gold_cdc.build_add_column_ddls(
        T, [("a", "STRING"), ("b", "STRING")], live)
    assert stmts == [
        f"ALTER TABLE {T} ADD COLUMN `a` STRING FIRST",
        f"ALTER TABLE {T} ADD COLUMN `b` STRING AFTER `a`",
    ]


def test_no_specs_no_statements():
    assert gold_cdc.build_add_column_ddls(T, [], LIVE) == []
