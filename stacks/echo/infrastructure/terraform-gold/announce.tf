# Gold merge announcer — gold tells the eventing layer a merge finished.
#
# The trigger is the NATIVE "Glue Job State Change" event on the default bus,
# the same shape marketing_copy_service.tf already consumes. Glue's event
# carries no domain and no entity, only jobName + jobRunId, so the Lambda
# resolves both: --gold_id off the run, then the domain off that entity's
# config item. gold_id is NOT the domain (marketing_copy's domain is product),
# which is why the config read is not optional.
#
# Which entities announce is config, not code: the Lambda publishes only for a
# gold_id whose config item sets `announce`. Adding a domain is a config write
# plus a sync_pipelines run, with no change here.

resource "aws_cloudwatch_event_bus" "event_control" {
  # The eventing layer's control bus. Created here because gold is its only
  # publisher today; the consumer rules live with their domains.
  name = "uif-event-${var.environment}-bus"
  tags = local.common_tags
}

data "archive_file" "gold_announce" {
  type        = "zip"
  output_path = "${path.module}/.build/gold_announce.zip"

  # Flat, like the dispatcher's: gold_announce.py imports its sibling by bare
  # name. Do not nest these under a directory.
  source {
    content  = file("${path.module}/../../src/apps/lambdas/gold_announce.py")
    filename = "gold_announce.py"
  }
  source {
    content  = file("${path.module}/../../src/libs/gold_resources.py")
    filename = "gold_resources.py"
  }
}

resource "aws_iam_role" "gold_announce" {
  name = "${local.name_prefix}-gold-announce-role"

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

resource "aws_iam_role_policy" "gold_announce" {
  # keeps the role/policy pairing: differs from the role by its last token only
  name = "${local.name_prefix}-gold-announce-policy"
  role = aws_iam_role.gold_announce.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:*:*:*"
      },
      {
        # Reads the run's Arguments for --gold_id; never starts a run.
        Effect = "Allow"
        Action = ["glue:GetJobRun"]
        Resource = [
          aws_glue_job.generic_gold_transform.arn,
          aws_glue_job.generic_gold_reconcile.arn,
        ]
      },
      {
        # The authoritative domain and the announce opt-in.
        Effect   = "Allow"
        Action   = ["dynamodb:GetItem"]
        Resource = aws_dynamodb_table.gold_pipelines_config.arn
      },
      {
        Effect   = "Allow"
        Action   = ["events:PutEvents"]
        Resource = aws_cloudwatch_event_bus.event_control.arn
      },
      {
        # The function's own on_failure destination. The queue policy below
        # authorizes EventBridge; this authorizes the Lambda.
        Effect   = "Allow"
        Action   = ["sqs:SendMessage"]
        Resource = aws_sqs_queue.gold_announce_dlq.arn
      },
    ]
  })
}

resource "aws_lambda_function" "gold_announce" {
  function_name = "${local.name_prefix}-gold-announce"
  role          = aws_iam_role.gold_announce.arn
  handler       = "gold_announce.handler"
  runtime       = "python3.11"
  timeout       = 30
  memory_size   = 128
  # UIFPE-576: the staged release artifact when one exists, the plan-time
  # zip otherwise. lookup(), never an index — Terraform evaluates both
  # branches of a conditional, so a bare [] on an empty map fails the plan.
  filename         = local.lambda_staged["gold_announce"] ? null : data.archive_file.gold_announce.output_path
  s3_bucket        = local.lambda_staged["gold_announce"] ? var.release_artifacts_bucket : null
  s3_key           = local.lambda_staged["gold_announce"] ? local.lambda_s3_key["gold_announce"] : null
  source_code_hash = local.lambda_staged["gold_announce"] ? lookup(var.lambda_code_hashes, "gold_announce", null) : data.archive_file.gold_announce.output_base64sha256

  environment {
    variables = {
      ENV                     = var.environment
      REGION                  = var.aws_region
      GOLD_CONFIG_TABLE       = aws_dynamodb_table.gold_pipelines_config.name
      GOLD_JOB_NAME           = aws_glue_job.generic_gold_transform.name
      GOLD_RECONCILE_JOB_NAME = aws_glue_job.generic_gold_reconcile.name
      EVENT_BUS_NAME          = aws_cloudwatch_event_bus.event_control.name
    }
  }

  tags = local.common_tags
}

# ── Failure plumbing ────────────────────────────────────────────────────────
#
# Two layers, because the invoke is asynchronous and each layer catches a
# different failure: the TARGET's dead_letter_config catches EventBridge
# failing to hand the event over, the function's invoke config catches the
# handler throwing. Without both, a gold run silently never publishes and
# nothing records that it should have.

resource "aws_sqs_queue" "gold_announce_dlq" {
  name                      = "${local.name_prefix}-gold-announce-dlq"
  message_retention_seconds = 1209600 # 14 days, the maximum
  sqs_managed_sse_enabled   = true
  tags                      = local.common_tags
}

resource "aws_sqs_queue_policy" "gold_announce_dlq" {
  queue_url = aws_sqs_queue.gold_announce_dlq.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "events.amazonaws.com" }
      Action    = "sqs:SendMessage"
      Resource  = aws_sqs_queue.gold_announce_dlq.arn
      Condition = {
        ArnEquals = {
          "aws:SourceArn" = aws_cloudwatch_event_rule.gold_succeeded.arn
        }
      }
    }]
  })
}

resource "aws_lambda_function_event_invoke_config" "gold_announce" {
  function_name          = aws_lambda_function.gold_announce.function_name
  maximum_retry_attempts = 2

  destination_config {
    on_failure {
      destination = aws_sqs_queue.gold_announce_dlq.arn
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "gold_announce_dlq_not_empty" {
  alarm_name          = "${local.name_prefix}-gold-announce-dlq-not-empty"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "ApproximateNumberOfMessagesVisible"
  namespace           = "AWS/SQS"
  period              = 300
  statistic           = "Maximum"
  threshold           = 0
  treat_missing_data  = "notBreaching"

  dimensions = { QueueName = aws_sqs_queue.gold_announce_dlq.name }

  alarm_description = join(" ", [
    "A completed gold merge was never announced to uif-event-${var.environment}-bus.",
    "The eventing layer is therefore behind on whatever ran: inspect the queue",
    "for the jobRunId, fix the cause, then re-announce by invoking",
    "${local.name_prefix}-gold-announce with the original Glue event.",
  ])

  # UIFPE-543: mails the alert-subscribers table via the notifier (alerts.tf).
  alarm_actions = [aws_lambda_function.gold_alert_notify.arn]
  ok_actions    = [aws_lambda_function.gold_alert_notify.arn]
  tags          = local.common_tags
}

# ── Trigger ─────────────────────────────────────────────────────────────────

resource "aws_cloudwatch_event_rule" "gold_succeeded" {
  name        = "${local.name_prefix}-on-gold-succeeded-announce"
  description = "A gold transform or reconcile run succeeded - announce the merge to the eventing layer (the Lambda resolves the entity and checks its announce opt-in)"

  # Default bus: Glue publishes its own state changes there, not to ours.
  event_pattern = jsonencode({
    source        = ["aws.glue"]
    "detail-type" = ["Glue Job State Change"]
    detail = {
      jobName = [
        aws_glue_job.generic_gold_transform.name,
        aws_glue_job.generic_gold_reconcile.name,
      ]
      state = ["SUCCEEDED"]
    }
  })

  tags = local.common_tags
}

resource "aws_cloudwatch_event_target" "gold_announce" {
  rule      = aws_cloudwatch_event_rule.gold_succeeded.name
  target_id = "GoldAnnounce"
  arn       = aws_lambda_function.gold_announce.arn

  dead_letter_config {
    arn = aws_sqs_queue.gold_announce_dlq.arn
  }

  retry_policy {
    maximum_retry_attempts       = 5
    maximum_event_age_in_seconds = 3600
  }
}

resource "aws_lambda_permission" "gold_announce" {
  statement_id  = "AllowExecutionFromEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.gold_announce.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.gold_succeeded.arn
}
