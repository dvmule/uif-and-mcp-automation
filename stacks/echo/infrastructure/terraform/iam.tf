

# ── Glue execution role ───────────────────────────────────────────
# Used by echo-{env}-bronze-ingest Glue job.
# Permissions: read/write Bronze S3, read/write DynamoDB config+metrics,
#              fetch Oracle credentials from Secrets Manager, CloudWatch logs.

data "aws_iam_policy_document" "glue_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["glue.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "glue_execution" {
  name               = "${local.name_prefix}-glue-role"
  assume_role_policy = data.aws_iam_policy_document.glue_assume_role.json

  tags = {
    Project     = local.project_tag
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

data "aws_iam_policy_document" "glue_permissions" {
  # S3: read Glue scripts, read/write Bronze and Silver buckets
  statement {
    effect  = "Allow"
    actions = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket"]
    resources = [
      "arn:aws:s3:::${var.artifacts_bucket}",
      "arn:aws:s3:::${var.artifacts_bucket}/*",
      "arn:aws:s3:::${var.bronze_bucket}",
      "arn:aws:s3:::${var.bronze_bucket}/*",
      "arn:aws:s3:::${var.silver_bucket}",
      "arn:aws:s3:::${var.silver_bucket}/*",
      "arn:aws:s3:::${local.gold_data_bucket}",
      "arn:aws:s3:::${local.gold_data_bucket}/*",
      "arn:aws:s3:::${local.gold_artifacts_bucket}",
      "arn:aws:s3:::${local.gold_artifacts_bucket}/*",
    ]
  }

  # S3: read the promoted Glue scripts (UIFPE-515).
  #
  # READ-ONLY, and a separate statement rather than two more ARNs on the one
  # above, because the access is genuinely different: a Glue job reads its
  # script and must never write into the release bucket. An artifact the runtime
  # can overwrite is not an artifact -- the content-addressed key is the
  # release's identity, and a job with PutObject could invalidate the manifest
  # of a release that has already shipped.
  #
  # Only for a uif-echo-* stack. The legacy echo-* stacks read their scripts
  # from artifacts_bucket, already covered above.
  dynamic "statement" {
    for_each = local.is_uif_stack ? [1] : []
    content {
      effect  = "Allow"
      actions = ["s3:GetObject", "s3:ListBucket"]
      resources = [
        "arn:aws:s3:::${var.release_artifacts_bucket}",
        "arn:aws:s3:::${var.release_artifacts_bucket}/*",
      ]
    }
  }

  # DynamoDB: read configs, write metrics, write analysis results
  statement {
    effect = "Allow"
    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
      "dynamodb:Query",
    ]
    resources = [
      aws_dynamodb_table.glue_job_config.arn,
      aws_dynamodb_table.glue_job_metrics.arn,
      "${aws_dynamodb_table.glue_job_metrics.arn}/index/*",
      aws_dynamodb_table.pipeline_analysis.arn,
      local.gold_config_table_arn,
    ]
  }

  # Secrets Manager: read Oracle credentials, plus GCP service-account JSON
  # secrets consumed by the GCP-native Silver jobs (Hadoop GCS + BigQuery DDL).
  # echo/* — legacy paths; uif/echo/* — UIF standard (identity-secrets.md).
  statement {
    effect  = "Allow"
    actions = ["secretsmanager:GetSecretValue"]
    resources = [
      "arn:aws:secretsmanager:${var.aws_region}:${var.aws_account_id}:secret:oracle-*",
      "arn:aws:secretsmanager:${var.aws_region}:${var.aws_account_id}:secret:gcp-*",
      "arn:aws:secretsmanager:${var.aws_region}:${var.aws_account_id}:secret:echo/*",
      "arn:aws:secretsmanager:${var.aws_region}:${var.aws_account_id}:secret:uif/echo/*",
    ]
  }

  # CloudWatch Logs
  statement {
    effect    = "Allow"
    actions   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["arn:aws:logs:${var.aws_region}:${var.aws_account_id}:log-group:/aws-glue/*"]
  }

  # CloudWatch Metrics — required by Spark's GlueCloudWatchReporter when
  # --enable-metrics is true. PutMetricData has no resource-level restriction.
  statement {
    effect    = "Allow"
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"]
  }

  # Glue catalog — read for Bronze job, read+write for Silver Iceberg tables.
  # GetConnection is required when the job has a connections block.
  statement {
    effect = "Allow"
    actions = [
      "glue:GetDatabase",
      "glue:GetDatabases",
      "glue:CreateDatabase",
      "glue:GetTable",
      "glue:GetTables",
      "glue:CreateTable",
      "glue:UpdateTable",
      "glue:DeleteTable",
      "glue:GetPartitions",
      "glue:GetConnection",
    ]
    resources = ["*"]
  }

  # Allow Glue jobs to trigger other jobs (Bronze → Silver, Lambda → Reconcile,
  # Silver → Gold chaining if ever enabled).
  #
  # Note 2026-06-17: generic_silver_transform_gcp_bq + generic_silver_reconcile_gcp_bq
  # were missing from this list — Bronze got AccessDeniedException trying to
  # trigger Silver for `apps-xxpso-pos-orasale-stg` after the first successful
  # backfill. Added retroactively, and generic_silver_bq_native (the Python Shell
  # replacement landing in Phase 2 of silver-bq-native-migration) added at the
  # same time so the chain works from day one.
  statement {
    effect  = "Allow"
    actions = ["glue:StartJobRun"]
    resources = concat([
      aws_glue_job.generic_silver_transform.arn,
      aws_glue_job.generic_silver_reconcile.arn,
      aws_glue_job.generic_silver_transform_gcp_iceberg.arn,
      aws_glue_job.generic_silver_reconcile_gcp.arn,
      aws_glue_job.generic_silver_transform_gcp_bq.arn,
      aws_glue_job.generic_silver_reconcile_gcp_bq.arn,
      aws_glue_job.generic_silver_bq_native.arn,
    ], local.gold_job_arns)
  }

  # EC2 ENI management — required when Glue runs inside a VPC.
  # Glue creates an elastic network interface in the configured subnet at job start
  # and deletes it on completion. Describe actions require resource = "*".
  statement {
    effect = "Allow"
    actions = [
      "ec2:DescribeVpcs",
      "ec2:DescribeSubnets",
      "ec2:DescribeSecurityGroups",
      "ec2:DescribeVpcAttribute",
      "ec2:DescribeVpcEndpoints",
      "ec2:DescribeRouteTables",
      "ec2:DescribeNetworkInterfaces",
      "ec2:CreateNetworkInterface",
      "ec2:DeleteNetworkInterface",
      "ec2:AttachNetworkInterface",
      "ec2:ModifyNetworkInterfaceAttribute",
      "ec2:CreateTags",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "glue_execution" {
  name   = "${local.name_prefix}-glue-policy"
  role   = aws_iam_role.glue_execution.id
  policy = data.aws_iam_policy_document.glue_permissions.json
}

# ── Lambda execution role ─────────────────────────────────────────
# Shared by both scheduler_dispatcher and pipeline_controller Lambdas.
# Permissions: read/write DynamoDB, start/get/stop Glue runs, CloudWatch logs.

data "aws_iam_policy_document" "lambda_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lambda_execution" {
  name               = "${local.name_prefix}-lambda-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json

  tags = {
    Project     = local.project_tag
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

data "aws_iam_policy_document" "lambda_permissions" {
  # DynamoDB: config CRUD, metrics reads, alert config, analysis results
  statement {
    effect = "Allow"
    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
      "dynamodb:DeleteItem",
      "dynamodb:Query",
      "dynamodb:Scan",
    ]
    resources = [
      aws_dynamodb_table.glue_job_config.arn,
      aws_dynamodb_table.glue_job_metrics.arn,
      "${aws_dynamodb_table.glue_job_metrics.arn}/index/*",
      local.alert_subscribers_table_arn,
      aws_dynamodb_table.pipeline_analysis.arn,
      local.gold_config_table_arn,
      aws_dynamodb_table.dq_check_config.arn,
    ]
  }

  # Glue: trigger runs, check status, stop runs, manage Silver/Gold databases, trigger async analysis
  #
  # Note 2026-07-18: generic_silver_reconcile_gcp_bq (+ transform/bq_native siblings) were
  # missing here — scheduler_dispatcher got AccessDeniedException on every staggered
  # reconcile slot. Keep in sync with glue_execution StartJobRun resources above.
  statement {
    effect = "Allow"
    actions = [
      "glue:StartJobRun",
      "glue:GetJobRun",
      "glue:BatchStopJobRun",
      "glue:GetJobRuns",
      "glue:GetDatabase",
      "glue:CreateDatabase",
      "glue:DeleteDatabase",
      "glue:GetDatabases",
    ]
    resources = concat([
      aws_glue_job.generic_bronze_ingest.arn,
      aws_glue_job.generic_silver_transform.arn,
      aws_glue_job.analyze_table.arn,
      aws_glue_job.generic_silver_reconcile.arn,
      aws_glue_job.generic_silver_transform_gcp_iceberg.arn,
      aws_glue_job.generic_silver_reconcile_gcp.arn,
      aws_glue_job.generic_silver_transform_gcp_bq.arn,
      aws_glue_job.generic_silver_reconcile_gcp_bq.arn,
      aws_glue_job.generic_silver_bq_native.arn,
      aws_glue_job.generic_silver_quality_check.arn,
      "arn:aws:glue:${var.aws_region}:${var.aws_account_id}:catalog",
      "arn:aws:glue:${var.aws_region}:${var.aws_account_id}:database/*",
    ], local.gold_job_arns)
  }

  # S3: delete Bronze, Silver, Gold data during cascade delete; read Gold artifacts
  statement {
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:DeleteObject",
      "s3:ListBucket",
      "s3:PutObject",
    ]
    resources = [
      "arn:aws:s3:::${var.bronze_bucket}",
      "arn:aws:s3:::${var.bronze_bucket}/*",
      "arn:aws:s3:::${var.silver_bucket}",
      "arn:aws:s3:::${var.silver_bucket}/*",
      "arn:aws:s3:::${local.gold_data_bucket}",
      "arn:aws:s3:::${local.gold_data_bucket}/*",
      "arn:aws:s3:::${local.gold_artifacts_bucket}",
      "arn:aws:s3:::${local.gold_artifacts_bucket}/*",
    ]
  }

  # Lake Formation: grant permissions on Silver databases + catalog access
  statement {
    effect    = "Allow"
    actions   = ["lakeformation:GrantPermissions", "lakeformation:Describe*"]
    resources = ["*"]
  }

  # CloudWatch Logs
  statement {
    effect    = "Allow"
    actions   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["arn:aws:logs:${var.aws_region}:${var.aws_account_id}:log-group:/aws/lambda/*"]
  }

  # Secrets Manager: read credentials + list tagged secrets for GET /connections.
  # gcp-* covers GCP service-account JSON secrets consumed by gcs_replicator.
  # echo/* — legacy paths; uif/echo/* — UIF standard (identity-secrets.md).
  statement {
    effect  = "Allow"
    actions = ["secretsmanager:GetSecretValue"]
    resources = [
      "arn:aws:secretsmanager:${var.aws_region}:${var.aws_account_id}:secret:oracle-*",
      "arn:aws:secretsmanager:${var.aws_region}:${var.aws_account_id}:secret:gcp-*",
      "arn:aws:secretsmanager:${var.aws_region}:${var.aws_account_id}:secret:echo/*",
      "arn:aws:secretsmanager:${var.aws_region}:${var.aws_account_id}:secret:uif/echo/*",
    ]
  }

  statement {
    effect    = "Allow"
    actions   = ["secretsmanager:ListSecrets"]
    resources = ["*"]
  }

  # EC2 ENI management — required when Lambda runs inside a VPC.
  statement {
    effect = "Allow"
    actions = [
      "ec2:CreateNetworkInterface",
      "ec2:DescribeNetworkInterfaces",
      "ec2:DeleteNetworkInterface",
      "ec2:AssignPrivateIpAddresses",
      "ec2:UnassignPrivateIpAddresses",
    ]
    resources = ["*"]
  }

  # CloudWatch metrics — gcs_replicator emits per-pipeline counters/durations
  # under namespace EnterpriseDataConnect/GcsReplication. PutMetricData has no
  # resource-level scoping.
  statement {
    effect    = "Allow"
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"]
  }

  # UIFPE-543: the Notifications page lists the gold alarms live (read-only).
  # DescribeAlarms is a list operation with no resource-level scoping.
  statement {
    effect    = "Allow"
    actions   = ["cloudwatch:DescribeAlarms"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "lambda_execution" {
  name   = "${local.name_prefix}-lambda-policy"
  role   = aws_iam_role.lambda_execution.id
  policy = data.aws_iam_policy_document.lambda_permissions.json
}



# GitHub Actions OIDC provider and per-environment IAM roles.
# Allows GitHub Actions to assume AWS roles without long-lived credentials.
# Scoped to this repository only.

locals {
  github_repo = "Raj-Karunakaran_pearson/enterprise-data-connect"
}

# The OIDC provider already exists in this account — reference it, don't create it.
data "aws_iam_openid_connect_provider" "github" {
  url = "https://token.actions.githubusercontent.com"
}

data "aws_iam_policy_document" "github_actions_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [data.aws_iam_openid_connect_provider.github.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      # Allow main branch (deploy) and any branch (CI plan/validate)
      values = [
        "repo:${local.github_repo}:ref:refs/heads/main",
        "repo:${local.github_repo}:environment:${var.environment}",
      ]
    }
  }
}

resource "aws_iam_role" "github_actions" {
  name               = "${local.name_prefix}-github-actions-role"
  assume_role_policy = data.aws_iam_policy_document.github_actions_assume_role.json

  tags = {
    Project     = local.project_tag
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

data "aws_iam_policy_document" "github_actions_permissions" {
  # Terraform state backend — UIF stacks use s3://{artifacts_bucket}/tfstate/;
  # live stacks use the shared pearson-ingestion-framework-tfstate bucket.
  statement {
    effect = "Allow"
    actions = [
      "s3:GetObject", "s3:PutObject", "s3:DeleteObject",
      "s3:ListBucket", "s3:GetBucketVersioning",
    ]
    resources = local.is_uif_stack ? [
      "arn:aws:s3:::${var.artifacts_bucket}",
      "arn:aws:s3:::${var.artifacts_bucket}/tfstate",
      "arn:aws:s3:::${var.artifacts_bucket}/tfstate/*",
      ] : [
      "arn:aws:s3:::pearson-ingestion-framework-tfstate-${var.aws_account_id}",
      "arn:aws:s3:::pearson-ingestion-framework-tfstate-${var.aws_account_id}/*",
    ]
  }

  dynamic "statement" {
    for_each = local.is_uif_stack ? [] : [1]
    content {
      effect  = "Allow"
      actions = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:DeleteItem"]
      resources = [
        "arn:aws:dynamodb:${var.aws_region}:${var.aws_account_id}:table/ingestion-framework-tfstate-locks",
      ]
    }
  }

  # Glue scripts S3
  statement {
    effect  = "Allow"
    actions = ["s3:PutObject", "s3:GetObject", "s3:DeleteObject", "s3:ListBucket"]
    resources = [
      "arn:aws:s3:::${var.artifacts_bucket}",
      "arn:aws:s3:::${var.artifacts_bucket}/*",
    ]
  }

  # UI S3 + CloudFront invalidation
  statement {
    effect = "Allow"
    actions = [
      "s3:PutObject", "s3:GetObject", "s3:DeleteObject",
      "s3:ListBucket", "s3:GetBucketLocation",
    ]
    resources = [
      "arn:aws:s3:::${local.ui_bucket}",
      "arn:aws:s3:::${local.ui_bucket}/*",
    ]
  }

  statement {
    effect    = "Allow"
    actions   = ["cloudfront:CreateInvalidation"]
    resources = ["arn:aws:cloudfront::${var.aws_account_id}:distribution/*"]
  }

  # Terraform needs broad IAM / resource management permissions
  # Scope is limited to resources prefixed with "echo-${env}"
  statement {
    effect = "Allow"
    actions = ["iam:GetRole", "iam:PassRole", "iam:CreateRole", "iam:DeleteRole",
      "iam:AttachRolePolicy", "iam:DetachRolePolicy", "iam:PutRolePolicy",
      "iam:DeleteRolePolicy", "iam:GetRolePolicy", "iam:ListRolePolicies",
    "iam:ListAttachedRolePolicies", "iam:TagRole", "iam:UntagRole"]
    resources = concat(
      ["arn:aws:iam::${var.aws_account_id}:role/echo-${var.environment}-*"],
      local.is_uif_stack ? ["arn:aws:iam::${var.aws_account_id}:role/${local.name_prefix}-*"] : [],
    )
  }

  # Lambda, Glue, API Gateway, DynamoDB, EventBridge, CloudWatch
  statement {
    effect  = "Allow"
    actions = ["lambda:*"]
    resources = [
      "arn:aws:lambda:${var.aws_region}:${var.aws_account_id}:function:*${var.environment}*",
    ]
  }

  # Event source mappings are their own Lambda resource type, so the statement
  # above (function:* ARNs) does not cover them. That grant is NOT here: it
  # lives in capabilities/infrastructure/echo-dev-deploy-1.policy.json, which
  # capabilities/infrastructure/README.md makes the place deploy-role grants go
  # ("a module never adds its own deploy policy"). Adding one here instead is
  # inert until somebody applies this root by hand, which is how the same grant
  # ended up in two places on 2026-09-17.

  statement {
    effect    = "Allow"
    actions   = ["glue:*"]
    resources = ["*"]
  }

  statement {
    effect    = "Allow"
    actions   = ["dynamodb:*"]
    resources = ["arn:aws:dynamodb:${var.aws_region}:${var.aws_account_id}:table/*${var.environment}*"]
  }

  statement {
    effect    = "Allow"
    actions   = ["apigateway:*"]
    resources = ["arn:aws:apigateway:${var.aws_region}::/*"]
  }

  statement {
    effect    = "Allow"
    actions   = ["events:*"]
    resources = ["arn:aws:events:${var.aws_region}:${var.aws_account_id}:rule/*${var.environment}*"]
  }

  statement {
    effect    = "Allow"
    actions   = ["logs:*"]
    resources = ["arn:aws:logs:${var.aws_region}:${var.aws_account_id}:log-group:/aws*"]
  }

  statement {
    effect = "Allow"
    actions = ["ec2:Describe*", "ec2:CreateSecurityGroup", "ec2:DeleteSecurityGroup",
    "ec2:AuthorizeSecurityGroupIngress", "ec2:RevokeSecurityGroupIngress"]
    resources = ["*"]
  }

  statement {
    effect  = "Allow"
    actions = ["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"]
    resources = [
      "arn:aws:secretsmanager:${var.aws_region}:${var.aws_account_id}:secret:oracle-*",
      "arn:aws:secretsmanager:${var.aws_region}:${var.aws_account_id}:secret:echo/*",
      "arn:aws:secretsmanager:${var.aws_region}:${var.aws_account_id}:secret:uif/echo/*",
    ]
  }

  # Read-only access to resolve ARNs / caller identity
  statement {
    effect    = "Allow"
    actions   = ["sts:GetCallerIdentity"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "github_actions" {
  name   = "${local.name_prefix}-github-actions-policy"
  role   = aws_iam_role.github_actions.id
  policy = data.aws_iam_policy_document.github_actions_permissions.json
}

