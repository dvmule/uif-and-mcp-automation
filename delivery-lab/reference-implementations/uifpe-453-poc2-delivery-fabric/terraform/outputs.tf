output "bus_name" {
  value = aws_cloudwatch_event_bus.domain.name
}

output "tables" {
  value = {
    publish_metrics      = aws_dynamodb_table.publish_metrics.name
    delivery_metrics     = aws_dynamodb_table.delivery_metrics.name
    subscription_configs = aws_dynamodb_table.subscription_configs.name
  }
}

output "buffer_queue" {
  value = aws_sqs_queue.buffer.url
}

output "buffer_dlq" {
  value = aws_sqs_queue.buffer_dlq.url
}

output "consumer_queues" {
  value = { for k, q in aws_sqs_queue.consumer : k => q.url }
}

output "consumer_streams" {
  value = { for k, s in aws_kinesis_stream.consumer : k => s.name }
}

output "lambdas" {
  value = {
    delivery_fabric = aws_lambda_function.delivery_fabric.function_name
    failed_writer   = aws_lambda_function.failed_writer.function_name
    test_publisher  = aws_lambda_function.test_publisher.function_name
  }
}
