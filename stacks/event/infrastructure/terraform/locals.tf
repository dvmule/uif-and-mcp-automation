locals {
  # uif-{stack}-{env} (stack = event) — the UIF Resource Name Lookup prefix.
  # Every stack resource AND every convention the stack relies on (domain
  # buses, delivery DLQs, subscription queues, rules) hangs off it.
  event_stack_prefix = "uif-event-${var.environment}"

  # The stack's working files (shared Glue script, Athena results) and the
  # domains' uploads (event-pipeline/domains/{domain}/...) in the shared
  # gold-artifacts bucket — ONE IAM prefix covers them all.
  working_prefix = "event-pipeline"

  # Every gold database by the platform convention uif_echo_{env}_gold_{domain}
  # (pipelines/base/*.yaml) — IAM statements wildcard on this so a new
  # domain's catalog is covered the day it exists.
  gold_db_wildcard = "uif_echo_${var.environment}_gold_*"

  # The control bus carrying GoldMergeCompleted (echo gold announcer).
  gold_merge_bus = var.gold_merge_bus_name != "" ? var.gold_merge_bus_name : "uif-event-${var.environment}-bus"

  # ── Release artifacts (stack promotion model, docs/uif-event-uat-rollout-plan.md)
  #
  # Nothing in this root reads its code from the repository. Every Lambda zip
  # and Glue script is read from this bucket at a key NAMED AFTER ITS CONTENT
  # HASH, staged by scripts/stage-release-artifacts.sh (dev) or copied from a build
  # tag (every later environment). The name is derived, not a variable: one
  # bucket per environment by convention, and promote-event.yml's copy targets
  # the same uif-event-<env>-release-artifacts.
  release_artifacts_bucket = "${local.event_stack_prefix}-release-artifacts"

  # A key built without its hash carries this, so a missing hash is a path no
  # object can be at -- and artifact_guard.tf refuses the plan rather than
  # leaving a human to spot a string in a long diff (echo's UIFPE-608).
  sha_sentinel = "ARTIFACT-SHA-NOT-SUPPLIED"

  # The six artifacts, by the manifest's own names. stage-release-artifacts.sh stages
  # exactly these; qe/test_release_artifacts.py asserts the two lists agree.
  glue_scripts = {
    event_fabric_processor = "event_fabric_processor.py"
    event_fabric_recovery  = "event_fabric_recovery.py"
  }
  lambda_artifacts = [
    "pipeline_controller",
    "dlq_recorder",
    "subscription_provisioner",
    "event_redrive",
  ]

  glue_script_uri = {
    for name, file in local.glue_scripts : name =>
    "s3://${local.release_artifacts_bucket}/glue/${lookup(var.artifact_shas, name, local.sha_sentinel)}/${file}"
  }
  lambda_s3_key = {
    for name in local.lambda_artifacts : name =>
    "lambdas/${lookup(var.artifact_shas, name, local.sha_sentinel)}.zip"
  }

  artifacts_missing_sha = sort(concat(
    [for name, uri in local.glue_script_uri : "glue/${name}" if strcontains(uri, local.sha_sentinel)],
    [for name, key in local.lambda_s3_key : "lambda/${name}" if strcontains(key, local.sha_sentinel)],
  ))

  common_tags = {
    Project     = "event"
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}
