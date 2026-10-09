# ─────────────────────────────────────────────────────────────────────────────
# THE DOMAIN DLQ — one per bus. The AUTHORITATIVE failure signal.
#
# CORRECTED 2026-10-01. This POC previously gave every customer its own DLQ and
# pointed both the rule's dead_letter_config AND the subscription queue's
# redrive_policy at it. Production does neither:
#
#   * every rule's DeadLetterConfig points at ONE domain DLQ
#     (subscription_provisioner.py:337, _bus_dlq_arn(bus)), which the shared
#     dlq-recorder consumes via an ESM created in the event-domain module
#     (modules/event-domain/main.tf:99)
#   * the per-subscription "-dlq" is the subscription QUEUE's RedrivePolicy
#     target — consumer-side poison — and nothing reads it
#
# Pointing a rule at the per-subscription queue was a REAL BUG, found live on
# 2026-09-21: failed deliveries landed somewhere nothing consumed and were
# invisible. The old POC shape reproduced the bug's topology, and needed a
# failure_origin attribute to tell the two kinds of failure apart — a problem
# production does not have, because consumer poison never reaches this queue.
#
# The grant is ArnLike over rule/{bus}/* rather than one statement per rule:
# subscriptions are created by the provisioner at runtime, so the policy cannot
# enumerate them. Matches the module.
# ─────────────────────────────────────────────────────────────────────────────
resource "aws_sqs_queue" "domain_dlq" {
  name                       = "${var.prefix}-${var.domain}-dlq"
  sqs_managed_sse_enabled    = true
  message_retention_seconds  = 1209600 # 14 days
  visibility_timeout_seconds = 360     # >= 6x event_handler's 60s timeout, per AWS guidance
}

resource "aws_sqs_queue_policy" "domain_dlq" {
  queue_url = aws_sqs_queue.domain_dlq.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "AllowEventBridgeDeadLetter"
      Effect    = "Allow"
      Principal = { Service = "events.amazonaws.com" }
      Action    = "sqs:SendMessage"
      Resource  = aws_sqs_queue.domain_dlq.arn
      Condition = { ArnLike = {
        "aws:SourceArn" = "arn:aws:events:${var.region}:${data.aws_caller_identity.current.account_id}:rule/${aws_cloudwatch_event_bus.domain.name}/*"
      } }
    }]
  })
}

# ── Per-subscription DLQ — deliberately UNREAD ──────────────────────────────
# The subscription queue's RedrivePolicy target: consumer-side poison
# messages, the consumer's problem. Created because production creates it, and
# left unattached because production leaves it unattached. Do NOT wire an ESM
# here "to be safe" — that is how the two failure kinds got conflated before,
# and delivery failures belong on the domain DLQ.
resource "aws_sqs_queue" "subscription_dlq" {
  for_each                   = local.sqs_customers
  name                       = "${var.prefix}-${each.key}-${var.domain}-${local.detail_type_dash}-dlq"
  sqs_managed_sse_enabled    = true
  message_retention_seconds  = 1209600 # 14 days
  visibility_timeout_seconds = 360
}

data "aws_caller_identity" "current" {}

# ── SQS subscription queues (pmc) ───────────────────────────────────────────
resource "aws_sqs_queue" "subscription" {
  for_each                   = local.sqs_customers
  name                       = "${var.prefix}-${each.key}-${var.domain}-${local.detail_type_dash}"
  sqs_managed_sse_enabled    = true
  message_retention_seconds  = 345600 # 4 days
  visibility_timeout_seconds = 60

  # Consumer-side failures drain into the SAME DLQ as EventBridge delivery
  # failures — the shared-DLQ situation failure_origin disambiguates.
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.subscription_dlq[each.key].arn
    maxReceiveCount     = 5
  })
}

resource "aws_sqs_queue_policy" "subscription" {
  for_each  = local.sqs_customers
  queue_url = aws_sqs_queue.subscription[each.key].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "AllowEventBridgeDelivery"
      Effect    = "Allow"
      Principal = { Service = "events.amazonaws.com" }
      Action    = "sqs:SendMessage"
      Resource  = aws_sqs_queue.subscription[each.key].arn
      Condition = { ArnEquals = { "aws:SourceArn" = aws_cloudwatch_event_rule.subscription[each.key].arn } }
    }]
  })
}

# ── Kinesis subscription streams (pdz) ──────────────────────────────────────
resource "aws_kinesis_stream" "subscription" {
  for_each = local.kinesis_customers
  name     = "${var.prefix}-${each.key}-${var.domain}-${local.detail_type_dash}"

  stream_mode_details {
    stream_mode = "ON_DEMAND"
  }
  retention_period = 24

  encryption_type = "KMS"
  kms_key_id      = "alias/aws/kinesis"
}

# ── No delivery-writer DLQ any more, and that is an improvement ─────────────
# It existed because a CloudWatch Logs subscription filter invokes Lambda
# ASYNCHRONOUSLY: a batch the writer could not process was retried twice and
# then discarded, so it needed somewhere to land. The scheduled poller has no
# async invocation and needs no such queue — a run that fails commits no
# watermark, so the next run re-reads the identical window. The retry is the
# schedule, and it is durable.
#
# What has NOT changed: the EventBridge -> Logs hop is best-effort by AWS's own
# statement, and no Lambda configuration ever fixed that. What changed is that
# a record lost on that hop is now recoverable by re-reading the window while
# it is still within bus log retention, rather than being undetectable.
