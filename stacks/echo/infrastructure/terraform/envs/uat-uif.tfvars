# ── The new uat stack: uif-echo-uat-*, built from scratch ──────────
#
# A complete second stack in the same account and subnet, standing beside the
# live echo-uat-* one. Its own Terraform state (backends/uat-uif.hcl), so a
# plan here can never propose a change to the existing stack. Nothing in
# envs/uat.tfvars is touched by anything in this file.
#
# WHY NOT ONE SHARED FILE WITH uat.tfvars. A tfvars file is where the resource
# NAMES come from -- name_prefix_override and the five bucket names. Two stacks
# reading one file would resolve to one set of names, which is not two stacks
# coexisting, it is one stack with two states fighting over it. The values that
# genuinely ARE shared are environment facts, not names: the account, the
# region, the subnet. Those are duplicated here deliberately, exactly as
# dev-uif.tfvars duplicates dev.tfvars' subnet.
#
# THE ACCOUNT IS THE SAME AS DEV: 283556840805. Only prod is separate
# (216064088366, backends/prod.hcl). So uif-echo-dev-*, echo-dev-*,
# uif-echo-uat-* and echo-uat-* all live in one account, and NAMING IS THE ONLY
# ISOLATION between them. A wrong prefix here does not fail, it writes into
# another stack's resources -- the failure UIFPE-355 already produced once,
# when uif-echo-dev jobs wrote into the echo-dev lake.
#
# Lifecycle: build -> load config -> migrate or reload data -> verify against
# echo-uat-* -> cut the UI and schedules over -> destroy echo-uat-*. Plan:
# ../../docs/uif-echo-uat-rollout-plan.md, phases 2 through 9.

# ── Identity ───────────────────────────────────────────────────────
# environment stays "uat": it drives tags, the UI login secret path and the
# --env argument Glue jobs receive. It is not the resource-name prefix.
environment    = "uat"
aws_account_id = "283556840805"
aws_region     = "eu-west-1"

# Every resource on the standard prefix. Glue databases take underscores
# because a database name cannot contain hyphens -- without this override the
# fallback is echo_uat_* and the new stack would create tables inside the old
# stack's databases.
name_prefix_override    = "uif-echo-uat"
glue_db_prefix_override = "uif_echo_uat"

# legacy_name_prefix_override is deliberately NOT set, for the same reason
# dev-uif.tfvars does not set it: it exists to hold data-bearing resources back
# during a phased in-place rename, and a from-scratch build has nothing to hold
# back. legacy_name_prefix falls through to name_prefix, so every resource --
# DynamoDB tables included -- is uif-echo-uat-*.

# project_tag_override stays unset, so SECRET_TAG_VAL remains "echo" and
# GET /connections keeps discovering secrets tagged app=echo. Do not set it to
# uif-echo unless every secret is retagged in the same window.
ui_login_secret_name = "uif/echo/uat/ui/login"

# ── S3 buckets ─────────────────────────────────────────────────────
# All new and all empty. How they get POPULATED is phase 6 and is not decided:
# Bronze is plain Parquet and could be copied, but Silver and Gold are Iceberg,
# whose metadata embeds absolute s3:// paths, so a bucket copy does not move a
# table. Dev reloaded from Oracle, which was also its end-to-end test; whether
# uat can do the same depends on uat's Oracle and GCP source access, which is
# an open question in the rollout plan.
# Glue scripts, layers, jars under code/; pipeline YAML/JSON under configs/.
artifacts_bucket = "uif-echo-uat-data"
bronze_bucket    = "uif-echo-uat-bronze"
silver_bucket    = "uif-echo-uat-silver"

# The UI bucket, on the standard name rather than the historical
# {account_id}-s3-{prefix}-ui shape. Built from scratch, so dropping the
# account-id prefix and the "s3-" infix costs nothing here.
#
# NOTE: dev's OLD bucket also hosts m/beacon and m/transform, and the rollout
# plan asks the same question of echo-uat's -- whether it serves anything
# besides Echo's UI. That has NOT been confirmed for uat. It matters at phase 9,
# not here: this bucket is new and empty, but the old one cannot be torn down
# until the answer is known.
ui_bucket_override = "uif-echo-uat-ui"

# ── VPC / networking ───────────────────────────────────────────────
# Same subnet as the live uat stack. The security group and Glue connection are
# name-prefixed, so both are new; nothing is shared at the SG level.
subnet_id = "subnet-038a8c6cc8c722470"

# ── VPC endpoints ──────────────────────────────────────────────────
# ALL THREE FALSE, and the third one is a correction rather than a copy.
#
# envs/uat.tfvars sets create_secretsmanager_endpoint = TRUE, so echo-uat owns
# the live interface endpoint for com.amazonaws.eu-west-1.secretsmanager in
# this VPC. vpc.tf creates it with private_dns_enabled = true, and AWS permits
# only ONE endpoint per service per VPC with private DNS enabled -- so setting
# this true here does not create a second one, it fails the apply. This is the
# single value where copying uat.tfvars verbatim would have broken, and it is
# why the two files cannot be one.
#
# CARRIES A CROSS-STACK DEPENDENCY, unresolved here. That endpoint's security
# group is echo-uat's own aws_security_group.glue_eni. This stack creates its
# OWN glue_eni SG, so its Glue jobs and Lambdas do not yet have a path through
# the endpoint: echo-uat's endpoint SG has to allow 443 inbound from this
# stack's SG. That is the same problem UIFPE-543 hit when the gold alert
# notifier needed "a seat on the Secrets Manager interface endpoint's security
# group". It is a manual, cross-stack change on a LIVE stack's SG, so it is not
# done from this file -- it belongs in phase 4 with a person watching, and
# GetSecretValue is what fails if it is missed.
#
# S3 and DynamoDB are Gateway type and could not be duplicated on the same
# route table even if we wanted to.
create_s3_endpoint             = false
create_dynamodb_endpoint       = false
create_secretsmanager_endpoint = false

# ── Schedules ──────────────────────────────────────────────────────
# DISABLED until cutover, and this is the value that must not be got wrong.
# Both stacks read pipeline configs and dispatch by schedule.frequency, so two
# sets of enabled rules double-dispatch every pipeline -- two Bronze runs per
# tick, twice the Oracle sessions on a SHARED source, two writers into
# different lakes. Flipped to ENABLED as the phase 8 cutover step, in the same
# change that disables echo-uat's rules.
event_rule_state = "DISABLED"

# EXCEPT the 4-hourly Bronze rule, which runs ahead of cutover. It was found
# ENABLED in live UAT on 2026-09-29 (enabled outside Terraform; CloudTrail has
# no record) when the echo/v0.11.0 converge planned it back to DISABLED. The
# product owner decided on 2026-09-29 (UIFPE-665) to keep UAT exactly as it
# runs: 57 pipelines in uif-echo-uat-pipeline-config are on the 4-hourly
# schedule and depend on it. The legacy echo-uat-bronze-ingest-4hr rule is also
# ENABLED, so a 4-hourly pipeline present in BOTH stacks' config tables is
# dispatched twice -- the double-dispatch the note above warns about, accepted
# for the 4-hourly tier only. Phase 8 cutover deletes this override together
# with the DISABLED default above, in the change that disables echo-uat's rules.
event_rule_state_overrides = {
  four_hourly = "ENABLED"
}

# ── API Gateway CORS ───────────────────────────────────────────────
# ["*"] matches both envs/uat.tfvars and envs/dev-uif.tfvars. Not tightened
# here: doing it for the new stack only would make uat's two stacks differ in a
# way unrelated to the migration, and the JWT authorizer below is off, so this
# is not the control holding anything back.
cors_allow_origins = ["*"]

# ── JWT authorizer — off, as in envs/uat.tfvars ────────────────────
enable_jwt_authorizer       = false
cognito_user_pool_endpoint  = ""
cognito_user_pool_client_id = ""

# ── Release artifacts (UIFPE-515) ──────────────────────────────────
# This environment's OWN release bucket, not dev's. Setting it is what puts
# this stack on the promoted-artifact path (locals.tf keys that off
# local.is_uif_stack, which this prefix already satisfies).
#
# Its own, even though dev's bucket is in the same account and readable. The
# promotion model copies the bytes into the receiving environment and re-hashes
# them there, so uat holds what uat runs and a dev-side lifecycle rule or
# cleanup cannot pull an artifact out from under a uat rollback. Apicurio has
# uif-apicurio-dev-release-artifacts and uif-apicurio-uat-release-artifacts for
# the same reason.
release_artifacts_bucket = "uif-echo-uat-release-artifacts"

# artifact_shas and lambda_code_hashes are DELIBERATELY NOT HERE, for the
# reason dev-uif.tfvars gives: they change every release, a tfvars file is
# frozen into the commit a build tag points at, and committing them would mean
# a new tag per release just to carry its own hashes. On uat they arrive from
# the build tag's release manifest, read by promote-echo.yml.
#
# TRAP, AND IT IS SILENT. Terraform auto-loads every *.auto.tfvars.json in the
# working directory, whichever -var-file you pass. The dev deploy writes
# artifacts.auto.tfvars.json into this directory, and because THIS file supplies
# no artifact_shas, that file's values win by default rather than being
# overridden. Demonstrated on 2026-09-23 rather than inferred: a
# `terraform console -var-file=envs/uat-uif.tfvars` resolved
# local.glue_script_uri["bronze_ingest"] to
# s3://uif-echo-uat-release-artifacts/glue/315e6260.../bronze-ingest.py -- the
# uat BUCKET with DEV's hash, an object that does not exist there. A plan built
# that way looks complete and points every Glue job and Lambda at keys uat has
# never been sent.
#
# It cannot reach CI: the file is gitignored, and a fresh checkout has none. It
# bites an engineer planning uat locally on a machine that has deployed dev,
# which is every machine that follows the runbook. Delete it, or plan uat from
# a clean checkout, before reading any uat plan.

# ── UI WAF (UIFPE-515) ─────────────────────────────────────────────
# OFF, and for a different reason than dev. On dev the blocker is measured: the
# deploy role holds none of the wafv2 permissions waf.tf needs. Here the role
# does not exist yet -- uif-echo-uat-deploy-role is created in phase 3 -- so
# whether it will hold them is a question for whoever writes its policy, and
# the grant drafted at ../../deploy/uif-echo-dev-waf-grant.json is the shape to
# copy with the ARNs changed.
#
# ui_allowed_cidrs stays empty. The web ACL blocks by default and refuses at
# plan time on an empty list rather than letting an apply take the UI dark, so
# turning this on needs a confirmed range for uat's users -- which has not been
# established for dev either.
enable_ui_waf    = false
ui_allowed_cidrs = []

# ── Pearson mandatory resource tags ────────────────────────────────
# Applied to every taggable resource through provider default_tags. Set here
# rather than as repo variables because this root has no uat or prod deploy
# workflow -- those are applied by a person running the deploy scripts, and a
# repo variable never reaches a local `make apply`. See variables.tf.
#
# t_dcl = 3 by owner decision of 2026-09-23. t_name defaults to "echo".
# t_environment is derived from `environment` above, not set here.
t_app_id                  = "SVC05287"
t_cost_centre             = "10.365.74172.40313.0000.35"
t_dcl                     = "3"
t_owner_individual        = "raj.karunakaran@pearson.com"
t_responsible_individuals = "uifbaa@grp.pearson.com"
