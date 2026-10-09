# ─────────────────────────────────────────────────────────────────────────────
# The bus, its logs, and one rule per subscription.
#
# The success signal this POC tests is EventBridge's own event-bus logging
# (GA 2025-07): at INFO the bus emits one INVOCATION_SUCCESS record per
# (event x rule x target), carrying event_id, details.rule_arn,
# details.target_arn and — with include_detail = FULL — details.target_input.
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_cloudwatch_event_bus" "domain" {
  name = local.bus_name

  log_config {
    level          = var.bus_log_level
    include_detail = var.bus_log_include_detail
  }
}

resource "aws_cloudwatch_log_group" "bus" {
  name              = local.bus_log_name
  retention_in_days = var.bus_log_retention_days
}

# ── Vended log delivery: bus -> CloudWatch Logs ─────────────────────────────
# Event-bus logs are a VENDED log source, so the wiring is the three-resource
# delivery_source / delivery_destination / delivery triple rather than anything
# on the bus itself. The bus's log_config decides WHAT is emitted; these decide
# WHERE it lands.
resource "aws_cloudwatch_log_delivery_source" "bus" {
  name         = "${var.prefix}-bus-logs"
  log_type     = "INFO_LOGS"
  resource_arn = aws_cloudwatch_event_bus.domain.arn
}

resource "aws_cloudwatch_log_delivery_destination" "bus" {
  name          = "${var.prefix}-bus-logs-cwl"
  output_format = "json"

  delivery_destination_configuration {
    destination_resource_arn = aws_cloudwatch_log_group.bus.arn
  }
}

resource "aws_cloudwatch_log_delivery" "bus" {
  delivery_source_name     = aws_cloudwatch_log_delivery_source.bus.name
  delivery_destination_arn = aws_cloudwatch_log_delivery_destination.bus.arn
}

# ── One rule per subscription (customer x detail-type) ──────────────────────
resource "aws_cloudwatch_event_rule" "subscription" {
  for_each       = var.customers
  name           = "${var.prefix}-on-${var.domain}-${local.detail_type_dash}-${each.key}"
  event_bus_name = aws_cloudwatch_event_bus.domain.name

  # detail-type alone by default — matching the observed behaviour, where ONE
  # bus entry (channel "event") fans out to both the SQS and the Kinesis
  # subscriber. subscription_provisioner.py:313 adds detail.channel as well;
  # set var.rule_channel_filter to model that, but note it only makes sense
  # alongside a publish_channels list that actually emits each subscriber's
  # channel, or non-SQS subscribers match nothing. See variables.tf.
  event_pattern = jsonencode(merge(
    { "detail-type" = [var.detail_type] },
    var.rule_channel_filter ? {
      detail = { channel = [local.target_type_channel[each.value.target_type]] }
    } : {},
  ))
}

# ── The delivery target: SQS ────────────────────────────────────────────────
# input_path = "$.detail" matches the real stack and is load-bearing three
# times over: the consumer receives the CloudEvents envelope (not the bus
# wrapper), a dead letter therefore carries the envelope — whose `id` is the
# event_id ledgered in Table 3 — and the bus log's target_input is that same
# envelope, which is what lets the delivery-poller join a success record back
# to the ledger with no extra column. The previous POC omitted it, received the
# bus wrapper, and ended up correlating on the bus-assigned EventId instead.
resource "aws_cloudwatch_event_target" "real_sqs" {
  for_each       = local.sqs_customers
  rule           = aws_cloudwatch_event_rule.subscription[each.key].name
  event_bus_name = aws_cloudwatch_event_bus.domain.name
  target_id      = "real-${each.key}"
  arn            = aws_sqs_queue.subscription[each.key].arn
  input_path     = "$.detail"

  retry_policy {
    maximum_retry_attempts       = var.retry_max_attempts
    maximum_event_age_in_seconds = var.retry_max_age_seconds
  }
  # THE DOMAIN DLQ, not a per-subscription one. Pointing a rule at the
  # per-subscription queue is the 2026-09-21 bug � nothing consumes it, so
  # failed deliveries go invisible. See sqs_kinesis.tf.
  dead_letter_config {
    arn = aws_sqs_queue.domain_dlq.arn
  }
}

# ── The delivery target: Kinesis (needs a role; no resource policy) ─────────
# VERIFY ON THE FIRST LIVE RUN: partition_key_path is a JSONPath over the
# event, and it is not documented whether input_path shifts the base it is
# evaluated against. If Kinesis delivery fails or lands everything on one
# shard, try "$.partitionkey" instead. The envelope always carries
# partitionkey ({product_id}|{routing_attribute}) as the real processor sets it.
resource "aws_cloudwatch_event_target" "real_kinesis" {
  for_each       = local.kinesis_customers
  rule           = aws_cloudwatch_event_rule.subscription[each.key].name
  event_bus_name = aws_cloudwatch_event_bus.domain.name
  target_id      = "real-${each.key}"
  arn            = aws_kinesis_stream.subscription[each.key].arn
  role_arn       = aws_iam_role.eventbridge_kinesis.arn
  input_path     = "$.detail"

  kinesis_target {
    partition_key_path = "$.detail.partitionkey"
  }
  retry_policy {
    maximum_retry_attempts       = var.retry_max_attempts
    maximum_event_age_in_seconds = var.retry_max_age_seconds
  }
  # THE DOMAIN DLQ, not a per-subscription one. Pointing a rule at the
  # per-subscription queue is the 2026-09-21 bug � nothing consumes it, so
  # failed deliveries go invisible. See sqs_kinesis.tf.
  dead_letter_config {
    arn = aws_sqs_queue.domain_dlq.arn
  }
}

# ── Bus logs -> delivery-poller, on a schedule ──────────────────────────────
# REPLACES aws_cloudwatch_log_subscription_filter + aws_lambda_permission for
# logs.amazonaws.com. Both are gone deliberately; the two mechanisms must not
# run together, because they would write the same rows from the same records
# and any divergence between them would be invisible (same key, same values,
# last writer wins).
#
# What the measurement said, 2026-09-30, 10,000 events x 2 consumers:
#   push  — 7,666 invocations for 20,020 records, average batch 2.6, max 13
#   poll  — one invocation per window, whatever the volume
# A subscription filter flushes as records arrive rather than accumulating, so
# the batching that would have made push viable never materialises: at a
# 1M-event run x 10 consumers it extrapolates to ~3.85M invocations.
#
# The decisive difference is not cost though. The filter was push-only, so a
# log record that never arrived was never asked for again. The poller reads by
# timestamp, so rewinding the watermark (or passing an explicit window) re-reads
# and re-writes idempotently. Retention on the bus log group is therefore the
# recovery horizon — see var.bus_log_retention_days.
#
# A scheduled rule must live on the DEFAULT bus, not the domain bus.
resource "aws_cloudwatch_event_rule" "poll_schedule" {
  name                = "${var.prefix}-delivery-poll"
  description         = "Read the bus log group's INVOCATION_* records into delivery-metrics"
  schedule_expression = var.poll_rate
}

resource "aws_cloudwatch_event_target" "poll_schedule" {
  rule      = aws_cloudwatch_event_rule.poll_schedule.name
  target_id = "delivery-poller"
  arn       = aws_lambda_function.delivery_poller.arn
}

# ── Failure counts, without the poller reading them ─────────────────────────
# The poller's filter is INVOCATION_SUCCESS only, so it no longer counts
# failure records. These replace that corroboration more cheaply: the count is
# computed by CloudWatch at ingestion with no Lambda involved, it is alarmable,
# and it keeps working when the poller is not running.
#
# Why it mattered: the 2026-09-30 reconciliation only worked because failures
# were counted — 19,588 success + 432 failure = 20,020 records received, which
# is what proved nothing had been lost in the log-delivery hop. Keep that
# arithmetic available or a future shortfall is ambiguous all over again.
#
# Cost: no charge for the filter; you pay for the custom metric itself.
resource "aws_cloudwatch_log_metric_filter" "bus_invocation_failure" {
  name           = "${var.prefix}-invocation-failure"
  log_group_name = aws_cloudwatch_log_group.bus.name
  pattern        = "\"INVOCATION_FAILURE\""

  metric_transformation {
    name      = "BusInvocationFailure"
    namespace = "UIF/Eventing/${var.prefix}"
    value     = "1"
    unit      = "Count"
    # So a window with no failures reads as 0 rather than as missing data,
    # which is what makes an alarm on this usable.
    default_value = 0
  }
}

resource "aws_cloudwatch_log_metric_filter" "bus_invocation_dlq" {
  name           = "${var.prefix}-invocation-dlq"
  log_group_name = aws_cloudwatch_log_group.bus.name
  pattern        = "\"INVOCATION_DLQ\""

  metric_transformation {
    name          = "BusInvocationDlq"
    namespace     = "UIF/Eventing/${var.prefix}"
    value         = "1"
    unit          = "Count"
    default_value = 0
  }
}

resource "aws_cloudwatch_log_metric_filter" "bus_invocation_success" {
  name           = "${var.prefix}-invocation-success"
  log_group_name = aws_cloudwatch_log_group.bus.name
  pattern        = "\"INVOCATION_SUCCESS\""

  metric_transformation {
    name          = "BusInvocationSuccess"
    namespace     = "UIF/Eventing/${var.prefix}"
    value         = "1"
    unit          = "Count"
    default_value = 0
  }
}

resource "aws_lambda_permission" "poll_schedule" {
  statement_id  = "AllowExecutionFromEventBridgeSchedule"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.delivery_poller.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.poll_schedule.arn
}
