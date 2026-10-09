# Gold operational statistics (UIFPE-544).
#
# One row per gold entity per day, written nightly into an Iceberg table in the
# catalog. The metrics rows the Syncs page reads expire after 90 days and the
# change logs are per entity, so "how did the fleet do last month" needs an
# engineer with Athena today. This writes the answer down once a night, where
# the Overview page (PR 2) and the monthly pack (PR 3) both read it.
#
# NAMING NOTE. catalog.md's gold form is uif_{stack}_{env}_gold_{domain}, and
# `ops` here occupies the {domain} slot for a dataset that belongs to no
# business domain - it is the platform's own. Stated rather than hidden: the
# alternative, a per-domain copy of the same fleet-wide table, would be worse.
# The table is `daily_stats`, entity-only, because the database already says
# gold and ops.

resource "aws_glue_catalog_database" "gold_ops" {
  name        = "${local.gold_db_prefix}_gold_ops"
  description = "Gold operational statistics: one row per entity per day (UIFPE-544)"
}

# The gold layer tag is how the shared Glue role reaches a database it did not
# create (gold_executor_access.tf grants CREATE_TABLE/ALTER/DROP/DESCRIBE and
# the table permissions by this tag). Without it the nightly job cannot create
# its own table, and the failure reads as a Lake Formation denial, not a
# missing tag.
resource "aws_lakeformation_resource_lf_tags" "gold_ops_database" {
  database {
    name = aws_glue_catalog_database.gold_ops.name
  }

  lf_tag {
    key   = local.gold_layer_tag_key
    value = var.environment
  }
}

locals {
  # One segment under the warehouse, as storage.md requires of a gold prefix.
  ops_stats_table    = "daily_stats"
  ops_stats_location = "s3://${var.gold_bucket}/daily_stats/"

  # This job's libraries. NOT local.gold_extra_py_files: it needs the stats
  # arithmetic and the cadence vocabulary, and none of the CDC machinery.
  ops_stats_py_files = join(",", [
    local.gold_glue_uri["gold_stats"],
    local.gold_glue_uri["gold_telemetry"],
    local.gold_glue_uri["gold_frequencies"],
  ])
}

resource "aws_s3_object" "gold_ops_summarize_script" {
  count  = local.glue_staged["gold_ops_summarize"] ? 0 : 1
  bucket = local.glue_scripts_bucket
  key    = "${local.gold_scripts_prefix}/gold_ops_summarize.py"
  source = "${path.module}/../../src/apps/pipeline/gold_ops_summarize.py"
  etag   = filemd5("${path.module}/../../src/apps/pipeline/gold_ops_summarize.py")
}

resource "aws_s3_object" "gold_stats_lib" {
  count  = local.glue_staged["gold_stats"] ? 0 : 1
  bucket = local.glue_scripts_bucket
  key    = "${local.gold_scripts_prefix}/gold_stats.py"
  source = "${path.module}/../../src/libs/gold_stats.py"
  etag   = filemd5("${path.module}/../../src/libs/gold_stats.py")
}

# The cadence vocabulary, already in the dispatcher's zip; the statistics need
# interval_minutes to judge freshness by the same rule the alarms use.
resource "aws_s3_object" "gold_frequencies_lib" {
  count  = local.glue_staged["gold_frequencies"] ? 0 : 1
  bucket = local.glue_scripts_bucket
  key    = "${local.gold_scripts_prefix}/gold_frequencies.py"
  source = "${path.module}/../../src/libs/gold_frequencies.py"
  etag   = filemd5("${path.module}/../../src/libs/gold_frequencies.py")
}

resource "aws_glue_job" "gold_ops_summarize" {
  # {operation} is a verb: this job summarises a day. `stats` would name the
  # output, not the work (00-core.md's three-slot note).
  name         = "${local.name_prefix}-gold-summarize"
  role_arn     = data.aws_iam_role.glue_execution.arn
  glue_version = "5.1"
  max_retries  = 1

  # Minutes of work over a few thousand DynamoDB rows and one small Iceberg
  # write per entity. The 90-day back-fill is the long case and still fits.
  timeout         = 60
  execution_class = "FLEX"

  command {
    name            = "glueetl"
    script_location = local.gold_glue_uri["gold_ops_summarize"]
    python_version  = "3"
  }

  worker_type       = "G.1X"
  number_of_workers = 2

  default_arguments = {
    "--enable-metrics"                   = "true"
    "--enable-spark-ui"                  = "false"
    "--enable-observability-metrics"     = "true"
    "--enable-job-insights"              = "true"
    "--enable-continuous-cloudwatch-log" = "true"
    "--job-language"                     = "python"
    "--TempDir"                          = "s3://${local.glue_scripts_bucket}/gold/tmp/"
    "--datalake-formats"                 = "iceberg"
    "--additional-python-modules"        = "pyyaml==6.0.1"
    "--extra-py-files"                   = local.ops_stats_py_files
    "--env"                              = var.environment
    "--CONFIG_TABLE"                     = aws_dynamodb_table.gold_pipelines_config.name
    "--METRICS_TABLE"                    = local.metrics_table_name
    "--GOLD_WAREHOUSE"                   = "s3://${var.gold_bucket}/"
    "--TRANSFORM_JOB_NAME"               = aws_glue_job.generic_gold_transform.name
    "--RECONCILE_JOB_NAME"               = aws_glue_job.generic_gold_reconcile.name
    "--STATS_DATABASE"                   = aws_glue_catalog_database.gold_ops.name
    "--STATS_TABLE"                      = local.ops_stats_table
    "--STATS_LOCATION"                   = local.ops_stats_location
    # The Overview page's 30-day file. The ARTIFACTS bucket, not the data
    # bucket: the controller already holds this name in its environment and
    # GetObject on it, so the Overview route needs no change to the latched
    # echo root, and the data bucket stays purely Iceberg tables.
    "--SUMMARY_BUCKET" = var.gold_artifacts_bucket
    # Yesterday, one day. The back-fill is a one-off run with --backfill_days.
    #
    # --day is DELIBERATELY ABSENT, not empty. Glue drops an empty value rather
    # than passing it as an argv token, so `"--day" = ""` left the flag next to
    # the orphaned value of a Glue-consumed --enable-* directive and argparse
    # bound day='true'. Every trigger run died on it (jr_203e4d89..., line 79,
    # 2026-09-24). Absent, the script falls back to yesterday, which is what
    # the nightly run wants; a back-fill passes --day explicitly.
    "--backfill_days" = "1"
  }

  execution_property {
    max_concurrent_runs = 1
  }

  tags = local.common_tags

  depends_on = [
    aws_s3_object.gold_ops_summarize_script,
    aws_s3_object.gold_stats_lib,
    aws_s3_object.gold_telemetry_lib,
    aws_s3_object.gold_frequencies_lib,
    aws_lakeformation_resource_lf_tags.gold_ops_database,
  ]
}

# A Glue trigger, not an EventBridge rule: EventBridge has no native
# StartJobRun target, and the deploy role has no scheduler:CreateSchedule
# (simulated 2026-09-24), so a Scheduler schedule cannot be applied. A Glue
# trigger is the purpose-built mechanism and needs no Lambda in between.
#
# 04:30 UTC: an hour after the nightly reconcile (03:30) so the day being
# summarised has every one of its rows, and before the working day in India.
resource "aws_glue_trigger" "gold_ops_summarize_nightly" {
  name        = "${local.name_prefix}-schedule-gold-summarize-daily"
  description = "Nightly gold statistics for yesterday (UIFPE-544) - 04:30 UTC, after the 03:30 reconcile"
  type        = "SCHEDULED"
  schedule    = "cron(30 4 * * ? *)"
  enabled     = true

  actions {
    job_name = aws_glue_job.gold_ops_summarize.name
  }

  tags = local.common_tags
}
