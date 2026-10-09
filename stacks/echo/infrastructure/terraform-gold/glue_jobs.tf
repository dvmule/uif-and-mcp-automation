# Gold layer Glue jobs (ADR-0001 / ADR-0003).
# CUTOVER NOTE: jobs with these exact names exist today in the platform
# stack's state. Do not `apply` before the cutover (state rm there, import
# here) — see README "Infra ownership & cutover". Keeping the same job names
# preserves the shared Glue role's StartJobRun allow-list and run history.

resource "aws_s3_object" "generic_gold_transform_script" {
  count  = local.glue_staged["gold_transform"] ? 0 : 1
  bucket = local.glue_scripts_bucket
  key    = "${local.gold_scripts_prefix}/generic_gold_transform.py"
  source = "${path.module}/../../src/apps/pipeline/generic_gold_transform.py"
  etag   = filemd5("${path.module}/../../src/apps/pipeline/generic_gold_transform.py")
}

resource "aws_s3_object" "generic_gold_reconcile_script" {
  count  = local.glue_staged["gold_reconcile"] ? 0 : 1
  bucket = local.glue_scripts_bucket
  key    = "${local.gold_scripts_prefix}/generic_gold_reconcile.py"
  source = "${path.module}/../../src/apps/pipeline/generic_gold_reconcile.py"
  etag   = filemd5("${path.module}/../../src/apps/pipeline/generic_gold_reconcile.py")
}

# Shared pure-logic library for the Gold CDC flow (ADR-0003), imported by
# both gold jobs via --extra-py-files.
resource "aws_s3_object" "gold_cdc_lib" {
  count  = local.glue_staged["gold_cdc"] ? 0 : 1
  bucket = local.glue_scripts_bucket
  key    = "${local.gold_scripts_prefix}/gold_cdc.py"
  source = "${path.module}/../../src/apps/pipeline/gold_cdc.py"
  etag   = filemd5("${path.module}/../../src/apps/pipeline/gold_cdc.py")
}

# EMF document builders (UIFPE-543), imported by both gold jobs and bundled
# into the dispatcher zip (dispatcher.tf). Stdlib only, so one file serves
# Glue and Lambda alike.
resource "aws_s3_object" "gold_telemetry_lib" {
  count  = local.glue_staged["gold_telemetry"] ? 0 : 1
  bucket = local.glue_scripts_bucket
  key    = "${local.gold_scripts_prefix}/gold_telemetry.py"
  source = "${path.module}/../../src/libs/gold_telemetry.py"
  etag   = filemd5("${path.module}/../../src/libs/gold_telemetry.py")
}

locals {
  # One list for both jobs: a library missing from either is an ImportError
  # at run time, not at plan time.
  gold_extra_py_files = join(",", [
    local.gold_glue_uri["gold_cdc"],
    local.gold_glue_uri["gold_telemetry"],
  ])
}

# Generic Gold transform — joins N Silver Iceberg tables per a mapping in S3,
# hash-gated MERGE INTO Gold Iceberg + change log (ADR-0003). FLEX: only
# touches S3 (Silver + Gold), no VPC.
resource "aws_glue_job" "generic_gold_transform" {
  name         = "${local.name_prefix}-gold-transform"
  role_arn     = data.aws_iam_role.glue_execution.arn
  glue_version = "5.1"
  max_retries  = 1

  # Matches pipelines/base/product.yaml glue.timeout_minutes. The dispatcher
  # passes that config value to StartJobRun, which overrides this default — so
  # when the two disagree, the effective ceiling silently depends on WHO
  # started the run (dispatcher vs console vs bare CLI). Keep them equal.
  timeout         = 180
  execution_class = "FLEX"

  command {
    name            = "glueetl"
    script_location = local.gold_glue_uri["gold_transform"]
    python_version  = "3"
  }

  worker_type       = "G.2X"
  number_of_workers = 16

  default_arguments = {
    "--enable-metrics"  = "true"
    "--enable-spark-ui" = "false"
    # UIFPE-543: Glue job/stage metrics, run insights, and driver/executor
    # logs streamed live to /aws-glue/jobs/logs-v2 (the echo root creates it).
    "--enable-observability-metrics"     = "true"
    "--enable-job-insights"              = "true"
    "--enable-continuous-cloudwatch-log" = "true"
    "--job-language"                     = "python"
    "--TempDir"                          = "s3://${local.glue_scripts_bucket}/gold/tmp/"
    "--CONFIG_TABLE"                     = aws_dynamodb_table.gold_pipelines_config.name
    "--METRICS_TABLE"                    = local.metrics_table_name
    "--datalake-formats"                 = "iceberg"
    "--env"                              = var.environment
    "--additional-python-modules"        = "pyyaml==6.0.1"
    "--extra-py-files"                   = local.gold_extra_py_files
    # Iceberg warehouse root: passed, never derived from --env in the script
    "--GOLD_WAREHOUSE" = "s3://${var.gold_bucket}/"
  }

  execution_property {
    max_concurrent_runs = 10
  }

  tags = local.common_tags

  depends_on = [
    aws_s3_object.generic_gold_transform_script,
    aws_s3_object.gold_cdc_lib,
    aws_s3_object.gold_telemetry_lib,
  ]
}

# Gold reconcile — anti-joins Silver source-of-truth PKs against Gold,
# deletes orphan rows and appends DELETE verdicts to the change log.
resource "aws_glue_job" "generic_gold_reconcile" {
  name         = "${local.name_prefix}-gold-reconcile"
  role_arn     = data.aws_iam_role.glue_execution.arn
  glue_version = "5.1"
  max_retries  = 0

  # Same config key drives both jobs: the dispatcher reads one
  # glue.timeout_minutes and passes it to whichever job it starts, so this
  # default tracks the transform's rather than keeping a separate value.
  timeout         = 180
  execution_class = "FLEX"

  command {
    name            = "glueetl"
    script_location = local.gold_glue_uri["gold_reconcile"]
    python_version  = "3"
  }

  worker_type       = "G.1X"
  number_of_workers = 4

  default_arguments = {
    "--enable-metrics"  = "true"
    "--enable-spark-ui" = "false"
    # UIFPE-543: Glue job/stage metrics, run insights, and driver/executor
    # logs streamed live to /aws-glue/jobs/logs-v2 (the echo root creates it).
    "--enable-observability-metrics"     = "true"
    "--enable-job-insights"              = "true"
    "--enable-continuous-cloudwatch-log" = "true"
    "--job-language"                     = "python"
    "--TempDir"                          = "s3://${local.glue_scripts_bucket}/gold/tmp/"
    "--CONFIG_TABLE"                     = aws_dynamodb_table.gold_pipelines_config.name
    "--METRICS_TABLE"                    = local.metrics_table_name
    "--datalake-formats"                 = "iceberg"
    "--env"                              = var.environment
    "--additional-python-modules"        = "pyyaml==6.0.1"
    "--extra-py-files"                   = local.gold_extra_py_files
    # Iceberg warehouse root: passed, never derived from --env in the script
    "--GOLD_WAREHOUSE" = "s3://${var.gold_bucket}/"
  }

  execution_property {
    max_concurrent_runs = 5
  }

  tags = local.common_tags

  depends_on = [
    aws_s3_object.generic_gold_reconcile_script,
    aws_s3_object.gold_cdc_lib,
    aws_s3_object.gold_telemetry_lib,
  ]
}
