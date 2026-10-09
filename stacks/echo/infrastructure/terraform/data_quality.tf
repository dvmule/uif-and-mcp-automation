# data_quality.tf
# All resources backing the Data Quality module:
#   - dq_check_config_{env}        DynamoDB table (Task 1)
#   - dq_check_controller          Lambda + IAM (Task 12)
#   - silver-quality-check         Glue job (Task 13)
#   - EventBridge frequency rules  (Task 14)
#   - API Gateway routes           (Task 11)

# ── dq_check_config ──────────────────────────────────────────────
# One item per DQ check (PK=check_id, SK=version).
# SK="latest" is the live config; older versions kept for audit (mirrors
# pipeline config table).
resource "aws_dynamodb_table" "dq_check_config" {
  name         = "${local.legacy_name_prefix}-dq-check-config"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "check_id"
  range_key    = "version"

  attribute {
    name = "check_id"
    type = "S"
  }
  attribute {
    name = "version"
    type = "S"
  }

  point_in_time_recovery { enabled = true }

  tags = {
    Project     = local.project_tag
    Environment = var.environment
    ManagedBy   = "terraform"
    Module      = "data-quality"
  }
}


# ── Glue script + py module uploads (Task 13) ───────────────────────
resource "aws_s3_object" "silver_quality_check_script" {
  count  = var.upload_glue_scripts && !local.is_uif_stack ? 1 : 0
  bucket = aws_s3_bucket.artifacts.id
  key    = "${local.glue_scripts_prefix}/silver-quality-check.py"
  source = "${path.module}/../../src/apps/pipeline/silver-quality-check.py"
  etag   = filemd5("${path.module}/../../src/apps/pipeline/silver-quality-check.py")
}

resource "aws_s3_object" "module_loader_script" {
  count  = var.upload_glue_scripts && !local.is_uif_stack ? 1 : 0
  bucket = aws_s3_bucket.artifacts.id
  key    = "${local.glue_scripts_prefix}/_module_loader.py"
  source = "${path.module}/../../src/apps/pipeline/_module_loader.py"
  etag   = filemd5("${path.module}/../../src/apps/pipeline/_module_loader.py")
}

resource "aws_s3_object" "dq_kinds_init" {
  # NOT gated on var.upload_glue_scripts, unlike its siblings above -- these
  # three never were, so prod (upload_glue_scripts = false) has been uploading
  # them all along. Gating them on it now would make Terraform DESTROY them
  # there. That inconsistency is pre-existing and left alone deliberately; only
  # the is_uif_stack gate is added, so a uif stack does not upload scripts its
  # jobs no longer read.
  count  = local.is_uif_stack ? 0 : 1
  bucket = aws_s3_bucket.artifacts.id
  key    = "${local.glue_scripts_prefix}/dq-kinds/__init__.py"
  source = "${path.module}/../../src/apps/pipeline/dq-kinds/__init__.py"
  etag   = filemd5("${path.module}/../../src/apps/pipeline/dq-kinds/__init__.py")
}

resource "aws_s3_object" "dq_kinds_reconcile" {
  # NOT gated on var.upload_glue_scripts, unlike its siblings above -- these
  # three never were, so prod (upload_glue_scripts = false) has been uploading
  # them all along. Gating them on it now would make Terraform DESTROY them
  # there. That inconsistency is pre-existing and left alone deliberately; only
  # the is_uif_stack gate is added, so a uif stack does not upload scripts its
  # jobs no longer read.
  count  = local.is_uif_stack ? 0 : 1
  bucket = aws_s3_bucket.artifacts.id
  key    = "${local.glue_scripts_prefix}/dq-kinds/reconcile.py"
  source = "${path.module}/../../src/apps/pipeline/dq-kinds/reconcile.py"
  etag   = filemd5("${path.module}/../../src/apps/pipeline/dq-kinds/reconcile.py")
}

resource "aws_s3_object" "dq_kinds_null_rate" {
  # NOT gated on var.upload_glue_scripts, unlike its siblings above -- these
  # three never were, so prod (upload_glue_scripts = false) has been uploading
  # them all along. Gating them on it now would make Terraform DESTROY them
  # there. That inconsistency is pre-existing and left alone deliberately; only
  # the is_uif_stack gate is added, so a uif stack does not upload scripts its
  # jobs no longer read.
  count  = local.is_uif_stack ? 0 : 1
  bucket = aws_s3_bucket.artifacts.id
  key    = "${local.glue_scripts_prefix}/dq-kinds/null-rate.py"
  source = "${path.module}/../../src/apps/pipeline/dq-kinds/null-rate.py"
  etag   = filemd5("${path.module}/../../src/apps/pipeline/dq-kinds/null-rate.py")
}

# ── Glue job: silver-quality-check (Task 13) ────────────────────────
# Multi-kind DQ runner. STANDARD execution class + VPC because the reconcile
# kind path still talks to Oracle over JDBC. null-rate only needs Iceberg
# but the job has to accommodate the heaviest kind.
# max_retries=0 — a FAILED DQ check is a signal, not transient infra.
resource "aws_glue_job" "generic_silver_quality_check" {
  name            = "${local.name_prefix}-silver-quality-check"
  role_arn        = aws_iam_role.glue_execution.arn
  glue_version    = "5.1"
  max_retries     = 0
  timeout         = 120
  execution_class = "STANDARD"

  command {
    name            = "glueetl"
    script_location = local.glue_script_uri["silver_quality_check"]
    python_version  = "3"
  }

  worker_type       = "G.1X"
  number_of_workers = 2

  default_arguments = {
    "--enable-metrics"          = "true"
    "--enable-spark-ui"         = "false"
    "--job-language"            = "python"
    "--TempDir"                 = "s3://${var.artifacts_bucket}/${local.glue_temp_prefix}/"
    "--CONFIG_TABLE"            = aws_dynamodb_table.dq_check_config.name
    "--METRICS_TABLE"           = aws_dynamodb_table.glue_job_metrics.name
    "--datalake-formats"        = "iceberg"
    "--env"                     = var.environment
    "--enable-glue-datacatalog" = ""
    "--extra-py-files" = join(",", [
      local.glue_script_uri["module_loader"],
      local.glue_script_uri["dq_kinds_init"],
      local.glue_script_uri["dq_kinds_reconcile"],
      local.glue_script_uri["dq_kinds_null_rate"],
    ])

    # UIFPE-355 phase 2: infrastructure names are PASSED, never derived from
    # --env. The scripts previously built echo-{env}-* themselves, so a
    # uif-echo-dev job wrote into the echo-dev lake and triggered echo-dev's
    # Silver. getResolvedOptions treats these as required — a missing value
    # fails the run instead of silently targeting the other stack.
    "--RESOURCE_PREFIX"  = local.name_prefix
    "--SILVER_WAREHOUSE" = "s3://${var.silver_bucket}/"
  }

  connections = [aws_glue_connection.vpc_network.name]

  execution_property {
    max_concurrent_runs = 10
  }

  tags = {
    Project     = local.project_tag
    Environment = var.environment
    ManagedBy   = "terraform"
    Module      = "data-quality"
  }

  depends_on = [
    aws_s3_object.silver_quality_check_script,
    aws_s3_object.module_loader_script,
    aws_s3_object.dq_kinds_init,
    aws_s3_object.dq_kinds_reconcile,
    aws_s3_object.dq_kinds_null_rate,
  ]
}

# ── Lambda: dq_check_controller (Task 12) ───────────────────────────
data "archive_file" "dq_check_controller" {
  type        = "zip"
  output_path = "${path.module}/.build/dq_check_controller.zip"

  source {
    content  = file("${path.module}/../../src/apps/control_panel/dq_check_controller.py")
    filename = "dq_check_controller.py"
  }
  source {
    content  = file("${path.module}/../../src/libs/echo_resources.py")
    filename = "echo_resources.py"
  }
}

resource "aws_lambda_function" "dq_check_controller" {
  function_name = "${local.name_prefix}-dq-check-controller"
  description   = "CRUD + trigger for Data Quality checks (dq_check_config table)"
  # Same gate as the four in lambda.tf; the reasoning is written up there.
  filename         = local.is_uif_stack ? null : data.archive_file.dq_check_controller.output_path
  s3_bucket        = local.is_uif_stack ? var.release_artifacts_bucket : null
  s3_key           = local.is_uif_stack ? local.lambda_s3_key["dq_check_controller"] : null
  source_code_hash = local.is_uif_stack ? lookup(var.lambda_code_hashes, "dq_check_controller", null) : data.archive_file.dq_check_controller.output_base64sha256
  handler          = "dq_check_controller.handler"
  runtime          = "python3.11"
  role             = aws_iam_role.lambda_execution.arn
  timeout          = 30
  memory_size      = 512

  environment {
    variables = {
      # UIFPE-355: the runtime reads its prefixes rather than computing them, so
      # the same code works in dev (renamed) and uat/prod (not yet renamed).
      RESOURCE_PREFIX        = local.name_prefix
      LEGACY_RESOURCE_PREFIX = local.legacy_name_prefix
      ENV                    = var.environment
      REGION                 = var.aws_region
      DQ_CHECK_TABLE         = aws_dynamodb_table.dq_check_config.name
      METRICS_TABLE          = aws_dynamodb_table.glue_job_metrics.name
      DQ_GLUE_JOB_NAME       = aws_glue_job.generic_silver_quality_check.name
    }
  }

  tags = {
    Project     = local.project_tag
    Environment = var.environment
    ManagedBy   = "terraform"
    Module      = "data-quality"
  }
}

# ── API Gateway integration + routes (Task 11) ──────────────────────
resource "aws_apigatewayv2_integration" "dq_check_controller" {
  api_id                 = aws_apigatewayv2_api.ingestion.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.dq_check_controller.invoke_arn
  payload_format_version = "2.0"
}

locals {
  dq_routes = [
    "GET /dq/checks",
    "GET /dq/checks/{check_id}",
    "PUT /dq/checks/{check_id}",
    "DELETE /dq/checks/{check_id}",
    "POST /dq/checks/{check_id}/trigger-run",
    "GET /dq/checks/{check_id}/runs",
    "GET /dq/kinds",
  ]
}

resource "aws_apigatewayv2_route" "dq" {
  for_each           = toset(local.dq_routes)
  api_id             = aws_apigatewayv2_api.ingestion.id
  route_key          = each.value
  target             = "integrations/${aws_apigatewayv2_integration.dq_check_controller.id}"
  authorization_type = local.authorizer_id != null ? "JWT" : "NONE"
  authorizer_id      = local.authorizer_id
}

resource "aws_lambda_permission" "apigateway_invoke_dq_controller" {
  statement_id  = "AllowAPIGatewayInvokeDq"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.dq_check_controller.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.ingestion.execution_arn}/*/*"
}
