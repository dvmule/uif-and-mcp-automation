# ── Identity ───────────────────────────────────────────────────────
environment    = "prod"
aws_account_id = "216064088366"
aws_region     = "eu-west-1"

# ── Naming (pre-rename stack) ──────────────────────────────────────
# PROD was deployed before the EDS -> Echo rename (cf66f7e) and never
# migrated: the live resources are eds-prod-*, the Glue databases are
# eds_prod_*, and resources are tagged Project=eds. State lives at
# eds/prod/terraform.tfstate (see backends/prod.hcl).
#
# These overrides make this config describe the stack that actually
# exists. Do NOT "tidy" them to echo-*: every resource would be renamed,
# which for most types means destroy + recreate.
name_prefix_override    = "eds-prod"
glue_db_prefix_override = "eds_prod"
project_tag_override    = "eds"

# ── S3 buckets ─────────────────────────────────────────────────────
# Bucket names are inputs, not derived from name_prefix — these are the
# live prod buckets.
artifacts_bucket = "eds-prod-glue-scripts"
bronze_bucket    = "eds-prod-bronze"
silver_bucket    = "eds-prod-silver"

# ── VPC / networking ───────────────────────────────────────────────
# vpc-0a2c5bbe92d9fa500 — Terraform creates echo-prod-glue-eni SG for Glue ENIs.
# Allow Oracle inbound from output glue_eni_security_group_id (not sg-0a41139956fb046fa
# unless you attach that separately).
subnet_id = "subnet-051f624cafbe8e72b"

# ── Interim UI login ───────────────────────────────────────────────
# eds/ prefix, not echo/: the prod Lambda role grants GetSecretValue on
# oracle-*, gcp-* and eds/* only, so an echo/-prefixed secret is unreadable
# and login would return 500. Created 2026-09-04 in account 216064088366.
ui_login_secret_name = "eds/prod/ui/login"

# ── Glue script uploads ────────────────────────────────────────────
# Prod keeps its pre-rename (eds-) glue scripts. The repo copies hardcode
# echo-{env}-* job names and s3://echo-{env}-silver|gold warehouse paths, which
# do not exist in this account. See upload_glue_scripts in providers.tf.
#
# Requires `terraform state rm` for the 12 script objects before the first
# apply with this set, or Terraform deletes them from S3.
upload_glue_scripts = false

# ── Lake Formation ─────────────────────────────────────────────────
# PROD IPM is GCP-native: Silver and Gold live in GCS + BigQuery, so the AWS
# Glue Catalog and Lake Formation are unused here. Disabling also gates the
# three aws_glue_catalog_database resources — the eds_prod_* databases that do
# exist were created on demand by pipeline_controller at registration and stay
# outside Terraform, which is the status quo.
#
# Nothing is revoked by setting this false: every LF resource shows as "create"
# in a prod plan, i.e. none of them exist today.
enable_lakeformation = false

# ── VPC endpoints ──────────────────────────────────────────────────
create_s3_endpoint             = false
create_dynamodb_endpoint       = false
create_secretsmanager_endpoint = true

# ── API Gateway CORS ───────────────────────────────────────────────
# Replace with CloudFront URL after first deploy:
#   cd infra && make output ENV=prod   → ui_cloudfront_url
cors_allow_origins = ["*"]

# ── JWT authorizer ─────────────────────────────────────────────────
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
