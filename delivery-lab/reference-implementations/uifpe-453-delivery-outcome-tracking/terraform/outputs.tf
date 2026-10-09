output "bus_name" {
  value = aws_cloudwatch_event_bus.domain.name
}

output "bus_log_group" {
  description = "Where INVOCATION_SUCCESS records land — the success signal"
  value       = aws_cloudwatch_log_group.bus.name
}

output "bus_log_config" {
  value = {
    level          = var.bus_log_level
    include_detail = var.bus_log_include_detail
  }
}

output "tables" {
  value = {
    publish_metrics      = aws_dynamodb_table.publish_metrics.name
    delivery_metrics     = aws_dynamodb_table.delivery_metrics.name
    subscription_configs = aws_dynamodb_table.subscription_configs.name
    poller_state         = aws_dynamodb_table.poller_state.name
  }
}

output "subscription_queues" {
  value = { for k, q in aws_sqs_queue.subscription : k => q.url }
}

output "subscription_streams" {
  value = { for k, s in aws_kinesis_stream.subscription : k => s.name }
}

output "domain_dlq" {
  description = "The authoritative delivery-failure signal — every rule dead-letters here"
  value       = aws_sqs_queue.domain_dlq.url
}

output "subscription_dlqs" {
  description = "Subscription-queue redrive targets (consumer poison). Deliberately unread."
  value       = { for k, q in aws_sqs_queue.subscription_dlq : k => q.url }
}

output "rules" {
  value = { for k, r in aws_cloudwatch_event_rule.subscription : k => r.name }
}

output "lambdas" {
  value = {
    publisher       = aws_lambda_function.publisher.function_name
    event_handler   = aws_lambda_function.event_handler.function_name
    delivery_poller = aws_lambda_function.delivery_poller.function_name
  }
}

output "poll_schedule" {
  description = "How often the bus log group is read into delivery-metrics"
  value = {
    rule               = aws_cloudwatch_event_rule.poll_schedule.name
    rate               = var.poll_rate
    lag_seconds        = var.poll_lag_seconds
    max_window_seconds = var.poll_max_window_seconds
    catch_up_horizon   = "${var.bus_log_retention_days} days (bus log retention)"
  }
}
