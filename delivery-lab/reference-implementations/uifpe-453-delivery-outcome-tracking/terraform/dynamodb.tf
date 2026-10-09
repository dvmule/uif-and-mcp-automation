# ─────────────────────────────────────────────────────────────────────────────
# VERBATIM REPLICAS of the real eventing tables.
#
# Every key, index, projection_type and non_key_attributes list below is copied
# from stacks/event/infrastructure/terraform/dynamodb.tf. ONLY the `name`
# differs. Do not "tidy" a projection to ALL — the projections are the point.
#
# WHY: the previous POC projected ALL on every index and invented two indexes
# production does not have. That let POC code pass against the POC and fail
# against production, silently. Two concrete instances found 2026-09-30:
#
#   1. audit_consumer and event_handler both read row["published_at"] from a
#      gsi_event_id query. Production projects that index INCLUDE without
#      published_at, so the read returns nothing — a silent None where the
#      handlers expected a value.
#   2. The old delivery_writer queried publish-metrics.gsi_gold_run and
#      delivery-metrics.gsi_pending. Production has NEITHER, so that design
#      silently assumed two production schema additions nobody had costed.
#
# Consequence, accepted deliberately: handlers here must get published_at by
# GetItem on the base table using publish_id (GSI results always carry the base
# table keys), never off the index.
#
# A THIRD instance, found 2026-10-01, and the worst of them: the POC keyed
# Table 4 on {published_at}#{event_id} while production's dlq_recorder.py:144
# keys on {envelope["time"]}#{event_id}. Both POC writers shared the wrong
# derivation, so the suite passed — and in production the poller's DELIVERED
# rows would have landed on keys that could never collide with the recorder's
# FAILED rows. One delivery, two rows, and the conditional write that is
# supposed to let failure evidence win would have evaluated against an empty
# slot and never fired. published_at is now an ENRICHMENT attribute only; the
# key comes from the envelope, which both writers receive. See
# lambda_src/outcome_row.py invariants 1 and 2.
#
# hash_key/range_key raise a deprecation warning on AWS provider 6.x ("use
# key_schema instead"). They stay, because stacks/event uses them and a
# verbatim copy is the entire value of this file. Migrate both together or
# neither.
# ─────────────────────────────────────────────────────────────────────────────

# ── Table 3 replica: publish-metrics ────────────────────────────────────────
# The ledger. One row per (gold run x primary key x operation x version),
# written by the publisher after every PutEvents batch. event_id here is the
# ENVELOPE id, exactly as the real event_fabric_processor sets it — NOT the
# bus-assigned EventId the previous POCs wrongly stored. The bus id is not
# persisted at all: correlation comes from target_input in the bus logs.
resource "aws_dynamodb_table" "publish_metrics" {
  name         = local.t_publish
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "publish_id"

  attribute {
    name = "publish_id"
    type = "S"
  }

  attribute {
    name = "recovery_scope"
    type = "S"
  }

  attribute {
    name = "event_id"
    type = "S"
  }

  # SPARSE, partition-key only: recovery_scope ({domain}#{gold_run_id}) exists
  # only while status = PUBLISH_FAILED and is REMOVEd on publish.
  global_secondary_index {
    name               = "gsi_recovery"
    hash_key           = "recovery_scope"
    projection_type    = "INCLUDE"
    non_key_attributes = ["primary_key", "version", "operation", "event_run_id", "attempt_count"]
  }

  # NOTE published_at is deliberately NOT projected — matching production.
  # Handlers needing it must GetItem the base table by publish_id.
  global_secondary_index {
    name               = "gsi_event_id"
    hash_key           = "event_id"
    projection_type    = "INCLUDE"
    non_key_attributes = ["domain", "gold_run_id", "primary_key", "version", "operation", "event_run_id"]
  }

  ttl {
    attribute_name = "ttl"
    enabled        = true
  }
}

# ── Table 4 replica: delivery-metrics ───────────────────────────────────────
#   pk = {domain}#{customer_id}   sk = {published_at}#{event_id}
# Production's comment names the sk as {failed_at}#{event_id} because the table
# is failure-only TODAY; UIFPE-453 widens it to one row per event per consumer,
# so the sk's TIME COMPONENT becomes the ledger's published_at. The STRUCTURE
# is identical either way, which is why this stays a verbatim replica — only
# what the writers put in the string changes. Tracked as ADR-0025 §8 item 5.
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
    projection_type = "INCLUDE"
    non_key_attributes = [
      "domain", "primary_key", "operation", "detail_type",
      "status", "reason", "failed_at", "redrive_count",
    ]
  }

  # KEYS_ONLY in production. Enough for an idempotency probe, not enough to
  # read a row's status from — code must GetItem the base table for that.
  global_secondary_index {
    name            = "gsi_event_id"
    hash_key        = "event_id"
    projection_type = "KEYS_ONLY"
  }

  ttl {
    attribute_name = "ttl"
    enabled        = true
  }
}

# ── Table 5 replica: subscription-configs ───────────────────────────────────
# Routing source of truth: rule_arn -> customer + target. The stream is
# replicated even though this POC does not run the subscription provisioner,
# because its absence is what made the previous POC unable to exercise
# provisioning at all — a gap worth not re-creating.
resource "aws_dynamodb_table" "subscription_configs" {
  name             = local.t_subscriptions
  billing_mode     = "PAY_PER_REQUEST"
  stream_enabled   = true
  stream_view_type = "NEW_AND_OLD_IMAGES"
  hash_key         = "customer_id"
  range_key        = "domain_detail_type"

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

  attribute {
    name = "target_arn"
    type = "S"
  }

  # The delivery-poller's join: rule_arn off an INVOCATION_SUCCESS record ->
  # subscription row -> customer_id. ALL in production, so ALL here.
  # The poller caches this lookup at module scope (outcome_row), because one
  # invocation now handles a whole window and the table is near-static.
  global_secondary_index {
    name            = "gsi_rule_arn"
    hash_key        = "rule_arn"
    projection_type = "ALL"
  }

  # Fallback when a DLQ message lacks RULE_ARN.
  global_secondary_index {
    name            = "gsi_target_arn"
    hash_key        = "target_arn"
    projection_type = "ALL"
  }

  global_secondary_index {
    name               = "gsi_detail_type"
    hash_key           = "domain_detail_type"
    range_key          = "customer_id"
    projection_type    = "INCLUDE"
    non_key_attributes = ["status", "subscription_id"]
  }
}

# ── Seed one subscription row per customer ──────────────────────────────────
# The TABLE is a verbatim replica; until 2026-10-01 the seeded ROW was not — it
# carried 10 of the 22 attributes a provisioned row actually has. Compared
# field-for-field against the live pmc and pdz rows on product#product.updated.v1
# and brought into line, because this row is about to become load-bearing:
# target.target_type derives the channel, and
# target_config.retry_policy.max_age_seconds is what bounds the settling window
# for per-run reconciliation.
#
# subscription_id is a UUID, as the provisioner generates it — not a readable
# composite. policy_sid is "uif" + the UUID with dashes stripped, matching the
# live rows.
#
# target_config IS BUILT AS A RAW JSON STRING, not an HCL object literal, and
# that is deliberate. The SQS and Kinesis branches need DIFFERENTLY SHAPED
# attribute values for the same keys (dlq_arn is {S=...} for SQS but
# {NULL=true} for Kinesis; message_group_id only exists for SQS,
# partition_key_path only for Kinesis) — and for_each forces Terraform to
# compute ONE static type across every instance of this resource. Terraform's
# object-type unification then silently coerces the {NULL=true} branch's
# boolean into the STRING "true" to make both branches fit one shape, which
# DynamoDB's own item validator rejects: "unexpected raw attribute type
# (string) for data type descriptor: NULL". Confirmed in isolation — wrapping
# the branches in jsondecode(jsonencode(...)) does NOT avoid it, because
# merge()'s own result still needs one type across the for_each. Plain string
# concatenation has no object shape to unify, so jsondecode() on the fully
# assembled string is the one construction that survives both plan and apply.
locals {
  subscription_target_config_json = {
    for k, v in var.customers : k => join("", [
      "{",
      "\"consumer_principal_arn\":{\"S\":\"${v.consumer_principal_arn}\"},",
      "\"retry_policy\":{\"M\":{",
      "\"max_attempts\":{\"N\":\"${var.retry_max_attempts}\"},",
      "\"max_age_seconds\":{\"N\":\"${var.retry_max_age_seconds}\"}",
      "}},",
      v.target_type == "SQS" ? join("", [
        "\"message_group_id\":{\"NULL\":true},",
        "\"dlq_arn\":{\"S\":\"${aws_sqs_queue.subscription_dlq[k].arn}\"}",
        ]) : join("", [
        "\"partition_key_path\":{\"S\":\"$.detail.partitionkey\"},",
        "\"dlq_arn\":{\"NULL\":true}",
      ]),
      "}",
    ])
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
    operation          = { S = local.detail_type_operation }
    version            = { S = local.detail_type_version }
    subscription_id    = { S = each.value.subscription_id }
    policy_sid         = { S = "uif${replace(each.value.subscription_id, "-", "")}" }
    record_version     = { N = "1" }
    rule_name          = { S = aws_cloudwatch_event_rule.subscription[each.key].name }
    rule_arn           = { S = aws_cloudwatch_event_rule.subscription[each.key].arn }
    target_arn         = { S = each.value.target_type == "SQS" ? aws_sqs_queue.subscription[each.key].arn : aws_kinesis_stream.subscription[each.key].arn }
    status             = { S = "ACTIVE" }
    status_reason      = { NULL = true }
    created_at         = { S = "2026-10-01T00:00:00Z" }
    created_by         = { S = "terraform (UIFPE-453 poc)" }
    updated_at         = { S = "2026-10-01T00:00:00Z" }
    updated_by         = { S = "subscription-provisioner" }
    deleted_at         = { NULL = true }
    deleted_by         = { NULL = true }
    target = { M = {
      target_type = { S = each.value.target_type }
      target_arn  = { S = each.value.target_type == "SQS" ? aws_sqs_queue.subscription[each.key].arn : aws_kinesis_stream.subscription[each.key].arn }
      # See the locals block above this resource for why this is a decoded
      # JSON string rather than an HCL merge() of two differently-shaped
      # object literals. SQS: message_group_id (null unless FIFO) and the
      # SUBSCRIPTION QUEUE's redrive target — not the rule's dead-letter
      # target, which is the domain DLQ. The provisioner's Kinesis branch
      # creates no per-subscription DLQ (subscription_provisioner.py:389)
      # because a stream has no RedrivePolicy, and the live pdz row has
      # dlq_arn: null, which is also what eventbridge.tf's kinesis_target
      # already uses.
      target_config = { M = jsondecode(local.subscription_target_config_json[each.key]) }
    } }
  })
}

# ── Poller state (POC MACHINERY, not a replica) ─────────────────────────────
# TWO item kinds, distinguished by a key prefix, because the key schema is
# poller_id alone and a second table would have bought nothing:
#
#   poller_id = "bus-log-poll"            the CURSOR — how far through the bus
#                                         log group the poller has read. No ttl
#                                         attribute, so it never expires.
#   poller_id = "run#<poller>#<started>"  RUN HISTORY — one row per run,
#                                         INCLUDING runs that did not commit,
#                                         which is the gap it exists to close:
#                                         `last_run` on the cursor only
#                                         survives a committed run. TTL'd. It is NOT a copy of anything in
# stacks/event — the real stack has no such table, because the push design this
# replaces needed no cursor. If option 6 is adopted, this table (or an SSM
# parameter in its place) is a new production resource and must be costed as
# one.
#
# Correctness note: the watermark is what makes a missed log record
# RECOVERABLE — rewind it, or call the poller with an explicit window, and the
# same records are re-read and re-written idempotently. That is the capability
# the subscription-filter design could not offer at any price.
resource "aws_dynamodb_table" "poller_state" {
  name         = local.t_poller_state
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "poller_id"

  attribute {
    name = "poller_id"
    type = "S"
  }

  # TTL is enabled for the RUN HISTORY rows only. DynamoDB expires an item
  # solely when it HAS the ttl attribute, and the cursor item deliberately does
  # not — so the cursor never expires while run rows age out after
  # poll_run_history_days. Losing the cursor is not harmless: the poller would
  # cold-start and silently skip everything older than its lookback.
  ttl {
    attribute_name = "ttl"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = true
  }
}
