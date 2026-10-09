# test_glue_entrypoint.py
# The Glue spike wrapper (UIFPE-205): Glue passes job parameters with
# underscores plus its own housekeeping args — the wrapper must hand the
# Fargate entrypoint exactly its dashed contract and nothing else.

import unittest

from beacon_export import glue_entrypoint


class TestTranslate(unittest.TestCase):
    def test_translates_glue_params_to_entrypoint_argv(self):
        argv = ["--JOB_NAME", "uif-beacon-dev-glue-spike",
                "--export_config_s3_path", "s3://b/configs/100000/v3/export.json",
                "--batch_id", "b-glue-spike-001",
                "--skip_delivery", "true",
                "--TempDir", "s3://glue-temp/x"]
        self.assertEqual(
            glue_entrypoint._translate(argv),
            ["--export-config-s3-path", "s3://b/configs/100000/v3/export.json",
             "--batch-id", "b-glue-spike-001",
             "--skip-delivery"])

    def test_skip_delivery_false_is_respected(self):
        argv = ["--export_config_s3_path", "s3://b/e.json",
                "--batch_id", "b-1", "--skip_delivery", "false"]
        self.assertNotIn("--skip-delivery", glue_entrypoint._translate(argv))

    def test_watermark_passes_through(self):
        # UIFPE-168: the incremental window's lower bound rides the job args
        argv = ["--export_config_s3_path", "s3://b/e.json",
                "--batch_id", "b-1",
                "--watermark", "2026-08-27 06:30:00.000001"]
        out = glue_entrypoint._translate(argv)
        self.assertIn("--watermark", out)
        self.assertEqual(out[out.index("--watermark") + 1],
                         "2026-08-27 06:30:00.000001")

    def test_empty_watermark_is_dropped(self):
        # the state machine passes the key unconditionally; "" = no stored
        # watermark (full/baseline) and must not reach the entrypoint
        argv = ["--export_config_s3_path", "s3://b/e.json",
                "--batch_id", "b-1", "--watermark", ""]
        self.assertNotIn("--watermark", glue_entrypoint._translate(argv))

    def test_bare_watermark_flag_the_shape_glue_actually_sends(self):
        # UIFPE-227: Glue does NOT deliver an empty argument value as an argv
        # token — the flag arrives BARE, immediately followed by the next
        # argument's key. The first post-UIFPE-168 run failed on exactly this
        # (run b-20260828-111359: "argument --watermark: expected one
        # argument", and the config-path key was swallowed with it).
        argv = ["--batch_id", "b-1",
                "--skip_delivery", "true",
                "--watermark",  # bare: Glue dropped the "" value
                "--export_config_s3_path", "s3://b/e.json"]
        out = glue_entrypoint._translate(argv)
        self.assertNotIn("--watermark", out)
        # the neighbouring argument must survive intact
        self.assertEqual(out[out.index("--export-config-s3-path") + 1],
                         "s3://b/e.json")

    def test_bare_watermark_as_the_last_token(self):
        argv = ["--export_config_s3_path", "s3://b/e.json",
                "--batch_id", "b-1", "--watermark"]
        out = glue_entrypoint._translate(argv)
        self.assertNotIn("--watermark", out)
        self.assertIn("--batch-id", out)

    def test_module_imports_without_spark(self):
        # the pyspark quarantine holds: importing the wrapper must not pull
        # pyspark or awsglue (only _glue_session does, at call time)
        import sys
        self.assertIn("beacon_export.glue_entrypoint", sys.modules)
        self.assertNotIn("awsglue", sys.modules)
