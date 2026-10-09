# test_sync_pipelines.py
# Unit tests for the config-as-code sync tool (scripts/sync_pipelines.py).

import unittest

import gold_config
import sync_pipelines


class TestPreserveRuntimeFields(unittest.TestCase):
    def test_runtime_values_survive_sync(self):
        desired = gold_config.auto_init({
            "gold_id": "product", "domain": "product",
            "schema": {"master_s3_path": "s3://a/m.json", "version": "v1"},
            "mapping": {"manifest_s3_path": "s3://a/m.yaml",
                        "sql_s3_path": "s3://a/p.sql"},
            "sources": [{"alias": "items", "silver_table": "s.t",
                         "is_source_of_truth": True}],
            "target": {"database": "g", "table": "product",
                       "s3_path": "s3://g/p/", "primary_key_cols": ["id"]},
        })
        existing = {
            "watermark": {"last_processed_silver_updated_at": {
                "items": "2026-07-20T10:00:00"}},
            "channels": {"registered": ["api", "canonical"],
                         "bootstrap_mode": "silent"},
            "materialized_properties": ["id", "name"],
            "publish_cursors": {"api": "2026-07-20T10:00:00Z"},
            "metadata": {"registered_at": "2026-06-01T00:00:00Z",
                         "last_run_id": "jr_123",
                         "last_run_at": "2026-07-20T10:00:00Z"},
        }
        out = sync_pipelines.preserve_runtime_fields(desired, existing)
        self.assertEqual(
            out["watermark"]["last_processed_silver_updated_at"]["items"],
            "2026-07-20T10:00:00")
        self.assertEqual(out["channels"]["registered"], ["api", "canonical"])
        self.assertEqual(out["materialized_properties"], ["id", "name"])
        self.assertEqual(out["publish_cursors"], {"api": "2026-07-20T10:00:00Z"})
        self.assertEqual(out["metadata"]["last_run_id"], "jr_123")
        self.assertEqual(out["metadata"]["registered_at"], "2026-06-01T00:00:00Z")
        # desired-owned fields untouched by preservation
        self.assertEqual(out["channels"]["bootstrap_mode"], "silent")
        self.assertEqual(out["gold_id"], "product")

    def test_console_owned_schedule_and_enabled_survive_sync(self):
        """UIFPE-513, and UIFPE-564 for the cadence: the operator sets all of
        these from the Syncs page, so YAML must not roll them back. This test
        asserted the opposite for `frequency` until 2026-09-24, when the owner
        made the cadence console-owned - the YAML value now seeds a NEW entity
        only, and a domain team editing it for an existing one sees no effect."""
        desired = {"enabled": True,
                   "schedule": {"frequency": "gold-24h", "start_time": "06:30"}}
        existing = {"enabled": False,
                    "schedule": {"frequency": "gold-1h", "start_time": "08:00"}}
        out = sync_pipelines.preserve_runtime_fields(desired, existing)
        self.assertFalse(out["enabled"])
        self.assertEqual(out["schedule"]["start_time"], "08:00")
        self.assertEqual(out["schedule"]["frequency"], "gold-1h")
        self.assertIn("schedule.frequency", sync_pipelines.RUNTIME_PATHS)

    def test_a_new_entity_still_takes_the_yaml_cadence(self):
        """The other half of the same rule: with no existing row there is
        nothing to preserve, so the YAML's cadence is what gets written."""
        desired = {"schedule": {"frequency": "gold-24h", "start_time": "06:30"}}
        out = sync_pipelines.preserve_runtime_fields(desired, {})
        self.assertEqual(out["schedule"]["frequency"], "gold-24h")

    def test_console_owned_weekly_days_survive_sync(self):
        """UIFPE-532: the weekly day set is set from the schedule dialog, so a
        sync must not roll it back to the YAML's (or auto_init's) Monday."""
        desired = {"schedule": {"frequency": "gold-weekly",
                                "weekly_days": ["MON"], "start_time": "06:30"}}
        existing = {"schedule": {"frequency": "gold-weekly",
                                 "weekly_days": ["TUE", "THU"], "start_time": "03:00"}}
        out = sync_pipelines.preserve_runtime_fields(desired, existing)
        self.assertEqual(out["schedule"]["weekly_days"], ["TUE", "THU"])
        self.assertEqual(out["schedule"]["start_time"], "03:00")
        self.assertIn("schedule.weekly_days", sync_pipelines.RUNTIME_PATHS)

    def test_missing_runtime_fields_keep_desired_defaults(self):
        desired = {"watermark": {"last_processed_silver_updated_at": {"a": "x"}},
                   "metadata": {"owner": "me"}}
        out = sync_pipelines.preserve_runtime_fields(desired, {})
        self.assertEqual(
            out["watermark"]["last_processed_silver_updated_at"], {"a": "x"})
        self.assertEqual(out["metadata"], {"owner": "me"})

    def test_snapshot_gate_state_survives_preservation(self):
        """F1: the gate's last-seen map lives under `watermark` precisely
        because that attribute is preserved wholesale — pin that placement.
        If someone moves the map or narrows the preserved path, this fails."""
        desired = {"watermark": {}}
        existing = {"watermark": {
            "last_processed_silver_updated_at": {"items": "2026-08-07"},
            "silver_snapshots": {"items": "s3://m/00042.metadata.json"}}}
        out = sync_pipelines.preserve_runtime_fields(desired, existing)
        self.assertEqual(out["watermark"]["silver_snapshots"],
                         {"items": "s3://m/00042.metadata.json"})


class TestDiffKeys(unittest.TestCase):
    def test_detects_changed_and_added_keys(self):
        desired = {"a": 1, "b": {"x": 2}, "c": 3}
        existing = {"a": 1, "b": {"x": 9}}
        self.assertEqual(sync_pipelines.diff_keys(desired, existing), ["b", "c"])

    def test_identical_yields_empty(self):
        d = {"a": [1, 2], "b": {"x": True}}
        self.assertEqual(sync_pipelines.diff_keys(d, dict(d)), [])

    def test_decimal_vs_int_compare_stable(self):
        existing = sync_pipelines.from_dynamo(
            sync_pipelines.to_dynamo_safe({"glue": {"num_workers": 16,
                                                    "max_affected_fraction": 0.5}}))
        desired = {"glue": {"num_workers": 16, "max_affected_fraction": 0.5}}
        self.assertEqual(sync_pipelines.diff_keys(desired, existing), [])


class TestLoadDesiredRealRepo(unittest.TestCase):
    """Integration: the committed pipelines/ definitions must load, merge,
    and validate for the dev environment."""

    def test_dev_product_loads_and_validates(self):
        desired = sync_pipelines.load_desired("dev")
        self.assertIn("product", desired)
        cfg = desired["product"]
        self.assertEqual(cfg["target"]["s3_path"], "s3://uif-echo-dev-gold/product/")
        self.assertEqual(cfg["schema"]["version"], "v1")
        # auto-init ran
        self.assertEqual(cfg["version"], "latest")
        # runtime-owned (ADR-0020): no seed, the first run writes the bookmark
        self.assertEqual(cfg["watermark"], {})
        # source-of-truth intact through merge
        sot = [s for s in cfg["sources"] if s.get("is_source_of_truth")]
        self.assertEqual(len(sot), 1)

    def test_env_without_overlay_skips_pipeline(self):
        """A SYNTHETIC environment name, deliberately not a real one.

        This passed "uat" until UIFPE-619 added uat overlays for price,
        product, marketing_copy and program — correct work that turned the
        assertion false and left develop red, because the test had encoded
        "uat has no overlays" as if it were the property under test.

        It is not. The property is that an environment with no overlay for a
        pipeline skips that pipeline, and which real environments happen to
        have overlays is incidental to it. So the name is one that cannot
        acquire overlays, and adding the next real environment cannot break
        this again.
        """
        desired = sync_pipelines.load_desired("nosuchenv")
        self.assertEqual(desired, {})


# ── Gold-database LF tag (folded into arming, 2026-09-02) ────────────────────
# The sync ensures every managed pipeline's gold database carries
# uif-echo-gold-layer={env}, so consuming stacks' Terraform (Beacon's gold grants)
# never needs a per-domain admin ask. Stubbed clients — the contract under
# test is: skip a database that doesn't exist yet, no-op an already-tagged
# one, tag only on --apply, and WARN (never raise) on any LF problem.

from botocore.exceptions import ClientError  # noqa: E402


def _client_error(code, op="Op"):
    return ClientError({"Error": {"Code": code}}, op)


class StubGlue:
    def __init__(self, exists=True, error=None):
        self._exists, self._error = exists, error

    def get_database(self, Name):
        if self._error:
            raise _client_error(self._error, "GetDatabase")
        if not self._exists:
            raise _client_error("EntityNotFoundException", "GetDatabase")
        return {"Database": {"Name": Name}}


class StubLF:
    def __init__(self, tags=None, get_error=None, add_failures=None):
        self._tags = tags or []
        self._get_error = get_error
        self._add_failures = add_failures or []
        self.added = []

    def get_resource_lf_tags(self, Resource):
        if self._get_error:
            raise _client_error(self._get_error, "GetResourceLFTags")
        return {"LFTagOnDatabase": self._tags}

    def add_lf_tags_to_resource(self, Resource, LFTags):
        self.added.append((Resource, LFTags))
        return {"Failures": self._add_failures}


class TestEnsureGoldDbTag(unittest.TestCase):
    DB = "uif_echo_dev_gold_price"

    def test_database_not_created_yet_is_skipped_without_lf_calls(self):
        lf = StubLF(get_error="ShouldNeverBeCalled")
        line = sync_pipelines.ensure_gold_db_tag(self.DB, "dev", apply=True, lf=lf, glue=StubGlue(exists=False))
        self.assertIn("SKIP", line)
        self.assertIn("first", line)
        self.assertEqual(lf.added, [])

    def test_already_tagged_is_a_noop(self):
        lf = StubLF(tags=[{"TagKey": "uif-echo-gold-layer", "TagValues": ["dev"]}])
        line = sync_pipelines.ensure_gold_db_tag(self.DB, "dev", apply=True, lf=lf, glue=StubGlue())
        self.assertIn("OK", line)
        self.assertEqual(lf.added, [])

    def test_untagged_plans_without_apply_and_tags_with_it(self):
        lf = StubLF()
        line = sync_pipelines.ensure_gold_db_tag(self.DB, "dev", apply=False, lf=lf, glue=StubGlue())
        self.assertIn("TAG", line)
        self.assertEqual(lf.added, [])          # plan mode writes nothing
        line = sync_pipelines.ensure_gold_db_tag(self.DB, "dev", apply=True, lf=lf, glue=StubGlue())
        self.assertIn("TAGGED", line)
        (resource, lftags), = lf.added
        self.assertEqual(resource, {"Database": {"Name": self.DB}})
        self.assertEqual(lftags, [{"TagKey": "uif-echo-gold-layer",
                                   "TagValues": ["dev"]}])

    def test_wrong_tag_value_is_retagged(self):
        lf = StubLF(tags=[{"TagKey": "uif-echo-gold-layer", "TagValues": ["uat"]}])
        line = sync_pipelines.ensure_gold_db_tag(self.DB, "dev", apply=True, lf=lf, glue=StubGlue())
        self.assertIn("TAGGED", line)
        self.assertEqual(len(lf.added), 1)

    def test_lf_problems_warn_and_never_raise(self):
        # tag key absent in the account (uat/prod before one-time setup)
        line = sync_pipelines.ensure_gold_db_tag(self.DB, "dev", apply=True,
            lf=StubLF(get_error="EntityNotFoundException"), glue=StubGlue())
        self.assertIn("WARN", line)
        self.assertIn("gold-control-plane", line)
        # permission missing on the deploy role
        line = sync_pipelines.ensure_gold_db_tag(self.DB, "dev", apply=True,
            lf=StubLF(get_error="AccessDeniedException"), glue=StubGlue())
        self.assertIn("WARN", line)
        # a glue failure OTHER than not-found is a warn, not a skip
        line = sync_pipelines.ensure_gold_db_tag(self.DB, "dev", apply=True, lf=StubLF(),
            glue=StubGlue(error="AccessDeniedException"))
        self.assertIn("WARN", line)
        # NON-ClientError failures (endpoint unreachable) must warn too — a
        # raise here would block the config writes the script exists to make
        class Down:
            def get_resource_lf_tags(self, Resource):
                raise ConnectionError("endpoint unreachable")
        line = sync_pipelines.ensure_gold_db_tag(self.DB, "dev", apply=True, lf=Down(), glue=StubGlue())
        self.assertIn("WARN", line)
        self.assertIn("ConnectionError", line)

    def test_partial_add_failures_warn(self):
        lf = StubLF(add_failures=[{"LFTag": {"TagKey": "uif-echo-gold-layer"},
                                   "Error": {"ErrorCode": "AccessDenied"}}])
        line = sync_pipelines.ensure_gold_db_tag(self.DB, "dev", apply=True, lf=lf, glue=StubGlue())
        self.assertIn("WARN", line)


class TestDefaultGoldConfigTable(unittest.TestCase):
    def test_standard_name_per_environment(self):
        self.assertEqual(gold_config.default_gold_config_table("dev"),
                         "uif-echo-dev-gold-pipeline-config")
        self.assertEqual(gold_config.default_gold_config_table("uat"),
                         "uif-echo-uat-gold-pipeline-config")


if __name__ == "__main__":
    unittest.main()
