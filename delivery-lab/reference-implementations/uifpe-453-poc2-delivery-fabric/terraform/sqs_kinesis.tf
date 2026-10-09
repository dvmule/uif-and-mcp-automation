# ═══ The shared delivery buffer — the shock absorber ═════════════════════════
# ONE queue for all subscriptions (tiered design: a heavy/slow subscription can
# graduate to a dedicated buffer later by re-targeting its rule — no code change).
resource "aws_sqs_queue" "buffer" {
  name                       = "${var.prefix}-delivery-buffer"
  sqs_managed_sse_enabled    = true
  message_retention_seconds  = 345600 # 4 days
  visibility_timeout_seconds = 360    # >= 6x the fabric Lambda's 60s timeout

  # Delivery retry semantics now live HERE, not in EventBridge: a failed
  # delivery attempt returns via batchItemFailures, redelivers with the
  # visibility backoff, and parks in the DLQ after 5 strikes.
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.buffer_dlq.arn
    maxReceiveCount     = 5
  })
}

resource "aws_sqs_queue" "buffer_dlq" {
  name                       = "${var.prefix}-delivery-buffer-dlq"
  sqs_managed_sse_enabled    = true
  message_retention_seconds  = 1209600 # 14 days
  visibility_timeout_seconds = 360     # >= failed-writer Lambda timeout
}

resource "aws_sqs_queue_policy" "buffer" {
  queue_url = aws_sqs_queue.buffer.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "AllowEventBridgeDelivery"
      Effect    = "Allow"
      Principal = { Service = "events.amazonaws.com" }
      Action    = "sqs:SendMessage"
      Resource  = aws_sqs_queue.buffer.arn
      Condition = { ArnEquals = { "aws:SourceArn" = [for r in aws_cloudwatch_event_rule.subscription : r.arn] } }
    }]
  })
}

resource "aws_sqs_queue_policy" "buffer_dlq" {
  queue_url = aws_sqs_queue.buffer_dlq.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "AllowEventBridgeDeadLetter"
      Effect    = "Allow"
      Principal = { Service = "events.amazonaws.com" }
      Action    = "sqs:SendMessage"
      Resource  = aws_sqs_queue.buffer_dlq.arn
      Condition = { ArnEquals = { "aws:SourceArn" = [for r in aws_cloudwatch_event_rule.subscription : r.arn] } }
    }]
  })
}

# ═══ Consumer targets — written to by the Delivery Fabric Lambda, not EventBridge ═══

resource "aws_sqs_queue" "consumer" {
  for_each                   = local.sqs_customers
  name                       = "${var.prefix}-${each.key}-${var.domain}-${local.detail_type_dash}"
  sqs_managed_sse_enabled    = true
  message_retention_seconds  = 345600
  visibility_timeout_seconds = 60
}

# The delivery grant goes to the FABRIC's role, not events.amazonaws.com —
# the queue-policy statement mirrors the production cross-account contract
# even though same-account IAM would technically suffice.
resource "aws_sqs_queue_policy" "consumer" {
  for_each  = local.sqs_customers
  queue_url = aws_sqs_queue.consumer[each.key].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "AllowDeliveryFabric"
      Effect    = "Allow"
      Principal = { AWS = aws_iam_role.delivery_fabric.arn }
      Action    = "sqs:SendMessage"
      Resource  = aws_sqs_queue.consumer[each.key].arn
    }]
  })
}

resource "aws_kinesis_stream" "consumer" {
  for_each = local.kinesis_customers
  name     = "${var.prefix}-${each.key}-${var.domain}-${local.detail_type_dash}"

  stream_mode_details {
    stream_mode = "ON_DEMAND"
  }
  retention_period = 24

  encryption_type = "KMS"
  kms_key_id      = "alias/aws/kinesis"
}
