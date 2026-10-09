# The shared Glue execution role runs the gold jobs but is the echo stack's,
# and its own policy names only that stack's stores. This grants the role
# what the gold jobs touch: the warehouse, the artifacts they read, and the
# pipeline config table they read and update (watermarks, run metadata).
resource "aws_iam_role_policy" "glue_gold_data_access" {
  name = "${local.name_prefix}-gold-data-access-policy"
  role = data.aws_iam_role.glue_execution.name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket"]
        Resource = ["arn:aws:s3:::${var.gold_bucket}", "arn:aws:s3:::${var.gold_bucket}/*"]
      },
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:ListBucket"]
        Resource = ["arn:aws:s3:::${var.gold_artifacts_bucket}", "arn:aws:s3:::${var.gold_artifacts_bucket}/*"]
      },
      {
        # Silver is read, never written. Lake Formation vends credentials for
        # a governed table, but the jobs also read the Iceberg files directly,
        # and this bucket is not the one the platform policy names.
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:ListBucket"]
        Resource = ["arn:aws:s3:::${local.silver_bucket}", "arn:aws:s3:::${local.silver_bucket}/*"]
      },
      {
        # Scan is here for the nightly statistics job, which needs EVERY
        # entity's row rather than one - the first gold job to read the table
        # whole, which is why nothing caught its absence until it ran
        # (jr_747cb8bd..., line 236, 2026-09-24). The dispatcher's own role
        # already holds Scan on this table for the same reason.
        Effect   = "Allow"
        Action   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:Query", "dynamodb:Scan", "dynamodb:UpdateItem"]
        Resource = aws_dynamodb_table.gold_pipelines_config.arn
      },
      {
        # Lake Formation vends the credentials for every governed table the
        # jobs read, so without these the Silver read fails no matter what the
        # LF grants say. The legacy role carried them; the standard one did not.
        Effect = "Allow"
        Action = [
          "lakeformation:GetDataAccess",
          "lakeformation:GetTemporaryGlueTableCredentials",
          "lakeformation:GetTemporaryGluePartitionCredentials",
        ]
        Resource = "*"
      },
    ]
  })
}

# Lake Formation: the bucket is a registered data location, so CREATE TABLE
# under it needs this on top of the IAM grant.
resource "aws_lakeformation_permissions" "glue_gold_data_location" {
  principal   = data.aws_iam_role.glue_execution.arn
  permissions = ["DATA_LOCATION_ACCESS"]
  data_location {
    arn = "arn:aws:s3:::${var.gold_bucket}"
  }
}

# A database the transform did not create itself (the standard-named product
# database pre-exists) is reached through the gold layer tag, which the
# config sync applies to every gold database.
resource "aws_lakeformation_permissions" "glue_gold_databases" {
  principal   = data.aws_iam_role.glue_execution.arn
  permissions = ["CREATE_TABLE", "ALTER", "DROP", "DESCRIBE"]
  lf_tag_policy {
    resource_type = "DATABASE"
    expression {
      key    = local.gold_layer_tag_key
      values = [var.environment]
    }
  }
}

resource "aws_lakeformation_permissions" "glue_gold_tables" {
  principal   = data.aws_iam_role.glue_execution.arn
  permissions = ["SELECT", "INSERT", "DELETE", "DESCRIBE", "ALTER", "DROP"]
  lf_tag_policy {
    resource_type = "TABLE"
    expression {
      key    = local.gold_layer_tag_key
      values = [var.environment]
    }
  }
}
