# ── The new dev stack: uif-echo-dev-*, built from scratch ──────────
#
# A complete second stack in the same account and subnet, standing beside the
# live echo-dev-* one. Its own Terraform state (backends/dev-uif.hcl), so a
# plan here can never propose a change to the existing stack. Nothing in
# envs/dev.tfvars is touched by anything in this file.
#
# Lifecycle: build → load config → reload data from Oracle → verify against
# echo-dev-* → cut the UI and schedules over → destroy echo-dev-*. Runbook:
# ../deploy/parallel-build-runbook.md.
#
# After the old stack is gone this file becomes dev.tfvars and this backend key
# becomes the dev key. Until then the two coexist and `make ... ENV=dev-uif`
# is how you reach this one.

# ── Identity ───────────────────────────────────────────────────────
# environment stays "dev": it drives tags, the UI login secret path and the
# --env argument Glue jobs receive. It is not the resource-name prefix.
environment    = "dev"
aws_account_id = "283556840805"
aws_region     = "eu-west-1"

# Every resource on the standard prefix. Glue databases take underscores
# because a database name cannot contain hyphens — without this override the
# fallback is echo_dev_* and the new stack would create tables inside the old
# stack's databases.
name_prefix_override    = "uif-echo-dev"
glue_db_prefix_override = "uif_echo_dev"

# legacy_name_prefix_override is deliberately NOT set. It exists to hold
# data-bearing resources back during a phased in-place rename; a from-scratch
# build has nothing to hold back, so legacy_name_prefix falls through to
# name_prefix and every resource — DynamoDB tables included — is uif-echo-dev-*.
# Once echo-dev-* is destroyed and prod is migrated, that mechanism can be
# deleted outright, as locals.tf says.

# project_tag_override stays unset — SECRET_TAG_VAL remains "echo" so
# GET /connections keeps discovering secrets tagged app=echo (including the
# new uif/echo/dev/... paths once copied). Do not set project_tag_override
# to uif-echo unless every secret is retagged in the same window.
ui_login_secret_name = "uif/echo/dev/ui/login"

# ── S3 buckets ─────────────────────────────────────────────────────
# All new and all empty. Bronze is plain Parquet, so it could be copied; Silver
# and Gold are Iceberg, whose metadata embeds absolute s3:// paths, so a bucket
# copy does not move a table. Dev reloads from Oracle instead, which is also
# the end-to-end test.
# Glue scripts, layers, jars under code/; pipeline YAML/JSON under configs/.
artifacts_bucket = "uif-echo-dev-data"
bronze_bucket    = "uif-echo-dev-bronze"
silver_bucket    = "uif-echo-dev-silver"

# The UI bucket, on the standard name rather than the historical
# {account_id}-s3-{prefix}-ui shape. Built from scratch, so dropping the
# account-id prefix and the "s3-" infix costs nothing here; doing it after the
# apply would be another destroy + create.
#
# NOTE: this bucket serves only Echo's UI. The old stack's bucket also hosts
# m/beacon and m/transform, and those do NOT come across automatically — they
# have to be moved, or the old bucket excluded from the teardown, before
# echo-dev-* is destroyed.
ui_bucket_override = "uif-echo-dev-ui"

# ── VPC / networking ───────────────────────────────────────────────
# Same subnet as the live stack. The security group and Glue connection are
# name-prefixed, so both are new; nothing is shared at the SG level.
subnet_id = "subnet-0ed517553ab77ba42"

# ── VPC endpoints ──────────────────────────────────────────────────
# False, as in every other env: these are managed outside this stack and are
# VPC-wide, so this stack's Glue jobs reach them without owning them. The S3
# and DynamoDB endpoints are Gateway type and could not be duplicated on the
# same route table even if we wanted to.
create_s3_endpoint             = false
create_dynamodb_endpoint       = false
create_secretsmanager_endpoint = false

# ── Schedules ──────────────────────────────────────────────────────
# DISABLED until cutover. Both stacks read pipeline configs and dispatch by
# schedule.frequency, so two sets of enabled rules would double-dispatch every
# pipeline — two Bronze runs per tick, twice the Oracle sessions, two writers
# into different lakes. Flipped to ENABLED as the cutover step, in the same
# change that disables the old stack's rules.
event_rule_state = "DISABLED"

# ── API Gateway CORS ───────────────────────────────────────────────
cors_allow_origins = ["*"]

# ── JWT authorizer — disabled for dev ──────────────────────────────
enable_jwt_authorizer       = false
cognito_user_pool_endpoint  = ""
cognito_user_pool_client_id = ""

# ── Release artifacts (UIFPE-515) ──────────────────────────────────
# The bucket holding content-addressed Lambda zips, layer zips, Glue scripts
# and the UI bundle. Setting it is what puts this stack on the promoted-artifact
# path (locals.tf keys that off local.is_uif_stack, which this prefix already
# satisfies); the legacy echo-* envs leave it unset and keep their current path.
release_artifacts_bucket = "uif-echo-dev-release-artifacts"

# artifact_shas and lambda_code_hashes are DELIBERATELY NOT HERE.
#
# They change with every release, and a tfvars file is frozen into the commit a
# build tag points at -- "changing a variable costs a re-run, changing a file
# costs a new tag". Committing them would mean a new tag for every release just
# to carry its own hashes, which is circular.
#
# They arrive at apply time instead, from whichever side owns the artifacts:
#   dev  infrastructure/deploy/_uif-echo-deploy-common.sh hashes the source,
#        uploads, and writes a tfvars.json the apply is given.
#   uat  promote-echo.yml reads them from the build tag's release manifest.

# ── UI WAF (UIFPE-515, enabled by UIFPE-756) ───────────────────────
# The wafv2 grant the deploy role needs is LIVE, as the attached managed
# policy uif-echo-dev-waf-deploy (not as an inline policy -- this role's
# inline budget is full, and put-role-policy on it fails LimitExceeded).
# Its document is ../../deploy/uif-echo-dev-waf-grant.json.
#
# WHEN RE-CHECKING IT, PASS --resource-arns. Two of the three statements are
# scoped to specific ARNs, and simulate-principal-policy without that flag
# evaluates against "*", which no scoped statement matches -- so it reports
# implicitDeny for a grant that works. Simulated against the real web ACL,
# IP set and rule group ARNs, every action is allowed.
#
# ON since UIFPE-756, with the allow-list deliberately OPEN.
#
# The compliance program's requirement is the sanctions rule group and the
# known-bad-inputs group enforcing in block mode. Both do, for every request:
# UIFPE-756 moved the allow rule to the END of the ACL so nothing is let
# through before they run. Read waf.tf's header for the full order.
#
# Restricting by source address is a SEPARATE piece of hardening and is not
# done here, because nobody has confirmed which ranges Echo's dev users
# actually come from. Apicurio uses ["159.182.0.0/16"]
# (connect.pearson.com's external range) in this account, but a dev user was
# observed outside it, so copying that value would lock real people out of
# their own console. An open list is honest about giving no source
# restriction; a guessed list would look like protection and act like an
# outage.
#
# WHY TWO /1s AND NOT 0.0.0.0/0: wafv2 IP sets accept every IPv4 CIDR EXCEPT
# /0. Terraform plans 0.0.0.0/0 clean and the apply fails with
# WAFInvalidParameterException, field IP_ADDRESS. The two halves below cover
# the whole space (0-127.255.255.255 and 128-255.255.255.255) and are
# accepted -- verified against the live API, not inferred.
#
# A non-empty list is also what satisfies waf.tf's plan-time precondition: the
# ACL blocks by default, so an EMPTY list is a blackhole rather than a weak
# ACL.
#
# To add the source restriction later: replace this with the confirmed ranges.
# Nothing else needs to change -- the rule is already last, so the enforcing
# groups keep applying to allowed traffic too.
enable_ui_waf    = true
ui_allowed_cidrs = ["0.0.0.0/1", "128.0.0.0/1"]

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
