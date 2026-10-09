# Gold pipelines config table.
# CUTOVER NOTE: this table exists today in the platform stack's state
# (uif-relay-echo). Do not `apply` before the cutover:
#   (platform repo) terraform state rm aws_dynamodb_table.gold_pipelines_config
#   (this repo)     terraform import aws_dynamodb_table.gold_pipelines_config uif-echo-{env}-gold-pipeline-config
# The shared metrics table is uif-echo-{env}-pipeline-metrics, the standard
# estate's, referenced by name only (locals.tf). The legacy
# echo-{env}-glue-job-metrics is no longer used by gold.

resource "aws_dynamodb_table" "gold_pipelines_config" {
  name         = "${local.name_prefix}-gold-pipeline-config"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "gold_id"
  range_key    = "version"

  attribute {
    name = "gold_id"
    type = "S"
  }

  attribute {
    name = "version"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  tags = local.common_tags

  # This table holds live watermarks/cursors/channel registries the
  # transform writes at runtime — not reproducible from git (unlike the
  # schema/mapping artifacts). Mirrors the buckets' prevent_destroy in
  # s3.tf.
  lifecycle {
    prevent_destroy = true
  }
}
