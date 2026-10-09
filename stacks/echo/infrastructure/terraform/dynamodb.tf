# ── glue_job_config ──────────────────────────────────────────────
# Stores one item per pipeline (PK=pipeline_id, SK=version).
# SK="latest" is the live config; older versions can be kept for audit.
resource "aws_dynamodb_table" "glue_job_config" {
  name         = "${local.legacy_name_prefix}-${local.config_table_slug}"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pipeline_id"
  range_key    = "version"

  attribute {
    name = "pipeline_id"
    type = "S"
  }
  attribute {
    name = "version"
    type = "S"
  }

  point_in_time_recovery { enabled = true }

  tags = {
    Project     = local.project_tag
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

# ── glue_job_metrics ─────────────────────────────────────────────
# One item per Glue job run. TTL auto-expires items after 90 days.
# GSI on pipeline_id lets the controller API query runs by pipeline
# without scanning the full table.
resource "aws_dynamodb_table" "glue_job_metrics" {
  name         = "${local.legacy_name_prefix}-${local.metrics_table_slug}"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "job_name"
  range_key    = "run_id"

  attribute {
    name = "job_name"
    type = "S"
  }
  attribute {
    name = "run_id"
    type = "S"
  }
  attribute {
    name = "pipeline_id"
    type = "S"
  }
  attribute {
    name = "started_at"
    type = "S"
  }

  ttl {
    attribute_name = "ttl"
    enabled        = true
  }

  global_secondary_index {
    name            = "pipeline_id-index"
    hash_key        = "pipeline_id"
    range_key       = "started_at"
    projection_type = "ALL"
  }

  point_in_time_recovery { enabled = true }

  tags = {
    Project     = local.project_tag
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

# The alert_config table (a single-item Slack/email/rules store the old Alerts
# page wrote) was retired at UIFPE-543 before it ever existed live. Who is
# emailed is now the gold root's alert-subscribers table, referenced by name
# in locals.tf.

# ── pipeline_analysis ────────────────────────────────────────────────
resource "aws_dynamodb_table" "pipeline_analysis" {
  name         = "${local.legacy_name_prefix}-pipeline-analysis"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "table_name"
  range_key    = "run_id"

  attribute {
    name = "table_name"
    type = "S"
  }
  attribute {
    name = "run_id"
    type = "S"
  }

  ttl {
    attribute_name = "ttl"
    enabled        = true
  }

  point_in_time_recovery { enabled = true }

  tags = {
    Project     = local.project_tag
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}
