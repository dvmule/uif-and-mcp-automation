# ── Identity ───────────────────────────────────────────────────────
environment    = "dev"
aws_account_id = "283556840805"
aws_region     = "eu-west-1"

# This file describes the LIVE echo-dev-* stack, and describes it exactly.
# Do not add name_prefix_override here.
#
# UIFPE-355 renames dev to the uif- prefix by BUILDING A NEW STACK beside this
# one, not by renaming this one — see envs/dev-uif.tfvars and
# ../deploy/parallel-build-runbook.md. A resource's name is part of its
# identity in Terraform, so setting name_prefix_override in this file, whose
# backend key is the live stack's state, makes every plan propose destroying
# and recreating the running platform. It briefly did: 178 add / 178 destroy,
# every EventBridge rule replaced, Bronze ingestion stopped for the duration.
# That approach was measured, written up, and then rejected in favour of the
# parallel build. The measurements are kept in ../deploy/tier1-rename-runbook.md
# so the rejection has evidence behind it.
#
# This stack is not modified again. It is destroyed, whole, once the new one
# has been deployed and verified.

# ── S3 buckets ─────────────────────────────────────────────────────
# The variable is called artifacts_bucket now; the live bucket is still named
# echo-dev-glue-scripts. Renaming the variable is a code change, renaming the
# value would be a destroy + create.
artifacts_bucket = "echo-dev-glue-scripts"
bronze_bucket    = "echo-dev-bronze"
silver_bucket    = "echo-dev-silver"

# ── VPC / networking ───────────────────────────────────────────────
subnet_id = "subnet-0ed517553ab77ba42"

# ── VPC endpoints ──────────────────────────────────────────────────
create_s3_endpoint             = false
create_dynamodb_endpoint       = false
create_secretsmanager_endpoint = false

# ── API Gateway CORS ───────────────────────────────────────────────
cors_allow_origins = ["*"]

# ── JWT authorizer — disabled for dev ──────────────────────────────
enable_jwt_authorizer       = false
cognito_user_pool_endpoint  = ""
cognito_user_pool_client_id = ""

# ── Pearson mandatory resource tags — NOT SET HERE, ON PURPOSE ─────
# This is the legacy stack. local.default_tags is gated on
# local.is_uif_stack, so this stack applies no Pearson tags and needs no
# values -- tagging live legacy resources was not asked for, and this stack
# retires on the coexist-then-retire schedule in
# ../../docs/uif-echo-uat-rollout-plan.md.
#
# The five variables default to "" for exactly this reason. Do not add values
# here expecting them to take effect: the gate, not the absence of values, is
# what keeps this stack untagged.
