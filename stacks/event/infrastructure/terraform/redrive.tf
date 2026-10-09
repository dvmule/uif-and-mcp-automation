# The event redrive - re-delivers FAILED deliveries (Table 4) to the
# subscriber's CURRENT target, directly (no bus, no fan-out). Steward-invoked;
# no event source. Contract: ../../docs/delivery-metrics.md (write rules 2-3
# + dispatch). Domain-agnostic like every stack component: the customer, the
# domain and the target all come from the invocation payload and the tables.

resource "aws_iam_role" "event_redrive" {
  name = "${local.event_stack_prefix}-redrive-role"

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

resource "aws_iam_role_policy" "event_redrive" {
  name = "${local.event_stack_prefix}-redrive-policy"
  role = aws_iam_role.event_redrive.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:*"
      },
      {
        # Table 4: the outstanding-failures query and the rules-2/3 row
        # settlements. Base table only - no GSI reads on this path.
        Effect   = "Allow"
        Action   = ["dynamodb:Query", "dynamodb:UpdateItem"]
        Resource = aws_dynamodb_table.delivery_metrics.arn
      },
      {
        # Table 5, read-only: the subscription's CURRENT target + status.
        Effect   = "Allow"
        Action   = ["dynamodb:GetItem"]
        Resource = aws_dynamodb_table.subscription_configs.arn
      },
      {
        # Table 2: the run's own REDRIVE row.
        Effect   = "Allow"
        Action   = ["dynamodb:PutItem"]
        Resource = aws_dynamodb_table.pipeline_metrics.arn
      },
      {
        # Direct delivery to MANAGED subscription queues (every domain, by
        # the naming convention). A bring-your-own queue authorizes through
        # its own resource policy instead - the {policy_sid}r statement the
        # provisioner writes now that REDRIVE_ROLE_ARN is set - and needs
        # this list widened only if it is cross-account AND the customer
        # scopes their grant to require it.
        Effect   = "Allow"
        Action   = ["sqs:SendMessage", "sqs:GetQueueUrl"]
        Resource = "arn:aws:sqs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:${local.event_stack_prefix}-*"
      },
    ]
  })
}

resource "aws_lambda_function" "event_redrive" {
  function_name = "${local.event_stack_prefix}-redrive"
  description   = "Re-delivers FAILED deliveries (delivery-metrics) to the subscription's current target - steward-invoked, per customer, oldest first."
  role          = aws_iam_role.event_redrive.arn
  runtime       = "python3.13"
  handler       = "event_redrive.lambda_handler"
  timeout       = 300
  # Code from the release-artifacts bucket at its content-addressed key
  # (locals.tf, release.tf) -- never zipped from the repo at plan time.
  s3_bucket        = aws_s3_bucket.release_artifacts.id
  s3_key           = local.lambda_s3_key["event_redrive"]
  source_code_hash = lookup(var.lambda_code_hashes, "event_redrive", null)

  environment {
    variables = {
      DELIVERY_METRICS_TABLE     = aws_dynamodb_table.delivery_metrics.name
      SUBSCRIPTION_CONFIGS_TABLE = aws_dynamodb_table.subscription_configs.name
      PIPELINE_METRICS_TABLE     = aws_dynamodb_table.pipeline_metrics.name
    }
  }

  tags = local.common_tags
}
