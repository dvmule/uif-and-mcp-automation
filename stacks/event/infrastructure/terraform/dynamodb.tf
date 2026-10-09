# Shared eventing tables (UIFPE-184) - ONE instance serves every domain.
# None of the names carry a domain and every key schema already does
# (pipelineid = event-{env}-{goldId} - the gold ENTITY, not the domain
# (UIFPE-474); the two coincided for every pre-474 row, so no migration -
# publish_id leads with domain,
# domain_customer, domain#detail_type), so onboarding a domain writes ROWS
# here (its own config seed / subscriptions), never resources.
# Moved from domains/product/infrastructure/terraform/product_events.tf
# (2026-09-21) - adopted via imports.tf, NOT recreated: live data.


# ── Pipeline master config: event-<env>-glue-configs ────────────────────────
# One config document per pipeline (keyed pipelineid + version, "latest" is
# the live pointer): which Glue job processes it, the delta table and its
# columns, eventing names and per-operation detail types, publish/skip
# rules, worker sizing, and batch limits. Seeded as config-as-code below —
# edit the item HERE and apply; never hand-edit in the console.
resource "aws_dynamodb_table" "glue_configs" {
  # uif-<stack>-<env>-pipeline-configs; the stack is "event". Renamed from
  # event-<env>-glue-configs (2026-09-03) - a name change replaces the table
  # and the seeded item is re-created by apply.
  name         = "uif-event-${var.environment}-pipeline-configs"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pipelineid"
  range_key    = "version"

  attribute {
    name = "pipelineid"
    type = "S"
  }

  attribute {
    name = "version"
    type = "S"
  }

  tags = local.common_tags
}

# ── Pipeline metrics: uif-event-<env>-pipeline-metrics ──────────────────────
# One row per EVENTING run at the OVERALL grain, keyed by the executing
# run's own id (Glue JobRunId for INCREMENTAL/REPLAY, rd_<uuid> for
# REDRIVE) + mode. Run-level summary only - per-record detail is
# publish-metrics' (future per-event) job. Contract, writers, status
# vocabulary (IN_PROGRESS -> STARTED -> SUCCEEDED | FAILED |
# PARTIAL_FAILURE) and the idempotency caveat:
# ../../src/events/docs/pipeline-metrics.md. Writers (Domain Controller,
# processor terminal update, Event Replayer, Event Handler) are wired in a
# follow-up - today's run summary still lands on publish-metrics' OVERALL
# row until that migration.
resource "aws_dynamodb_table" "pipeline_metrics" {
  name         = "uif-event-${var.environment}-pipeline-metrics"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "event_run_id"
  range_key    = "mode"

  attribute {
    name = "event_run_id"
    type = "S"
  }

  attribute {
    name = "mode"
    type = "S"
  }

  attribute {
    name = "gold_run_id"
    type = "S"
  }

  # SPARSE by construction: REDRIVE rows carry no gold_run_id, so they never
  # enter this index - "has this gold run been processed?" can't match a
  # redrive. mode as range key narrows to the pipeline that ran (Query
  # gold_run_id = X AND mode = INCREMENTAL). Projection covers the
  # idempotency check (status) and the ops view without dragging error text
  # or the versions maps into the index.
  global_secondary_index {
    name               = "gsi_gold_run"
    hash_key           = "gold_run_id"
    range_key          = "mode"
    projection_type    = "INCLUDE"
    non_key_attributes = ["domain", "operation", "status", "started_at", "completed_at", "started_by"]
  }

  ttl {
    attribute_name = "ttl"
    enabled        = true
  }

  tags = local.common_tags
}

# ── Publish metrics: uif-event-<env>-publish-metrics ────────────────────────
# RESHAPED 2026-09-17 (Table 3): one row per BUS PUBLISH ATTEMPT at the
# grain domain x gold run x record x operation x version - PUBLISHED and
# PUBLISH_FAILED both. Single hash key publish_id =
# {domain}#{gold_run_id}#{primary_key}#{operation}#{version} (no sort key:
# point reads only; range access via the GSIs). Written by the Glue
# eventing job after every PutEvents batch, updated in place by recovery,
# read-only for the DLQ Lambda. The gzip'd payload (Binary) is the replay
# source of truth and is excluded from all GSI projections. Full contract:
# ../../src/events/docs/publish-metrics.md.
# NOTE the key change REPLACES the table: the previous run-level OVERALL
# rows (pk + operation) are gone, and the deployed writers' old-shape
# metrics writes fail benignly (best-effort by design) until the writer
# migration lands - run summaries move to pipeline-metrics (Table 2),
# per-event rows land here.
resource "aws_dynamodb_table" "glue_metrics" {
  name         = "uif-event-${var.environment}-publish-metrics"
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

  # SPARSE, partition-key only: recovery_scope ({domain}#{gold_run_id}) is
  # present only while status = PUBLISH_FAILED and REMOVEd on publish, so
  # this index holds exactly the outstanding failures for a gold run.
  global_secondary_index {
    name               = "gsi_recovery"
    hash_key           = "recovery_scope"
    projection_type    = "INCLUDE"
    non_key_attributes = ["primary_key", "version", "operation", "event_run_id", "attempt_count"]
  }

  # DLQ Lambda: correlate a failed delivery's event id back to its lineage
  # (read-only; its writes go to delivery-metrics).
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

  tags = local.common_tags
}

# ── Delivery metrics: uif-event-<env>-delivery-metrics ───────────────────────
# One row per event that FAILED delivery from the event bus to a customer
# target (inserted by the DLQ Lambda, updated by the redrive Lambda - both
# upcoming; full attribute/write-rule contract:
# ../../src/events/docs/delivery-metrics.md).
#   pk = {domain}#{customer_id}   sk = {failed_at}#{event_id}
# so one Query returns a customer's outstanding failures oldest-first.
# status FAILED -> REPLAYED (redrive) / ABANDONED (future scope); ttl is set
# only on terminal states (+90d), so outstanding failures never expire.
# The gzip'd original payload lives ONLY on the base table - both GSIs
# project without it by design.
resource "aws_dynamodb_table" "delivery_metrics" {
  name         = "uif-event-${var.environment}-delivery-metrics"
  billing_mode = "PAY_PER_REQUEST"
  # 2026-09-17 spec: named keys - domain_customer = {domain}#{customer_id},
  # delivery_id = {failed_at}#{event_id} (time-ordered, oldest first).
  hash_key  = "domain_customer"
  range_key = "delivery_id"

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

  # Ops: which customers are still owed events from gold run X.
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

  # Idempotent DLQ processing: skip the insert if the event already has a row.
  global_secondary_index {
    name            = "gsi_event_id"
    hash_key        = "event_id"
    projection_type = "KEYS_ONLY"
  }

  ttl {
    attribute_name = "ttl"
    enabled        = true
  }

  tags = local.common_tags
}

# ── Subscription configs: uif-event-<env>-subscription-configs ───────────────
# THE single source of truth for what a customer is subscribed to, at what
# version, and where events are delivered - one row per subscription
# (customer x detail-type x target); full contract:
# ../../src/events/docs/subscription-configs.md. clients.json is REMOVED
# (2026-09-17): sample-data/subscriptions.json seeds this table via
# GENERATED aws_dynamodb_table_item rows in
# product_events_clients.generated.tf, and the same source still compiles
# the interim delivery rules until the subscription provisioner creates
# rules from table rows.
#   pk = {customer_id}   sk = {domain}#{detail_type}
resource "aws_dynamodb_table" "subscription_configs" {
  name         = "uif-event-${var.environment}-subscription-configs"
  billing_mode = "PAY_PER_REQUEST"
  # The provisioner consumes this stream: PENDING rows are provisioned,
  # DELETING rows torn down, SUSPENDED/ACTIVE transitions toggle the rule.
  stream_enabled   = true
  stream_view_type = "NEW_AND_OLD_IMAGES"
  # 2026-09-17 spec: the partition key holds the customer code directly (no
  # flat copy attribute); the range key prefixes detail_type with the domain
  # so begins_with() filters to one domain or one operation.
  hash_key  = "customer_id"
  range_key = "domain_detail_type"

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

  # DLQ Lambda: RULE_ARN on the dead-lettered message -> subscription row.
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

  # Ops: "who is subscribed to product.created.v1" - deprecation notices,
  # redrive fan-out checks (projection: status is what those checks read).
  global_secondary_index {
    name               = "gsi_detail_type"
    hash_key           = "domain_detail_type"
    range_key          = "customer_id"
    projection_type    = "INCLUDE"
    non_key_attributes = ["status", "subscription_id"]
  }

  tags = local.common_tags
}

# ── Run registry: every announced run, STARTED → COMPLETED/FAILED, attempts
#    incremented on reprocess — the reprocessing hook ───────────────────────

resource "aws_dynamodb_table" "event_runs" {
  name         = "${local.event_stack_prefix}-run-registry"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "run_id"

  attribute {
    name = "run_id"
    type = "S"
  }

  tags = local.common_tags
}
