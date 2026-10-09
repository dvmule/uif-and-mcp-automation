# Outputs for the Echo stack.
# Centralized here to match stacks/beacon/infrastructure/terraform/outputs.tf.
# NOTE: infrastructure/deploy/uif-echo-*-deploy.sh read several of these BY NAME
# (artifacts_bucket, ui_bucket_name, api_endpoint, ui_cloudfront_id) - do not
# rename outputs without updating the deploy scripts in the same commit.

output "api_endpoint" {
  value       = aws_apigatewayv2_api.ingestion.api_endpoint
  description = "Base URL for the ingestion framework API (use this in the React UI)"
} # was: apigateway.tf

output "api_id" {
  value       = aws_apigatewayv2_api.ingestion.id
  description = "API Gateway HTTP API ID"
} # was: apigateway.tf

output "dq_check_config_table_name" {
  value       = aws_dynamodb_table.dq_check_config.name
  description = "Name of the DQ check config table — used by Lambda env vars"
} # was: data_quality.tf

output "dynamodb_endpoint_id" {
  value       = var.create_dynamodb_endpoint ? aws_vpc_endpoint.dynamodb[0].id : "pre-existing"
  description = "DynamoDB Gateway VPC Endpoint ID"
} # was: vpc.tf

output "gcs_replication_dashboard_url" {
  value       = "https://${var.aws_region}.console.aws.amazon.com/cloudwatch/home?region=${var.aws_region}#dashboards:name=${aws_cloudwatch_dashboard.gcs_replication.dashboard_name}"
  description = "CloudWatch dashboard for Bronze → GCS replication"
} # was: cloudwatch.tf

output "gcs_replicator_arn" {
  value       = aws_lambda_function.gcs_replicator.arn
  description = "ARN of the gcs_replicator Lambda — invoke manually in Phase 1"
} # was: lambda.tf

output "gcs_replicator_function_name" {
  value       = aws_lambda_function.gcs_replicator.function_name
  description = "Function name for `aws lambda invoke`"
} # was: lambda.tf

output "github_actions_role_arn" {
  description = "IAM role ARN for GitHub Actions (set as AWS_ROLE_ARN_<ENV> secret)"
  value       = aws_iam_role.github_actions.arn
} # was: iam.tf

output "glue_connection_name" {
  value       = aws_glue_connection.vpc_network.name
  description = "Glue connection name — reference this in any additional Glue jobs that need VPC access"
} # was: glue.tf

output "glue_eni_security_group_id" {
  value       = aws_security_group.glue_eni.id
  description = "Security group attached to Glue ENIs — add inbound rule on Oracle DB's SG to allow this"
} # was: glue.tf

# ── Outputs (consumed by glue_job.tf and eventbridge_rules.tf) ───
output "glue_role_arn" {
  value = aws_iam_role.glue_execution.arn
} # was: iam.tf

output "artifacts_bucket" {
  description = "S3 bucket name for Glue scripts (used by CI/CD to upload job files)"
  value       = aws_s3_bucket.artifacts.bucket
} # was: s3.tf

output "lambda_role_arn" {
  value = aws_iam_role.lambda_execution.arn
} # was: iam.tf

# ── Outputs (used by api_gateway.tf) ─────────────────────────────
output "pipeline_controller_arn" {
  value       = aws_lambda_function.pipeline_controller.arn
  description = "ARN of the pipeline_controller Lambda"
} # was: lambda.tf

output "pipeline_controller_invoke_arn" {
  value       = aws_lambda_function.pipeline_controller.invoke_arn
  description = "Invoke ARN used by API Gateway integration"
} # was: lambda.tf

# ── Outputs ───────────────────────────────────────────────────────
output "s3_endpoint_id" {
  value       = var.create_s3_endpoint ? aws_vpc_endpoint.s3[0].id : "pre-existing"
  description = "S3 Gateway VPC Endpoint ID"
} # was: vpc.tf

output "silver_bq_replication_dashboard_url" {
  value       = "https://${var.aws_region}.console.aws.amazon.com/cloudwatch/home?region=${var.aws_region}#dashboards:name=${aws_cloudwatch_dashboard.silver_bq_replication.dashboard_name}"
  description = "CloudWatch dashboard for Silver → BigQuery replication"
} # was: cloudwatch.tf

output "silver_bq_replicator_arn" {
  value       = aws_lambda_function.silver_bq_replicator.arn
  description = "ARN of the silver_bq_replicator Lambda — invoke manually for backfill"
} # was: lambda.tf

output "silver_bq_replicator_function_name" {
  value       = aws_lambda_function.silver_bq_replicator.function_name
  description = "Function name for `aws lambda invoke`"
} # was: lambda.tf

output "ui_bucket_name" {
  value       = aws_s3_bucket.ui.id
  description = "S3 bucket to deploy the React build into: aws s3 sync ui/dist s3://<bucket>/ --delete"
} # was: cloudfront.tf

output "ui_cloudfront_id" {
  value       = aws_cloudfront_distribution.ui.id
  description = "CloudFront distribution ID (used for cache invalidation after deploy)"
} # was: cloudfront.tf

output "ui_cloudfront_url" {
  value       = "https://${aws_cloudfront_distribution.ui.domain_name}"
  description = "CloudFront URL for the EDS UI"
} # was: cloudfront.tf

# Publishes local.data_config_prefix, which until now nothing referenced --
# tflint reported it as terraform_unused_declarations, the one finding failing
# that gate, and locals.tf carried a comment explaining why it was left alone
# rather than a fix.
#
# An output rather than a `tflint-ignore`, for two reasons. The suppression
# would have to sit on the line above the local, which splits `terraform fmt`'s
# alignment group and collapses the padding that
# qe/pipeline/test_dq_infra.py::test_uif_data_bucket_layout_in_locals asserts
# character for character -- trading a tflint failure for a pytest failure,
# which is how that local got its comment in the first place. And the fact it
# records has a real consumer: this root's own convention is that the deploy
# scripts read prefixes and bucket names from outputs rather than hardcoding
# them (stacks/echo/CLAUDE.md, "Deploy scripts read Terraform outputs - no
# hardcoded bucket names"), and uif-echo-dev-phase5-bootstrap.sh currently
# names `configs/` only in a comment, marked "when wired". This is the wire.
#
# The local's ternary returns the same value on both branches today, so this
# output is the same for a uif and a legacy stack. That is deliberate: the
# prefix has not diverged, and the branch is there so it can without a
# consumer changing.
output "data_config_prefix" {
  value       = local.data_config_prefix
  description = "Key prefix holding pipeline config objects in the data bucket, beside code/"
} # was: locals.tf
