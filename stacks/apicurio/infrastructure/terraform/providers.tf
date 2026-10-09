# Apicurio Registry — Terraform root module.
# Pattern mirrors stacks/beacon/infrastructure/terraform (this repo's only
# proven ECS Fargate + ALB stack): flat resource files, per-env tfvars in
# envs/, partial backend config in backends/.
#   terraform init -backend-config=backends/dev.hcl
#   terraform plan -var-file=envs/dev.tfvars
#
# See ../../CLAUDE.md and ../../../../architecture-design/decisions/0011-apicurio-schema-governance.md
# for why this stack exists and what it's for.

terraform {
  # >= 1.10, not >= 1.6: backends/<env>.hcl sets use_lockfile, which is a 1.10
  # feature. An older binary accepts the argument and runs without state
  # locking rather than refusing, so the floor is the only thing standing
  # between two concurrent applies and a corrupted state file.
  required_version = ">= 1.10"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.60"
    }
    # Zips authorizer/index.py at plan time (authorizer.tf). Here only for the
    # Phase 1 auth stop-gap, so it leaves with it.
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
  }
  backend "s3" {} # bucket/key/region/lock come from backends/<env>.hcl
}

provider "aws" {
  region = var.region
  default_tags {
    # Stack-wide keys only. See local.default_tags for why `component` is not
    # among them and why `Project` survives alongside the standard's keys.
    tags = local.default_tags
  }
}

# WAFv2 web ACLs at CLOUDFRONT scope must be created in us-east-1 regardless of
# where the distribution's origin lives (waf.tf) -- an AWS-wide requirement,
# not a choice made here. Same alias name Beacon's own providers.tf uses for
# the same reason.
provider "aws" {
  alias  = "use1"
  region = "us-east-1"
  default_tags {
    tags = local.default_tags
  }
}
