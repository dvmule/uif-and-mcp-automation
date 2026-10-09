# scheduler.tf — per-export schedules (UIFPE-206).
#
# This file REPLACES eventbridge.tf's single-cron chain (rule -> SQS -> Pipe,
# permanently disabled, one hardcoded config path). Authors now schedule each
# export from the control panel; the API creates ONE EventBridge Scheduler
# schedule per export. The schedules themselves are runtime data (like the
# glue-job-config rows), so Terraform owns only the roles and the target
# Lambda — never the schedules.
#
# Flow: schedule fires -> scheduled-starter Lambda -> resolves the export's
# LATEST published version -> skips (and records) if a batch for that export
# is still running -> starts the same Step Functions execution Run-now uses,
# with trigger="scheduled". One run path keeps live status, stop and run
# history working unchanged.
#
# Missed triggers are NEVER caught up (owner decision, 27 Aug 2026): a feed
# delivered hours late can be worse than no feed. Scheduler retries target
# invocation errors briefly (transient Lambda throttles), but an expired
# window is a missed run — visible via UIFPE-208's alarms.
#
# The schedules live in the account's DEFAULT schedule group, like every
# other team's (checked 2026-08-27: the two existing schedules in this
# account both use it, and the deploy role has no scheduler:* permissions to
# create a group of our own). Isolation is by name instead: every Beacon
# schedule is named beacon-<env>-export-<id>, and the API role's permissions
# are scoped to exactly that prefix. Moving to a dedicated group later is a
# one-line policy ask plus re-PUTting the schedules — they are runtime data.
#
# Naming: the schedules and table follow the <project>-<env>-<operation>
# convention; IAM roles and the Lambda keep the ${local.name} prefix because
# the CI deploy role's IAM/Lambda permissions are scoped to uif-beacon-*.

locals {
  scheduler_group      = "default"
  schedule_name_prefix = "beacon-${var.env}-export-" # + <export id>
}

# -- the role Scheduler assumes to invoke the starter -------------------------
# uif:rename-approved UIFPE-365
resource "aws_iam_role" "scheduler_invoke" {
  # Renaming this invalidates existing schedules: Scheduler stores the role ARN
  # on each, and they are runtime data. Re-PUT via /catalog/{job_id}/schedule.
  name = "${local.name}-scheduler-invoke-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "scheduler.amazonaws.com" }
      # confused-deputy guard: only schedules in THIS account may assume it
      Condition = { StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.current.account_id } }
    }]
  })
  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_iam_role_policy" "scheduler_invoke" {
  name = "invoke-scheduled-starter"
  role = aws_iam_role.scheduler_invoke.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "lambda:InvokeFunction"
      Resource = aws_lambda_function.scheduled_starter.arn
    }]
  })
}

# -- the scheduled-starter Lambda ---------------------------------------------
# uif:rename-approved UIFPE-365
resource "aws_iam_role" "lambda_scheduled_starter" {
  name               = "${local.name}-scheduled-starter-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  lifecycle {
    create_before_destroy = true
  }
}

data "aws_iam_policy_document" "lambda_scheduled_starter" {
  statement { # resolve the latest published version + read transform sizing
    # + the stored incremental watermark (UIFPE-168) + the schedule record's
    # pause window (UIFPE-231)
    sid     = "ReadCatalogAndSizing"
    actions = ["dynamodb:GetItem", "dynamodb:Query"]
    resources = [aws_dynamodb_table.catalog.arn,
      aws_dynamodb_table.glue_job_config.arn,
      aws_dynamodb_table.extraction_state.arn,
    aws_dynamodb_table.schedules.arn]
  }
  statement { # record skipped triggers where the export's history is read
    sid       = "RecordSkip"
    actions   = ["dynamodb:PutItem"]
    resources = [aws_dynamodb_table.runs.arn]
  }
  statement { # overlap check + start: same run path as the API's Run now
    sid       = "StartExport"
    actions   = ["states:StartExecution", "states:ListExecutions"]
    resources = [aws_sfn_state_machine.export.arn]
  }
  statement {
    sid       = "InspectRunning"
    actions   = ["states:DescribeExecution"]
    resources = ["arn:aws:states:${var.region}:${data.aws_caller_identity.current.account_id}:execution:${local.name}-export:*"]
  }
}

resource "aws_iam_role_policy" "lambda_scheduled_starter" {
  name   = "scheduled-starter"
  role   = aws_iam_role.lambda_scheduled_starter.id
  policy = data.aws_iam_policy_document.lambda_scheduled_starter.json
}

resource "aws_lambda_function" "scheduled_starter" {
  function_name    = "${local.name}-scheduled-starter"
  role             = aws_iam_role.lambda_scheduled_starter.arn
  runtime          = local.lambda_runtime
  handler          = "beacon_export.lambdas.scheduled_starter.handler"
  s3_bucket        = local.lambda_zip.s3_bucket
  s3_key           = local.lambda_zip.s3_key
  source_code_hash = var.lambda_zip_hash != "" ? var.lambda_zip_hash : null
  timeout          = 60 # list+describe running executions, then one StartExecution

  environment {
    variables = {
      BEACON_STATE_MACHINE_ARN      = aws_sfn_state_machine.export.arn
      BEACON_TABLE_CATALOG          = aws_dynamodb_table.catalog.name
      BEACON_TABLE_RUNS             = aws_dynamodb_table.runs.name
      BEACON_TABLE_GLUE_CONFIG      = aws_dynamodb_table.glue_job_config.name
      BEACON_TABLE_EXTRACTION_STATE = aws_dynamodb_table.extraction_state.name
      # UIFPE-231: pause windows live on the schedule record
      BEACON_TABLE_SCHEDULES = aws_dynamodb_table.schedules.name
    }
  }
}

resource "aws_cloudwatch_log_group" "scheduled_starter" {
  name              = "/aws/lambda/${aws_lambda_function.scheduled_starter.function_name}"
  retention_in_days = 30
}

resource "aws_iam_role_policy_attachment" "scheduled_starter_logs" {
  role       = aws_iam_role.lambda_scheduled_starter.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

output "state_machine_arn" {
  value = aws_sfn_state_machine.export.arn
}
