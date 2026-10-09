# Input variables for the Echo stack.
# Centralized here to match stacks/beacon/infrastructure/terraform/variables.tf;
# previously these were scattered across providers.tf, glue.tf, s3.tf and others.
# Values come from envs/<env>.tfvars.

variable "aws_account_id" {
  description = "AWS account ID"
  type        = string
} # was: iam.tf

variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "eu-west-1"
} # was: iam.tf

variable "bronze_bucket" {
  description = "S3 bucket for Bronze layer Parquet output"
  type        = string
} # was: glue.tf

variable "cloudfront_price_class" {
  description = "CloudFront price class. PriceClass_100 = EU + North America only (cheapest)."
  type        = string
  default     = "PriceClass_100"
} # was: cloudfront.tf

variable "cognito_user_pool_client_id" {
  description = "Cognito User Pool App Client ID (used as JWT audience)"
  type        = string
  default     = ""
} # was: apigateway.tf

variable "cognito_user_pool_endpoint" {
  description = "Cognito User Pool issuer URL (e.g. https://cognito-idp.eu-west-1.amazonaws.com/eu-west-1_XXXXXX)"
  type        = string
  default     = ""
} # was: apigateway.tf

variable "cors_allow_origins" {
  description = "Allowed origins for CORS (use specific domain in production, e.g. https://app.example.com)"
  type        = list(string)
  default     = ["*"]
} # was: apigateway.tf

variable "create_dynamodb_endpoint" {
  description = "Set to false if a DynamoDB Gateway VPC endpoint already exists in this VPC."
  type        = bool
  default     = true
} # was: vpc.tf

variable "create_s3_endpoint" {
  description = "Set to false if an S3 Gateway VPC endpoint already exists in this VPC."
  type        = bool
  default     = true
} # was: vpc.tf

variable "create_secretsmanager_endpoint" {
  description = "Set to true if the VPC has no NAT Gateway and Glue cannot reach Secrets Manager. Costs ~$7/month per AZ."
  type        = bool
  default     = false
} # was: vpc.tf

variable "enable_jwt_authorizer" {
  description = "Set to true to require a valid Cognito JWT on all routes"
  type        = bool
  default     = false
} # was: apigateway.tf

variable "enable_lakeformation" {
  description = "Create Lake Formation grants and AWS Glue Silver/Gold catalog databases. Disable for GCP-only environments."
  type        = bool
  default     = true
} # was: lakeformation.tf

variable "environment" {
  description = "Deployment environment: dev, uat, or prod"
  type        = string
  validation {
    condition     = contains(["dev", "uat", "prod"], var.environment)
    error_message = "environment must be one of: dev, uat, prod"
  }
} # was: providers.tf

variable "glue_db_prefix_override" {
  description = "Glue database prefix; empty = echo_{environment}"
  type        = string
  default     = ""
} # was: providers.tf

variable "artifacts_bucket" {
  description = "S3 bucket where Glue job scripts are uploaded"
  type        = string
} # was: glue.tf

# ── Release artifacts (UIFPE-515) ─────────────────────────────────────────────
#
# Separate from artifacts_bucket, which also holds Terraform state, pipeline
# configs and the legacy code/ prefix. This one holds ONLY content-addressed
# release artifacts, so its lifecycle and its grants are about one thing.
variable "release_artifacts_bucket" {
  description = "S3 bucket holding content-addressed release artifacts (Lambda zips, layer zips, Glue scripts, the UI bundle). Required for uif-echo-* stacks; leave empty for the legacy echo-* stacks."
  type        = string
  default     = ""
}

# The artifact identity for this release, keyed by the manifest's own artifact
# names. The S3 key IS the hash: a Lambda is lambdas/<sha>.zip, a Glue script
# is glue/<sha>/<filename>.
#
# WHERE THE VALUES COME FROM, WHICH DIFFERS BY ENVIRONMENT AND IS THE POINT:
#   dev   computes them from source, because dev is the only environment that
#         builds. The deploy script hashes, uploads and writes the tfvars.
#   uat   reads them from the build tag's release manifest, because uat
#         receives artifacts and never makes them.
# Same variable, same shape, two sources -- which is exactly what "promote, do
# not rebuild" means in practice.
#
# EMPTY IS LEGAL, AND ONLY FOR THE LEGACY STACKS. local.is_uif_stack gates the
# whole artifact path: a uif-echo-* stack reads content-addressed objects from
# release_artifacts_bucket, while echo-dev, echo-uat and echo-prod keep the
# plan-time zip and the s3-sync'd script prefix they run on today. Those three
# retire on their own schedule (the coexist-then-retire plan in
# docs/uif-echo-uat-rollout-plan.md), and until they do, their next apply must
# not require values nobody has produced for them.
#
# A uif stack that reaches a plan without these gets a loud failure, not a
# silent fallback: locals.tf substitutes ARTIFACT-SHA-NOT-SUPPLIED into the key,
# which shows up in the plan as a path no object can be at.
variable "artifact_shas" {
  description = "Content hash per artifact, keyed by manifest artifact name (e.g. pipeline_controller, bronze_ingest, oracle_instant_client, ui_bundle). Empty for the legacy echo-* stacks, which still build at plan time."
  type        = map(string)
  default     = {}

  validation {
    # 64 lowercase hex, no prefix -- the same shape release-manifest.sh writes
    # and read-manifest.sh returns. A truncated or uppercase value would build
    # a key that points at nothing, and the apply would fail at the Lambda or
    # the Glue job rather than here.
    condition = alltrue([
      for k, v in var.artifact_shas : can(regex("^[0-9a-f]{64}$", v))
    ])
    error_message = "Every artifact_shas value must be 64 lowercase hex characters with no algorithm prefix."
  }
}

# Terraform's source_code_hash for each Lambda, base64 of the zip's own sha256.
#
# Kept separate from artifact_shas because it answers a different question.
# artifact_shas is the CONTENT identity (the hash of the file tree, which names
# the S3 key); this is the hash of the ARCHIVE's bytes, which is what lets a
# plan notice that somebody ran UpdateFunctionCode by hand. Dropping
# archive_file removed the only thing that used to detect that.
variable "lambda_code_hashes" {
  description = "Base64 sha256 of each Lambda zip, for source_code_hash; keyed by manifest artifact name"
  type        = map(string)
  default     = {}

  validation {
    # base64 of a 32-byte digest is 43 characters plus one '=' pad. A hex
    # digest here is silently accepted by Terraform and then never matches,
    # producing a permanent diff nobody can explain.
    condition = alltrue([
      for k, v in var.lambda_code_hashes : can(regex("^[A-Za-z0-9+/]{43}=$", v))
    ])
    error_message = "Every lambda_code_hashes value must be base64 of a sha256 digest (43 characters plus '='), not hex."
  }
}

# Naming overrides for stacks that predate the EDS -> Echo rename (cf66f7e).
#
# PROD was deployed as eds-prod-* and was never migrated, so the derived
# echo-* names in locals.tf do not match it. Leave these empty and naming
# derives from var.environment as before (dev, uat); set them to
# adopt an existing differently-named stack (prod).
#
# These change the NAME of nearly every resource, so a non-empty value on an
# env whose state was created with the default renames -- i.e. destroys and
# recreates -- everything. Only set them for a stack whose live resources
# already carry those names, and only after a plan shows no replacements.
variable "legacy_name_prefix_override" {
  description = "Prefix for resources that hold data and are not yet renamed; empty = same as name_prefix (nothing diverged)"
  type        = string
  default     = ""
} # UIFPE-355

variable "name_prefix_override" {
  description = "Resource name prefix; empty = echo-{environment}"
  type        = string
  default     = ""
} # was: providers.tf

variable "project_tag_override" {
  description = "Project tag value; empty = echo"
  type        = string
  default     = ""
} # was: providers.tf

variable "silver_bucket" {
  description = "S3 bucket for Silver layer Iceberg output"
  type        = string
} # was: s3.tf

variable "subnet_id" {
  description = "Private subnet ID where Glue ENIs will be provisioned (must have route to Oracle)"
  type        = string
} # was: glue.tf

variable "ui_domain" {
  description = "Optional custom domain for the UI (e.g. data-connect.internal.example.com). Leave empty to use the CloudFront domain."
  type        = string
  default     = ""
} # was: cloudfront.tf

variable "ui_login_secret_name" {
  description = "Secrets Manager name holding interim UI login credentials; empty = echo/{environment}/ui/login"
  type        = string
  default     = ""
} # was: providers.tf

# Whether Terraform uploads jobs/*.py to the scripts bucket.
#
# PROD is false: the scripts in this repo hardcode echo-{env}-* resource names
# and s3://echo-{env}-silver|gold warehouse paths (11 references across 8
# scripts, introduced by the rename in cf66f7e). Prod runs eds-prod-*, so
# uploading them would point production Spark at buckets that do not exist and
# break the hourly/daily Gold runs. Prod keeps its pre-rename scripts until
# those references are parameterised.
#
# WARNING: flipping this to false for an env whose state already tracks the
# objects makes Terraform DELETE them from S3. Run `terraform state rm` for
# each object first -- that forgets them without touching S3.
variable "upload_glue_scripts" {
  description = "Upload jobs/*.py to the scripts bucket. False for stacks whose scripts are managed out-of-band."
  type        = bool
  default     = true
} # was: providers.tf

# State of the nine SCHEDULE EventBridge rules (30min / hourly / 4hr / the
# twelve daily Bronze slots / weekly / the twelve reconcile slots / the three
# Gold rules). ENABLED everywhere except a stack being built in parallel.
#
# A second stack with enabled schedules double-dispatches every pipeline: two
# Bronze runs per tick against the same Oracle source, twice the JDBC sessions,
# and two writers landing into different lakes. So envs/dev-uif.tfvars sets
# DISABLED, and flipping it to ENABLED is the cutover step — paired with
# disabling the outgoing stack's rules.
#
# This does NOT cover the two event-pattern rules (Bronze SUCCEEDED -> GCS
# replicator, Silver SUCCEEDED -> BQ replicator). Those match on
# `jobName = [aws_glue_job.X.name]`, so each stack's rule only ever sees its
# own jobs and there is no cross-talk to prevent. They stay enabled, which is
# what makes the new stack's replication chain testable before cutover.
variable "event_rule_state" {
  description = "ENABLED or DISABLED for the schedule-driven EventBridge rules."
  type        = string
  default     = "ENABLED"

  validation {
    condition     = contains(["ENABLED", "DISABLED"], var.event_rule_state)
    error_message = "event_rule_state must be ENABLED or DISABLED."
  }
}

variable "event_rule_state_overrides" {
  description = "Per-rule exceptions to event_rule_state, keyed by the scheduler.tf resource name (half_hourly, hourly, four_hourly, daily_bronze_slot, weekly, reconcile_slot). Empty everywhere except an environment deliberately running one schedule ahead of its cutover."
  type        = map(string)
  default     = {}

  validation {
    condition = alltrue([
      for k, v in var.event_rule_state_overrides :
      contains(["half_hourly", "hourly", "four_hourly", "daily_bronze_slot", "weekly", "reconcile_slot"], k) && contains(["ENABLED", "DISABLED"], v)
    ])
    error_message = "event_rule_state_overrides keys must be scheduler.tf schedule rules (half_hourly, hourly, four_hourly, daily_bronze_slot, weekly, reconcile_slot) and values ENABLED or DISABLED."
  }
}

# Exact name for the UI bucket. Empty = the historical
# {account_id}-s3-{prefix}-ui shape, which every existing stack uses and must
# keep: renaming a bucket in Terraform is destroy + create, and dev's UI bucket
# also serves m/beacon and m/transform.
#
# Set only by stacks built from scratch, where the conformant name costs
# nothing. envs/dev-uif.tfvars uses uif-echo-dev-ui.
variable "ui_bucket_override" {
  description = "Exact UI bucket name; empty = {account_id}-s3-{prefix}-ui."
  type        = string
  default     = ""
}

# ── UI WAF (UIFPE-515) ───────────────────────────────────────────────────────
#
# DEFAULTS OFF, AND THAT IS NOT A PLACEHOLDER. waf.tf is complete and
# reviewable; what is missing is an IAM grant this repository cannot make.
# Simulated against the real role on 2026-09-22:
#
#   aws iam simulate-principal-policy \
#     --policy-source-arn arn:aws:iam::283556840805:role/echo-dev-github-actions
#
# returned implicitDeny for wafv2:CreateWebACL, GetWebACL, UpdateWebACL,
# DeleteWebACL, CreateIPSet, GetIPSet, GetRuleGroup, PutLoggingConfiguration
# and TagResource -- nine of the ten actions waf.tf needs. Only
# wafv2:ListRuleGroups is allowed. So with this flag true the plan renders and
# the APPLY fails at CreateIPSet, which would block the release tag this
# change exists to cut.
#
# Apicurio hit the identical wall and closed it with a separate change to a
# live managed policy (infrastructure/deploy/uif-apicurio-dev-deploy-policy-fix.sh,
# "this role had zero wafv2:* permissions (confirmed implicitDeny before
# drafting)"). Echo's role is worse placed: echo-dev-github-actions is SHARED
# with the live echo-dev stack, so widening it is not a private change.
# infrastructure/deploy/uif-echo-dev-waf-grant.json holds the statement to add.
#
# Flip this to true in envs/dev-uif.tfvars once that grant is live. Nothing
# else needs to change.
variable "enable_ui_waf" {
  description = "Attach a CLOUDFRONT-scope WAFv2 web ACL to the UI distribution. Requires wafv2:* on the deploy role; see the comment above."
  type        = bool
  default     = false
}

# Source ranges allowed to load the Echo UI, as the public internet sees them.
#
# UNLIKE APICURIO, THIS IS NOT THE ONLY CONTROL IN FRONT OF THE APP. Echo's UI
# authenticates against the secret named by var.ui_login_secret_name, so the
# allow-list here is defence in depth and the Pearson sanctions rule group's
# carrier -- not the authentication boundary. Apicurio's equivalent variable
# says the opposite about itself, on purpose; do not copy its reasoning across.
#
# Empty fails CLOSED, and the web ACL's precondition refuses to build at plan
# time rather than letting an empty allow-list reach a default_action = block
# ACL, which would take the UI dark at apply for everyone including the
# operator who ran it.
variable "ui_allowed_cidrs" {
  description = "IPv4 CIDRs allowed through the UI web ACL. Required when enable_ui_waf is true."
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for c in var.ui_allowed_cidrs : can(cidrnetmask(c))])
    error_message = "Every entry must be a valid IPv4 CIDR block; a hostname or a wildcard here would silently widen the allow-list to everything."
  }
}

# ── Pearson mandatory resource tags ─────────────────────────────────────────
#
# Ported from stacks/apicurio, which carries the same seven keys and states the
# reasoning at length in its locals.tf. The short version: these are the
# vocabulary the enterprise bills, classifies and pages against OUTSIDE this
# repository -- t_AppID ties a resource to its ServiceNow service record,
# t_cost_centre to a real cost centre, t_dcl to a stated data classification,
# and the two people keys to a named owner and an on-call list.
#
# Applied through provider default_tags (providers.tf), not per resource. Every
# taggable resource in this root picks them up with no edit at its own
# declaration, and a resource type that does not support tags ignores them.
#
# WHERE THE VALUES LIVE, AND A DELIBERATE DIVERGENCE FROM APICURIO. Apicurio
# supplies these as repo variables through TF_VAR_* in its deploy workflow.
# This root does not, and the reason is that Echo has no uat or prod deploy
# workflow -- only deploy-uif-echo-dev.yml plans or applies it, and uat and
# prod are applied by a person running the deploy scripts. A repo variable does
# not reach a local `make apply ENV=prod`, so that path would fail on a missing
# required variable, or worse be "fixed" by exporting a guessed value. The
# values are therefore set in each envs/*.tfvars, where every path -- CI,
# script and bare terraform -- reads them identically and git records who
# changed a classification and when.
#
# None of these is secret: a service id, a cost centre, a classification level
# and two Pearson addresses. t_name is the only one with a default, because it
# is derivable rather than a service-record fact.

variable "t_name" {
  description = "Mandatory `t_name` tag: the short application name, lower case. Defaults because it is derivable -- this stack is `echo` everywhere else, including the {stack} slot in the naming standard."
  type        = string
  default     = "echo"

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]*$", var.t_name))
    error_message = "t_name must be lower-case alphanumeric with hyphens, starting with a letter or digit."
  }
}

variable "t_app_id" {
  description = "Mandatory `t_AppID` tag: this app's ServiceNow service record id. No default -- a module may not invent the record a resource is billed and paged against."
  type        = string

  # EMPTY DEFAULT, and it is not laziness. local.default_tags is gated on
  # local.is_uif_stack, so the legacy echo-dev-*, echo-uat-* and eds-prod-*
  # stacks apply NO Pearson tags -- tagging live legacy resources was never
  # asked for, and prod's plan would have been the largest. Those three envs
  # therefore supply no value, and a variable with no default would fail their
  # plans outright with "No value for required variable".
  #
  # The strictness is not lost, it moves: s3.tf's release_artifacts bucket
  # exists only on a uif stack and carries a precondition asserting all five
  # are non-empty, so a uif stack cannot ship with blank tags. The validation
  # below still rejects a malformed value wherever one IS given.
  default = ""

  validation {
    condition     = var.t_app_id == "" || can(regex("^SVC[0-9]{5}$", var.t_app_id))
    error_message = "t_app_id must be SVC followed by exactly five digits, as the service record states it."
  }
}

variable "t_cost_centre" {
  description = "Mandatory `t_cost_centre` tag: the real cost centre this stack bills to. No default -- a wrong value misbills another team silently."
  type        = string

  # EMPTY DEFAULT, and it is not laziness. local.default_tags is gated on
  # local.is_uif_stack, so the legacy echo-dev-*, echo-uat-* and eds-prod-*
  # stacks apply NO Pearson tags -- tagging live legacy resources was never
  # asked for, and prod's plan would have been the largest. Those three envs
  # therefore supply no value, and a variable with no default would fail their
  # plans outright with "No value for required variable".
  #
  # The strictness is not lost, it moves: s3.tf's release_artifacts bucket
  # exists only on a uif stack and carries a precondition asserting all five
  # are non-empty, so a uif stack cannot ship with blank tags. The validation
  # below still rejects a malformed value wherever one IS given.
  default = ""

  validation {
    condition     = var.t_cost_centre == "" || can(regex("^[0-9]+([.][0-9]+)+$", var.t_cost_centre))
    error_message = "t_cost_centre must be dot-separated digit groups, e.g. 10.365.74172.40313.0000.35."
  }
}

variable "t_dcl" {
  description = <<-DESC
    Mandatory `t_dcl` tag: the data classification level, 1-4, of the data this
    stack holds.

    Echo is 3, by owner decision of 2026-09-23. Recorded here rather than
    copied from another stack on purpose: apicurio's own deploy workflow
    explains that a DCL is "a property of the DATA A STACK HOLDS, not of the
    organisation", and that understating it is the dangerous direction because
    it exempts protected data from controls keyed off the tag. Apicurio holds
    schema and registry metadata; this stack holds the Oracle replica and the
    Bronze, Silver and Gold layers built from it. The two arriving at the same
    number is a decision, not an inheritance.

    What it does NOT yet settle: whether 3 mandates a customer-managed key.
    trivy's AVD-AWS-0132 is suppressed on four buckets in this root with a note
    saying it revisits when a classification exists. One now does, so that note
    is actionable -- but the DCL-to-controls mapping is a Pearson policy
    document this repository does not contain, so the suppressions stand until
    someone reads it.
  DESC
  type        = string

  # EMPTY DEFAULT, and it is not laziness. local.default_tags is gated on
  # local.is_uif_stack, so the legacy echo-dev-*, echo-uat-* and eds-prod-*
  # stacks apply NO Pearson tags -- tagging live legacy resources was never
  # asked for, and prod's plan would have been the largest. Those three envs
  # therefore supply no value, and a variable with no default would fail their
  # plans outright with "No value for required variable".
  #
  # The strictness is not lost, it moves: s3.tf's release_artifacts bucket
  # exists only on a uif stack and carries a precondition asserting all five
  # are non-empty, so a uif stack cannot ship with blank tags. The validation
  # below still rejects a malformed value wherever one IS given.
  default = ""

  validation {
    condition     = var.t_dcl == "" || contains(["1", "2", "3", "4"], var.t_dcl)
    error_message = "t_dcl must be exactly \"1\", \"2\", \"3\" or \"4\" -- the level from this app's service record, as a string."
  }
}

variable "t_owner_individual" {
  description = "Mandatory `t_owner_individual` tag: the named owner's Pearson address. No default -- an owner is a person the service record names."
  type        = string

  # EMPTY DEFAULT, and it is not laziness. local.default_tags is gated on
  # local.is_uif_stack, so the legacy echo-dev-*, echo-uat-* and eds-prod-*
  # stacks apply NO Pearson tags -- tagging live legacy resources was never
  # asked for, and prod's plan would have been the largest. Those three envs
  # therefore supply no value, and a variable with no default would fail their
  # plans outright with "No value for required variable".
  #
  # The strictness is not lost, it moves: s3.tf's release_artifacts bucket
  # exists only on a uif stack and carries a precondition asserting all five
  # are non-empty, so a uif stack cannot ship with blank tags. The validation
  # below still rejects a malformed value wherever one IS given.
  default = ""

  validation {
    condition     = var.t_owner_individual == "" || can(regex("^[a-zA-Z0-9._%+-]+@([a-zA-Z0-9-]+[.])*pearson[.]com$", var.t_owner_individual))
    error_message = "t_owner_individual must be a pearson.com email address."
  }
}

variable "t_responsible_individuals" {
  description = "Mandatory `t_responsible_individuals` tag: the on-call or team distribution list responsible day to day. A list rather than a person, so it survives someone changing team. One address, or comma-separated."
  type        = string

  # EMPTY DEFAULT, and it is not laziness. local.default_tags is gated on
  # local.is_uif_stack, so the legacy echo-dev-*, echo-uat-* and eds-prod-*
  # stacks apply NO Pearson tags -- tagging live legacy resources was never
  # asked for, and prod's plan would have been the largest. Those three envs
  # therefore supply no value, and a variable with no default would fail their
  # plans outright with "No value for required variable".
  #
  # The strictness is not lost, it moves: s3.tf's release_artifacts bucket
  # exists only on a uif stack and carries a precondition asserting all five
  # are non-empty, so a uif stack cannot ship with blank tags. The validation
  # below still rejects a malformed value wherever one IS given.
  default = ""

  validation {
    condition     = var.t_responsible_individuals == "" || can(regex("^[a-zA-Z0-9._%+-]+@([a-zA-Z0-9-]+[.])*pearson[.]com(,[ ]*[a-zA-Z0-9._%+-]+@([a-zA-Z0-9-]+[.])*pearson[.]com)*$", var.t_responsible_individuals))
    error_message = "t_responsible_individuals must be one pearson.com address, or several separated by commas."
  }
}
