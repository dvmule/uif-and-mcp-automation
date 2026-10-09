output "ui_url" {
  value = "https://${aws_cloudfront_distribution.ui.domain_name}"
}

output "ui_bucket" {
  value = aws_s3_bucket.ui.bucket
}

output "data_bucket" {
  value = aws_s3_bucket.data.bucket
}

output "dynamodb_tables" {
  value = local.ddb_tables
}
