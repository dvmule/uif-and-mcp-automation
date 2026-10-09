# Gold dispatcher — our own EventBridge → Lambda → Glue control loop
# (ADR-0004: no dependency on the platform's scheduler_dispatcher).
#
# The Lambda source dir was `lambda/` in uif-dal-gold and is
# `src/apps/lambda_dispatcher/` here ("lambda" is a Python keyword, so the old
# name could never be a package). The zip stays FLAT — each `filename` below is
# a bare module name — because gold_dispatcher.py imports its siblings with
# `from echo_resources import echo` / `from gold_frequencies import ...`. Do not
# nest the modules under a directory.

data "archive_file" "gold_dispatcher" {
  type        = "zip"
  output_path = "${path.module}/.build/gold_dispatcher.zip"

  source {
    content  = file("${path.module}/../../src/apps/lambdas/gold_dispatcher.py")
    filename = "gold_dispatcher.py"
  }
  source {
    content  = file("${path.module}/../../src/libs/gold_resources.py")
    filename = "gold_resources.py"
  }
  # UIFPE-149: the cadence vocabulary, shared with the control-plane CLIs
  # (bare-name import; the zip is flat). REMOVING THIS BLOCK BREAKS EVERY TICK.
  source {
    content  = file("${path.module}/../../src/libs/gold_frequencies.py")
    filename = "gold_frequencies.py"
  }
  # UIFPE-543: EMF document builders (bare-name import; the zip is flat).
  source {
    content  = file("${path.module}/../../src/libs/gold_telemetry.py")
    filename = "gold_telemetry.py"
  }
}

# uif:rename-approved UIFPE-364
resource "aws_iam_role" "gold_dispatcher" {
  # `dispatch` is the operation word; `dispatcher` was the actor (00-core.md).
  name = "${local.name_prefix}-gold-dispatch-role"

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

resource "aws_iam_role_policy" "gold_dispatcher" {
  # keeps the role/policy pairing: differs from the role by its last token only
  name = "${local.name_prefix}-gold-dispatch-policy"
  role = aws_iam_role.gold_dispatcher.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:*:*:*"
      },
      {
        Effect   = "Allow"
        Action   = ["dynamodb:Scan", "dynamodb:GetItem"]
        Resource = aws_dynamodb_table.gold_pipelines_config.arn
      },
      {
        Effect = "Allow"
        Action = ["glue:StartJobRun", "glue:GetJobRuns", "glue:GetJobRun"]
        Resource = [
          aws_glue_job.generic_gold_transform.arn,
          aws_glue_job.generic_gold_reconcile.arn,
        ]
      },
      {
        # Snapshot pre-gate + first-run detection: the dispatcher reads
        # Iceberg metadata_location from the Silver sources and probes the
        # Gold target for existence. GetTable authorizes against catalog +
        # database + table ARNs — the job ARNs above grant none of that.
        # Scoped to this environment's uif_echo_* databases, which is what
        # every gold target and silver source is actually called — the old
        # echo_${env}_* spelling matched nothing after the ADR-0018 rename, so
        # the probe was permanently AccessDenied and the gate below never ran
        # at all (UIFPE-498). Silver read is metadata-only; no data
        # permissions are granted here. If the catalog is Lake
        # Formation-governed, this principal additionally needs LF DESCRIBE on
        # the silver/gold databases — grant it before enabling the gate, or
        # the gate stays fail-open (dispatches every tick, logs
        # GOLD_GATE_FAIL_OPEN).
        Effect = "Allow"
        Action = ["glue:GetTable"]
        Resource = [
          "arn:aws:glue:${var.aws_region}:${data.aws_caller_identity.current.account_id}:catalog",
          "arn:aws:glue:${var.aws_region}:${data.aws_caller_identity.current.account_id}:database/uif_echo_${var.environment}_*",
          "arn:aws:glue:${var.aws_region}:${data.aws_caller_identity.current.account_id}:table/uif_echo_${var.environment}_*/*",
        ]
      },
    ]
  })
}

data "aws_caller_identity" "current" {}

resource "aws_lambda_function" "gold_dispatcher" {
  function_name = "${local.name_prefix}-gold-dispatch"
  role          = aws_iam_role.gold_dispatcher.arn
  handler       = "gold_dispatcher.handler"
  runtime       = "python3.11"
  timeout       = 120
  memory_size   = 128
  # UIFPE-576: the staged release artifact when one exists, the plan-time
  # zip otherwise. lookup(), never an index — Terraform evaluates both
  # branches of a conditional, so a bare [] on an empty map fails the plan.
  filename         = local.lambda_staged["gold_dispatcher"] ? null : data.archive_file.gold_dispatcher.output_path
  s3_bucket        = local.lambda_staged["gold_dispatcher"] ? var.release_artifacts_bucket : null
  s3_key           = local.lambda_staged["gold_dispatcher"] ? local.lambda_s3_key["gold_dispatcher"] : null
  source_code_hash = local.lambda_staged["gold_dispatcher"] ? lookup(var.lambda_code_hashes, "gold_dispatcher", null) : data.archive_file.gold_dispatcher.output_base64sha256

  environment {
    variables = {
      ENV                     = var.environment
      REGION                  = var.aws_region
      GOLD_CONFIG_TABLE       = aws_dynamodb_table.gold_pipelines_config.name
      GOLD_JOB_NAME           = aws_glue_job.generic_gold_transform.name
      GOLD_RECONCILE_JOB_NAME = aws_glue_job.generic_gold_reconcile.name
    }
  }

  tags = local.common_tags
}

# ── EventBridge sweep rules (UIFPE-149) ─────────────────────────────────────
#
# TWO rules, not one per cadence. Before UIFPE-149 each cadence was its own rule
# and the payload named it, which meant every pipeline on a cadence shared one
# clock — two daily tables could not run at different times, because there was
# exactly one daily rule.
#
# Now each pipeline declares its own `schedule.frequency` + `schedule.start_time`
# in pipelines/base/<domain>.yaml, and the dispatcher works out which pipelines
# are due on each sweep. Scheduling became config-as-code, which is what lets the
# Transform UI set a table's schedule with no Terraform change and no manual step.
#
# The sweep MUST tick at least as often as the finest rung (gold-5m) or that rung
# could never be served. Both values are pinned to
# src/apps/scripts/gold_frequencies.py by qe/test_gold_frequency_parity.py —
# Terraform cannot import Python, so this is a checked copy, not a shared one.
#
# Cost of sweeping every 5 minutes regardless of what is registered: 288
# invocations/day of a 128 MB Lambda that scans one DynamoDB table. Pennies. It
# replaces ~628 invocations/day across the twelve per-cadence rules this design
# briefly had, so it is also cheaper than the alternative.
locals {
  gold_schedules = {
    tick = {
      cron        = "cron(0/5 * * * ? *)"
      description = "Gold transform sweep every 5 minutes — dispatches the pipelines whose slot has arrived (schedule.frequency + schedule.start_time per pipeline)"
      frequency   = "gold-tick"
    }
    # There is no second transform rule. The targeted-refresh sweep that used to
    # sit here was retired with UIFPE-457 (ADR-0020 decision 4): the periodic
    # recompute now rides the entity's own scheduled run once every N runs, so
    # it needs no rule, no payload and no second slot grid of its own. Removing
    # this key DESTROYS the rule and its target — intended, and the reason the
    # apply wants to land away from a cadence slot.
    reconcile = {
      cron        = "cron(30 3 * * ? *)"
      description = "Gold reconciliation, orphan hard-delete detection (03:30 UTC — 30 min after Silver reconcile)"
      frequency   = "gold-reconcile"
    }
  }
}

resource "aws_cloudwatch_event_rule" "gold_dispatch" {
  for_each = local.gold_schedules

  name                = "${local.name_prefix}-schedule-gold-dispatch-${each.key}"
  description         = each.value.description
  schedule_expression = each.value.cron
  state               = "ENABLED"

  tags = local.common_tags
}

resource "aws_cloudwatch_event_target" "gold_dispatch" {
  for_each = local.gold_schedules

  rule      = aws_cloudwatch_event_rule.gold_dispatch[each.key].name
  target_id = "GoldDispatch-${each.key}"
  arn       = aws_lambda_function.gold_dispatcher.arn
  input     = jsonencode({ frequency = each.value.frequency })
}

resource "aws_lambda_permission" "gold_dispatch" {
  for_each = local.gold_schedules

  statement_id  = "AllowEventBridge-${each.key}"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.gold_dispatcher.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.gold_dispatch[each.key].arn
}
