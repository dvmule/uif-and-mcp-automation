# Four stores behind the control panel API (lifecycle.py / registries.py
# swap their in-memory dicts for these tables; access only via the API's
# task role — the browser never touches AWS directly, D19).

resource "aws_dynamodb_table" "registries" {
  name         = local.ddb_tables.registries
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "registry" # sources | formats | channels | destinations
  range_key    = "id"

  attribute {
    name = "registry"
    type = "S"
  }
  attribute {
    name = "id"
    type = "S"
  }
}

resource "aws_dynamodb_table" "drafts" {
  name         = local.ddb_tables.drafts
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "id"

  attribute {
    name = "id"
    type = "S"
  }
  attribute {
    name = "status"
    type = "S"
  }

  global_secondary_index {
    name            = "by-status" # approvals queue = query status=submitted
    hash_key        = "status"
    projection_type = "ALL"
  }
}

resource "aws_dynamodb_table" "catalog" {
  name         = local.ddb_tables.catalog
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "job_id"
  range_key    = "version"

  attribute {
    name = "job_id"
    type = "S"
  }
  attribute {
    name = "version"
    type = "N"
  }
}

# Transform sizing, kept OUT of Terraform's hands at runtime: an operator
# retunes a slow export (more workers, longer timeout) by editing this item,
# no deploy. Terraform seeds the default row and owns nothing after that —
# hence ignore_changes on the item's value.
#
# The API reads this when starting a run and passes the values into the Glue
# job run as StartJobRun overrides, so the job definition's own worker/timeout
# settings are only the fallback for runs started outside the API.
resource "aws_dynamodb_table" "glue_job_config" {
  name         = local.ddb_tables.glue_job_config
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "config_key" # "default" today; an export id overrides it later

  attribute {
    name = "config_key"
    type = "S"
  }
}

resource "aws_dynamodb_table_item" "glue_job_config_default" {
  table_name = aws_dynamodb_table.glue_job_config.name
  hash_key   = aws_dynamodb_table.glue_job_config.hash_key

  item = jsonencode({
    config_key      = { S = "default" }
    worker_type     = { S = var.transform_worker_type }
    workers         = { N = tostring(var.transform_workers) }
    timeout_minutes = { N = tostring(var.transform_timeout_minutes) }
  })

  lifecycle {
    # Operators tune these live; Terraform must not drag them back to the
    # variable defaults on the next apply.
    ignore_changes = [item]
  }
}

# One schedule record per export (UIFPE-206). The record the UI reads;
# the armed thing is the matching EventBridge Scheduler schedule — the API's
# scheduler.py keeps the two in sync (Scheduler first, so this table never
# claims a schedule the service rejected). Lives OUTSIDE the immutable
# catalog versions (D22): changing a time never mints a version.
resource "aws_dynamodb_table" "schedules" {
  name         = local.ddb_tables.schedules
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "job_id"

  attribute {
    name = "job_id"
    type = "S"
  }
}

# Incremental extraction state (UIFPE-168): one row per export holding the
# stored high-water mark an incremental feed's next window starts from. The
# extraction MODE lives in the immutable config versions; this VALUE is
# runtime state (D22), like schedules. Written by the record_run Lambda on
# the success branch only (monotonic conditional advance); the API reads it
# at run start and deletes it on the panel's reset-watermark action (the next
# run then re-baselines with a full extract). No Terraform-seeded rows: an
# absent row IS the baseline state.
resource "aws_dynamodb_table" "extraction_state" {
  name         = local.ddb_tables.extraction_state
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "job_id"

  attribute {
    name = "job_id"
    type = "S"
  }
}

resource "aws_dynamodb_table" "runs" {
  name         = local.ddb_tables.runs
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "job_id"
  range_key    = "batch_id"

  attribute {
    name = "job_id"
    type = "S"
  }
  attribute {
    name = "batch_id"
    type = "S"
  }
}
