# Grain test run service (UIFPE-219, ADR-0008 #5) — "one row per <entity>?"
# answered against real dev Silver data, from the wizard's Derived panel.
# Runs the author's query VERBATIM on Athena (aliases resolved by a CTE
# prelude); the caller's SQL is executed by design, so the fence is the
# role: read Silver AND Gold (Gold arrived with the entityStatus mode for
# the step-1 cards), write ONLY the results prefix — nothing a hostile
# query could damage, and both layers sit behind the same dev-MVP trust
# level as the other NONE-auth service URLs. IAM shape mirrors the Event Fabric Processor's
# Athena statements (product_events.tf), which are proven live.
#
# Function URL is AuthType NONE like ai_draft (dev MVP; revisit with the
# Entra story before uat/prod). Abuse is bounded by
# reserved_concurrent_executions, the poll cap, and the read-only role.
#
# Lake Formation: if the Silver databases are LF-governed, this role needs
# one-time LF grants (DESCRIBE on echo_dev_silver_%, SELECT on their
# tables) issued outside Terraform — same note as the eventing role.
# ISSUED for silver in dev 2026-08-31. The GOLD databases need the same
# grants for the entityStatus row counts (uif_echo_dev_gold_%) — without them
# the cards silently omit counts (the client degrades, never errors).
#
# Workgroup precondition (account state, not code): the client-side
# OUTPUT_S3 only holds while the workgroup does NOT enforce its own result
# location. Verified 2026-08-31 in dev: `aws athena get-work-group
# --work-group primary` -> EnforceWorkGroupConfiguration = false.

data "archive_file" "grain_probe" {
  type = "zip"
  source {
    content  = file("${path.module}/../../src/apps/lambdas/grain_probe.py")
    filename = "grain_probe.py"
  }
  output_path = "${path.module}/.build/grain_probe.zip"
}

resource "aws_iam_role" "grain_probe" {
  # Not renamed yet (UIFPE-364): a rename orphans the hand-issued LF grants
  # on the untagged silver databases, and the row counts then fail silently.
  name = "${local.name_prefix}-grain-probe-role"

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

# Gold reads, Terraform-managed so they survive a rename of the role.
# TABLE only: the deploy role can grant TABLE by tag, not DATABASE (UIFPE-229).
resource "aws_lakeformation_permissions" "grain_probe_gold_tables" {
  principal   = aws_iam_role.grain_probe.arn
  permissions = ["SELECT", "DESCRIBE"]

  lf_tag_policy {
    resource_type = "TABLE"
    expression {
      key    = local.gold_layer_tag_key
      values = [var.environment]
    }
  }
}

resource "aws_iam_role_policy" "grain_probe" {
  name = "${local.name_prefix}-grain-probe-policy"
  role = aws_iam_role.grain_probe.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "athena:StartQueryExecution", "athena:GetQueryExecution",
          "athena:GetQueryResults", "athena:StopQueryExecution",
        ]
        Resource = "arn:aws:athena:${var.aws_region}:${data.aws_caller_identity.current.account_id}:workgroup/${var.athena_workgroup}"
      },
      {
        # every Silver database this environment carries — the wizard is
        # domain-agnostic, so the probe reads whichever domain's sources
        # the tested query names. uif_echo_* is the post-ADR-0018 spelling and
        # the only one that matches a database holding data (UIFPE-498); the
        # old echo_${env}_silver_* reached one retired table and nothing else.
        Effect = "Allow"
        Action = ["glue:GetDatabase", "glue:GetTable", "glue:GetPartition", "glue:GetPartitions"]
        Resource = [
          "arn:aws:glue:${var.aws_region}:${data.aws_caller_identity.current.account_id}:catalog",
          "arn:aws:glue:${var.aws_region}:${data.aws_caller_identity.current.account_id}:database/uif_echo_${var.environment}_silver_*",
          "arn:aws:glue:${var.aws_region}:${data.aws_caller_identity.current.account_id}:table/uif_echo_${var.environment}_silver_*/*",
        ]
      },
      {
        # the step-1 entity cards' live half (mode=entityStatus): the
        # entity's last transform run — read-only
        Effect   = "Allow"
        Action   = ["glue:GetJobRuns"]
        Resource = "arn:aws:glue:${var.aws_region}:${data.aws_caller_identity.current.account_id}:job/${local.name_prefix}-gold-transform"
      },
      {
        # gold catalog + data for the cards' row counts
        Effect = "Allow"
        Action = ["glue:GetDatabase", "glue:GetTable", "glue:GetPartition", "glue:GetPartitions"]
        Resource = [
          "arn:aws:glue:${var.aws_region}:${data.aws_caller_identity.current.account_id}:database/${local.gold_db_prefix}_gold_*",
          "arn:aws:glue:${var.aws_region}:${data.aws_caller_identity.current.account_id}:table/${local.gold_db_prefix}_gold_*/*",
        ]
      },
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = ["arn:aws:s3:::${var.gold_bucket}/*"]
      },
      {
        Effect   = "Allow"
        Action   = ["s3:ListBucket", "s3:GetBucketLocation"]
        Resource = ["arn:aws:s3:::${var.gold_bucket}"]
      },
      {
        # No-op unless Silver is Lake Formation-governed; required there for
        # Athena to vend the tables' credentials (see the header note).
        Effect   = "Allow"
        Action   = ["lakeformation:GetDataAccess"]
        Resource = "*"
      },
      {
        # Silver data files (plain IAM-governed tables read directly).
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = "arn:aws:s3:::echo-${var.environment}-silver/*"
      },
      {
        Effect   = "Allow"
        Action   = ["s3:ListBucket", "s3:GetBucketLocation"]
        Resource = "arn:aws:s3:::echo-${var.environment}-silver"
      },
      {
        # Athena writes results here and the service reads them back —
        # the ONLY prefix this role can write.
        Effect = "Allow"
        Action = [
          "s3:PutObject", "s3:GetObject",
          # Athena switches to multipart for larger result writes — the
          # peer eventing role carries the same pair for the same reason
          "s3:AbortMultipartUpload", "s3:ListMultipartUploadParts",
        ]
        Resource = "arn:aws:s3:::${var.gold_artifacts_bucket}/grain-probe-results/*"
      },
      {
        Effect   = "Allow"
        Action   = ["s3:ListBucket", "s3:GetBucketLocation"]
        Resource = "arn:aws:s3:::${var.gold_artifacts_bucket}"
      },
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:*"
      },
    ]
  })
}

resource "aws_lambda_function" "grain_probe" {
  function_name = "${local.name_prefix}-grain-probe"
  role          = aws_iam_role.grain_probe.arn
  handler       = "grain_probe.handler"
  runtime       = "python3.11"
  timeout       = 60
  memory_size   = 256
  # UIFPE-576: the staged release artifact when one exists, the plan-time
  # zip otherwise. lookup(), never an index — Terraform evaluates both
  # branches of a conditional, so a bare [] on an empty map fails the plan.
  filename         = local.lambda_staged["grain_probe"] ? null : data.archive_file.grain_probe.output_path
  s3_bucket        = local.lambda_staged["grain_probe"] ? var.release_artifacts_bucket : null
  s3_key           = local.lambda_staged["grain_probe"] ? local.lambda_s3_key["grain_probe"] : null
  source_code_hash = local.lambda_staged["grain_probe"] ? lookup(var.lambda_code_hashes, "grain_probe", null) : data.archive_file.grain_probe.output_base64sha256

  reserved_concurrent_executions = 2

  environment {
    variables = {
      ENV_NAME         = var.environment
      ATHENA_WORKGROUP = var.athena_workgroup
      OUTPUT_S3        = "s3://${var.gold_artifacts_bucket}/grain-probe-results/"
      GLUE_JOB_NAME    = "${local.name_prefix}-gold-transform"
      POLL_CAP_SECONDS = "45"
    }
  }

  tags = local.common_tags
}

resource "aws_lambda_function_url" "grain_probe" {
  function_name      = aws_lambda_function.grain_probe.function_name
  authorization_type = "NONE"

  cors {
    allow_origins = ["https://d2qua7fu7qeqlx.cloudfront.net", "http://localhost:5183", "http://localhost:5198", "http://localhost:5199"]
    allow_methods = ["POST"]
    allow_headers = ["content-type"]
  }
}

# Terraform-created NONE-auth URLs 403 without this (the console adds it
# silently) — the same fix submit.tf carries.
resource "aws_lambda_permission" "grain_probe_public_url" {
  statement_id           = "AllowPublicFunctionUrl"
  action                 = "lambda:InvokeFunctionUrl"
  function_name          = aws_lambda_function.grain_probe.function_name
  principal              = "*"
  function_url_auth_type = "NONE"
}

output "grain_probe_url" {
  value       = aws_lambda_function_url.grain_probe.function_url
  description = "POST endpoint the Derived panel's test run calls."
}
