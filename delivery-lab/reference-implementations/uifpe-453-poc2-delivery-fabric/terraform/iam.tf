# Per-function roles this time (review learning from PR #351: shared roles
# blur audit boundaries). Naming: -role / -policy suffixes per the standard.

locals {
  lambda_trust = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

# ── Delivery Fabric: reads buffer + routing + ledger, writes targets + outcomes ─
resource "aws_iam_role" "delivery_fabric" {
  name               = "${var.prefix}-delivery-fabric-role"
  assume_role_policy = local.lambda_trust
}

resource "aws_iam_role_policy_attachment" "delivery_fabric_logs" {
  role       = aws_iam_role.delivery_fabric.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "delivery_fabric" {
  name = "${var.prefix}-delivery-fabric-policy"
  role = aws_iam_role.delivery_fabric.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "BufferConsume"
        Effect   = "Allow"
        Action   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
        Resource = [aws_sqs_queue.buffer.arn]
      },
      {
        Sid      = "DeliverToConsumerQueues"
        Effect   = "Allow"
        Action   = ["sqs:SendMessage", "sqs:GetQueueUrl"]
        Resource = [for q in aws_sqs_queue.consumer : q.arn]
      },
      {
        Sid      = "DeliverToConsumerStreams"
        Effect   = "Allow"
        Action   = ["kinesis:PutRecord"]
        Resource = [for s in aws_kinesis_stream.consumer : s.arn]
      },
      {
        Sid    = "ReadRoutingAndLedger"
        Effect = "Allow"
        Action = ["dynamodb:GetItem", "dynamodb:Query"]
        Resource = [
          aws_dynamodb_table.subscription_configs.arn, "${aws_dynamodb_table.subscription_configs.arn}/index/*",
          aws_dynamodb_table.publish_metrics.arn, "${aws_dynamodb_table.publish_metrics.arn}/index/*",
        ]
      },
      {
        Sid      = "WriteOutcomes"
        Effect   = "Allow"
        Action   = ["dynamodb:PutItem", "dynamodb:UpdateItem"]
        Resource = [aws_dynamodb_table.delivery_metrics.arn]
      }
    ]
  })
}

# ── Failed writer: reads buffer DLQ + routing + ledger, writes outcomes ───────
resource "aws_iam_role" "failed_writer" {
  name               = "${var.prefix}-failed-writer-role"
  assume_role_policy = local.lambda_trust
}

resource "aws_iam_role_policy_attachment" "failed_writer_logs" {
  role       = aws_iam_role.failed_writer.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "failed_writer" {
  name = "${var.prefix}-failed-writer-policy"
  role = aws_iam_role.failed_writer.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "DlqConsume"
        Effect   = "Allow"
        Action   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
        Resource = [aws_sqs_queue.buffer_dlq.arn]
      },
      {
        Sid    = "ReadRoutingAndLedger"
        Effect = "Allow"
        Action = ["dynamodb:GetItem", "dynamodb:Query"]
        Resource = [
          aws_dynamodb_table.subscription_configs.arn, "${aws_dynamodb_table.subscription_configs.arn}/index/*",
          aws_dynamodb_table.publish_metrics.arn, "${aws_dynamodb_table.publish_metrics.arn}/index/*",
        ]
      },
      {
        Sid      = "WriteOutcomes"
        Effect   = "Allow"
        Action   = ["dynamodb:PutItem", "dynamodb:UpdateItem"]
        Resource = [aws_dynamodb_table.delivery_metrics.arn]
      }
    ]
  })
}

# ── Test publisher: PutEvents + ledger writes (console-invokable) ─────────────
resource "aws_iam_role" "test_publisher" {
  name               = "${var.prefix}-test-publisher-role"
  assume_role_policy = local.lambda_trust
}

resource "aws_iam_role_policy_attachment" "test_publisher_logs" {
  role       = aws_iam_role.test_publisher.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "test_publisher" {
  name = "${var.prefix}-test-publisher-policy"
  role = aws_iam_role.test_publisher.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "Publish"
        Effect   = "Allow"
        Action   = ["events:PutEvents"]
        Resource = [aws_cloudwatch_event_bus.domain.arn]
      },
      {
        Sid      = "WriteLedger"
        Effect   = "Allow"
        Action   = ["dynamodb:PutItem"]
        Resource = [aws_dynamodb_table.publish_metrics.arn]
      }
    ]
  })
}
