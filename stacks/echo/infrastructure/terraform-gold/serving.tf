# Gold serving copy (UIFPE-518) — ONE table and ONE Lambda for every entity.
#
# Gold is Iceberg on S3 with no point lookup, so a synchronous GET needs a
# serving copy. Nothing in this file is per-entity: the table is keyed by
# entity, the Lambda reads which entity an event is about, and serving an
# entity is a `serving.enabled` line in its pipeline config.
#
# That is deliberate (owner direction 2026-09-24): infrastructure is a one-time
# step per environment, and adding a domain to it must be configuration. A
# table or a function per entity would make every new entity a Terraform apply.
#
# The retrieval half — the read Lambda and its public surface — is NOT here.

# ── The shared serving table ────────────────────────────────────────────────
#
# pk = "{entity}#{principal identifier}", sk = the rest of the grain. The
# split is what lets a contract operation over a partial key be a Query: for
# stock, GET /stock/{productId}/organization/{organizationCode} is a GetItem
# and GET /stock/{productId} is a Query on the partition. A single flat hash
# key would make the second one a Scan.
resource "aws_dynamodb_table" "gold_serving" {
  name         = "${local.name_prefix}-gold-serving"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"
  range_key    = "sk"

  attribute {
    name = "pk"
    type = "S"
  }
  attribute {
    name = "sk"
    type = "S"
  }
  attribute {
    name = "ek"
    type = "S"
  }
  attribute {
    name = "eks"
    type = "S"
  }

  # Business-key lookups, for an entity whose gold table merges on a different
  # key than the one its API addresses rows by. Program is the case: only its
  # driving Silver table carries SEGMENT1, so CDC needs the Oracle pair while
  # the API wants programId alone.
  #
  # SPARSE on purpose. The sync writes `ek` only for such an entity, so every
  # other one is absent from this index and costs nothing to store or write.
  #
  # HASH is the business key's FIRST column and SORT is the second, so one
  # index answers both reads: the item read pins both, and the collection read
  # pins the hash and pages the sort. A composite hash would make the item
  # read work and leave the collection read unanswerable.
  #
  # Projection ALL because the reader wants the whole item: KEYS_ONLY would
  # make every lookup two round trips, and the API's p50 budget is 20 ms. The
  # cost is a second copy of the indexed items only.
  global_secondary_index {
    name            = "entity-key-index"
    hash_key        = "ek"
    range_key       = "eks"
    projection_type = "ALL"
  }

  # Derived data, rebuildable from gold with one {"mode":"baseline"} invoke —
  # PITR would protect nothing the change log does not already.
  tags = local.common_tags
}

# ── The sync Lambda ─────────────────────────────────────────────────────────

# Written for the promotion model (capabilities/delivery/stack-promotion-model.md):
# when a release has staged the zip, the function runs the artifact the release
# built; otherwise it falls back to the plan-time zip, which is what the other
# seven gold Lambdas do today. The staging half that fills lambda_code_hashes
# for gold is UIFPE-576 — until then this takes the fallback branch, but the
# resource does not have to change when it lands.
data "archive_file" "gold_serving_sync" {
  type        = "zip"
  output_path = "${path.module}/.build/gold_serving_sync.zip"

  source {
    content  = file("${path.module}/../../src/apps/lambdas/gold_serving_sync.py")
    filename = "gold_serving_sync.py"
  }
  # The engine (bare-name import; the zip is flat). REMOVING THIS BLOCK BREAKS
  # EVERY SYNC.
  source {
    content  = file("${path.module}/../../src/libs/gold_serving.py")
    filename = "gold_serving.py"
  }
  # gold_serving imports physical_name from it, for the logical -> snake_case
  # column rendering (Schema Standards N8).
  source {
    content  = file("${path.module}/../../src/apps/pipeline/gold_cdc.py")
    filename = "gold_cdc.py"
  }
}

resource "aws_iam_role" "gold_serving_sync" {
  name = "${local.name_prefix}-gold-serving-sync-role"

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

resource "aws_iam_role_policy" "gold_serving_sync" {
  name = "${local.name_prefix}-gold-serving-sync-policy"
  role = aws_iam_role.gold_serving_sync.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AthenaQuery"
        Effect = "Allow"
        Action = [
          "athena:StartQueryExecution", "athena:GetQueryExecution",
          "athena:GetQueryResults", "athena:StopQueryExecution",
        ]
        Resource = "arn:aws:athena:${var.aws_region}:${data.aws_caller_identity.current.account_id}:workgroup/${var.athena_workgroup}"
      },
      {
        Sid    = "GoldCatalogRead"
        Effect = "Allow"
        Action = ["glue:GetDatabase", "glue:GetTable", "glue:GetPartition", "glue:GetPartitions"]
        # WILDCARD ON PURPOSE, over this environment's gold databases only.
        # Enumerating the served entities would mean a Terraform apply for
        # every entity that opts in, which is the thing this design removes.
        Resource = [
          "arn:aws:glue:${var.aws_region}:${data.aws_caller_identity.current.account_id}:catalog",
          "arn:aws:glue:${var.aws_region}:${data.aws_caller_identity.current.account_id}:database/${local.gold_db_prefix}_gold_*",
          "arn:aws:glue:${var.aws_region}:${data.aws_caller_identity.current.account_id}:table/${local.gold_db_prefix}_gold_*/*",
        ]
      },
      {
        Sid    = "ReadTheRunThatTriggeredUs"
        Effect = "Allow"
        Action = ["glue:GetJobRun", "glue:GetJobRuns"]
        Resource = [
          "arn:aws:glue:${var.aws_region}:${data.aws_caller_identity.current.account_id}:job/${local.name_prefix}-gold-transform",
          "arn:aws:glue:${var.aws_region}:${data.aws_caller_identity.current.account_id}:job/${local.name_prefix}-gold-reconcile",
        ]
      },
      {
        Sid      = "GoldDataRead"
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = ["arn:aws:s3:::${var.gold_bucket}/*"]
      },
      {
        Sid    = "ReadTheMasterSchema"
        Effect = "Allow"
        # Every entity's master lives at schemas/gold/{entity}/v{n}/master.json
        # and the exact path comes from that entity's config, so this grants
        # the PREFIX. Listing entities here would mean an IAM change to serve
        # a new one, which is the thing this design removes.
        Action   = ["s3:GetObject"]
        Resource = ["arn:aws:s3:::${var.gold_artifacts_bucket}/schemas/*"]
      },
      {
        Sid      = "ListForAthena"
        Effect   = "Allow"
        Action   = ["s3:ListBucket", "s3:GetBucketLocation"]
        Resource = ["arn:aws:s3:::${var.gold_bucket}", "arn:aws:s3:::${var.gold_artifacts_bucket}"]
      },
      {
        Sid    = "UnloadAndResults"
        Effect = "Allow"
        Action = [
          "s3:GetObject", "s3:PutObject", "s3:DeleteObject",
          "s3:AbortMultipartUpload",
        ]
        Resource = [
          "arn:aws:s3:::${var.gold_artifacts_bucket}/serving-sync/*",
          "arn:aws:s3:::${var.gold_artifacts_bucket}/athena-results/*",
        ]
      },
      {
        Sid      = "LakeFormationCredentialVending"
        Effect   = "Allow"
        Action   = ["lakeformation:GetDataAccess"]
        Resource = "*"
      },
      {
        Sid      = "ReadThePipelineConfig"
        Effect   = "Allow"
        Action   = ["dynamodb:GetItem"]
        Resource = aws_dynamodb_table.gold_pipelines_config.arn
      },
      {
        Sid      = "WriteTheServingCopy"
        Effect   = "Allow"
        Action   = ["dynamodb:BatchWriteItem", "dynamodb:PutItem", "dynamodb:DeleteItem", "dynamodb:Scan"]
        Resource = aws_dynamodb_table.gold_serving.arn
      },
      {
        Sid      = "DeadLetter"
        Effect   = "Allow"
        Action   = ["sqs:SendMessage"]
        Resource = aws_sqs_queue.gold_serving_sync_dlq.arn
      },
      {
        Sid      = "SelfChain"
        Effect   = "Allow"
        Action   = ["lambda:InvokeFunction"]
        Resource = "arn:aws:lambda:${var.aws_region}:${data.aws_caller_identity.current.account_id}:function:${local.name_prefix}-gold-serving-sync"
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

resource "aws_lambda_function" "gold_serving_sync" {
  function_name = "${local.name_prefix}-gold-serving-sync"
  role          = aws_iam_role.gold_serving_sync.arn
  handler       = "gold_serving_sync.handler"
  runtime       = "python3.11"
  timeout       = 900

  # 1769 MB, and this buys CPU rather than memory. Lambda scales vCPU with
  # memory and 1769 is the documented one-vCPU point; at 512 the function had
  # roughly a third of a core.
  #
  # Measured during price's first baseline, which is what settled it. The fill
  # ran at ~53 items/second per leg on 16 put threads while DynamoDB answered
  # PutItem in 3.5 ms average and reported ZERO conditional-check failures — so
  # ~300 ms per item was our own work, not the store's. Max memory used was
  # 330 MB of 512, so headroom was never the constraint.
  #
  # The work is heavy for a nested entity and it is SERIAL: build_item does one
  # json.loads plus up to 126 maps x 14 typed members per row, and the GIL means
  # the put threads do not help with any of it. That is also why this stops at
  # one vCPU — past 1769 MB the extra cores have nothing to do while the build
  # holds the GIL. marketing-copy is fast on the same engine because it is a
  # claim-check: one compressed blob per item, no per-member construction.
  memory_size = 1769

  filename  = local.lambda_staged["gold_serving_sync"] ? null : data.archive_file.gold_serving_sync.output_path
  s3_bucket = local.lambda_staged["gold_serving_sync"] ? var.release_artifacts_bucket : null
  # lookup(..., default), never an index: Terraform evaluates BOTH branches of
  # a conditional, so a bare ["gold_serving_sync"] on today's empty map fails
  # the plan with "Invalid index" - which `terraform validate` does not catch.
  s3_key           = local.lambda_staged["gold_serving_sync"] ? local.lambda_s3_key["gold_serving_sync"] : null
  source_code_hash = local.lambda_staged["gold_serving_sync"] ? lookup(var.lambda_code_hashes, "gold_serving_sync", null) : data.archive_file.gold_serving_sync.output_base64sha256

  environment {
    variables = {
      ENV_NAME           = var.environment
      CONFIG_TABLE       = aws_dynamodb_table.gold_pipelines_config.name
      TABLE_NAME         = aws_dynamodb_table.gold_serving.name
      ATHENA_WORKGROUP   = var.athena_workgroup
      ATHENA_OUTPUT      = "s3://${var.gold_artifacts_bucket}/athena-results/"
      UNLOAD_S3          = "s3://${var.gold_artifacts_bucket}/serving-sync/"
      TRANSFORM_JOB      = "${local.name_prefix}-gold-transform"
      RECONCILE_JOB      = "${local.name_prefix}-gold-reconcile"
      SELF_FUNCTION_NAME = "${local.name_prefix}-gold-serving-sync"
    }
  }

  tags = local.common_tags
}

# A dropped event must be VISIBLE: once the async retries exhaust, or an event
# ages out behind a long baseline, it lands here and the depth alarm fires.
# Encrypted like both sibling DLQs (gold_announce_dlq, gold_alert_notify_dlq).
# This one alone was missing it, which trivy reads as AVD-AWS-0096 (HIGH): a DLQ
# holds the same payloads the live queue does, so the odd one out was the bug,
# not the rule (UIFPE-742).
resource "aws_sqs_queue" "gold_serving_sync_dlq" {
  name                      = "${local.name_prefix}-gold-serving-sync-dlq"
  message_retention_seconds = 1209600 # 14 days to investigate and redrive
  sqs_managed_sse_enabled   = true
  tags                      = local.common_tags
}

resource "aws_lambda_function_event_invoke_config" "gold_serving_sync" {
  function_name          = aws_lambda_function.gold_serving_sync.function_name
  maximum_retry_attempts = 2

  destination_config {
    on_failure {
      destination = aws_sqs_queue.gold_serving_sync_dlq.arn
    }
  }
}

# The sync chains long fills by async self-invoke, and Lambda's recursive-loop
# detection kills such a chain after ~16 hops. Marketing-copy's 2026-09-03
# baseline needed ~46 and was stranded in the DLQ by exactly this. Declare the
# recursion as intended so the platform never breaks the chain again.
resource "aws_lambda_function_recursion_config" "gold_serving_sync" {
  function_name  = aws_lambda_function.gold_serving_sync.function_name
  recursive_loop = "Allow"
}

resource "aws_cloudwatch_log_group" "gold_serving_sync" {
  name              = "/aws/lambda/${aws_lambda_function.gold_serving_sync.function_name}"
  retention_in_days = 30
  tags              = local.common_tags
}

# ── Gold read grant ─────────────────────────────────────────────────────────
#
# Tag-based, so a newly registered gold database needs no grant work — the
# config sync applies the layer tag. Same reason the catalog grant above is a
# wildcard: a new served entity must not need a Terraform change.
resource "aws_lakeformation_permissions" "gold_serving_sync" {
  principal   = aws_iam_role.gold_serving_sync.arn
  permissions = ["SELECT", "DESCRIBE"]

  lf_tag_policy {
    resource_type = "TABLE"
    expression {
      key    = local.gold_layer_tag_key
      values = [var.environment]
    }
  }
}

# ── The trigger ─────────────────────────────────────────────────────────────
#
# ONE rule for every entity: the gold jobs are shared, so the job name says
# nothing about the entity and the Lambda reads the run's own --gold_id.
# Reconcile is included because it is the one producer of gold deletions — a
# transform-only trigger would never remove a serving item.
resource "aws_cloudwatch_event_rule" "gold_serving_sync" {
  name        = "${local.name_prefix}-on-gold-succeeded-serve"
  description = "A gold transform or reconcile finished — sync that entity's serving copy if it is served"

  event_pattern = jsonencode({
    source        = ["aws.glue"]
    "detail-type" = ["Glue Job State Change"]
    detail = {
      jobName = [
        "${local.name_prefix}-gold-transform",
        "${local.name_prefix}-gold-reconcile",
      ]
      state = ["SUCCEEDED"]
    }
  })

  # The trigger must never go live before the permission it depends on.
  depends_on = [aws_lakeformation_permissions.gold_serving_sync]

  tags = local.common_tags
}

resource "aws_cloudwatch_event_target" "gold_serving_sync" {
  rule      = aws_cloudwatch_event_rule.gold_serving_sync.name
  target_id = "GoldServingSync"
  arn       = aws_lambda_function.gold_serving_sync.arn
}

resource "aws_lambda_permission" "gold_serving_sync_events" {
  statement_id  = "AllowEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.gold_serving_sync.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.gold_serving_sync.arn
}

# ── Alarms ──────────────────────────────────────────────────────────────────
#
# Both carry alarm_actions AND ok_actions pointing at the gold notifier, per
# alerts.tf — an alarm with no action is a decoy (UIFPE-543).

resource "aws_cloudwatch_metric_alarm" "gold_serving_sync_dlq_depth" {
  alarm_name          = "${local.name_prefix}-gold-serving-sync-dropped"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "ApproximateNumberOfMessagesVisible"
  namespace           = "AWS/SQS"
  period              = 300
  statistic           = "Maximum"
  threshold           = 0
  treat_missing_data  = "notBreaching"

  dimensions = { QueueName = aws_sqs_queue.gold_serving_sync_dlq.name }

  alarm_description = join(" ", [
    "A serving sync was dropped after its retries.",
    "The serving copy is now BEHIND gold for that entity and will stay behind:",
    "the next run only carries its own change rows, so the missed rows are not",
    "retried by it. Read the DLQ message for the gold_id and the run id, then",
    "redrive, or invoke with {\"mode\":\"baseline\",\"gold_id\":\"<entity>\"} to refill.",
  ])

  alarm_actions = local.alert_targets
  ok_actions    = local.alert_targets
  tags          = local.common_tags
}

resource "aws_cloudwatch_metric_alarm" "gold_serving_sync_errors" {
  alarm_name          = "${local.name_prefix}-gold-serving-sync-failed"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "Errors"
  namespace           = "AWS/Lambda"
  period              = 300
  statistic           = "Sum"
  threshold           = 0
  treat_missing_data  = "notBreaching"

  dimensions = { FunctionName = aws_lambda_function.gold_serving_sync.function_name }

  alarm_description = join(" ", [
    "The gold serving sync raised. A self-chain leg that raises stops the",
    "chain, so a baseline can be left part-written; the conditional put makes",
    "a re-run safe, so re-invoking baseline is the recovery.",
  ])

  alarm_actions = local.alert_targets
  ok_actions    = local.alert_targets
  tags          = local.common_tags
}

output "gold_serving_table" {
  description = "The shared serving table every served gold entity writes into."
  value       = aws_dynamodb_table.gold_serving.name
}

output "gold_serving_sync_function" {
  description = "The one sync Lambda, for a manual baseline invoke."
  value       = aws_lambda_function.gold_serving_sync.function_name
}
