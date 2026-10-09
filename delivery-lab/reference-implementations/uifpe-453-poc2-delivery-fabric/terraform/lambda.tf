locals {
  lambda_env = {
    T_PUBLISH       = aws_dynamodb_table.publish_metrics.name
    T_DELIVERY      = aws_dynamodb_table.delivery_metrics.name
    T_SUBSCRIPTIONS = aws_dynamodb_table.subscription_configs.name
    DOMAIN          = var.domain
    PREFIX          = var.prefix
    BUS_NAME        = aws_cloudwatch_event_bus.domain.name
    DETAIL_TYPE     = var.detail_type
  }
}

resource "aws_cloudwatch_log_group" "lambda" {
  for_each          = toset(["delivery-fabric", "failed-writer", "test-publisher"])
  name              = "/aws/lambda/${var.prefix}-${each.key}"
  retention_in_days = 30
}

data "archive_file" "lambda_src" {
  type        = "zip"
  source_dir  = "${path.module}/lambda_src"
  output_path = "${path.module}/.build/lambda_src.zip"
}

# ── Delivery Fabric: the single writer of outcomes ───────────────────────────
resource "aws_lambda_function" "delivery_fabric" {
  function_name    = "${var.prefix}-delivery-fabric"
  role             = aws_iam_role.delivery_fabric.arn
  runtime          = "python3.12"
  handler          = "delivery_fabric.handler"
  filename         = data.archive_file.lambda_src.output_path
  source_code_hash = data.archive_file.lambda_src.output_base64sha256
  timeout          = 60

  environment {
    variables = local.lambda_env
  }
  depends_on = [aws_cloudwatch_log_group.lambda]
}

resource "aws_lambda_event_source_mapping" "buffer" {
  event_source_arn                   = aws_sqs_queue.buffer.arn
  function_name                      = aws_lambda_function.delivery_fabric.arn
  batch_size                         = 10
  maximum_batching_window_in_seconds = 5
  function_response_types            = ["ReportBatchItemFailures"]

  # The fleet-wide delivery throttle. In the tiered design a dedicated buffer
  # gets its own ESM and its own cap — per-customer throttling by graduation.
  scaling_config {
    maximum_concurrency = var.fabric_max_concurrency
  }
}

# ── Failed writer: buffer-DLQ consumer -> FAILED rows ────────────────────────
resource "aws_lambda_function" "failed_writer" {
  function_name    = "${var.prefix}-failed-writer"
  role             = aws_iam_role.failed_writer.arn
  runtime          = "python3.12"
  handler          = "failed_writer.handler"
  filename         = data.archive_file.lambda_src.output_path
  source_code_hash = data.archive_file.lambda_src.output_base64sha256
  timeout          = 60

  environment {
    variables = local.lambda_env
  }
  depends_on = [aws_cloudwatch_log_group.lambda]
}

resource "aws_lambda_event_source_mapping" "buffer_dlq" {
  event_source_arn                   = aws_sqs_queue.buffer_dlq.arn
  function_name                      = aws_lambda_function.failed_writer.arn
  batch_size                         = 10
  maximum_batching_window_in_seconds = 5
  function_response_types            = ["ReportBatchItemFailures"]
}

# ── Test publisher: console-invokable event generator ────────────────────────
# {"gold_run_id": "fr-1", "events": 10}  from the Lambda Test tab — no local
# credentials or scripts needed (the lesson of the first POC's testing).
resource "aws_lambda_function" "test_publisher" {
  function_name    = "${var.prefix}-test-publisher"
  role             = aws_iam_role.test_publisher.arn
  runtime          = "python3.12"
  handler          = "test_publisher.handler"
  filename         = data.archive_file.lambda_src.output_path
  source_code_hash = data.archive_file.lambda_src.output_base64sha256
  timeout          = 120

  environment {
    variables = local.lambda_env
  }
  depends_on = [aws_cloudwatch_log_group.lambda]
}
