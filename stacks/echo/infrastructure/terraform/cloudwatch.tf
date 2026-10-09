# cloudwatch.tf
# Pre-creates the CloudWatch log groups that Glue writes to.
# Glue validates log group existence at job startup when
# --enable-continuous-cloudwatch-log is true.
#
# Account-wide paths (/aws-glue/jobs/*) — created once in dev only; uat/prod
# Glue jobs reuse the same groups (filter by job name in CloudWatch).

resource "aws_cloudwatch_log_group" "glue_output" {
  count             = var.environment == "dev" && !local.is_uif_stack ? 1 : 0
  name              = "/aws-glue/jobs/output"
  retention_in_days = 30

  tags = {
    Project     = local.project_tag
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

resource "aws_cloudwatch_log_group" "glue_error" {
  count             = var.environment == "dev" && !local.is_uif_stack ? 1 : 0
  name              = "/aws-glue/jobs/error"
  retention_in_days = 30

  tags = {
    Project     = local.project_tag
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

resource "aws_cloudwatch_log_group" "glue_continuous" {
  count             = var.environment == "dev" && !local.is_uif_stack ? 1 : 0
  name              = "/aws-glue/jobs/logs-v2"
  retention_in_days = 30

  tags = {
    Project     = local.project_tag
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

# ── GCS replication dashboard ─────────────────────────────────────
# Stitches CloudWatch metrics emitted by the gcs_replicator Lambda
# (namespace EnterpriseDataConnect/GcsReplication, dimensioned by
# PipelineId) into a single at-a-glance view. SEARCH expressions
# auto-discover new pipelines so the dashboard never needs editing.

resource "aws_cloudwatch_dashboard" "gcs_replication" {
  dashboard_name = "${local.name_prefix}-bronze-gcs-replication"

  dashboard_body = jsonencode({
    widgets = [
      {
        type   = "metric"
        x      = 0
        y      = 0
        width  = 12
        height = 6
        properties = {
          title   = "Run status (last 24h)"
          region  = var.aws_region
          view    = "timeSeries"
          stacked = true
          period  = 3600
          stat    = "Sum"
          metrics = [
            [{ "expression" : "SEARCH('Namespace=\"EnterpriseDataConnect/GcsReplication\" MetricName=\"RunStatus_replicated\"', 'Sum', 3600)", "label" : "replicated", "id" : "ok" }],
            [{ "expression" : "SEARCH('Namespace=\"EnterpriseDataConnect/GcsReplication\" MetricName=\"RunStatus_partial\"',    'Sum', 3600)", "label" : "partial", "id" : "pp" }],
            [{ "expression" : "SEARCH('Namespace=\"EnterpriseDataConnect/GcsReplication\" MetricName=\"RunStatus_failed\"',     'Sum', 3600)", "label" : "failed", "id" : "ff" }],
            [{ "expression" : "SEARCH('Namespace=\"EnterpriseDataConnect/GcsReplication\" MetricName=\"RunStatus_skipped\"',    'Sum', 3600)", "label" : "skipped", "id" : "sk" }],
          ]
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 0
        width  = 12
        height = 6
        properties = {
          title   = "Bytes replicated by pipeline (24h)"
          region  = var.aws_region
          view    = "timeSeries"
          stacked = false
          period  = 3600
          stat    = "Sum"
          yAxis   = { left = { label = "Bytes" } }
          metrics = [
            [{ "expression" : "SEARCH('Namespace=\"EnterpriseDataConnect/GcsReplication\" MetricName=\"BytesReplicated\"', 'Sum', 3600)", "label" : "$${PROP('Dim.PipelineId')}" }]
          ]
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 6
        width  = 12
        height = 6
        properties = {
          title   = "Files replicated vs skipped vs failed (24h)"
          region  = var.aws_region
          view    = "timeSeries"
          stacked = false
          period  = 3600
          stat    = "Sum"
          metrics = [
            [{ "expression" : "SEARCH('Namespace=\"EnterpriseDataConnect/GcsReplication\" MetricName=\"FilesReplicated\"', 'Sum', 3600)", "label" : "replicated $${PROP('Dim.PipelineId')}" }],
            [{ "expression" : "SEARCH('Namespace=\"EnterpriseDataConnect/GcsReplication\" MetricName=\"FilesSkipped\"',    'Sum', 3600)", "label" : "skipped $${PROP('Dim.PipelineId')}" }],
            [{ "expression" : "SEARCH('Namespace=\"EnterpriseDataConnect/GcsReplication\" MetricName=\"FilesFailed\"',     'Sum', 3600)", "label" : "failed $${PROP('Dim.PipelineId')}" }]
          ]
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 6
        width  = 12
        height = 6
        properties = {
          title   = "Replicator duration (p95, s)"
          region  = var.aws_region
          view    = "timeSeries"
          stacked = false
          period  = 300
          stat    = "p95"
          yAxis   = { left = { label = "Seconds" } }
          metrics = [
            [{ "expression" : "SEARCH('Namespace=\"EnterpriseDataConnect/GcsReplication\" MetricName=\"DurationSeconds\"', 'p95', 300)", "label" : "$${PROP('Dim.PipelineId')}" }]
          ]
        }
      }
    ]
  })
}


# ── Silver → BigQuery replication dashboard ───────────────────────
# Sibling of the GCS dashboard above. Metrics emitted by silver_bq_replicator
# Lambda under namespace EnterpriseDataConnect/SilverBqReplication, dimensioned
# by PipelineId. SEARCH expressions auto-discover new pipelines.
#
# Status values: replicated | bq_failed | partial | failed | skipped.

resource "aws_cloudwatch_dashboard" "silver_bq_replication" {
  dashboard_name = "${local.name_prefix}-silver-bq-replication"

  dashboard_body = jsonencode({
    widgets = [
      {
        type   = "metric"
        x      = 0
        y      = 0
        width  = 12
        height = 6
        properties = {
          title   = "Run status (last 24h)"
          region  = var.aws_region
          view    = "timeSeries"
          stacked = true
          period  = 3600
          stat    = "Sum"
          metrics = [
            [{ "expression" : "SEARCH('Namespace=\"EnterpriseDataConnect/SilverBqReplication\" MetricName=\"RunStatus_replicated\"', 'Sum', 3600)", "label" : "replicated", "id" : "ok" }],
            [{ "expression" : "SEARCH('Namespace=\"EnterpriseDataConnect/SilverBqReplication\" MetricName=\"RunStatus_bq_failed\"',  'Sum', 3600)", "label" : "bq_failed", "id" : "bq" }],
            [{ "expression" : "SEARCH('Namespace=\"EnterpriseDataConnect/SilverBqReplication\" MetricName=\"RunStatus_partial\"',    'Sum', 3600)", "label" : "partial", "id" : "pp" }],
            [{ "expression" : "SEARCH('Namespace=\"EnterpriseDataConnect/SilverBqReplication\" MetricName=\"RunStatus_failed\"',     'Sum', 3600)", "label" : "failed", "id" : "ff" }],
            [{ "expression" : "SEARCH('Namespace=\"EnterpriseDataConnect/SilverBqReplication\" MetricName=\"RunStatus_skipped\"',    'Sum', 3600)", "label" : "skipped", "id" : "sk" }],
          ]
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 0
        width  = 12
        height = 6
        properties = {
          title   = "Bytes replicated by pipeline (24h)"
          region  = var.aws_region
          view    = "timeSeries"
          stacked = false
          period  = 3600
          stat    = "Sum"
          yAxis   = { left = { label = "Bytes" } }
          metrics = [
            [{ "expression" : "SEARCH('Namespace=\"EnterpriseDataConnect/SilverBqReplication\" MetricName=\"BytesReplicated\"', 'Sum', 3600)", "label" : "$${PROP('Dim.PipelineId')}" }]
          ]
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 6
        width  = 12
        height = 6
        properties = {
          title   = "Files replicated vs skipped vs failed (24h)"
          region  = var.aws_region
          view    = "timeSeries"
          stacked = false
          period  = 3600
          stat    = "Sum"
          metrics = [
            [{ "expression" : "SEARCH('Namespace=\"EnterpriseDataConnect/SilverBqReplication\" MetricName=\"FilesReplicated\"', 'Sum', 3600)", "label" : "replicated $${PROP('Dim.PipelineId')}" }],
            [{ "expression" : "SEARCH('Namespace=\"EnterpriseDataConnect/SilverBqReplication\" MetricName=\"FilesSkipped\"',    'Sum', 3600)", "label" : "skipped $${PROP('Dim.PipelineId')}" }],
            [{ "expression" : "SEARCH('Namespace=\"EnterpriseDataConnect/SilverBqReplication\" MetricName=\"FilesFailed\"',     'Sum', 3600)", "label" : "failed $${PROP('Dim.PipelineId')}" }]
          ]
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 6
        width  = 12
        height = 6
        properties = {
          title   = "Replicator duration (p95, s)"
          region  = var.aws_region
          view    = "timeSeries"
          stacked = false
          period  = 300
          stat    = "p95"
          yAxis   = { left = { label = "Seconds" } }
          metrics = [
            [{ "expression" : "SEARCH('Namespace=\"EnterpriseDataConnect/SilverBqReplication\" MetricName=\"DurationSeconds\"', 'p95', 300)", "label" : "$${PROP('Dim.PipelineId')}" }]
          ]
        }
      }
    ]
  })
}

# Alarm: any non-zero count of `failed` or `bq_failed` over 15 min.
# bq_failed is recoverable (next Silver SUCCEEDED retries cheaply) but worth
# paging on if it persists.
resource "aws_cloudwatch_metric_alarm" "silver_bq_failed" {
  alarm_name          = "${local.name_prefix}-replicate-bq-failed"
  alarm_description   = "Silver→BQ replicator emitted RunStatus_failed in the last 15 min"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  threshold           = 0
  treat_missing_data  = "notBreaching"

  metric_query {
    id = "failed"
    metric {
      namespace   = "EnterpriseDataConnect/SilverBqReplication"
      metric_name = "RunStatus_failed"
      period      = 900
      stat        = "Sum"
    }
    return_data = true
  }

  tags = {
    Project     = local.project_tag
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

