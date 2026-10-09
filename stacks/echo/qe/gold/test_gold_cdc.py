# test_gold_cdc.py
# Unit tests for glue_jobs/gold_cdc.py — the pure-logic library behind the
# Gold incremental CDC flow (ADR-0003). Imports the real module; no Spark
# or AWS needed.

import unittest
from pathlib import Path

import gold_cdc


def make_master(props=None):
    """Minimal master schema in the shape of schemas/gold/product/v1."""
    return {
        "properties": props or {
            "inventory_item_id": {"type": "integer", "x-channels": ["api"]},
            "product_id": {"type": "string",
                           "x-channels": ["event", "message", "api"]},
            "org_id": {"type": "integer",
                       "x-channels": ["event", "message", "api"]},
            "isbn10": {"type": ["string", "null"],
                       "x-channels": ["event", "message", "api"]},
            "isbn13": {"type": ["string", "null"],
                       "x-channels": ["event", "message", "api"]},
        }
    }


class TestDiscoverChannels(unittest.TestCase):
    def test_discovers_union_plus_canonical(self):
        self.assertEqual(
            gold_cdc.discover_channels(make_master()),
            ["api", "canonical", "event", "message"],
        )

    def test_new_channel_tag_is_discovered(self):
        master = make_master()
        master["properties"]["isbn13"]["x-channels"].append("datafeed")
        self.assertIn("datafeed", gold_cdc.discover_channels(master))

    def test_explicit_canonical_tag_rejected(self):
        master = make_master()
        master["properties"]["product_id"]["x-channels"].append("canonical")
        with self.assertRaises(ValueError):
            gold_cdc.discover_channels(master)

    def test_untagged_schema_yields_canonical_only(self):
        master = {"properties": {"a": {"type": "string"}}}
        self.assertEqual(gold_cdc.discover_channels(master), ["canonical"])


class TestChannelProperties(unittest.TestCase):
    def test_canonical_carries_all_properties_sorted(self):
        self.assertEqual(
            gold_cdc.channel_properties(make_master(), "canonical"),
            ["inventory_item_id", "isbn10", "isbn13", "org_id", "product_id"],
        )

    def test_event_carries_tagged_subset(self):
        self.assertEqual(
            gold_cdc.channel_properties(make_master(), "event"),
            ["isbn10", "isbn13", "org_id", "product_id"],
        )

    def test_unknown_channel_is_empty(self):
        self.assertEqual(gold_cdc.channel_properties(make_master(), "nope"), [])


class TestHashExprs(unittest.TestCase):
    def test_hash_expr_shape_and_property_order(self):
        expr = gold_cdc.build_channel_hash_expr(make_master(), "event")
        self.assertTrue(expr.startswith("sha2(to_json(named_struct("))
        self.assertIn("map('ignoreNullFields', 'false')", expr)
        self.assertIn("256", expr)
        # sorted property order — isbn10 before org_id before product_id
        self.assertLess(expr.index("isbn10"), expr.index("org_id"))
        self.assertLess(expr.index("org_id"), expr.index("product_id"))

    def test_hash_expr_excludes_untagged_property(self):
        expr = gold_cdc.build_channel_hash_expr(make_master(), "event")
        self.assertNotIn("inventory_item_id", expr)

    def test_qualifier_prefixes_columns(self):
        expr = gold_cdc.build_channel_hash_expr(make_master(), "event", "s")
        self.assertIn("'isbn10', s.`isbn10`", expr)

    def test_empty_channel_raises(self):
        with self.assertRaises(ValueError):
            gold_cdc.build_channel_hash_expr(make_master(), "ghost")

    def test_hash_map_covers_all_channels(self):
        expr = gold_cdc.build_hash_map_expr(make_master())
        for ch in ("api", "canonical", "event", "message"):
            self.assertIn(f"'{ch}', sha2(", expr)

    def _nested_master(self):
        return {"properties": {
            "id": {"type": "integer", "x-channels": ["event", "api"]},
            "contributors": {
                "type": ["array", "null"],
                "x-channels": ["event", "api"],
                "items": {"type": "object", "properties": {
                    "name": {"type": ["string", "null"]},
                    "affiliation": {"type": ["string", "null"],
                                    "x-channels": ["api"]},
                }},
            },
            "fulfillment": {
                "type": ["object", "null"],
                "x-channels": ["event", "api"],
                "properties": {
                    "status": {"type": ["string", "null"]},
                    "secret": {"type": ["string", "null"],
                               "x-channels": ["api"]},
                },
            },
        }}

    def test_nested_projection_in_restricted_channel_hash(self):
        expr = gold_cdc.build_channel_hash_expr(self._nested_master(), "event")
        # array projected via transform, hidden field absent
        self.assertIn("transform(`contributors`, x -> named_struct('name', x.`name`))", expr)
        self.assertNotIn("affiliation", expr)
        # object projected via named_struct rebuild
        self.assertIn("named_struct('status', `fulfillment`.`status`)", expr)
        self.assertNotIn("secret", expr)

    def test_full_visibility_channels_use_bare_columns(self):
        for ch in ("api", "canonical"):
            expr = gold_cdc.build_channel_hash_expr(self._nested_master(), ch)
            self.assertIn("'contributors', `contributors`", expr)
            self.assertNotIn("transform(", expr)

    def test_validate_nested_channels(self):
        gold_cdc.validate_nested_channels(self._nested_master())  # ok
        bad = self._nested_master()
        bad["properties"]["contributors"]["items"]["properties"]["affiliation"][
            "x-channels"] = ["message"]  # parent has no message
        with self.assertRaises(ValueError):
            gold_cdc.validate_nested_channels(bad)

    def test_changed_channels_expr_handles_insert_and_diff(self):
        expr = gold_cdc.build_changed_channels_expr(["event", "canonical"])
        self.assertIn("g.`_channel_hashes` IS NULL", expr)
        self.assertIn("IS DISTINCT FROM", expr)
        self.assertIn("filter(array(", expr)
        self.assertIn("'canonical'", expr)
        self.assertIn("'event'", expr)


class TestResolveEnv(unittest.TestCase):
    def test_substitutes_env_token(self):
        self.assertEqual(
            gold_cdc.resolve_env("uif_echo_{env}_silver_mdm.mtl_system_items_b", "dev"),
            "uif_echo_dev_silver_mdm.mtl_system_items_b")
        self.assertEqual(
            gold_cdc.resolve_env("uif_echo_{env}_gold_product", "uat"),
            "uif_echo_uat_gold_product")

    def test_passthrough_without_token_or_non_string(self):
        self.assertEqual(gold_cdc.resolve_env("plain_db", "dev"), "plain_db")
        self.assertIsNone(gold_cdc.resolve_env(None, "dev"))


class TestRefreshColumns(unittest.TestCase):
    """F3 helpers — the pure half of --refresh_columns. The Spark half
    (delta guard, diff, MERGE execution) lives in the transform and follows
    the same patterns as the incremental path."""

    MASTER = {"properties": {"ppid": {}, "coverImageRegular": {},
                             "fulfillmentOrgDetails": {}, "brand": {},
                             "inventoryItemId": {}, "organizationId": {}}}
    PKS = ["inventoryItemId", "organizationId"]

    def test_parse_splits_strips_dedupes_preserving_order(self):
        self.assertEqual(
            gold_cdc.parse_refresh_columns(" coverImageRegular, brand ,"
                                           "coverImageRegular"),
            ["coverImageRegular", "brand"])
        self.assertEqual(gold_cdc.parse_refresh_columns(""), [])
        self.assertEqual(gold_cdc.parse_refresh_columns("  "), [])

    def test_rebuild_and_refresh_are_mutually_exclusive(self):
        with self.assertRaises(ValueError):
            gold_cdc.validate_refresh_args(["brand"], True, "emit")
        gold_cdc.validate_refresh_args(["brand"], False, "emit")  # ok
        gold_cdc.validate_refresh_args([], True, "emit")          # ok

    def test_mode_is_a_strict_enum(self):
        with self.assertRaises(ValueError):
            gold_cdc.validate_refresh_args(["brand"], False, "loud")
        gold_cdc.validate_refresh_args(["brand"], False, "silent")

    def test_unknown_pk_and_metadata_columns_refused(self):
        with self.assertRaises(ValueError) as cm:
            gold_cdc.validate_refresh_columns(["nope"], self.MASTER, self.PKS)
        self.assertIn("nope", str(cm.exception))
        with self.assertRaises(ValueError):
            gold_cdc.validate_refresh_columns(["inventoryItemId"],
                                              self.MASTER, self.PKS)
        with self.assertRaises(ValueError):
            gold_cdc.validate_refresh_columns(["_channel_hashes"],
                                              self.MASTER, self.PKS)
        gold_cdc.validate_refresh_columns(["brand", "coverImageRegular"],
                                          self.MASTER, self.PKS)  # ok

    def test_merge_sets_only_named_columns_plus_hashes(self):
        sql = gold_cdc.build_refresh_merge_sql(
            "`glue_catalog`.`db`.`product`", self.PKS, "refresh_diff",
            ["coverImageRegular", "fulfillmentOrgDetails"],
            bump_metadata=False)
        # named logically, written physically (N8)
        self.assertIn("t.`cover_image_regular` = s.`cover_image_regular`", sql)
        self.assertIn("t.`fulfillment_org_details` = s.`fulfillment_org_details`",
                      sql)
        self.assertIn("t.`_channel_hashes` = s.`_channel_hashes`", sql)
        # silent mode must not announce the change
        self.assertNotIn("_gold_updated_at", sql)
        self.assertNotIn("_gold_run_id", sql)
        # never a WHEN NOT MATCHED arm — a refresh cannot insert
        self.assertNotIn("NOT MATCHED", sql)
        # and never touches un-named business columns
        self.assertNotIn("`brand`", sql)

    def test_refresh_row_is_composed_from_fresh_named_and_stored_rest(self):
        # UIFPE-269: the hash is computed over the row the MERGE will leave
        # behind — named columns from the fresh relation (u), everything else
        # from Gold (g) — so pending deltas on un-named columns can never be
        # hidden behind a hash that claims they were applied.
        master = make_master()
        sql = gold_cdc.build_refresh_row_sql(
            "glue_catalog.`db`.`t`", ["inventory_item_id", "org_id"], "fresh",
            ["isbn13"], master, ["api", "canonical", "event", "message"],
            "TIMESTAMP '2026-09-07 07:15:00'", "jr_1")
        self.assertIn("u.`isbn13`", sql)
        for col in ("inventory_item_id", "product_id", "org_id", "isbn10"):
            self.assertIn(f"g.`{col}`", sql)
        self.assertNotIn("u.`isbn10`", sql)
        # the hash reads the same relations per column as the projection
        hash_part = sql[sql.index("map("):sql.index(" AS `_channel_hashes`")]
        self.assertIn("'isbn13', u.`isbn13`", hash_part)
        self.assertIn("'isbn10', g.`isbn10`", hash_part)
        self.assertNotIn("'isbn13', g.`isbn13`", hash_part)
        self.assertIn("AS `_gold_updated_at`", sql)
        self.assertIn("'jr_1' AS `_gold_run_id`", sql)
        self.assertTrue(sql.rstrip().endswith(
            "FROM fresh u JOIN glue_catalog.`db`.`t` g ON "
            "g.`inventory_item_id` = u.`inventory_item_id` "
            "AND g.`org_id` = u.`org_id`"), sql)

    def test_per_column_qualifiers_override_the_default(self):
        master = make_master()
        expr = gold_cdc.build_channel_hash_expr(
            master, "event", qualifier="g", qualifiers={"isbn13": "u"})
        self.assertIn("'isbn13', u.`isbn13`", expr)
        self.assertIn("'isbn10', g.`isbn10`", expr)

    def test_emit_mode_bumps_row_metadata(self):
        sql = gold_cdc.build_refresh_merge_sql(
            "t_full", self.PKS, "v", ["brand"], bump_metadata=True)
        self.assertIn("t.`_gold_updated_at` = s.`_gold_updated_at`", sql)
        self.assertIn("t.`_gold_run_id` = s.`_gold_run_id`", sql)

    def test_refresh_op_is_distinct_from_other_ops(self):
        self.assertEqual(gold_cdc.REFRESH_OP, "REFRESH")
        self.assertNotIn(gold_cdc.REFRESH_OP, ("INSERT", "UPDATE", "DELETE",
                                               "BACKFILL"))


class TestBuildSotPkSql(unittest.TestCase):
    MAPPING = {"inventoryItemId": "INVENTORY_ITEM_ID",
               "organizationId": "ORGANIZATION_ID"}
    PKS = ["inventoryItemId", "organizationId"]

    def test_unfiltered_projection(self):
        self.assertEqual(
            gold_cdc.build_sot_pk_sql("`glue_catalog`.`db`.`t`",
                                      self.MAPPING, self.PKS),
            "SELECT INVENTORY_ITEM_ID AS inventory_item_id, "
            "ORGANIZATION_ID AS organization_id "
            "FROM `glue_catalog`.`db`.`t`")

    def test_sot_filter_becomes_where_clause(self):
        sql = gold_cdc.build_sot_pk_sql("t", self.MAPPING, self.PKS,
                                        "SEGMENT1 LIKE 'A%'")
        self.assertTrue(sql.endswith("FROM t WHERE SEGMENT1 LIKE 'A%'"))

    def test_blank_filter_is_no_filter(self):
        for f in (None, "", "   "):
            self.assertNotIn("WHERE",
                             gold_cdc.build_sot_pk_sql("t", self.MAPPING,
                                                       self.PKS, f))

    def test_column_order_follows_primary_key_cols_not_dict(self):
        # Deterministic output — reversed PK list must reverse the projection.
        sql = gold_cdc.build_sot_pk_sql("t", self.MAPPING,
                                        list(reversed(self.PKS)))
        self.assertTrue(sql.startswith(
            "SELECT ORGANIZATION_ID AS organization_id, "
            "INVENTORY_ITEM_ID AS inventory_item_id"))


class TestQualifyTable(unittest.TestCase):
    def test_backticks_every_part(self):
        self.assertEqual(
            gold_cdc.qualify_table("uif_echo_dev_silver_mdm.mtl_system_items_b"),
            "`glue_catalog`.`uif_echo_dev_silver_mdm`.`mtl_system_items_b`")

    def test_hyphenated_table_survives_the_sql_parser(self):
        # The case this exists for: an unquoted hyphen parses as subtraction and
        # Spark reports TABLE_OR_VIEW_NOT_FOUND, which looks like a missing
        # table rather than a quoting bug.
        self.assertEqual(
            gold_cdc.qualify_table("uif_echo_dev_silver_mdm.mtl-system-items-b"),
            "`glue_catalog`.`uif_echo_dev_silver_mdm`.`mtl-system-items-b`")

    def test_idempotent_on_already_quoted_input(self):
        self.assertEqual(
            gold_cdc.qualify_table("`db`.`tbl`"),
            "`glue_catalog`.`db`.`tbl`")

    def test_embedded_backtick_is_escaped_by_doubling(self):
        self.assertEqual(gold_cdc.qualify_table("db.we`ird", catalog=None),
                         "`db`.`we``ird`")

    def test_catalog_can_be_omitted(self):
        self.assertEqual(gold_cdc.qualify_table("db.tbl", catalog=None),
                         "`db`.`tbl`")

    def test_composes_with_resolve_env(self):
        self.assertEqual(
            gold_cdc.qualify_table(
                gold_cdc.resolve_env("uif_echo_{env}_silver_mdm.tbl", "dev")),
            "`glue_catalog`.`uif_echo_dev_silver_mdm`.`tbl`")


class TestResolveBookmark(unittest.TestCase):
    """ADR-0020 decision 1: one bookmark per entity, or None before the first
    run writes it."""

    def test_reads_the_bookmark(self):
        self.assertEqual(
            gold_cdc.resolve_bookmark({"bookmark": "2026-09-23T06:30:12.000001+00:00"}),
            "2026-09-23T06:30:12.000001+00:00")

    def test_absent_is_none_not_epoch(self):
        for cfg in ({}, None, {"silver_snapshots": {"a": "s3://m/1.json"}},
                    {"last_processed_silver_updated_at": {"items": "2026-09-18T09:19:59"}}):
            with self.subTest(cfg=cfg):
                self.assertIsNone(gold_cdc.resolve_bookmark(cfg))

    def test_the_retired_per_source_map_is_never_the_bookmark(self):
        cfg = {"last_processed_silver_updated_at": {"items": "2026-09-30T00:00:00"},
               "bookmark": "2026-09-22T06:31:13+00:00"}
        self.assertEqual(gold_cdc.resolve_bookmark(cfg), "2026-09-22T06:31:13+00:00")

    def test_rejects_non_timestamps(self):
        for bad in (42, {"items": "x"}, "yesterday"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                gold_cdc.resolve_bookmark({"bookmark": bad})


class TestLatestSuccessfulStart(unittest.TestCase):
    """UIFPE-522 seed: the newest SUCCEEDED transform start for THIS entity."""

    RUNS = [
        {"gold_id": "product", "status": "SUCCEEDED", "started_at": "2026-09-21T06:31:02+00:00"},
        {"gold_id": "product", "status": "SUCCEEDED", "started_at": "2026-09-22T06:31:13+00:00"},
        {"gold_id": "product", "status": "FAILED",    "started_at": "2026-09-22T12:00:00+00:00"},
        {"gold_id": "product", "status": "SKIPPED",   "started_at": "2026-09-22T13:00:00+00:00"},
        {"gold_id": "price",   "status": "SUCCEEDED", "started_at": "2026-09-22T08:47:01+00:00"},
    ]

    def test_newest_success_for_the_entity(self):
        self.assertEqual(gold_cdc.latest_successful_start(self.RUNS, "product"),
                         "2026-09-22T06:31:13+00:00")

    def test_failed_and_skipped_runs_do_not_count(self):
        # a failed run advanced nothing; a skipped run processed nothing
        only_bad = [r for r in self.RUNS if r["status"] != "SUCCEEDED"]
        self.assertIsNone(gold_cdc.latest_successful_start(only_bad, "product"))

    def test_other_entities_are_ignored(self):
        self.assertEqual(gold_cdc.latest_successful_start(self.RUNS, "price"),
                         "2026-09-22T08:47:01+00:00")
        self.assertIsNone(gold_cdc.latest_successful_start(self.RUNS, "stock"))

    def test_no_runs_is_none(self):
        self.assertIsNone(gold_cdc.latest_successful_start([], "product"))
        self.assertIsNone(gold_cdc.latest_successful_start(None, "product"))


class TestRegistryDiffs(unittest.TestCase):
    def test_new_channel_detected(self):
        master = make_master()
        master["properties"]["isbn13"]["x-channels"].append("datafeed")
        self.assertEqual(
            gold_cdc.diff_new_channels(
                master, ["api", "canonical", "event", "message"]),
            ["datafeed"],
        )

    def test_no_new_channels(self):
        self.assertEqual(
            gold_cdc.diff_new_channels(
                make_master(), ["api", "canonical", "event", "message"]),
            [],
        )

    def test_new_property_detected(self):
        master = make_master()
        master["properties"]["item_status"] = {
            "type": "string", "x-channels": ["api"]}
        self.assertEqual(
            gold_cdc.diff_new_properties(
                master,
                ["inventory_item_id", "product_id", "org_id",
                 "isbn10", "isbn13"]),
            ["item_status"],
        )

    def test_empty_registry_means_all_new(self):
        self.assertEqual(len(gold_cdc.diff_new_properties(make_master(), [])), 5)


class TestJsonTypeToSpark(unittest.TestCase):
    def test_scalar_mappings(self):
        self.assertEqual(
            gold_cdc.json_type_to_spark("a", {"type": "string"}), "STRING")
        self.assertEqual(
            gold_cdc.json_type_to_spark("a", {"type": "integer"}), "BIGINT")
        self.assertEqual(
            gold_cdc.json_type_to_spark("a", {"type": "number"}),
            "DECIMAL(38,10)")
        self.assertEqual(
            gold_cdc.json_type_to_spark("a", {"type": "boolean"}), "BOOLEAN")

    def test_nullable_union_resolves(self):
        self.assertEqual(
            gold_cdc.json_type_to_spark("a", {"type": ["string", "null"]}),
            "STRING")

    def test_number_format_float_and_double(self):
        self.assertEqual(
            gold_cdc.json_type_to_spark("a", {"type": "number",
                                              "format": "float"}), "FLOAT")
        self.assertEqual(
            gold_cdc.json_type_to_spark(
                "a", {"type": ["number", "null"], "format": "double"}),
            "DOUBLE")
        # plain number stays exact decimal
        self.assertEqual(
            gold_cdc.json_type_to_spark("a", {"type": "number"}),
            "DECIMAL(38,10)")

    def test_array_requires_manifest_override(self):
        with self.assertRaises(ValueError):
            gold_cdc.json_type_to_spark("bom", {"type": "array"})

    def test_manifest_override_wins(self):
        self.assertEqual(
            gold_cdc.json_type_to_spark(
                "bom", {"type": "array"},
                {"bom": "ARRAY<STRUCT<id: BIGINT>>"}),
            "ARRAY<STRUCT<id: BIGINT>>")


class TestValidateKeyMappings(unittest.TestCase):
    PK = ["inventory_item_id", "org_id"]

    def test_valid_full_and_subset_mappings_pass(self):
        gold_cdc.validate_key_mappings([
            {"alias": "items", "is_source_of_truth": True,
             "key_mapping": {"inventory_item_id": "INVENTORY_ITEM_ID",
                             "org_id": "ORGANIZATION_ID"}},
            {"alias": "items_ext",
             "key_mapping": {"inventory_item_id": "INVENTORY_ITEM_ID"}},
        ], self.PK)

    def test_missing_key_mapping_raises(self):
        with self.assertRaises(ValueError) as ctx:
            gold_cdc.validate_key_mappings(
                [{"alias": "items_ext"}], self.PK)
        self.assertIn("items_ext", str(ctx.exception))

    def test_non_pk_column_raises(self):
        with self.assertRaises(ValueError):
            gold_cdc.validate_key_mappings(
                [{"alias": "x", "key_mapping": {"not_a_pk": "COL"}}], self.PK)

    def test_sot_must_map_full_pk(self):
        with self.assertRaises(ValueError):
            gold_cdc.validate_key_mappings(
                [{"alias": "items", "is_source_of_truth": True,
                  "key_mapping": {"inventory_item_id": "INVENTORY_ITEM_ID"}}],
                self.PK)

    # ── lookup_only (taxonomy dimensions) ────────────────────────────────
    def test_lookup_only_source_needs_no_key_mapping(self):
        # A dimension carrying NEITHER PK column (MTL_CATEGORIES_B has no item
        # and no org) has no honest projection to Gold keys. It declares
        # lookup_only instead and is skipped by Phase 1.
        gold_cdc.validate_key_mappings([
            {"alias": "items", "is_source_of_truth": True,
             "key_mapping": {"inventory_item_id": "INVENTORY_ITEM_ID",
                             "org_id": "ORGANIZATION_ID"}},
            {"alias": "categories_b", "lookup_only": True},
        ], self.PK)

    def test_lookup_only_with_key_mapping_raises(self):
        # Declaring both is ambiguous, and the ambiguity has a specific bad
        # resolution: a null-valued key_mapping passes the PK check, advances
        # the watermark and detects nothing. Refuse the combination outright.
        with self.assertRaises(ValueError) as ctx:
            gold_cdc.validate_key_mappings([
                {"alias": "categories_b", "lookup_only": True,
                 "key_mapping": {"org_id": "CAST(NULL AS BIGINT)"}},
            ], self.PK)
        self.assertIn("lookup_only", str(ctx.exception))

    def test_lookup_only_sot_raises(self):
        with self.assertRaises(ValueError):
            gold_cdc.validate_key_mappings(
                [{"alias": "items", "is_source_of_truth": True,
                  "lookup_only": True}], self.PK)

    def test_non_lookup_source_still_needs_key_mapping(self):
        # lookup_only defaults off: the existing D1 requirement is unchanged
        # for every source that does not opt in.
        with self.assertRaises(ValueError) as ctx:
            gold_cdc.validate_key_mappings(
                [{"alias": "categories_b", "lookup_only": False}], self.PK)
        self.assertIn("lookup_only", str(ctx.exception),
                      "the error should point at the escape hatch")


class TestValidateArraySortKeys(unittest.TestCase):
    def test_array_without_sort_key_raises(self):
        master = make_master()
        master["properties"]["bom_components"] = {
            "type": "array", "x-channels": ["api"]}
        with self.assertRaises(ValueError):
            gold_cdc.validate_array_sort_keys(master, {"array_sort_keys": {}})

    def test_array_with_sort_key_passes(self):
        master = make_master()
        master["properties"]["bom_components"] = {
            "type": "array", "x-channels": ["api"]}
        gold_cdc.validate_array_sort_keys(
            master,
            {"array_sort_keys": {"bom_components": ["component_item_id"]}})

    def test_scalar_only_schema_passes_without_block(self):
        gold_cdc.validate_array_sort_keys(make_master(), {})


class TestKeyDetectionSql(unittest.TestCase):
    def test_projects_key_mapping_with_watermark_filter(self):
        sql = gold_cdc.build_key_detection_sql(
            "silver_product.ego_mtl_sy_items_ext_b",
            {"inventory_item_id": "CAST(INVENTORY_ITEM_ID AS BIGINT)"},
            "_silver_updated_at", "2026-07-01T00:00:00")
        self.assertIn("SELECT DISTINCT", sql)
        self.assertIn(
            "CAST(INVENTORY_ITEM_ID AS BIGINT) AS `inventory_item_id`", sql)
        self.assertIn("glue_catalog.silver_product.ego_mtl_sy_items_ext_b", sql)
        self.assertIn(
            "`_silver_updated_at` > TIMESTAMP '2026-07-01T00:00:00'", sql)


class TestDrivingFilter(unittest.TestCase):
    KM = {"inventory_item_id": "CAST(src.INVENTORY_ITEM_ID AS BIGINT)",
          "org_id": "CAST(src.ORGANIZATION_ID AS BIGINT)"}

    def test_full_key_and_subset_keysets(self):
        sql = gold_cdc.build_driving_filter(self.KM, [
            ("ak_items", ["inventory_item_id", "org_id"]),
            ("ak_ext", ["inventory_item_id"]),
        ])
        self.assertIn("EXISTS (SELECT 1 FROM ak_items k WHERE", sql)
        self.assertIn("EXISTS (SELECT 1 FROM ak_ext k WHERE", sql)
        self.assertIn(" OR ", sql)
        # subset keyset compares only its bound column
        ext_clause = sql.split(" OR ")[1]
        self.assertNotIn("org_id", ext_clause)

    def test_no_keysets_yields_false(self):
        self.assertEqual(gold_cdc.build_driving_filter(self.KM, []), "FALSE")

    def test_the_recompute_leg_rebuilds_a_doubly_selected_entity_once(self):
        """ADR-0020 decision 5: an entity picked by BOTH detection and the
        recompute leg must be rebuilt once, not twice.

        This is a property of the filter's shape, not of a dedupe step, which
        is why no dedupe exists in the transform. The clauses become one
        disjunctive WHERE on the driving relation, so a driving row satisfying
        both EXISTS still yields exactly one row — one rebuild, one diff row,
        one change-log row, one MERGE target.
        """
        sql = gold_cdc.build_driving_filter(self.KM, [
            ("ak_items", ["inventory_item_id", "org_id"]),
            ("recompute_keys", ["inventory_item_id", "org_id"]),
        ])
        # One disjunction, not a union or a join: OR cannot duplicate a row.
        self.assertEqual(sql.count(" OR "), 1)
        self.assertEqual(sql.count("EXISTS"), 2)
        self.assertIn("EXISTS (SELECT 1 FROM recompute_keys k WHERE", sql)
        for frag in ("UNION", "JOIN", "GROUP BY", "DISTINCT"):
            self.assertNotIn(frag, sql.upper(),
                             f"{frag} would change the row count")

    def test_the_leg_binds_the_whole_primary_key_so_it_cannot_fan_out(self):
        """The leg reads keys off Gold, so it always binds every PK column.
        A subset would fan out one key across every row sharing it."""
        sql = gold_cdc.build_driving_filter(
            self.KM, [("recompute_keys", ["inventory_item_id", "org_id"])])
        for col in self.KM:
            self.assertIn(f"k.`{col}`", sql, f"{col} was not bound")


class TestMergeAndDdl(unittest.TestCase):
    def test_gated_merge_gates_on_canonical(self):
        sql = gold_cdc.build_gated_merge_sql(
            "glue_catalog.`gold_product`.`product`",
            ["inventory_item_id", "org_id"])
        self.assertIn("WHEN MATCHED AND t.`_channel_hashes`['canonical'] "
                      "IS DISTINCT FROM", sql)
        self.assertIn("WHEN NOT MATCHED THEN INSERT *", sql)
        self.assertIn("t.`inventory_item_id` = s.`inventory_item_id`", sql)

    def test_changes_table_naming_and_location(self):
        self.assertEqual(gold_cdc.changes_table_name("product"),
                         "product_changes")
        self.assertEqual(
            gold_cdc.changes_table_location("s3://dal-gold-dev/product/"),
            "s3://dal-gold-dev/product_changes/")

    def test_changes_ddl_shape(self):
        ddl = gold_cdc.build_changes_table_ddl(
            "glue_catalog.`gold_product`", "product",
            ["inventory_item_id", "org_id"],
            {"inventory_item_id": "BIGINT", "org_id": "BIGINT"},
            "s3://dal-gold-dev/product_changes/")
        self.assertIn("CREATE TABLE IF NOT EXISTS", ddl)
        self.assertIn("`product_changes`", ddl)
        self.assertIn("`changed_channels` ARRAY<STRING>", ddl)
        self.assertIn("PARTITIONED BY (`change_date`)", ddl)
        self.assertIn("USING iceberg", ddl)


# ═════════════════════════════════════════════════════════════════
# PR-review remediation coverage
# ═════════════════════════════════════════════════════════════════
class TestParseBoolArg(unittest.TestCase):
    """#11 — Glue passes `--dry-run false`, so argparse store_true is wrong."""

    def test_true_tokens(self):
        for token in ("true", "True", "TRUE", "1", "yes", "y", "on", " true "):
            self.assertTrue(gold_cdc.parse_bool_arg(token, "dry-run"), token)

    def test_false_tokens(self):
        for token in ("false", "False", "0", "no", "n", "off", "", "  "):
            self.assertFalse(gold_cdc.parse_bool_arg(token, "dry-run"), token)

    def test_the_actual_bug_false_string_does_not_enable(self):
        # store_true would have made this True and armed a delete run.
        self.assertFalse(gold_cdc.parse_bool_arg("false", "dry-run"))

    def test_none_uses_default(self):
        self.assertFalse(gold_cdc.parse_bool_arg(None, "dry-run"))
        self.assertTrue(gold_cdc.parse_bool_arg(None, "dry-run", default=True))

    def test_bools_pass_through(self):
        self.assertTrue(gold_cdc.parse_bool_arg(True, "rebuild"))
        self.assertFalse(gold_cdc.parse_bool_arg(False, "rebuild"))

    def test_unrecognised_value_raises_rather_than_guessing(self):
        for token in ("ture", "maybe", "2", "-1"):
            with self.assertRaises(ValueError):
                gold_cdc.parse_bool_arg(token, "dry-run")

    def test_transform_and_reconcile_share_one_parser(self):
        # --rebuild and --dry-run must agree on what "false" means.
        self.assertEqual(gold_cdc.parse_bool_arg("false", "rebuild"),
                         gold_cdc.parse_bool_arg("false", "dry-run"))


class TestValidateSqlLiteral(unittest.TestCase):
    """MINOR — values interpolated into single-quoted Spark SQL literals."""

    def test_accepts_real_glue_run_ids(self):
        for run_id in ("jr_9f3c2b1a4d", "unknown", "jr_1", "jr_a-b.c:d+e"):
            self.assertEqual(
                gold_cdc.validate_sql_literal(run_id, "run_id"), run_id)

    def test_rejects_quote_and_statement_breakers_in_run_id(self):
        for bad in ("jr_1' OR '1'='1", "jr_1'; DROP TABLE x; --",
                    "jr_1\nSELECT", "jr_1 ", "jr\\_1", ""):
            with self.assertRaises(ValueError, msg=bad):
                gold_cdc.validate_sql_literal(bad, "run_id")

    def test_channel_names(self):
        for ch in ("api", "event", "message", "canonical", "data-feed", "v1_x"):
            self.assertEqual(gold_cdc.validate_sql_literal(ch, "channel"), ch)
        for bad in ("api'", "a b", "api;", "'"):
            with self.assertRaises(ValueError, msg=bad):
                gold_cdc.validate_sql_literal(bad, "channel")

    def test_timestamps_in_both_emitted_forms(self):
        for ts in ("2026-07-31 06:30:00", "2026-07-31T06:30:00",
                   "2026-07-31T06:30:00.123456", "2026-07-31T06:30:00+00:00",
                   "2026-07-31T06:30:00Z"):
            self.assertEqual(gold_cdc.validate_sql_literal(ts, "timestamp"), ts)
        for bad in ("2026-07-31", "now()", "2026-07-31 06:30:00'",
                    "2026-07-31 06:30:00; DELETE FROM t"):
            with self.assertRaises(ValueError, msg=bad):
                gold_cdc.validate_sql_literal(bad, "timestamp")

    def test_non_string_and_unknown_kind_raise(self):
        with self.assertRaises(ValueError):
            gold_cdc.validate_sql_literal(None, "run_id")
        with self.assertRaises(ValueError):
            gold_cdc.validate_sql_literal(42, "run_id")
        with self.assertRaises(ValueError):
            gold_cdc.validate_sql_literal("api", "not_a_kind")

    def test_validate_many_returns_list_and_rejects_any_bad_member(self):
        self.assertEqual(
            gold_cdc.validate_sql_literals(["api", "canonical"], "channel"),
            ["api", "canonical"])
        with self.assertRaises(ValueError):
            gold_cdc.validate_sql_literals(["api", "bad'"], "channel")


class TestLaggedWatermark(unittest.TestCase):
    """#5 — strict `>` with no overlap loses boundary rows permanently."""

    def test_rewinds_by_lag(self):
        self.assertEqual(
            gold_cdc.lagged_watermark("2026-07-31T06:30:00", 15),
            "2026-07-31T06:15:00")

    def test_crosses_hour_and_day_boundaries(self):
        self.assertEqual(
            gold_cdc.lagged_watermark("2026-07-31T00:05:00", 15),
            "2026-07-30T23:50:00")

    def test_zero_or_negative_lag_is_a_passthrough(self):
        wm = "2026-07-31T06:30:00"
        for lag in (0, None, -5):
            self.assertEqual(gold_cdc.lagged_watermark(wm, lag), wm)

    def test_accepts_space_separated_form(self):
        self.assertEqual(
            gold_cdc.lagged_watermark("2026-07-31 06:30:00", 30),
            "2026-07-31T06:00:00")

    def test_tz_aware_watermark_keeps_its_offset(self):
        self.assertEqual(
            gold_cdc.lagged_watermark("2026-07-31T06:30:00+00:00", 30),
            "2026-07-31T06:00:00+00:00")

    def test_never_rewinds_past_epoch(self):
        self.assertEqual(
            gold_cdc.lagged_watermark(gold_cdc.EPOCH_WATERMARK, 60),
            gold_cdc.EPOCH_WATERMARK)

    def test_malformed_watermark_fails_loudly(self):
        with self.assertRaises(ValueError):
            gold_cdc.lagged_watermark("not-a-timestamp", 15)

    def test_result_is_still_parseable_as_a_watermark(self):
        once = gold_cdc.lagged_watermark("2026-07-31T06:30:00", 15)
        self.assertEqual(gold_cdc.lagged_watermark(once, 15),
                         "2026-07-31T06:00:00")


class TestBookmarkUpdateArgs(unittest.TestCase):
    """ADR-0020 decision 1 / D6 step 3: one guarded write sets the bookmark,
    the snapshot map and run metadata, and REMOVEs the retired per-source
    map."""

    BM = "2026-09-23T06:30:12.000001+00:00"
    END = "2026-09-23T06:33:10.000000+00:00"

    def setUp(self):
        self.args = gold_cdc.build_bookmark_update_args(self.BM, "jr_1", self.END)

    def test_sets_the_bookmark_under_watermark(self):
        self.assertIn("#wm.#bm = :b", self.args["UpdateExpression"])
        names = self.args["ExpressionAttributeNames"]
        self.assertEqual(names["#wm"], "watermark")
        self.assertEqual(names["#bm"], gold_cdc.BOOKMARK_ATTR)
        self.assertEqual(self.args["ExpressionAttributeValues"][":b"], self.BM)

    def test_condition_only_allows_advancement(self):
        cond = self.args["ConditionExpression"]
        self.assertIn("attribute_not_exists(#wm.#bm)", cond)
        self.assertIn("#wm.#bm <= :b", cond)
        self.assertNotIn(">=", cond)

    def test_removes_the_retired_per_source_map(self):
        self.assertIn("REMOVE #wm.#legacy", self.args["UpdateExpression"])
        self.assertEqual(self.args["ExpressionAttributeNames"]["#legacy"],
                         gold_cdc.LEGACY_WATERMARK_ATTR)
        self.assertEqual(gold_cdc.LEGACY_WATERMARK_ATTR,
                         "last_processed_silver_updated_at")

    def test_never_writes_a_per_source_map(self):
        self.assertNotIn("#wm.#legacy =", self.args["UpdateExpression"])
        self.assertNotIn(":w", self.args["ExpressionAttributeValues"])

    def test_run_end_is_metadata_not_the_bookmark(self):
        update = self.args["UpdateExpression"]
        self.assertIn("#meta.last_run_id = :rid", update)
        self.assertIn("#meta.last_run_at = :ts", update)
        values = self.args["ExpressionAttributeValues"]
        self.assertEqual(values[":rid"], "jr_1")
        self.assertEqual(values[":ts"], self.END)
        self.assertEqual(values[":b"], self.BM)
        self.assertNotEqual(values[":b"], values[":ts"])

    def test_placeholders_are_all_declared(self):
        import re
        names = self.args["ExpressionAttributeNames"]
        values = self.args["ExpressionAttributeValues"]
        text = self.args["UpdateExpression"] + " " + self.args["ConditionExpression"]
        for token in set(re.findall(r"[#:][A-Za-z0-9_]+", text)):
            self.assertTrue(token in names or token in values, token)

    def test_idle_run_advances_nothing_but_still_cleans_and_records(self):
        # ADR-0020: "a run that processes nothing advances nothing" - but the
        # snapshot map, run metadata and the REMOVE still land
        args = gold_cdc.build_bookmark_update_args(
            None, "jr_2", self.END, snapshots={"a": "s3://m/1.json"})
        update = args["UpdateExpression"]
        self.assertNotIn("#bm", update)
        self.assertNotIn("ConditionExpression", args)
        self.assertIn("REMOVE #wm.#legacy", update)
        self.assertIn("#wm.#snap = :snap", update)
        self.assertIn("#meta.last_run_id = :rid", update)
        self.assertIn("#meta.last_run_at = :ts", update)

    def test_snapshots_and_recompute_ride_the_same_update(self):
        args = gold_cdc.build_bookmark_update_args(
            self.BM, "jr_x", self.END,
            snapshots={"items": "s3://m/00001.metadata.json"},
            recompute_at=self.BM)
        update = args["UpdateExpression"]
        self.assertIn("#wm.#snap = :snap", update)
        self.assertIn("#wm.#rc = :rc", update)
        self.assertEqual(args["ExpressionAttributeNames"]["#snap"], gold_cdc.SNAPSHOT_ATTR)
        self.assertEqual(args["ExpressionAttributeNames"]["#rc"], gold_cdc.RECOMPUTE_ATTR)
        self.assertEqual(args["ExpressionAttributeValues"][":snap"],
                         {"items": "s3://m/00001.metadata.json"})
        # the condition stays bookmark-based - snapshots add none
        self.assertNotIn(":snap", args["ConditionExpression"])

    def test_without_snapshots_no_snapshot_clause(self):
        for args in (self.args,
                     gold_cdc.build_bookmark_update_args(self.BM, "jr_x", self.END, snapshots={})):
            self.assertNotIn("#snap", args["UpdateExpression"])
            self.assertNotIn("#snap", args["ExpressionAttributeNames"])

    def test_expression_is_well_formed(self):
        # "SET a, b REMOVE c": one SET, one REMOVE, no dangling comma
        update = self.args["UpdateExpression"]
        self.assertTrue(update.startswith("SET "))
        self.assertEqual(update.count("SET "), 1)
        self.assertEqual(update.count("REMOVE "), 1)
        self.assertNotRegex(update, r",\s*REMOVE")


class TestIsMissingTableError(unittest.TestCase):
    """#9 — only a genuinely-absent change-log table may be swallowed."""

    def test_spark_analysis_exception_error_class(self):
        exc = Exception(
            "[TABLE_OR_VIEW_NOT_FOUND] The table or view "
            "`glue_catalog`.`uif_echo_dev_gold_product`.`product_changes` "
            "cannot be found.")
        self.assertTrue(gold_cdc.is_missing_table_error(exc, "product_changes"))

    def test_iceberg_no_such_table(self):
        exc = Exception("NoSuchTableException: Table does not exist: "
                        "uif_echo_dev_gold_product.product_changes")
        self.assertTrue(gold_cdc.is_missing_table_error(exc, "product_changes"))

    def test_glue_entity_not_found(self):
        exc = Exception("EntityNotFoundException: Table product_changes not found.")
        self.assertTrue(gold_cdc.is_missing_table_error(exc, "product_changes"))

    def test_backticked_and_qualified_names_resolve_to_the_bare_name(self):
        exc = Exception("[TABLE_OR_VIEW_NOT_FOUND] product_changes not found")
        for given in ("product_changes",
                      "`product_changes`",
                      "glue_catalog.`uif_echo_dev_gold_product`.`product_changes`"):
            self.assertTrue(gold_cdc.is_missing_table_error(exc, given), given)

    def test_real_failures_are_not_swallowed(self):
        for exc in (
            Exception("AccessDeniedException: not authorized to perform "
                      "glue:GetTable"),
            Exception("ThrottlingException: Rate exceeded"),
            Exception("[DATATYPE_MISMATCH] cannot cast STRING to ARRAY<STRING>"),
            Exception("S3 Slow Down"),
            Exception("Py4JJavaError: OutOfMemoryError"),
            Exception(""),
        ):
            self.assertFalse(
                gold_cdc.is_missing_table_error(exc, "product_changes"),
                str(exc))

    def test_a_different_missing_relation_is_not_swallowed(self):
        # The INSERT also reads the `orphans` temp view. If THAT is missing it
        # is a real bug and must abort — not be treated as a benign absence.
        exc = Exception("[TABLE_OR_VIEW_NOT_FOUND] The table or view "
                        "`orphans` cannot be found.")
        self.assertFalse(gold_cdc.is_missing_table_error(exc, "product_changes"))
        self.assertTrue(gold_cdc.is_missing_table_error(exc, "orphans"))

    def test_exception_class_name_is_considered(self):
        class NoSuchTableException(Exception):
            pass
        self.assertTrue(gold_cdc.is_missing_table_error(
            NoSuchTableException("product_changes"), "product_changes"))

    def test_without_a_table_name_marker_match_is_enough(self):
        exc = Exception("[TABLE_OR_VIEW_NOT_FOUND] whatever")
        self.assertTrue(gold_cdc.is_missing_table_error(exc))


class TestOrphanGraceCutoff(unittest.TestCase):
    """#1b — never hard-delete a row Gold has just written."""

    def setUp(self):
        from datetime import datetime, timezone
        self.started = datetime(2026, 7, 31, 3, 30, 0, tzinfo=timezone.utc)

    def test_default_one_hour_window(self):
        self.assertEqual(gold_cdc.orphan_grace_cutoff(self.started, 1),
                         "2026-07-31 02:30:00")

    def test_fractional_hours(self):
        self.assertEqual(gold_cdc.orphan_grace_cutoff(self.started, 0.5),
                         "2026-07-31 03:00:00")

    def test_zero_window_is_job_start(self):
        self.assertEqual(gold_cdc.orphan_grace_cutoff(self.started, 0),
                         "2026-07-31 03:30:00")

    def test_crosses_midnight(self):
        self.assertEqual(gold_cdc.orphan_grace_cutoff(self.started, 6),
                         "2026-07-30 21:30:00")

    def test_negative_window_rejected(self):
        with self.assertRaises(ValueError):
            gold_cdc.orphan_grace_cutoff(self.started, -1)

    def test_output_is_a_valid_spark_timestamp_literal(self):
        cutoff = gold_cdc.orphan_grace_cutoff(self.started, 1)
        self.assertEqual(
            gold_cdc.validate_sql_literal(cutoff, "timestamp"), cutoff)


class TestSnapshotSkip(unittest.TestCase):
    """UIFPE-256 — Phase 1 skips a source only on proven snapshot equality."""

    def test_equal_locations_skip(self):
        self.assertTrue(gold_cdc.source_snapshot_unchanged(
            {"a": "s3://m/v2.json"}, {"a": "s3://m/v2.json"}, "a"))

    def test_any_gap_fails_open_to_a_scan(self):
        cases = [
            ({"a": "s3://m/v1.json"}, {"a": "s3://m/v2.json"}),  # commit landed
            ({}, {"a": "s3://m/v2.json"}),                       # first run
            ({"a": "s3://m/v1.json"}, {}),                       # capture failed
            (None, {"a": "s3://m/v1.json"}),
            ({"a": ""}, {"a": ""}),                              # empty value
            ({"b": "s3://m/v1.json"}, {"a": "s3://m/v1.json"}),  # other alias
        ]
        for stored, captured in cases:
            with self.subTest(stored=stored, captured=captured):
                self.assertFalse(gold_cdc.source_snapshot_unchanged(
                    stored, captured, "a"))

    def test_stored_map_extraction(self):
        wm = {"last_processed_silver_updated_at": {"a": "2026-01-01"},
              "silver_snapshots": {"a": "s3://m/v1.json"}}
        self.assertEqual(gold_cdc.stored_silver_snapshots(wm),
                         {"a": "s3://m/v1.json"})
        # legacy / first-run shapes all fail open to {}
        for cfg in (None, "scalar", {}, {"silver_snapshots": "not-a-map"}):
            with self.subTest(cfg=cfg):
                self.assertEqual(gold_cdc.stored_silver_snapshots(cfg), {})

    def test_snapshot_gate_opt_out_disables_the_skip(self):
        # snapshot_gate: false is the operator escape hatch — it must force
        # real scans in the job too, not only in the dispatcher
        wm = {"silver_snapshots": {"a": "s3://m/v1.json"}}
        self.assertEqual(gold_cdc.stored_silver_snapshots(wm, gate=False), {})
        self.assertEqual(gold_cdc.stored_silver_snapshots(wm, gate=True),
                         {"a": "s3://m/v1.json"})

    def test_round_trip_with_the_state_writer(self):
        # what build_bookmark_update_args persists is what the skip reads
        args = gold_cdc.build_bookmark_update_args(
            "2026-01-01T00:00:00+00:00", "run1", "2026-01-01T00:05:00+00:00",
            snapshots={"a": "s3://m/v1.json"})
        stored = args["ExpressionAttributeValues"][":snap"]
        self.assertTrue(gold_cdc.source_snapshot_unchanged(
            stored, {"a": "s3://m/v1.json"}, "a"))
        # the document path the reader depends on, spelled out
        names = args["ExpressionAttributeNames"]
        self.assertEqual(names["#wm"], "watermark")
        self.assertEqual(names["#snap"], gold_cdc.SNAPSHOT_ATTR)

    def test_dispatcher_reads_the_same_attribute_name(self):
        # the dispatcher zip cannot import gold_cdc, so it hardcodes the
        # literal — pin the two spellings together
        src = (Path(__file__).resolve().parents[2] /
               "src/apps/lambdas/gold_dispatcher.py").read_text(
                   encoding="utf-8")
        self.assertIn(f'"{gold_cdc.SNAPSHOT_ATTR}"', src)


if __name__ == "__main__":
    unittest.main()
