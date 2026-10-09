# ── The ledger: every published event, source of the Table-4 sort key ────────
resource "aws_dynamodb_table" "publish_metrics" {
  name         = local.t_publish
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "publish_id"

  attribute {
    name = "publish_id"
    type = "S"
  }
  attribute {
    name = "event_id"
    type = "S"
  }
  attribute {
    name = "gold_run_id"
    type = "S"
  }

  global_secondary_index {
    name            = "gsi_event_id"
    hash_key        = "event_id"
    projection_type = "ALL"
  }
  global_secondary_index {
    name            = "gsi_gold_run"
    hash_key        = "gold_run_id"
    projection_type = "ALL"
  }
}

# ── Outcome rows — the Delivery Fabric is the SINGLE writer ──────────────────
# No gsi_pending, no ATTEMPTED state: rows are born DELIVERED (with the target
# API's acknowledgment attached) or FAILED (from the buffer DLQ path).
resource "aws_dynamodb_table" "delivery_metrics" {
  name         = local.t_delivery
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "domain_customer"
  range_key    = "delivery_id"

  attribute {
    name = "domain_customer"
    type = "S"
  }
  attribute {
    name = "delivery_id"
    type = "S"
  }
  attribute {
    name = "gold_run_id"
    type = "S"
  }
  attribute {
    name = "customer_id"
    type = "S"
  }
  attribute {
    name = "event_id"
    type = "S"
  }

  global_secondary_index {
    name            = "gsi_gold_run"
    hash_key        = "gold_run_id"
    range_key       = "customer_id"
    projection_type = "ALL"
  }
  global_secondary_index {
    name            = "gsi_event_id"
    hash_key        = "event_id"
    projection_type = "ALL"
  }

  ttl {
    attribute_name = "ttl"
    enabled        = true
  }
}

# ── Routing: rule_arn -> customer + real target ──────────────────────────────
resource "aws_dynamodb_table" "subscription_configs" {
  name         = local.t_subscriptions
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "customer_id"
  range_key    = "domain_detail_type"

  attribute {
    name = "customer_id"
    type = "S"
  }
  attribute {
    name = "domain_detail_type"
    type = "S"
  }
  attribute {
    name = "rule_arn"
    type = "S"
  }

  # The Delivery Fabric's join: the transformer-stamped rule ARN -> this row.
  global_secondary_index {
    name            = "gsi_rule_arn"
    hash_key        = "rule_arn"
    projection_type = "ALL"
  }
}

resource "aws_dynamodb_table_item" "subscription" {
  for_each   = var.customers
  table_name = aws_dynamodb_table.subscription_configs.name
  hash_key   = "customer_id"
  range_key  = "domain_detail_type"

  item = jsonencode({
    customer_id        = { S = each.key }
    domain_detail_type = { S = "${var.domain}#${var.detail_type}" }
    domain             = { S = var.domain }
    detail_type        = { S = var.detail_type }
    rule_name          = { S = aws_cloudwatch_event_rule.subscription[each.key].name }
    rule_arn           = { S = aws_cloudwatch_event_rule.subscription[each.key].arn }
    status             = { S = "ACTIVE" }
    target = { M = {
      target_type = { S = each.value.target_type }
      target_arn  = { S = each.value.target_type == "SQS" ? aws_sqs_queue.consumer[each.key].arn : aws_kinesis_stream.consumer[each.key].arn }
      target_config = { M = {
        partition_key_path = { S = "detail.data.ppid" }
      } }
    } }
  })
}
