# Gold alerting (UIFPE-543) — the alarms that page someone, and how.
#
# Every gold alarm here carries alarm_actions AND ok_actions, both pointing at
# ONE Lambda: uif-echo-{env}-gold-alert-notify. CloudWatch invokes it directly
# on a state change (alarm actions target Lambda natively since 2023), so there
# is no SNS topic between them — owner decision 2026-09-23: the deploy role's
# SNS grant covers echo-dev-* only, and a topic earns its place when a second
# channel (Teams via AWS Chatbot) needs fan-out, not before.
#
# The Lambda mails every ENABLED address in the alert-subscribers table through
# the Pearson Notifications API, using the credential beacon's export mail
# already holds (uif/beacon/{env}/notifications/api — owner decision: reuse, do
# not mint a second). WHO is told is a table row the Notifications page edits,
# never a Terraform change; Terraform seeds the owner's address once and then
# ignores the row.
#
# The metrics these alarms read are the EMF documents from PR 1 (UIF/Gold):
# gold_telemetry.run_document and tick_document. The test suite pins that every
# metric name used here is one the library actually emits.

# ── Who is told ─────────────────────────────────────────────────────────────

resource "aws_dynamodb_table" "alert_subscribers" {
  name         = "${local.name_prefix}-alert-subscribers"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "email"

  attribute {
    name = "email"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  tags = local.common_tags
}

# One row per address in var.alert_seed_subscribers, created once. After that
# the row is the console's (enable/disable/remove from the Notifications page),
# which is why changes to it are ignored here.
resource "aws_dynamodb_table_item" "alert_seed_subscriber" {
  for_each   = toset(var.alert_seed_subscribers)
  table_name = aws_dynamodb_table.alert_subscribers.name
  hash_key   = aws_dynamodb_table.alert_subscribers.hash_key

  item = jsonencode({
    email    = { S = lower(each.value) }
    enabled  = { BOOL = true }
    added_by = { S = "terraform:alert_seed_subscribers" }
    added_at = { S = timestamp() }
  })

  lifecycle {
    ignore_changes = [item]
  }
}

# ── The notifier ────────────────────────────────────────────────────────────

locals {
  # Beacon's Pearson Notifications credential, reused (owner decision). The
  # secret's VALUE is created by an admin through beacon's API, never here.
  alert_notifications_secret = "uif/beacon/${var.environment}/notifications/api"
}

data "archive_file" "gold_alert_notify" {
  type        = "zip"
  output_path = "${path.module}/.build/gold_alert_notify.zip"

  # Flat like the dispatcher's, plus the shared platform package as a package
  # DIRECTORY at the zip root (platform/python/README.md). Removing either
  # pearson_notifications file breaks every alert mail on import.
  source {
    content  = file("${path.module}/../../src/apps/lambdas/gold_alert_notify.py")
    filename = "gold_alert_notify.py"
  }
  source {
    content  = file("${path.module}/../../src/libs/gold_resources.py")
    filename = "gold_resources.py"
  }
  source {
    content  = file("${path.module}/../../../../platform/python/pearson_notifications/__init__.py")
    filename = "pearson_notifications/__init__.py"
  }
  source {
    content  = file("${path.module}/../../../../platform/python/pearson_notifications/client.py")
    filename = "pearson_notifications/client.py"
  }
}

resource "aws_iam_role" "gold_alert_notify" {
  name = "${local.name_prefix}-gold-alert-notify-role"

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

resource "aws_iam_role_policy" "gold_alert_notify" {
  name = "${local.name_prefix}-gold-alert-notify-policy"
  role = aws_iam_role.gold_alert_notify.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:*"
      },
      {
        # Who is told. Read-only: the Notifications page (PR 3) writes rows
        # through the controller, never this function.
        Effect   = "Allow"
        Action   = ["dynamodb:Scan"]
        Resource = aws_dynamodb_table.alert_subscribers.arn
      },
      {
        # Beacon's credential, value only — the trailing wildcard is the
        # random suffix Secrets Manager appends to every secret ARN.
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"]
        Resource = "arn:aws:secretsmanager:${var.aws_region}:${data.aws_caller_identity.current.account_id}:secret:${local.alert_notifications_secret}*"
      },
      {
        Effect   = "Allow"
        Action   = ["sqs:SendMessage"]
        Resource = aws_sqs_queue.gold_alert_notify_dlq.arn
      },
    ]
  })
}

# Inside the VPC, on beacon's reasoning (stacks/beacon/.../lambda.tf UIFPE-444):
# private-subnet egress reaches the Pearson-internal Notifications and PI
# endpoints over the corporate gateway route and AWS over NAT. Egress-only.
#
# The 0.0.0.0/0 egress is trivy AVD-AWS-0104 (CRITICAL). Suppressed rather than
# left failing, on the position this estate already holds: the main echo root
# carries the identical directive three times for the identical check
# (infrastructure/terraform/glue.tf:719, :733, :746, on owner instruction and
# following apicurio). Narrowing it would need the Pearson-internal endpoint
# ranges, which are not ours and move. This is consistency with the sibling
# root the same gate already enforces, not a new exception (UIFPE-742).
#
# The directive must be the LAST comment line before the block or trivy
# silently ignores it -- apicurio established that by testing.
# trivy:ignore:AVD-AWS-0104
resource "aws_security_group" "gold_alert_notify" {
  name   = "${local.name_prefix}-gold-alert-notify-sg"
  vpc_id = var.vpc_id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = local.common_tags

  # A renamed group forces replacement; keep the Lambda attached throughout,
  # and absorb Lambda's lazy ENI release (up to ~20 minutes) on delete.
  lifecycle {
    create_before_destroy = true
  }
  timeouts {
    delete = "45m"
  }
}

# The VPC's Secrets Manager interface endpoint has private DNS, so the
# notifier's credential read is forced through it - and its security group
# admits only listed groups. One additive HTTPS rule from ours. Replace, never
# update in place: the deploy role can Authorize/Revoke but not Modify rules.
resource "aws_vpc_security_group_ingress_rule" "gold_alert_notify_secretsmanager_access" {
  count                        = var.secretsmanager_endpoint_sg_id != "" ? 1 : 0
  security_group_id            = var.secretsmanager_endpoint_sg_id
  referenced_security_group_id = aws_security_group.gold_alert_notify.id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
  description                  = "gold alert notifier to secretsmanager vpc endpoint (UIFPE-543)"

  lifecycle {
    replace_triggered_by = [aws_security_group.gold_alert_notify.id]
  }
}

# ENI create/describe/delete for a VPC-attached function.
resource "aws_iam_role_policy_attachment" "gold_alert_notify_vpc" {
  role       = aws_iam_role.gold_alert_notify.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

resource "aws_lambda_function" "gold_alert_notify" {
  function_name = "${local.name_prefix}-gold-alert-notify"
  role          = aws_iam_role.gold_alert_notify.arn
  handler       = "gold_alert_notify.handler"
  runtime       = "python3.11"
  timeout       = 30
  memory_size   = 128
  # UIFPE-576: the staged release artifact when one exists, the plan-time
  # zip otherwise. lookup(), never an index — Terraform evaluates both
  # branches of a conditional, so a bare [] on an empty map fails the plan.
  filename         = local.lambda_staged["gold_alert_notify"] ? null : data.archive_file.gold_alert_notify.output_path
  s3_bucket        = local.lambda_staged["gold_alert_notify"] ? var.release_artifacts_bucket : null
  s3_key           = local.lambda_staged["gold_alert_notify"] ? local.lambda_s3_key["gold_alert_notify"] : null
  source_code_hash = local.lambda_staged["gold_alert_notify"] ? lookup(var.lambda_code_hashes, "gold_alert_notify", null) : data.archive_file.gold_alert_notify.output_base64sha256

  vpc_config {
    subnet_ids         = var.private_subnet_ids
    security_group_ids = [aws_security_group.gold_alert_notify.id]
  }

  environment {
    variables = {
      ENV                           = var.environment
      REGION                        = var.aws_region
      ALERT_SUBSCRIBERS_TABLE       = aws_dynamodb_table.alert_subscribers.name
      ALERT_NOTIFICATIONS_SECRET    = local.alert_notifications_secret
      ALERT_NOTIFICATIONS_API_URL   = var.notifications_api_url
      ALERT_NOTIFICATIONS_TOKEN_URL = var.notifications_token_url
    }
  }

  tags = local.common_tags

  depends_on = [aws_iam_role_policy_attachment.gold_alert_notify_vpc]
}

# CloudWatch alarms invoke the function directly. Scoped to this account's
# gold alarms by name prefix, so no other alarm in the account can drive it.
resource "aws_lambda_permission" "gold_alert_notify_from_alarms" {
  statement_id   = "AllowExecutionFromCloudWatchAlarms"
  action         = "lambda:InvokeFunction"
  function_name  = aws_lambda_function.gold_alert_notify.function_name
  principal      = "lambda.alarms.cloudwatch.amazonaws.com"
  source_account = data.aws_caller_identity.current.account_id
  source_arn     = "arn:aws:cloudwatch:${var.aws_region}:${data.aws_caller_identity.current.account_id}:alarm:${local.name_prefix}-*"
}

# Alarm actions invoke asynchronously, so a failing mail lands here after
# Lambda's own retries instead of vanishing.
resource "aws_sqs_queue" "gold_alert_notify_dlq" {
  name                      = "${local.name_prefix}-gold-alert-notify-dlq"
  message_retention_seconds = 1209600 # 14 days, the maximum
  sqs_managed_sse_enabled   = true
  tags                      = local.common_tags
}

resource "aws_lambda_function_event_invoke_config" "gold_alert_notify" {
  function_name          = aws_lambda_function.gold_alert_notify.function_name
  maximum_retry_attempts = 2

  destination_config {
    on_failure {
      destination = aws_sqs_queue.gold_alert_notify_dlq.arn
    }
  }
}

# ── The alarms ──────────────────────────────────────────────────────────────
#
# Names follow observability.md: uif-{stack}-{env}-{subject}-{condition}. The
# description is what the email says under "What it means", so it is written
# for the person reading it at 07:00, not for Terraform.

locals {
  alert_targets = [aws_lambda_function.gold_alert_notify.arn]

  # One alarm set PER GOLD ENTITY (owner feedback 2026-09-23: a fleet alarm's
  # mail could not say which table, and a second entity failing while another
  # was already in ALARM produced no transition, so no mail). The entities are
  # read from the domain pipeline YAMLs - the same file that makes an entity
  # exist makes its alarms exist, with no Terraform edit. Filename == gold_id
  # (pinned by test_gold_alert_notify).
  gold_entities = toset([
    for f in fileset("${path.module}/../../../..", "domains/*/src/apps/pipelines/base/*.yaml") :
    trimsuffix(basename(f), ".yaml")
  ])
}

# Alarm names hyphenate the entity (marketing_copy -> marketing-copy) to stay
# on the naming standard; the GoldId DIMENSION keeps the real id, and the
# notifier reads the dimension, never the name.
resource "aws_cloudwatch_metric_alarm" "gold_run_failed" {
  for_each            = local.gold_entities
  alarm_name          = "${local.name_prefix}-gold-${replace(each.key, "_", "-")}-run-failed"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "RunCount"
  namespace           = "UIF/Gold"
  period              = 300
  statistic           = "Sum"
  threshold           = 0
  treat_missing_data  = "notBreaching"

  dimensions = {
    Env    = var.environment
    GoldId = each.key
    Status = "FAILED"
  }

  alarm_description = join(" ", [
    "The ${each.key} gold transform or reconcile run FAILED in ${var.environment}.",
    "Open the Syncs page for this entity's run history and the error message on the",
    "metrics row; the Glue log stream is named after the run id. The next scheduled",
    "slot retries by itself once the failure cooldown passes.",
  ])

  alarm_actions = local.alert_targets
  ok_actions    = local.alert_targets
  tags          = local.common_tags
}

resource "aws_cloudwatch_metric_alarm" "gold_freshness_breached" {
  for_each            = local.gold_entities
  alarm_name          = "${local.name_prefix}-gold-${replace(each.key, "_", "-")}-freshness-breached"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  datapoints_to_alarm = 3
  metric_name         = "FreshnessBreached"
  namespace           = "UIF/Gold"
  period              = 300
  statistic           = "Maximum"
  threshold           = 0
  # Per entity, missing data is NOT a breach: a disabled entity emits no tick
  # document at all. "The dispatcher stopped sweeping" is gold_dispatch_silent's job.
  treat_missing_data = "notBreaching"

  dimensions = {
    Env    = var.environment
    GoldId = each.key
  }

  alarm_description = join(" ", [
    "The ${each.key} gold table in ${var.environment} has gone longer than 1.5x its",
    "cadence without a successful transform run. Check its schedule on the Syncs",
    "page, the dispatcher log for SKIPPED reasons, and the Glue run history.",
  ])

  alarm_actions = local.alert_targets
  ok_actions    = local.alert_targets
  tags          = local.common_tags
}

resource "aws_cloudwatch_metric_alarm" "gold_run_too_long" {
  for_each            = local.gold_entities
  alarm_name          = "${local.name_prefix}-gold-${replace(each.key, "_", "-")}-run-too-long"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "DurationSeconds"
  namespace           = "UIF/Gold"
  period              = 3600
  statistic           = "Maximum"
  threshold           = 5400
  treat_missing_data  = "notBreaching"

  dimensions = {
    Env    = var.environment
    GoldId = each.key
  }

  alarm_description = join(" ", [
    "A ${each.key} gold run in ${var.environment} took more than 90 minutes - half the",
    "Glue timeout and several times a normal incremental. Cost is climbing with it:",
    "check the run's Glue metrics for a full-load profile that should have been",
    "incremental, a Silver source without its snapshot pointer, or skew.",
  ])

  alarm_actions = local.alert_targets
  ok_actions    = local.alert_targets
  tags          = local.common_tags
}

# Fleet-level: the sweep itself. Every enabled entity gets a tick document
# every five minutes, so fifteen minutes with none means the dispatcher is not
# running at all. Missing data BREACHES here, and only here.
resource "aws_cloudwatch_metric_alarm" "gold_dispatch_silent" {
  alarm_name          = "${local.name_prefix}-gold-dispatch-silent"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 3
  datapoints_to_alarm = 3
  metric_name         = "Dispatched"
  namespace           = "UIF/Gold"
  period              = 300
  statistic           = "SampleCount"
  threshold           = 1
  treat_missing_data  = "breaching"

  dimensions = {
    Env = var.environment
  }

  alarm_description = join(" ", [
    "The gold dispatcher in ${var.environment} has produced no sweep telemetry for",
    "fifteen minutes: the 5-minute rule is not firing or the function is failing",
    "before its first entity. Nothing is being scheduled. Check the EventBridge rule",
    "and the dispatcher log.",
  ])

  alarm_actions = local.alert_targets
  ok_actions    = local.alert_targets
  tags          = local.common_tags
}

resource "aws_cloudwatch_metric_alarm" "gold_dispatch_errors" {
  alarm_name          = "${local.name_prefix}-gold-dispatch-errors"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "Errors"
  namespace           = "AWS/Lambda"
  period              = 300
  statistic           = "Sum"
  threshold           = 0
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = aws_lambda_function.gold_dispatcher.function_name
  }

  alarm_description = join(" ", [
    "The gold dispatcher Lambda in ${var.environment} raised on a sweep, so",
    "nothing was dispatched that tick. Slot-based scheduling means the next",
    "sweep retries the same slot; while this repeats, gold is not running.",
    "Read the function's log for the traceback.",
  ])

  alarm_actions = local.alert_targets
  ok_actions    = local.alert_targets
  tags          = local.common_tags
}

resource "aws_cloudwatch_metric_alarm" "gold_alert_notify_dlq_not_empty" {
  alarm_name          = "${local.name_prefix}-gold-alert-notify-dlq-not-empty"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "ApproximateNumberOfMessagesVisible"
  namespace           = "AWS/SQS"
  period              = 300
  statistic           = "Maximum"
  threshold           = 0
  treat_missing_data  = "notBreaching"

  dimensions = { QueueName = aws_sqs_queue.gold_alert_notify_dlq.name }

  alarm_description = join(" ", [
    "An alert mail could not be sent: the notifier failed after retries and the",
    "alarm event is in its dead-letter queue. Read the notifier's log for the",
    "Notifications API's refusal (credential, endpoint) - this alarm's own mail",
    "may not arrive for the same reason, so it is also on the dashboard.",
  ])

  # Wired anyway: the transition still shows on the dashboard's alarm widget,
  # and the mail arrives once the cause is fixed.
  alarm_actions = local.alert_targets
  ok_actions    = local.alert_targets
  tags          = local.common_tags
}

# ── The dashboard ───────────────────────────────────────────────────────────

locals {
  gold_alarm_arns = concat(
    [for a in aws_cloudwatch_metric_alarm.gold_run_failed : a.arn],
    [for a in aws_cloudwatch_metric_alarm.gold_freshness_breached : a.arn],
    [for a in aws_cloudwatch_metric_alarm.gold_run_too_long : a.arn],
    [
      aws_cloudwatch_metric_alarm.gold_dispatch_silent.arn,
      aws_cloudwatch_metric_alarm.gold_dispatch_errors.arn,
      aws_cloudwatch_metric_alarm.gold_announce_dlq_not_empty.arn,
      aws_cloudwatch_metric_alarm.gold_alert_notify_dlq_not_empty.arn,
    ],
  )

  # One SEARCH per panel: a new entity appears without a Terraform change.
  gold_search = "{UIF/Gold,Env,GoldId} Env=\"${var.environment}\" MetricName="
}

resource "aws_cloudwatch_dashboard" "gold" {
  dashboard_name = "${local.name_prefix}-gold"

  dashboard_body = jsonencode({
    widgets = [
      {
        type   = "alarm"
        x      = 0
        y      = 0
        width  = 24
        height = 3
        properties = {
          title  = "Gold alarms (${var.environment})"
          alarms = local.gold_alarm_arns
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 3
        width  = 12
        height = 6
        properties = {
          title   = "Runs per hour by outcome"
          region  = var.aws_region
          view    = "timeSeries"
          stacked = true
          period  = 3600
          stat    = "Sum"
          metrics = [[{ expression = "SEARCH('{UIF/Gold,Env,Status} Env=\"${var.environment}\" MetricName=\"RunCount\"', 'Sum', 3600)", label = "", id = "runs" }]]
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 3
        width  = 12
        height = 6
        properties = {
          title   = "Minutes since last successful transform, per entity"
          region  = var.aws_region
          view    = "timeSeries"
          period  = 300
          stat    = "Maximum"
          metrics = [[{ expression = "SEARCH('${local.gold_search}\"MinutesSinceLastSuccess\"', 'Maximum', 300)", label = "", id = "age" }]]
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 9
        width  = 12
        height = 6
        properties = {
          title   = "Freshness breached (1 = stale), per entity"
          region  = var.aws_region
          view    = "timeSeries"
          period  = 300
          stat    = "Maximum"
          metrics = [[{ expression = "SEARCH('${local.gold_search}\"FreshnessBreached\"', 'Maximum', 300)", label = "", id = "fresh" }]]
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 9
        width  = 12
        height = 6
        properties = {
          title   = "Run duration (seconds), per entity"
          region  = var.aws_region
          view    = "timeSeries"
          period  = 3600
          stat    = "Maximum"
          metrics = [[{ expression = "SEARCH('${local.gold_search}\"DurationSeconds\"', 'Maximum', 3600)", label = "", id = "dur" }]]
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 15
        width  = 12
        height = 6
        properties = {
          title   = "Rows written per run, per entity"
          region  = var.aws_region
          view    = "timeSeries"
          period  = 3600
          stat    = "Sum"
          metrics = [[{ expression = "SEARCH('${local.gold_search}\"RowsWritten\"', 'Sum', 3600)", label = "", id = "rows" }]]
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 15
        width  = 12
        height = 6
        properties = {
          title   = "Dispatches per day, per entity"
          region  = var.aws_region
          view    = "timeSeries"
          period  = 86400
          stat    = "Sum"
          metrics = [[{ expression = "SEARCH('${local.gold_search}\"Dispatched\"', 'Sum', 86400)", label = "", id = "disp" }]]
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 21
        width  = 12
        height = 6
        properties = {
          title  = "Dispatcher invocations and errors"
          region = var.aws_region
          view   = "timeSeries"
          period = 300
          metrics = [
            ["AWS/Lambda", "Invocations", "FunctionName", aws_lambda_function.gold_dispatcher.function_name, { stat = "Sum" }],
            ["AWS/Lambda", "Errors", "FunctionName", aws_lambda_function.gold_dispatcher.function_name, { stat = "Sum", color = "#d62728" }],
          ]
        }
      },
      {
        type   = "log"
        x      = 12
        y      = 21
        width  = 12
        height = 6
        properties = {
          title  = "Alert mail"
          region = var.aws_region
          query  = "SOURCE '/aws/lambda/${aws_lambda_function.gold_alert_notify.function_name}' | fields @timestamp, @message | filter @message like /ALERT MAILED|NO SUBSCRIBERS|SUBSCRIBER SKIPPED|NO ALARM IN EVENT/ | sort @timestamp desc | limit 50"
          view   = "table"
        }
      },
    ]
  })
}
