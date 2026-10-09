locals {
  # Every gold resource is uif-echo-{env}-…, and so is everything gold READS:
  # the Glue role, the scripts bucket and the metrics table are the standard
  # estate's, shared with its silver jobs rather than copied per layer.
  name_prefix = "uif-echo-${var.environment}"
  project_tag = "echo"

  # The retrieval API's published latency SLO, in ONE place because it is
  # stated twice: here, where the alarms fire on it, and in each entity's
  # OpenAPI contract, where a consumer reads it. A test asserts the two agree
  # — an objective the alarms do not watch is not an objective, and a
  # threshold no contract publishes is not an SLO.
  #
  # Measured on dev 2026-09-28 over 300 samples per path: warm p50 5.2 ms,
  # warm p99 12.4 ms, cold total p50 1,091 ms, cold max 1,211 ms. p50 is set
  # at ~4x the warm median to catch structural regression; p99 admits a cold
  # start, because cold starts are accepted rather than bought out with
  # provisioned concurrency (owner decision 2026-09-28).
  serving_api_slo = {
    p50_ms = 20
    p99_ms = 1300
  }

  common_tags = {
    Project     = local.project_tag
    Environment = var.environment
    ManagedBy   = "terraform"
  }

  # Shared with the standard estate's silver jobs: one role, one metrics
  # table. The legacy echo-{env}-glue-role and -glue-job-metrics are the
  # retiring estate's and are no longer referenced here.
  glue_execution_role_name = "${local.name_prefix}-glue-role"
  metrics_table_name       = "${local.name_prefix}-pipeline-metrics"
  glue_scripts_bucket      = "${local.name_prefix}-data"

  # Beside the standard estate's own code/ scripts, under a gold prefix. The
  # old location squatted in the platform's bucket to survive its
  # `glue_jobs/ --delete` sync; in our own bucket that hazard is gone.
  gold_scripts_prefix = "code/gold"

  gold_db_prefix = "uif_echo_${var.environment}"

  # UIFPE-576. Gold folds into the ECHO stack's release unit (owner decision
  # 2026-09-24): `gold` is a {layer} in the naming vocabulary, not a {stack},
  # and the promotion model is stack-scoped. So these names are the artifact
  # names in echo/v*'s manifest, staged by stacks/echo/scripts/stage-artifacts.sh.
  gold_lambda_artifacts = [
    "ai_draft",
    "compile_preview",
    "gold_alert_notify",
    "gold_announce",
    "gold_dispatcher",
    "gold_serving_api",
    "gold_serving_sync",
    "grain_probe",
    "submit",
  ]

  # Per artifact: has a release staged this one? A dev apply stages nothing, so
  # every entry is false and the plan-time zip is used - which is what keeps
  # dev working while the release half is still being built.
  lambda_staged = {
    for name in local.gold_lambda_artifacts : name =>
    var.release_artifacts_bucket != "" && contains(keys(var.lambda_code_hashes), name)
  }

  # The S3 key IS the content hash. Never a mutable key: Glue and Lambda both
  # take a plain s3:// reference with no version id, so a fixed key would make
  # every past release's recorded identity silently change on the next deploy
  # (stack-promotion-model.md). Same derivation as the echo root's locals.tf.
  lambda_s3_key = {
    for name in local.gold_lambda_artifacts : name =>
    "lambdas/${lookup(var.artifact_shas, name, "ARTIFACT-SHA-NOT-SUPPLIED")}.zip"
  }

  # UIFPE-576 phase 1b. The same treatment for the GOLD root's Glue artifacts:
  # four job scripts and five libraries. Keyed by artifact name, which is what
  # the manifest and var.artifact_shas index.
  #
  #   file = the name the object is stored under inside the content prefix, and
  #          also its name under the fixed prefix. Glue FLATTENS every
  #          --extra-py-files entry into the job's working directory, so a
  #          library's basename is what the import sees either way.
  #   src  = repo path, relative to the stack root. The staging script and the
  #          aws_s3_object below must read the SAME file, which
  #          qe/pipeline/test_glue_artifact_sets.py asserts.
  gold_glue_artifacts = {
    gold_transform     = { file = "generic_gold_transform.py", src = "src/apps/pipeline/generic_gold_transform.py" }
    gold_reconcile     = { file = "generic_gold_reconcile.py", src = "src/apps/pipeline/generic_gold_reconcile.py" }
    gold_ops_export    = { file = "gold_ops_export.py", src = "src/apps/pipeline/gold_ops_export.py" }
    gold_ops_summarize = { file = "gold_ops_summarize.py", src = "src/apps/pipeline/gold_ops_summarize.py" }
    gold_cdc           = { file = "gold_cdc.py", src = "src/apps/pipeline/gold_cdc.py" }
    gold_telemetry     = { file = "gold_telemetry.py", src = "src/libs/gold_telemetry.py" }
    gold_report        = { file = "gold_report.py", src = "src/libs/gold_report.py" }
    gold_stats         = { file = "gold_stats.py", src = "src/libs/gold_stats.py" }
    gold_frequencies   = { file = "gold_frequencies.py", src = "src/libs/gold_frequencies.py" }
  }

  # Per artifact: has a release staged this one? A dev apply stages nothing, so
  # every entry is false, every URI below is the fixed prefix, and the uploads
  # stay switched on -- dev is unchanged by this.
  glue_staged = {
    for name, _ in local.gold_glue_artifacts : name =>
    var.release_artifacts_bucket != "" && contains(keys(var.artifact_shas), name)
  }

  # Where each job and each library is read from. ONE map, so a job script and
  # the libraries it imports cannot end up resolved from different releases.
  #
  # A missing sha becomes a visible sentinel rather than an empty path segment:
  # `glue/<sha>//name.py` is a valid-looking URI that silently resolves to
  # nothing, while ARTIFACT-SHA-NOT-SUPPLIED is a plan diff somebody stops on.
  # Same derivation as the echo root's local.glue_script_uri.
  gold_glue_uri = {
    for name, s in local.gold_glue_artifacts : name => local.glue_staged[name]
    ? "s3://${var.release_artifacts_bucket}/glue/${lookup(var.artifact_shas, name, "ARTIFACT-SHA-NOT-SUPPLIED")}/${s.file}"
    : "s3://${local.glue_scripts_bucket}/${local.gold_scripts_prefix}/${s.file}"
  }

  # Bootstrapped by hand, not managed here — see serving_api.tf for why.
  gold_api_gateway_secret = "uif/echo/${var.environment}/gateway/gold-api"

  # Lake Formation tag every gold database carries (the config sync applies it);
  # consumer grants are tag-based, so a new database needs no grant work.
  gold_layer_tag_key = "uif-echo-gold-layer"

  # The same shape for the Silver databases the gold pipelines read, so the
  # executor grant is one tag rule instead of a list (silver_source_access.tf).
  silver_layer_tag_key = "uif-echo-silver-layer"

  # The Silver databases the gold pipelines read: product and program take mdm,
  # price and stock take tep (owner decision 2026-09-16). Enumerated only to
  # carry the tag; the grants themselves match the tag, not these names.
  silver_source_databases = [
    "uif_echo_${var.environment}_silver_mdm",
    "uif_echo_${var.environment}_silver_tep",
  ]

  # Where those databases keep their data. The gold jobs read it directly as
  # well as through Lake Formation, so the role needs both.
  silver_bucket = "uif-echo-${var.environment}-silver"
}

# Shared Glue execution role (the echo stack's) — reused, not managed here.
data "aws_iam_role" "glue_execution" {
  name = local.glue_execution_role_name
}
