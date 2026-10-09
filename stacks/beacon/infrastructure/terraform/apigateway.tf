# apigateway.tf — the control-panel API on Lambda (UIFPE-226).
#
# The Echo pattern, verified in this account (echo-dev-api): API Gateway HTTP
# API -> zip Lambda, no ECS, no ALB, nothing always-on. One deliberate
# difference from Echo: the Lambda runs the existing FastAPI app through
# Mangum ($default catch-all route) instead of hand-rolled routing — we
# arrive with ~40 working routes and their test suite; Echo started plain.
#
# Cold starts: none of the mitigations to begin with, exactly like Echo.
# Measure first; if the ~1-2 s first-hit after idle bothers users,
# provisioned concurrency = 1 is a one-attribute change here (~$12/mo —
# which is most of the ECS+ALB saving, so the lasting win is no image to
# patch, not dollars).
#
# Probation exit (owner call, 2026-08-28): the ECS service, ALB and the api
# image/ECR/CodeBuild were deleted the same day the path was verified end to
# end — restoring ECS is a git revert plus apply. The last ECS resources (the
# parked Fargate transform and the empty cluster) went at UIFPE-215's
# probation exit (2026-09-02); nothing in this stack runs on ECS any more.

variable "api_lambda_zip_hash" {
  description = "Base64 SHA256 of the staged API lambda zip (CI computes it at apply time). Empty = code changes not tracked — the UIFPE-200 stale-lambda trap."
  type        = string
  default     = ""
}

variable "beacon_ui_origins" {
  description = "Comma-separated browser origins allowed CORS access to the API (UIs served from a host that does not proxy /api/*, e.g. the shared RELAY distribution). Empty = same-origin + localhost only."
  type        = string
  default     = ""
}

# Same-name-prefix rule as the other Lambdas: the CI deploy role's IAM/Lambda
# permissions are scoped to uif-beacon-*.
# uif:rename-approved UIFPE-365
resource "aws_iam_role" "api_lambda" {
  name               = "${local.name}-api-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json

  lifecycle {
    create_before_destroy = true
  }
}

# The api_task policy document lives in iam.tf (its name survives from the
# ECS era so the permission history stays greppable).
resource "aws_iam_role_policy" "api_lambda" {
  name   = "beacon-api"
  role   = aws_iam_role.api_lambda.id
  policy = data.aws_iam_policy_document.api_task.json
}

resource "aws_iam_role_policy_attachment" "api_lambda_logs" {
  role       = aws_iam_role.api_lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_lambda_function" "api" {
  function_name = "${local.name}-api"
  role          = aws_iam_role.api_lambda.arn
  runtime       = local.lambda_runtime
  handler       = "control_panel.lambda_api.handler"
  s3_bucket     = aws_s3_bucket.data.bucket
  s3_key        = "code/beacon-api.zip"
  # the key is fixed, so this hash is the ONLY change signal for new code
  source_code_hash = var.api_lambda_zip_hash != "" ? var.api_lambda_zip_hash : null
  # under API Gateway's hard 29 s integration cap; every handler is
  # sub-second today (the slowest are /convert and live introspection)
  timeout     = 29
  memory_size = 1024 # also buys CPU — sqlglot conversion is CPU-bound

  environment {
    # Mirrors ecs.tf's container environment VERBATIM (that list is the
    # source of truth during probation; a var added there must be added here)
    variables = {
      BEACON_ENV                    = var.env
      BEACON_DATA_BUCKET            = aws_s3_bucket.data.bucket
      BEACON_TABLE_REGISTRIES       = aws_dynamodb_table.registries.name
      BEACON_TABLE_DRAFTS           = aws_dynamodb_table.drafts.name
      BEACON_TABLE_CATALOG          = aws_dynamodb_table.catalog.name
      BEACON_TABLE_RUNS             = aws_dynamodb_table.runs.name
      BEACON_STATE_MACHINE_ARN      = "arn:aws:states:${var.region}:${data.aws_caller_identity.current.account_id}:stateMachine:${local.name}-export"
      BEACON_TRANSFORM_JOB_NAME     = aws_glue_job.transform.name
      BEACON_TABLE_GLUE_CONFIG      = aws_dynamodb_table.glue_job_config.name
      BEACON_TABLE_SCHEDULES        = aws_dynamodb_table.schedules.name
      BEACON_TABLE_EXTRACTION_STATE = aws_dynamodb_table.extraction_state.name
      BEACON_SCHEDULER_GROUP        = local.scheduler_group
      BEACON_SCHEDULE_NAME_PREFIX   = local.schedule_name_prefix
      BEACON_SCHEDULER_TARGET_ARN   = aws_lambda_function.scheduled_starter.arn
      BEACON_SCHEDULER_ROLE_ARN     = aws_iam_role.scheduler_invoke.arn
      BEACON_CONFIG_PREFIX          = "configs"
      # UIFPE-229: the domains the wizard offers. Read access comes from the
      # tag-based LF grants (lakeformation.tf) covering the whole gold layer;
      # this list is the deliberate subset the picker exposes.
      BEACON_GOLD_DATABASES = join(",", var.gold_databases)
      # UIFPE-540: where the gold masters are read from, to decide which
      # columns may be exported. Empty leaves the guard inert.
      BEACON_GOLD_ARTIFACTS_BUCKET = var.gold_artifacts_bucket
      BEACON_EXPORT_CONFIG_URI     = "s3://${aws_s3_bucket.data.bucket}/configs/export.json"
      BEACON_PICKUP_PREFIX         = "pickup"
      BEACON_UI_ORIGINS            = var.beacon_ui_origins
      # UIFPE-241: destination secrets live under the prefix the IAM policies
      # scope to; connectors read them at transfer time (UIFPE-339)
      BEACON_DESTINATION_SECRET_PREFIX = "${local.secrets_prefix}/destinations"
      # UIFPE-339: the shared access role every Transfer Family connector
      # assumes (transfer.tf); presence of this var is what switches new SFTP
      # destinations onto the transfer_family adapter
      BEACON_TRANSFER_ACCESS_ROLE_ARN = aws_iam_role.transfer_access.arn
      # UIFPE-440: external S3 destinations — the deliver Lambda runs the s3
      # connection test (it must execute under the deliver role, the identity
      # partners grant), and the role ARN goes verbatim into the bucket-policy
      # snippet the panel shows the author to send to the partner
      BEACON_DELIVER_FUNCTION = aws_lambda_function.deliver.function_name
      BEACON_DELIVER_ROLE_ARN = aws_iam_role.lambda_deliver.arn
      # UIFPE-444: the API creates/updates the Pearson Notifications OAuth
      # credential through PUT /notifications/credentials (see lambda.tf's
      # UIFPE-444 comment for why Terraform cannot own the secret)
      BEACON_NOTIFICATIONS_SECRET = "${local.secrets_prefix}/notifications/api"
      # UIFPE-487: every API request must carry a valid RELAY shell session
      # token; this names the shell's login secret (Beacon reads only its
      # token_secret to verify signatures — never the users map)
      BEACON_AUTH_SECRET = var.shell_login_secret_name
      # UIFPE-441: the wizard's live filter preview on Athena. Presence of the
      # workgroup var is the feature switch (/health preview_enabled); results
      # go to the data bucket's preview/ prefix (iam.tf S3PreviewResults,
      # s3.tf expire-preview).
      BEACON_PREVIEW_WORKGROUP = var.athena_workgroup
      BEACON_PREVIEW_PREFIX    = local.preview_prefix
    }
  }
}

resource "aws_cloudwatch_log_group" "api_lambda" {
  name              = "/aws/lambda/${aws_lambda_function.api.function_name}"
  retention_in_days = 30
}

# -- the front door ------------------------------------------------------------
resource "aws_apigatewayv2_api" "api" {
  # uif- prefix per the UIF Resource Name Lookup (UIFPE-266) — renames in
  # place; the invoke URL is id-based and unchanged
  name          = "${local.name}-api"
  protocol_type = "HTTP"
}

resource "aws_apigatewayv2_integration" "api" {
  api_id                 = aws_apigatewayv2_api.api.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.api.invoke_arn
  payload_format_version = "2.0"
}

# One catch-all route: FastAPI owns the routing (Echo registers each route
# explicitly because its gateway IS its router; ours is not).
resource "aws_apigatewayv2_route" "default" {
  api_id    = aws_apigatewayv2_api.api.id
  route_key = "$default"
  target    = "integrations/${aws_apigatewayv2_integration.api.id}"
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.api.id
  name        = "$default"
  auto_deploy = true
}

resource "aws_lambda_permission" "apigw" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.api.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.api.execution_arn}/*/*"
}

output "api_gateway_endpoint" {
  value = aws_apigatewayv2_api.api.api_endpoint
}
