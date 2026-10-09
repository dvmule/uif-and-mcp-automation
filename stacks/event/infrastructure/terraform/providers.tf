# Event stack — Terraform root module.
# Pattern mirrors stacks/beacon and domains/product: flat resource files,
# per-env tfvars in envs/, partial backend config in backends/.
#   terraform init -backend-config=backends/dev.s3.tfbackend
#   terraform plan -var-file=envs/dev.tfvars
#
# INVARIANT (the reason this stack exists): onboarding a NEW DOMAIN changes
# NOTHING under stacks/event. A domain brings its own event-domain module
# instance (bus, DLQ, mapping/seed uploads), its pipeline-configs seed row,
# and its schemas — every stack resource here is domain-agnostic by
# construction (wildcarded IAM, convention-derived names, table keys that
# carry the domain). If a change here is ever needed to onboard a domain,
# that is a design regression — fix the stack, don't fork it.

terraform {
  # >= 1.10.0: backends/*.s3.tfbackend set use_lockfile = true (S3-native
  # state locking), understood from 1.10 onward. The migration also uses
  # import blocks (>= 1.5) and removed blocks (>= 1.7) — see MIGRATION.md.
  required_version = ">= 1.10.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # Partial S3 backend — per-env config supplied at init:
  #   terraform init -backend-config=backends/{env}.s3.tfbackend
  backend "s3" {}
}

provider "aws" {
  region = var.aws_region
}

data "aws_caller_identity" "current" {}
