# test_joined_feeds.py
# UIFPE-167: a multi-table feed on CATALOG-picked tables from two domains,
# through the real submit → validate → publish path. Until now every
# control-panel fixture carried "joins": [] — the join capability was proven
# only at the engine and in the product domain's own tests, never through
# the API that publishes it. Fake Glue + S3 + Step Functions, no AWS.

import copy
import json
import os
import unittest

from control_panel import lifecycle, publisher, registries, validation
from gold_fixtures import CATALOG, GOLD_DBS, FakeGlue  # shared, non-test module
from lifecycle_fixtures import MAPPING_JOINED, client, make_joined_draft


class FakeS3:
    def __init__(self):
        self.objects = {}

    def put_object(self, Bucket, Key, Body, **kw):
        self.objects[f"{Bucket}/{Key}"] = Body


class FakeSfn:
    def start_execution(self, stateMachineArn, name, input):
        return {"executionArn": f"arn:aws:states:eu-west-1:1:execution:m:{name}"}


@unittest.skipUnless(validation.ENGINE_AVAILABLE, "needs engine")
class TestJoinedFeedPublish(unittest.TestCase):
    def setUp(self):
        lifecycle.reset()
        self._env = os.environ.get("BEACON_ENV")
        os.environ.pop("BEACON_ENV", None)
        os.environ["BEACON_GOLD_DATABASES"] = GOLD_DBS
        registries.set_clients_for_testing(FakeGlue(CATALOG), object())
        self.s3 = FakeS3()
        publisher.set_clients_for_testing(self.s3, FakeSfn())
        os.environ["BEACON_DATA_BUCKET"] = "demo-bucket"
        os.environ["BEACON_CONFIG_PREFIX"] = "configs"
        os.environ["BEACON_STATE_MACHINE_ARN"] = \
            "arn:aws:states:eu-west-1:1:stateMachine:m"

    def tearDown(self):
        for key in ("BEACON_GOLD_DATABASES", "BEACON_DATA_BUCKET",
                    "BEACON_CONFIG_PREFIX", "BEACON_STATE_MACHINE_ARN"):
            os.environ.pop(key, None)
        if self._env is None:
            os.environ.pop("BEACON_ENV", None)
        else:
            os.environ["BEACON_ENV"] = self._env
        registries.set_clients_for_testing(None, None)
        publisher.set_clients_for_testing(None, None)

    def test_joined_feed_publishes_with_the_joins_intact(self):
        draft = make_joined_draft()
        response = client.post(f"/drafts/{draft['id']}/submit")
        self.assertEqual(response.status_code, 200, response.text)
        version = response.json()
        self.assertTrue(version["s3"]["published"], version["s3"])
        published = json.loads(self.s3.objects["demo-bucket/configs/100000/v1/mapping.json"])
        # the publisher rewrites only the environment-owned paths; sources
        # and joins land byte-for-byte as authored (D22 freezes them)
        self.assertEqual(published["joins"], MAPPING_JOINED["joins"])
        self.assertEqual([s["path"] for s in published["sources"]],
                         [s["path"] for s in MAPPING_JOINED["sources"]])
        self.assertEqual(json.loads(self.s3.objects[
            "demo-bucket/configs/100000/v1/export.json"])["extraction"], {"mode": "full"})

    def test_bad_join_key_on_a_catalog_table_is_a_422_at_submit(self):
        # the whole point of UIFPE-167's validation fix: price.price has no
        # sources.yaml row, yet its columns are checked against the catalog
        mapping = copy.deepcopy(MAPPING_JOINED)
        mapping["joins"][0]["condition"] = "product.product_id = price.product_idd"
        draft = make_joined_draft(mapping_config=mapping)
        response = client.post(f"/drafts/{draft['id']}/submit")
        self.assertEqual(response.status_code, 422)
        detail = str(response.json()["detail"])
        self.assertIn("product_idd", detail)
        self.assertIn("not found", detail)
        self.assertEqual(client.get("/catalog").json(), [])
        self.assertEqual(self.s3.objects, {})

    def test_join_referencing_a_later_source_is_a_422_at_submit(self):
        # the chain check (validate_structure) runs BEFORE the live-schema
        # check, so this is caught even when a catalog is unreachable
        mapping = copy.deepcopy(MAPPING_JOINED)
        mapping["sources"].append({"name": "program",
                                   "path": "glue_catalog.uif_echo_dev_gold_program.program",
                                   "format": "iceberg"})
        mapping["joins"] = [
            {"left": "product", "right": "price", "type": "left",
             "condition": "product.product_id = price.product_id AND program.name = price.isbn13"},
            {"left": "product", "right": "program", "type": "left",
             "condition": "product.product_id = program.program_id"},
        ]
        draft = make_joined_draft(mapping_config=mapping)
        response = client.post(f"/drafts/{draft['id']}/submit")
        self.assertEqual(response.status_code, 422)
        self.assertIn("before it is joined", str(response.json()["detail"]))

    def test_cross_join_publishes_with_an_empty_condition(self):
        # the wizard emits condition "" for a cross join; the loader and the
        # validator accept it and the published config carries it verbatim
        mapping = copy.deepcopy(MAPPING_JOINED)
        mapping["joins"] = [{"left": "product", "right": "price", "type": "cross",
                             "condition": ""}]
        draft = make_joined_draft(mapping_config=mapping)
        response = client.post(f"/drafts/{draft['id']}/submit")
        self.assertEqual(response.status_code, 200, response.text)
        published = json.loads(self.s3.objects["demo-bucket/configs/100000/v1/mapping.json"])
        self.assertEqual(published["joins"], mapping["joins"])


if __name__ == "__main__":
    unittest.main()
