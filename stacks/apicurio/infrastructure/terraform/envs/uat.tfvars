# Apicurio — uat.
#
# uat SHARES DEV'S ACCOUNT AND NETWORK, and that is a property of the estate
# rather than a shortcut taken here. Account 283556840805 holds both
# (owner decision 2026-09-20; stacks/echo's uat.tfvars carries the same
# account id). Isolation between dev and uat is therefore by RESOURCE NAME --
# separate RDS instance, ECS cluster, ALB, buckets and ECR repository, all
# derived from local.name = uif-apicurio-uat -- and not by network or account.
#
# Worth knowing what that means before relying on it: a security-group mistake
# or a noisy neighbour crosses between dev and uat, and both depend on the same
# single-AZ Secrets Manager interface endpoint. That is acceptable for a
# non-prod promotion target and would not be for prod, which is a separate
# account (216064088366).
#
# Five of the six network values below are therefore IDENTICAL to dev's, each
# for a reason recorded at the value rather than by copying the file. Only
# cors_origins is environment-specific.

env    = "uat"
region = "eu-west-1"

# Identical to dev's, and not a copy-paste: gip-uif-nonprod-vpc-euw1 is THE
# non-prod VPC in this account, so there is no separate uat VPC to point at.
vpc_id = "vpc-0287e62dc748ffab8" # gip-uif-nonprod-vpc-euw1, 10.116.48.0/21

# The SAME two subnets dev uses, because they are the only private-css subnets
# in the VPC -- there is no az3 and no second private-css pair (see
# dev.tfvars' survey note). uat cannot have its own.
#
# Capacity checked 2026-09-20 rather than assumed, since dev's stack now sits
# in them: 53 of 59 addresses free in az1 and 55 of 59 in az2, so dev consumed
# 6 and 4. A second stack of the same shape leaves roughly 47 and 51 free.
private_subnet_ids = [
  "subnet-086ed7f5de377bb91", # private-css-az1  10.116.54.128/26  eu-west-1a
  "subnet-0b323a493f21f4a87", # private-css-az2  10.116.54.192/26  eu-west-1b
]

# Explicit rather than relying on the default, because it is the value that
# decides whether the ALB is reachable from outside. The public edge is API
# Gateway in front of the BFF; the ALB itself is never public.
alb_internal = true

# Identical to dev's, necessarily: this is the sole security group of
# vpce-0742f8ea5aa68967e, the ACCOUNT's Secrets Manager interface endpoint, and
# an account has one. It is echo-uat-glue-eni -- the Glue worker group reused --
# so the name already reflects that it is not dev-specific. The endpoint has an
# ENI in private-glue-az1 only, so credential resolution is a single-AZ
# dependency that uat inherits along with dev.
secretsmanager_endpoint_sg_id = "sg-0b45c554b51638786"

# Identical to dev's: these describe who may reach the API, and the answer does
# not change by environment. 159.182.0.0/16 is the Pearson corporate range and
# 10.122.0.0/15 is connect.pearson.com's client pool.
# Pearson CloudPC egresses from Azure, not from 159.182.0.0/16, so a CloudPC
# session needs its own entries. These are the three regional EXTERNAL ranges
# (UIFPE-763). CloudPC's internal 10.x subnets are deliberately absent: API
# Gateway and CloudFront only ever see a public source address.
api_allowed_cidrs = [
  "159.182.0.0/16",     # Pearson corporate / connect.pearson.com
  "104.43.130.76/30",   # CloudPC US
  "40.112.91.44/30",    # CloudPC EMEA
  "137.116.140.120/30", # CloudPC APAC
]
vpn_client_cidrs = ["10.122.0.0/15"]

# PASS 1 OF TWO, AND THIS VALUE IS TEMPORARY.
#
# The variable requires at least one origin matching ^https?://[^/]+$ -- a
# blank value is rejected at plan time on purpose, because an empty list starts
# the BFF healthily with no allowed origin and closes CORS silently. So the
# first apply needs *something*, and uat's CloudFront domain does not exist
# until that apply has created the distribution. Same two-pass sequence as
# DEPLOY-DEV.md Phase 4.
#
# PASS 2 IS DONE. This is uat's CloudFront domain alone, read off the
# distribution the first apply created (comment `uif-apicurio-uat-ui`, origin
# `uif-apicurio-uat-ui.s3.eu-west-1.amazonaws.com`). Localhost is deliberately
# absent: nobody develops against uat, so it would be a browser origin on a
# shared environment that no one needs. Dev's tfvars keeps localhost because
# dev is where the UI is developed; that reason does not transfer.
#
# This value is frozen into whichever build tag carries it -- changing it
# costs a NEW TAG, not a re-run. See DEPLOY-UAT.md phase 3.
cors_origins = "https://dbjg7v3miozyq.cloudfront.net"

# Identical to dev's, and deliberately so: the promotable artifact is the same
# upstream image pinned to the same digest, which is what lets one build tag be
# promoted rather than rebuilt. locals.tf strips the tag and appends the digest,
# so the digest is the contract and the tag is documentation.
#
# registry_image_digest BELOW IS OVERRIDDEN AT DEPLOY TIME for uat. Both
# deploy-apicurio-infra.yml and promote-apicurio.yml pass
# `-var "registry_image_digest=..."` AFTER `-var-file`, so the CLI value wins
# and this one is inert on every tag-driven run; the value actually applied
# comes from the pushed build tag's release manifest (originally dev's
# tfvars, at build time). Editing this line has no effect on a tag-driven
# deploy -- it matters only for a manual `terraform plan`/`apply` run with no
# `-var` override, which is not how uat is deployed.
registry_image        = "quay.io/apicurio/apicurio-registry:3.3.2"
registry_image_digest = "sha256:e9476584866f9cbfd6b11c1fbdd0df64976277e069f54d537572032751a4b6fc"

# Empty, as in dev: the Delivery Lab / CDC arm is half-built -- the cdc-poller
# source is not in this repo -- so there is no enrollment config to point at.
# An empty value disables the parameter and its IAM grant rather than creating
# a grant for something that does not exist.
cdc_tables_ssm_parameter = ""

# frontend_force_destroy IS DELIBERATELY ABSENT, so it takes its default of
# false -- a divergence from dev, which opts in.
#
# dev's UI bucket holds a rebuildable bundle and dev is meant to be disposable.
# uat is a promotion target: its bucket holds what a release actually put there,
# and a `terraform destroy` that silently emptied it would remove the thing a
# rollback republishes. Add it only if uat is ever genuinely meant to be torn
# down, and say so when you do.
