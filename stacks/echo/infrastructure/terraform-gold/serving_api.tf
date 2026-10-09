# Gold retrieval API (UIFPE-518) — ONE Lambda for every served entity.
#
# Reads the shared serving table that serving.tf's sync fills. Like the sync,
# nothing here is per-entity: the entity comes from the request path and its
# API property set is derived from its gold master, so exposing another
# entity's API is a `serving.enabled` line in config and no Terraform.
#
# This role is much smaller than the sync's: a read of two DynamoDB tables, a
# read of the schemas prefix, and the gateway secret. No Athena, no Glue, no
# Lake Formation, no self-invoke, no queue.

# ── The gateway's shared backend header ─────────────────────────────────────
#
# CONSUMER auth (apikey, Authorization/EntraID) is enforced at the Mule
# gateway and never reaches this Lambda. This secret is a different thing: it
# stops a caller who discovers the function URL from bypassing the gateway
# altogether, which would make the gateway's auth decorative.
#
# NOT CREATED BY TERRAFORM, on purpose and with evidence. The deploy role can
# `GetSecretValue` and `DescribeSecret` on this name but CANNOT `CreateSecret`
# on it: its Secrets grants are anchored to `echo-dev-*`, `oracle-*` and
# `eds/*`, and a simulation on 2026-09-24 returned CreateSecret DENY with ZERO
# matched statements for `uif/echo/dev/gateway/*` while ALLOWING the identical
# action on marketing-copy's legacy `echo-dev-*` name. Creating it here would
# fail the apply partway and leave a half-applied stack — the same failure as
# UIFPE-254/PR #177.
#
# So it is a ONE-TIME BOOTSTRAP, which is what a privileged one-off should be
# anyway (capabilities/delivery/stack-promotion-model.md, Phase 1):
#
#     aws secretsmanager create-secret --region eu-west-1
#       --name uif/echo/dev/gateway/gold-api
#       --secret-string '<value agreed with the gateway owner>'
#
# UNTIL IT EXISTS THE CHECK IS DISABLED AND THIS API IS OPEN. Owner decision
# 2026-09-24: the gateway is a separate layer and the API has to be
# exercisable before it lands, which is the posture marketing-copy already
# runs. The check arms itself when the secret gains a value - no code change,
# no redeploy, because the value is read per invocation.
#
# So creating this secret is the ONLY thing standing between the current
# state and an authenticated backend. Treat it as outstanding work, not as a
# nice-to-have.
#
# The alternative is widening the deploy role to manage `uif/echo/*` secrets.
# That role's policies are not Terraform-managed in this repo, so it is an
# out-of-band change to the access estate rather than part of this PR.

# ── The Lambda ──────────────────────────────────────────────────────────────

# Same dual-mode artifact as the sync (see serving.tf): the staged release zip
# when one exists, the plan-time zip otherwise. UIFPE-576.
data "archive_file" "gold_serving_api" {
  type        = "zip"
  output_path = "${path.module}/.build/gold_serving_api.zip"

  source {
    content  = file("${path.module}/../../src/apps/lambdas/gold_serving_api.py")
    filename = "gold_serving_api.py"
  }
  # The spec derivation, shared with the sync (bare-name import; flat zip).
  source {
    content  = file("${path.module}/../../src/libs/gold_serving.py")
    filename = "gold_serving.py"
  }
  # gold_serving imports physical_name from it.
  source {
    content  = file("${path.module}/../../src/apps/pipeline/gold_cdc.py")
    filename = "gold_cdc.py"
  }
  # The gateway hop's token verification, shared with the domain APIs that
  # need the same check (platform/python/README.md). Stdlib: no layer, no wheel.
  source {
    content  = file("${path.module}/../../../../platform/python/jwt_verify.py")
    filename = "jwt_verify.py"
  }
}

resource "aws_iam_role" "gold_serving_api" {
  name = "${local.name_prefix}-gold-serving-api-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy" "gold_serving_api" {
  name = "${local.name_prefix}-gold-serving-api-policy"
  role = aws_iam_role.gold_serving_api.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ReadTheServingCopy"
        Effect = "Allow"
        Action = ["dynamodb:GetItem", "dynamodb:Query"]
        # BOTH ARNs. A Query against a secondary index is authorised on the
        # INDEX arn, which the table arn does not cover — grant the table
        # alone and every business-key lookup fails AccessDenied while the
        # merge-key reads carry on working.
        Resource = [
          aws_dynamodb_table.gold_serving.arn,
          "${aws_dynamodb_table.gold_serving.arn}/index/*",
        ]
      },
      {
        Sid      = "ReadThePipelineConfig"
        Effect   = "Allow"
        Action   = ["dynamodb:GetItem"]
        Resource = aws_dynamodb_table.gold_pipelines_config.arn
      },
      {
        Sid    = "ReadTheMasterSchema"
        Effect = "Allow"
        # The PREFIX, not a list of entities: the api-tier property set comes
        # from each entity's master, and naming them here would mean an IAM
        # change to expose a new one.
        Action   = ["s3:GetObject"]
        Resource = ["arn:aws:s3:::${var.gold_artifacts_bucket}/schemas/*"]
      },
      {
        Sid    = "ReadTheGatewaySecret"
        Effect = "Allow"
        # Trailing wildcard because Secrets Manager appends a random six-character
        # suffix to every secret ARN, so the exact ARN is not knowable here.
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = ["arn:aws:secretsmanager:${var.aws_region}:${data.aws_caller_identity.current.account_id}:secret:${local.gold_api_gateway_secret}-*"]
      },
      {
        Sid      = "Logs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:*"
      },
    ]
  })
}

resource "aws_lambda_function" "gold_serving_api" {
  function_name = "${local.name_prefix}-gold-serving-api"
  role          = aws_iam_role.gold_serving_api.arn
  handler       = "gold_serving_api.handler"
  runtime       = "python3.11"
  timeout       = 15

  # 1024, and this is a CPU setting rather than a memory one. Lambda scales
  # vCPU with memory, and the cost measured on dev at 256 MB was CPU-bound:
  # botocore parses a JSON service model per client, which dominated a warm
  # response. Only 103 MB of 256 was ever used, so headroom was never the
  # constraint. Billing is GB-ms, so 4x the rate over a duration that falls by
  # more than 4x is cost-NEUTRAL or better -- which is why this is paired with
  # the caching in gold_serving_api.py rather than standing in for it.
  memory_size = 1024

  # lookup(..., default), never an index — Terraform evaluates both branches.
  filename         = local.lambda_staged["gold_serving_api"] ? null : data.archive_file.gold_serving_api.output_path
  s3_bucket        = local.lambda_staged["gold_serving_api"] ? var.release_artifacts_bucket : null
  s3_key           = local.lambda_staged["gold_serving_api"] ? local.lambda_s3_key["gold_serving_api"] : null
  source_code_hash = local.lambda_staged["gold_serving_api"] ? lookup(var.lambda_code_hashes, "gold_serving_api", null) : data.archive_file.gold_serving_api.output_base64sha256

  environment {
    variables = {
      ENV_NAME            = var.environment
      TABLE_NAME          = aws_dynamodb_table.gold_serving.name
      CONFIG_TABLE        = aws_dynamodb_table.gold_pipelines_config.name
      GATEWAY_SECRET_ID   = local.gold_api_gateway_secret
      GATEWAY_HEADER_NAME = "x-uif-gateway-auth"
      CACHE_MAX_AGE       = "300"

      # The gateway hop, not consumer auth. The allow-list names the GATEWAY
      # and IS the authorization decision here: issuer tokens carry no audience
      # claim, so every other check passes for any valid token from it.
      JWT_REQUIRED     = var.gold_api_jwt_required ? "true" : "false"
      JWT_ISSUER       = var.gold_api_jwt_issuer
      JWT_JWKS_URL     = var.gold_api_jwt_jwks_url
      JWT_TOKEN_TYPE   = var.gold_api_jwt_token_type
      JWT_ALGS         = join(",", var.gold_api_jwt_algs)
      JWT_ALLOWED_SUBS = join(",", var.gold_api_jwt_allowed_subs)
    }
  }

  tags = local.common_tags
}

# Reached through the Mule gateway, not a browser, so no CORS block.
#
# authorization_type = NONE and, while the gateway secret is unpopulated, no
# backend check either — so anyone who learns this URL can read gold data.
# That is the accepted position until the gateway layer lands (owner decision
# 2026-09-24), not an oversight. AWS_IAM here plus SigV4 at the gateway would
# remove the shared secret entirely if the gateway can sign.
resource "aws_lambda_function_url" "gold_serving_api" {
  function_name      = aws_lambda_function.gold_serving_api.function_name
  authorization_type = "NONE"
}

# Terraform-created NONE-auth URLs 403 without this, the same fix the peer
# service URLs carry.
resource "aws_lambda_permission" "gold_serving_api_public_url" {
  statement_id           = "AllowPublicFunctionUrl"
  action                 = "lambda:InvokeFunctionUrl"
  function_name          = aws_lambda_function.gold_serving_api.function_name
  principal              = "*"
  function_url_auth_type = "NONE"
}

resource "aws_cloudwatch_log_group" "gold_serving_api" {
  name              = "/aws/lambda/${aws_lambda_function.gold_serving_api.function_name}"
  retention_in_days = 30
  tags              = local.common_tags
}

# ── Alarms ──────────────────────────────────────────────────────────────────

resource "aws_cloudwatch_metric_alarm" "gold_serving_api_failed" {
  alarm_name          = "${local.name_prefix}-gold-serving-api-failed"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "Errors"
  namespace           = "AWS/Lambda"
  period              = 300
  statistic           = "Sum"
  threshold           = 0
  treat_missing_data  = "notBreaching"

  dimensions = { FunctionName = aws_lambda_function.gold_serving_api.function_name }

  alarm_description = join(" ", [
    "The gold retrieval API raised. A handled refusal is a 4XX or 5XX response",
    "and does NOT appear here, so this means an unhandled exception:",
    "read the log group, not the response codes.",
  ])

  alarm_actions = local.alert_targets
  ok_actions    = local.alert_targets
  tags          = local.common_tags
}

# A business key that resolves to more than one row.
#
# This exists because of the alarm ABOVE, whose own description says a handled
# refusal "does NOT appear here". An entity addressed by its business key is
# served through a secondary index, and an index does not enforce uniqueness —
# so the API detects a duplicate and returns 500 rather than picking one. That
# is a RETURNED 500, which never increments Lambda's Errors metric, so without
# this filter the duplicate would be refused, logged, and alarm on nothing.
#
# The pattern is a contract with gold_serving_api.py's log line. A test pins
# the token on both sides.
resource "aws_cloudwatch_log_metric_filter" "gold_serving_api_key_not_unique" {
  name           = "${local.name_prefix}-gold-serving-api-entity-key-not-unique"
  log_group_name = aws_cloudwatch_log_group.gold_serving_api.name
  pattern        = "ENTITY_KEY_NOT_UNIQUE"

  metric_transformation {
    name          = "EntityKeyNotUnique"
    namespace     = "UIF/Gold"
    value         = "1"
    default_value = 0
  }
}

resource "aws_cloudwatch_metric_alarm" "gold_serving_api_key_not_unique" {
  alarm_name          = "${local.name_prefix}-gold-serving-api-entity-key-not-unique"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  # A LITERAL, matching the filter's metric_transformation above. An
  # interpolated reference cannot drift, but it also cannot be read by a human
  # scanning the alarm or by this repo's text-based Terraform assertions — and
  # one of those assertions is what keeps a UIF/Gold alarm from reading a
  # metric nothing emits. A test pins the pair instead.
  metric_name        = "EntityKeyNotUnique"
  namespace          = "UIF/Gold"
  period             = 300
  statistic          = "Sum"
  threshold          = 0
  treat_missing_data = "notBreaching"

  alarm_description = join(" ", [
    "A gold entity's business key resolved to more than one row, so the API",
    "refused the read rather than serving one of two answers. This is a DATA",
    "defect, not a service fault: the gold table's merge key still guarantees",
    "one row per merge key, but the business key it is served by is not",
    "enforced by anything. Find the duplicate before a consumer notices.",
  ])

  alarm_actions = local.alert_targets
  ok_actions    = local.alert_targets
  tags          = local.common_tags
}

resource "aws_cloudwatch_metric_alarm" "gold_serving_api_throttled" {
  alarm_name          = "${local.name_prefix}-gold-serving-api-throttled"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "Throttles"
  namespace           = "AWS/Lambda"
  period              = 300
  statistic           = "Sum"
  threshold           = 0
  treat_missing_data  = "notBreaching"

  dimensions = { FunctionName = aws_lambda_function.gold_serving_api.function_name }

  alarm_description = join(" ", [
    "The retrieval API is being throttled, so consumers are seeing 429s from",
    "Lambda itself. This function carries no reserved concurrency, so this",
    "means the ACCOUNT limit is the constraint.",
  ])

  alarm_actions = local.alert_targets
  ok_actions    = local.alert_targets
  tags          = local.common_tags
}

# ── The SLO, as something that fires ────────────────────────────────────────
#
# capabilities/api/guidelines.md asks for an SLO before shipping. These two
# alarms ARE it for latency: the numbers below are the ones the stock contract
# publishes, and a test asserts the pair cannot drift apart. The other two
# lines of the SLO already had mechanisms and are not duplicated here —
# availability rides the Errors and Throttles alarms above, and freshness is
# the per-entity FreshnessBreached alarm in alerts.tf.
#
# Every figure comes from measurement on dev (2026-09-28, 300 samples per
# path), not from a target someone liked the look of:
#
#   warm p50   5.2 ms      warm p99   12.4 ms
#   cold p50   1,091 ms    cold max   1,211 ms
#
# COLD STARTS ARE ACCEPTED (owner decision 2026-09-28) rather than bought out
# with provisioned concurrency, which is a standing charge whether requests
# arrive or not and would dominate the bill at today's traffic of none. That
# decision is the whole reason the p99 line is 1,300 ms and not 50.

resource "aws_cloudwatch_metric_alarm" "gold_serving_api_slow_warm" {
  alarm_name          = "${local.name_prefix}-gold-serving-api-slow-warm"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  datapoints_to_alarm = 2
  metric_name         = "Duration"
  namespace           = "AWS/Lambda"
  period              = 300
  extended_statistic  = "p50"
  threshold           = local.serving_api_slo.p50_ms
  treat_missing_data  = "notBreaching"

  dimensions = { FunctionName = aws_lambda_function.gold_serving_api.function_name }

  # The regression alarm, and the more useful of the two. A median response is
  # a warm one, so this watches the work per request rather than the sandbox
  # lifecycle. It is set at four times the measured 5.2 ms because the failure
  # it exists to catch is structural, not marginal: the version of this
  # handler before 2026-09-28 built three boto3 clients and re-read the config
  # and the master schema on EVERY request, and sat at 317 ms. This would have
  # said so in ten minutes instead of nobody noticing for four days.
  alarm_description = join(" ", [
    "The gold retrieval API's MEDIAN response has passed",
    "${local.serving_api_slo.p50_ms} ms, so warm requests are slow and this is",
    "not cold starts. Suspect per-request work that should be cached: the",
    "client memoisation or the spec cache in gold_serving_api.py.",
  ])

  alarm_actions = local.alert_targets
  ok_actions    = local.alert_targets
  tags          = local.common_tags
}

resource "aws_cloudwatch_metric_alarm" "gold_serving_api_slo_breached" {
  alarm_name          = "${local.name_prefix}-gold-serving-api-slo-breached"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "Duration"
  namespace           = "AWS/Lambda"
  # An HOUR, not five minutes. A percentile needs samples to mean anything,
  # and at this API's traffic a 300-second window would make p99 the single
  # worst request — so the alarm would report "one cold start happened",
  # which is expected behaviour and not a breach.
  period             = 3600
  extended_statistic = "p99"
  threshold          = local.serving_api_slo.p99_ms
  treat_missing_data = "notBreaching"

  dimensions = { FunctionName = aws_lambda_function.gold_serving_api.function_name }

  alarm_description = join(" ", [
    "The gold retrieval API has breached its published p99 of",
    "${local.serving_api_slo.p99_ms} ms over an hour. Cold starts are inside",
    "this budget by design — measured at 1,211 ms worst case — so a breach",
    "means either cold starts got slower or they stopped being rare.",
  ])

  alarm_actions = local.alert_targets
  ok_actions    = local.alert_targets
  tags          = local.common_tags
}

output "gold_serving_api_url" {
  description = "The retrieval API's function URL — the origin the Mule gateway fronts."
  value       = aws_lambda_function_url.gold_serving_api.function_url
}
