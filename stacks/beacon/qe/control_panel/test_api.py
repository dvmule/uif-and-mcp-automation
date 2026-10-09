# test_api.py
# End-to-end API tests via FastAPI's TestClient — registries with the D17
# eligibility filter, the D16 destinations flow, validation, and conversion.

import unittest

from fastapi.testclient import TestClient

from control_panel import lifecycle, registries, validation
from control_panel.api import app

client = TestClient(app)


class TestRegistries(unittest.TestCase):
    def test_health(self):
        response = client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertIn("engine_available", response.json())

    def test_sources_unfiltered(self):
        rows = client.get("/registries/sources").json()
        ids = {r["id"] for r in rows}
        self.assertIn("gold_product.product", ids)
        # UIFPE-220: the stale eds_dev_gold_* rows are GONE — a registered
        # source that resolves in no environment 404s the author who picks it
        self.assertNotIn("gold_product.onix_3_0", ids)
        self.assertNotIn("gold_assessment.results", ids)

    def test_onix_rule_xml_filter(self):
        # D17 (the ONIX rule): format eligibility filters the registry — an
        # xml request keeps xml-eligible sources and drops the rest
        rows = client.get("/registries/sources", params={"formats": "xml"}).json()
        ids = {r["id"] for r in rows}
        self.assertIn("gold_product.product", ids)    # csv+json+xml
        self.assertNotIn("gold_price.price", ids)     # csv-only
        self.assertNotIn("gold_stock.stock", ids)     # csv+json

    def test_price_source_registry_entry(self):
        """UIFPE-237: the price entry tracks the entity's relocation to
        gold_price.price and carries the offline column fallback; no
        watermark_column while the table is draft (full-load only), and the
        per-env path map never leaves the API."""
        rows = client.get("/registries/sources").json()
        ids = {r["id"] for r in rows}
        self.assertNotIn("gold_product.price", ids)   # the stale id is gone
        price = next(r for r in rows if r["id"] == "gold_price.price")
        self.assertEqual(price["path"], "glue_catalog.eds_dev_gold_price.price")
        self.assertEqual(price["columns"], ["product_id", "isbn13", "price_specifications"])
        self.assertNotIn("watermark_column", price)
        self.assertNotIn("path_by_env", price)

    def test_multi_format_needs_all(self):
        rows = client.get("/registries/sources",
                          params={"formats": "csv,json"}).json()
        ids = {r["id"] for r in rows}
        self.assertEqual(ids, {"gold_product.product", "gold_stock.stock",
                               "demo.products_csv"})

    def test_stock_source_registry_entry(self):
        """UIFPE-167: the stock entry tracks the entity's relocation to
        gold_stock.stock (its own gold database) with the offline column
        fallback from the stock master schema; no watermark_column while the
        table is draft, and the per-env path map never leaves the API."""
        rows = client.get("/registries/sources").json()
        ids = {r["id"] for r in rows}
        self.assertNotIn("gold_product.stock", ids)   # the stale id is gone
        stock = next(r for r in rows if r["id"] == "gold_stock.stock")
        self.assertEqual(stock["domain"], "stock")
        self.assertEqual(stock["path"], "glue_catalog.eds_dev_gold_stock.stock")
        self.assertEqual(stock["columns"],
                         ["product_id", "isbn13", "organization_code", "on_hand_quantity",
                          "available_to_promise_timestamp", "available_to_promise_indicator"])
        self.assertNotIn("watermark_column", stock)
        self.assertNotIn("path_by_env", stock)

    def test_unknown_format_422(self):
        response = client.get("/registries/sources", params={"formats": "yaml"})
        self.assertEqual(response.status_code, 422)

    def test_source_path_resolves_per_environment(self):
        """One image, many environments: gold_product.product must point at
        the sandbox catalog by default and at the echo stack's catalog when
        BEACON_ENV=dev — and the path_by_env map itself never leaves the API."""
        import os

        def product_row():
            rows = client.get("/registries/sources").json()
            return next(r for r in rows if r["id"] == "gold_product.product")

        original = os.environ.get("BEACON_ENV")
        try:
            os.environ.pop("BEACON_ENV", None)  # local / tests -> fallback path
            self.assertEqual(product_row()["path"],
                             "glue_catalog.eds_dev_gold_product.product")
            os.environ["BEACON_ENV"] = "dev"
            row = product_row()
            self.assertEqual(row["path"],
                             "glue_catalog.uif_echo_dev_gold_product.product")
            self.assertNotIn("path_by_env", row)
        finally:
            if original is None:
                os.environ.pop("BEACON_ENV", None)
            else:
                os.environ["BEACON_ENV"] = original

    def test_formats_carry_options_schema(self):
        rows = client.get("/registries/formats").json()
        csv = next(f for f in rows if f["id"] == "csv")
        self.assertTrue(csv["options_schema"]["properties"]
                        ["formula_escape"]["default"])


class TestDestinations(unittest.TestCase):
    def setUp(self):
        registries.reset_destinations()

    def _body(self, **overrides):
        body = {"name": "partner-y-sftp", "channel": "sftp",
                "host": "sftp.partner-y.com", "remote_dir": "/inbound",
                "domain": "product",
                "credential": {"host": "sftp.partner-y.com",
                               "username": "beacon", "password": "x"}}
        body.update(overrides)
        return body

    def test_create_destination(self):
        response = client.post("/destinations", json=self._body())
        self.assertEqual(response.status_code, 201)
        record = response.json()
        self.assertEqual(record["status"], "pending_connection_test")
        self.assertIn("secret_ref", record)
        # the credential must never be echoed back
        self.assertNotIn("credential", record)
        self.assertNotIn("password", str(record))

    def test_created_destination_listed_without_credential(self):
        client.post("/destinations", json=self._body())
        rows = client.get("/destinations").json()
        self.assertEqual(len(rows), 1)
        self.assertNotIn("credential", rows[0])

    def test_unknown_channel_422(self):
        response = client.post("/destinations",
                               json=self._body(channel="carrier-pigeon"))
        self.assertEqual(response.status_code, 422)

    def test_planned_channel_rejected(self):
        response = client.post("/destinations", json=self._body(channel="gcs"))
        self.assertEqual(response.status_code, 422)
        self.assertIn("not available", response.json()["detail"])

    def test_relative_remote_dir_422(self):
        response = client.post("/destinations",
                               json=self._body(remote_dir="inbound"))
        self.assertEqual(response.status_code, 422)


class TestConvertAndValidate(unittest.TestCase):
    def test_convert_endpoint(self):
        response = client.post("/convert", json={
            "sql": "SELECT p.product_id FROM gold_product.product p WHERE p.x = 1"})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["mappings"][0]["target"], "product_id")
        self.assertEqual(len(body["filters"]), 1)

    def test_convert_bad_sql_422(self):
        response = client.post("/convert", json={"sql": "DROP TABLE x"})
        self.assertEqual(response.status_code, 422)

    def test_validate_export_config_roundtrip(self):
        good = {
            "version": "1.0", "job_id": "t",
            "mapping_config_s3_path": "s3://b/m.json",
            "canonical_s3_prefix": "s3://b/s/{batch_id}/c/",
            "quality_gates": {"min_rows": 1, "max_rejection_pct": 2.0},
            "renderings": [{"format": "csv", "options": {}}],
            "packaging": {"filename_pattern": "e_{batch_id}.{ext}",
                          "pickup_s3_prefix": "s3://b/p/{batch_id}/"},
            "delivery": {"channel": "sftp", "adapter": "paramiko",
                         "secret_ref": "s", "remote_dir": "/in"},
        }
        self.assertTrue(client.post("/validate/export-config",
                                    json=good).json()["valid"])
        bad = dict(good)
        del bad["quality_gates"]
        result = client.post("/validate/export-config", json=bad).json()
        self.assertFalse(result["valid"])
        self.assertIn("quality_gates", result["error"])

    def test_validate_mapping_config_reports_engine_state(self):
        result = client.post("/validate/mapping-config",
                             json={"version": "1.0"}).json()
        self.assertIn("engine_available", result)
        # in either environment the answer is honest: with the engine it
        # validates (and this minimal config is invalid); without it, it
        # says validation did not run
        self.assertFalse(result["valid"])


class TestS3Destinations(unittest.TestCase):
    def setUp(self):
        registries.reset_destinations()

    def test_s3_destination_needs_no_credential(self):
        response = client.post("/destinations", json={
            "name": "partner-s3-drop", "channel": "s3", "domain": "product",
            "destination_s3_prefix": "s3://partner-bucket/inbound/product"})
        self.assertEqual(response.status_code, 201)
        record = response.json()
        self.assertNotIn("secret_ref", record)  # IAM-authenticated channel
        self.assertEqual(record["destination_s3_prefix"],
                         "s3://partner-bucket/inbound/product")

    def test_s3_destination_requires_prefix(self):
        response = client.post("/destinations", json={
            "name": "partner-s3-drop", "channel": "s3"})
        self.assertEqual(response.status_code, 422)
        self.assertIn("destination_s3_prefix", response.json()["detail"])

    def test_s3_destination_rejects_non_s3_uri(self):
        response = client.post("/destinations", json={
            "name": "x", "channel": "s3", "destination_s3_prefix": "/local"})
        self.assertEqual(response.status_code, 422)

    def test_s3_channel_listed_available(self):
        rows = client.get("/registries/channels").json()
        s3 = next(c for c in rows if c["id"] == "s3")
        self.assertEqual(s3["status"], "available")
        self.assertFalse(s3["requires_credential"])


class FakeGlueCatalog:
    def get_table(self, DatabaseName, Name):
        assert (DatabaseName, Name) == ("eds_dev_gold_product", "product")
        cols = [{"Name": n, "Type": t} for n, t in
                (("inventory_item_id", "bigint"), ("product_id", "string"),
                 ("isbn13", "string"), ("_gold_updated_at", "timestamp"))]
        return {"Table": {"StorageDescriptor": {"Columns": cols}}}


class FakeS3Csv:
    def list_objects_v2(self, Bucket, Prefix, MaxKeys):
        return {"Contents": [{"Key": Prefix + "products.csv"}]}

    def get_object(self, Bucket, Key, Range):
        import io
        return {"Body": io.BytesIO(b'"colA","colB","_meta"\n1,2,3\n')}


class TestSourceColumnsLive(unittest.TestCase):
    """The mapping tab's attributes come LIVE from the source — a new column
    needs no registry edit and no deployment."""

    def tearDown(self):
        registries.set_clients_for_testing(None, None)

    def test_iceberg_columns_from_glue_catalog(self):
        registries.set_clients_for_testing(FakeGlueCatalog(), None)
        result = client.get("/registries/sources/gold_product.product/columns").json()
        self.assertEqual(result["provenance"], "live")
        self.assertEqual(result["columns"], ["inventory_item_id", "product_id", "isbn13"])
        # framework metadata (_prefixed) excluded from authoring
        self.assertNotIn("_gold_updated_at", result["columns"])

    def test_iceberg_columns_carry_their_catalogued_types(self):
        # the wizard's row-filter builder picks operators from the type; the
        # map covers exactly the authoring columns (no _-prefixed metadata)
        registries.set_clients_for_testing(FakeGlueCatalog(), None)
        result = client.get("/registries/sources/gold_product.product/columns").json()
        self.assertEqual(result["types"], {"inventory_item_id": "bigint",
                                           "product_id": "string", "isbn13": "string"})
        self.assertEqual(set(result["types"]), set(result["columns"]))

    def test_csv_columns_from_header(self):
        registries.set_clients_for_testing(None, FakeS3Csv())
        result = client.get("/registries/sources/demo.products_csv/columns").json()
        self.assertEqual(result["provenance"], "live")
        self.assertEqual(result["columns"], ["colA", "colB"])
        # a header row carries no types: the key is absent, not guessed
        self.assertNotIn("types", result)

    def test_fallback_to_registry_when_offline(self):
        class Broken:
            def __getattr__(self, name):
                raise ConnectionError("no AWS")

        registries.set_clients_for_testing(Broken(), Broken())
        result = client.get("/registries/sources/gold_product.product/columns").json()
        self.assertEqual(result["provenance"], "registry")
        self.assertIn("product_id", result["columns"])  # seeded fallback
        self.assertIn("reason", result)
        self.assertNotIn("types", result)  # the registry row has no types

    def test_unknown_source_404(self):
        response = client.get("/registries/sources/ghost.table/columns")
        self.assertEqual(response.status_code, 404)


class TestSubmitTimeColumnValidation(unittest.TestCase):
    """The 2026-08-10 failed run: a query used inventory_item_id but the
    Gold table's column is inventoryitemid. With live schemas, that mistake
    is now caught at SUBMIT — not as a failed batch 20 minutes later."""

    def setUp(self):
        lifecycle.reset()
        registries.set_clients_for_testing(FakeGlueCatalog(), None)

    def tearDown(self):
        registries.set_clients_for_testing(None, None)

    def _draft(self, column):
        return {
            "job_id": "col-check", "domain": "product", "author": "karthik",
            "mapping_config": {
                "version": "1.0",
                "sources": [{"name": "p",
                             "path": "glue_catalog.eds_dev_gold_product.product",
                             "format": "iceberg"}],
                "joins": [],
                "mappings": [{"type": "one_to_one", "source": f"p.{column}",
                              "target": "product_id"}],
                "validations": [],
                "rejected_path": "s3://placeholder/rejected/${batch_id}/",
                "output": {"targets": [{"format": "parquet", "mode": "overwrite",
                                        "path": "s3://placeholder/staging/${batch_id}/canonical/"}]},
            },
            "settings": {"formats": ["csv"]},
        }

    @unittest.skipUnless(validation.ENGINE_AVAILABLE, "needs engine")
    def test_bad_column_blocked_at_submit_with_live_schema(self):
        draft = client.post("/drafts", json=self._draft("no_such_column")).json()
        response = client.post(f"/drafts/{draft['id']}/submit")
        self.assertEqual(response.status_code, 422)
        detail = str(response.json()["detail"])
        self.assertIn("no_such_column", detail)
        self.assertIn("not found", detail)

    @unittest.skipUnless(validation.ENGINE_AVAILABLE, "needs engine")
    def test_good_column_passes_live_schema_check(self):
        draft = client.post("/drafts", json=self._draft("product_id")).json()
        response = client.post(f"/drafts/{draft['id']}/submit")
        self.assertEqual(response.status_code, 200)
