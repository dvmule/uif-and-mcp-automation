# test_gold_catalog.py
# Domain-first source discovery (UIFPE-229): the wizard's picker reads the
# Glue catalog live — domains from BEACON_GOLD_DATABASES, tables per domain,
# incremental eligibility from the platform CDC column. Fake clients, no AWS.

import os
import unittest

from control_panel import registries
from gold_fixtures import CATALOG, FakeGlue  # shared, non-test module


class TestGoldCatalog(unittest.TestCase):
    def setUp(self):
        os.environ["BEACON_GOLD_DATABASES"] = \
            "uif_echo_dev_gold_product, uif_echo_dev_gold_program"
        registries.set_clients_for_testing(FakeGlue(CATALOG), object())

    def tearDown(self):
        os.environ.pop("BEACON_GOLD_DATABASES", None)
        registries.set_clients_for_testing(None, None)

    def test_domains_come_from_the_env_list(self):
        self.assertEqual(registries.gold_domains(), [
            {"domain": "product", "database": "uif_echo_dev_gold_product"},
            {"domain": "program", "database": "uif_echo_dev_gold_program"},
        ])

    def test_tables_are_listed_live_with_incremental_eligibility(self):
        tables = {t["id"]: t for t in registries.gold_tables("product")}
        # product_changes is a CDC change log and is NOT offered (UIFPE-369)
        self.assertEqual(set(tables), {"product.product"})
        product = tables["product.product"]
        self.assertEqual(product["path"], "glue_catalog.uif_echo_dev_gold_product.product")
        self.assertEqual(product["kind"], "iceberg")
        # CDC column present => incremental ready; metadata columns not counted
        self.assertEqual(product["watermark_column"], "_gold_updated_at")
        self.assertEqual(product["columns_count"], 2)

    def test_a_table_carries_what_a_row_is_from_the_registry(self):
        # UIFPE-607: registry_data/tables.yaml says what one row of a Gold
        # table is and which column narrows it; the wizard shows those words.
        # A table without an entry says nothing.
        registries.set_clients_for_testing(FakeGlue({"uif_echo_dev_gold_product": {
            "product": ["product_id", "organization_code", "_gold_updated_at"],
            "pending_changes": ["product_id"],
        }}), object())
        tables = {t["id"]: t for t in registries.gold_tables("product")}
        self.assertEqual(tables["product.product"]["row"], "one row per product per organization")
        self.assertEqual(tables["product.product"]["narrow_by"], ["organization_code"])
        self.assertNotIn("row", tables["product.pending_changes"])
        # the live record's own fields are never overridden by the registry
        self.assertEqual(tables["product.product"]["path"], "glue_catalog.uif_echo_dev_gold_product.product")

    def test_cdc_change_logs_are_not_offered_as_sources(self):
        # UIFPE-369: a change log carries the entity key plus a verdict and no
        # attributes, so a feed driven by one delivers nothing a partner wants.
        # Its own catalog, not the shared fixture: the guard case below needs a
        # table no other suite should have to know about.
        registries.set_clients_for_testing(FakeGlue({"uif_echo_dev_gold_product": {
            "product": ["product_id", "_gold_updated_at"],
            "product_changes": ["product_id", "op", "run_id", "_gold_updated_at"],
            "pending_changes": ["product_id", "requested_by"],
        }}), object())
        ids = {t["id"] for t in registries.gold_tables("product")}
        self.assertNotIn("product.product_changes", ids)
        # derived, not a blanket suffix match: "pending_changes" has no
        # "pending" table beside it, so it is an entity and keeps its card
        self.assertEqual(ids, {"product.product", "product.pending_changes"})
        # hidden from the PICKER only — the id still resolves, which is what
        # keeps the designed full-CDC slice able to read one
        self.assertEqual(
            registries.source_id_for_path(
                "glue_catalog.uif_echo_dev_gold_product.product_changes"),
            "product.product_changes")

    def test_unknown_domain_is_none(self):
        self.assertIsNone(registries.gold_tables("finance"))

    def test_configured_database_not_created_yet_lists_no_tables(self):
        # UIFPE-167: a domain can be put in the picker ahead of its first
        # gold load (price/stock). Glue answers EntityNotFoundException for
        # the missing database — that is an empty domain, not an outage.
        os.environ["BEACON_GOLD_DATABASES"] += ", uif_echo_dev_gold_stock"
        self.assertEqual(registries.gold_tables("stock"), [])
        # any other failure still propagates (the API turns it into a 503)
        registries.set_clients_for_testing(FakeGlue(CATALOG, fail=True), object())
        with self.assertRaises(RuntimeError):
            registries.gold_tables("stock")

    def test_columns_resolve_for_catalog_discovered_ids(self):
        # no sources.yaml row exists for program.program — the id itself
        # resolves against the configured databases
        result = registries.source_columns("program.program")
        self.assertEqual(result["provenance"], "live")
        self.assertEqual(result["columns"], ["program_id", "name"])  # _-cols stripped

    def test_watermark_lookup_prefers_the_registry_row(self):
        # sources.yaml's gold_product.product row declares the column
        column = registries.watermark_column_for_path(
            "glue_catalog.eds_dev_gold_product.product")
        self.assertEqual(column, "_gold_updated_at")

    def test_watermark_lookup_falls_back_to_the_live_catalog(self):
        self.assertEqual(
            registries.watermark_column_for_path(
                "glue_catalog.uif_echo_dev_gold_program.program"),
            "_gold_updated_at")
        self.assertIsNone(registries.watermark_column_for_path(
            "glue_catalog.uif_echo_dev_gold_product.product_changes"))

    def test_watermark_lookup_refuses_unconfigured_databases(self):
        self.assertIsNone(registries.watermark_column_for_path(
            "glue_catalog.someone_elses_db.table"))


class TestGoldCatalogRoutes(unittest.TestCase):
    def setUp(self):
        os.environ["BEACON_GOLD_DATABASES"] = "uif_echo_dev_gold_product"
        registries.set_clients_for_testing(FakeGlue(CATALOG), object())
        from fastapi.testclient import TestClient
        from control_panel.api import app
        self.client = TestClient(app)

    def tearDown(self):
        os.environ.pop("BEACON_GOLD_DATABASES", None)
        registries.set_clients_for_testing(None, None)

    def test_domains_endpoint(self):
        response = self.client.get("/registries/domains")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["domains"],
                         [{"domain": "product", "database": "uif_echo_dev_gold_product"}])

    def test_tables_endpoint_and_unknown_domain(self):
        response = self.client.get("/registries/domains/product/tables")
        self.assertEqual(response.status_code, 200)
        # UIFPE-369: the change log never reaches the client
        self.assertEqual({t["id"] for t in response.json()["tables"]},
                         {"product.product"})
        self.assertEqual(
            self.client.get("/registries/domains/finance/tables").status_code, 404)

    def test_catalog_outage_is_a_503_not_an_empty_list(self):
        registries.set_clients_for_testing(FakeGlue(fail=True), object())
        response = self.client.get("/registries/domains/product/tables")
        self.assertEqual(response.status_code, 503)
        self.assertIn("catalog is unavailable", response.json()["detail"])

    def test_database_not_created_yet_is_200_with_no_tables(self):
        # UIFPE-167: distinct from an outage — the UI explains "no gold
        # tables have landed yet" instead of failing the domain
        os.environ["BEACON_GOLD_DATABASES"] = "uif_echo_dev_gold_product, uif_echo_dev_gold_stock"
        response = self.client.get("/registries/domains/stock/tables")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["tables"], [])


if __name__ == "__main__":
    unittest.main()
