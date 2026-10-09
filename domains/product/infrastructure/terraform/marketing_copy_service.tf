# Marketing-copy content service (UIFPE-254) — the serving copy + GET API
# behind the claim-check pattern: gold_product.marketing_copy holds the
# library, this table serves it at API speed, and product's marketingCopy
# contentUrl (UIFPE-253) points here through the Mule gateway (UIFPE-255).
# Design: domains/product/docs/design-marketing-copy-content-service.md.
#
# One serving approach for all future gold APIs (owner decision 2026-09-03):
# DynamoDB, content stored GZIPPED (worst measured item compresses to
# 36.8 KB against the 400 KB limit; the sync Lambda refuses anything that
# would exceed its ceiling even compressed rather than writing truncated).
#
# The sync trigger is the NATIVE "Glue Job State Change" event on the
# default bus — deliberately not the platform GoldMergeCompleted bus, which
# is still gated (UIFPE-184 enable_gold_merge_trigger). The Lambda verifies
# the run was --gold_id marketing_copy before doing anything.
#
# API Function URL is AuthType NONE like the ai-draft/grain-probe/compile-
# preview peers (dev MVP; the Mule gateway owns consumer auth, and the
# backend validates a gateway-injected shared header once the secret below
# is given a value — until then the check is disabled and logged).
#
# Lake Formation: the sync role's SELECT+DESCRIBE on the gold tables is
# MANAGED IN TERRAFORM below via the uif-echo-gold-layer tag (UIFPE-229 made the
# deploy role a grantable tag-scoped grantor; beacon's lakeformation.tf is
# the proven precedent) — no manual post-deploy grant, and the EventBridge
# rule depends_on it so the trigger never goes live before the permission.

# ── Serving table ────────────────────────────────────────────────────────────
resource "aws_dynamodb_table" "marketing_copy_serving" {
  name         = "uif-${var.environment}-marketing-copy-serving"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "contentId"

  attribute {
    name = "contentId"
    type = "S"
  }

  # Derived data, rebuildable from gold with one {"mode":"baseline"} invoke —
  # PITR would protect nothing the change log doesn't already.
  tags = local.common_tags
}

# ── Gateway shared-header secret (shell — UIFPE-255 fills the value) ────────
resource "aws_secretsmanager_secret" "marketing_copy_gateway" {
  # echo-{env}-* prefix, NOT uif-{env}-*: the deploy role's Secrets Manager
  # grant (echo-dev-deploy v2) is scoped to echo-dev-* — the first deploy
  # failed AccessDenied on exactly this name choice (run 33768526371).
  name        = "${local.name_prefix}-uif-marketing-copy-gateway"
  description = "Shared header value the Mule gateway injects and the marketing-copy API validates (UIFPE-255). Empty = check disabled (pre-gateway dev posture)."
  tags        = local.common_tags
}

# ── Sync Lambda (the materializer) ──────────────────────────────────────────
data "archive_file" "marketing_copy_sync" {
  type        = "zip"
  source_dir  = "${path.module}/../../src/apps/lambda_marketing_copy_sync"
  output_path = "${path.module}/.build/marketing_copy_sync.zip"
  excludes    = ["__pycache__"]
}

# uif:rename-approved UIFPE-364
resource "aws_iam_role" "marketing_copy_sync" {
  # `mcopy` spelled out: nothing authorises abbreviating, and 38 chars fits the 64 cap.
  name = "${local.uif_prefix}-marketing-copy-sync-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = local.common_tags
  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_iam_role_policy" "marketing_copy_sync" {
  name = "marketing-copy-sync"
  role = aws_iam_role.marketing_copy_sync.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "athena:StartQueryExecution", "athena:GetQueryExecution",
          "athena:GetQueryResults", "athena:StopQueryExecution",
        ]
        Resource = "arn:aws:athena:${var.aws_region}:${data.aws_caller_identity.current.account_id}:workgroup/${var.athena_workgroup}"
      },
      {
        # the entity + its change log, in the shared product gold database
        Effect = "Allow"
        Action = ["glue:GetDatabase", "glue:GetTable", "glue:GetPartition", "glue:GetPartitions"]
        Resource = [
          "arn:aws:glue:${var.aws_region}:${data.aws_caller_identity.current.account_id}:catalog",
          "arn:aws:glue:${var.aws_region}:${data.aws_caller_identity.current.account_id}:database/${local.gold_db_prefix}_gold_product",
          "arn:aws:glue:${var.aws_region}:${data.aws_caller_identity.current.account_id}:table/${local.gold_db_prefix}_gold_product/marketing_copy",
          "arn:aws:glue:${var.aws_region}:${data.aws_caller_identity.current.account_id}:table/${local.gold_db_prefix}_gold_product/marketing_copy_changes",
        ]
      },
      {
        # which gold_id a finished run carried — the event filter's other
        # half. BOTH jobs: reconcile is the one producer of gold DELETIONS
        Effect = "Allow"
        Action = ["glue:GetJobRuns", "glue:GetJobRun"]
        Resource = [
          "arn:aws:glue:${var.aws_region}:${data.aws_caller_identity.current.account_id}:job/${local.uif_prefix}-gold-transform",
          "arn:aws:glue:${var.aws_region}:${data.aws_caller_identity.current.account_id}:job/${local.uif_prefix}-gold-reconcile",
        ]
      },
      {
        # gold data files (Iceberg reads go straight to S3)
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = ["arn:aws:s3:::${var.gold_bucket}/*"]
      },
      {
        Effect   = "Allow"
        Action   = ["s3:ListBucket", "s3:GetBucketLocation"]
        Resource = ["arn:aws:s3:::${var.gold_bucket}", "arn:aws:s3:::${var.gold_artifacts_bucket}"]
      },
      {
        # Athena results + UNLOAD data — the only prefixes this role writes.
        # DeleteObject: processed UNLOAD files are removed as they finish,
        # so retries re-see only unfinished work and nothing accumulates.
        Effect = "Allow"
        Action = [
          "s3:PutObject", "s3:GetObject", "s3:DeleteObject",
          "s3:AbortMultipartUpload", "s3:ListMultipartUploadParts",
        ]
        Resource = [
          "arn:aws:s3:::${var.gold_artifacts_bucket}/marketing-copy-sync/*",
        ]
      },
      {
        Effect   = "Allow"
        Action   = ["lakeformation:GetDataAccess"]
        Resource = "*"
      },
      {
        # conditional puts + batch deletes + the post-baseline prune scan
        Effect   = "Allow"
        Action   = ["dynamodb:BatchWriteItem", "dynamodb:PutItem", "dynamodb:DeleteItem", "dynamodb:Scan"]
        Resource = aws_dynamodb_table.marketing_copy_serving.arn
      },
      {
        # the on-failure destination — a dropped event must be visible
        Effect   = "Allow"
        Action   = ["sqs:SendMessage"]
        Resource = aws_sqs_queue.marketing_copy_sync_dlq.arn
      },
      {
        # baseline self-chain: the 6.5M-item first fill spans invocations
        Effect   = "Allow"
        Action   = ["lambda:InvokeFunction"]
        Resource = "arn:aws:lambda:${var.aws_region}:${data.aws_caller_identity.current.account_id}:function:${local.name_prefix}-uif-marketing-copy-sync"
      },
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:*"
      },
    ]
  })
}

resource "aws_lambda_function" "marketing_copy_sync" {
  function_name    = "${local.name_prefix}-uif-marketing-copy-sync"
  role             = aws_iam_role.marketing_copy_sync.arn
  handler          = "marketing_copy_sync.handler"
  runtime          = "python3.11"
  timeout          = 900
  memory_size      = 1024
  filename         = data.archive_file.marketing_copy_sync.output_path
  source_code_hash = data.archive_file.marketing_copy_sync.output_base64sha256

  # one writer at a time: a delta arriving mid-baseline queues (async)
  # instead of interleaving
  reserved_concurrent_executions = 1

  environment {
    variables = {
      ENV_NAME           = var.environment
      TABLE_NAME         = aws_dynamodb_table.marketing_copy_serving.name
      GOLD_DB            = "${local.gold_db_prefix}_gold_product"
      ATHENA_WORKGROUP   = var.athena_workgroup
      OUTPUT_S3          = "s3://${var.gold_artifacts_bucket}/marketing-copy-sync/results/"
      UNLOAD_S3          = "s3://${var.gold_artifacts_bucket}/marketing-copy-sync/unload"
      TRANSFORM_JOB      = "${local.uif_prefix}-gold-transform"
      RECONCILE_JOB      = "${local.uif_prefix}-gold-reconcile"
      SELF_FUNCTION_NAME = "${local.name_prefix}-uif-marketing-copy-sync"
    }
  }

  tags = local.common_tags
}

# A dropped event must be VISIBLE: after the async retries exhaust (or an
# event ages out behind a long baseline), the event lands here and the depth
# alarm fires — the estate's product-events DLQ pattern.
resource "aws_sqs_queue" "marketing_copy_sync_dlq" {
  name                      = "${local.name_prefix}-uif-marketing-copy-sync-dlq"
  message_retention_seconds = 1209600 # 14 days to investigate and redrive
  tags                      = local.common_tags
}

resource "aws_lambda_function_event_invoke_config" "marketing_copy_sync" {
  function_name          = aws_lambda_function.marketing_copy_sync.function_name
  maximum_retry_attempts = 2

  destination_config {
    on_failure {
      destination = aws_sqs_queue.marketing_copy_sync_dlq.arn
    }
  }
}

# The sync chains long fills by async self-invoke; Lambda's recursive-loop
# detection kills such chains after ~16 hops (a baseline needs ~46 — it
# stranded the 2026-09-03 fill in the DLQ). Declare the recursion as
# intended so the platform never breaks the chain again.
resource "aws_lambda_function_recursion_config" "marketing_copy_sync" {
  function_name  = aws_lambda_function.marketing_copy_sync.function_name
  recursive_loop = "Allow"
}

resource "aws_cloudwatch_metric_alarm" "marketing_copy_sync_dlq_depth" {
  alarm_name          = "${local.name_prefix}-uif-marketing-copy-sync-dlq-depth"
  alarm_description   = "A marketing-copy sync event was dropped after retries — the serving copy is stale for that run's contentIds; investigate and redrive (or run mode=baseline)."
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  dimensions = {
    QueueName = aws_sqs_queue.marketing_copy_sync_dlq.name
  }
  tags = local.common_tags
}

# The 380KB-compressed refusal is "content silently not served" unless it is
# loud: metric-filter the log line and alarm on it (the design doc promises
# "logged, alarmed" — this is the alarmed half).
resource "aws_cloudwatch_log_group" "marketing_copy_sync" {
  name              = "/aws/lambda/${local.name_prefix}-uif-marketing-copy-sync"
  retention_in_days = 30
  tags              = local.common_tags
}

resource "aws_cloudwatch_log_metric_filter" "marketing_copy_refused" {
  name           = "${local.name_prefix}-uif-marketing-copy-refused"
  log_group_name = aws_cloudwatch_log_group.marketing_copy_sync.name
  pattern        = "\"REFUSED contentId=\""

  metric_transformation {
    name      = "MarketingCopyServingRefused"
    namespace = "UIF/MarketingCopy"
    value     = "1"
  }
}

resource "aws_cloudwatch_metric_alarm" "marketing_copy_refused" {
  alarm_name          = "${local.name_prefix}-uif-marketing-copy-refused"
  alarm_description   = "An item exceeded the serving ceiling even compressed and was refused — that contentId serves 404/stale until resolved (raise the ceiling or use an S3 pointer)."
  namespace           = "UIF/MarketingCopy"
  metric_name         = "MarketingCopyServingRefused"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  tags                = local.common_tags
}

# Tag-scoped Lake Formation read for the sync role — Terraform-managed (the
# beacon precedent), so there is NO manual post-deploy grant and no window
# where the trigger is live but the permission is not.
resource "aws_lakeformation_permissions" "marketing_copy_sync_gold" {
  principal   = aws_iam_role.marketing_copy_sync.arn
  permissions = ["SELECT", "DESCRIBE"]

  lf_tag_policy {
    resource_type = "TABLE"
    expression {
      key    = "uif-echo-gold-layer"
      values = [var.environment]
    }
  }
}

resource "aws_cloudwatch_event_rule" "marketing_copy_sync" {
  name        = "${local.name_prefix}-uif-marketing-copy-sync"
  description = "Gold transform or reconcile finished — sync the marketing-copy serving copy (the Lambda checks the run's gold_id; reconcile is the one producer of deletions)"

  event_pattern = jsonencode({
    source        = ["aws.glue"]
    "detail-type" = ["Glue Job State Change"]
    detail = {
      jobName = [
        "${local.uif_prefix}-gold-transform",
        "${local.uif_prefix}-gold-reconcile",
      ]
      state = ["SUCCEEDED"]
    }
  })

  # the trigger must never go live before the permission it depends on
  depends_on = [aws_lakeformation_permissions.marketing_copy_sync_gold]

  tags = local.common_tags
}

resource "aws_cloudwatch_event_target" "marketing_copy_sync" {
  rule      = aws_cloudwatch_event_rule.marketing_copy_sync.name
  target_id = "MarketingCopySync"
  arn       = aws_lambda_function.marketing_copy_sync.arn
}

resource "aws_lambda_permission" "marketing_copy_sync_events" {
  statement_id  = "AllowEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.marketing_copy_sync.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.marketing_copy_sync.arn
}

# ── API Lambda ───────────────────────────────────────────────────────────────
# Explicit sources rather than source_dir: the zip carries a shared module
# from outside this directory, which source_dir cannot reach. Listing files
# also stops __pycache__ riding along, which the excludes line used to handle.
data "archive_file" "marketing_copy_api" {
  type        = "zip"
  output_path = "${path.module}/.build/marketing_copy_api.zip"

  source {
    content  = file("${path.module}/../../src/apps/lambda_marketing_copy_api/marketing_copy_api.py")
    filename = "marketing_copy_api.py"
  }
  # The gateway hop's token verification, the SAME implementation the gold
  # API uses (platform/python/README.md). COPIED into the zip, never imported
  # across the tree: a domain takes no runtime dependency on a stack's code
  # (ADR-0004), and platform/ is owned by neither.
  source {
    content  = file("${path.module}/../../../../platform/python/jwt_verify.py")
    filename = "jwt_verify.py"
  }
}

# uif:rename-approved UIFPE-364
resource "aws_iam_role" "marketing_copy_api" {
  name = "${local.uif_prefix}-marketing-copy-api-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = local.common_tags
  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_iam_role_policy" "marketing_copy_api" {
  name = "marketing-copy-api"
  role = aws_iam_role.marketing_copy_api.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["dynamodb:GetItem"]
        Resource = aws_dynamodb_table.marketing_copy_serving.arn
      },
      {
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = aws_secretsmanager_secret.marketing_copy_gateway.arn
      },
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:*"
      },
    ]
  })
}

resource "aws_lambda_function" "marketing_copy_api" {
  function_name = "${local.name_prefix}-uif-marketing-copy-api"
  role          = aws_iam_role.marketing_copy_api.arn
  handler       = "marketing_copy_api.handler"
  runtime       = "python3.11"
  timeout       = 10
  # 256MB gave ~1/7 vCPU: 3.3s cold starts, 130ms warm reads. 1024MB puts
  # both at target latency for roughly the same per-request cost.
  memory_size      = 1024
  filename         = data.archive_file.marketing_copy_api.output_path
  source_code_hash = data.archive_file.marketing_copy_api.output_base64sha256

  # no reserved concurrency: this is the 100 req/s consumer path — let it
  # scale; the sync Lambda is the one that must stay single-writer

  environment {
    variables = {
      ENV_NAME          = var.environment
      TABLE_NAME        = aws_dynamodb_table.marketing_copy_serving.name
      GATEWAY_SECRET_ID = aws_secretsmanager_secret.marketing_copy_gateway.name

      # The gateway hop, not consumer auth. Same variable names and values as
      # the gold retrieval API, so one configuration covers the estate.
      JWT_REQUIRED     = var.api_jwt_required ? "true" : "false"
      JWT_ISSUER       = var.api_jwt_issuer
      JWT_JWKS_URL     = var.api_jwt_jwks_url
      JWT_TOKEN_TYPE   = var.api_jwt_token_type
      JWT_ALGS         = join(",", var.api_jwt_algs)
      JWT_ALLOWED_SUBS = join(",", var.api_jwt_allowed_subs)
    }
  }

  tags = local.common_tags
}

resource "aws_lambda_function_url" "marketing_copy_api" {
  function_name      = aws_lambda_function.marketing_copy_api.function_name
  authorization_type = "NONE"
  # No CORS block: consumers reach this through the Mule gateway, not a
  # browser origin.
}

# Terraform-created NONE-auth URLs 403 without this — same fix the peer
# service URLs carry.
resource "aws_lambda_permission" "marketing_copy_api_public_url" {
  statement_id           = "AllowPublicFunctionUrl"
  action                 = "lambda:InvokeFunctionUrl"
  function_name          = aws_lambda_function.marketing_copy_api.function_name
  principal              = "*"
  function_url_auth_type = "NONE"
}

output "marketing_copy_api_url" {
  value       = aws_lambda_function_url.marketing_copy_api.function_url
  description = "Backend URL the Mule gateway routes /uif/v1/marketing-copy/* to (UIFPE-255)."
}

output "marketing_copy_serving_table" {
  value       = aws_dynamodb_table.marketing_copy_serving.name
  description = "The DynamoDB serving copy the sync Lambda maintains from the gold change log."
}
