# lakeformation.tf
# Lake Formation permissions for the Glue execution role.
# Lake Formation adds a permission layer on top of IAM — both must allow access.
#
# Set enable_lakeformation = false for GCP-only stacks (prod IPM) where Silver
# and Gold live in BigQuery/GCS and the AWS Glue Catalog + LF are unused. The
# flag gates every resource in this file, including the three Glue catalog
# databases — in a GCP-only stack those are created on demand by
# pipeline_controller at registration time and stay outside Terraform.
#
# Pattern for adding a new silver domain (when LF is enabled):
#   1. Add an aws_glue_catalog_database resource (e.g. "silver_hr")
#   2. Add aws_lakeformation_permissions for that database + its tables
#   3. make apply ENV=dev


# ── Register Silver S3 as a Lake Formation data location ──────────
# Required so the Glue role can write Iceberg data to S3 via Lake Formation.

resource "aws_lakeformation_resource" "silver" {
  count = var.enable_lakeformation ? 1 : 0
  arn   = "arn:aws:s3:::${var.silver_bucket}"
}

# ── Catalog-level: allow Glue role to create new databases ────────

resource "aws_lakeformation_permissions" "glue_catalog_create_database" {
  count       = var.enable_lakeformation ? 1 : 0
  principal   = aws_iam_role.glue_execution.arn
  permissions = ["CREATE_DATABASE", "ALTER"]

  catalog_resource = true
}

# ── Catalog-level: allow Lambda role to describe & create databases ────────
# Lambda auto-creates Silver databases on pipeline registration

resource "aws_lakeformation_permissions" "lambda_catalog_permissions" {
  count       = var.enable_lakeformation ? 1 : 0
  principal   = aws_iam_role.lambda_execution.arn
  permissions = ["CREATE_DATABASE", "DESCRIBE", "ALTER"]

  catalog_resource = true
}

# ── Silver S3 data location access ───────────────────────────────

resource "aws_lakeformation_permissions" "glue_silver_data_location" {
  count       = var.enable_lakeformation ? 1 : 0
  principal   = aws_iam_role.glue_execution.arn
  permissions = ["DATA_LOCATION_ACCESS"]

  data_location {
    arn = aws_lakeformation_resource.silver[0].arn
  }

  depends_on = [aws_lakeformation_resource.silver]
}

# ── silver_product database (legacy echo-dev-* / echo-uat-* only) ──

resource "aws_glue_catalog_database" "silver_product" {
  count = var.enable_lakeformation && !local.is_uif_stack ? 1 : 0
  name  = "${local.glue_db_prefix}_silver_product"
}

resource "aws_lakeformation_permissions" "glue_silver_product_db" {
  count       = var.enable_lakeformation && !local.is_uif_stack ? 1 : 0
  principal   = aws_iam_role.glue_execution.arn
  permissions = ["DESCRIBE", "CREATE_TABLE", "ALTER", "DROP"]

  database {
    name = aws_glue_catalog_database.silver_product[0].name
  }

  depends_on = [aws_glue_catalog_database.silver_product]
}

resource "aws_lakeformation_permissions" "glue_silver_product_tables" {
  count       = var.enable_lakeformation && !local.is_uif_stack ? 1 : 0
  principal   = aws_iam_role.glue_execution.arn
  permissions = ["SELECT", "INSERT", "DELETE", "DESCRIBE", "ALTER", "DROP"]

  table {
    database_name = aws_glue_catalog_database.silver_product[0].name
    wildcard      = true
  }

  depends_on = [aws_lakeformation_permissions.glue_silver_product_db]
}

# ── silver_fipm database (legacy only) ───────────────────────────

resource "aws_glue_catalog_database" "silver_fipm" {
  count = var.enable_lakeformation && !local.is_uif_stack ? 1 : 0
  name  = "${local.glue_db_prefix}_silver_fipm"
}

resource "aws_lakeformation_permissions" "glue_silver_fipm_db" {
  count       = var.enable_lakeformation && !local.is_uif_stack ? 1 : 0
  principal   = aws_iam_role.glue_execution.arn
  permissions = ["DESCRIBE", "CREATE_TABLE", "ALTER", "DROP"]

  database {
    name = aws_glue_catalog_database.silver_fipm[0].name
  }

  depends_on = [aws_glue_catalog_database.silver_fipm]
}

resource "aws_lakeformation_permissions" "glue_silver_fipm_tables" {
  count       = var.enable_lakeformation && !local.is_uif_stack ? 1 : 0
  principal   = aws_iam_role.glue_execution.arn
  permissions = ["SELECT", "INSERT", "DELETE", "DESCRIBE", "ALTER", "DROP"]

  table {
    database_name = aws_glue_catalog_database.silver_fipm[0].name
    wildcard      = true
  }

  depends_on = [aws_lakeformation_permissions.glue_silver_fipm_db]
}

# ── silver_tep + silver_mdm (uif-echo-* greenfield only) ───────────

resource "aws_glue_catalog_database" "silver_tep" {
  count = var.enable_lakeformation && local.is_uif_stack ? 1 : 0
  name  = "${local.glue_db_prefix}_silver_tep"
}

resource "aws_lakeformation_permissions" "glue_silver_tep_db" {
  count       = var.enable_lakeformation && local.is_uif_stack ? 1 : 0
  principal   = aws_iam_role.glue_execution.arn
  permissions = ["DESCRIBE", "CREATE_TABLE", "ALTER", "DROP"]

  database {
    name = aws_glue_catalog_database.silver_tep[0].name
  }

  depends_on = [aws_glue_catalog_database.silver_tep]
}

resource "aws_lakeformation_permissions" "glue_silver_tep_tables" {
  count       = var.enable_lakeformation && local.is_uif_stack ? 1 : 0
  principal   = aws_iam_role.glue_execution.arn
  permissions = ["SELECT", "INSERT", "DELETE", "DESCRIBE", "ALTER", "DROP"]

  table {
    database_name = aws_glue_catalog_database.silver_tep[0].name
    wildcard      = true
  }

  depends_on = [aws_lakeformation_permissions.glue_silver_tep_db]
}

resource "aws_glue_catalog_database" "silver_mdm" {
  count = var.enable_lakeformation && local.is_uif_stack ? 1 : 0
  name  = "${local.glue_db_prefix}_silver_mdm"
}

resource "aws_lakeformation_permissions" "glue_silver_mdm_db" {
  count       = var.enable_lakeformation && local.is_uif_stack ? 1 : 0
  principal   = aws_iam_role.glue_execution.arn
  permissions = ["DESCRIBE", "CREATE_TABLE", "ALTER", "DROP"]

  database {
    name = aws_glue_catalog_database.silver_mdm[0].name
  }

  depends_on = [aws_glue_catalog_database.silver_mdm]
}

resource "aws_lakeformation_permissions" "glue_silver_mdm_tables" {
  count       = var.enable_lakeformation && local.is_uif_stack ? 1 : 0
  principal   = aws_iam_role.glue_execution.arn
  permissions = ["SELECT", "INSERT", "DELETE", "DESCRIBE", "ALTER", "DROP"]

  table {
    database_name = aws_glue_catalog_database.silver_mdm[0].name
    wildcard      = true
  }

  depends_on = [aws_lakeformation_permissions.glue_silver_mdm_db]
}

# ── Analyst access (PCMPowerUser) ─────────────────────────────────

resource "aws_lakeformation_permissions" "analyst_silver_product_db" {
  count       = var.enable_lakeformation && !local.is_uif_stack ? 1 : 0
  principal   = "arn:aws:iam::${var.aws_account_id}:role/PCMPowerUser"
  permissions = ["DESCRIBE", "CREATE_TABLE"]

  database {
    name = aws_glue_catalog_database.silver_product[0].name
  }
}

resource "aws_lakeformation_permissions" "analyst_silver_product_tables" {
  count       = var.enable_lakeformation && !local.is_uif_stack ? 1 : 0
  principal   = "arn:aws:iam::${var.aws_account_id}:role/PCMPowerUser"
  permissions = ["SELECT", "DESCRIBE"]

  table {
    database_name = aws_glue_catalog_database.silver_product[0].name
    wildcard      = true
  }

  depends_on = [aws_lakeformation_permissions.analyst_silver_product_db]
}

resource "aws_lakeformation_permissions" "analyst_silver_fipm_db" {
  count       = var.enable_lakeformation && !local.is_uif_stack ? 1 : 0
  principal   = "arn:aws:iam::${var.aws_account_id}:role/PCMPowerUser"
  permissions = ["DESCRIBE"]

  database {
    name = aws_glue_catalog_database.silver_fipm[0].name
  }
}

resource "aws_lakeformation_permissions" "analyst_silver_fipm_tables" {
  count       = var.enable_lakeformation && !local.is_uif_stack ? 1 : 0
  principal   = "arn:aws:iam::${var.aws_account_id}:role/PCMPowerUser"
  permissions = ["SELECT", "DESCRIBE"]

  table {
    database_name = aws_glue_catalog_database.silver_fipm[0].name
    wildcard      = true
  }

  depends_on = [aws_lakeformation_permissions.analyst_silver_fipm_db]
}

resource "aws_lakeformation_permissions" "analyst_silver_tep_db" {
  count       = var.enable_lakeformation && local.is_uif_stack ? 1 : 0
  principal   = "arn:aws:iam::${var.aws_account_id}:role/PCMPowerUser"
  permissions = ["DESCRIBE", "CREATE_TABLE"]

  database {
    name = aws_glue_catalog_database.silver_tep[0].name
  }
}

resource "aws_lakeformation_permissions" "analyst_silver_tep_tables" {
  count       = var.enable_lakeformation && local.is_uif_stack ? 1 : 0
  principal   = "arn:aws:iam::${var.aws_account_id}:role/PCMPowerUser"
  permissions = ["SELECT", "DESCRIBE"]

  table {
    database_name = aws_glue_catalog_database.silver_tep[0].name
    wildcard      = true
  }

  depends_on = [aws_lakeformation_permissions.analyst_silver_tep_db]
}

resource "aws_lakeformation_permissions" "analyst_silver_mdm_db" {
  count       = var.enable_lakeformation && local.is_uif_stack ? 1 : 0
  principal   = "arn:aws:iam::${var.aws_account_id}:role/PCMPowerUser"
  permissions = ["DESCRIBE"]

  database {
    name = aws_glue_catalog_database.silver_mdm[0].name
  }
}

resource "aws_lakeformation_permissions" "analyst_silver_mdm_tables" {
  count       = var.enable_lakeformation && local.is_uif_stack ? 1 : 0
  principal   = "arn:aws:iam::${var.aws_account_id}:role/PCMPowerUser"
  permissions = ["SELECT", "DESCRIBE"]

  table {
    database_name = aws_glue_catalog_database.silver_mdm[0].name
    wildcard      = true
  }

  depends_on = [aws_lakeformation_permissions.analyst_silver_mdm_db]
}
