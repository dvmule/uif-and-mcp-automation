# glue.tf — the transform runtime (UIFPE-209).
#
# The heavy transform step of an export batch runs here: a managed Glue 5.0
# job executing the SAME dynamic-mapping engine the Fargate task ran, shipped
# as an --extra-py-files zip instead of an ~800 MB image we patch forever.
# D20's split is unchanged — heavy transform outside Lambda, light steps
# (deliver, record_run, emit_event) in Lambda.
#
# Proven by spike UIFPE-205 run b-glue-spike-006: same published config, same
# 3 rows, same 363 bytes as the Fargate run of that config (row order aside,
# which Spark never guarantees).
#
# The Fargate pipeline, its image and CodeBuild project were DELETED at
# UIFPE-215's probation exit (2026-09-02) after a week of clean Glue runs —
# restoring the Fargate path is a git revert of that commit plus an apply.
# Glue is now the ONLY batch runtime; the stack ships no container images.

# uif:rename-approved UIFPE-365
resource "aws_iam_role" "glue_transform" {
  name = "${local.name}-transform-role"
  # Plain ASCII only: IAM rejects a description containing characters outside
  # its allowed range, em dash included (this apply failed on exactly that).
  description = "Beacon transform runtime (Glue) - reads gold, writes staging/pickup"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "glue.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
  lifecycle {
    create_before_destroy = true
  }
}

data "aws_iam_policy_document" "glue_transform" {
  statement { # published configs in, canonical/rendered/rejected out
    sid       = "BeaconDataBucket"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket"]
    resources = [aws_s3_bucket.data.arn, "${aws_s3_bucket.data.arn}/*"]
  }
  statement {
    # Iceberg's S3FileIO reads gold with THIS role's credentials — Lake
    # Formation credential vending is not in play on either runtime (UIFPE-165
    # defect 1, re-confirmed by UIFPE-205 and by the platform team: no job in
    # this account uses LF fine-grained access). Same posture as the Fargate
    # pipeline_task, neither tighter nor looser.
    #
    # Follow-up when a sensitive domain (HR, finance) onboards: narrow these
    # to per-table prefixes (uif-echo-dev-gold/product/*) instead of the bucket.
    sid     = "S3GoldRead"
    actions = ["s3:GetObject", "s3:ListBucket"]
    resources = concat([for b in var.gold_data_buckets : "arn:aws:s3:::${b}"],
    [for b in var.gold_data_buckets : "arn:aws:s3:::${b}/*"])
  }
  statement { # table metadata for the Iceberg catalog lookup
    sid     = "GlueCatalogRead"
    actions = ["glue:GetDatabase", "glue:GetDatabases", "glue:GetTable", "glue:GetTables", "glue:GetPartition", "glue:GetPartitions"]
    resources = ["arn:aws:glue:${var.region}:${data.aws_caller_identity.current.account_id}:catalog",
      "arn:aws:glue:${var.region}:${data.aws_caller_identity.current.account_id}:database/*gold*",
    "arn:aws:glue:${var.region}:${data.aws_caller_identity.current.account_id}:table/*gold*/*"]
  }
  statement { # sftp/email destinations keep D16's secret_ref contract
    sid       = "Secrets"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = ["arn:aws:secretsmanager:${var.region}:${data.aws_caller_identity.current.account_id}:secret:${local.name}/*"]
  }
  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["arn:aws:logs:${var.region}:${data.aws_caller_identity.current.account_id}:log-group:/aws-glue/*"]
  }
  statement { # --enable-metrics publishes job metrics
    sid       = "Metrics"
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "glue_transform" {
  name   = "beacon-glue-transform"
  role   = aws_iam_role.glue_transform.id
  policy = data.aws_iam_policy_document.glue_transform.json
}

# Catalog/metadata access is LF-governed even though data reads are not —
# mirrors pipeline_gold in lakeformation.tf (tag-based; see the header there).
resource "aws_lakeformation_permissions" "glue_transform_gold" {
  count       = length(var.gold_databases) > 0 ? 1 : 0
  principal   = aws_iam_role.glue_transform.arn
  permissions = ["SELECT", "DESCRIBE"]

  lf_tag_policy {
    resource_type = "TABLE"
    expression {
      key    = local.gold_lf_tag_key
      values = [local.gold_lf_tag_value]
    }
  }
}

resource "aws_glue_job" "transform" {
  name        = "${local.name}-transform"
  description = "Beacon export transform - runs the dynamic-mapping engine (UIFPE-209)"
  role_arn    = aws_iam_role.glue_transform.arn

  glue_version      = "5.0"
  worker_type       = var.transform_worker_type
  number_of_workers = var.transform_workers
  # A retry would silently re-run a whole export batch: the state machine's
  # catch owns failure, and D4 requires a breach to stop, not to be retried.
  max_retries = 0
  timeout     = var.transform_timeout_minutes

  command {
    name            = "glueetl"
    script_location = "s3://${aws_s3_bucket.data.bucket}/code/glue/glue_entrypoint.py"
    python_version  = "3"
  }

  default_arguments = {
    "--extra-py-files"   = "s3://${aws_s3_bucket.data.bucket}/code/glue/beacon-engine.zip"
    "--datalake-formats" = "iceberg"
    # the engine's config validator imports sqlglot; the retired Fargate image
    # installed it from requirements.txt, Glue must be told explicitly
    "--additional-python-modules" = "sqlglot==30.15.0"
    # XML rendering (UIFPE-250): spark-xml + the compile deps Glue lacks, staged
    # to S3 by the deploy workflow (Maven Central, pinned SHA-256). Same story
    # as sqlglot — the retired image baked these in, Glue must be handed them.
    "--extra-jars" = join(",", [
      "s3://${aws_s3_bucket.data.bucket}/code/glue/jars/spark-xml_2.12-0.18.0.jar",
      "s3://${aws_s3_bucket.data.bucket}/code/glue/jars/txw2-3.0.2.jar",
      "s3://${aws_s3_bucket.data.bucket}/code/glue/jars/xmlschema-core-2.3.0.jar",
      "s3://${aws_s3_bucket.data.bucket}/code/glue/jars/scala-collection-compat_2.12-2.9.0.jar",
    ])
    "--job-language"                     = "python"
    "--enable-metrics"                   = "true"
    "--enable-continuous-cloudwatch-log" = "true"
  }

  execution_property {
    # Concurrent batches across different exports are normal (each is its own
    # job run writing its own batch prefix). 10 is the ceiling that stops a
    # runaway from consuming the account's DPU capacity; per-export overlap is
    # prevented upstream, not here.
    max_concurrent_runs = 10
  }
}

variable "transform_worker_type" {
  description = "Glue worker size for the transform (G.1X = 4 vCPU/16 GB per worker)"
  type        = string
  default     = "G.1X"
}

variable "transform_workers" {
  description = "Glue workers per batch. 2 matches the retired Fargate task's capacity; raise for full-table exports with joins."
  type        = number
  default     = 2
}

variable "transform_timeout_minutes" {
  description = "Hard stop for one batch. Long batches are expected (UIFPE-209); this is the runaway backstop."
  type        = number
  default     = 60
}

output "transform_job_name" {
  value = aws_glue_job.transform.name
}
