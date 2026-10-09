# test_extraction.py
# UIFPE-168 across the control plane: the extraction mode as versioned
# authoring config (publish-time resolution of the platform-owned watermark
# column), the watermark as runtime state outside versions (D22), the
# run-start contract, and the panel's read/reset endpoints. Fake AWS clients
# — no AWS in CI.

import json
import os
import unittest

from fastapi.testclient import TestClient

from control_panel import extraction_state, lifecycle, publisher, validation
from control_panel.api import app
from lifecycle_fixtures import MAPPING, make_draft  # shared, non-test module

client = TestClient(app)


class FakeS3:
    def __init__(self):
        self.objects = {}

    def put_object(self, Bucket, Key, Body, **kw):
        self.objects[f"{Bucket}/{Key}"] = Body


class FakeSfn:
    def __init__(self):
        self.executions = {}

    def start_execution(self, stateMachineArn, name, input):
        arn = f"arn:aws:states:eu-west-1:1:execution:m:{name}"
        self.executions[arn] = {"status": "RUNNING", "input": input}
        return {"executionArn": arn}


class FakeStateDdb:
    """The extraction-state table: get_item / delete_item."""

    def __init__(self, watermark=None):
        self.watermark = watermark
        self.deleted = []

    def get_item(self, TableName, Key):
        if self.watermark is None:
            return {}
        return {"Item": {"job_id": Key["job_id"],
                         "watermark": {"S": self.watermark},
                         "updated_at": {"S": "2026-08-27T07:00:00Z"},
                         "batch_id": {"S": "b-42"}}}

    def delete_item(self, TableName, Key):
        self.deleted.append((TableName, Key["job_id"]["S"]))


class ExtractionTestBase(unittest.TestCase):
    def setUp(self):
        lifecycle.reset()
        self.s3, self.sfn = FakeS3(), FakeSfn()
        publisher.set_clients_for_testing(self.s3, self.sfn)
        os.environ["BEACON_DATA_BUCKET"] = "demo-bucket"
        os.environ["BEACON_CONFIG_PREFIX"] = "configs"
        os.environ["BEACON_STATE_MACHINE_ARN"] = \
            "arn:aws:states:eu-west-1:1:stateMachine:m"

    def tearDown(self):
        for key in ("BEACON_DATA_BUCKET", "BEACON_CONFIG_PREFIX",
                    "BEACON_STATE_MACHINE_ARN", "BEACON_TABLE_EXTRACTION_STATE"):
            os.environ.pop(key, None)
        publisher.set_clients_for_testing(None, None)
        extraction_state.set_client_for_testing(None)


class TestExtractionPublish(ExtractionTestBase):
    @unittest.skipUnless(validation.ENGINE_AVAILABLE, "needs engine")
    def test_incremental_publish_resolves_the_registry_watermark_column(self):
        # the author picks a MODE; the column is platform-owned (authors
        # never see _-prefixed metadata columns) and resolved at publish
        draft = make_draft(settings={"formats": ["csv"],
                                     "extraction": {"mode": "incremental"}})
        version = client.post(f"/drafts/{draft['id']}/submit").json()
        self.assertTrue(version["s3"]["published"])
        cfg = json.loads(self.s3.objects[
            "demo-bucket/configs/100000/v1/export.json"])
        self.assertEqual(cfg["extraction"],
                         {"mode": "incremental", "source": "product",
                          "watermark_column": "_gold_updated_at"})

    @unittest.skipUnless(validation.ENGINE_AVAILABLE, "needs engine")
    def test_full_mode_is_stamped_explicitly(self):
        # resolve-at-publish (UIFPE-172 pattern): a version WITHOUT the block
        # is unambiguously pre-feature
        draft = make_draft()
        client.post(f"/drafts/{draft['id']}/submit")
        cfg = json.loads(self.s3.objects[
            "demo-bucket/configs/100000/v1/export.json"])
        self.assertEqual(cfg["extraction"], {"mode": "full"})

    @unittest.skipUnless(validation.ENGINE_AVAILABLE, "needs engine")
    def test_source_without_watermark_column_refuses_incremental(self):
        # refused at publish, never discovered at run time
        mapping = dict(MAPPING, sources=[{
            "name": "products", "path": "s3://uif-beacon-sandbox-data/raw/products/",
            "format": "csv", "options": {"header": "true", "inferSchema": "true"}}])
        mapping["mappings"] = [{"type": "one_to_one",
                                "source": "products.ppid", "target": "productId"}]
        draft = make_draft(mapping_config=mapping,
                           settings={"formats": ["csv"],
                                     "extraction": {"mode": "incremental"}})
        version = client.post(f"/drafts/{draft['id']}/submit").json()
        self.assertFalse(version["s3"]["published"])
        self.assertIn("incremental", version["s3"]["reason"])

    @unittest.skipUnless(validation.ENGINE_AVAILABLE, "needs engine")
    def test_multi_source_feed_refuses_incremental(self):
        # UIFPE-167 closing a UIFPE-238 hole: the wizard already forces a
        # multi-source feed to full load, but an API-authored draft could
        # publish `incremental` and silently window only the first source.
        # Refused at publish, with the ticket named so the reason is actionable.
        from lifecycle_fixtures import make_joined_draft
        draft = make_joined_draft(settings={"formats": ["csv"],
                                            "extraction": {"mode": "incremental"}})
        version = client.post(f"/drafts/{draft['id']}/submit").json()
        self.assertFalse(version["s3"]["published"])
        self.assertIn("UIFPE-238", version["s3"]["reason"])
        self.assertIn("2 sources", version["s3"]["reason"])

    @unittest.skipUnless(validation.ENGINE_AVAILABLE, "needs engine")
    def test_malformed_extraction_blocks_publish_gracefully(self):
        for bad in ("incremental", {"mode": "delta"}, ["incremental"], 1):
            lifecycle.reset()
            draft = make_draft(settings={"formats": ["csv"], "extraction": bad})
            response = client.post(f"/drafts/{draft['id']}/submit")
            self.assertEqual(response.status_code, 200, f"500 on {bad!r}")
            version = response.json()
            self.assertFalse(version["s3"]["published"], f"published {bad!r}")
            self.assertIn("extraction", version["s3"]["reason"])

    @unittest.skipUnless(validation.ENGINE_AVAILABLE, "needs engine")
    def test_version_record_stamps_extraction_explicitly(self):
        # lifecycle._normalized_settings: missing key = full, written out —
        # the panel can trust every post-feature version to carry the mode
        draft = make_draft()
        version = client.post(f"/drafts/{draft['id']}/submit").json()
        self.assertEqual(version["settings"]["extraction"], {"mode": "full"})


class TestRunStartWatermark(ExtractionTestBase):
    def _input_of(self, run):
        return json.loads(self.sfn.executions[run["run_id"]]["input"])

    def test_full_mode_sends_empty_watermark(self):
        run = publisher.start_run("100000", "b-1",
                                  export_config_uri="s3://demo-bucket/configs/100000/v1/export.json",
                                  extraction={"mode": "full"})
        self.assertEqual(self._input_of(run)["watermark"], "")

    def test_incremental_sends_the_stored_watermark(self):
        os.environ["BEACON_TABLE_EXTRACTION_STATE"] = "state-table"
        extraction_state.set_client_for_testing(
            FakeStateDdb(watermark="2026-08-27 06:30:00.000001"))
        run = publisher.start_run("100000", "b-2",
                                  export_config_uri="s3://demo-bucket/configs/100000/v1/export.json",
                                  extraction={"mode": "incremental"})
        self.assertEqual(self._input_of(run)["watermark"],
                         "2026-08-27 06:30:00.000001")
        self.assertEqual(run["watermark"], "2026-08-27 06:30:00.000001")

    def test_incremental_without_state_baselines(self):
        os.environ["BEACON_TABLE_EXTRACTION_STATE"] = "state-table"
        extraction_state.set_client_for_testing(FakeStateDdb(watermark=None))
        run = publisher.start_run("100000", "b-3",
                                  export_config_uri="s3://demo-bucket/configs/100000/v1/export.json",
                                  extraction={"mode": "incremental"})
        self.assertEqual(self._input_of(run)["watermark"], "")

    def test_state_table_unset_degrades_to_baseline(self):
        run = publisher.start_run("100000", "b-4",
                                  export_config_uri="s3://demo-bucket/configs/100000/v1/export.json",
                                  extraction={"mode": "incremental"})
        self.assertEqual(self._input_of(run)["watermark"], "")


class TestExtractionEndpoints(ExtractionTestBase):
    @unittest.skipUnless(validation.ENGINE_AVAILABLE, "needs engine")
    def test_get_extraction_reports_mode_and_watermark(self):
        draft = make_draft(settings={"formats": ["csv"],
                                     "extraction": {"mode": "incremental"}})
        client.post(f"/drafts/{draft['id']}/submit")
        os.environ["BEACON_TABLE_EXTRACTION_STATE"] = "state-table"
        extraction_state.set_client_for_testing(
            FakeStateDdb(watermark="2026-08-27 06:30:00.000001"))
        result = client.get("/catalog/100000/extraction").json()
        self.assertEqual(result["mode"], "incremental")
        self.assertEqual(result["watermark"], "2026-08-27 06:30:00.000001")
        self.assertEqual(result["watermark_batch_id"], "b-42")
        self.assertTrue(result["state_enabled"])

    @unittest.skipUnless(validation.ENGINE_AVAILABLE, "needs engine")
    def test_get_extraction_pre_feature_version_reads_full(self):
        draft = make_draft(settings={"formats": ["csv"]})
        # simulate a pre-feature version: strip the stamped extraction key
        version = client.post(f"/drafts/{draft['id']}/submit").json()
        stored = lifecycle.catalog_versions(version["job_id"])[-1]
        stored["settings"].pop("extraction", None)
        result = client.get("/catalog/100000/extraction").json()
        self.assertEqual(result["mode"], "full")
        self.assertIsNone(result["watermark"])

    def test_get_extraction_unknown_job_404(self):
        self.assertEqual(client.get("/catalog/ghost/extraction").status_code, 404)

    @unittest.skipUnless(validation.ENGINE_AVAILABLE, "needs engine")
    def test_reset_watermark_deletes_the_state(self):
        draft = make_draft(settings={"formats": ["csv"],
                                     "extraction": {"mode": "incremental"}})
        client.post(f"/drafts/{draft['id']}/submit")
        os.environ["BEACON_TABLE_EXTRACTION_STATE"] = "state-table"
        fake = FakeStateDdb(watermark="2026-08-27 06:30:00.000001")
        extraction_state.set_client_for_testing(fake)
        response = client.delete("/catalog/100000/extraction/watermark")
        self.assertEqual(response.status_code, 204)
        self.assertEqual(fake.deleted, [("state-table", "100000")])

    @unittest.skipUnless(validation.ENGINE_AVAILABLE, "needs engine")
    def test_reset_without_state_table_is_503(self):
        draft = make_draft(settings={"formats": ["csv"]})
        client.post(f"/drafts/{draft['id']}/submit")
        response = client.delete("/catalog/100000/extraction/watermark")
        self.assertEqual(response.status_code, 503)

    def test_reset_unknown_job_404(self):
        response = client.delete("/catalog/ghost/extraction/watermark")
        self.assertEqual(response.status_code, 404)


class TestExtractionStateModule(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("BEACON_TABLE_EXTRACTION_STATE", None)
        extraction_state.set_client_for_testing(None)

    def test_disabled_without_env(self):
        self.assertFalse(extraction_state.enabled())
        self.assertIsNone(extraction_state.get_state("100000"))
        self.assertEqual(extraction_state.watermark("100000"), "")

    def test_get_state_parses_the_item(self):
        os.environ["BEACON_TABLE_EXTRACTION_STATE"] = "state-table"
        extraction_state.set_client_for_testing(
            FakeStateDdb(watermark="2026-08-27 06:30:00.000001"))
        state = extraction_state.get_state("100000")
        self.assertEqual(state["watermark"], "2026-08-27 06:30:00.000001")
        self.assertEqual(state["batch_id"], "b-42")

    def test_watermark_degrades_to_empty_on_error(self):
        # over-delivery, never a gap: unreadable state = baseline
        class BrokenDdb:
            def get_item(self, TableName, Key):
                raise RuntimeError("throttled")

        os.environ["BEACON_TABLE_EXTRACTION_STATE"] = "state-table"
        extraction_state.set_client_for_testing(BrokenDdb())
        self.assertEqual(extraction_state.watermark("100000"), "")
