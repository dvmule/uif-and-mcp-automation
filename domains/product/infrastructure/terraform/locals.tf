# EDS naming conventions — mirrors uif-relay-echo/infra/locals.tf so the two
# stacks read as one platform (ADR-0004: same infra design, separate repos).
# This repo's Terraform state is ISOLATED (backend key echo-gold/{env}/) and
# never adopts or destroys resources owned by the platform repo, except via
# the documented cutover imports (see README "Infra ownership & cutover").

locals {
  # name_prefix is FROZEN: the platform-owned lookups below derive from it, one
  # a data source. uif_prefix is the standard's stem, for resources we own.
  name_prefix = "echo-${var.environment}"
  uif_prefix  = "uif-echo-${var.environment}"
  project_tag = "echo"

  common_tags = {
    Project     = local.project_tag
    Environment = var.environment
    ManagedBy   = "terraform"
  }

  gold_db_prefix = "uif_echo_${var.environment}"
}

data "aws_caller_identity" "current" {}
