# Consumed by humans and runbooks; domains do NOT read these (cross-state
# references would couple the domains to this state — they use the naming
# conventions instead, which is what keeps onboarding stack-change-free).

output "pipeline_configs_table" {
  value = aws_dynamodb_table.glue_configs.name
}

output "subscription_configs_table" {
  value = aws_dynamodb_table.subscription_configs.name
}

output "publish_glue_job" {
  value = aws_glue_job.event_fabric_processor.name
}

output "pipeline_controller_function" {
  value = aws_lambda_function.pipeline_controller.function_name
}

output "dlq_recorder_function" {
  value = aws_lambda_function.dlq_recorder.function_name
}

output "working_prefix" {
  value = "s3://${var.gold_artifacts_bucket}/${local.working_prefix}/"
}
