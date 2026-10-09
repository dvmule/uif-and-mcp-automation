# eventbridge_rules.tf
# One EventBridge rule per frequency group (30min / hourly / 4hr / daily / weekly).
# Each rule targets scheduler_dispatcher Lambda with a fixed frequency payload.
# Lambda permissions are in lambda_functions.tf.

# Every schedule rule takes its state from here: var.event_rule_state, unless
# var.event_rule_state_overrides names that rule. The override exists for ONE
# case -- a rule an environment runs ahead of its cutover (uat-uif.tfvars) --
# and variables.tf rejects unknown rule names, so a typo cannot silently fall
# back to the default. The two event-PATTERN rules below are not in this map:
# they are always ENABLED (see qe/pipeline/test_scheduler_infra.py).
locals {
  schedule_rule_state = {
    for rule in ["half_hourly", "hourly", "four_hourly", "daily_bronze_slot", "weekly", "reconcile_slot"] :
    rule => lookup(var.event_rule_state_overrides, rule, var.event_rule_state)
  }
}

# ── 30-minute rule ────────────────────────────────────────────────
resource "aws_cloudwatch_event_rule" "half_hourly" {
  name                = "${local.name_prefix}-schedule-bronze-ingest-30min"
  description         = "Triggers Bronze ingestion for all 30-minute pipelines"
  schedule_expression = "cron(0/30 * * * ? *)"
  state               = local.schedule_rule_state["half_hourly"]

  tags = {
    Project     = local.project_tag
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

resource "aws_cloudwatch_event_target" "half_hourly" {
  rule      = aws_cloudwatch_event_rule.half_hourly.name
  target_id = "Dispatch30Min"
  arn       = aws_lambda_function.scheduler_dispatcher.arn
  input     = jsonencode({ frequency = "30min" })
}

# ── Hourly rule ───────────────────────────────────────────────────
resource "aws_cloudwatch_event_rule" "hourly" {
  name                = "${local.name_prefix}-schedule-bronze-ingest-hourly"
  description         = "Triggers Bronze ingestion for all hourly pipelines"
  schedule_expression = "cron(0 * * * ? *)"
  state               = local.schedule_rule_state["hourly"]

  tags = {
    Project     = local.project_tag
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

resource "aws_cloudwatch_event_target" "hourly" {
  rule      = aws_cloudwatch_event_rule.hourly.name
  target_id = "DispatchHourly"
  arn       = aws_lambda_function.scheduler_dispatcher.arn
  input     = jsonencode({ frequency = "hourly" })
}

# ── 4-hour rule ───────────────────────────────────────────────────
resource "aws_cloudwatch_event_rule" "four_hourly" {
  name                = "${local.name_prefix}-schedule-bronze-ingest-4hr"
  description         = "Triggers Bronze ingestion for all 4-hour pipelines"
  schedule_expression = "cron(0 */4 * * ? *)"
  state               = local.schedule_rule_state["four_hourly"]

  tags = {
    Project     = local.project_tag
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

resource "aws_cloudwatch_event_target" "four_hourly" {
  rule      = aws_cloudwatch_event_rule.four_hourly.name
  target_id = "Dispatch4Hr"
  arn       = aws_lambda_function.scheduler_dispatcher.arn
  input     = jsonencode({ frequency = "4hr" })
}

# ── Daily Bronze slots (05:30–11:00 UTC, every 30 min) ─────────────
# Replaces the single daily rule. Each slot dispatches pipelines whose
# schedule.frequency=daily and schedule.daily_slot matches (missing → slot 0).

locals {
  daily_bronze_slot_crons = {
    "0"  = "cron(30 5 * * ? *)"  # 05:30 UTC
    "1"  = "cron(0 6 * * ? *)"   # 06:00 UTC
    "2"  = "cron(30 6 * * ? *)"  # 06:30 UTC
    "3"  = "cron(0 7 * * ? *)"   # 07:00 UTC
    "4"  = "cron(30 7 * * ? *)"  # 07:30 UTC
    "5"  = "cron(0 8 * * ? *)"   # 08:00 UTC
    "6"  = "cron(30 8 * * ? *)"  # 08:30 UTC
    "7"  = "cron(0 9 * * ? *)"   # 09:00 UTC
    "8"  = "cron(30 9 * * ? *)"  # 09:30 UTC
    "9"  = "cron(0 10 * * ? *)"  # 10:00 UTC
    "10" = "cron(30 10 * * ? *)" # 10:30 UTC
    "11" = "cron(0 11 * * ? *)"  # 11:00 UTC
  }

  # Silver reconcile: +30 min after each Bronze slot (06:00–11:30 UTC).
  reconcile_slot_crons = {
    "0"  = "cron(0 6 * * ? *)"   # 06:00 UTC
    "1"  = "cron(30 6 * * ? *)"  # 06:30 UTC
    "2"  = "cron(0 7 * * ? *)"   # 07:00 UTC
    "3"  = "cron(30 7 * * ? *)"  # 07:30 UTC
    "4"  = "cron(0 8 * * ? *)"   # 08:00 UTC
    "5"  = "cron(30 8 * * ? *)"  # 08:30 UTC
    "6"  = "cron(0 9 * * ? *)"   # 09:00 UTC
    "7"  = "cron(30 9 * * ? *)"  # 09:30 UTC
    "8"  = "cron(0 10 * * ? *)"  # 10:00 UTC
    "9"  = "cron(30 10 * * ? *)" # 10:30 UTC
    "10" = "cron(0 11 * * ? *)"  # 11:00 UTC
    "11" = "cron(30 11 * * ? *)" # 11:30 UTC
  }
}

resource "aws_cloudwatch_event_rule" "daily_bronze_slot" {
  for_each = local.daily_bronze_slot_crons

  name                = "${local.name_prefix}-schedule-bronze-ingest-daily-slot-${format("%02d", tonumber(each.key))}"
  description         = "Bronze daily slot ${each.key} (${each.value})"
  schedule_expression = each.value
  state               = local.schedule_rule_state["daily_bronze_slot"]

  tags = {
    Project     = local.project_tag
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

resource "aws_cloudwatch_event_target" "daily_bronze_slot" {
  for_each = local.daily_bronze_slot_crons

  rule      = aws_cloudwatch_event_rule.daily_bronze_slot[each.key].name
  target_id = "DispatchDailySlot${each.key}"
  arn       = aws_lambda_function.scheduler_dispatcher.arn
  input     = jsonencode({ frequency = "daily", daily_slot = tonumber(each.key) })
}

# ── Weekly rule ───────────────────────────────────────────────────
resource "aws_cloudwatch_event_rule" "weekly" {
  name                = "${local.name_prefix}-schedule-bronze-ingest-weekly"
  description         = "Triggers Bronze ingestion for all weekly pipelines"
  schedule_expression = "cron(0 6 ? * MON *)"
  state               = local.schedule_rule_state["weekly"]

  tags = {
    Project     = local.project_tag
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

resource "aws_cloudwatch_event_target" "weekly" {
  rule      = aws_cloudwatch_event_rule.weekly.name
  target_id = "DispatchWeekly"
  arn       = aws_lambda_function.scheduler_dispatcher.arn
  input     = jsonencode({ frequency = "weekly" })
}

# ── Silver reconcile slots (06:00–11:30 UTC, +30 min after Bronze) ─
# Each slot dispatches reconcile for daily pipelines whose schedule.daily_slot
# matches (missing daily_slot → slot 0 at 06:00 UTC).
resource "aws_cloudwatch_event_rule" "reconcile_slot" {
  for_each = local.reconcile_slot_crons

  name                = "${local.name_prefix}-schedule-silver-reconcile-daily-slot-${format("%02d", tonumber(each.key))}"
  description         = "Silver reconcile slot ${each.key} (${each.value})"
  schedule_expression = each.value
  state               = local.schedule_rule_state["reconcile_slot"]

  tags = {
    Project     = local.project_tag
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

resource "aws_cloudwatch_event_target" "reconcile_slot" {
  for_each = local.reconcile_slot_crons

  rule      = aws_cloudwatch_event_rule.reconcile_slot[each.key].name
  target_id = "DispatchReconcileSlot${each.key}"
  arn       = aws_lambda_function.scheduler_dispatcher.arn
  input     = jsonencode({ frequency = "reconcile", daily_slot = tonumber(each.key) })
}

# ── Bronze SUCCEEDED → gcs_replicator ─────────────────────────────
# Fires once per Bronze run that completes successfully. The replicator
# Lambda inspects the run's metrics record, skips pipelines without
# `replication.gcs.enabled=true`, and copies anything new in S3 to GCS.
# Pattern is scoped to the Bronze Glue job name so Silver/Gold/reconcile
# SUCCEEDED events never reach the replicator.

resource "aws_cloudwatch_event_rule" "bronze_succeeded_replicate" {
  name        = "${local.name_prefix}-on-bronze-succeeded-replicate-gcs"
  description = "On Bronze Glue SUCCEEDED, invoke gcs_replicator to mirror new files to GCS"
  state       = "ENABLED"

  event_pattern = jsonencode({
    source        = ["aws.glue"]
    "detail-type" = ["Glue Job State Change"]
    detail = {
      jobName = [aws_glue_job.generic_bronze_ingest.name]
      state   = ["SUCCEEDED"]
    }
  })

  tags = {
    Project     = local.project_tag
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

resource "aws_cloudwatch_event_target" "bronze_succeeded_replicate" {
  rule      = aws_cloudwatch_event_rule.bronze_succeeded_replicate.name
  target_id = "InvokeGcsReplicator"
  arn       = aws_lambda_function.gcs_replicator.arn
}

# ── Silver SUCCEEDED → silver_bq_replicator ───────────────────────
# Fires once per Silver Glue job that completes successfully. The
# replicator Lambda inspects the run's metrics row, skips pipelines without
# `replication.silver_bq.enabled=true`, mirrors the Iceberg snapshot to GCS
# (rewriting the metadata chain along the way), and refreshes the BigQuery
# external table pointer. Scoped to the Silver Glue job name so Bronze,
# Gold, and reconcile events never reach this Lambda.

resource "aws_cloudwatch_event_rule" "silver_succeeded_bq" {
  name        = "${local.name_prefix}-on-silver-succeeded-replicate-bq"
  description = "On Silver Glue SUCCEEDED, invoke silver_bq_replicator to mirror to GCS + refresh BQ external table"
  state       = "ENABLED"

  event_pattern = jsonencode({
    source        = ["aws.glue"]
    "detail-type" = ["Glue Job State Change"]
    detail = {
      jobName = [aws_glue_job.generic_silver_transform.name]
      state   = ["SUCCEEDED"]
    }
  })

  tags = {
    Project     = local.project_tag
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

resource "aws_cloudwatch_event_target" "silver_succeeded_bq" {
  rule      = aws_cloudwatch_event_rule.silver_succeeded_bq.name
  target_id = "InvokeSilverBqReplicator"
  arn       = aws_lambda_function.silver_bq_replicator.arn
}
