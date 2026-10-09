resource "aws_cloudwatch_event_bus" "domain" {
  name = local.bus_name
}

# One rule per subscription (customer x detail-type) — platform policy kept.
resource "aws_cloudwatch_event_rule" "subscription" {
  for_each       = var.customers
  name           = "${var.prefix}-on-${var.domain}-${local.detail_type_dash}-${each.key}"
  event_bus_name = aws_cloudwatch_event_bus.domain.name

  event_pattern = jsonencode({
    "detail-type" = [var.detail_type]
  })
}

# ── The ONE target per rule: the shared delivery buffer ──────────────────────
# The input transformer stamps the matching rule's ARN onto the message — the
# Delivery Fabric's subscription identity, since a SHARED buffer's
# eventSourceARN cannot tell customers apart. Reserved-variable quoting rule:
# <aws.events.rule-arn> resolves to TEXT (quote it); <aws.events.event.json>
# resolves to JSON (leave it bare).
resource "aws_cloudwatch_event_target" "buffer" {
  for_each       = var.customers
  rule           = aws_cloudwatch_event_rule.subscription[each.key].name
  event_bus_name = aws_cloudwatch_event_bus.domain.name
  target_id      = "buffer-${each.key}"
  arn            = aws_sqs_queue.buffer.arn

  input_transformer {
    input_paths = {
      event = "$"
    }
    input_template = <<-EOT
      {"rule_arn": "<aws.events.rule-arn>", "event": <aws.events.event.json>}
    EOT
  }

  # EventBridge -> buffer is a same-account SQS delivery with an explicit
  # policy; failures here (the only EventBridge-delivered hop left) dead-letter
  # to the buffer DLQ, where the failed-writer records them.
  retry_policy {
    maximum_retry_attempts       = 3
    maximum_event_age_in_seconds = 900
  }
  dead_letter_config {
    arn = aws_sqs_queue.buffer_dlq.arn
  }
}
