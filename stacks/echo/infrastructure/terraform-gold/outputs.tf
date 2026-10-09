output "gold_transform_job" {
  value = aws_glue_job.generic_gold_transform.name
}

output "gold_reconcile_job" {
  value = aws_glue_job.generic_gold_reconcile.name
}

output "gold_config_table" {
  value = aws_dynamodb_table.gold_pipelines_config.name
}

output "gold_dispatch_function" {
  value = aws_lambda_function.gold_dispatcher.function_name
}

output "gold_alert_notify_function" {
  value = aws_lambda_function.gold_alert_notify.function_name
}

output "alert_subscribers_table" {
  value = aws_dynamodb_table.alert_subscribers.name
}

output "gold_dashboard" {
  value = aws_cloudwatch_dashboard.gold.dashboard_name
}

# The FIXED prefix, which is where the jobs read from until a release has staged
# them. Once one has, each job resolves its own content-addressed key instead
# (local.gold_glue_uri) and nothing is written here -- so treat this as the
# unstaged location, not as a claim about where a given job is reading from.
output "gold_scripts_prefix" {
  value = "s3://${local.glue_scripts_bucket}/${local.gold_scripts_prefix}/"
}
