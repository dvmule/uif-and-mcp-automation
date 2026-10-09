# Wizard submission service (UIFPE-218) — the UI writes pull requests, never
# live state (ADR-0006 D4). POST turns a finished wizard task into a branch +
# PR; GET is "My submissions" (the author never needs GitHub to know where
# their change stands).
#
# Credential — the decision parked since UIFPE-101, recorded in submit.py's
# docstring: a fine-grained PAT scoped to THIS repository only
# (contents:write + pull_requests:write), stored in the secret below. Not a
# GitHub App: commits are authored by the submitting engineer (the
# lint-identity rule), so the credential never appears as an identity, and an
# App would add a JWT exchange for the same two scopes. Provision once:
#
#   aws secretsmanager put-secret-value \
#     --secret-id uif/echo/dev/submit/github \
#     --secret-string '{"token":"<fine-grained PAT>"}' --region eu-west-1
#
# The SAME PAT is also stored as the GitHub Actions secret
# INTAKE_COMPILE_TOKEN — intake-compile.yml pushes with it so the PR's
# required checks run against the compile commit (pushes made with the
# default GITHUB_TOKEN do not trigger workflows). One credential, two homes;
# rotate both together.
#
# Function URL is AuthType NONE like ai_draft (dev MVP; revisit with the
# Entra story before uat/prod) — but unlike ai_draft this Lambda HOLDS a
# write credential, so the blast radius is fenced in code, not just in IAM:
# submit.py refuses any branch outside intake/UIFPE-<n>-* and any file
# outside domains/*/src/schemas/**.json, and everything it writes still has
# to get through human review and every PR gate before it can merge.

data "archive_file" "submit" {
  type = "zip"
  source {
    content  = file("${path.module}/../../src/apps/lambdas/submit.py")
    filename = "submit.py"
  }
  output_path = "${path.module}/.build/submit.zip"
}

# The GitHub token for the submission service lives in a secret created and
# valued by hand; the deploy role can neither manage nor describe secrets
# under uif/echo/, so this root names it and never reads it.
locals {
  submit_secret_name = "uif/echo/${var.environment}/submit/github"
}

# uif:rename-approved UIFPE-364
resource "aws_iam_role" "submit" {
  name = "${local.name_prefix}-submit-role"

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

resource "aws_iam_role_policy" "submit" {
  name = "${local.name_prefix}-submit-policy"
  role = aws_iam_role.submit.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = "arn:aws:secretsmanager:${var.aws_region}:${data.aws_caller_identity.current.account_id}:secret:${local.submit_secret_name}-*"
      },
      {
        # first-run evidence for the My submissions ladder — read-only
        Effect   = "Allow"
        Action   = ["glue:GetJobRuns"]
        Resource = "arn:aws:glue:${var.aws_region}:${data.aws_caller_identity.current.account_id}:job/${local.name_prefix}-gold-transform"
      },
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:*"
      },
    ]
  })
}

resource "aws_lambda_function" "submit" {
  function_name = "${local.name_prefix}-submit"
  role          = aws_iam_role.submit.arn
  handler       = "submit.handler"
  runtime       = "python3.11"
  timeout       = 30
  memory_size   = 256
  # UIFPE-576: the staged release artifact when one exists, the plan-time
  # zip otherwise. lookup(), never an index — Terraform evaluates both
  # branches of a conditional, so a bare [] on an empty map fails the plan.
  filename         = local.lambda_staged["submit"] ? null : data.archive_file.submit.output_path
  s3_bucket        = local.lambda_staged["submit"] ? var.release_artifacts_bucket : null
  s3_key           = local.lambda_staged["submit"] ? local.lambda_s3_key["submit"] : null
  source_code_hash = local.lambda_staged["submit"] ? lookup(var.lambda_code_hashes, "submit", null) : data.archive_file.submit.output_base64sha256

  reserved_concurrent_executions = 2

  environment {
    variables = {
      GITHUB_REPO          = "pearson-dnt-coe/universal-Integration-framework"
      BASE_BRANCH          = "develop"
      GITHUB_TOKEN_SECRET  = local.submit_secret_name
      DEPLOY_WORKFLOW_FILE = "deploy-product-dev.yml"
      GLUE_JOB_NAME        = "${local.name_prefix}-gold-transform"
    }
  }

  tags = local.common_tags
}

resource "aws_lambda_function_url" "submit" {
  function_name      = aws_lambda_function.submit.function_name
  authorization_type = "NONE"

  cors {
    allow_origins = ["https://d2qua7fu7qeqlx.cloudfront.net", "http://localhost:5183", "http://localhost:5198", "http://localhost:5199"]
    allow_methods = ["GET", "POST"]
    allow_headers = ["content-type"]
  }
}

# A NONE-auth function URL still needs a resource policy allowing public
# InvokeFunctionUrl — the console adds it silently, Terraform does not, and
# without it every caller gets 403. (ai_draft's equivalent statement was
# console-added; bringing it under Terraform is a separate cleanup.)
resource "aws_lambda_permission" "submit_public_url" {
  statement_id           = "AllowPublicFunctionUrl"
  action                 = "lambda:InvokeFunctionUrl"
  function_name          = aws_lambda_function.submit.function_name
  principal              = "*"
  function_url_auth_type = "NONE"
}

output "submit_url" {
  value       = aws_lambda_function_url.submit.function_url
  description = "Submission service: POST opens the PR, GET lists My submissions."
}
