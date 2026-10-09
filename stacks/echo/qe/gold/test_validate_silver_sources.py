# test_validate_silver_sources.py
# Unit tests for scripts/validate_silver_sources.py — specifically the SQL
# column-contract parser, which is the one part of the preflight that can be
# wrong in a dangerous direction: a column it fails to extract is a column the
# validator never checks, producing a green preflight for a run that will fail
# in Glue. The AWS checks are thin get/head wrappers and are not mocked here.

import unittest
from pathlib import Path

import yaml

import validate_silver_sources as v

# DERIVED from the manifest, not hand-copied. The old copy listed nine aliases
# and the manifest had grown to fifteen, so every source added since went
# unchecked here - including items_ext_tl, which is why a genuinely ambiguous
# multi-source block passed unnoticed (UIFPE-476). A hand-copied list of a
# thing that grows is a vacuous PASS waiting to happen; the file said so itself.
_MANIFEST = (Path(__file__).resolve().parents[4] / "domains" / "product"
             / "src/schemas/mapping/product/v1/manifest.yaml")
ALIASES = [s["alias"] for s in
           yaml.safe_load(_MANIFEST.read_text(encoding="utf-8"))["sources"]]
SQL_PATH = (Path(__file__).resolve().parents[4] / "domains" / "product"
            / "src/schemas/mapping/product/v1/sql/product.sql")


class TestRequiredColumns(unittest.TestCase):

    def test_keywords_and_types_are_not_columns(self):
        sql = """
        WITH a AS (
          SELECT CAST(INVENTORY_ITEM_ID AS BIGINT) AS iid,
                 max(C_EXT_ATTR40) AS lineOfBusiness
          FROM items_ext WHERE ATTR_GROUP_ID = 802 GROUP BY 1
        )
        SELECT DESCRIPTION FROM items
        """
        cols, ambiguous = v.required_columns(sql, ALIASES)
        self.assertEqual(cols["items_ext"],
                         {"INVENTORY_ITEM_ID", "C_EXT_ATTR40",
                          "ATTR_GROUP_ID"})
        self.assertEqual(cols["items"], {"DESCRIPTION"})
        self.assertEqual(ambiguous, [])

    def test_string_literals_are_not_columns(self):
        sql = """
        WITH a AS (
          SELECT INVENTORY_ITEM_ID, LONG_DESCRIPTION
          FROM items_tl WHERE LANGUAGE = 'US' GROUP BY 1, 2
        )
        SELECT 1 FROM items
        """
        cols, _ = v.required_columns(sql, ALIASES)
        self.assertNotIn("US", cols["items_tl"])
        self.assertIn("LANGUAGE", cols["items_tl"])

    def test_commented_out_columns_are_not_requirements(self):
        sql = """
        WITH a AS (
          -- max(C_EXT_ATTR99) AS retired,
          SELECT INVENTORY_ITEM_ID FROM items_ext GROUP BY 1
        )
        SELECT 1 FROM items
        """
        cols, _ = v.required_columns(sql, ALIASES)
        self.assertNotIn("C_EXT_ATTR99", cols["items_ext"])

    def test_qualified_references_are_attributed_to_their_alias(self):
        sql = """
        WITH a AS (SELECT 1 AS x FROM items_ext)
        SELECT items.SEGMENT1 FROM items LEFT JOIN a ON a.x = 1
        """
        cols, _ = v.required_columns(sql, ALIASES)
        self.assertIn("SEGMENT1", cols["items"])

    def test_block_reading_two_sources_is_reported_not_guessed(self):
        sql = """
        WITH a AS (
          SELECT INVENTORY_ITEM_ID, SOME_COL
          FROM items JOIN items_tl ON 1 = 1
        )
        SELECT 1 FROM items
        """
        cols, ambiguous = v.required_columns(sql, ALIASES)
        self.assertTrue(ambiguous)
        # SOME_COL is not silently assigned to either side.
        self.assertNotIn("SOME_COL", cols["items"])
        self.assertNotIn("SOME_COL", cols["items_tl"])

    def test_nested_parens_do_not_truncate_a_cte_body(self):
        # A non-greedy regex split would end the CTE at the CAST's ")" and
        # miss every column after it.
        sql = """
        WITH a AS (
          SELECT CAST(INVENTORY_ITEM_ID AS BIGINT) AS iid,
                 COALESCE(max(C_EXT_ATTR1), max(C_EXT_ATTR2)) AS v,
                 LAST_COL
          FROM items_ext GROUP BY 1
        )
        SELECT 1 FROM items
        """
        cols, _ = v.required_columns(sql, ALIASES)
        self.assertIn("LAST_COL", cols["items_ext"])


class TestAgainstRealMappingSql(unittest.TestCase):
    """Pins the parser against the mapping SQL actually shipped, so a future
    edit to product.sql that introduces a source column cannot silently escape
    the preflight."""

    @classmethod
    def setUpClass(cls):
        cls.cols, cls.ambiguous = v.required_columns(
            SQL_PATH.read_text(encoding="utf-8"), ALIASES)

    def test_no_ambiguous_blocks(self):
        """Every multi-source block in the real SQL is fully qualified, so the
        preflight can attribute all of it. product's instructorResourceFiles
        (UIFPE-476) is the only such block today: it joins the base and
        translations EAV tables, and qualifying its columns with the REGISTERED
        alias names is what lets EXTENSION_ID be checked at all."""
        self.assertEqual(self.ambiguous, [])

    def test_the_ambiguity_check_still_fires_on_an_unqualified_block(self):
        """CONTROL for the test above. Relaxing the check to allow a fully
        qualified multi-source block must not make it unable to fire: a block
        that reads two sources AND leaves a bare column is still unattributable,
        and a gate that can no longer complain is not a gate."""
        sql = ("WITH bad AS (\n"
               "  SELECT INVENTORY_ITEM_ID, SOME_COLUMN\n"
               "  FROM items_ext\n"
               "  LEFT JOIN items_ext_tl ON 1 = 1\n"
               ")\nSELECT * FROM bad")
        _cols, ambiguous = v.required_columns(sql, ["items_ext", "items_ext_tl"])
        self.assertEqual(len(ambiguous), 1, ambiguous)
        self.assertIn("SOME_COLUMN", ambiguous[0])

    def test_a_fully_qualified_multi_source_block_is_not_ambiguous(self):
        """The other half of the control: the same shape with every column
        qualified resolves cleanly and attributes each column to its own
        source, which is the behaviour UIFPE-476 needed."""
        sql = ("WITH good AS (\n"
               "  SELECT items_ext.INVENTORY_ITEM_ID, items_ext_tl.TL_EXT_ATTR1\n"
               "  FROM items_ext\n"
               "  LEFT JOIN items_ext_tl\n"
               "         ON items_ext.EXTENSION_ID = items_ext_tl.EXTENSION_ID\n"
               ")\nSELECT * FROM good")
        cols, ambiguous = v.required_columns(sql, ["items_ext", "items_ext_tl"])
        self.assertEqual(ambiguous, [])
        self.assertIn("EXTENSION_ID", cols["items_ext"])
        self.assertIn("TL_EXT_ATTR1", cols["items_ext_tl"])
        self.assertNotIn("TL_EXT_ATTR1", cols["items_ext"])

    def test_every_source_is_read_by_the_sql(self):
        for alias in ALIASES:
            self.assertTrue(self.cols[alias],
                            f"{alias} resolved to zero columns")

    def test_pk_columns_required_from_the_source_of_truth(self):
        self.assertIn("INVENTORY_ITEM_ID", self.cols["items"])
        self.assertIn("ORGANIZATION_ID", self.cols["items"])

    def test_eav_pivot_keys_required_from_the_extension_table(self):
        # The branch discriminator lives on the extension table in this mapping;
        # since 2026-09-15 the driving table also exposes the catalog group as
        # a column of its own (itemCatalogGroupId), so both must be required.
        self.assertIn("ATTR_GROUP_ID", self.cols["items_ext"])
        self.assertIn("ITEM_CATALOG_GROUP_ID", self.cols["items_ext"])
        self.assertIn("ITEM_CATALOG_GROUP_ID", self.cols["items"])

    def test_translation_columns_required_from_the_tl_table(self):
        self.assertEqual(
            self.cols["items_tl"],
            {"INVENTORY_ITEM_ID", "ORGANIZATION_ID", "LANGUAGE",
             "LONG_DESCRIPTION"})

    def test_org_dimension_columns_required(self):
        # The org dimension supplies organizationCode and, via the '01' prefix,
        # the UK proliferation group. If the preflight stops demanding
        # ORGANIZATION_CODE, a Silver schema change that drops it produces a
        # green preflight and a NULL column for every Gold row.
        # MASTER_ORGANIZATION_ID joined the set on 2026-08-31: `categories`
        # reads the master org as a RULE off this dimension instead of
        # hard-coding the dev id 82, so dropping that column upstream would
        # empty the column for every row.
        self.assertEqual(self.cols["org_params"],
                         {"ORGANIZATION_ID", "ORGANIZATION_CODE",
                          "MASTER_ORGANIZATION_ID"})

    def test_category_source_columns_required(self):
        # Five tables behind one column; each contributes something the SQL
        # cannot do without. Pinned so a Silver schema change upstream fails
        # the preflight rather than emptying part of the struct.
        self.assertEqual(
            self.cols["item_categories"],
            {"INVENTORY_ITEM_ID", "ORGANIZATION_ID", "CATEGORY_SET_ID",
             "CATEGORY_ID"})
        self.assertEqual(self.cols["category_sets"],
                         {"CATEGORY_SET_ID", "CATEGORY_SET_NAME"})
        self.assertEqual(
            self.cols["category_valid_cats"],
            {"CATEGORY_SET_ID", "CATEGORY_ID", "PARENT_CATEGORY_ID"})
        self.assertEqual(self.cols["categories_tl"],
                         {"CATEGORY_ID", "LANGUAGE", "DESCRIPTION"})
        # ATTRIBUTE8 / DISABLE_DATE are the business-requested attributes of
        # the mapped category (isIncludeInECatalog / disableDate).
        self.assertEqual(
            self.cols["categories_b"],
            {"CATEGORY_ID", "SEGMENT1", "ATTRIBUTE8", "DISABLE_DATE"})

    def test_dimension_columns_required_from_items(self):
        for col in ("UNIT_HEIGHT", "UNIT_LENGTH", "UNIT_WIDTH", "UNIT_WEIGHT",
                    "DIMENSION_UOM_CODE", "WEIGHT_UOM_CODE"):
            self.assertIn(col, self.cols["items"])

    def test_gold_output_names_are_not_mistaken_for_source_columns(self):
        # Gold columns are camelCase; none should appear as a requirement.
        for alias in ALIASES:
            for col in self.cols[alias]:
                self.assertEqual(col, col.upper(),
                                 f"{col} looks like a Gold output name")


if __name__ == "__main__":
    unittest.main()
