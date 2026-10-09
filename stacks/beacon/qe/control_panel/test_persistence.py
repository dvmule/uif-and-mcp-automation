# test_persistence.py
# Regression tests for the catalog-reset incident (2026-08-10): a service
# restart wiped the in-memory catalog, the version counter restarted at 1,
# and a fresh publish silently overwrote the immutable v1 in S3.
# Three layers now prevent it:
#   1. DynamoDB-backed drafts/catalog (this file's FakeDdb)
#   2. conditional catalog puts (no overwrite at the store)
#   3. the publisher's S3 head-check (no overwrite at the bucket)

import os
import unittest
from collections import defaultdict

from control_panel import lifecycle, publisher, validation

MAPPING = {
    "version": "1.0",
    "sources": [{"name": "product", "path": "s3://placeholder/raw/",
                 "format": "csv"}],
    "joins": [],
    "mappings": [{"type": "one_to_one", "source": "product.product_id", "target": "productId"}],
    "validations": [],
    "rejected_path": "s3://placeholder/rejected/${batch_id}/",
    "output": {"targets": [{"format": "parquet", "mode": "overwrite",
                            "path": "s3://placeholder/staging/${batch_id}/canonical/"}]},
}


class FakeDdb:
    """Enough DynamoDB to prove persistence: put/get/scan/query with
    ConditionExpression support on puts."""

    def __init__(self):
        self.tables = defaultdict(dict)

    @staticmethod
    def _key(record):
        if "id" in record:
            return record["id"]["S"]
        return (record["job_id"]["S"], record["version"]["N"])

    def put_item(self, TableName, Item, ConditionExpression=None):
        key = self._key(Item)
        if ConditionExpression and key in self.tables[TableName]:
            raise Exception("ConditionalCheckFailedException")
        self.tables[TableName][key] = Item

    def get_item(self, TableName, Key):
        item = self.tables[TableName].get(self._key(Key))
        return {"Item": item} if item else {}

    def scan(self, TableName):
        return {"Items": list(self.tables[TableName].values())}

    def query(self, TableName, KeyConditionExpression, ExpressionAttributeValues):
        job = ExpressionAttributeValues[":j"]["S"]
        return {"Items": [i for k, i in self.tables[TableName].items()
                          if isinstance(k, tuple) and k[0] == job]}

    def update_item(self, TableName, Key, UpdateExpression,
                    ExpressionAttributeValues, ReturnValues):
        # just enough for the export-id counter: ADD n :one
        key = self._key(Key)
        item = self.tables[TableName].setdefault(key, dict(Key))
        n = int(item.get("n", {}).get("N", "0")) + int(
            ExpressionAttributeValues[":one"]["N"])
        item["n"] = {"N": str(n)}
        return {"Attributes": {"n": {"N": str(n)}}}


@unittest.skipUnless(validation.ENGINE_AVAILABLE, "needs engine")
class TestVersionsSurviveRestart(unittest.TestCase):
    def setUp(self):
        os.environ["BEACON_TABLE_DRAFTS"] = "drafts-table"
        os.environ["BEACON_TABLE_CATALOG"] = "catalog-table"
        self.ddb = FakeDdb()
        lifecycle.set_ddb_for_testing(self.ddb)

    def tearDown(self):
        os.environ.pop("BEACON_TABLE_DRAFTS", None)
        os.environ.pop("BEACON_TABLE_CATALOG", None)
        lifecycle.set_ddb_for_testing(None)
        lifecycle.reset()

    def _publish_once(self):
        draft, _ = lifecycle.create_draft({
            "job_id": "restart-test", "domain": "product", "author": "a",
            "mapping_config": MAPPING, "settings": {"formats": ["csv"]}})
        version, error = lifecycle.submit(draft["id"])  # validated => published
        self.assertIsNone(error)
        return version

    def test_the_incident_cannot_recur(self):
        v1 = self._publish_once()
        self.assertEqual(v1["version"], 1)
        self.assertEqual(v1["export_id"], 100000)

        # === the restart: in-process memory dies, DynamoDB survives ===
        lifecycle.reset()

        # UIFPE-196: identity survives the restart — the export-id counter
        # continues, it never reuses an id (the incident's lesson applied
        # to identity, not just versions)
        v_next = self._publish_once()
        self.assertEqual(v_next["export_id"], 100001)
        self.assertEqual(v_next["version"], 1)
        self.assertEqual(
            [v["version"] for v in lifecycle.catalog_versions(v1["job_id"])], [1])
        self.assertEqual(
            [v["version"] for v in lifecycle.catalog_versions(v_next["job_id"])], [1])

    def test_conditional_put_refuses_overwrite(self):
        v1 = self._publish_once()
        # simulate counter drift: force a version-1 write again
        clash = {"job_id": v1["job_id"], "version": 1, "domain": "product",
                 "mapping_config": MAPPING, "settings": {},
                 "author": "b", "approved_by": "s", "published_at": "x",
                 "from_draft": "d"}
        error = lifecycle._save_version(clash)
        self.assertIsNotNone(error)
        self.assertIn("concurrent publish", error)

    def test_drafts_survive_restart(self):
        draft, _ = lifecycle.create_draft({
            "job_id": "restart-test", "domain": "product", "author": "a",
            "mapping_config": MAPPING})
        version, _ = lifecycle.submit(draft["id"])  # validated => published
        lifecycle.reset()  # restart
        self.assertEqual(lifecycle.get_draft(draft["id"])["status"], "published")
        self.assertEqual(
            [v["version"] for v in lifecycle.catalog_versions(version["job_id"])], [1])

    def test_publish_result_persisted_on_version(self):
        version = self._publish_once()
        lifecycle.attach_publish_result(version, {"published": True,
                                                  "export_config_uri": "s3://x/v1/export.json"})
        lifecycle.reset()  # restart
        stored = lifecycle.catalog_versions(version["job_id"])[-1]
        self.assertEqual(stored["s3"]["export_config_uri"], "s3://x/v1/export.json")


class TestPublisherOverwriteGuard(unittest.TestCase):
    def setUp(self):
        os.environ["BEACON_DATA_BUCKET"] = "demo-bucket"

    def tearDown(self):
        os.environ.pop("BEACON_DATA_BUCKET", None)
        publisher.set_clients_for_testing(None, None)

    def test_existing_version_key_is_never_overwritten(self):
        class S3KeyExists:
            def head_object(self, Bucket, Key):
                return {"ContentLength": 1002}  # the key exists

            def put_object(self, **kwargs):
                raise AssertionError("must not write over an existing version")

        publisher.set_clients_for_testing(S3KeyExists(), None)
        result = publisher.publish_version({
            "job_id": "product-export", "version": 1,
            "mapping_config": MAPPING, "settings": {"formats": ["csv"]}})
        self.assertFalse(result["published"])
        self.assertIn("refusing to overwrite", result["reason"])
