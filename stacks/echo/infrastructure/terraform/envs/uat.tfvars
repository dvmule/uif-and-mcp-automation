# ── Identity ───────────────────────────────────────────────────────
environment    = "uat"
aws_account_id = "283556840805"
aws_region     = "eu-west-1"

# ── S3 buckets ─────────────────────────────────────────────────────
artifacts_bucket = "echo-uat-glue-scripts"
bronze_bucket    = "echo-uat-bronze"
silver_bucket    = "echo-uat-silver"

# ── VPC / networking ───────────────────────────────────────────────
subnet_id = "subnet-038a8c6cc8c722470"

# ── VPC endpoints ──────────────────────────────────────────────────
create_s3_endpoint             = false
create_dynamodb_endpoint       = false
create_secretsmanager_endpoint = true

# ── API Gateway CORS ───────────────────────────────────────────────
cors_allow_origins = ["*"]

# ── JWT authorizer — disabled until Cognito pool/client IDs are supplied ──
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
