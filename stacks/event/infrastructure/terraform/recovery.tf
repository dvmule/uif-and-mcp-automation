# The recovery job (Event Fabric Recovery) - re-publishes Table 3 rows that
# never reached the bus (PUBLISH_FAILED), per docs/publish-metrics.md write
# rules 2-3. Steward-triggered per Gold run:
#
#   aws glue start-job-run --job-name uif-event-{env}-fabric-recovery \
#     --arguments '{"--domain":"product","--gold_run_id":"jr_...","--started_by":"you@pearson.com"}'
#
# Domain-agnostic like every stack component: the domain rides the
# invocation, the bus is derived by convention, the payloads are the stored
# envelopes - onboarding a domain changes nothing here.

resource "aws_iam_role" "event_fabric_recovery" {
  name = "${local.event_stack_prefix}-fabric-recovery-role"

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

resource "aws_iam_role_policy" "event_fabric_recovery" {
  name = "${local.event_stack_prefix}-fabric-recovery-policy"
  role = aws_iam_role.event_fabric_recovery.id

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
        Effect   = "Allow"
        Action   = ["s3:ListBucket", "s3:GetBucketLocation"]
        Resource = "arn:aws:s3:::${var.gold_artifacts_bucket}"
      },
      {
        # Table 3: the gsi_recovery sweep, the base-row payload reads
        # (payload is excluded from every GSI projection), and the
        # rules-2/3 settlements on the SAME rows.
        Effect = "Allow"
        Action = ["dynamodb:Query", "dynamodb:GetItem", "dynamodb:UpdateItem"]
        Resource = [
          aws_dynamodb_table.glue_metrics.arn,
          "${aws_dynamodb_table.glue_metrics.arn}/index/*",
        ]
      },
      {
        # Table 2: this run's REPLAY row, plus the replay_count bump on the
        # failed run's own INCREMENTAL row (found via gsi_gold_run).
        Effect = "Allow"
        Action = ["dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:Query"]
        Resource = [
          aws_dynamodb_table.pipeline_metrics.arn,
          "${aws_dynamodb_table.pipeline_metrics.arn}/index/*",
        ]
      },
      {
        # Republish to any DOMAIN bus by convention - same wildcard as the
        # publish job.
        Effect   = "Allow"
        Action   = ["events:PutEvents"]
        Resource = "arn:aws:events:${var.aws_region}:${data.aws_caller_identity.current.account_id}:event-bus/${local.event_stack_prefix}-*"
      },
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:/aws-glue/*"
      },
    ]
  })
}

# max_retries = 0 on purpose, like the publish job: an automatic retry would
# re-run settlements blindly; a recovery is a deliberate act, and re-running
# it deliberately is safe (every settlement is conditional).
resource "aws_glue_job" "event_fabric_recovery" {
  name        = "${local.event_stack_prefix}-fabric-recovery"
  description = "Re-publishes PUBLISH_FAILED publish-metrics rows for a Gold run - byte-identical stored envelopes, settled on the same rows (rules 2-3)."
  role_arn    = aws_iam_role.event_fabric_recovery.arn
  max_retries = 0
  timeout     = 30 # minutes

  command {
    name = "pythonshell"
    # Content-addressed: a changed script is a new key, never an overwrite.
    script_location = local.glue_script_uri["event_fabric_recovery"]
    python_version  = "3.9"
  }

  max_capacity = 0.0625

  execution_property {
    max_concurrent_runs = 2
  }

  default_arguments = {
    "--publish_metrics_table"  = aws_dynamodb_table.glue_metrics.name
    "--pipeline_metrics_table" = aws_dynamodb_table.pipeline_metrics.name
    "--event_stack_prefix"     = local.event_stack_prefix
  }

  tags = local.common_tags
}
