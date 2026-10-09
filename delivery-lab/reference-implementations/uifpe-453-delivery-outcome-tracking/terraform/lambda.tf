locals {
  lambda_env = {
    PREFIX           = var.prefix
    DOMAIN           = var.domain
    DETAIL_TYPE      = var.detail_type
    T_PUBLISH        = aws_dynamodb_table.publish_metrics.name
    T_DELIVERY       = aws_dynamodb_table.delivery_metrics.name
    T_SUBSCRIPTIONS  = aws_dynamodb_table.subscription_configs.name
    BUS_NAME         = aws_cloudwatch_event_bus.domain.name
    PUBLISH_CHANNELS = join(",", var.publish_channels)
  }

  poller_env = merge(local.lambda_env, {
    BUS_LOG_GROUP           = aws_cloudwatch_log_group.bus.name
    T_STATE                 = aws_dynamodb_table.poller_state.name
    POLL_LAG_SECONDS        = tostring(var.poll_lag_seconds)
    POLL_OVERLAP_SECONDS    = tostring(var.poll_overlap_seconds)
    POLL_MAX_WINDOW_SECONDS = tostring(var.poll_max_window_seconds)
    # NOT passed before 2026-10-01, so the Lambda silently used the module
    # default of 3600 — four times max_window, which capped the very first
    # window and started the watermark behind the lag floor.
    POLL_COLD_START_LOOKBACK_SECONDS = tostring(var.poll_cold_start_lookback_seconds)
    POLL_RUN_HISTORY_DAYS            = tostring(var.poll_run_history_days)
    POLL_CONCURRENCY                 = tostring(var.poll_concurrency)
    DDB_POOL                         = tostring(var.poll_concurrency + 8)
  })

  functions = ["publisher", "event-handler", "delivery-poller"]
}

# One zip for all three handlers: they share outcome_row.py, which is the
# mechanism that stops the two writers deriving the sort key differently — the
# drift that produced duplicate rows in the previous POC.
data "archive_file" "src" {
  type        = "zip"
  source_dir  = "${path.module}/lambda_src"
  output_path = "${path.module}/.build/lambda_src.zip"
  excludes    = ["__pycache__"]
}

resource "aws_cloudwatch_log_group" "lambda" {
  for_each          = toset(local.functions)
  name              = "/aws/lambda/${var.prefix}-${each.key}"
  retention_in_days = 14
}

# ── Publisher ───────────────────────────────────────────────────────────────
# Mimics the PUBLISH CONTRACT of stacks/event/src/glue/event_fabric_processor.py
# — CloudEvents envelope, PutEvents in batches of 10, ledger row keyed on
# publish_id with event_id = the ENVELOPE id. It is NOT a Glue job and does not
# reproduce the Athena delta read, the run registry, channel projection or the
# gzip'd replay payload.
resource "aws_lambda_function" "publisher" {
  function_name    = "${var.prefix}-publisher"
  role             = aws_iam_role.publisher.arn
  handler          = "publisher.handler"
  runtime          = "python3.12"
  timeout          = 120
  memory_size      = 512
  filename         = data.archive_file.src.output_path
  source_code_hash = data.archive_file.src.output_base64sha256
  environment { variables = local.lambda_env }
  depends_on = [aws_cloudwatch_log_group.lambda]
}

# ── Event handler: consumer DLQs -> FAILED rows ─────────────────────────────
resource "aws_lambda_function" "event_handler" {
  function_name    = "${var.prefix}-event-handler"
  role             = aws_iam_role.event_handler.arn
  handler          = "event_handler.handler"
  runtime          = "python3.12"
  timeout          = 60
  memory_size      = 256
  filename         = data.archive_file.src.output_path
  source_code_hash = data.archive_file.src.output_base64sha256
  environment { variables = local.lambda_env }
  depends_on = [aws_cloudwatch_log_group.lambda]
}

# ONE mapping, on the DOMAIN DLQ — matching modules/event-domain/main.tf:99,
# where production attaches the shared dlq-recorder to each domain's single
# DLQ. The previous per-customer fan-out here modelled a topology production
# does not have, and made the POC unable to exercise the real one: dead letters
# from every customer and every detail-type arriving interleaved on one queue.
resource "aws_lambda_event_source_mapping" "dlq" {
  event_source_arn                   = aws_sqs_queue.domain_dlq.arn
  function_name                      = aws_lambda_function.event_handler.arn
  batch_size                         = 10
  maximum_batching_window_in_seconds = 5
  function_response_types            = ["ReportBatchItemFailures"]
}

# ── Delivery poller: bus logs -> DELIVERED rows ─────────────────────────────
# Invoked on a SCHEDULE (see eventbridge.tf), not by a subscription filter.
# Why, in one line: the filter delivered an average of 2.6 records per
# invocation at 10k events, and could never re-read a record it had lost.
#
# reserved_concurrent_executions = 1 is the concurrency control for the
# watermark. Two overlapping runs would read the same window and race the
# cursor; a single reserved slot makes that unconstructible, which is cheaper
# than a conditional-update protocol and costs nothing — the poller is one
# invocation every 15 minutes.
resource "aws_lambda_function" "delivery_poller" {
  function_name                  = "${var.prefix}-delivery-poller"
  role                           = aws_iam_role.delivery_poller.arn
  handler                        = "delivery_poller.handler"
  runtime                        = "python3.12"
  timeout                        = var.poll_timeout_seconds
  memory_size                    = var.poll_memory_mb
  reserved_concurrent_executions = 1
  filename                       = data.archive_file.src.output_path
  source_code_hash               = data.archive_file.src.output_base64sha256
  environment { variables = local.poller_env }
  depends_on = [aws_cloudwatch_log_group.lambda]
}

# ── Per-run counts as CloudWatch metrics ────────────────────────────────────
# The poller prints one JSON report per run, so the numbers are already in its
# log group — but a log line is not alarmable. These metric filters lift
# `records` and `written` out of that line at ingestion, with no code in the
# Lambda and nothing to maintain.
#
# The JSON selector only matches the REPORT line: the per-record error lines
# the poller also prints carry no `records`/`written` field.
#
# What to alarm on:
#   * records - written > 0 sustained  (metric math) — reading but not writing
#   * unaccounted > 0                  — a record is being dropped outright
#   * records = 0 while events flow    — usually a broken filter pattern
locals {
  poller_metric_namespace = "UIF/Eventing/${var.prefix}"
}

resource "aws_cloudwatch_log_metric_filter" "poller_records" {
  name           = "${var.prefix}-poller-records"
  log_group_name = aws_cloudwatch_log_group.lambda["delivery-poller"].name
  pattern        = "{ $.log_records_read = * }"

  metric_transformation {
    name          = "PollerLogRecordsRead"
    namespace     = local.poller_metric_namespace
    value         = "$.log_records_read"
    unit          = "Count"
    default_value = 0
  }
}

resource "aws_cloudwatch_log_metric_filter" "poller_written" {
  name           = "${var.prefix}-poller-written"
  log_group_name = aws_cloudwatch_log_group.lambda["delivery-poller"].name
  pattern        = "{ $.rows_written = * }"

  metric_transformation {
    name          = "PollerRowsWritten"
    namespace     = local.poller_metric_namespace
    value         = "$.rows_written"
    unit          = "Count"
    default_value = 0
  }
}

# Should never fire. If it does, a record was read and silently discarded.
resource "aws_cloudwatch_log_metric_filter" "poller_unaccounted" {
  name           = "${var.prefix}-poller-unaccounted"
  log_group_name = aws_cloudwatch_log_group.lambda["delivery-poller"].name
  pattern        = "{ $.unaccounted = * }"

  metric_transformation {
    name          = "PollerUnaccountedRecords"
    namespace     = local.poller_metric_namespace
    value         = "$.unaccounted"
    unit          = "Count"
    default_value = 0
  }
}

# A run that did not finish its window commits nothing, so the schedule retries
# it. Harmless once; sustained, it means the window is too big for one run.
resource "aws_cloudwatch_log_metric_filter" "poller_incomplete" {
  name           = "${var.prefix}-poller-incomplete"
  log_group_name = aws_cloudwatch_log_group.lambda["delivery-poller"].name
  pattern        = "{ $.action_required = * }"

  metric_transformation {
    name          = "PollerIncompleteRuns"
    namespace     = local.poller_metric_namespace
    value         = "1"
    unit          = "Count"
    default_value = 0
  }
}

# No on-failure destination, and that is the point rather than an omission. A
# failed run commits no watermark, so the next scheduled run re-reads exactly
# the same window — the retry is the schedule itself, and it is durable in a
# way an async-invoke DLQ never was. The only thing that can still lose a row
# is the log record never being ingested at all, which POLL_LAG_SECONDS and a
# manual window re-run exist to cover.
