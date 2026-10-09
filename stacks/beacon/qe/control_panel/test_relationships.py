# test_relationships.py
# UIFPE-580: the declared Gold relationships the wizard will prefill joins
# from (UIFPE-581), and submit-time validation of a config that joins THROUGH
# a list column — the element's fields come from the catalogued Glue type.
# Fake Glue, no AWS.

import os
import unittest

from fastapi.testclient import TestClient

from control_panel import registries, validation
from control_panel.api import app
from gold_fixtures import GOLD_DBS, FakeGlue

client = TestClient(app)

#: The dev product table's list columns as Glue catalogues them (types
#: abridged to the fields the tests reference).
CATALOG = {
    "uif_echo_dev_gold_product": {
        "product": {
            "product_id": "string", "organization_code": "string",
            "associated_programs": "array<string>",
            "marketing_copy": "array<struct<content_id:string,marketing_copy_text:string,"
                              "audience_code:string,owning_market_code:string>>",
            "_gold_updated_at": "timestamp",
        },
        "marketing_copy": {"content_id": "string", "marketing_copy_content_text": "string",
                           "_gold_updated_at": "timestamp"},
    },
    "uif_echo_dev_gold_program": {
        "program": {"program_id": "string", "description": "string", "_gold_updated_at": "timestamp"},
    },
    "uif_echo_dev_gold_price": {
        "price": {"product_id": "string",
                  "price_specifications": "array<struct<price_list_name:string,list_price_amount:decimal(38, 2)>>",
                  "_gold_updated_at": "timestamp"},
    },
}


def _config(joins, unnest=None, mappings=None, filters=()):
    raw = {"version": "1.0",
           "sources": [{"name": "product", "format": "iceberg",
                        "path": "glue_catalog.uif_echo_dev_gold_product.product"},
                       {"name": "marketing_copy", "format": "iceberg",
                        "path": "glue_catalog.uif_echo_dev_gold_product.marketing_copy"}],
           "joins": joins,
           "mappings": mappings or [{"type": "one_to_one", "source": "product.product_id", "target": "id"}],
           "filters": list(filters), "validations": [],
           "rejected_path": "s3://b/rejected/${batch_id}/",
           "output": {"targets": [{"format": "parquet", "mode": "overwrite",
                                   "path": "s3://b/staging/${batch_id}/canonical/"}]}}
    if unnest:
        raw["unnest"] = unnest
    return raw


COPY_JOIN = {"left": "product", "right": "marketing_copy", "type": "left",
             "through": {"list": "product.marketing_copy", "alias": "product_copy"},
             "condition": "product_copy.content_id = marketing_copy.content_id"}


class TestRelationshipsRoute(unittest.TestCase):
    def test_the_four_product_relationships_are_declared(self):
        body = client.get("/registries/relationships").json()
        rels = {(r["driving"], r["related"]): r for r in body["relationships"]}
        self.assertEqual(set(rels), {("product", "price"), ("product", "stock"),
                                     ("product", "program"), ("product", "marketing_copy")})
        for r in rels.values():
            self.assertEqual(r["join"]["type"], "left")          # owner: keep unmatched products
            self.assertIn("summary", r)
        self.assertEqual(rels[("product", "marketing_copy")]["join"]["through"],
                         {"list": "{product}.marketing_copy", "alias": "product_copy"})
        self.assertEqual(rels[("product", "program")]["join"]["through"]["list"], "{product}.associated_programs")
        self.assertIn("organization_code", rels[("product", "stock")]["join"]["condition"])
        self.assertEqual(rels[("product", "price")]["unnest"],
                         [{"list": "{price}.price_specifications", "alias": "price_spec"}])

    def test_placeholders_name_only_the_two_tables_of_the_pair(self):
        import re
        for r in registries.relationships():
            text = " ".join([r["join"].get("condition", ""), str(r["join"].get("through", "")),
                             str(r.get("unnest", "")), " ".join(r.get("narrow_by", []))])
            self.assertLessEqual(set(re.findall(r"\{(\w+)\}", text)), {r["driving"], r["related"]}, r)


@unittest.skipUnless(validation.ENGINE_AVAILABLE, "needs engine")
class TestThroughListValidation(unittest.TestCase):
    def setUp(self):
        self._env = os.environ.get("BEACON_ENV")
        os.environ.pop("BEACON_ENV", None)
        os.environ["BEACON_GOLD_DATABASES"] = GOLD_DBS
        registries.set_clients_for_testing(FakeGlue(CATALOG), object())

    def tearDown(self):
        os.environ.pop("BEACON_GOLD_DATABASES", None)
        if self._env is not None:
            os.environ["BEACON_ENV"] = self._env
        registries.set_clients_for_testing(None, None)

    def test_a_through_list_join_validates_with_element_fields_from_the_glue_type(self):
        result = validation.validate_mapping_config(_config(
            [COPY_JOIN],
            mappings=[{"type": "one_to_one", "source": "product.product_id", "target": "id"},
                      {"type": "one_to_one", "source": "product_copy.audience_code", "target": "audience"},
                      {"type": "one_to_one", "source": "marketing_copy.marketing_copy_content_text", "target": "copy"}],
            filters=["product_copy.owning_market_code = 'UK'"]))
        self.assertTrue(result["valid"], result["issues"])

    def test_an_unknown_element_field_is_refused_by_name(self):
        result = validation.validate_mapping_config(_config(
            [COPY_JOIN],
            mappings=[{"type": "one_to_one", "source": "product_copy.nope", "target": "x"}]))
        self.assertFalse(result["valid"])
        self.assertIn("column 'nope' not found in source 'product_copy'",
                      " ".join(i["message"] for i in result["issues"]))

    def test_the_old_array_contains_shape_still_validates_but_the_new_one_is_preferred(self):
        # not refused (an expert may still write it); the wizard will prefill
        # the through form because of the nested-loop plan (UIFPE-581)
        result = validation.validate_mapping_config(_config(
            [{"left": "product", "right": "marketing_copy", "type": "left",
              "condition": "array_contains(product.marketing_copy.content_id, marketing_copy.content_id)"}]))
        self.assertTrue(result["valid"], result["issues"])
