# Shared naming for the Echo greenfield stack.
#
# All application AWS resources use: echo-{environment}-<slug>
#
# Terraform state is isolated under backends/*.hcl keys (echo/<env>/…)
# so `terraform apply` creates NEW resources and never adopts or destroys the
# legacy ingestion-framework-* / generic-* / eds-* stack.
locals {
  # The *_override variables (providers.tf) exist for stacks that predate the
  # EDS -> Echo rename and were never migrated -- prod is still eds-prod-*.
  # Empty override = derive from var.environment, which is what dev and uat
  # do.
  name_prefix    = var.name_prefix_override != "" ? var.name_prefix_override : "echo-${var.environment}"
  project_tag    = var.project_tag_override != "" ? var.project_tag_override : "echo"
  glue_db_prefix = var.glue_db_prefix_override != "" ? var.glue_db_prefix_override : "echo_${var.environment}"

  # UIFPE-355 / the uif- rename, phased dev -> uat -> prod.
  #
  # name_prefix is the prefix for resources that HAVE been renamed. dev sets
  # name_prefix_override = "uif-echo-dev" in its tfvars; uat and prod set no
  # override yet, so nothing there moves.
  #
  # legacy_name_prefix is for resources that hold DATA and so cannot be renamed
  # by a name change alone - renaming a DynamoDB table or an S3 bucket in
  # Terraform is destroy + create, and takes the contents with it. They stay on
  # the old prefix until each has a migration behind it.
  #
  # Where the two are equal (uat, prod) nothing has diverged. Grep for
  # legacy_name_prefix to see exactly what is left to migrate; when that returns
  # nothing, delete this local and the variable.
  legacy_name_prefix = var.legacy_name_prefix_override != "" ? var.legacy_name_prefix_override : local.name_prefix

  # Greenfield uif-echo-* stacks use UIF naming (no glue token in DynamoDB;
  # Silver catalogs keyed by source system). Live echo-dev-* / echo-uat-* keep
  # legacy slugs until those stacks are retired.
  is_uif_stack = startswith(local.name_prefix, "uif-echo")

  # ── Pearson mandatory resource tags ──────────────────────────────
  #
  # Applied through provider default_tags (providers.tf), so every taggable
  # resource in this root carries them without an edit at its own declaration,
  # and a type that does not support tags ignores them. Variable descriptions
  # in variables.tf carry the reasoning and the value decisions.
  #
  # t_environment is DERIVED, not supplied, and uat maps to "STG". Pearson's
  # standard offers Dev, STG and Prod, with no UAT value, so STG is the only
  # non-dev non-prod token available -- recorded here rather than in a tfvars
  # comment because a reader who finds STG on a uat resource would otherwise
  # assume Pearson sanctioned that mapping. Keyed on var.environment, which is
  # "dev" for both dev and dev-uif and "uat" for both uat and uat-uif, so the
  # coexisting stacks agree on it.
  #
  # Spelling is exact and case matters: AWS tag keys are case-sensitive. The
  # AWS key is `t_AppID` while the Terraform variable is `t_app_id`, following
  # HCL convention; the other six keys match their variable names.
  t_environment = {
    dev  = "Dev"
    uat  = "STG"
    prod = "Prod"
  }[var.environment]

  # `Project` is not a Pearson tag and is kept deliberately. Existing resources
  # in this root already set it per-resource from local.project_tag, and the
  # control panel's GET /connections discovers secrets by the tag app=echo --
  # so the value stays local.project_tag ("echo" unless overridden) rather than
  # becoming "uif-echo". Changing it would need every secret retagged in the
  # same window, which envs/dev-uif.tfvars already warns about.
  #
  # Per-resource `tags` blocks still win over these, which is how bronze and
  # silver keep their own Layer key and how Project stays consistent.
  #
  # GATED ON is_uif_stack, SO ONLY THE NEW STACKS ARE TAGGED. An empty map
  # means the provider applies no default tags at all, so echo-dev-*,
  # echo-uat-* and eds-prod-* are untouched. That is an owner decision of
  # 2026-09-23: tagging live legacy resources was not asked for, those three
  # retire on their own schedule, and prod's would have been the largest diff
  # of the three for no benefit.
  #
  # The cost of the gate is that the five values must be optional, or a legacy
  # plan fails on a missing required variable. They default to "" instead, and
  # the strictness moves to s3.tf's release_artifacts bucket -- which exists
  # only on a uif stack and carries a precondition asserting all five are
  # non-empty, so a uif stack cannot quietly ship with blank tags.
  #
  # For the record of what this does when it IS on: a plan against dev-uif on
  # 2026-09-23 was 72 in-place updates, 0 adds, 0 destroys -- 66 tag-only, and
  # 6 policy resources reading "(known after apply)" because changing the
  # provider configuration invalidates the cached aws_iam_policy_document data
  # sources, which then re-render identically. Tags never force a replacement.
  default_tags = local.is_uif_stack ? {
    t_name                    = var.t_name
    t_AppID                   = var.t_app_id
    t_environment             = local.t_environment
    t_cost_centre             = var.t_cost_centre
    t_dcl                     = var.t_dcl
    t_owner_individual        = var.t_owner_individual
    t_responsible_individuals = var.t_responsible_individuals
    Project                   = local.project_tag
  } : {}

  # The UI web ACL needs BOTH gates, and they mean different things.
  #
  # is_uif_stack is structural: echo-dev, echo-uat and eds-prod are live
  # distributions serving real users, and a default_action = block ACL in front
  # of one of them is an outage, not a hardening. Those three never get it, no
  # matter what their tfvars say.
  #
  # var.enable_ui_waf is the deploy window: off until the deploy role actually
  # holds wafv2:* (see variables.tf). AND, not OR -- a legacy stack must not be
  # able to opt in through tfvars alone.
  ui_waf_enabled = local.is_uif_stack && var.enable_ui_waf

  config_table_slug  = local.is_uif_stack ? "pipeline-config" : "glue-job-config"
  metrics_table_slug = local.is_uif_stack ? "pipeline-metrics" : "glue-job-metrics"

  # The gold layer's jobs and stores are declared in ../terraform-gold
  # (ADR-0018) and carry the standard names in every environment, exactly as
  # src/libs/gold_resources.py spells them. This root only reaches them by
  # name, so it derives them here instead of taking them per environment: one
  # gold estate per env, one spelling, nothing to drift.
  gold_prefix             = "uif-echo-${var.environment}"
  gold_config_table_name  = "${local.gold_prefix}-gold-pipeline-config"
  gold_transform_job_name = "${local.gold_prefix}-gold-transform"
  gold_reconcile_job_name = "${local.gold_prefix}-gold-reconcile"
  gold_data_bucket        = "${local.gold_prefix}-gold"
  gold_artifacts_bucket   = "${local.gold_prefix}-gold-artifacts"
  gold_config_table_arn   = "arn:aws:dynamodb:${var.aws_region}:${var.aws_account_id}:table/${local.gold_config_table_name}"
  # UIFPE-543: who is emailed on a gold alarm (the gold root owns the table;
  # the controller edits its rows from the Notifications page).
  alert_subscribers_table_name = "${local.gold_prefix}-alert-subscribers"
  alert_subscribers_table_arn  = "arn:aws:dynamodb:${var.aws_region}:${var.aws_account_id}:table/${local.alert_subscribers_table_name}"
  gold_alarm_prefix            = "${local.gold_prefix}-gold-"
  gold_job_arns = [
    "arn:aws:glue:${var.aws_region}:${var.aws_account_id}:job/${local.gold_transform_job_name}",
    "arn:aws:glue:${var.aws_region}:${var.aws_account_id}:job/${local.gold_reconcile_job_name}",
  ]

  # UI bucket: {account_id}-s3-{prefix}-ui. Deliberately on legacy_name_prefix -
  # per uif-echo-ui-deploy.sh this bucket also hosts sibling apps under m/beacon and
  # m/transform, so recreating it would take those down with it. It also carries
  # the account-id prefix the standard drops, so its rename is a two-part change
  # that belongs with the other stateful buckets.
  # The default carries an account-id prefix and an "s3-" infix that the naming
  # standard drops. Existing stacks keep it: dev's live bucket is
  # 283556840805-s3-echo-dev-ui and hosts sibling apps under m/beacon and
  # m/transform, so changing this expression rather than overriding it would
  # rename that bucket — destroy + create, taking those two apps with it.
  #
  # A stack built from scratch has no such constraint, so dev-uif overrides it
  # to the conformant uif-echo-dev-ui. Free now, another destroy + create later.
  ui_bucket = (
    var.ui_bucket_override != ""
    ? var.ui_bucket_override
    : "${var.aws_account_id}-s3-${local.legacy_name_prefix}-ui"
  )

  # Secret holding the interim UI login credentials, passed to the controller
  # Lambda as UI_LOGIN_SECRET. Default matches the fallback in
  # pipeline_controller.py, so dev and uat keep resolving echo/{env}/ui/login.
  #
  # PROD must override: its Lambda role grants GetSecretValue on eds/* only
  # (oracle-*, gcp-*, eds/*), so an echo/-prefixed name is unreadable and login
  # returns 500 "Login is not configured".
  ui_login_secret_name = var.ui_login_secret_name != "" ? var.ui_login_secret_name : "echo/${var.environment}/ui/login"

  # UIF stacks: one data bucket (uif-echo-{env}-data) with code/ + configs/.
  # Live echo-* stacks keep jobs/, lambda-layers/, jars/, tmp/ at bucket root.
  #
  # tflint flags data_config_prefix as declared-but-unused, and in Terraform
  # terms it is -- a no-op ternary no resource references. It stays because
  # qe/pipeline/test_dq_infra.py::test_uif_data_bucket_layout_in_locals asserts
  # its presence, including its alignment: it records that a uif data bucket
  # holds configs/ beside code/, which the deploy scripts rely on and nothing
  # else states. Deleting it silences one tflint warning and deletes that
  # statement, so the warning is left with the other pre-existing findings.
  glue_scripts_prefix = local.is_uif_stack ? "code" : "jobs"
  glue_temp_prefix    = local.is_uif_stack ? "code/tmp" : "tmp"
  glue_jars_prefix    = local.is_uif_stack ? "code/jars" : "jars"
  data_config_prefix  = local.is_uif_stack ? "configs" : "configs"


  # Lambda layers are pre-staged in the data/scripts bucket. Terraform references
  # s3_key only — no local zip upload or filemd5() at plan time (CI-safe).
  lambda_layer_keys = local.is_uif_stack ? {
    oracle_instant_client = "code/lambda-layers/oracle_instant_client.zip"
    gcs                   = "code/lambda-layers/gcs_layer.zip"
    silver_bq             = "code/lambda-layers/silver_bq_layer.zip"
    controller_bq         = "code/lambda-layers/controller_bq_layer.zip"
    } : {
    oracle_instant_client = "lambda-layers/oracle_instant_client.zip"
    gcs                   = "lambda-layers/gcs_layer.zip"
    silver_bq             = "lambda-layers/silver_bq_layer.zip"
    controller_bq         = "lambda-layers/controller_bq_layer.zip"
  }
}

# ── Artifact locations (UIFPE-515) ───────────────────────────────────────────
#
# ONE place decides where a Glue script or a Lambda zip comes from, because the
# alternative is the same `is_uif_stack ? ... : ...` ternary repeated at
# twenty-three call sites, where one of them eventually gets it wrong.
#
#   uif-echo-*   content-addressed objects in the release-artifacts bucket. The
#                key IS the sha256, so an object is never overwritten and a
#                release's recorded identity cannot change under it.
#   echo-*       the path they run on today: scripts s3-sync'd to a fixed
#                prefix, Lambdas zipped at plan time. Untouched until these
#                stacks retire (docs/uif-echo-uat-rollout-plan.md).
locals {
  # file   = the name the object is stored under inside the content prefix.
  #          Glue FLATTENS every --extra-py-files entry into the job's working
  #          directory, which is why dq-kinds/reconcile.py stores as
  #          reconcile.py and why _module_loader.load_dq_kind_module tries
  #          dq-kinds/<stem>.py and then <stem>.py.
  # legacy = the path under the old shared prefix, which does keep the subdir.
  glue_scripts = {
    bronze_ingest                = { file = "bronze-ingest.py", legacy = "bronze-ingest.py" }
    silver_routing               = { file = "silver_routing.py", legacy = "silver_routing.py" }
    bronze_extract_sql           = { file = "bronze_extract_sql.py", legacy = "bronze_extract_sql.py" }
    silver_transform             = { file = "silver-transform.py", legacy = "silver-transform.py" }
    silver_transform_gcs_iceberg = { file = "silver-transform-gcs-iceberg.py", legacy = "silver-transform-gcs-iceberg.py" }
    silver_transform_bq          = { file = "silver-transform-bq.py", legacy = "silver-transform-bq.py" }
    silver_transform_bq_native   = { file = "silver-transform-bq-native.py", legacy = "silver-transform-bq-native.py" }
    silver_reconcile             = { file = "silver-reconcile.py", legacy = "silver-reconcile.py" }
    silver_reconcile_gcs         = { file = "silver-reconcile-gcs.py", legacy = "silver-reconcile-gcs.py" }
    silver_reconcile_bq          = { file = "silver-reconcile-bq.py", legacy = "silver-reconcile-bq.py" }
    silver_quality_check         = { file = "silver-quality-check.py", legacy = "silver-quality-check.py" }
    analyze_table                = { file = "analyze-table.py", legacy = "analyze-table.py" }
    iceberg_gcs_probe            = { file = "iceberg-gcs-probe.py", legacy = "iceberg-gcs-probe.py" }
    module_loader                = { file = "_module_loader.py", legacy = "_module_loader.py" }
    bq_partition_ddl             = { file = "bq_partition_ddl.py", legacy = "bq_partition_ddl.py" }
    dq_kinds_init                = { file = "__init__.py", legacy = "dq-kinds/__init__.py" }
    dq_kinds_reconcile           = { file = "reconcile.py", legacy = "dq-kinds/reconcile.py" }
    dq_kinds_null_rate           = { file = "null-rate.py", legacy = "dq-kinds/null-rate.py" }
  }

  # A missing sha becomes a visible sentinel rather than an empty path segment:
  # `glue/<sha>//name.py` would be a valid-looking URI that resolves to nothing.
  # Named once here and asserted in artifact_guard.tf -- it used to be only "a
  # plan diff somebody reads and stops on", which put the estate behind someone
  # spotting a string in a long diff (UIFPE-608).
  sha_sentinel = "ARTIFACT-SHA-NOT-SUPPLIED"

  glue_script_uri = {
    for name, s in local.glue_scripts : name => local.is_uif_stack
    ? "s3://${var.release_artifacts_bucket}/glue/${lookup(var.artifact_shas, name, local.sha_sentinel)}/${s.file}"
    : "s3://${var.artifacts_bucket}/${local.glue_scripts_prefix}/${s.legacy}"
  }

  # The five Lambda functions. Four are declared in lambda.tf; the fifth,
  # dq_check_controller, is in data_quality.tf -- which is why lambda.tf's
  # header says "five" while only four resources live in it.
  lambda_artifacts = [
    "pipeline_controller",
    "scheduler_dispatcher",
    "gcs_replicator",
    "silver_bq_replicator",
    "dq_check_controller",
  ]

  lambda_s3_key = {
    for name in local.lambda_artifacts : name =>
    "lambdas/${lookup(var.artifact_shas, name, local.sha_sentinel)}.zip"
  }

  # Layer zips are vendored rather than built (their pip trees are not
  # byte-stable), so a uif stack records the hash of what is already in S3.
  # See the header of .github/workflows/release-echo.yml.
  layer_s3_key = {
    for name in ["oracle_instant_client", "gcs_client", "silver_bq_client", "controller_bq_client"] :
    name => "layers/${lookup(var.artifact_shas, name, local.sha_sentinel)}.zip"
  }

  # Every artifact whose key still carries the sentinel, derived from the three
  # maps above rather than a hand-kept list -- so a new Lambda, layer or Glue
  # script is covered the day it is added.
  #
  # Empty for the legacy echo-* stacks: they build at plan time and have no
  # release artifacts, which is what `artifact_shas = {}` means there.
  artifacts_missing_sha = local.is_uif_stack ? sort(concat(
    [for name, uri in local.glue_script_uri : "glue/${name}" if strcontains(uri, local.sha_sentinel)],
    [for name, key in local.lambda_s3_key : "lambda/${name}" if strcontains(key, local.sha_sentinel)],
    [for name, key in local.layer_s3_key : "layer/${name}" if strcontains(key, local.sha_sentinel)],
  )) : []
}
