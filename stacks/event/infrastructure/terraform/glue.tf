# The shared publish job (Event Fabric Processor) - one Glue job serves
# every domain; per-domain arguments arrive per run from the controller.
# Moved from domains/product (2026-09-21); job + role adopted via imports.tf.

# ── Event Fabric Processor (Glue Python shell job) ──────────────────────────

resource "aws_iam_role" "event_fabric_processor" {
  name = "${local.event_stack_prefix}-publish-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "glue.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy" "event_fabric_processor" {
  name = "${local.event_stack_prefix}-publish-policy"
  role = aws_iam_role.event_fabric_processor.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # The job's own script, from the release-artifacts bucket. READ-ONLY
        # and scoped to glue/*: a job that could write here could overwrite a
        # shipped artifact and invalidate the manifest that names it.
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = "arn:aws:s3:::${local.release_artifacts_bucket}/glue/*"
      },
      {
        # Working prefix: per-domain mapping/seed reads + Athena result
        # writes. Domains upload THEIR files under
        # ${local.working_prefix}/domains/{domain}/ (the event-domain
        # module), inside this one grant - no policy change per domain.
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject", "s3:AbortMultipartUpload"]
        Resource = "arn:aws:s3:::${var.gold_artifacts_bucket}/${local.working_prefix}/*"
      },
      {
        # Without ListBucket, S3 reports a missing key as AccessDenied
        # instead of NoSuchKey, hiding real data errors.
        Effect   = "Allow"
        Action   = ["s3:ListBucket", "s3:GetBucketLocation"]
        Resource = "arn:aws:s3:::${var.gold_artifacts_bucket}"
      },
      {
        # Every DOMAIN bus by convention (uif-event-{env}-{domain}, owned
        # by the event-domain module instances) - publishing to a new
        # domain's bus needs no stack change.
        Effect   = "Allow"
        Action   = ["events:PutEvents"]
        Resource = "arn:aws:events:${var.aws_region}:${data.aws_caller_identity.current.account_id}:event-bus/${local.event_stack_prefix}-*"
      },
      {
        # Emit validation reads the DEPLOYED delivery state (2026-09-24):
        # rules on the domain bus and their targets, instead of
        # subscription-configs rows. ListRules authorizes on rule/{bus} and
        # ListTargetsByRule on rule/{bus}/{name} - one prefix wildcard
        # covers both for every domain bus.
        Effect   = "Allow"
        Action   = ["events:ListRules", "events:ListTargetsByRule"]
        Resource = "arn:aws:events:${var.aws_region}:${data.aws_caller_identity.current.account_id}:rule/${local.event_stack_prefix}-*"
      },
      {
        Effect   = "Allow"
        Action   = ["dynamodb:UpdateItem", "dynamodb:PutItem", "dynamodb:GetItem"]
        Resource = aws_dynamodb_table.event_runs.arn
      },
      {
        # Table 2: STARTED + terminal status on this run's row (found via
        # the sparse gsi_gold_run - python-shell jobs get no run id).
        Effect = "Allow"
        Action = ["dynamodb:UpdateItem", "dynamodb:Query"]
        Resource = [
          aws_dynamodb_table.pipeline_metrics.arn,
          "${aws_dynamodb_table.pipeline_metrics.arn}/index/*",
        ]
      },
      {
        # Table 3: one row per publish attempt, batched.
        Effect   = "Allow"
        Action   = ["dynamodb:BatchWriteItem", "dynamodb:PutItem"]
        Resource = aws_dynamodb_table.glue_metrics.arn
      },
      {
        # Table 5: emit validation (who is subscribed, via gsi_detail_type)
        # plus the ONE bootstrap path - conditional inserts of the default
        # subscriptions when the table holds no rows for the domain.
        Effect = "Allow"
        Action = ["dynamodb:Query", "dynamodb:PutItem"]
        Resource = [
          aws_dynamodb_table.subscription_configs.arn,
          "${aws_dynamodb_table.subscription_configs.arn}/index/*",
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "athena:StartQueryExecution", "athena:GetQueryExecution",
          "athena:GetQueryResults", "athena:StopQueryExecution",
        ]
        Resource = "arn:aws:athena:${var.aws_region}:${data.aws_caller_identity.current.account_id}:workgroup/${var.athena_workgroup}"
      },
      {
        Effect = "Allow"
        Action = ["glue:GetDatabase", "glue:GetTable", "glue:GetPartition", "glue:GetPartitions"]
        # Every gold database by the platform convention
        # uif_echo_{env}_gold_{domain} - a new domain's catalog is covered
        # the day it exists.
        Resource = [
          "arn:aws:glue:${var.aws_region}:${data.aws_caller_identity.current.account_id}:catalog",
          "arn:aws:glue:${var.aws_region}:${data.aws_caller_identity.current.account_id}:database/${local.gold_db_wildcard}",
          "arn:aws:glue:${var.aws_region}:${data.aws_caller_identity.current.account_id}:table/${local.gold_db_wildcard}/*",
        ]
      },
      {
        # No-op unless the gold table is Lake Formation-governed; required
        # there for Athena to vend the table's credentials. The LF grants
        # themselves (DESCRIBE on the db, SELECT/DESCRIBE on the table, to
        # THIS role) are issued outside Terraform — one-time, per account.
        Effect   = "Allow"
        Action   = ["lakeformation:GetDataAccess"]
        Resource = "*"
      },
      {
        # Gold table data files (skipped by LF-vended credentials, needed
        # for plain IAM-governed tables).
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = ["arn:aws:s3:::${var.gold_bucket}/*"]
      },
      {
        Effect   = "Allow"
        Action   = ["s3:ListBucket", "s3:GetBucketLocation"]
        Resource = ["arn:aws:s3:::${var.gold_bucket}"]
      },
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:/aws-glue/*"
      },
    ]
  })
}

# Run registration (DynamoDB), gold fetch (Athena), mapping-based channel
# gating, and bus publishing all live in this job. max_retries = 0 on
# purpose: an automatic retry would re-publish events; reprocessing is a
# deliberate act via the run registry.
resource "aws_glue_job" "event_fabric_processor" {
  # uif-{stack}-{env}-{operation} (stack = event): this job PUBLISHES gold
  # changes as domain events - pairs with uif-event-<env>-publish-metrics.
  # (UIF Resource Name Lookup; renamed from echo-<env>-event-fabric-processor.)
  name        = "${local.event_stack_prefix}-publish"
  description = "Registers a run in DynamoDB, fetches its products from the gold table, projects per channel, publishes to the domain bus."
  role_arn    = aws_iam_role.event_fabric_processor.arn
  max_retries = 0
  timeout     = 15 # minutes

  command {
    name = "pythonshell"
    # Content-addressed: a changed script is a new key, never an overwrite.
    script_location = local.glue_script_uri["event_fabric_processor"]
    python_version  = "3.9"
  }

  max_capacity = 0.0625

  execution_property {
    max_concurrent_runs = 5
  }

  # ONLY the stack-shared arguments live here. Everything per-domain
  # (schema_name, glue_database/table, mapping_key, event_type/source/
  # dataschema, join_key, routing_attribute, business keys, bootstrap seed,
  # bus name, classification) arrives PER RUN from the controller, read from
  # the domain's pipeline-configs record (processor.arguments) - one job,
  # every domain, no stack change to onboard one.
  default_arguments = {
    "--data_bucket"                = var.gold_artifacts_bucket
    "--dynamo_table"               = aws_dynamodb_table.event_runs.name
    "--pipeline_metrics_table"     = aws_dynamodb_table.pipeline_metrics.name
    "--subscription_configs_table" = aws_dynamodb_table.subscription_configs.name
    "--publish_metrics_table"      = aws_dynamodb_table.glue_metrics.name
    "--run_id_column"              = "run_id"
    "--athena_workgroup"           = var.athena_workgroup
    "--athena_output"              = "s3://${var.gold_artifacts_bucket}/${local.working_prefix}/athena-results/"
  }

  tags = local.common_tags
}
