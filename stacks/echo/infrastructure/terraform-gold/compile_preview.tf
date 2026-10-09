# Query preview service (UIFPE-219) — the Review step's pre-submit diff.
# Runs the REAL delta compiler (compile_intake_delta.py + the derivation
# battery) over artifact texts the UI sends, and returns the recompiled
# texts + change log. Pure function: no AWS API calls at runtime, nothing
# persisted — the IAM role carries logs only.
#
# PACKAGING IS THE ONE UNUSUAL THING HERE. Unlike the other lambdas (zipped
# straight from their source dir), this bundle is ASSEMBLED by the
# Makefile's `build-preview` target, which `make plan` depends on: the
# handler + the three compiler modules are copied into
# .build/compile_preview_src/ and sqlglot (the battery's parser — the one
# third-party dep any of our lambdas carries) is pip-installed beside them,
# pinned from qe/requirements-dev.txt. archive_file can only zip one
# directory, hence the staging step. Running `terraform plan` without
# `make plan` fails on the missing dir — that is intentional, not a bug:
# an empty bundle must never ship.
#
# Function URL is AuthType NONE like the ai-draft / grain-probe peers (dev
# MVP; revisit with the Entra story before uat/prod). Abuse is bounded by
# reserved concurrency, the 5 MB body cap, the 30s timeout, and the fact
# the handler only ever transforms text and throws it away.

data "archive_file" "compile_preview" {
  # NOT EVALUATED ON A PROMOTED ENVIRONMENT, and it has to be gated rather than
  # merely unreferenced. A data source with no count is read during EVERY plan,
  # whether or not anything uses its output -- so on uif-echo-uat, where
  # lambda_staged is true and the function comes from S3, this still ran and
  # killed the gold plan (run 36332656568):
  #
  #   Error: Archive creation error
  #     error archiving directory: could not archive missing directory:
  #     ./.build/compile_preview_src
  #
  # The header above is right that the missing directory is intentional for a
  # DEV plan -- an empty bundle must never ship, and `make plan` assembles it.
  # What it did not anticipate is an environment that legitimately never builds
  # this bundle at all, because it promotes the one dev built.
  #
  # The other eight archive_file blocks in this root are ungated and fine: they
  # use source_file on paths that exist in any checkout. This is the only one
  # needing a directory something has to assemble first, which is why it is the
  # only one that broke.
  count = local.lambda_staged["compile_preview"] ? 0 : 1

  type        = "zip"
  source_dir  = "${path.module}/.build/compile_preview_src"
  output_path = "${path.module}/.build/compile_preview.zip"
  # pip may leave bytecode behind despite --no-compile flags on older pips;
  # keep the hash independent of the local interpreter either way
  excludes = ["__pycache__", "**/__pycache__"]
}

# uif:rename-approved UIFPE-364
resource "aws_iam_role" "compile_preview" {
  name = "${local.name_prefix}-compile-preview-role"

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

resource "aws_iam_role_policy" "compile_preview" {
  name = "${local.name_prefix}-compile-preview-policy"
  role = aws_iam_role.compile_preview.id

  # logs only — the handler makes no other AWS call by design.
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:*"
      },
    ]
  })
}

resource "aws_lambda_function" "compile_preview" {
  function_name = "${local.name_prefix}-compile-preview"
  role          = aws_iam_role.compile_preview.arn
  handler       = "compile_preview.handler"
  runtime       = "python3.11"
  timeout       = 30
  memory_size   = 512 # sqlglot parsing is CPU-bound; Lambda CPU scales with memory
  # UIFPE-576: the staged release artifact when one exists, the plan-time
  # zip otherwise. lookup(), never an index — Terraform evaluates both
  # branches of a conditional, so a bare [] on an empty map fails the plan.
  #
  # one(...[*]...) FOR THE SAME REASON, now that the archive is counted. The
  # splat yields [] when count is 0 and one() turns that into null, so the
  # untaken branch is still a legal expression. `[0]` would not be: the comment
  # above is exactly why, and it applies to an empty resource list as much as to
  # an empty map.
  filename         = local.lambda_staged["compile_preview"] ? null : one(data.archive_file.compile_preview[*].output_path)
  s3_bucket        = local.lambda_staged["compile_preview"] ? var.release_artifacts_bucket : null
  s3_key           = local.lambda_staged["compile_preview"] ? local.lambda_s3_key["compile_preview"] : null
  source_code_hash = local.lambda_staged["compile_preview"] ? lookup(var.lambda_code_hashes, "compile_preview", null) : one(data.archive_file.compile_preview[*].output_base64sha256)

  reserved_concurrent_executions = 2

  tags = local.common_tags
}

resource "aws_lambda_function_url" "compile_preview" {
  function_name      = aws_lambda_function.compile_preview.function_name
  authorization_type = "NONE"

  cors {
    allow_origins = ["https://d2qua7fu7qeqlx.cloudfront.net", "http://localhost:5183", "http://localhost:5198", "http://localhost:5199"]
    allow_methods = ["POST"]
    allow_headers = ["content-type"]
  }
}

# Terraform-created NONE-auth URLs 403 without this (the console adds it
# silently) — the same fix submit.tf and grain_probe.tf carry.
resource "aws_lambda_permission" "compile_preview_public_url" {
  statement_id           = "AllowPublicFunctionUrl"
  action                 = "lambda:InvokeFunctionUrl"
  function_name          = aws_lambda_function.compile_preview.function_name
  principal              = "*"
  function_url_auth_type = "NONE"
}

output "compile_preview_url" {
  value       = aws_lambda_function_url.compile_preview.function_url
  description = "POST endpoint the Review step's full-query diff calls."
}
