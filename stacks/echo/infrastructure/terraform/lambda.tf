# lambda.tf
# Packages and deploys all five Lambda functions.
# Also owns all aws_lambda_permission blocks so invoke permissions
# are co-located with the function definitions.

# ── Zip archives built at terraform plan time ─────────────────────

data "archive_file" "pipeline_controller" {
  type        = "zip"
  output_path = "${path.module}/.build/pipeline_controller.zip"

  source {
    content  = file("${path.module}/../../src/apps/control_panel/pipeline_controller.py")
    filename = "pipeline_controller.py"
  }
  source {
    content  = file("${path.module}/../../src/libs/pipeline_profiles.py")
    filename = "pipeline_profiles.py"
  }
  source {
    content  = file("${path.module}/../../src/libs/echo_resources.py")
    filename = "echo_resources.py"
  }
  # The one authority for gold names — the controller's gold routes resolve
  # through it rather than deriving a second spelling (ADR-0018).
  source {
    content  = file("${path.module}/../../src/libs/gold_resources.py")
    filename = "gold_resources.py"
  }
  # The cadence and start-time authority (UIFPE-149). The schedule PATCH
  # validates with the SAME parse_time_of_day the dispatcher runs (UIFPE-513).
  source {
    content  = file("${path.module}/../../src/libs/gold_frequencies.py")
    filename = "gold_frequencies.py"
  }
  source {
    content  = file("${path.module}/../../src/libs/sizing_policy.py")
    filename = "sizing_policy.py"
  }
  source {
    content  = file("${path.module}/../../src/libs/daily_schedule.py")
    filename = "daily_schedule.py"
  }
  source {
    content  = file("${path.module}/../../src/libs/schedule_vocabulary.py")
    filename = "schedule_vocabulary.py"
  }
  source {
    content  = file("${path.module}/../../src/libs/bq_partition_ddl.py")
    filename = "bq_partition_ddl.py"
  }
  # The statistics arithmetic (UIFPE-544), shared with the nightly Glue job so
  # a tile on the Overview and a row in daily_stats cannot define "fresh",
  # "failed" or "a run today" two different ways. gold_telemetry comes with it:
  # gold_stats imports the freshness rule from there, which is the SAME rule
  # the alarms use.
  source {
    content  = file("${path.module}/../../src/libs/gold_stats.py")
    filename = "gold_stats.py"
  }
  source {
    content  = file("${path.module}/../../src/libs/gold_telemetry.py")
    filename = "gold_telemetry.py"
  }
}

data "archive_file" "scheduler_dispatcher" {
  type        = "zip"
  output_path = "${path.module}/.build/scheduler_dispatcher.zip"

  source {
    content  = file("${path.module}/../../src/apps/lambdas/scheduler_dispatcher.py")
    filename = "scheduler_dispatcher.py"
  }
  source {
    content  = file("${path.module}/../../src/libs/pipeline_profiles.py")
    filename = "pipeline_profiles.py"
  }
  source {
    content  = file("${path.module}/../../src/libs/echo_resources.py")
    filename = "echo_resources.py"
  }
  source {
    content  = file("${path.module}/../../src/libs/daily_schedule.py")
    filename = "daily_schedule.py"
  }
  source {
    content  = file("${path.module}/../../src/libs/sizing_policy.py")
    filename = "sizing_policy.py"
  }
  source {
    content  = file("${path.module}/../../src/libs/schedule_vocabulary.py")
    filename = "schedule_vocabulary.py"
  }
}

data "archive_file" "gcs_replicator" {
  type        = "zip"
  output_path = "${path.module}/.build/gcs_replicator.zip"

  source {
    content  = file("${path.module}/../../src/apps/lambdas/gcs_replicator.py")
    filename = "gcs_replicator.py"
  }
  source {
    content  = file("${path.module}/../../src/libs/echo_resources.py")
    filename = "echo_resources.py"
  }
}

data "archive_file" "silver_bq_replicator" {
  type        = "zip"
  output_path = "${path.module}/.build/silver_bq_replicator.zip"

  source {
    content  = file("${path.module}/../../src/apps/lambdas/silver_bq_replicator.py")
    filename = "silver_bq_replicator.py"
  }
  source {
    content  = file("${path.module}/../../src/libs/echo_resources.py")
    filename = "echo_resources.py"
  }
}

# ── Oracle Instant Client Lambda Layer ───────────────────────────
# lib/   → Oracle native .so files (thick mode)
# python/ → oracledb Python package (manylinux, Python 3.11)

resource "aws_lambda_layer_version" "oracle_instant_client" {
  layer_name          = "${local.name_prefix}-oracle-layer"
  description         = "Oracle Instant Client Basic Lite + oracledb Python package for thick-mode connections"
  s3_bucket           = local.is_uif_stack ? var.release_artifacts_bucket : aws_s3_bucket.artifacts.id
  s3_key              = local.is_uif_stack ? local.layer_s3_key["oracle_instant_client"] : local.lambda_layer_keys.oracle_instant_client
  compatible_runtimes = ["python3.11"]
}

# ── GCS client Lambda Layer ──────────────────────────────────────
# python/ → google-cloud-storage + transitive deps, built via
# `cd infra && make build-gcs-layer` (forces manylinux wheels).

resource "aws_lambda_layer_version" "gcs_client" {
  layer_name          = "${local.name_prefix}-gcs-layer"
  description         = "google-cloud-storage + google-auth for Bronze→GCS replication"
  s3_bucket           = local.is_uif_stack ? var.release_artifacts_bucket : aws_s3_bucket.artifacts.id
  s3_key              = local.is_uif_stack ? local.layer_s3_key["gcs_client"] : local.lambda_layer_keys.gcs
  compatible_runtimes = ["python3.11"]
}

# ── Silver→BigQuery client Lambda Layer ──────────────────────────
# python/ → google-cloud-storage + google-cloud-bigquery + google-auth +
# fastavro. Separate from the gcs_client layer so the Bronze→GCS replicator's
# cold-start stays small and the two deploy lifecycles are decoupled.
# Build via `cd infra && make build-silver-bq-layer`.

resource "aws_lambda_layer_version" "silver_bq_client" {
  layer_name          = "${local.name_prefix}-replicate-bq-layer"
  description         = "google-cloud-storage + google-cloud-bigquery + google-auth + fastavro for Silver→BQ replication"
  s3_bucket           = local.is_uif_stack ? var.release_artifacts_bucket : aws_s3_bucket.artifacts.id
  s3_key              = local.is_uif_stack ? local.layer_s3_key["silver_bq_client"] : local.lambda_layer_keys.silver_bq
  compatible_runtimes = ["python3.11"]
}

# ── Slim controller-only BQ layer ────────────────────────────────
# pipeline_controller needs to mint a GCP access token and call the BigQuery
# REST API directly (for ensure_bq_dataset). The full silver_bq_client layer
# can't be attached here because oracle_instant_client (~218 MiB unzipped)
# + silver_bq_client (~67 MiB) blow past Lambda's 250 MiB layer ceiling.
# This layer ships only google-auth + rsa (~4 MB unzipped) and relies on the
# pure-Python `rsa` fallback for JWT signing (no `cryptography` package).
# REST calls go through urllib3, which is already on the Lambda runtime via
# boto3 — so we don't need google-cloud-bigquery or `requests`.
# Build via `cd infra && make build-controller-bq-layer`.

resource "aws_lambda_layer_version" "controller_bq_client" {
  layer_name          = "${local.name_prefix}-controller-bq-layer"
  description         = "google-auth + rsa (pure-Python crypto) for pipeline_controller ensure_bq_dataset via BigQuery REST API"
  s3_bucket           = local.is_uif_stack ? var.release_artifacts_bucket : aws_s3_bucket.artifacts.id
  s3_key              = local.is_uif_stack ? local.layer_s3_key["controller_bq_client"] : local.lambda_layer_keys.controller_bq
  compatible_runtimes = ["python3.11"]
}

# ── pipeline_controller ───────────────────────────────────────────
# Backs the API Gateway HTTP API — handles all pipeline CRUD,
# manual run triggers, run history queries, and synchronous Oracle analysis.

resource "aws_lambda_function" "pipeline_controller" {
  function_name = "${local.name_prefix}-pipeline-controller"
  description   = "Pipeline config CRUD, manual triggers, run history, and Oracle table analysis"
  # ARTIFACT SOURCE, gated (UIFPE-515). A uif-echo-* stack runs the promoted
  # artifact from the release bucket at a content-addressed key; a legacy
  # echo-* stack keeps the plan-time zip until it retires. `filename` and
  # `s3_bucket`/`s3_key` are mutually exclusive on this resource, so each is
  # null on the path it does not take.
  #
  # source_code_hash is what lets a plan notice somebody ran UpdateFunctionCode
  # by hand. On the S3 path it comes from the manifest, because the archive is
  # no longer built here to derive it from.
  filename         = local.is_uif_stack ? null : data.archive_file.pipeline_controller.output_path
  s3_bucket        = local.is_uif_stack ? var.release_artifacts_bucket : null
  s3_key           = local.is_uif_stack ? local.lambda_s3_key["pipeline_controller"] : null
  source_code_hash = local.is_uif_stack ? lookup(var.lambda_code_hashes, "pipeline_controller", null) : data.archive_file.pipeline_controller.output_base64sha256
  handler          = "pipeline_controller.handler"
  runtime          = "python3.11"
  role             = aws_iam_role.lambda_execution.arn
  timeout          = 60
  memory_size      = 1024
  # controller_bq_client is a slim google-auth-only layer that lets
  # ensure_bq_dataset() mint a GCP token and call BigQuery REST directly.
  # Keeping it separate from silver_bq_client (which adds 67 MB for
  # google-cloud-bigquery + storage + fastavro) is what allows both layers
  # to coexist alongside the ~218 MiB oracle_instant_client without breaching
  # Lambda's 250 MiB unzipped layer ceiling.
  layers = [
    aws_lambda_layer_version.oracle_instant_client.arn,
    aws_lambda_layer_version.controller_bq_client.arn,
  ]

  environment {
    variables = {
      # UIFPE-355: the runtime reads its prefixes rather than computing them, so
      # the same code works in dev (renamed) and uat/prod (not yet renamed).
      RESOURCE_PREFIX         = local.name_prefix
      LEGACY_RESOURCE_PREFIX  = local.legacy_name_prefix
      ENV                     = var.environment
      REGION                  = var.aws_region
      CONFIG_TABLE            = aws_dynamodb_table.glue_job_config.name
      METRICS_TABLE           = aws_dynamodb_table.glue_job_metrics.name
      EXPORT_METRICS_TABLE    = "${local.legacy_name_prefix}-partner-export-metrics"
      DQ_METRICS_TABLE        = "${local.legacy_name_prefix}-dq-check-metrics"
      HOME_WINDOW_HOURS       = "24"
      ALERT_SUBSCRIBERS_TABLE = local.alert_subscribers_table_name
      GOLD_ALARM_PREFIX       = local.gold_alarm_prefix
      GLUE_JOB_NAME           = aws_glue_job.generic_bronze_ingest.name
      ANALYZE_GLUE_JOB_NAME   = aws_glue_job.analyze_table.name
      ANALYSIS_TABLE          = aws_dynamodb_table.pipeline_analysis.name
      RECONCILE_JOB_NAME      = aws_glue_job.generic_silver_reconcile.name
      RECONCILE_JOB_NAME_GCP  = aws_glue_job.generic_silver_reconcile_gcp.name
      GOLD_CONFIG_TABLE       = local.gold_config_table_name
      GOLD_JOB_NAME           = local.gold_transform_job_name
      GOLD_RECONCILE_JOB_NAME = local.gold_reconcile_job_name
      GOLD_ARTIFACTS_BUCKET   = local.gold_artifacts_bucket
      DQ_CHECK_TABLE          = aws_dynamodb_table.dq_check_config.name
      SECRET_TAG_KEY          = "app"
      SECRET_TAG_VAL          = local.project_tag
      LD_LIBRARY_PATH         = "/opt/lib"
      GLUE_ROLE_ARN           = aws_iam_role.glue_execution.arn
      ANALYST_ROLE_ARN        = "arn:aws:iam::${var.aws_account_id}:role/PCMPowerUser"
      UI_LOGIN_SECRET         = local.ui_login_secret_name

      # Set explicitly rather than left to echo_resources.echo() in the Lambda,
      # which hardcodes the echo- prefix. On a stack named eds-prod-* the
      # fallback resolves to echo-prod-silver-* -- Glue jobs that do not exist.
      SILVER_JOB_NAME        = aws_glue_job.generic_silver_transform.name
      SILVER_JOB_NAME_GCP    = aws_glue_job.generic_silver_transform_gcp_iceberg.name
      SILVER_JOB_NAME_GCP_BQ = aws_glue_job.generic_silver_bq_native.name
    }
  }

  # VPC config so the Lambda can reach Oracle on the private subnet —
  # same subnet and SG as the Glue NETWORK connection.
  vpc_config {
    subnet_ids         = [var.subnet_id]
    security_group_ids = [aws_security_group.glue_eni.id]
  }

  tags = {
    Project     = local.project_tag
    Environment = var.environment
    ManagedBy   = "terraform"
  }
  # WAIT FOR THE ROLE'S INLINE POLICY, not just for the role. AWS validates a
  # VPC-attached function's execution role for ec2:CreateNetworkInterface at
  # CreateFunction, and `role = aws_iam_role.lambda_execution.arn` creates an
  # edge to the ROLE only -- so with default parallelism Terraform is free to
  # create the function before aws_iam_role_policy.lambda_execution exists, and
  # does. It fails with:
  #
  #   InvalidParameterValueException: The provided execution role does not have
  #   permissions to call CreateNetworkInterface on EC2
  #
  # which names a permission the Terraform plainly grants (iam.tf's
  # lambda_permissions), so it reads as a propagation delay. It is not: the
  # grant did not exist yet. It cost two rounds of the uif-echo-uat converge
  # (36253830424, 36327029234) before the ordering was checked, and re-applying
  # does not help because the graph has no reason to order it differently.
  #
  # On the four functions here, not only the VPC-attached three: a function that
  # can be created before its role can do anything is wrong regardless of which
  # API notices first.
  depends_on = [aws_iam_role_policy.lambda_execution]

}

# Allow API Gateway to invoke pipeline_controller.
# source_arn scoped to this API only — wildcard on method+path.
resource "aws_lambda_permission" "apigateway_invoke_controller" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.pipeline_controller.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.ingestion.execution_arn}/*/*"
}

# ── scheduler_dispatcher ──────────────────────────────────────────
# EventBridge-only Lambda — not exposed via API Gateway.
# Fans out Glue runs for all enabled pipelines at a given frequency.

resource "aws_lambda_function" "scheduler_dispatcher" {
  function_name    = "${local.name_prefix}-dispatch"
  description      = "Fans out Glue runs for all enabled pipelines at a given frequency"
  filename         = local.is_uif_stack ? null : data.archive_file.scheduler_dispatcher.output_path
  s3_bucket        = local.is_uif_stack ? var.release_artifacts_bucket : null
  s3_key           = local.is_uif_stack ? local.lambda_s3_key["scheduler_dispatcher"] : null
  source_code_hash = local.is_uif_stack ? lookup(var.lambda_code_hashes, "scheduler_dispatcher", null) : data.archive_file.scheduler_dispatcher.output_base64sha256
  handler          = "scheduler_dispatcher.handler"
  runtime          = "python3.11"
  role             = aws_iam_role.lambda_execution.arn
  timeout          = 120
  memory_size      = 128

  environment {
    variables = {
      # UIFPE-355: the runtime reads its prefixes rather than computing them, so
      # the same code works in dev (renamed) and uat/prod (not yet renamed).
      RESOURCE_PREFIX           = local.name_prefix
      LEGACY_RESOURCE_PREFIX    = local.legacy_name_prefix
      ENV                       = var.environment
      REGION                    = var.aws_region
      CONFIG_TABLE              = aws_dynamodb_table.glue_job_config.name
      GLUE_JOB_NAME             = aws_glue_job.generic_bronze_ingest.name
      RECONCILE_JOB_NAME        = aws_glue_job.generic_silver_reconcile.name
      RECONCILE_JOB_NAME_GCP    = aws_glue_job.generic_silver_reconcile_gcp.name
      RECONCILE_JOB_NAME_GCP_BQ = aws_glue_job.generic_silver_reconcile_gcp_bq.name
      DQ_CHECK_TABLE            = aws_dynamodb_table.dq_check_config.name
      DQ_GLUE_JOB_NAME          = aws_glue_job.generic_silver_quality_check.name
    }
  }

  tags = {
    Project     = local.project_tag
    Environment = var.environment
    ManagedBy   = "terraform"
  }
  depends_on = [aws_iam_role_policy.lambda_execution]

}

# Allow EventBridge to invoke scheduler_dispatcher for each frequency rule.
resource "aws_lambda_permission" "allow_eventbridge_half_hourly" {
  statement_id  = "AllowEventBridge30Min"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.scheduler_dispatcher.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.half_hourly.arn
}

resource "aws_lambda_permission" "allow_eventbridge_hourly" {
  statement_id  = "AllowEventBridgeHourly"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.scheduler_dispatcher.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.hourly.arn
}

resource "aws_lambda_permission" "allow_eventbridge_four_hourly" {
  statement_id  = "AllowEventBridge4Hr"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.scheduler_dispatcher.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.four_hourly.arn
}

resource "aws_lambda_permission" "allow_eventbridge_daily_slot" {
  for_each = local.daily_bronze_slot_crons

  statement_id  = "AllowEventBridgeDailySlot${each.key}"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.scheduler_dispatcher.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.daily_bronze_slot[each.key].arn
}

resource "aws_lambda_permission" "allow_eventbridge_weekly" {
  statement_id  = "AllowEventBridgeWeekly"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.scheduler_dispatcher.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.weekly.arn
}

resource "aws_lambda_permission" "allow_eventbridge_reconcile_slot" {
  for_each = local.reconcile_slot_crons

  statement_id  = "AllowEventBridgeReconcileSlot${each.key}"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.scheduler_dispatcher.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.reconcile_slot[each.key].arn
}

# ── gcs_replicator ────────────────────────────────────────────────
# Async replicator: copies Bronze Parquet from S3 to a GCS bucket for
# pipelines that set `replication.gcs.enabled=true`. Phase 1 = manual
# invoke. Phase 2 (later) wires an EventBridge rule on Glue SUCCEEDED.

resource "aws_lambda_function" "gcs_replicator" {
  function_name    = "${local.name_prefix}-replicate-gcs"
  description      = "Replicate Bronze Parquet from S3 to GCS for opted-in pipelines"
  filename         = local.is_uif_stack ? null : data.archive_file.gcs_replicator.output_path
  s3_bucket        = local.is_uif_stack ? var.release_artifacts_bucket : null
  s3_key           = local.is_uif_stack ? local.lambda_s3_key["gcs_replicator"] : null
  source_code_hash = local.is_uif_stack ? lookup(var.lambda_code_hashes, "gcs_replicator", null) : data.archive_file.gcs_replicator.output_base64sha256
  handler          = "gcs_replicator.handler"
  runtime          = "python3.11"
  role             = aws_iam_role.lambda_execution.arn
  timeout          = 300
  memory_size      = 1024
  layers           = [aws_lambda_layer_version.gcs_client.arn]

  environment {
    variables = {
      # UIFPE-355: the runtime reads its prefixes rather than computing them, so
      # the same code works in dev (renamed) and uat/prod (not yet renamed).
      RESOURCE_PREFIX        = local.name_prefix
      LEGACY_RESOURCE_PREFIX = local.legacy_name_prefix
      ENV                    = var.environment
      REGION                 = var.aws_region
      CONFIG_TABLE           = aws_dynamodb_table.glue_job_config.name
      METRICS_TABLE          = aws_dynamodb_table.glue_job_metrics.name
      JOB_NAME               = "${local.name_prefix}-replicate-gcs"
    }
  }

  # VPC config so the Lambda can reach Secrets Manager via the interface
  # endpoint. Egress to storage.googleapis.com flows through the existing
  # 0.0.0.0/0:443 rule on the glue_eni security group.
  vpc_config {
    subnet_ids         = [var.subnet_id]
    security_group_ids = [aws_security_group.glue_eni.id]
  }

  tags = {
    Project     = local.project_tag
    Environment = var.environment
    ManagedBy   = "terraform"
  }
  depends_on = [aws_iam_role_policy.lambda_execution]

}

resource "aws_lambda_permission" "allow_eventbridge_bronze_succeeded" {
  statement_id  = "AllowEventBridgeBronzeSucceeded"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.gcs_replicator.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.bronze_succeeded_replicate.arn
}



# ── silver_bq_replicator ──────────────────────────────────────────
# Mirrors a Silver Iceberg table from S3 to GCS and refreshes the BigQuery
# BigLake external table that points at it. Opted into per pipeline via
# `replication.silver_bq.enabled=true` on the DynamoDB config.
#
# Sizing: 2048 MB / 900 s because the full Avro manifest rewrite scales with
# Iceberg's manifest count for the current snapshot (typically tens, can
# spike to hundreds before compaction).

resource "aws_lambda_function" "silver_bq_replicator" {
  function_name    = "${local.name_prefix}-replicate-bq"
  description      = "Mirror Silver Iceberg from S3 to GCS, then refresh BigLake external table"
  filename         = local.is_uif_stack ? null : data.archive_file.silver_bq_replicator.output_path
  s3_bucket        = local.is_uif_stack ? var.release_artifacts_bucket : null
  s3_key           = local.is_uif_stack ? local.lambda_s3_key["silver_bq_replicator"] : null
  source_code_hash = local.is_uif_stack ? lookup(var.lambda_code_hashes, "silver_bq_replicator", null) : data.archive_file.silver_bq_replicator.output_base64sha256
  handler          = "silver_bq_replicator.handler"
  runtime          = "python3.11"
  role             = aws_iam_role.lambda_execution.arn
  timeout          = 900
  memory_size      = 2048
  layers           = [aws_lambda_layer_version.silver_bq_client.arn]

  environment {
    variables = {
      # UIFPE-355: the runtime reads its prefixes rather than computing them, so
      # the same code works in dev (renamed) and uat/prod (not yet renamed).
      RESOURCE_PREFIX        = local.name_prefix
      LEGACY_RESOURCE_PREFIX = local.legacy_name_prefix
      ENV                    = var.environment
      REGION                 = var.aws_region
      CONFIG_TABLE           = aws_dynamodb_table.glue_job_config.name
      METRICS_TABLE          = aws_dynamodb_table.glue_job_metrics.name
      JOB_NAME               = "${local.name_prefix}-replicate-bq"
    }
  }

  # Same VPC/SG as gcs_replicator: Secrets Manager via interface endpoint;
  # egress to *.googleapis.com flows through the existing 0.0.0.0/0:443 rule.
  vpc_config {
    subnet_ids         = [var.subnet_id]
    security_group_ids = [aws_security_group.glue_eni.id]
  }

  tags = {
    Project     = local.project_tag
    Environment = var.environment
    ManagedBy   = "terraform"
  }
  depends_on = [aws_iam_role_policy.lambda_execution]

}

resource "aws_lambda_permission" "allow_eventbridge_silver_succeeded_bq" {
  statement_id  = "AllowEventBridgeSilverSucceededBq"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.silver_bq_replicator.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.silver_succeeded_bq.arn
}




