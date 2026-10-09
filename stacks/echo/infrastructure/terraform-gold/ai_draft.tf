# Generate-with-AI draft service (UIFPE-102) — Bedrock via IAM, no API keys.
# The Lambda only DRAFTS intake JSON for the wizard's Rules step (ADR-0006
# D6); nothing it returns reaches an artifact without human review + the
# deterministic gates.
#
# Function URL is AuthType NONE for the dev MVP: the endpoint drafts JSON and
# holds no data. Abuse is bounded by reserved_concurrent_executions and
# MAX_TOKENS; revisit with the platform auth story (Entra) before uat/prod.

data "archive_file" "ai_draft" {
  type = "zip"
  source {
    content  = file("${path.module}/../../src/apps/lambdas/ai_draft.py")
    filename = "ai_draft.py"
  }
  output_path = "${path.module}/.build/ai_draft.zip"
}

# uif:rename-approved UIFPE-364
resource "aws_iam_role" "ai_draft" {
  name = "${local.name_prefix}-gold-ai-draft-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = local.common_tags
  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_iam_role_policy" "ai_draft" {
  name = "${local.name_prefix}-gold-ai-draft-policy"
  role = aws_iam_role.ai_draft.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = ["bedrock:InvokeModel"]
        # EU cross-region inference profile + the regional foundation models
        # it fans out to.
        Resource = [
          "arn:aws:bedrock:*:${data.aws_caller_identity.current.account_id}:inference-profile/eu.anthropic.*",
          "arn:aws:bedrock:*::foundation-model/anthropic.*",
        ]
      },
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:*"
      },
    ]
  })
}

resource "aws_lambda_function" "ai_draft" {
  function_name = "${local.name_prefix}-gold-ai-draft"
  role          = aws_iam_role.ai_draft.arn
  handler       = "ai_draft.handler"
  runtime       = "python3.11"
  timeout       = 60
  memory_size   = 256
  # UIFPE-576: the staged release artifact when one exists, the plan-time
  # zip otherwise. lookup(), never an index — Terraform evaluates both
  # branches of a conditional, so a bare [] on an empty map fails the plan.
  filename         = local.lambda_staged["ai_draft"] ? null : data.archive_file.ai_draft.output_path
  s3_bucket        = local.lambda_staged["ai_draft"] ? var.release_artifacts_bucket : null
  s3_key           = local.lambda_staged["ai_draft"] ? local.lambda_s3_key["ai_draft"] : null
  source_code_hash = local.lambda_staged["ai_draft"] ? lookup(var.lambda_code_hashes, "ai_draft", null) : data.archive_file.ai_draft.output_base64sha256

  reserved_concurrent_executions = 2

  environment {
    variables = {
      BEDROCK_MODEL_ID = "eu.anthropic.claude-sonnet-4-5-20250929-v1:0"
      MAX_TOKENS       = "4000"
    }
  }

  tags = local.common_tags
}

resource "aws_lambda_function_url" "ai_draft" {
  function_name      = aws_lambda_function.ai_draft.function_name
  authorization_type = "NONE"

  cors {
    allow_origins = ["https://d2qua7fu7qeqlx.cloudfront.net", "http://localhost:5183"]
    allow_methods = ["POST"]
    allow_headers = ["content-type"]
  }
}

output "ai_draft_url" {
  value       = aws_lambda_function_url.ai_draft.function_url
  description = "POST endpoint the wizard's Generate-with-AI method calls."
}
