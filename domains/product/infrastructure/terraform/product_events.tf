# Product's slice of the EVENT STACK (UIFPE-184 stack split, 2026-09-21).
#
# The eventing PLATFORM — the pipeline controller, the shared publish Glue
# job, the subscription provisioner, the DLQ recorder, the six shared tables
# and the GoldMergeCompleted trigger — moved to stacks/event: it was domain-
# agnostic by design (table keys carry the domain, IAM wildcards on the
# naming conventions) and now lives once. What stays HERE is exactly what is
# product's to own:
#
#   1. the event-domain module instance — product's bus, delivery DLQ
#      (+ alarm), the shared DLQ recorder's mapping on that DLQ, and the
#      uploads of product's mapping + bootstrap subscription seeds;
#   2. the pipeline-configs SEED ROW — every product-specific fact the
#      shared controller/processor need (gold database, mapping key, event
#      type, business keys, sizing, ...) as DATA in the shared table;
#   3. the migration bookkeeping: moved blocks into the module, and
#      removed { destroy = false } blocks that hand the platform resources
#      to stacks/event WITHOUT touching AWS (adopted there via imports.tf —
#      ordered runbook in stacks/event/MIGRATION.md).
#
# Onboarding another domain copies THIS FILE's shape (module + seed row),
# never edits stacks/event.

# ── Domain configuration ─────────────────────────────────────────────────────

# Used by marketing_copy_service.tf (the domain's own Athena queries); the
# event stack's copy of this variable moved to stacks/event.
variable "athena_workgroup" {
  type        = string
  description = "Athena workgroup for this domain's own query paths."
  default     = "primary"
}

variable "gold_glue_database" {
  type        = string
  description = "Glue Data Catalog database holding the gold product table. Empty = the platform convention uif_echo_{env}_gold_product."
  default     = ""
}

variable "gold_glue_table" {
  type        = string
  description = "Gold table queried (via Athena) for a run's products."
  default     = "product"
}

variable "event_source_uri" {
  type        = string
  description = "CloudEvents `source` attribute (urn:pearson:svc:<service>, guidelines.md §1)."
  default     = "urn:pearson:svc:product-change-pipeline"
}

variable "event_dataschema_uri" {
  type        = string
  description = "CloudEvents `dataschema` attribute — `{operation}` is filled per change verdict."
  default     = "https://schemas.pearson.com/product/product-{operation}-data/v1"
}

# Pending cloudwatch:PutMetricAlarm for the deploy role: gated so the apply
# stays green; flip in envs/<env>.tfvars once the grant lands.
variable "enable_dlq_alarm" {
  type        = bool
  description = "Create the DLQ non-zero-depth alarm (needs cloudwatch:PutMetricAlarm on the deploy role)."
  default     = false
}

# RETIRED (generated channel rules, 2026-09-17) — declared so stray tfvars
# values cannot fail an apply; unused otherwise.
variable "api_base_url" {
  type        = string
  description = "RETIRED - was the base URL for generated client API destinations."
  default     = "https://example.com"
}

variable "api_channel_api_key" {
  type        = string
  description = "RETIRED - was the x-api-key for the shared client API connection."
  default     = "placeholder-api-key"
  sensitive   = true
}

locals {
  event_stack_prefix = "uif-event-${var.environment}"
  # `{operation}` is filled per change verdict: INSERT -> created, otherwise updated.
  event_type = "com.pearson.product.product.{operation}.v1"
  # Gold databases follow the UIF standard uif_echo_{env}_{layer}_{domain}.
  product_gold_glue_database = var.gold_glue_database != "" ? var.gold_glue_database : "${local.gold_db_prefix}_gold_product"
}

# ── 1. Product's eventing plane: bus, DLQ, recorder hook, data uploads ──────

module "product_events" {
  source = "../../../../stacks/event/infrastructure/terraform/modules/event-domain"

  domain           = "product"
  environment      = var.environment
  artifacts_bucket = var.gold_artifacts_bucket
  # The registered gold mapping IS the channel-gating rule (single source of
  # truth); an edit reaches the next run on apply.
  mapping_source = "${path.module}/../../src/schemas/mapping/product/v1/product.mapping.json"
  # Bootstrap-if-empty seeds (pmc/platinum product.created.v1) — domain DATA,
  # inserted by the shared publish job only when subscription-configs holds
  # no product rows.
  subscription_seeds_source = "${path.module}/../../src/events/config/product.subscriptions.seed.json"
  enable_dlq_alarm          = var.enable_dlq_alarm
  tags                      = local.common_tags
}

# ── 2. The pipeline-configs seed row: product as DATA in the shared table ───
# Edit HERE and apply; never hand-edit in the console. The shared controller
# reads this record per announced run; processor.arguments is passed through
# verbatim to the shared publish Glue job (run-specific keys win).

resource "aws_dynamodb_table_item" "product_pipeline_config" {
  table_name = "${local.event_stack_prefix}-pipeline-configs"
  hash_key   = "pipelineid"
  range_key  = "version"

  item = jsonencode({
    pipelineid = { S = "event-${var.environment}-product" }
    domain     = { S = "product" }
    version    = { S = "latest" }
    enabled    = { BOOL = true }

    processor = { M = {
      # The DEPLOYED job name — the trigger Lambda starts whatever this says.
      glue_job_name = { S = "${local.event_stack_prefix}-publish" }
      mode_default  = { S = "INCREMENTAL" }
      # Everything product-specific the SHARED job needs, passed per run by
      # the controller (--{key}). The stack's job defaults carry only
      # stack-shared values.
      arguments = { M = {
        schema_name                 = { S = "product" }
        glue_database               = { S = local.product_gold_glue_database }
        glue_table                  = { S = var.gold_glue_table }
        event_bus_name              = { S = module.product_events.bus_name }
        mapping_key                 = { S = module.product_events.mapping_key }
        bootstrap_subscriptions_key = { S = module.product_events.subscription_seeds_key }
        # Composite: the gold change log and master key on both columns.
        join_key             = { S = "inventory_item_id,organization_id" }
        business_key_columns = { S = "product_id,productId,ppid" }
        routing_attribute    = { S = "organizationCode" }
        event_type           = { S = local.event_type }
        event_source         = { S = var.event_source_uri }
        event_dataschema     = { S = var.event_dataschema_uri }
        data_classification  = { S = "internal" }
        pii_present          = { S = "false" }
      } }
    } }

    delta = { M = {
      table      = { S = "PRODUCT_CHANGES" }
      run_id_col = { S = "run_id" }
      op_col     = { S = "OP" }
    } }

    eventing = { M = {
      event_bus_name = { S = module.product_events.bus_name }
      source         = { S = var.event_source_uri }
      # Where the shared controller's Athena delta count writes its results
      # (the stack's shared prefix; moved here from the retired `gold`
      # section, 2026-09-24).
      athena_output       = { S = "s3://${var.gold_artifacts_bucket}/event-pipeline/athena-results/" }
      detail_type_pattern = { S = "product.{operation}.{version}" }
      operation_suffix_by_op = { M = {
        INSERT = { S = "created" }
        UPDATE = { S = "updated" }
        DELETE = { S = "deleted" }
      } }
      apicurio_artifact_pattern = { S = "product.product.{operation}" }
      emit_all_active_tiers     = { BOOL = true }
    } }

    publish_ops = { L = [{ S = "INSERT" }, { S = "UPDATE" }, { S = "DELETE" }] }

    skip_run_when = { M = {
      total_zero         = { BOOL = true }
      no_publishable_ops = { BOOL = true }
    } }

    # Ordered sizing tiers, smallest first (2026-09-22): the controller
    # takes the FIRST tier whose max_rows covers the run's change-log count
    # (from product_changes, derived per run via Athena); null max_rows =
    # the unbounded catch-all.
    glue = { M = {
      # The domain's gold Glue catalog (moved here from the retired `gold`
      # section, 2026-09-24) - the database the controller counts the run's
      # change-log rows in.
      database    = { S = local.product_gold_glue_database }
      max_retries = { N = "0" }
      sizing_tiers = { L = [
        { M = {
          name            = { S = "small" }
          max_rows        = { N = "100000" }
          max_capacity    = { N = "0.0625" } # python-shell: 1 GB
          worker_type     = { S = "G.1X" }
          num_workers     = { N = "2" }
          timeout_minutes = { N = "120" }
        } },
        { M = {
          name            = { S = "medium" }
          max_rows        = { N = "500000" }
          max_capacity    = { N = "1" } # python-shell: 16 GB
          worker_type     = { S = "G.2X" }
          num_workers     = { N = "4" }
          timeout_minutes = { N = "240" }
        } },
        { M = {
          name            = { S = "large" }
          max_rows        = { NULL = true }
          max_capacity    = { N = "1" } # python-shell: 16 GB
          worker_type     = { S = "G.4X" }
          num_workers     = { N = "6" }
          timeout_minutes = { N = "600" }
        } },
      ] }
    } }

    batch = { M = {
      putevents_batch_size        = { N = "10" }
      delta_chunk_size            = { N = "5000" }
      apicurio_fetch_once_per_run = { BOOL = true }
    } }
  })
}

# ── 3a. Address moves INTO the module (same state, same AWS resources) ──────

moved {
  from = aws_cloudwatch_event_bus.product_change
  to   = module.product_events.aws_cloudwatch_event_bus.this
}

moved {
  from = aws_sqs_queue.product_events_dlq
  to   = module.product_events.aws_sqs_queue.dlq
}

moved {
  from = aws_cloudwatch_metric_alarm.product_events_dlq_depth
  to   = module.product_events.aws_cloudwatch_metric_alarm.dlq_depth
}

moved {
  from = aws_lambda_event_source_mapping.dlq_recorder
  to   = module.product_events.aws_lambda_event_source_mapping.dlq_recorder
}

# The mapping upload moves too; its KEY changes (product-events/config/... →
# event-pipeline/domains/product/...), so the move is followed by an in-place
# replace to the new key on the same apply.
moved {
  from = aws_s3_object.product_events_mapping
  to   = module.product_events.aws_s3_object.mapping
}

# ── 3b. Hand-off to stacks/event: drop from THIS state, never touch AWS ─────
# Adopted by stacks/event/infrastructure/terraform/imports.tf. Ordered
# runbook: stacks/event/MIGRATION.md (domain apply FIRST, then stack apply).

removed {
  from = aws_dynamodb_table.glue_configs
  lifecycle { destroy = false }
}

removed {
  from = aws_dynamodb_table.pipeline_metrics
  lifecycle { destroy = false }
}

removed {
  from = aws_dynamodb_table.glue_metrics
  lifecycle { destroy = false }
}

removed {
  from = aws_dynamodb_table.delivery_metrics
  lifecycle { destroy = false }
}

removed {
  from = aws_dynamodb_table.subscription_configs
  lifecycle { destroy = false }
}

removed {
  from = aws_dynamodb_table.event_runs
  lifecycle { destroy = false }
}

removed {
  from = aws_s3_object.event_fabric_processor_script
  lifecycle { destroy = false }
}

removed {
  from = aws_glue_job.event_fabric_processor
  lifecycle { destroy = false }
}

removed {
  from = aws_iam_role.event_fabric_processor
  lifecycle { destroy = false }
}

removed {
  from = aws_iam_role_policy.event_fabric_processor
  lifecycle { destroy = false }
}

removed {
  from = aws_lambda_function.product_change_trigger
  lifecycle { destroy = false }
}

removed {
  from = aws_iam_role.product_change_trigger
  lifecycle { destroy = false }
}

removed {
  from = aws_iam_role_policy.product_change_trigger
  lifecycle { destroy = false }
}

removed {
  from = aws_cloudwatch_event_rule.gold_merge_completed
  lifecycle { destroy = false }
}

removed {
  from = aws_cloudwatch_event_target.gold_merge_completed
  lifecycle { destroy = false }
}

removed {
  from = aws_lambda_permission.gold_merge_completed
  lifecycle { destroy = false }
}

removed {
  from = aws_lambda_function.dlq_recorder
  lifecycle { destroy = false }
}

removed {
  from = aws_iam_role.dlq_recorder
  lifecycle { destroy = false }
}

removed {
  from = aws_iam_role_policy.dlq_recorder
  lifecycle { destroy = false }
}

removed {
  from = aws_lambda_function.subscription_provisioner
  lifecycle { destroy = false }
}

removed {
  from = aws_iam_role.subscription_provisioner
  lifecycle { destroy = false }
}

removed {
  from = aws_iam_role_policy.subscription_provisioner
  lifecycle { destroy = false }
}

removed {
  from = aws_lambda_event_source_mapping.subscription_provisioner
  lifecycle { destroy = false }
}

# ── PDZ: KINESIS subscription to product.updated.v1 (config-as-code) ────────
# Onboarding a subscriber = writing a PENDING row; the provisioner
# materialises it (managed ON_DEMAND stream uif-event-{env}-pdz-product-
# product-updated-v1, consumer grant on the stream policy, rule on the
# MESSAGE channel with the shared EventBridge->Kinesis delivery role) and
# flips it ACTIVE with the real ARNs. ignore_changes: Terraform seeds the
# row ONCE and the provisioner owns it thereafter - without this, every
# apply would revert the row to PENDING and re-provision.
resource "aws_dynamodb_table_item" "pdz_product_updated_subscription" {
  table_name = "${local.event_stack_prefix}-subscription-configs"
  hash_key   = "customer_id"
  range_key  = "domain_detail_type"

  lifecycle {
    ignore_changes = [item]
  }

  item = jsonencode({
    customer_id        = { S = "pdz" }
    domain_detail_type = { S = "product#product.updated.v1" }
    subscription_id    = { S = "6b1f4d2e-8c53-4a9b-b0e7-2f9d6c81a345" }
    policy_sid         = { S = "uif6b1f4d2e8c534a9bb0e72f9d6c81a345" }
    domain             = { S = "product" }
    operation          = { S = "updated" }
    version            = { S = "v1" }
    detail_type        = { S = "product.updated.v1" }
    rule_name          = { S = "${local.event_stack_prefix}-on-product-product-updated-v1-pdz" }
    # rule_arn is deliberately ABSENT, not NULL: it keys gsi_rule_arn, and
    # DynamoDB rejects a NULL-typed index key (the whole PutItem fails -
    # same class as the recorder's gold_run_id bug, 2026-09-21). Omission is
    # what makes the GSI sparse; the provisioner writes it at activation.
    target = { M = {
      target_type = { S = "KINESIS" }
      # Conventional stream ARN as the intent; the provisioner writes the
      # REAL ARN back (flat + nested in the same update).
      target_arn = { S = "arn:aws:kinesis:${var.aws_region}:${data.aws_caller_identity.current.account_id}:stream/${local.event_stack_prefix}-pdz-product-product-updated-v1" }
      target_config = { M = {
        # Placeholder until pdz supplies their real consumer role - the
        # account root keeps the stream policy valid so consumption works
        # for same-account readers from day one.
        consumer_principal_arn = { S = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root" }
        partition_key_path     = { S = "$.detail.partitionkey" }
        dlq_arn                = { NULL = true }
        retry_policy = { M = {
          max_attempts    = { N = "3" }
          max_age_seconds = { N = "900" }
        } }
      } }
    } }
    target_arn     = { S = "arn:aws:kinesis:${var.aws_region}:${data.aws_caller_identity.current.account_id}:stream/${local.event_stack_prefix}-pdz-product-product-updated-v1" }
    status         = { S = "PENDING" }
    status_reason  = { NULL = true }
    record_version = { N = "1" }
    created_at     = { S = "2026-09-24T00:00:00Z" }
    created_by     = { S = "terraform (UIFPE-184 pdz onboarding)" }
    updated_at     = { S = "2026-09-24T00:00:00Z" }
    updated_by     = { S = "terraform" }
    deleted_at     = { NULL = true }
    deleted_by     = { NULL = true }
  })
}
