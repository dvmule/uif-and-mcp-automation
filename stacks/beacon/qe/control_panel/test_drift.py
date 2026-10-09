# test_drift.py
# UIFPE-220: gold drift detection — a published version's frozen table paths
# and column names checked against the live catalog. Fake Glue, no AWS. The
# three fixture shapes mirror the real incidents of 8–15 Sep 2026: a renamed
# column, a vanished table, and a migrated (de-configured) database.

import os
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from control_panel import drift, lifecycle, registries, validation
from control_panel.api import app

client = TestClient(app)

DB = "uif_echo_test_gold_product"


class FakeGlue:
    """get_table/get_tables over an in-memory {table: [columns]} catalog —
    or {table: {column: type}} when a test needs the types (UIFPE-605)."""

    class EntityNotFoundException(Exception):
        pass

    def __init__(self, tables):
        self.tables = tables  # {name: [columns] | {column: type}}

    def _described(self, name):
        cols = self.tables[name]
        typed = cols.items() if isinstance(cols, dict) else ((c, "string") for c in cols)
        return {"Name": name,
                "StorageDescriptor": {"Columns": [{"Name": c, "Type": t} for c, t in typed]}}

    def get_table(self, DatabaseName, Name):
        if DatabaseName != DB or Name not in self.tables:
            raise self.EntityNotFoundException()
        return {"Table": self._described(Name)}

    def get_tables(self, DatabaseName, **kwargs):
        if DatabaseName != DB:
            raise self.EntityNotFoundException()
        return {"TableList": [self._described(n) for n in self.tables]}


def _version(job_id="100777", path=f"glue_catalog.{DB}.product",
             source_col="product_id", filters=None):
    return {"job_id": job_id, "name": "drift-fixture", "version": 3,
            "mapping_config": {
                "version": "1.0",
                "sources": [{"name": "product", "path": path,
                             "format": "iceberg"}],
                "joins": [],
                "filters": filters or [],
                "mappings": [{"type": "one_to_one",
                              "source": f"product.{source_col}",
                              "target": "productId"}]}}


class DriftCase(unittest.TestCase):
    def setUp(self):
        self.glue = FakeGlue({"product": ["product_id", "isbn13"]})
        registries.set_clients_for_testing(self.glue, None)
        os.environ["BEACON_GOLD_DATABASES"] = DB

    def tearDown(self):
        registries.set_clients_for_testing(None, None)
        os.environ.pop("BEACON_GOLD_DATABASES", None)


class TestCheckVersion(DriftCase):
    def test_healthy_version_is_ok(self):
        result = drift.check_version(_version())
        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(result["issues"], [])
        # column-level verification ran (engine present in the test venv)
        self.assertEqual(result["columns_checked"], validation.ENGINE_AVAILABLE)

    def test_renamed_column_is_drift(self):
        # the 13-Sep incident: gold renamed a column under a frozen config
        result = drift.check_version(_version(source_col="ppid"))
        if not validation.ENGINE_AVAILABLE:
            self.skipTest("column checks need the engine")
        self.assertEqual(result["status"], "drift")
        self.assertTrue(any("ppid" in i["message"] for i in result["issues"]))

    def test_vanished_table_is_drift(self):
        # the marketing_copy shape: database configured, table gone
        result = drift.check_version(
            _version(path=f"glue_catalog.{DB}.marketing_copy"))
        self.assertEqual(result["status"], "drift")
        self.assertIn("no longer exists", result["issues"][0]["message"])

    def test_migrated_database_is_drift(self):
        # the 15-Sep incident: the config's database is no longer configured
        result = drift.check_version(
            _version(path="glue_catalog.echo_dev_gold_product.product"))
        self.assertEqual(result["status"], "drift")
        self.assertIn("not in any configured gold database",
                      result["issues"][0]["message"])

    def test_filter_on_gone_column_is_drift(self):
        if not validation.ENGINE_AVAILABLE:
            self.skipTest("column checks need the engine")
        result = drift.check_version(
            _version(filters=["product.ppid = 'A1'"]))
        self.assertEqual(result["status"], "drift")

    def _list_version(self, field):
        # the dev shape of 2026-09-25: product + price joined, the price
        # specifications list spread as price_spec and its fields mapped
        version = _version()
        cfg = version["mapping_config"]
        cfg["sources"].append({"name": "price", "path": f"glue_catalog.{DB}.price", "format": "iceberg"})
        cfg["joins"] = [{"left": "product", "right": "price", "type": "left",
                         "condition": "product.product_id = price.product_id"}]
        cfg["unnest"] = [{"list": "price.price_specifications", "alias": "price_spec"}]
        cfg["mappings"].append({"type": "one_to_one", "source": f"price_spec.{field}", "target": "listName"})
        return version

    def test_list_element_mappings_are_not_drift(self):
        # UIFPE-605: the check read column names only, so every price_spec.*
        # mapping was "list element has no known fields" — a false alarm the
        # publish-time validation and the run itself never raised
        if not validation.ENGINE_AVAILABLE:
            self.skipTest("column checks need the engine")
        registries.set_clients_for_testing(FakeGlue({
            "product": ["product_id", "isbn13"],
            "price": {"product_id": "string",
                      "price_specifications": "array<struct<price_list_name:string,list_price_amount:decimal(38, 2)>>"},
        }), None)
        result = drift.check_version(self._list_version("price_list_name"))
        self.assertEqual(result["status"], "ok", result)
        # a field the list's records do not carry is still drift
        result = drift.check_version(self._list_version("gone_field"))
        self.assertEqual(result["status"], "drift")
        self.assertTrue(any("gone_field" in i["message"] for i in result["issues"]), result)

    def test_catalog_outage_is_unchecked_not_drift(self):
        class DownGlue:
            def get_table(self, DatabaseName, Name):
                raise ConnectionError("glue unreachable")
        registries.set_clients_for_testing(DownGlue(), None)
        result = drift.check_version(_version())
        self.assertEqual(result["status"], "unchecked")
        self.assertIn("could not verify", result["issues"][0]["message"])


class TestEndpoints(DriftCase):
    def _catalog(self, *versions):
        return mock.patch.object(lifecycle, "catalog",
                                 return_value=list(versions))

    def test_drift_endpoint_reports_every_export(self):
        healthy = _version(job_id="100001")
        broken = _version(job_id="100002",
                          path="glue_catalog.echo_dev_gold_product.product")
        with self._catalog(healthy, broken):
            body = client.get("/drift").json()
        self.assertEqual(body["drift_count"], 1)
        by_job = {e["job_id"]: e["status"] for e in body["exports"]}
        self.assertEqual(by_job, {"100001": "ok", "100002": "drift"})
        self.assertTrue(body["checked_at"])

    def test_job_drift_endpoint(self):
        with self._catalog(_version(job_id="100001")):
            response = client.get("/catalog/100001/drift")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    def test_job_drift_unknown_export_404(self):
        with self._catalog():
            self.assertEqual(client.get("/catalog/100404/drift").status_code,
                             404)

    def test_shared_source_is_read_once_across_exports(self):
        # ten exports on one table = one catalog read (the check must stay
        # cheap enough to run on every dashboard load)
        calls = []
        original = self.glue.get_table
        self.glue.get_table = lambda **kw: (calls.append(1), original(**kw))[1]
        with self._catalog(*[_version(job_id=str(100100 + i))
                             for i in range(10)]):
            client.get("/drift")
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
