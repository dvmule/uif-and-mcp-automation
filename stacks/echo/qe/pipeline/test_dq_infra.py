# test_dq_infra.py
# Sanity-check the data_quality.tf file declares the dq_check_config table
# with the expected key shape and PITR enabled. We parse the HCL text rather
# than invoking `terraform plan`, because plan requires backend init + creds.
import os
import re
import unittest


# UIFPE-355: infra/ moved to infrastructure/terraform/ under the stack root.
INFRA_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "infrastructure", "terraform"
)
DQ_TF = os.path.join(INFRA_DIR, "data_quality.tf")


class TestDqCheckConfigTable(unittest.TestCase):
    def setUp(self):
        with open(DQ_TF) as f:
            self.text = f.read()

    def test_dq_check_config_resource_present(self):
        self.assertIn('resource "aws_dynamodb_table" "dq_check_config"', self.text)

    def test_hash_and_range_keys(self):
        self.assertRegex(self.text, r'hash_key\s*=\s*"check_id"')
        self.assertRegex(self.text, r'range_key\s*=\s*"version"')

    def test_pay_per_request(self):
        self.assertRegex(self.text, r'billing_mode\s*=\s*"PAY_PER_REQUEST"')

    def test_pitr_enabled(self):
        self.assertRegex(self.text, r'point_in_time_recovery\s*\{\s*enabled\s*=\s*true\s*\}')

    def test_table_name_uses_environment_var(self):
        # UIFPE-355: this table holds data, so it is deliberately pinned to
        # legacy_name_prefix while the uif- rename is phased. Renaming a
        # DynamoDB table in Terraform is destroy + create and takes the rows
        # with it. Asserting the legacy prefix specifically - rather than
        # accepting either - is what stops a well-meaning prefix sweep from
        # silently scheduling the DQ configs for deletion.
        self.assertRegex(
            self.text,
            r'name\s*=\s*"\$\{local\.legacy_name_prefix\}-dq-check-config"',
        )


class TestDqRoutes(unittest.TestCase):
    def setUp(self):
        with open(DQ_TF) as f:
            self.text = f.read()

    def test_seven_dq_routes_declared(self):
        for route in (
            "GET /dq/checks",
            "GET /dq/checks/{check_id}",
            "PUT /dq/checks/{check_id}",
            "DELETE /dq/checks/{check_id}",
            "POST /dq/checks/{check_id}/trigger-run",
            "GET /dq/checks/{check_id}/runs",
            "GET /dq/kinds",
        ):
            self.assertIn(f'"{route}"', self.text)

    def test_route_integration_targets_dq_controller(self):
        self.assertIn(
            'integration_uri        = aws_lambda_function.dq_check_controller.invoke_arn',
            self.text,
        )


class TestDqLambda(unittest.TestCase):
    def setUp(self):
        with open(DQ_TF) as f:
            self.text = f.read()

    def test_dq_lambda_resource_present(self):
        self.assertIn('resource "aws_lambda_function" "dq_check_controller"', self.text)

    def test_dq_lambda_env_vars(self):
        for var in ("DQ_CHECK_TABLE", "METRICS_TABLE", "DQ_GLUE_JOB_NAME"):
            self.assertRegex(self.text, rf'{var}\s*=')

    def test_dq_lambda_handler_and_runtime(self):
        self.assertRegex(self.text, r'handler\s*=\s*"dq_check_controller\.handler"')
        self.assertRegex(self.text, r'runtime\s*=\s*"python3\.11"')


class TestDqGlueJob(unittest.TestCase):
    def setUp(self):
        with open(DQ_TF) as f:
            self.text = f.read()

    def test_dq_glue_job_resource_present(self):
        self.assertIn('resource "aws_glue_job" "generic_silver_quality_check"', self.text)

    def test_dq_glue_job_no_retries(self):
        self.assertRegex(self.text, r'max_retries\s*=\s*0')

    def test_dq_glue_job_iceberg_format(self):
        self.assertRegex(self.text, r'"--datalake-formats"\s*=\s*"iceberg"')

    def test_dq_glue_job_extra_py_files(self):
        for fragment in (
            "glue_scripts_prefix}/_module_loader.py",
            "glue_scripts_prefix}/dq-kinds/__init__.py",
            "glue_scripts_prefix}/dq-kinds/reconcile.py",
            "glue_scripts_prefix}/dq-kinds/null-rate.py",
        ):
            self.assertIn(fragment, self.text)

    def test_dq_glue_script_uses_prefix_local(self):
        self.assertIn('key    = "${local.glue_scripts_prefix}/silver-quality-check.py"', self.text)
        self.assertNotIn("glue_jobs/", self.text)
        self.assertNotIn('key    = "jobs/', self.text)


class TestLambdaLayersS3(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(INFRA_DIR, "lambda.tf")) as f:
            self.lambda_tf = f.read()
        with open(os.path.join(INFRA_DIR, "locals.tf")) as f:
            self.locals_tf = f.read()

    def test_layers_read_from_artifacts_bucket_not_local_zips(self):
        self.assertNotIn("deploy/layers/", self.lambda_tf)
        self.assertNotIn('resource "aws_s3_object" "oracle_instant_client_layer"', self.lambda_tf)
        self.assertNotIn('resource "aws_s3_object" "gcs_layer"', self.lambda_tf)
        for key in (
            "oracle_instant_client",
            "gcs",
            "silver_bq",
            "controller_bq",
        ):
            self.assertIn(f"local.lambda_layer_keys.{key}", self.lambda_tf)

    def test_lambda_layer_keys_in_locals(self):
        self.assertIn("code/lambda-layers/oracle_instant_client.zip", self.locals_tf)
        self.assertIn("lambda-layers/oracle_instant_client.zip", self.locals_tf)
        for suffix in ("gcs_layer.zip", "silver_bq_layer.zip", "controller_bq_layer.zip"):
            self.assertIn(suffix, self.locals_tf)

    def test_uif_data_bucket_layout_in_locals(self):
        self.assertIn('glue_scripts_prefix = local.is_uif_stack ? "code" : "jobs"', self.locals_tf)
        self.assertIn('data_config_prefix  = local.is_uif_stack ? "configs" : "configs"', self.locals_tf)


class TestSchedulerEnvVars(unittest.TestCase):
    def test_scheduler_dispatcher_has_dq_env_vars(self):
        # UIFPE-355: lambda_functions.tf renamed to lambda.tf.
        with open(os.path.join(INFRA_DIR, "lambda.tf")) as f:
            text = f.read()
        # Look in the scheduler_dispatcher block specifically — both env vars
        # must appear after the resource declaration.
        sched_start = text.find('resource "aws_lambda_function" "scheduler_dispatcher"')
        self.assertGreater(sched_start, 0, "scheduler_dispatcher resource not found")
        # Search the next ~1500 chars (resource body) for env vars
        block = text[sched_start:sched_start + 2000]
        self.assertIn("DQ_CHECK_TABLE", block)
        self.assertIn("DQ_GLUE_JOB_NAME", block)


if __name__ == "__main__":
    unittest.main()
