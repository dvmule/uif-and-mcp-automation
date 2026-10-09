

# Upload the job script to S3 so Glue can reference it.
resource "aws_s3_object" "generic_bronze_ingest_script" {
  count  = var.upload_glue_scripts && !local.is_uif_stack ? 1 : 0
  bucket = aws_s3_bucket.artifacts.id
  key    = "${local.glue_scripts_prefix}/bronze-ingest.py"
  source = "${path.module}/../../src/apps/pipeline/bronze-ingest.py"
  etag   = filemd5("${path.module}/../../src/apps/pipeline/bronze-ingest.py")
}

# Single Glue 5.1 job that handles all pipeline_ids.
# Worker settings here are defaults — the scheduler_dispatcher overrides
# WorkerType and NumberOfWorkers per run using each pipeline's glue config.
#
# max_retries=0: bronze talks to shared Oracle. A retry storm (3 attempts ×
# N JDBC predicates) is exactly the SPEA9I session-multiplication anti-pattern
# at the infra layer — the smoke test of 2026-06-11 reproduced it. Failures
# here are almost always config/query-plan driven, not transient, so auto-
# retry has no upside and a large downside. Other STANDARD-class jobs
# (silver_reconcile, gold_reconcile) already opted out for the same reason.
resource "aws_glue_job" "generic_bronze_ingest" {
  name         = "${local.name_prefix}-bronze-ingest"
  role_arn     = aws_iam_role.glue_execution.arn
  glue_version = "5.1"
  max_retries  = 0
  timeout      = 120

  command {
    name            = "glueetl"
    script_location = local.glue_script_uri["bronze_ingest"]
    python_version  = "3"
  }

  worker_type       = "G.1X"
  number_of_workers = 2

  default_arguments = {
    # --enable-continuous-cloudwatch-log removed — deprecated in Glue 5.0+
    # All logs now go to /aws-glue/jobs/error log group automatically.
    "--enable-metrics"  = "true"
    "--enable-spark-ui" = "false"
    "--job-language"    = "python"
    "--TempDir"         = "s3://${var.artifacts_bucket}/${local.glue_temp_prefix}/"
    "--CONFIG_TABLE"    = aws_dynamodb_table.glue_job_config.name
    "--METRICS_TABLE"   = aws_dynamodb_table.glue_job_metrics.name
    # --extra-jars is also passed on Bronze because pipelines toggle to GCS
    # via runtime config (destination.cloud=gcp). JAR presence on the classpath
    # is harmless when fs.s3a is the active filesystem; the GCS connector only
    # activates when fs.gs.impl is set in Spark conf at session start.
    "--extra-jars" = local.gcp_glue_extra_jars

    # UIFPE-355 phase 2: infrastructure names are PASSED, never derived from
    # --env. The scripts previously built echo-{env}-* themselves, so a
    # uif-echo-dev job wrote into the echo-dev lake and triggered echo-dev's
    # Silver. getResolvedOptions treats these as required — a missing value
    # fails the run instead of silently targeting the other stack.
    "--RESOURCE_PREFIX" = local.name_prefix

    # resolve_silver_trigger lives in silver_routing.py, and build_extract_sql
    # in bronze_extract_sql.py, so tests import the real implementations
    # rather than copies — see those modules' docstrings. One comma-separated
    # value: Glue takes --extra-py-files as a single list.
    "--extra-py-files" = join(",", [local.glue_script_uri["silver_routing"], local.glue_script_uri["bronze_extract_sql"]])
  }

  # Attach the VPC network connection so workers run inside the private subnet
  # and can reach Oracle databases. Defined in glue_connection.tf.
  connections = [aws_glue_connection.vpc_network.name]

  execution_property {
    max_concurrent_runs = 50
  }

  job_run_queuing_enabled = true

  tags = {
    Project     = local.project_tag
    Environment = var.environment
    ManagedBy   = "terraform"
  }

  depends_on = [
    aws_s3_object.generic_bronze_ingest_script,
    aws_s3_object.gcp_glue_jars["gcs_connector"],
    aws_s3_object.gcp_glue_jars["iceberg_gcp_bundle"],
  ]
}

resource "aws_s3_object" "generic_silver_transform_script" {
  count  = var.upload_glue_scripts && !local.is_uif_stack ? 1 : 0
  bucket = aws_s3_bucket.artifacts.id
  key    = "${local.glue_scripts_prefix}/silver-transform.py"
  source = "${path.module}/../../src/apps/pipeline/silver-transform.py"
  etag   = filemd5("${path.module}/../../src/apps/pipeline/silver-transform.py")
}

resource "aws_glue_job" "generic_silver_transform" {
  name         = "${local.name_prefix}-silver-transform"
  role_arn     = aws_iam_role.glue_execution.arn
  glue_version = "5.1"
  max_retries  = 1
  timeout      = 120
  # FLEX uses pre-warmed spare capacity — cold start ~30s vs ~3min for STANDARD.
  # Safe for Silver because it only reads/writes S3; no VPC connection needed.
  execution_class = "FLEX"

  command {
    name            = "glueetl"
    script_location = local.glue_script_uri["silver_transform"]
    python_version  = "3"
  }

  worker_type       = "G.2X"
  number_of_workers = 16

  default_arguments = {
    "--enable-metrics"   = "true"
    "--enable-spark-ui"  = "false"
    "--job-language"     = "python"
    "--TempDir"          = "s3://${var.artifacts_bucket}/${local.glue_temp_prefix}/"
    "--CONFIG_TABLE"     = aws_dynamodb_table.glue_job_config.name
    "--METRICS_TABLE"    = aws_dynamodb_table.glue_job_metrics.name
    "--datalake-formats" = "iceberg"
    "--env"              = var.environment

    # UIFPE-355 phase 2: infrastructure names are PASSED, never derived from
    # --env. The scripts previously built echo-{env}-* themselves, so a
    # uif-echo-dev job wrote into the echo-dev lake and triggered echo-dev's
    # Silver. getResolvedOptions treats these as required — a missing value
    # fails the run instead of silently targeting the other stack.
    "--SILVER_WAREHOUSE" = "s3://${var.silver_bucket}/"
  }

  # No VPC connection — Silver reads S3 (Bronze) and writes S3 (Iceberg only).
  # Removing the connection eliminates ENI provisioning (~60-90s saved per run).

  execution_property {
    max_concurrent_runs = 20
  }

  tags = {
    Project     = local.project_tag
    Environment = var.environment
    ManagedBy   = "terraform"
  }

  depends_on = [aws_s3_object.generic_silver_transform_script]
}

resource "aws_s3_object" "analyze_table_script" {
  count  = var.upload_glue_scripts && !local.is_uif_stack ? 1 : 0
  bucket = aws_s3_bucket.artifacts.id
  key    = "${local.glue_scripts_prefix}/analyze-table.py"
  source = "${path.module}/../../src/apps/pipeline/analyze-table.py"
  etag   = filemd5("${path.module}/../../src/apps/pipeline/analyze-table.py")
}

# Async Oracle table analysis job (long-running).
# Triggered by Lambda API endpoint, stores results in DynamoDB.
resource "aws_glue_job" "analyze_table" {
  name            = "${local.name_prefix}-analyze-table"
  role_arn        = aws_iam_role.glue_execution.arn
  glue_version    = "5.1"
  max_retries     = 0
  timeout         = 120
  execution_class = "STANDARD"

  command {
    name            = "glueetl"
    script_location = local.glue_script_uri["analyze_table"]
    python_version  = "3"
  }

  worker_type       = "G.1X"
  number_of_workers = 2

  default_arguments = {
    "--enable-metrics"  = "true"
    "--enable-spark-ui" = "false"
    "--job-language"    = "python"
    "--TempDir"         = "s3://${var.artifacts_bucket}/${local.glue_temp_prefix}/"
    "--ANALYSIS_TABLE"  = aws_dynamodb_table.pipeline_analysis.name

    # UIFPE-355 phase 2: infrastructure names are PASSED, never derived from
    # --env. The scripts previously built echo-{env}-* themselves, so a
    # uif-echo-dev job wrote into the echo-dev lake and triggered echo-dev's
    # Silver. getResolvedOptions treats these as required — a missing value
    # fails the run instead of silently targeting the other stack.
    "--RESOURCE_PREFIX" = local.name_prefix
  }

  # Needs VPC connection for Oracle JDBC access
  connections = [aws_glue_connection.vpc_network.name]

  execution_property {
    max_concurrent_runs = 5
  }

  tags = {
    Project     = local.project_tag
    Environment = var.environment
    ManagedBy   = "terraform"
  }

  depends_on = [aws_s3_object.analyze_table_script]
}

resource "aws_s3_object" "generic_silver_reconcile_script" {
  count  = var.upload_glue_scripts && !local.is_uif_stack ? 1 : 0
  bucket = aws_s3_bucket.artifacts.id
  key    = "${local.glue_scripts_prefix}/silver-reconcile.py"
  source = "${path.module}/../../src/apps/pipeline/silver-reconcile.py"
  etag   = filemd5("${path.module}/../../src/apps/pipeline/silver-reconcile.py")
}

# Nightly hard-delete reconciliation job for Silver Iceberg tables.
# Scans Oracle PKs, anti-joins against Iceberg, and deletes orphan rows.
# STANDARD execution class required for VPC/Oracle JDBC access.
# max_retries=0 ensures partial deletes don't auto-retry (safety constraint).
resource "aws_glue_job" "generic_silver_reconcile" {
  name            = "${local.name_prefix}-silver-reconcile"
  role_arn        = aws_iam_role.glue_execution.arn
  glue_version    = "4.0"
  max_retries     = 0
  timeout         = 120
  execution_class = "STANDARD"

  command {
    name            = "glueetl"
    script_location = local.glue_script_uri["silver_reconcile"]
    python_version  = "3"
  }

  worker_type       = "G.1X"
  number_of_workers = 2

  default_arguments = {
    "--enable-metrics"          = "true"
    "--enable-spark-ui"         = "false"
    "--job-language"            = "python"
    "--TempDir"                 = "s3://${var.artifacts_bucket}/${local.glue_temp_prefix}/"
    "--CONFIG_TABLE"            = aws_dynamodb_table.glue_job_config.name
    "--METRICS_TABLE"           = aws_dynamodb_table.glue_job_metrics.name
    "--datalake-formats"        = "iceberg"
    "--enable-glue-datacatalog" = ""

    # UIFPE-355 phase 2: infrastructure names are PASSED, never derived from
    # --env. The scripts previously built echo-{env}-* themselves, so a
    # uif-echo-dev job wrote into the echo-dev lake and triggered echo-dev's
    # Silver. getResolvedOptions treats these as required — a missing value
    # fails the run instead of silently targeting the other stack.
    "--SILVER_WAREHOUSE" = "s3://${var.silver_bucket}/"
  }

  # VPC connection required for Oracle JDBC access
  connections = [aws_glue_connection.vpc_network.name]

  execution_property {
    max_concurrent_runs = 5
  }

  tags = {
    Project     = local.project_tag
    Environment = var.environment
    ManagedBy   = "terraform"
  }

  depends_on = [aws_s3_object.generic_silver_reconcile_script]
}

# ─────────────────────────────────────────────────────────────────
# GCP-native Silver jobs (Phase 1 — Option 4)
#
# These mirror the AWS Silver jobs but target Iceberg tables stored in GCS
# via the Hadoop catalog + post-write BigLake DDL fallback (because
# BigQueryMetastoreCatalog is not in the pinned iceberg-gcp-bundle 1.6.1).
#
# JAR distribution is handled in glue_jars.tf — versions pinned by the
# Phase 0 probe on 2026-06-10.
# ─────────────────────────────────────────────────────────────────

resource "aws_s3_object" "generic_silver_transform_gcp_iceberg_script" {
  count  = var.upload_glue_scripts && !local.is_uif_stack ? 1 : 0
  bucket = aws_s3_bucket.artifacts.id
  key    = "${local.glue_scripts_prefix}/silver-transform-gcs-iceberg.py"
  source = "${path.module}/../../src/apps/pipeline/silver-transform-gcs-iceberg.py"
  etag   = filemd5("${path.module}/../../src/apps/pipeline/silver-transform-gcs-iceberg.py")
}

# GCP-native Silver transform — reads Bronze Parquet (S3 or GCS), writes
# Iceberg to GCS via Hadoop catalog, then issues a post-write BigLake DDL
# from inside the Glue runtime (needs google-cloud-bigquery on the workers).
#
# STANDARD (not FLEX) because: (a) BigQuery DDL requires reliable egress to
# googleapis.com — cold starts on FLEX have flaky network init; (b) the
# Hadoop catalog path requires consistent metadata fsync semantics that
# benefit from non-preempted workers.
resource "aws_glue_job" "generic_silver_transform_gcp_iceberg" {
  name            = "${local.name_prefix}-silver-transform-gcs-iceberg"
  role_arn        = aws_iam_role.glue_execution.arn
  glue_version    = "5.1"
  max_retries     = 1
  timeout         = 120
  execution_class = "STANDARD"

  command {
    name            = "glueetl"
    script_location = local.glue_script_uri["silver_transform_gcs_iceberg"]
    python_version  = "3"
  }

  worker_type       = "G.2X"
  number_of_workers = 16

  default_arguments = {
    "--enable-metrics"            = "true"
    "--enable-spark-ui"           = "false"
    "--job-language"              = "python"
    "--TempDir"                   = "s3://${var.artifacts_bucket}/${local.glue_temp_prefix}/"
    "--CONFIG_TABLE"              = aws_dynamodb_table.glue_job_config.name
    "--METRICS_TABLE"             = aws_dynamodb_table.glue_job_metrics.name
    "--datalake-formats"          = "iceberg"
    "--env"                       = var.environment
    "--extra-jars"                = local.gcp_glue_extra_jars
    "--additional-python-modules" = "google-cloud-bigquery==3.24.0,google-cloud-storage==2.18.0"
    # iceberg-gcp-bundle has unrelocated com.google.cloud.storage.* classes;
    # without user-jars-first, broadcast deserialisation on executors hits
    # NoClassDefFoundError. See memory: feedback_gcp_glue_classpath_auth.
    "--user-jars-first" = "true"
  }

  # No VPC connection — Silver reads Bronze (S3/GCS) and writes Iceberg (GCS).
  # BigQuery DDL goes over public googleapis.com endpoints with SA auth.

  execution_property {
    max_concurrent_runs = 20
  }

  tags = {
    Project     = local.project_tag
    Environment = var.environment
    ManagedBy   = "terraform"
    Purpose     = "gcp-native-mode"
  }

  depends_on = [
    aws_s3_object.generic_silver_transform_gcp_iceberg_script,
    aws_s3_object.gcp_glue_jars["gcs_connector"],
    aws_s3_object.gcp_glue_jars["iceberg_gcp_bundle"],
  ]
}

resource "aws_s3_object" "generic_silver_reconcile_gcp_script" {
  count  = var.upload_glue_scripts && !local.is_uif_stack ? 1 : 0
  bucket = aws_s3_bucket.artifacts.id
  key    = "${local.glue_scripts_prefix}/silver-reconcile-gcs.py"
  source = "${path.module}/../../src/apps/pipeline/silver-reconcile-gcs.py"
  etag   = filemd5("${path.module}/../../src/apps/pipeline/silver-reconcile-gcs.py")
}

# GCP-native Silver reconcile — scans Oracle PKs (via JDBC), anti-joins
# against the GCS Iceberg / BigLake table, deletes orphans. VPC connection
# is retained because we still pull source-of-truth PKs from Oracle.
#
# Glue 5.1 (not 4.0 like the AWS reconcile) is required for the iceberg-gcp
# bundle classpath. STANDARD execution class for JDBC + GCS reliability.
# Slightly more workers than AWS reconcile because BigLake reads have higher
# per-task latency than direct Glue Catalog access.
resource "aws_glue_job" "generic_silver_reconcile_gcp" {
  name            = "${local.name_prefix}-silver-reconcile-gcs"
  role_arn        = aws_iam_role.glue_execution.arn
  glue_version    = "5.1"
  max_retries     = 0
  timeout         = 120
  execution_class = "STANDARD"

  command {
    name            = "glueetl"
    script_location = local.glue_script_uri["silver_reconcile_gcs"]
    python_version  = "3"
  }

  worker_type       = "G.1X"
  number_of_workers = 4

  default_arguments = {
    "--enable-metrics"            = "true"
    "--enable-spark-ui"           = "false"
    "--job-language"              = "python"
    "--TempDir"                   = "s3://${var.artifacts_bucket}/${local.glue_temp_prefix}/"
    "--CONFIG_TABLE"              = aws_dynamodb_table.glue_job_config.name
    "--METRICS_TABLE"             = aws_dynamodb_table.glue_job_metrics.name
    "--datalake-formats"          = "iceberg"
    "--enable-glue-datacatalog"   = ""
    "--extra-jars"                = local.gcp_glue_extra_jars
    "--additional-python-modules" = "google-cloud-bigquery==3.24.0,google-cloud-storage==2.18.0"
    "--user-jars-first"           = "true"
  }

  # VPC connection required for Oracle JDBC access (source-of-truth PK scan).
  connections = [aws_glue_connection.vpc_network.name]

  execution_property {
    max_concurrent_runs = 5
  }

  tags = {
    Project     = local.project_tag
    Environment = var.environment
    ManagedBy   = "terraform"
    Purpose     = "gcp-native-mode"
  }

  depends_on = [
    aws_s3_object.generic_silver_reconcile_gcp_script,
    aws_s3_object.gcp_glue_jars["gcs_connector"],
    aws_s3_object.gcp_glue_jars["iceberg_gcp_bundle"],
  ]
}

# ─────────────────────────────────────────────────────────────────
# BQ-native Silver — for pipelines with silver.write_mode="bq_native".
# Bronze still on GCS Parquet; Silver writes a native BigQuery table via
# google-cloud-bigquery (Parquet→GCS staging→BQ load→BQ MERGE). No Iceberg,
# no BigLake external table, no --user-jars-first, no iceberg-gcp-bundle.
# Only the GCS connector JAR is needed so Spark can read Bronze from gs://.
# ─────────────────────────────────────────────────────────────────

resource "aws_s3_object" "bq_partition_ddl_lib" {
  count  = var.upload_glue_scripts && !local.is_uif_stack ? 1 : 0
  bucket = aws_s3_bucket.artifacts.id
  key    = "${local.glue_scripts_prefix}/bq_partition_ddl.py"
  source = "${path.module}/../../src/libs/bq_partition_ddl.py"
  etag   = filemd5("${path.module}/../../src/libs/bq_partition_ddl.py")
}

resource "aws_s3_object" "generic_silver_transform_gcp_bq_script" {
  count  = var.upload_glue_scripts && !local.is_uif_stack ? 1 : 0
  bucket = aws_s3_bucket.artifacts.id
  key    = "${local.glue_scripts_prefix}/silver-transform-bq.py"
  source = "${path.module}/../../src/apps/pipeline/silver-transform-bq.py"
  etag   = filemd5("${path.module}/../../src/apps/pipeline/silver-transform-bq.py")
}

resource "aws_glue_job" "generic_silver_transform_gcp_bq" {
  name            = "${local.name_prefix}-silver-transform-bq"
  role_arn        = aws_iam_role.glue_execution.arn
  glue_version    = "5.1"
  max_retries     = 1
  timeout         = 120
  execution_class = "STANDARD"

  command {
    name            = "glueetl"
    script_location = local.glue_script_uri["silver_transform_bq"]
    python_version  = "3"
  }

  worker_type       = "G.2X"
  number_of_workers = 16

  default_arguments = {
    "--enable-metrics"  = "true"
    "--enable-spark-ui" = "false"
    "--job-language"    = "python"
    "--TempDir"         = "s3://${var.artifacts_bucket}/${local.glue_temp_prefix}/"
    "--CONFIG_TABLE"    = aws_dynamodb_table.glue_job_config.name
    "--METRICS_TABLE"   = aws_dynamodb_table.glue_job_metrics.name
    # Only the GCS connector — no Iceberg JAR.
    "--extra-jars"                = "s3://${var.artifacts_bucket}/${local.glue_jars_prefix}/${local.gcp_glue_jars.gcs_connector.filename}"
    "--env"                       = var.environment
    "--additional-python-modules" = "google-cloud-bigquery==3.24.0,google-cloud-storage==2.18.0"
    "--extra-py-files"            = local.glue_script_uri["bq_partition_ddl"]
  }

  # No VPC connection — Silver reads Bronze (GCS) and writes BQ.
  # BigQuery requests go over public googleapis.com endpoints with SA auth.

  execution_property {
    max_concurrent_runs = 20
  }

  tags = {
    Project     = local.project_tag
    Environment = var.environment
    ManagedBy   = "terraform"
    Purpose     = "gcp-native-mode"
  }

  depends_on = [
    aws_s3_object.bq_partition_ddl_lib,
    aws_s3_object.generic_silver_transform_gcp_bq_script,
    aws_s3_object.gcp_glue_jars["gcs_connector"],
  ]
}

resource "aws_s3_object" "generic_silver_reconcile_gcp_bq_script" {
  count  = var.upload_glue_scripts && !local.is_uif_stack ? 1 : 0
  bucket = aws_s3_bucket.artifacts.id
  key    = "${local.glue_scripts_prefix}/silver-reconcile-bq.py"
  source = "${path.module}/../../src/apps/pipeline/silver-reconcile-bq.py"
  etag   = filemd5("${path.module}/../../src/apps/pipeline/silver-reconcile-bq.py")
}

resource "aws_glue_job" "generic_silver_reconcile_gcp_bq" {
  name            = "${local.name_prefix}-silver-reconcile-bq"
  role_arn        = aws_iam_role.glue_execution.arn
  glue_version    = "5.1"
  max_retries     = 0
  timeout         = 120
  execution_class = "STANDARD"

  command {
    name            = "glueetl"
    script_location = local.glue_script_uri["silver_reconcile_bq"]
    python_version  = "3"
  }

  worker_type       = "G.1X"
  number_of_workers = 4

  default_arguments = {
    "--enable-metrics"            = "true"
    "--enable-spark-ui"           = "false"
    "--job-language"              = "python"
    "--TempDir"                   = "s3://${var.artifacts_bucket}/${local.glue_temp_prefix}/"
    "--CONFIG_TABLE"              = aws_dynamodb_table.glue_job_config.name
    "--METRICS_TABLE"             = aws_dynamodb_table.glue_job_metrics.name
    "--extra-jars"                = "s3://${var.artifacts_bucket}/${local.glue_jars_prefix}/${local.gcp_glue_jars.gcs_connector.filename}"
    "--additional-python-modules" = "google-cloud-bigquery==3.24.0,google-cloud-storage==2.18.0"
  }

  # VPC connection required for Oracle JDBC access (source-of-truth PK scan).
  connections = [aws_glue_connection.vpc_network.name]

  execution_property {
    max_concurrent_runs = 50
  }

  job_run_queuing_enabled = true

  tags = {
    Project     = local.project_tag
    Environment = var.environment
    ManagedBy   = "terraform"
    Purpose     = "gcp-native-mode"
  }

  depends_on = [
    aws_s3_object.generic_silver_reconcile_gcp_bq_script,
    aws_s3_object.gcp_glue_jars["gcs_connector"],
  ]
}

# ─────────────────────────────────────────────────────────────────
# Silver BQ-native (Python Shell — Phase 2 of silver-bq-native-migration)
#
# Replaces the Spark `generic_silver_transform_gcp_bq` job for pipelines with
# silver.write_mode="bq_native". Pure Python + google-cloud-bigquery — all
# compute runs in BigQuery slots. No Spark cluster, no GCS connector, no
# cross-cloud Parquet shuffle. Initial loads complete in single-digit minutes.
#
# Sizing: max_capacity = 1.0 DPU ≈ $0.06/hr. The job mostly polls BQ jobs;
# CPU usage is trivial. 0.0625 DPU also works but 1.0 gives more memory
# headroom for the bigquery client during large schema introspection.
# ─────────────────────────────────────────────────────────────────

resource "aws_s3_object" "generic_silver_bq_native_script" {
  count  = var.upload_glue_scripts && !local.is_uif_stack ? 1 : 0
  bucket = aws_s3_bucket.artifacts.id
  key    = "${local.glue_scripts_prefix}/silver-transform-bq-native.py"
  source = "${path.module}/../../src/apps/pipeline/silver-transform-bq-native.py"
  etag   = filemd5("${path.module}/../../src/apps/pipeline/silver-transform-bq-native.py")
}

resource "aws_glue_job" "generic_silver_bq_native" {
  name         = "${local.name_prefix}-silver-transform-bq-native"
  role_arn     = aws_iam_role.glue_execution.arn
  glue_version = "3.0" # required for pythonshell
  max_retries  = 1
  timeout      = 240 # slot-constrained project: a contended MERGE can exceed 60min; better to finish late than die and re-burn slots

  command {
    name            = "pythonshell"
    script_location = local.glue_script_uri["silver_transform_bq_native"]
    python_version  = "3.9"
  }

  max_capacity = 1.0 # ~$0.06/hr; pure orchestration, BQ does the work

  default_arguments = {
    "--CONFIG_TABLE"  = aws_dynamodb_table.glue_job_config.name
    "--METRICS_TABLE" = aws_dynamodb_table.glue_job_metrics.name
    "--env"           = var.environment
    # google-cloud-bigquery pulls in google-cloud-core, google-auth, requests, etc.
    "--additional-python-modules" = "google-cloud-bigquery==3.24.0"
    "--extra-py-files"            = local.glue_script_uri["bq_partition_ddl"]

    # UIFPE-355 phase 2: infrastructure names are PASSED, never derived from
    # --env. The scripts previously built echo-{env}-* themselves, so a
    # uif-echo-dev job wrote into the echo-dev lake and triggered echo-dev's
    # Silver. getResolvedOptions treats these as required — a missing value
    # fails the run instead of silently targeting the other stack.
    "--RESOURCE_PREFIX" = local.name_prefix
  }

  # No VPC connection — Silver reads Bronze (GCS) via BQ LOAD/external table,
  # writes managed BQ tables. All traffic is to AWS Secrets Manager + DynamoDB
  # + googleapis.com over public endpoints.

  execution_property {
    max_concurrent_runs = 20
  }

  tags = {
    Project     = local.project_tag
    Environment = var.environment
    ManagedBy   = "terraform"
    Purpose     = "gcp-native-mode"
    Variant     = "bq-native-pythonshell"
  }

  depends_on = [
    aws_s3_object.bq_partition_ddl_lib,
    aws_s3_object.generic_silver_bq_native_script,
  ]
}

# glue_connection.tf
# NETWORK-type Glue connection that places Glue workers inside an existing VPC subnet.
# This is purely network plumbing — credentials stay in Secrets Manager, not here.
#
# Inputs required (pass via terraform.tfvars or -var):
#   subnet_id              — private subnet where Oracle is reachable
#   glue_security_group_id — SG for Glue ENIs; must allow TCP 1521 outbound to Oracle


# Look up the subnet so we can derive vpc_id and availability_zone automatically.
data "aws_subnet" "glue" {
  id = var.subnet_id
}

# Security group for Glue ENIs.
# Rules are managed as separate aws_security_group_rule resources below
# to allow the self-referencing rules Glue requires for worker communication.
resource "aws_security_group" "glue_eni" {
  name        = "${local.name_prefix}-glue-eni-sg"
  description = "Glue ENI SG - egress to Oracle (1521) and AWS endpoints (443)"
  vpc_id      = data.aws_subnet.glue.vpc_id

  # No inline ingress/egress blocks — all rules are separate aws_security_group_rule
  # resources below. Setting ingress=[] / egress=[] here would cause Terraform to
  # continuously destroy the rules created by those resources on every plan.

  lifecycle {
    ignore_changes = [ingress, egress]
  }

  tags = {
    Name        = "${local.name_prefix}-glue-eni-sg"
    Project     = local.project_tag
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

# Glue requirement: workers must be able to reach each other on all ports.
# Both ingress and egress self-referencing rules are mandatory.
resource "aws_security_group_rule" "glue_self_ingress" {
  type                     = "ingress"
  from_port                = 0
  to_port                  = 0
  protocol                 = "-1"
  security_group_id        = aws_security_group.glue_eni.id
  source_security_group_id = aws_security_group.glue_eni.id
  description              = "Glue worker-to-worker (required by Glue VPC)"
}

resource "aws_security_group_rule" "glue_self_egress" {
  type                     = "egress"
  from_port                = 0
  to_port                  = 0
  protocol                 = "-1"
  security_group_id        = aws_security_group.glue_eni.id
  source_security_group_id = aws_security_group.glue_eni.id
  description              = "Glue worker-to-worker (required by Glue VPC)"
}

# UNRESTRICTED EGRESS, DELIBERATE, ON ALL THREE RULES BELOW (UIFPE-515).
#
# trivy reports AVD-AWS-0104 CRITICAL on each, and each is accepted for the
# same reason: the destinations are not knowable as CIDRs from here.
#
#   1500-1599 / 16710   Oracle sources. The hosts are resolved per pipeline
#                       from DynamoDB config at RUN time, not declared in
#                       Terraform, so this SG cannot enumerate them. 16710 is
#                       PPDEBS's non-standard port, per the comment below.
#   443                 AWS service endpoints, plus GCS and BigQuery for the
#                       GCP-native pipeline variants. Google publishes those
#                       ranges as a changing list, so pinning them here would
#                       fail closed on their next change, silently, inside a
#                       Glue job.
#
# The egress is bounded by PORT rather than by address, which is the control
# that is actually available: 0.0.0.0/0 on tcp/1500-1599 is not the same
# exposure as 0.0.0.0/0 on all protocols. Note the rule directly above --
# glue_self_egress, protocol "-1" -- is worker-to-worker and scoped to the SG
# itself, so it is not one of the three and is not flagged.
#
# Narrowing these needs VPC endpoints for the AWS services and a resolved
# source inventory for Oracle. That is infrastructure work in its own right,
# not a line change here.
#
# Suppressed rather than left failing on owner instruction, following
# stacks/apicurio's precedent for the same check (bff.tf:218, ecs.tf:64). The
# directive must be the LAST comment line before the block or trivy silently
# ignores it -- apicurio established that by testing, see its qe/ci.sh.
# trivy:ignore:AVD-AWS-0104
resource "aws_security_group_rule" "glue_egress_oracle" {
  type              = "egress"
  from_port         = 1500
  to_port           = 1599
  protocol          = "tcp"
  security_group_id = aws_security_group.glue_eni.id
  cidr_blocks       = ["0.0.0.0/0"]
  description       = "Oracle JDBC (1500-1599)"
}

# PPDEBS and other EBS instances listen on non-standard ports (e.g. 16710).
# Legacy ingestion-framework SG had this added manually; codify here for EDS.
# See the accepted-egress note above glue_egress_oracle for why 0.0.0.0/0.
# trivy:ignore:AVD-AWS-0104
resource "aws_security_group_rule" "glue_egress_oracle_16710" {
  type              = "egress"
  from_port         = 16710
  to_port           = 16710
  protocol          = "tcp"
  security_group_id = aws_security_group.glue_eni.id
  cidr_blocks       = ["0.0.0.0/0"]
  description       = "Oracle database port 16710"
}

# See the accepted-egress note above glue_egress_oracle. This is the rule that
# also carries GCS and BigQuery, whose ranges Google changes.
# trivy:ignore:AVD-AWS-0104
resource "aws_security_group_rule" "glue_egress_https" {
  type              = "egress"
  from_port         = 443
  to_port           = 443
  protocol          = "tcp"
  security_group_id = aws_security_group.glue_eni.id
  cidr_blocks       = ["0.0.0.0/0"]
  description       = "AWS service endpoints (S3, DynamoDB, Secrets Manager)"
}

# NETWORK type = VPC attachment only. No JDBC URL or credentials embedded.
resource "aws_glue_connection" "vpc_network" {
  name            = "${local.name_prefix}-vpc-network"
  connection_type = "NETWORK"

  # NETWORK connections don't need connection_properties, but the block
  # must be present (empty map) to satisfy the provider schema.
  connection_properties = {}

  physical_connection_requirements {
    availability_zone      = data.aws_subnet.glue.availability_zone
    security_group_id_list = [aws_security_group.glue_eni.id]
    subnet_id              = var.subnet_id
  }

  tags = {
    Project     = local.project_tag
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}



# glue_jars.tf
#
# GCP-native ingestion mode JAR distribution.
#
# JAR versions below were pinned by the Phase 0 classpath probe on 2026-06-10
# (Glue 5.1, eu-west-1). The probe confirmed:
#   - gcs-connector-hadoop3-2.2.22-shaded.jar provides a working
#     GoogleHadoopFileSystem (fs.gs.impl) on Glue 5.1's Hadoop 3 runtime.
#   - iceberg-gcp-bundle-1.6.1.jar provides GCSFileIO + SparkCatalog wiring
#     compatible with the Iceberg runtime shipped via --datalake-formats=iceberg.
#   - BigQueryMetastoreCatalog is NOT present in this bundle; Silver uses
#     Hadoop catalog + post-write BigLake DDL fallback.
#
# To update versions: edit the `locals.gcp_glue_jars` block below. The
# null_resource trigger is keyed on the version string, so changing it forces
# a re-download. aws_s3_object uses filemd5 + source_hash so the S3 object
# is replaced when the local cache file changes.
#
# The local cache dir (`${path.module}/.cache/jars/`) is gitignored — see
# `.gitignore` at the repo root (infra/.cache/).

locals {
  gcp_glue_jars = {
    gcs_connector = {
      version  = "2.2.22"
      filename = "gcs-connector-hadoop3-2.2.22-shaded.jar"
      url      = "https://repo1.maven.org/maven2/com/google/cloud/bigdataoss/gcs-connector/hadoop3-2.2.22/gcs-connector-hadoop3-2.2.22-shaded.jar"
    }
    iceberg_gcp_bundle = {
      version  = "1.6.1"
      filename = "iceberg-gcp-bundle-1.6.1.jar"
      url      = "https://repo1.maven.org/maven2/org/apache/iceberg/iceberg-gcp-bundle/1.6.1/iceberg-gcp-bundle-1.6.1.jar"
    }
  }

  # Comma-separated S3 URIs for the --extra-jars Glue argument.
  gcp_glue_extra_jars = join(",", [
    "s3://${var.artifacts_bucket}/${local.glue_jars_prefix}/${local.gcp_glue_jars.gcs_connector.filename}",
    "s3://${var.artifacts_bucket}/${local.glue_jars_prefix}/${local.gcp_glue_jars.iceberg_gcp_bundle.filename}",
  ])

  # Local on-disk cache directory used to stage downloaded JARs before upload.
  gcp_glue_jar_cache_dir = "${path.module}/.cache/jars"
}

# Download the JARs from Maven Central into a local cache directory.
# Triggers re-download only when the pinned version changes. We use one
# null_resource per JAR via for_each so the trigger is precise.
resource "null_resource" "gcp_glue_jar_download" {
  for_each = local.gcp_glue_jars

  triggers = {
    version = each.value.version
    url     = each.value.url
  }

  provisioner "local-exec" {
    # GitHub Actions runs on Linux; local deploy hosts use bash (Git Bash/WSL on Windows).
    interpreter = ["/bin/bash", "-c"]
    command     = <<-EOT
      mkdir -p '${local.gcp_glue_jar_cache_dir}'
      dest='${local.gcp_glue_jar_cache_dir}/${each.value.filename}'
      if [ ! -f "$dest" ]; then
        curl -fsSL '${each.value.url}' -o "$dest"
      fi
    EOT
  }
}

# Upload the cached JARs to the Glue scripts S3 bucket. source_hash forces
# replacement when the local file changes (post-download). depends_on ensures
# the download runs first on initial apply.
resource "aws_s3_object" "gcp_glue_jars" {
  for_each = local.gcp_glue_jars

  bucket = aws_s3_bucket.artifacts.id
  key    = "${local.glue_jars_prefix}/${each.value.filename}"
  source = "${local.gcp_glue_jar_cache_dir}/${each.value.filename}"
  # Tie the S3 object's identity to the download null_resource so the upload
  # happens iff a download just ran. Using fileexists()+filemd5() here causes
  # Terraform's plan-vs-apply consistency check to fail because the file does
  # not exist at plan time but does after null_resource runs in the same apply.
  source_hash = null_resource.gcp_glue_jar_download[each.key].id

  # NO `tags` BLOCK, AND THE LIMIT IS WHY. An S3 OBJECT accepts at most 10 tags
  # -- ten, not the 50 almost every other AWS resource allows. local.default_tags
  # contributes 8 on a uif stack, this resource used to add 4, and Project
  # overlapped, so the effective set was 11 and CreateMultipartUpload was
  # rejected outright:
  #
  #   api error BadRequest: Object tags cannot be greater than 10
  #
  # It failed the first uif-echo-uat apply (run 36253830424) at 86 of 210
  # resources. The three keys dropped alongside Project were carrying nothing
  # the defaults do not: Environment duplicates t_environment, ManagedBy is true
  # of every resource in this root, and Purpose described a mode that
  # local.gcp_glue_jars already names. This is now the only aws_s3_object in
  # either root with no `tags` block of its own -- which is to say, it matches
  # the other 25.
  #
  # Anything added back here must keep default_tags + these <= 10. Nothing warns
  # you: the plan is clean, because the count is only checked at PutObject.

  depends_on = [null_resource.gcp_glue_jar_download]
}

# glue_job_phase0_probe.tf
# Dev-only Glue job for GCP-native classpath + auth verification.
#
# Originally a one-shot Phase 0 probe; kept around as a permanent canary so
# CI catches GCS auth or iceberg-gcp-bundle regressions BEFORE they break
# the next end-to-end smoke. The probe writes a 1-row parquet to GCS using
# the same inline-credentials pattern as the production Bronze/Silver jobs
# (see [[feedback_gcp_glue_classpath_auth]]). Any failure here is a real
# failure — not a soft warning.

# Dev-only: uat/prod use eds/{env}/gip/cloud/gcp in pipeline configs, not
# the legacy gcp-bronze-replicator-{env} name.
data "aws_secretsmanager_secret" "gcp_bronze_replicator_phase0" {
  count = var.environment == "dev" && !local.is_uif_stack ? 1 : 0
  name  = "gcp-bronze-replicator-${var.environment}"
}
#
# Submission (after `terraform apply`):
#   aws s3 cp ../jobs/iceberg-gcs-probe.py s3://<glue-scripts-bucket>/jobs/
#   aws glue start-job-run --job-name phase0-iceberg-gcp-probe-${ENV} \
#     --arguments '{"--extra-jars": "s3://<bucket>/jars/gcs-connector-hadoop3-2.2.22-shaded.jar,s3://<bucket>/jars/iceberg-gcp-bundle-1.6.1.jar"}'

resource "aws_s3_object" "phase0_iceberg_gcp_probe_script" {
  count  = var.environment == "dev" && !local.is_uif_stack ? 1 : 0
  bucket = aws_s3_bucket.artifacts.id
  key    = "${local.glue_scripts_prefix}/iceberg-gcs-probe.py"
  source = "${path.module}/../../src/apps/pipeline/iceberg-gcs-probe.py"
  etag   = filemd5("${path.module}/../../src/apps/pipeline/iceberg-gcs-probe.py")
}

resource "aws_glue_job" "phase0_iceberg_gcp_probe" {
  count           = var.environment == "dev" && !local.is_uif_stack ? 1 : 0
  name            = "${local.name_prefix}-iceberg-gcs-probe"
  role_arn        = aws_iam_role.glue_execution.arn
  glue_version    = "5.1"
  max_retries     = 0
  timeout         = 15
  execution_class = "STANDARD"

  command {
    name            = "glueetl"
    script_location = local.glue_script_uri["iceberg_gcs_probe"]
    python_version  = "3"
  }

  worker_type       = "G.1X"
  number_of_workers = 2

  default_arguments = {
    "--enable-metrics"   = "true"
    "--enable-spark-ui"  = "false"
    "--job-language"     = "python"
    "--TempDir"          = "s3://${var.artifacts_bucket}/${local.glue_temp_prefix}/"
    "--datalake-formats" = "iceberg"
    "--extra-jars"       = local.gcp_glue_extra_jars
    "--user-jars-first"  = "true"
    # Probe-specific args. The GCS write is non-conditional once the args are
    # present — failure of any of these surfaces as Glue job FAILED, which is
    # exactly what we want so CI catches GCS auth/classpath regressions before
    # the next end-to-end smoke. Values are dev-only — the entire file is
    # marked throwaway (see header comment).
    "--gcs_write_uri"  = "gs://echo-${var.environment}-silver/.phase0_probe/${var.environment}/probe.parquet"
    "--gcs_secret_arn" = data.aws_secretsmanager_secret.gcp_bronze_replicator_phase0[0].arn
  }

  execution_property {
    max_concurrent_runs = 2
  }

  tags = {
    Project     = local.project_tag
    Environment = var.environment
    ManagedBy   = "terraform"
    Purpose     = "phase0-classpath-probe"
  }

  depends_on = [aws_s3_object.phase0_iceberg_gcp_probe_script]
}
