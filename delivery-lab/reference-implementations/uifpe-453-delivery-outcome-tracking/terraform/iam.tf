data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

# ── EventBridge -> Kinesis (a stream has no resource policy, so a role) ─────
resource "aws_iam_role" "eventbridge_kinesis" {
  name = "${var.prefix}-eventbridge-kinesis-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "events.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "eventbridge_kinesis" {
  name = "${var.prefix}-eventbridge-kinesis-policy"
  role = aws_iam_role.eventbridge_kinesis.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["kinesis:PutRecord", "kinesis:PutRecords"]
      Resource = [for s in aws_kinesis_stream.subscription : s.arn]
    }]
  })
}

# ── Publisher ───────────────────────────────────────────────────────────────
resource "aws_iam_role" "publisher" {
  name               = "${var.prefix}-publisher-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "publisher_logs" {
  role       = aws_iam_role.publisher.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "publisher" {
  name = "${var.prefix}-publisher-policy"
  role = aws_iam_role.publisher.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = "events:PutEvents"
        Resource = aws_cloudwatch_event_bus.domain.arn
      },
      {
        Effect   = "Allow"
        Action   = ["dynamodb:PutItem", "dynamodb:UpdateItem"]
        Resource = aws_dynamodb_table.publish_metrics.arn
      },
    ]
  })
}

# ── Event handler (failures, from the consumer DLQs) ────────────────────────
resource "aws_iam_role" "event_handler" {
  name               = "${var.prefix}-event-handler-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "event_handler_logs" {
  role       = aws_iam_role.event_handler.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "event_handler" {
  name = "${var.prefix}-event-handler-policy"
  role = aws_iam_role.event_handler.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
        Resource = [aws_sqs_queue.domain_dlq.arn]
      },
      # GetItem on the BASE table is not belt-and-braces: production's
      # gsi_event_id does not project published_at, so the sort key can only be
      # derived by reading the base row.
      {
        Effect = "Allow"
        Action = ["dynamodb:Query", "dynamodb:GetItem"]
        Resource = [
          aws_dynamodb_table.publish_metrics.arn,
          "${aws_dynamodb_table.publish_metrics.arn}/index/*",
          aws_dynamodb_table.subscription_configs.arn,
          "${aws_dynamodb_table.subscription_configs.arn}/index/*",
        ]
      },
      {
        Effect   = "Allow"
        Action   = ["dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:GetItem"]
        Resource = aws_dynamodb_table.delivery_metrics.arn
      },
    ]
  })
}

# ── Delivery poller (successes, read back from the bus logs) ────────────────
resource "aws_iam_role" "delivery_poller" {
  name               = "${var.prefix}-delivery-poller-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "delivery_poller_logs" {
  role       = aws_iam_role.delivery_poller.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "delivery_poller" {
  name = "${var.prefix}-delivery-poller-policy"
  role = aws_iam_role.delivery_poller.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      # Reading the bus log group is now the success signal's transport, so
      # this single statement is what the whole design rests on. Scoped to the
      # one group: FilterLogEvents needs the ":*" suffix on the ARN, and
      # DescribeLogGroups is NOT required because the group name is passed in
      # by environment rather than discovered.
      {
        Effect   = "Allow"
        Action   = "logs:FilterLogEvents"
        Resource = "${aws_cloudwatch_log_group.bus.arn}:*"
      },
      {
        Effect = "Allow"
        Action = ["dynamodb:Query", "dynamodb:GetItem"]
        Resource = [
          aws_dynamodb_table.publish_metrics.arn,
          "${aws_dynamodb_table.publish_metrics.arn}/index/*",
          aws_dynamodb_table.subscription_configs.arn,
          "${aws_dynamodb_table.subscription_configs.arn}/index/*",
        ]
      },
      {
        Effect   = "Allow"
        Action   = ["dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:GetItem"]
        Resource = aws_dynamodb_table.delivery_metrics.arn
      },
      {
        Effect   = "Allow"
        Action   = ["dynamodb:GetItem", "dynamodb:PutItem"]
        Resource = aws_dynamodb_table.poller_state.arn
      },
    ]
  })
}
