# The DLQ recorder - failed subscriber deliveries (any domain) become
# delivery-metrics rows. The Lambda is shared; each domain's event source
# mapping on its own DLQ is created by ITS event-domain module instance.
# Moved from domains/product (2026-09-21); adopted via imports.tf.

# ── DLQ recorder: uif-event-<env>-dlq-recorder ───────────────────────────────
# Consumes the pipeline DLQ and records each failed SUBSCRIBER DELIVERY as a
# FAILED row in delivery-metrics (Table 4) - idempotent on the EventBridge
# EventId, payload copied in at DLQ time, lineage from publish-metrics,
# customer resolved from subscription-configs. Contract:
# ../../src/events/docs/delivery-metrics.md. Non-delivery DLQ messages (the
# trigger Lambda's own async dead letters carry no RULE_ARN) are logged in
# full and dropped - Table 4 is delivery-only by policy.

resource "aws_iam_role" "dlq_recorder" {
  name = "${local.event_stack_prefix}-dlq-recorder-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy" "dlq_recorder" {
  name = "${local.event_stack_prefix}-dlq-recorder-policy"
  role = aws_iam_role.dlq_recorder.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:*"
      },
      {
        # The event sources: EVERY domain's delivery DLQ by naming
        # convention (uif-event-{env}-{domain}-dlq, event-domain module).
        # The per-domain event source mapping is created by the module -
        # a new domain plugs in with no change here.
        Effect   = "Allow"
        Action   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
        Resource = "arn:aws:sqs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:${local.event_stack_prefix}-*-dlq"
      },
      {
        # Table 4: the ONLY table this Lambda writes. Query = the
        # gsi_event_id idempotency check.
        Effect = "Allow"
        Action = ["dynamodb:PutItem", "dynamodb:Query"]
        Resource = [
          aws_dynamodb_table.delivery_metrics.arn,
          "${aws_dynamodb_table.delivery_metrics.arn}/index/*",
        ]
      },
      {
        # Table 3, read-only per contract: gsi_event_id lineage + the
        # base-table payload read (payload is excluded from projections).
        Effect = "Allow"
        Action = ["dynamodb:GetItem", "dynamodb:Query"]
        Resource = [
          aws_dynamodb_table.glue_metrics.arn,
          "${aws_dynamodb_table.glue_metrics.arn}/index/*",
        ]
      },
      {
        # Table 5, read-only: RULE_ARN / TARGET_ARN -> customer.
        Effect = "Allow"
        Action = ["dynamodb:Query"]
        Resource = [
          aws_dynamodb_table.subscription_configs.arn,
          "${aws_dynamodb_table.subscription_configs.arn}/index/*",
        ]
      },
    ]
  })
}

resource "aws_lambda_function" "dlq_recorder" {
  function_name = "${local.event_stack_prefix}-dlq-recorder"
  description   = "Records failed subscriber deliveries from the pipeline DLQ into delivery-metrics (Table 4) - idempotent, payload copied in, lineage from publish-metrics."
  role          = aws_iam_role.dlq_recorder.arn
  runtime       = "python3.13"
  handler       = "dlq_recorder.lambda_handler"
  timeout       = 60
  # Code from the release-artifacts bucket at its content-addressed key
  # (locals.tf, release.tf) -- never zipped from the repo at plan time.
  s3_bucket        = aws_s3_bucket.release_artifacts.id
  s3_key           = local.lambda_s3_key["dlq_recorder"]
  source_code_hash = lookup(var.lambda_code_hashes, "dlq_recorder", null)

  environment {
    variables = {
      DELIVERY_METRICS_TABLE     = aws_dynamodb_table.delivery_metrics.name
      PUBLISH_METRICS_TABLE      = aws_dynamodb_table.glue_metrics.name
      SUBSCRIPTION_CONFIGS_TABLE = aws_dynamodb_table.subscription_configs.name
    }
  }

  tags = local.common_tags
}
