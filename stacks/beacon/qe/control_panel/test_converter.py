# test_converter.py
# The Door 2 converter's behavioral contract (D11/D12). The first test is
# the exact Oracle query used in the UX prototype walkthrough — it must
# convert 5 expressions, flag the window function, and capture the WHERE.

import unittest

from control_panel import converter

ORACLE_SAMPLE = """
SELECT p.inventory_item_id AS product_id,
       p.productId,
       NVL(p.isbn_13, 'N/A') AS isbn13,
       p.description AS title,
       p.discipline_l1 || ' / ' || p.discipline_l2 AS discipline,
       ROW_NUMBER() OVER (PARTITION BY p.productId ORDER BY p.last_update_date DESC) rn
FROM   gold_product.product p
WHERE  p.item_type = 'BOOK'
"""


class TestOracleSample(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = converter.convert(ORACLE_SAMPLE)

    def test_five_mapped_one_unresolved(self):
        self.assertEqual(len(self.result["mappings"]), 5)
        self.assertEqual(len(self.result["unresolved"]), 1)

    def test_window_function_flagged_not_dropped(self):
        item = self.result["unresolved"][0]
        self.assertEqual(item["target"], "rn")
        self.assertIn("window", item["reason"])
        self.assertIn("ROW_NUMBER", item["expression"].upper())

    def test_source_captured_with_alias(self):
        self.assertEqual(self.result["sources"][0]["name"], "p")
        self.assertEqual(self.result["sources"][0]["registry_hint"],
                         "gold_product.product")

    def test_where_becomes_filter_with_d18_note(self):
        self.assertEqual(len(self.result["filters"]), 1)
        self.assertIn("item_type = 'BOOK'", self.result["filters"][0])
        self.assertTrue(any("D18" in n for n in self.result["notes"]))

    def test_oracle_idioms_transpile_to_spark(self):
        by_target = {m["target"]: m for m in self.result["mappings"]}
        # Oracle NVL normalizes to COALESCE (allowlisted; the exact behavior
        # the UX shows: "NVL -> coalesce")
        self.assertIn("COALESCE", by_target["isbn13"]["expression"].upper())
        # Oracle || becomes CONCAT in Spark
        self.assertIn("CONCAT", by_target["discipline"]["expression"].upper())
        self.assertNotIn("||", by_target["discipline"]["expression"])

    def test_plain_columns_become_one_to_one(self):
        by_target = {m["target"]: m for m in self.result["mappings"]}
        self.assertEqual(by_target["product_id"]["type"], "one_to_one")
        self.assertEqual(by_target["product_id"]["source"], "p.inventory_item_id")
        self.assertEqual(by_target["productId"]["type"], "one_to_one")


class TestJoins(unittest.TestCase):
    def test_join_with_condition(self):
        result = converter.convert(
            "SELECT o.id, c.name FROM orders o "
            "JOIN customers c ON o.customer_id = c.id WHERE o.total > 0")
        self.assertEqual(len(result["sources"]), 2)
        self.assertEqual(result["joins"][0]["type"], "inner")
        self.assertIn("o.customer_id = c.id", result["joins"][0]["condition"])

    def test_left_join_type(self):
        result = converter.convert(
            "SELECT o.id FROM orders o LEFT JOIN customers c ON o.cid = c.id")
        self.assertEqual(result["joins"][0]["type"], "left")

    def test_subquery_in_from_rejected(self):
        with self.assertRaises(converter.ConversionError):
            converter.convert("SELECT x.a FROM (SELECT a FROM t) x")

    # UIFPE-167: `left` names the table a join attaches to, not always the
    # FROM table — the engine folds in declaration order and the validator
    # checks the chain, so the converter must describe the chain truthfully.
    def test_chained_join_left_is_the_referenced_prior_table(self):
        result = converter.convert(
            "SELECT a.id FROM a JOIN b ON a.id = b.a_id JOIN c ON b.id = c.b_id")
        self.assertEqual([j["left"] for j in result["joins"]], ["a", "b"])
        self.assertEqual([j["right"] for j in result["joins"]], ["b", "c"])

    def test_condition_spanning_two_prior_tables_attaches_to_the_driver(self):
        result = converter.convert(
            "SELECT a.id FROM a JOIN b ON a.id = b.a_id "
            "JOIN c ON a.id = c.a_id AND b.id = c.b_id")
        self.assertEqual(result["joins"][1]["left"], "a")

    def test_missing_on_flags_that_join_and_keeps_the_rest(self):
        # before UIFPE-167 the converter RETURNED at the first join without
        # ON, silently dropping every later table (D12) — now each source is
        # still collected, the bad join is unresolved, the good one converts
        result = converter.convert(
            "SELECT a.id FROM a, b JOIN c ON a.id = c.a_id")
        self.assertEqual([s["name"] for s in result["sources"]], ["a", "b", "c"])
        self.assertEqual([j["right"] for j in result["joins"]], ["c"])
        self.assertEqual(len(result["unresolved"]), 1)
        self.assertIn("ON condition", result["unresolved"][0]["reason"])

    def test_cross_join_has_no_condition_key(self):
        result = converter.convert("SELECT a.id FROM a CROSS JOIN b")
        self.assertEqual(result["joins"],
                         [{"left": "a", "right": "b", "type": "cross"}])
        self.assertEqual(result["unresolved"], [])


class TestGuardrails(unittest.TestCase):
    def test_multiple_statements_rejected(self):
        with self.assertRaises(converter.ConversionError):
            converter.convert("SELECT 1 FROM t; SELECT 2 FROM u")

    def test_non_select_rejected(self):
        with self.assertRaises(converter.ConversionError):
            converter.convert("DELETE FROM products WHERE id = 1")

    def test_union_rejected(self):
        with self.assertRaises(converter.ConversionError):
            converter.convert("SELECT a FROM t UNION SELECT a FROM u")

    def test_select_star_flagged(self):
        result = converter.convert("SELECT * FROM gold_product.product p")
        self.assertEqual(result["mappings"], [])
        self.assertIn("SELECT *", result["unresolved"][0]["reason"])

    def test_scalar_subquery_flagged(self):
        result = converter.convert(
            "SELECT a, (SELECT max(b) FROM u) AS mb FROM t")
        targets = [u["target"] for u in result["unresolved"]]
        self.assertIn("mb", targets)

    def test_group_by_flagged(self):
        result = converter.convert(
            "SELECT category, count(1) AS n FROM t GROUP BY category")
        reasons = " ".join(u["reason"] for u in result["unresolved"])
        self.assertIn("GROUP BY", reasons)

    def test_order_and_limit_noted_not_flagged(self):
        result = converter.convert(
            "SELECT a FROM t ORDER BY a LIMIT 10")
        self.assertEqual(result["unresolved"], [])
        notes = " ".join(result["notes"])
        self.assertIn("ORDER BY", notes)
        self.assertIn("LIMIT", notes)

    def test_literals_become_literal_rules(self):
        result = converter.convert("SELECT 'crm' AS source_system, a FROM t")
        by_target = {m["target"]: m for m in result["mappings"]}
        self.assertEqual(by_target["source_system"]["type"], "literal")
        self.assertEqual(by_target["source_system"]["value"], "crm")


class TestDialects(unittest.TestCase):
    def test_explicit_dialect_respected(self):
        result = converter.convert(
            "SELECT TOP 5 name FROM products", dialect="tsql")
        self.assertEqual(result["dialect_detected"], "tsql")

    def test_unparseable_raises(self):
        with self.assertRaises(converter.ConversionError):
            converter.convert("this is not sql at all !!!")
