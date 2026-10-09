# test_validation.py
# UIFPE-167: submit-time column validation for CATALOG-picked tables. Before
# this, validation._live_schemas resolved a mapping source's path only
# through sources.yaml, so every table the wizard discovers live (program,
# marketing_copy, any new domain) silently disabled the reference check for
# the WHOLE config — a mistyped join key surfaced as a failed run, never in
# Review. Fake Glue, no AWS.

import copy
import os
import unittest

from control_panel import registries, validation
from gold_fixtures import CATALOG, GOLD_DBS, FakeGlue  # shared, non-test module
from lifecycle_fixtures import MAPPING_JOINED  # shared, non-test module


class GoldEnvBase(unittest.TestCase):
    def setUp(self):
        self._env = os.environ.get("BEACON_ENV")
        os.environ.pop("BEACON_ENV", None)
        os.environ["BEACON_GOLD_DATABASES"] = GOLD_DBS
        registries.set_clients_for_testing(FakeGlue(CATALOG), object())

    def tearDown(self):
        os.environ.pop("BEACON_GOLD_DATABASES", None)
        if self._env is None:
            os.environ.pop("BEACON_ENV", None)
        else:
            os.environ["BEACON_ENV"] = self._env
        registries.set_clients_for_testing(None, None)


class TestSourceIdForPath(GoldEnvBase):
    def test_registry_row_resolves_by_its_fallback_path(self):
        self.assertEqual(
            registries.source_id_for_path("glue_catalog.eds_dev_gold_product.product"),
            "gold_product.product")

    def test_registry_row_wins_over_the_catalog_at_dev(self):
        # at BEACON_ENV=dev the YAML product row resolves to the SAME path the
        # catalog derives for product.product — two ids, one table; the
        # registry row takes precedence (as watermark_column_for_path does)
        os.environ["BEACON_ENV"] = "dev"
        self.assertEqual(
            registries.source_id_for_path("glue_catalog.uif_echo_dev_gold_product.product"),
            "gold_product.product")

    def test_catalog_path_resolves_to_domain_table_id(self):
        self.assertEqual(
            registries.source_id_for_path("glue_catalog.uif_echo_dev_gold_program.program"),
            "program.program")
        self.assertEqual(
            registries.source_id_for_path("glue_catalog.uif_echo_dev_gold_price.price"),
            "price.price")

    def test_unconfigured_database_is_none(self):
        self.assertIsNone(
            registries.source_id_for_path("glue_catalog.someone_elses_db.table"))

    def test_catalog_path_needs_the_env_list(self):
        os.environ.pop("BEACON_GOLD_DATABASES", None)
        self.assertIsNone(
            registries.source_id_for_path("glue_catalog.uif_echo_dev_gold_program.program"))

    def test_non_catalog_or_malformed_paths_are_none(self):
        for path in ("s3://uif-beacon-sandbox-data/raw/other/", "glue_catalog.onlytwo",
                     "other_catalog.uif_echo_dev_gold_program.program", "", None):
            self.assertIsNone(registries.source_id_for_path(path), path)


class TestLiveSchemas(GoldEnvBase):
    def test_catalog_sources_from_two_domains_resolve_live(self):
        schemas = validation._live_schemas([
            {"name": "product", "path": "glue_catalog.uif_echo_dev_gold_product.product"},
            {"name": "program", "path": "glue_catalog.uif_echo_dev_gold_program.program"},
        ])
        # keyed by the config ALIAS, platform _-columns stripped. Since
        # UIFPE-540 each entry also carries the channel-eligibility verdict.
        self.assertEqual({a: r["columns"] for a, r in schemas.items()},
                         {"product": ["product_id", "isbn13"],
                          "program": ["program_id", "name"]})
        # no artifacts bucket configured here, so the guard is inert: nothing
        # excluded and nothing unverified, which is what keeps every existing
        # config valid until Terraform sets BEACON_GOLD_ARTIFACTS_BUCKET
        for alias, r in schemas.items():
            self.assertEqual(r["excluded"], set(), alias)
            self.assertFalse(r["unverified"], alias)

    def test_one_unknown_source_disables_the_map(self):
        # all-or-nothing: validate_references would report a missing alias as
        # an unknown source, so a partial map is worse than no map
        self.assertIsNone(validation._live_schemas([
            {"name": "product", "path": "glue_catalog.uif_echo_dev_gold_product.product"},
            {"name": "x", "path": "glue_catalog.someone_elses_db.table"},
        ]))

    def test_catalog_outage_disables_the_map(self):
        registries.set_clients_for_testing(FakeGlue(CATALOG, fail=True), object())
        self.assertIsNone(validation._live_schemas([
            {"name": "program", "path": "glue_catalog.uif_echo_dev_gold_program.program"},
        ]))


@unittest.skipUnless(validation.ENGINE_AVAILABLE, "needs engine")
class TestJoinedMappingValidation(GoldEnvBase):
    def _with_condition(self, condition):
        body = copy.deepcopy(MAPPING_JOINED)
        body["joins"][0]["condition"] = condition
        return body

    def test_joined_config_on_catalog_tables_is_valid(self):
        result = validation.validate_mapping_config(MAPPING_JOINED)
        self.assertEqual(result["issues"], [])
        self.assertTrue(result["valid"])

    def test_bad_join_key_on_a_catalog_table_is_caught(self):
        result = validation.validate_mapping_config(
            self._with_condition("product.product_id = price.product_idd"))
        self.assertFalse(result["valid"])
        issue = next(i for i in result["issues"] if i["where"] == "joins[0].condition")
        self.assertIn("'product_idd' not found", issue["message"])
        self.assertIn("price", issue["message"])

    def test_unknown_join_alias_is_caught(self):
        result = validation.validate_mapping_config(
            self._with_condition("product.product_id = prize.ppid"))
        self.assertFalse(result["valid"])
        self.assertTrue(any("unknown source 'prize'" in i["message"]
                            for i in result["issues"]))

    def test_a_bare_list_column_is_refused_for_csv_only(self):
        # UIFPE-609: two dev runs died at the rendering guard with a bare
        # array column in a CSV export — Validate/Publish say so first
        body = copy.deepcopy(MAPPING_JOINED)
        body["mappings"][1] = {"type": "one_to_one", "source": "price.price_specifications",
                               "target": "priceSpecifications"}
        refused = validation.validate_mapping_config(body, fmt="csv")
        self.assertFalse(refused["valid"])
        self.assertEqual([i["where"] for i in refused["issues"]], ["mappings[1]"])
        self.assertIn("a CSV cell cannot carry it", refused["issues"][0]["message"])
        self.assertIn("to_json(price.price_specifications)", refused["issues"][0]["message"])
        # json and xml serialize nested values natively; an unknown format is not judged
        for fmt in ("json", "xml", None):
            self.assertTrue(validation.validate_mapping_config(body, fmt=fmt)["valid"], fmt)
        # the remedy itself passes for csv
        self.assertTrue(validation.validate_mapping_config(MAPPING_JOINED, fmt="csv")["valid"])

    def test_bad_mapping_column_on_the_joined_table_is_caught(self):
        body = copy.deepcopy(MAPPING_JOINED)
        body["mappings"][1]["expression"] = "to_json(price.price_specs)"
        result = validation.validate_mapping_config(body)
        self.assertTrue(any("'price_specs' not found" in i["message"]
                            for i in result["issues"]))


if __name__ == "__main__":
    unittest.main()
