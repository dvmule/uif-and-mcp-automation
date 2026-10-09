# event-domain — everything a domain owns in the eventing plane, in one
# module instance (called FROM the domain's terraform, applied in the
# DOMAIN's state): its event bus, its delivery DLQ (+ optional depth alarm),
# the shared DLQ recorder's event source mapping on that DLQ, and the
# domain's data uploads (channel-gating mapping, optional bootstrap
# subscription seeds) into the stack's working prefix.
#
# The stack (stacks/event) is never edited to onboard a domain: its IAM and
# its Lambdas rely only on the naming conventions this module implements
# (bus uif-event-{env}-{domain}, DLQ uif-event-{env}-{domain}-dlq, uploads
# under event-pipeline/domains/{domain}/). The domain's remaining duties —
# its pipeline-configs seed row and its schemas — live beside the module
# call in the domain's own terraform.

data "aws_caller_identity" "current" {}

locals {
  prefix         = "uif-event-${var.environment}"
  working_prefix = "event-pipeline"
  domain_prefix  = "${local.working_prefix}/domains/${var.domain}"
}

# ── The domain event bus ─────────────────────────────────────────────────────
resource "aws_cloudwatch_event_bus" "this" {
  # uif-{stack}-{env}-{domain}: the name every stack component derives —
  # the publish job's PutEvents, the provisioner's rules, the rule ARNs in
  # subscription-configs.
  name = "${local.prefix}-${var.domain}"
  tags = var.tags
}

# ── Delivery DLQ ─────────────────────────────────────────────────────────────
# EventBridge dead-letters failed deliveries here; the SHARED DLQ recorder
# (stacks/event) consumes it through the mapping below.
resource "aws_sqs_queue" "dlq" {
  name                      = "${local.prefix}-${var.domain}-dlq"
  message_retention_seconds = 1209600 # 14 days (SQS maximum)
  # The recorder's event source mapping requires visibility timeout >= the
  # function timeout (60s); 6x is AWS's recommendation.
  visibility_timeout_seconds = 360
  sqs_managed_sse_enabled    = true
  tags                       = var.tags
}

# WITHOUT this policy EventBridge cannot write dead letters AT ALL: SQS
# denies service principals by default, EventBridge drops the event after
# logging only a metric (InvocationsFailedToBeSentToDlq), and the recorder
# never fires - exactly what the 2026-09-21 live test found. Scoped to rules
# on THIS domain's bus.
resource "aws_sqs_queue_policy" "dlq" {
  queue_url = aws_sqs_queue.dlq.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "AllowEventBridgeDeadLetters"
      Effect    = "Allow"
      Principal = { Service = "events.amazonaws.com" }
      Action    = "sqs:SendMessage"
      Resource  = aws_sqs_queue.dlq.arn
      Condition = {
        ArnLike = {
          "aws:SourceArn" = "arn:aws:events:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:rule/${aws_cloudwatch_event_bus.this.name}/*"
        }
      }
    }]
  })
}

data "aws_region" "current" {}

resource "aws_cloudwatch_metric_alarm" "dlq_depth" {
  count = var.enable_dlq_alarm ? 1 : 0

  alarm_name          = "${local.prefix}-${var.domain}-dlq-depth"
  alarm_description   = "Dead-letter queue has messages - investigate and redrive within 14 days."
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  dimensions = {
    QueueName = aws_sqs_queue.dlq.name
  }
  tags = var.tags
}

# ── Shared DLQ recorder ← this domain's DLQ ─────────────────────────────────
# The Lambda lives in the stack; the mapping lives HERE (domain state), so a
# new domain attaches itself. Looked up by its conventional name — never a
# cross-state output.
data "aws_lambda_function" "dlq_recorder" {
  function_name = "${local.prefix}-dlq-recorder"
}

resource "aws_lambda_event_source_mapping" "dlq_recorder" {
  event_source_arn                   = aws_sqs_queue.dlq.arn
  function_name                      = data.aws_lambda_function.dlq_recorder.arn
  batch_size                         = 10
  maximum_batching_window_in_seconds = 5
  function_response_types            = ["ReportBatchItemFailures"]
}

# ── Domain data uploads (into the stack's working prefix) ────────────────────
# The registered gold mapping IS the channel-gating rule; uploaded by apply
# so a mapping edit reaches the next run with no manual step. The stack's
# publish role reads event-pipeline/* — no per-domain grant.
resource "aws_s3_object" "mapping" {
  bucket = var.artifacts_bucket
  key    = "${local.domain_prefix}/${var.domain}.mapping.json"
  source = var.mapping_source
  etag   = filemd5(var.mapping_source)
}

# Optional bootstrap subscription seeds (JSON list of DynamoDB-typed items):
# inserted by the publish job only when subscription-configs holds no rows
# for the domain. Domain data — never baked into stack code.
resource "aws_s3_object" "subscription_seeds" {
  count = var.subscription_seeds_source != "" ? 1 : 0

  bucket = var.artifacts_bucket
  key    = "${local.domain_prefix}/${var.domain}.subscriptions.seed.json"
  source = var.subscription_seeds_source
  etag   = filemd5(var.subscription_seeds_source)
}
