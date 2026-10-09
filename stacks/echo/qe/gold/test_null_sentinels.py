# test_null_sentinels.py
# UIFPE-268: the manifest declares Oracle's placeholder values once and the
# transform re-projects every Silver view so they read as NULL. Pure
# functions only — the SQL text is asserted, no Spark.

import unittest

import gold_cdc

DECL = {"null_sentinels": {"string": ["TBD", "N/A"],
                           "numeric": [99998, 99999]}}


class TestValidateNullSentinels(unittest.TestCase):
    def test_absent_block_means_no_cleaning(self):
        self.assertIsNone(gold_cdc.validate_null_sentinels({}))
        self.assertIsNone(gold_cdc.validate_null_sentinels({"sources": []}))

    def test_normalizes_the_declared_shape(self):
        got = gold_cdc.validate_null_sentinels(DECL)
        self.assertEqual(got, {"string": ["TBD", "N/A"],
                               "numeric": [99998, 99999],
                               "exclude_columns": []})

    def test_one_kind_alone_is_enough(self):
        got = gold_cdc.validate_null_sentinels(
            {"null_sentinels": {"string": ["TBD"]}})
        self.assertEqual(got["numeric"], [])

    def test_empty_block_is_refused(self):
        with self.assertRaises(ValueError):
            gold_cdc.validate_null_sentinels({"null_sentinels": {}})
        with self.assertRaises(ValueError):
            gold_cdc.validate_null_sentinels(
                {"null_sentinels": {"string": [], "numeric": []}})

    def test_unknown_keys_and_wrong_shapes_are_refused(self):
        for bad in ({"strings": ["TBD"]},          # typo'd key
                    {"string": "TBD"},             # not a list
                    {"numeric": ["99999"]},        # numbers as strings
                    {"numeric": [True]},           # bool is not a number
                    {"numeric": [float("nan")]},
                    {"string": ["it's"]},          # would break the literal
                    {"string": ["a\\b"]},
                    {"string": [""]},
                    {"exclude_columns": ["bad-name"], "string": ["TBD"]}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                gold_cdc.validate_null_sentinels({"null_sentinels": bad})
        with self.assertRaises(ValueError):
            gold_cdc.validate_null_sentinels({"null_sentinels": ["TBD"]})


class TestProtectedColumns(unittest.TestCase):
    def test_identity_and_discriminator_names_are_protected(self):
        for name in ("INVENTORY_ITEM_ID", "organization_id", "ATTR_GROUP_ID",
                     "ITEM_CATALOG_GROUP_ID", "ITEM_NUM", "OPERATION_SEQ_NUM",
                     "_silver_updated_at", "_any_metadata"):
            self.assertTrue(gold_cdc.is_sentinel_protected(name), name)

    def test_value_columns_are_not(self):
        for name in ("C_EXT_ATTR1", "SEGMENT1", "DESCRIPTION", "N_EXT_ATTR5",
                     "UNIT_WEIGHT", "STRUCTURE_TYPE_NAME", "IDENTITY"):
            self.assertFalse(gold_cdc.is_sentinel_protected(name), name)

    def test_explicit_protection_is_case_insensitive(self):
        self.assertTrue(gold_cdc.is_sentinel_protected(
            "Language", protected=["LANGUAGE"]))


class TestSourceProtectedColumns(unittest.TestCase):
    """The blocker from the UIFPE-268 review: string KEY columns that do not
    end in _ID. A cleaned key hides the row from Phase 2's key filter and
    makes reconcile's anti-join flag it forever — so protection comes from
    the source's own contract, not from a naming rule."""

    def test_price_and_stock_keys_are_protected_by_contract(self):
        price = {"alias": "price", "watermark_col": "_silver_updated_at",
                 "key_mapping": {"ppid": "PPID"},
                 "sot_pk_mapping": {"ppid": "PPID"},
                 "sot_filter": "PPID IS NOT NULL"}
        self.assertEqual(gold_cdc.source_protected_columns(price),
                         ["PPID", "_silver_updated_at"])
        stock = {"alias": "stock",
                 "key_mapping": {"ppid": "PPID",
                                 "organizationCode": "ORGANIZATION_CODE"}}
        self.assertEqual(gold_cdc.source_protected_columns(stock),
                         ["ORGANIZATION_CODE", "PPID", "_silver_updated_at"])

    def test_identifiers_are_lifted_out_of_expressions_and_filters(self):
        src = {"alias": "items", "watermark_col": "_silver_updated_at",
               "key_mapping": {"inventoryItemId": "CAST(INVENTORY_ITEM_ID AS BIGINT)",
                               "organizationId": "CAST(ORGANIZATION_ID AS BIGINT)"},
               "sot_filter": "SEGMENT1 LIKE 'A%' AND STATUS NOT IN ('X')"}
        got = gold_cdc.source_protected_columns(src)
        for name in ("INVENTORY_ITEM_ID", "ORGANIZATION_ID", "SEGMENT1",
                     "STATUS", "_silver_updated_at"):
            self.assertIn(name, got)

    def test_the_plumbing_keeps_a_contract_column_raw(self):
        # a string key with no _id suffix, on a table where TBD appears
        fields = [("PPID", "string"), ("NET_PRICE", "decimal(38,10)"),
                  ("LIST_NAME", "string"), ("loaded_at", "timestamp")]
        src = {"alias": "price", "watermark_col": "loaded_at",
               "key_mapping": {"ppid": "PPID"}}
        sent = gold_cdc.validate_null_sentinels(DECL)
        exprs, cleaned = gold_cdc.sentinel_select_exprs(
            fields, sent, protected=gold_cdc.source_protected_columns(src),
            alias="price")
        self.assertEqual(cleaned, ["NET_PRICE", "LIST_NAME"])
        self.assertEqual(exprs[0], "`PPID`")
        self.assertEqual(exprs[3], "`loaded_at`")


class TestSentinelSelectExprs(unittest.TestCase):
    FIELDS = [("INVENTORY_ITEM_ID", "decimal(38,10)"),
              ("ATTR_GROUP_ID", "decimal(38,10)"),
              ("C_EXT_ATTR1", "string"),
              ("N_EXT_ATTR1", "decimal(38,10)"),
              ("UNIT_WEIGHT", "double"),
              ("PAGE_COUNT", "int"),
              ("D_EXT_ATTR1", "timestamp"),
              ("ACTIVE", "boolean"),
              ("_silver_updated_at", "timestamp")]

    def setUp(self):
        self.sent = gold_cdc.validate_null_sentinels(DECL)

    def test_every_column_survives_in_order_under_its_own_name(self):
        exprs, _ = gold_cdc.sentinel_select_exprs(
            self.FIELDS, self.sent, protected=["_silver_updated_at"])
        self.assertEqual(len(exprs), len(self.FIELDS))
        for (name, _), e in zip(self.FIELDS, exprs):
            self.assertTrue(e == f"`{name}`" or e.endswith(f" AS `{name}`"),
                            (name, e))

    def test_only_value_columns_of_a_declared_kind_are_wrapped(self):
        exprs, cleaned = gold_cdc.sentinel_select_exprs(
            self.FIELDS, self.sent, protected=["_silver_updated_at"])
        self.assertEqual(cleaned, ["C_EXT_ATTR1", "N_EXT_ATTR1",
                                   "UNIT_WEIGHT", "PAGE_COUNT"])
        by_name = dict(zip((f for f, _ in self.FIELDS), exprs))
        self.assertEqual(
            by_name["C_EXT_ATTR1"],
            "CASE WHEN `C_EXT_ATTR1` IN ('TBD', 'N/A') THEN NULL "
            "ELSE `C_EXT_ATTR1` END AS `C_EXT_ATTR1`")
        self.assertEqual(
            by_name["N_EXT_ATTR1"],
            "CASE WHEN `N_EXT_ATTR1` IN (99998, 99999) THEN NULL "
            "ELSE `N_EXT_ATTR1` END AS `N_EXT_ATTR1`")
        # keys, discriminators, dates, booleans, metadata: passed through bare
        for untouched in ("INVENTORY_ITEM_ID", "ATTR_GROUP_ID", "D_EXT_ATTR1",
                          "ACTIVE", "_silver_updated_at"):
            self.assertEqual(by_name[untouched], f"`{untouched}`")

    def test_a_kind_with_no_declared_values_is_left_alone(self):
        strings_only = gold_cdc.validate_null_sentinels(
            {"null_sentinels": {"string": ["TBD"]}})
        _, cleaned = gold_cdc.sentinel_select_exprs(self.FIELDS, strings_only)
        self.assertEqual(cleaned, ["C_EXT_ATTR1"])

    def test_exclude_columns_from_the_manifest_are_honoured(self):
        sent = gold_cdc.validate_null_sentinels(
            {"null_sentinels": {"string": ["TBD"],
                                "exclude_columns": ["c_ext_attr1"]}})
        _, cleaned = gold_cdc.sentinel_select_exprs(self.FIELDS, sent)
        self.assertEqual(cleaned, [])

    def test_alias_scoped_exclusions_apply_to_that_source_only(self):
        # SEGMENT1 is the ppid on items but a category CODE on categories_b
        sent = gold_cdc.validate_null_sentinels(
            {"null_sentinels": {"string": ["N/A"],
                                "exclude_columns": ["categories_b.SEGMENT1"]}})
        fields = [("SEGMENT1", "string")]
        _, on_dim = gold_cdc.sentinel_select_exprs(fields, sent,
                                                   alias="categories_b")
        _, on_items = gold_cdc.sentinel_select_exprs(fields, sent,
                                                     alias="items")
        self.assertEqual(on_dim, [])
        self.assertEqual(on_items, ["SEGMENT1"])

    def test_string_types_with_length_and_integer_widths_are_recognised(self):
        fields = [("A", "varchar(40)"), ("B", "char(1)"), ("C", "bigint"),
                  ("D", "smallint"), ("E", "tinyint"), ("F", "float"),
                  ("G", "decimal(10,2)"), ("H", "date"), ("I", "binary")]
        _, cleaned = gold_cdc.sentinel_select_exprs(fields, self.sent)
        self.assertEqual(cleaned, ["A", "B", "C", "D", "E", "F", "G"])

    def test_interval_and_complex_types_are_never_numeric(self):
        for t in ("interval day to second", "interval year to month",
                  "interval", "array<int>", "struct<a:int>", "map<string,int>"):
            self.assertIsNone(gold_cdc._sentinel_kind(t), t)


if __name__ == "__main__":
    unittest.main()
