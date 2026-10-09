# Gold monthly leadership pack (UIFPE-544 PR 3).
#
# daily_stats answers "what happened on this day to this table". Once a month
# leadership asks a different question per domain - tables served, rows served,
# freshness attainment, failed runs, compute used - and answering it today means
# an engineer writing Athena queries by hand. This writes one CSV instead.
#
# Two grains in one file, domain and entity, distinguished by a `grain` column
# (owner decision 2026-09-24): leadership reads the domain rows, an engineer
# drills into the entity rows, and because both are accumulated from the same
# daily rows they cannot disagree. The job asserts that before it writes.
#
# NOT in the pack, deliberately: events published and delivered, because no
# durable record of them exists (UIFPE-572); and DPU-hours and cost, because
# capacity varies per run and lives only in Glue's own run history, and a price
# in code goes stale (owner decision 2026-09-24, follow-up to be raised). The
# pack reports compute MINUTES, which comes from the rows already read.

locals {
  # gold_report imports gold_stats, which imports gold_telemetry, so all three
  # ship. gold_stats.py and gold_telemetry.py are uploaded by ops_stats.tf and
  # glue_jobs.tf; only the report library is this file's to upload.
  ops_report_py_files = join(",", [
    local.gold_glue_uri["gold_report"],
    local.gold_glue_uri["gold_stats"],
    local.gold_glue_uri["gold_telemetry"],
  ])
}

resource "aws_s3_object" "gold_ops_export_script" {
  count  = local.glue_staged["gold_ops_export"] ? 0 : 1
  bucket = local.glue_scripts_bucket
  key    = "${local.gold_scripts_prefix}/gold_ops_export.py"
  source = "${path.module}/../../src/apps/pipeline/gold_ops_export.py"
  etag   = filemd5("${path.module}/../../src/apps/pipeline/gold_ops_export.py")
}

resource "aws_s3_object" "gold_report_lib" {
  count  = local.glue_staged["gold_report"] ? 0 : 1
  bucket = local.glue_scripts_bucket
  key    = "${local.gold_scripts_prefix}/gold_report.py"
  source = "${path.module}/../../src/libs/gold_report.py"
  etag   = filemd5("${path.module}/../../src/libs/gold_report.py")
}

resource "aws_glue_job" "gold_ops_export" {
  # {operation} is a verb, and `export` is in the standard's operation
  # vocabulary. The {layer} token keeps it clear of the partner export.
  name         = "${local.name_prefix}-gold-export"
  role_arn     = data.aws_iam_role.glue_execution.arn
  glue_version = "5.1"
  max_retries  = 1

  # One month of daily_stats is a few hundred rows and one small CSV.
  timeout         = 30
  execution_class = "FLEX"

  command {
    name            = "glueetl"
    script_location = local.gold_glue_uri["gold_ops_export"]
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
    "--extra-py-files"                   = local.ops_report_py_files
    "--env"                              = var.environment
    "--GOLD_WAREHOUSE"                   = "s3://${var.gold_bucket}/"
    "--STATS_DATABASE"                   = aws_glue_catalog_database.gold_ops.name
    "--STATS_TABLE"                      = local.ops_stats_table
    # The artifacts bucket, as the Overview's file uses: the reporting prefix
    # belongs with the other artifacts, not in the Iceberg data bucket.
    "--REPORT_BUCKET" = var.gold_artifacts_bucket
    # --month is DELIBERATELY ABSENT, not empty: Glue drops an empty value
    # rather than passing it as an argv token, and that cost the nightly job
    # every one of its runs. Absent, the script reports the month that just
    # ended, which is what a monthly run wants.
  }

  execution_property {
    max_concurrent_runs = 1
  }

  tags = local.common_tags

  depends_on = [
    aws_s3_object.gold_ops_export_script,
    aws_s3_object.gold_report_lib,
    aws_s3_object.gold_stats_lib,
    aws_s3_object.gold_telemetry_lib,
    aws_lakeformation_resource_lf_tags.gold_ops_database,
  ]
}

# A Glue trigger, for the same reason as the nightly one: EventBridge has no
# native StartJobRun target and the deploy role holds no scheduler permissions.
#
# 06:00 UTC on the 1st, an hour and a half after the nightly statistics run, so
# the last day of the month being reported is already in the table.
#
# NAMING DEVIATION, flagged rather than hidden: 00-core.md's {cadence} slot is
# CLOSED to 30min, 4hr, daily and hourly, and says to flag a value outside it
# rather than invent a token. `monthly` is outside it. The same gap applies to
# `weekly` (UIFPE-532). Needs a decision or an explicit exemption on the
# standard; the alternative, forcing this into `daily`, would name the resource
# after something it does not do.
resource "aws_glue_trigger" "gold_ops_export_monthly" {
  name        = "${local.name_prefix}-schedule-gold-export-monthly"
  description = "Monthly gold leadership pack for the month that just ended (UIFPE-544) - 06:00 UTC on the 1st"
  type        = "SCHEDULED"
  schedule    = "cron(0 6 1 * ? *)"
  enabled     = true

  actions {
    job_name = aws_glue_job.gold_ops_export.name
  }

  tags = local.common_tags
}
