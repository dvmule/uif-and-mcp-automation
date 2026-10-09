"""gold_resources: every gold name follows the UIF standard, per environment."""
import os
import unittest
from unittest import mock

import gold_resources as gr


class TestNames(unittest.TestCase):
    def test_compute_and_config(self):
        self.assertEqual(gr.gold_config_table("dev"), "uif-echo-dev-gold-pipeline-config")
        self.assertEqual(gr.gold_transform_job("dev"), "uif-echo-dev-gold-transform")
        self.assertEqual(gr.gold_reconcile_job("uat"), "uif-echo-uat-gold-reconcile")
        self.assertEqual(gr.gold_dispatch_function("dev"), "uif-echo-dev-gold-dispatch")
        # UIFPE-543: the notifier, its subscribers, and the prefix every gold alarm shares
        self.assertEqual(gr.gold_alert_notify_function("dev"), "uif-echo-dev-gold-alert-notify")
        self.assertEqual(gr.gold_alert_subscribers_table("dev"), "uif-echo-dev-alert-subscribers")
        self.assertEqual(gr.gold_alarm_prefix("dev"), "uif-echo-dev-gold-")

    def test_stores(self):
        self.assertEqual(gr.gold_artifacts_bucket("dev"), "uif-echo-dev-gold-artifacts")
        self.assertEqual(gr.gold_data_bucket("dev"), "uif-echo-dev-gold")
        self.assertEqual(gr.gold_database("product", "dev"), "uif_echo_dev_gold_product")

    def test_layer_tag_is_one_key_with_the_environment_as_value(self):
        self.assertEqual(gr.gold_layer_tag("dev"), ("uif-echo-gold-layer", "dev"))
        self.assertEqual(gr.gold_layer_tag("prod"), ("uif-echo-gold-layer", "prod"))


class TestWritesChangeLog(unittest.TestCase):
    """What may be announced turns on whether the run appends change-log rows,
    NOT on whether it is 'maintenance'. Conflating the two cost every
    refresh-detected change its event on price, product and program.
    """

    def test_a_plain_incremental_writes_a_change_log(self):
        self.assertTrue(gr.writes_change_log({"--gold_id": "product"}))

    def test_a_rebuild_never_does(self):
        self.assertFalse(gr.writes_change_log({"--rebuild": "true"}))
        # even when a stray refresh mode rides along
        self.assertFalse(gr.writes_change_log(
            {"--rebuild": "true", "--refresh_mode": "emit"}))

    def test_an_emit_refresh_does(self):
        self.assertTrue(gr.writes_change_log(
            {"--refresh_columns": "a,b", "--refresh_mode": "emit"}))

    def test_a_silent_refresh_does_not(self):
        self.assertFalse(gr.writes_change_log(
            {"--refresh_columns": "a,b", "--refresh_mode": "silent"}))

    def test_an_absent_mode_means_emit(self):
        """The transform's own argparse default. A manually started refresh
        carries no --refresh_mode and still writes op=REFRESH rows."""
        self.assertTrue(gr.writes_change_log({"--refresh_columns": "a,b"}))

    def test_mode_is_normalised_like_the_transform_does(self):
        for mode in ("EMIT", " emit ", "Emit"):
            self.assertTrue(gr.writes_change_log(
                {"--refresh_columns": "a", "--refresh_mode": mode}), mode)
        for mode in ("SILENT", " silent "):
            self.assertFalse(gr.writes_change_log(
                {"--refresh_columns": "a", "--refresh_mode": mode}), mode)

    def test_maintenance_args_still_covers_both_for_slot_semantics(self):
        """The dispatcher's rule is unchanged: neither kind satisfies a
        schedule slot. Only the ANNOUNCE rule changed."""
        self.assertEqual(set(gr.MAINTENANCE_ARGS),
                         {"--refresh_columns", "--rebuild"})


class TestRefreshModeDefaultParity(unittest.TestCase):
    """The announce decision mirrors a default that lives in the transform.
    Read it out of the source rather than trusting a comment: importing
    generic_gold_transform needs Spark, so parse its argparse default.
    """

    def test_default_matches_the_transform_argparse_default(self):
        import pathlib
        import re
        src = (pathlib.Path(__file__).resolve().parents[2]
               / "src" / "apps" / "pipeline" / "generic_gold_transform.py")
        text = src.read_text(encoding="utf-8")
        m = re.search(r'add_argument\(\s*"--refresh_mode"\s*,\s*'
                      r'default\s*=\s*"([^"]+)"', text)
        self.assertIsNotNone(
            m, "could not find the --refresh_mode argparse default in "
               f"{src} — if the flag moved, gold_resources."
               "DEFAULT_REFRESH_MODE_ARG must be re-derived, not guessed")
        self.assertEqual(
            m.group(1), gr.DEFAULT_REFRESH_MODE_ARG,
            "gold_resources.DEFAULT_REFRESH_MODE_ARG has drifted from the "
            "transform's own default: an absent --refresh_mode would be "
            "classified one way by the announcer and another by the job")


class TestEnvironmentResolution(unittest.TestCase):
    def test_env_comes_from_the_environment_when_not_passed(self):
        with mock.patch.dict(os.environ, {"ENV": "uat"}, clear=True):
            self.assertEqual(gr.gold_transform_job(), "uif-echo-uat-gold-transform")
        with mock.patch.dict(os.environ, {"ENVIRONMENT": "prod"}, clear=True):
            self.assertEqual(gr.gold_config_table(), "uif-echo-prod-gold-pipeline-config")

    def test_missing_env_is_a_hard_failure(self):
        for env in ({}, {"ENV": ""}, {"ENV": "  "}):
            with mock.patch.dict(os.environ, env, clear=True):
                with self.assertRaises(RuntimeError) as ctx:
                    gr.gold_config_table()
                self.assertIn("ENV", str(ctx.exception))

    def test_explicit_env_never_consults_the_environment(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(gr.gold_artifacts_bucket("prod"), "uif-echo-prod-gold-artifacts")


if __name__ == "__main__":
    unittest.main()
