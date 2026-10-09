output "bus_name" {
  value = aws_cloudwatch_event_bus.this.name
}

output "bus_arn" {
  value = aws_cloudwatch_event_bus.this.arn
}

output "dlq_arn" {
  value = aws_sqs_queue.dlq.arn
}

output "mapping_key" {
  description = "S3 key of the uploaded mapping - the value the domain's pipeline-configs record passes as --mapping_key."
  value       = aws_s3_object.mapping.key
}

output "subscription_seeds_key" {
  description = "S3 key of the uploaded bootstrap seeds ('' when none) - the --bootstrap_subscriptions_key value."
  value       = var.subscription_seeds_source != "" ? aws_s3_object.subscription_seeds[0].key : ""
}
