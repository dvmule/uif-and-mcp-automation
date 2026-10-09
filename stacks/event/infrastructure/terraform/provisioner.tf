# The subscription provisioner - table rows (any domain) become queues,
# policies and rules on that domain's bus. Fully data-driven; onboarding a
# domain adds ROWS, never stack resources.
# Moved from domains/product (2026-09-21); Lambda/role adopted via
# imports.tf; the DynamoDB-stream event source mapping is imported by UUID
# (see MIGRATION.md) because ESM ids are runtime-assigned.

# ── Subscription provisioner: uif-event-<env>-subscription-provisioner ──────
# Materialises subscription-configs rows as AWS resources (the component the
# table-driven model was waiting for): PENDING -> managed queue + DLQ +
# policy Sids + rule/target on the domain bus -> ACTIVE with real ARNs;
# DELETING -> teardown -> DELETED tombstone; SUSPENDED/resume toggle the
# rule. Driven by the table's DynamoDB Stream; a manual {} invoke runs the
# crash-recovery sweep over PENDING/DELETING rows. Contract:
# ../../src/events/docs/subscription-configs.md.

resource "aws_iam_role" "subscription_provisioner" {
  name = "${local.event_stack_prefix}-subscription-provisioner-role"

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

resource "aws_iam_role_policy" "subscription_provisioner" {
  name = "${local.event_stack_prefix}-subscription-provisioner-policy"
  role = aws_iam_role.subscription_provisioner.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:*"
      },
      {
        # The table stream (event source) + the optimistic-locked row updates
        # and the sweep scan.
        Effect = "Allow"
        Action = [
          "dynamodb:GetRecords", "dynamodb:GetShardIterator",
          "dynamodb:DescribeStream", "dynamodb:ListStreams",
        ]
        Resource = aws_dynamodb_table.subscription_configs.stream_arn
      },
      {
        Effect   = "Allow"
        Action   = ["dynamodb:UpdateItem", "dynamodb:Scan"]
        Resource = aws_dynamodb_table.subscription_configs.arn
      },
      {
        # Bus resolution (2026-09-25): a provision reads the domain's
        # pipeline-configs row to learn the bus its publish job emits to
        # (a domain may ride another domain's bus while its own is
        # pending); read-only, one GetItem per domain per invocation.
        Effect   = "Allow"
        Action   = ["dynamodb:GetItem"]
        Resource = aws_dynamodb_table.glue_configs.arn
      },
      {
        # Managed subscription queues + DLQs - name-scoped to the stack
        # prefix, so the provisioner can never touch foreign queues.
        Effect = "Allow"
        Action = [
          "sqs:CreateQueue", "sqs:GetQueueUrl", "sqs:GetQueueAttributes",
          "sqs:SetQueueAttributes", "sqs:TagQueue",
        ]
        Resource = "arn:aws:sqs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:${local.event_stack_prefix}-*"
      },
      {
        # Managed subscription STREAMS (KINESIS targets, 2026-09-24) - same
        # prefix scoping as the queues. PutResourcePolicy carries the
        # consumer grant; DeleteResourcePolicy + AddTags serve teardown.
        Effect = "Allow"
        Action = [
          "kinesis:CreateStream", "kinesis:DescribeStreamSummary",
          "kinesis:AddTagsToStream", "kinesis:PutResourcePolicy",
          "kinesis:DeleteResourcePolicy",
        ]
        Resource = "arn:aws:kinesis:${var.aws_region}:${data.aws_caller_identity.current.account_id}:stream/${local.event_stack_prefix}-*"
      },
      {
        # PutTargets with a Kinesis target hands EventBridge the shared
        # delivery role - the caller must be allowed to pass it, and only
        # to EventBridge.
        Effect   = "Allow"
        Action   = ["iam:PassRole"]
        Resource = aws_iam_role.events_to_kinesis.arn
        Condition = {
          StringEquals = { "iam:PassedToService" = "events.amazonaws.com" }
        }
      },
      {
        # Per-subscription rules on the domain bus only.
        Effect = "Allow"
        Action = [
          "events:PutRule", "events:DeleteRule", "events:PutTargets",
          "events:RemoveTargets", "events:EnableRule", "events:DisableRule",
          "events:DescribeRule",
        ]
        # Rules on ANY domain bus (rule ARNs are rule/{bus}/{name}; both
        # start with the stack prefix by convention).
        Resource = "arn:aws:events:${var.aws_region}:${data.aws_caller_identity.current.account_id}:rule/${local.event_stack_prefix}-*"
      },
    ]
  })
}

resource "aws_lambda_function" "subscription_provisioner" {
  function_name = "${local.event_stack_prefix}-subscription-provisioner"
  description   = "Materialises subscription-configs rows: PENDING -> queue + policy + rule -> ACTIVE; DELETING -> teardown; suspend/resume toggles the rule."
  role          = aws_iam_role.subscription_provisioner.arn
  runtime       = "python3.13"
  handler       = "subscription_provisioner.lambda_handler"
  timeout       = 120
  # Code from the release-artifacts bucket at its content-addressed key
  # (locals.tf, release.tf) -- never zipped from the repo at plan time.
  s3_bucket        = aws_s3_bucket.release_artifacts.id
  s3_key           = local.lambda_s3_key["subscription_provisioner"]
  source_code_hash = lookup(var.lambda_code_hashes, "subscription_provisioner", null)

  environment {
    variables = {
      SUBSCRIPTION_CONFIGS_TABLE = aws_dynamodb_table.subscription_configs.name
      # No DOMAIN_BUS: the bus is resolved per subscription ROW - the
      # domain's pipeline-configs row first (where its publish job actually
      # emits), the uif-event-{env}-{domain} convention as fallback - so one
      # provisioner serves every domain, including one riding another's bus.
      PIPELINE_CONFIGS_TABLE = aws_dynamodb_table.glue_configs.name
      EVENT_STACK_PREFIX     = local.event_stack_prefix
      ACCOUNT_ID             = data.aws_caller_identity.current.account_id
      # The redrive Lambda's role: provisions now add the {policy_sid}r
      # SendMessage statement to every managed queue's policy, so redrive can
      # deliver directly. Subscriptions provisioned BEFORE this landed pick
      # it up on their next re-provision (a PENDING flip); same-account
      # managed queues also authorize via the redrive role's own policy, so
      # the statement only gates bring-your-own/cross-account targets.
      REDRIVE_ROLE_ARN = aws_iam_role.event_redrive.arn
      # The ONE shared role EventBridge assumes to deliver to managed
      # Kinesis streams (Kinesis targets require a RoleArn) - stack-owned,
      # prefix-wildcarded, so KINESIS subscriptions for any domain need no
      # stack change.
      KINESIS_DELIVERY_ROLE_ARN = aws_iam_role.events_to_kinesis.arn
    }
  }

  tags = local.common_tags
}

resource "aws_lambda_event_source_mapping" "subscription_provisioner" {
  event_source_arn                   = aws_dynamodb_table.subscription_configs.stream_arn
  function_name                      = aws_lambda_function.subscription_provisioner.arn
  starting_position                  = "LATEST"
  batch_size                         = 10
  maximum_batching_window_in_seconds = 2
  function_response_types            = ["ReportBatchItemFailures"]
}

# The shared EventBridge -> Kinesis delivery role (KINESIS targets require a
# RoleArn; SQS targets authorize via queue policy instead). One role serves
# every managed stream by the naming convention - no per-domain, no
# per-subscription IAM.
resource "aws_iam_role" "events_to_kinesis" {
  name = "${local.event_stack_prefix}-events-to-kinesis-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "events.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy" "events_to_kinesis" {
  name = "${local.event_stack_prefix}-events-to-kinesis-policy"
  role = aws_iam_role.events_to_kinesis.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["kinesis:PutRecord", "kinesis:PutRecords"]
      Resource = "arn:aws:kinesis:${var.aws_region}:${data.aws_caller_identity.current.account_id}:stream/${local.event_stack_prefix}-*"
    }]
  })
}
