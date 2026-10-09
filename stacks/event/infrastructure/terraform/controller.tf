# The pipeline controller (thin trigger) - ONE Lambda for every domain.
# GoldMergeCompleted (any domain) and manual announcements funnel here; the
# domain's pipeline-configs record supplies everything domain-specific.
# Moved from domains/product (2026-09-21); adopted via imports.tf.

# ── Thin-trigger Lambda: validates {"run_id": ...}, starts the job
#    (API registry invocation planned here) ─────────────────────────────────

resource "aws_iam_role" "pipeline_controller" {
  name = "${local.event_stack_prefix}-pipeline-controller-role"

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

resource "aws_iam_role_policy" "pipeline_controller" {
  name = "${local.event_stack_prefix}-pipeline-controller-policy"
  role = aws_iam_role.pipeline_controller.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:*"
      },
      {
        Effect   = "Allow"
        Action   = ["glue:StartJobRun", "glue:GetJobRun"]
        Resource = aws_glue_job.event_fabric_processor.arn
      },
      {
        # Pipeline master config, read-only (sizing, skip rules, publish ops).
        Effect   = "Allow"
        Action   = ["dynamodb:GetItem"]
        Resource = aws_dynamodb_table.glue_configs.arn
      },
      {
        # Derives the run's op counts from the change log at trigger time.
        Effect = "Allow"
        Action = [
          "athena:StartQueryExecution", "athena:GetQueryExecution",
          "athena:GetQueryResults", "athena:StopQueryExecution",
        ]
        Resource = "arn:aws:athena:${var.aws_region}:${data.aws_caller_identity.current.account_id}:workgroup/${var.athena_workgroup}"
      },
      {
        Effect = "Allow"
        Action = ["glue:GetDatabase", "glue:GetTable", "glue:GetPartition", "glue:GetPartitions"]
        # Every gold database by convention - see glue.tf.
        Resource = [
          "arn:aws:glue:${var.aws_region}:${data.aws_caller_identity.current.account_id}:catalog",
          "arn:aws:glue:${var.aws_region}:${data.aws_caller_identity.current.account_id}:database/${local.gold_db_wildcard}",
          "arn:aws:glue:${var.aws_region}:${data.aws_caller_identity.current.account_id}:table/${local.gold_db_wildcard}/*",
        ]
      },
      {
        # Athena result files under the pipeline's own prefix.
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject"]
        Resource = "arn:aws:s3:::${var.gold_artifacts_bucket}/${local.working_prefix}/athena-results/*"
      },
      {
        Effect   = "Allow"
        Action   = ["s3:GetBucketLocation", "s3:ListBucket"]
        Resource = "arn:aws:s3:::${var.gold_artifacts_bucket}"
      },
      {
        # The change log is Lake Formation-governed (like the EFP role, the
        # Lambda role needs LF grants added out-of-band: DESCRIBE on the DB,
        # SELECT/DESCRIBE on the changes table).
        Effect   = "Allow"
        Action   = ["lakeformation:GetDataAccess"]
        Resource = "*"
      },
      {
        # Pipeline metrics (Table 2): write the IN_PROGRESS row per started
        # run, and query the sparse gsi_gold_run for idempotency/replay_count.
        Effect = "Allow"
        Action = ["dynamodb:PutItem", "dynamodb:Query"]
        Resource = [
          aws_dynamodb_table.pipeline_metrics.arn,
          "${aws_dynamodb_table.pipeline_metrics.arn}/index/*",
        ]
      },
      {
        # Failed async invocations dead-letter here (never silent-drop).
        Effect   = "Allow"
        Action   = ["sqs:SendMessage"]
        Resource = aws_sqs_queue.trigger_dlq.arn
      },
    ]
  })
}
# Async-failure dead letters for the SHARED controller (its own invocation
# failures + the GoldMergeCompleted rule's delivery failures). Distinct from
# the per-domain delivery DLQs (event-domain module): messages here carry no
# RULE_ARN routing to a subscriber and are operator-owned.
resource "aws_sqs_queue" "trigger_dlq" {
  name                      = "${local.event_stack_prefix}-trigger-dlq"
  message_retention_seconds = 1209600 # 14 days
  sqs_managed_sse_enabled   = true
  tags                      = local.common_tags
}

# EventBridge needs an explicit grant to write dead letters (SQS denies
# service principals by default; without it the event is dropped with only
# the InvocationsFailedToBeSentToDlq metric). Scoped to rules on the
# gold-merge control bus - the only rule targeting this queue. The
# controller's own async dead letters authorize via its role instead.
resource "aws_sqs_queue_policy" "trigger_dlq" {
  queue_url = aws_sqs_queue.trigger_dlq.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "AllowEventBridgeDeadLetters"
      Effect    = "Allow"
      Principal = { Service = "events.amazonaws.com" }
      Action    = "sqs:SendMessage"
      Resource  = aws_sqs_queue.trigger_dlq.arn
      Condition = {
        ArnLike = {
          "aws:SourceArn" = "arn:aws:events:${var.aws_region}:${data.aws_caller_identity.current.account_id}:rule/${local.gold_merge_bus}/*"
        }
      }
    }]
  })
}

resource "aws_lambda_function" "pipeline_controller" {
  # uif-{stack}-{env}-{subject}-controller (UIF Resource Name Lookup;
  # stack = event, consistent with the stack's tables - renamed from
  # echo-<env>-product-change-processor).
  function_name = "${local.event_stack_prefix}-pipeline-controller"
  description   = "Thin trigger - starts the Event Fabric Processor Glue job for the announced run_id (API registry invocation planned here next)."
  role          = aws_iam_role.pipeline_controller.arn
  runtime       = "python3.13"
  handler       = "handler.lambda_handler"
  timeout       = 30
  # Code from the release-artifacts bucket at its content-addressed key
  # (locals.tf, release.tf) -- never zipped from the repo at plan time.
  s3_bucket        = aws_s3_bucket.release_artifacts.id
  s3_key           = local.lambda_s3_key["pipeline_controller"]
  source_code_hash = lookup(var.lambda_code_hashes, "pipeline_controller", null)

  # NOTHING per-domain in this environment: gold database, Athena output
  # and every processor argument come from the domain's pipeline-configs
  # record at run time (the stack is shared; see handler.py).
  environment {
    variables = {
      EVENT_FABRIC_GLUE_JOB  = aws_glue_job.event_fabric_processor.name
      PIPELINE_CONFIGS_TABLE = aws_dynamodb_table.glue_configs.name
      PIPELINE_METRICS_TABLE = aws_dynamodb_table.pipeline_metrics.name
      PIPELINE_ID_PREFIX     = "event-${var.environment}"
      ATHENA_WORKGROUP       = var.athena_workgroup
    }
  }

  dead_letter_config {
    target_arn = aws_sqs_queue.trigger_dlq.arn
  }

  tags = local.common_tags
}

# ── Trigger: the gold transform's completion event starts the pipeline ──────
# The gold transform publishes GoldMergeCompleted (Source = its own job name,
# to the platform bus when a merge finishes; this rule routes product merges
# to the thin-trigger Lambda, which skips non-Success merges and starts the
# Event Fabric Processor with the merge's run_id and delta table. Manual
# reprocessing still works by invoking the Lambda directly with
# {"run_id": ...}.

resource "aws_cloudwatch_event_rule" "gold_merge_completed" {
  count = var.enable_gold_merge_trigger ? 1 : 0

  name           = "${local.event_stack_prefix}-on-gold-merge-completed-publish"
  event_bus_name = local.gold_merge_bus
  # No source filter (Source is the publishing gold job's name, which
  # varies) and NO domain filter: the domain allowlist is DATA, not rule
  # config - the controller looks the domain up in pipeline-configs and
  # skips cleanly when no config is seeded. Onboarding a domain therefore
  # never edits this rule (the stack's no-change-per-domain invariant).
  event_pattern = jsonencode({
    "detail-type" = ["GoldMergeCompleted"]
  })
  tags = local.common_tags
}

resource "aws_cloudwatch_event_target" "gold_merge_completed" {
  count = var.enable_gold_merge_trigger ? 1 : 0

  rule           = aws_cloudwatch_event_rule.gold_merge_completed[0].name
  event_bus_name = local.gold_merge_bus
  # Keeps the live target id from the domain era - changing it would force
  # a target replace (a brief delivery gap) for zero benefit.
  target_id = "ProductChangeTrigger"
  arn       = aws_lambda_function.pipeline_controller.arn

  dead_letter_config {
    arn = aws_sqs_queue.trigger_dlq.arn
  }

  retry_policy {
    maximum_retry_attempts       = 5
    maximum_event_age_in_seconds = 3600
  }
}

resource "aws_lambda_permission" "gold_merge_completed" {
  count = var.enable_gold_merge_trigger ? 1 : 0

  function_name = aws_lambda_function.pipeline_controller.function_name
  action        = "lambda:InvokeFunction"
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.gold_merge_completed[0].arn
}
