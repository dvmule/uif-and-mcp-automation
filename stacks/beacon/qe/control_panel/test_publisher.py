# test_publisher.py
# The publish→S3 / Run-now seam, with fake AWS clients (no AWS in CI).
# Also proves the disabled path: without BEACON_DATA_BUCKET the publish flow
# still works catalog-only and says so honestly.

import json
from datetime import datetime, timezone
import os
import unittest

from fastapi.testclient import TestClient

from control_panel import lifecycle, publisher, validation
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
        self.stopped = []

    def start_execution(self, stateMachineArn, name, input):
        arn = f"arn:aws:states:eu-west-1:1:execution:m:{name}"
        self.executions[arn] = {"status": "RUNNING", "input": input, "name": name}
        return {"executionArn": arn}

    def describe_execution(self, executionArn):
        record = self.executions[executionArn]
        return {"status": record["status"], "input": record.get("input", "{}"),
                "name": record.get("name", ""),
                # UIFPE-720: boto3 returns the start as a datetime
                "startDate": datetime(2026, 9, 30, 9, 12, 0, tzinfo=timezone.utc)}

    def stop_execution(self, executionArn, error=None, cause=None):
        self.executions[executionArn]["status"] = "ABORTED"
        self.stopped.append(executionArn)
        return {}

    def get_execution_history(self, executionArn, reverseOrder=False, maxResults=100):
        """Execution history as the GLUE transform state writes it (UIFPE-209
        — the only transform runtime since UIFPE-215 deleted Fargate)."""
        import json as j
        return {"events": [{"taskSubmittedEventDetails": {
            "resourceType": "glue",
            "resource": "startJobRun.sync:uif-beacon-dev-transform",
            "output": j.dumps({"JobRunId": "jr_abc123"}),
        }}]}


class FakeGlue:
    def __init__(self):
        self.stopped = []

    def batch_stop_job_run(self, JobName, JobRunIds):
        self.stopped.append((JobName, JobRunIds))
        return {"SuccessfulSubmissions": [{"JobRunId": JobRunIds[0]}]}


class TestPublisherWire(unittest.TestCase):
    def setUp(self):
        lifecycle.reset()
        self.s3, self.sfn, self.glue = FakeS3(), FakeSfn(), FakeGlue()
        publisher.set_clients_for_testing(self.s3, self.sfn, self.glue)
        os.environ["BEACON_DATA_BUCKET"] = "demo-bucket"
        os.environ["BEACON_CONFIG_PREFIX"] = "beacon-demo/configs"
        os.environ["BEACON_STATE_MACHINE_ARN"] = \
            "arn:aws:states:eu-west-1:1:stateMachine:m"
        os.environ["BEACON_TRANSFORM_JOB_NAME"] = "uif-beacon-dev-transform"

    def tearDown(self):
        for key in ("BEACON_DATA_BUCKET", "BEACON_CONFIG_PREFIX",
                    "BEACON_STATE_MACHINE_ARN", "BEACON_TRANSFORM_JOB_NAME"):
            os.environ.pop(key, None)
        publisher.set_clients_for_testing(None, None)

    def _publish_one(self):
        draft = make_draft()
        # validated => published (UIFPE-195): submit returns the version
        return client.post(f"/drafts/{draft['id']}/submit").json()

    def test_stop_running_batch(self):
        # UIFPE-203/209: aborts the execution AND stops the Glue job run it
        # spawned (BatchStopJobRun), derives identity from the execution input,
        # and the endpoint records the run as stopped — not failed
        run = publisher.start_run(
            "100000", "b-stop1",
            export_config_uri="s3://demo-bucket/beacon-demo/configs/100000/v1/export.json")
        response = client.post(f"/runs/{run['run_id']}/stop")
        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertTrue(result["stopped"])
        self.assertTrue(result["task_stopped"])
        self.assertEqual(result["job_id"], "100000")
        self.assertEqual(result["batch_id"], "b-stop1")
        self.assertEqual(result["started_at"], "2026-09-30T09:12:00+00:00")  # UIFPE-720
        self.assertEqual(self.sfn.stopped, [run["run_id"]])
        self.assertEqual(self.glue.stopped,
                         [("uif-beacon-dev-transform", ["jr_abc123"])])

    def test_run_carries_transform_sizing_from_the_config_table(self):
        # the sizing travels in the execution input, so the state machine can
        # pass it to StartJobRun as overrides without reading DynamoDB itself
        self._publish_one() if validation.ENGINE_AVAILABLE else None
        run = publisher.start_run(
            "100000", "b-sizing",
            export_config_uri="s3://demo-bucket/beacon-demo/configs/100000/v1/export.json")
        sent = json.loads(self.sfn.executions[run["run_id"]]["input"])
        self.assertEqual(sent["job_config"], publisher.DEFAULT_JOB_CONFIG)
        self.assertEqual(run["job_config"]["worker_type"], "G.1X")

    def test_sizing_falls_back_when_the_table_is_unset(self):
        # sizing is an optimization, never a reason to fail a batch
        os.environ.pop("BEACON_TABLE_GLUE_CONFIG", None)
        self.assertEqual(publisher.job_config(), publisher.DEFAULT_JOB_CONFIG)

    def test_stop_refuses_finished_batch(self):
        run = publisher.start_run(
            "100000", "b-stop2",
            export_config_uri="s3://demo-bucket/beacon-demo/configs/100000/v1/export.json")
        self.sfn.executions[run["run_id"]]["status"] = "SUCCEEDED"
        response = client.post(f"/runs/{run['run_id']}/stop")
        self.assertEqual(response.status_code, 409)
        self.assertIn("SUCCEEDED", response.json()["detail"])
        self.assertEqual(self.glue.stopped, [])

    @unittest.skipUnless(validation.ENGINE_AVAILABLE, "needs engine")
    def test_publish_writes_immutable_config_pair_no_live_pointer(self):
        version = self._publish_one()
        self.assertTrue(version["s3"]["published"])
        keys = set(self.s3.objects)
        base = "demo-bucket/beacon-demo/configs/100000/v1"
        self.assertIn(f"{base}/mapping.json", keys)
        self.assertIn(f"{base}/export.json", keys)
        # D22: the mutable shared pointer is gone
        self.assertNotIn("demo-bucket/beacon-demo/configs/mapping.json", keys)
        self.assertEqual(
            version["s3"]["export_config_uri"],
            "s3://demo-bucket/beacon-demo/configs/100000/v1/export.json")

    @unittest.skipUnless(validation.ENGINE_AVAILABLE, "needs engine")
    def test_generated_export_config_is_valid_and_paths_normalized(self):
        import json as j
        self._publish_one()
        base = "demo-bucket/beacon-demo/configs/100000/v1"
        export_cfg = j.loads(self.s3.objects[f"{base}/export.json"])
        # generated config passes the pipeline's own validator
        from beacon_export import export_config as bec
        self.assertIsNone(bec.validate(export_cfg))
        self.assertEqual(export_cfg["mapping_config_s3_path"], f"s3://{base}/mapping.json")
        # environment-owned paths normalized into the mapping (N1 parity)
        mapping_cfg = j.loads(self.s3.objects[f"{base}/mapping.json"])
        self.assertEqual(mapping_cfg["output"]["targets"][0]["path"],
                         "s3://demo-bucket/staging/${batch_id}/canonical/")
        self.assertEqual(
            mapping_cfg["output"]["targets"][0]["path"].replace("${batch_id}", "X"),
            export_cfg["canonical_s3_prefix"].replace("{batch_id}", "X"))

    @unittest.skipUnless(validation.ENGINE_AVAILABLE, "needs engine")
    def test_run_now_and_status(self):
        self._publish_one()
        run = client.post("/catalog/100000/run", json={}).json()
        self.assertTrue(run["started"])
        self.assertTrue(run["batch_id"].startswith("b-"))
        self.assertEqual(run["version"], 1)
        self.assertEqual(run["engine"], "stepfunctions")
        # execution name = batch id (the whole flow shares it)
        self.assertIn(run["batch_id"], run["run_id"])
        status = client.get(f"/runs/{run['run_id']}").json()
        self.assertEqual(status["state"], "RUNNING")

    def test_run_requires_published_version(self):
        response = client.post("/catalog/ghost-job/run", json={})
        self.assertEqual(response.status_code, 404)

    @unittest.skipUnless(validation.ENGINE_AVAILABLE, "needs engine")
    def test_chosen_delimiter_reaches_published_config(self):
        # UIFPE-171: the author's delimiter choice (settings.format_options)
        # overrides the safe default. The published config carries the LITERAL
        # character, never the symbolic name — pipeline images that predate
        # the names must render it correctly (deploy-skew safety).
        import json as j
        draft = make_draft(settings={"formats": ["csv"],
                                     "format_options": {"csv": {"delimiter": "tab"}}})
        client.post(f"/drafts/{draft['id']}/submit")
        base = "demo-bucket/beacon-demo/configs/100000/v1"
        options = j.loads(self.s3.objects[f"{base}/export.json"])["renderings"][0]["options"]
        self.assertEqual(options["delimiter"], "\t")
        self.assertTrue(options["header"])  # untouched defaults survive the merge
        self.assertTrue(options["formula_escape"])

    @unittest.skipUnless(validation.ENGINE_AVAILABLE, "needs engine")
    def test_delivered_filenames_carry_export_id_and_name(self):
        # UIFPE-196: names may repeat, so the id keeps two same-named
        # exports' delivered files distinguishable at one destination
        import json as j
        self._publish_one()
        base = "demo-bucket/beacon-demo/configs/100000/v1"
        pattern = j.loads(self.s3.objects[f"{base}/export.json"])["packaging"]["filename_pattern"]
        self.assertTrue(pattern.startswith("100000_product_export_"), pattern)

    @unittest.skipUnless(validation.ENGINE_AVAILABLE, "needs engine")
    def test_default_delimiter_is_comma(self):
        import json as j
        self._publish_one()
        base = "demo-bucket/beacon-demo/configs/100000/v1"
        options = j.loads(self.s3.objects[f"{base}/export.json"])["renderings"][0]["options"]
        self.assertEqual(options["delimiter"], ",")

    @unittest.skipUnless(validation.ENGINE_AVAILABLE, "needs engine")
    def test_manifest_defaults_off_and_opts_in(self):
        # UIFPE-258: the manifest is opt-in per feed. Default publish -> no
        # manifest; settings.packaging.manifest true -> the pre-258 behavior.
        import json as j
        self._publish_one()
        base = "demo-bucket/beacon-demo/configs/100000/v1"
        packaging = j.loads(self.s3.objects[f"{base}/export.json"])["packaging"]
        self.assertFalse(packaging["manifest"]["enabled"])

        lifecycle.reset()
        draft = make_draft(settings={"formats": ["csv"],
                                     "packaging": {"manifest": True}})
        client.post(f"/drafts/{draft['id']}/submit")
        packaging = j.loads(self.s3.objects[f"{base}/export.json"])["packaging"]
        self.assertTrue(packaging["manifest"]["enabled"])
        self.assertEqual(packaging["manifest"]["checksum"], "sha256")
        self.assertTrue(packaging["zip"]["enabled"])  # untouched default

    @unittest.skipUnless(validation.ENGINE_AVAILABLE, "needs engine")
    def test_non_boolean_manifest_blocks_publish_gracefully(self):
        draft = make_draft(settings={"formats": ["csv"],
                                     "packaging": {"manifest": "yes"}})
        version = client.post(f"/drafts/{draft['id']}/submit").json()
        self.assertFalse(version["s3"]["published"])
        self.assertIn("manifest", version["s3"]["reason"])

    @unittest.skipUnless(validation.ENGINE_AVAILABLE, "needs engine")
    def test_malformed_format_options_blocks_publish_gracefully(self):
        # Author-controlled JSON with the wrong SHAPE must take the same
        # graceful {published: false, reason} path as a wrong value — never
        # a 500 after the catalog version is already promoted.
        for bad in ("tab", {"csv": "tab"}, {"csv": ["tab"]}):
            lifecycle.reset()
            draft = make_draft(settings={"formats": ["csv"],
                                         "format_options": bad})
            response = client.post(f"/drafts/{draft['id']}/submit")
            self.assertEqual(response.status_code, 200, f"500 on {bad!r}")
            version = response.json()
            self.assertFalse(version["s3"]["published"], f"published {bad!r}")
            self.assertIn("format_options", version["s3"]["reason"])

    @unittest.skipUnless(validation.ENGINE_AVAILABLE, "needs engine")
    def test_null_settings_publishes_with_defaults(self):
        import json as j
        draft = make_draft(settings=None)
        version = client.post(f"/drafts/{draft['id']}/submit").json()
        self.assertTrue(version["s3"]["published"])
        base = "demo-bucket/beacon-demo/configs/100000/v1"
        options = j.loads(self.s3.objects[f"{base}/export.json"])["renderings"][0]["options"]
        self.assertEqual(options["delimiter"], ",")

    @unittest.skipUnless(validation.ENGINE_AVAILABLE, "needs engine")
    def test_zip_default_on_in_published_config(self):
        # UIFPE-172: newly published configs carry an EXPLICIT zip block,
        # default-on — the engine's missing-key default stays False so
        # pre-feature immutable versions keep uncompressed behavior.
        import json as j
        self._publish_one()
        # UIFPE-196: identity is the minted export id, not the name
        base = "demo-bucket/beacon-demo/configs/100000/v1"
        pkg = j.loads(self.s3.objects[f"{base}/export.json"])["packaging"]
        self.assertEqual(pkg["zip"], {"enabled": True})

    @unittest.skipUnless(validation.ENGINE_AVAILABLE, "needs engine")
    def test_zip_opt_out_respected(self):
        import json as j
        draft = make_draft(settings={"formats": ["csv"],
                                     "packaging": {"zip": False}})
        client.post(f"/drafts/{draft['id']}/submit")
        base = "demo-bucket/beacon-demo/configs/100000/v1"
        pkg = j.loads(self.s3.objects[f"{base}/export.json"])["packaging"]
        self.assertEqual(pkg["zip"], {"enabled": False})

    @unittest.skipUnless(validation.ENGINE_AVAILABLE, "needs engine")
    def test_malformed_packaging_blocks_publish_gracefully(self):
        # falsy shapes included: "packaging": false is a plausible opt-out
        # shorthand and must fail loudly, never silently publish zip-enabled
        for bad in ("zip", {"zip": "nope"}, {"zip": 1}, False, 0, "", []):
            lifecycle.reset()
            draft = make_draft(settings={"formats": ["csv"],
                                         "packaging": bad})
            response = client.post(f"/drafts/{draft['id']}/submit")
            self.assertEqual(response.status_code, 200, f"500 on {bad!r}")
            version = response.json()
            self.assertFalse(version["s3"]["published"], f"published {bad!r}")
            self.assertIn("packaging", version["s3"]["reason"])

    @unittest.skipUnless(validation.ENGINE_AVAILABLE, "needs engine")
    def test_multiple_targets_publish_as_a_deliveries_list(self):
        # UIFPE-170: several targets publish under `deliveries`; the single
        # `delivery` key is absent so the two shapes never coexist
        import json as j
        draft = make_draft(settings={"formats": ["csv"], "deliveries": [
            {"channel": "s3", "destination_s3_prefix": "s3://a/in"},
            {"channel": "s3", "destination_s3_prefix": "s3://b/in"}]})
        version = client.post(f"/drafts/{draft['id']}/submit").json()
        self.assertTrue(version["s3"]["published"])
        base = "demo-bucket/beacon-demo/configs/100000/v1"
        cfg = j.loads(self.s3.objects[f"{base}/export.json"])
        self.assertNotIn("delivery", cfg)
        self.assertEqual([t["destination_s3_prefix"] for t in cfg["deliveries"]],
                         ["s3://a/in", "s3://b/in"])

    @unittest.skipUnless(validation.ENGINE_AVAILABLE, "needs engine")
    def test_a_single_target_list_publishes_the_original_delivery_shape(self):
        # one target = the exact pre-170 config shape, even when the wizard
        # sent it as a list — old and new engines read it identically
        import json as j
        draft = make_draft(settings={"formats": ["csv"], "deliveries": [
            {"channel": "s3", "destination_s3_prefix": "s3://a/in"}]})
        version = client.post(f"/drafts/{draft['id']}/submit").json()
        self.assertTrue(version["s3"]["published"])
        base = "demo-bucket/beacon-demo/configs/100000/v1"
        cfg = j.loads(self.s3.objects[f"{base}/export.json"])
        self.assertNotIn("deliveries", cfg)
        self.assertEqual(cfg["delivery"]["destination_s3_prefix"], "s3://a/in")

    @unittest.skipUnless(validation.ENGINE_AVAILABLE, "needs engine")
    def test_both_delivery_shapes_block_publish_gracefully(self):
        draft = make_draft(settings={
            "formats": ["csv"],
            "delivery": {"channel": "s3", "destination_s3_prefix": "s3://a/in"},
            "deliveries": [{"channel": "s3",
                            "destination_s3_prefix": "s3://b/in"}]})
        version = client.post(f"/drafts/{draft['id']}/submit").json()
        self.assertFalse(version["s3"]["published"])
        self.assertIn("deliveries", version["s3"]["reason"])

    @unittest.skipUnless(validation.ENGINE_AVAILABLE, "needs engine")
    def test_duplicate_targets_block_publish(self):
        # delivering one batch to the same place twice is never intended
        draft = make_draft(settings={"formats": ["csv"], "deliveries": [
            {"channel": "s3", "destination_s3_prefix": "s3://a/in"},
            {"channel": "s3", "destination_s3_prefix": "s3://a/in"}]})
        version = client.post(f"/drafts/{draft['id']}/submit").json()
        self.assertFalse(version["s3"]["published"])
        self.assertIn("duplicates", version["s3"]["reason"])

    @unittest.skipUnless(validation.ENGINE_AVAILABLE, "needs engine")
    def test_malformed_deliveries_blocks_publish_gracefully(self):
        for bad in ("s3", {"channel": "s3"}, [], ["s3"], 1):
            lifecycle.reset()
            draft = make_draft(settings={"formats": ["csv"],
                                         "deliveries": bad})
            response = client.post(f"/drafts/{draft['id']}/submit")
            self.assertEqual(response.status_code, 200, f"500 on {bad!r}")
            version = response.json()
            self.assertFalse(version["s3"]["published"], f"published {bad!r}")
            self.assertIn("deliveries", version["s3"]["reason"])

    @unittest.skipUnless(validation.ENGINE_AVAILABLE, "needs engine")
    def test_unsupported_delimiter_blocks_publish(self):
        # An invalid choice must never publish — the pipeline's own validator
        # rejects the generated config and publish reports why.
        draft = make_draft(settings={"formats": ["csv"],
                                     "format_options": {"csv": {"delimiter": ";"}}})
        version = client.post(f"/drafts/{draft['id']}/submit").json()
        self.assertFalse(version["s3"]["published"])
        self.assertIn("delimiter", version["s3"]["reason"])

    @unittest.skipUnless(validation.ENGINE_AVAILABLE, "needs engine")
    def test_disabled_publisher_is_honest(self):
        del os.environ["BEACON_DATA_BUCKET"]
        version = self._publish_one()
        self.assertFalse(version["s3"]["published"])
        self.assertIn("catalog-only", version["s3"]["reason"])
        # catalog still updated despite no S3
        self.assertEqual(client.get("/catalog").json()[0]["version"], 1)


class FakeS3WithListing(FakeS3):
    def __init__(self, keys):
        super().__init__()
        self.keys = keys  # key -> size
        self.presigned_keys = []  # every key a URL was minted for

    def list_objects_v2(self, Bucket, Prefix):
        return {"Contents": [{"Key": k, "Size": s} for k, s in self.keys.items()
                             if k.startswith(Prefix)]}

    def generate_presigned_url(self, op, Params, ExpiresIn):
        self.presigned_keys.append(Params["Key"])
        return f"https://signed.example/{Params['Key']}?ttl={ExpiresIn}"


class TestBatchFiles(unittest.TestCase):
    def setUp(self):
        lifecycle.reset()
        self.s3 = FakeS3WithListing({
            "beacon-demo/pickup/b-1/export_b-1.csv": 100,
            "beacon-demo/pickup/b-1/export_b-1.manifest.json": 20,
        })
        publisher.set_clients_for_testing(self.s3, None)
        os.environ["BEACON_DATA_BUCKET"] = "demo-bucket"
        os.environ["BEACON_PICKUP_PREFIX"] = "beacon-demo/pickup"

    def tearDown(self):
        for key in ("BEACON_DATA_BUCKET", "BEACON_PICKUP_PREFIX"):
            os.environ.pop(key, None)
        publisher.set_clients_for_testing(None, None)

    @unittest.skipUnless(validation.ENGINE_AVAILABLE, "needs engine")
    def test_files_listed_with_presigned_urls(self):
        draft = make_draft()
        client.post(f"/drafts/{draft['id']}/submit")  # validated => published

        result = client.get("/catalog/100000/batches/b-1/files").json()
        names = {f["name"] for f in result["files"]}
        # UIFPE-202: the manifest is internal — data files only
        self.assertEqual(names, {"export_b-1.csv"})
        self.assertTrue(all(f["url"].startswith("https://signed.example/")
                            for f in result["files"]))
        self.assertTrue(all(f["expires_seconds"] == 900 for f in result["files"]))

    @unittest.skipUnless(validation.ENGINE_AVAILABLE, "needs engine")
    def test_manifest_never_listed_or_signed(self):
        """UIFPE-202: the batch manifest must not appear in the download
        listing, and no presigned URL may be minted for it — filtering
        happens before signing, not after."""
        draft = make_draft()
        client.post(f"/drafts/{draft['id']}/submit")  # validated => published

        result = client.get("/catalog/100000/batches/b-1/files").json()
        self.assertFalse([f for f in result["files"]
                          if f["name"].endswith(".manifest.json")])
        self.assertFalse([k for k in self.s3.presigned_keys
                          if k.endswith(".manifest.json")])

    @unittest.skipUnless(validation.ENGINE_AVAILABLE, "needs engine")
    def test_unknown_batch_404(self):
        draft = make_draft()
        client.post(f"/drafts/{draft['id']}/submit")  # validated => published
        response = client.get("/catalog/100000/batches/b-GHOST/files")
        self.assertEqual(response.status_code, 404)

    def test_unpublished_job_404(self):
        response = client.get("/catalog/ghost/batches/b-1/files")
        self.assertEqual(response.status_code, 404)


class TestBatchFilesManifestFilter(unittest.TestCase):
    """UIFPE-202, at the publisher seam directly — no API, no engine, so
    these run even where dynamic_mapping isn't installed."""

    def setUp(self):
        os.environ["BEACON_DATA_BUCKET"] = "demo-bucket"
        os.environ["BEACON_PICKUP_PREFIX"] = "beacon-demo/pickup"

    def tearDown(self):
        for key in ("BEACON_DATA_BUCKET", "BEACON_PICKUP_PREFIX"):
            os.environ.pop(key, None)
        publisher.set_clients_for_testing(None, None)

    def test_manifest_excluded_from_listing(self):
        s3 = FakeS3WithListing({
            "beacon-demo/pickup/b-9/export_b-9.csv.zip": 5000,
            "beacon-demo/pickup/b-9/export_b-9.manifest.json": 20,
        })
        publisher.set_clients_for_testing(s3, None)
        result = publisher.batch_files("100000", "b-9")
        self.assertEqual([f["name"] for f in result["files"]],
                         ["export_b-9.csv.zip"])
        self.assertEqual(s3.presigned_keys,
                         ["beacon-demo/pickup/b-9/export_b-9.csv.zip"])

    def test_manifest_only_batch_yields_no_files(self):
        # the API route turns an empty files list into its existing 404
        s3 = FakeS3WithListing({
            "beacon-demo/pickup/b-9/export_b-9.manifest.json": 20,
        })
        publisher.set_clients_for_testing(s3, None)
        result = publisher.batch_files("100000", "b-9")
        self.assertEqual(result["files"], [])
        self.assertEqual(s3.presigned_keys, [])


class TestRunNowStepFunctions(unittest.TestCase):
    def setUp(self):
        lifecycle.reset()
        self.sfn = FakeSfn()
        publisher.set_clients_for_testing(FakeS3(), self.sfn)
        os.environ["BEACON_DATA_BUCKET"] = "demo-bucket"
        os.environ["BEACON_STATE_MACHINE_ARN"] = "arn:aws:states:eu-west-1:1:stateMachine:m"

    def tearDown(self):
        for key in ("BEACON_DATA_BUCKET", "BEACON_STATE_MACHINE_ARN"):
            os.environ.pop(key, None)
        publisher.set_clients_for_testing(None, None)

    @unittest.skipUnless(validation.ENGINE_AVAILABLE, "needs engine")
    def test_run_now_prefers_step_functions(self):
        draft = make_draft()
        client.post(f"/drafts/{draft['id']}/submit")  # validated => published

        run = client.post("/catalog/100000/run", json={}).json()
        self.assertEqual(run["engine"], "stepfunctions")
        self.assertTrue(run["run_id"].startswith("arn:aws:states:"))
        # execution name = batch id (the whole flow shares it)
        self.assertIn(run["batch_id"], run["run_id"])
        # D22: the execution input is THE PUBLISHED VERSION's config
        self.assertIn("configs/100000/v1/export.json",
                      self.sfn.executions[run["run_id"]]["input"])

        status = client.get(f"/runs/{run['run_id']}").json()
        self.assertEqual(status["state"], "RUNNING")

    @unittest.skipUnless(validation.ENGINE_AVAILABLE, "needs engine")
    def test_run_pinned_version(self):
        # publish v1, then v2 the way the future Edit flow will: a draft
        # carrying the SAME export_id republishes as the next version
        v1 = client.post(f"/drafts/{make_draft()['id']}/submit").json()
        edit = make_draft()
        stored = lifecycle.get_draft(edit["id"])
        stored["export_id"], stored["job_id"] = v1["export_id"], v1["job_id"]
        lifecycle._save_draft(stored)
        client.post(f"/drafts/{edit['id']}/submit")

        run = client.post("/catalog/100000/run",
                          json={"version": 1, "batch_id": "pin-1"}).json()
        self.assertEqual(run["version"], 1)
        self.assertIn("/v1/export.json",
                      self.sfn.executions[run["run_id"]]["input"])

        latest = client.post("/catalog/100000/run",
                             json={"batch_id": "pin-2"}).json()
        self.assertEqual(latest["version"], 2)
        self.assertIn("/v2/export.json",
                      self.sfn.executions[latest["run_id"]]["input"])
