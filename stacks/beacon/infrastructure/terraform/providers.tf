# Beacon control panel + pipeline — Terraform root module.
# Pattern mirrors domains/product/infrastructure/terraform: flat resource
# files, per-env tfvars in envs/, partial backend config in backends/.
#   terraform init -backend-config=backends/dev.hcl
#   terraform plan -var-file=envs/dev.tfvars

terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.60"
    }
  }
  backend "s3" {} # bucket/key/region/lock table come from backends/<env>.hcl
}

provider "aws" {
  region = var.region
  default_tags {
    tags = {
      Project   = local.project_tag
      Component = "control-panel"
      ManagedBy = "terraform"
      Env       = var.env
    }
  }
}

# CloudFront certificates/functions must live in us-east-1
provider "aws" {
  alias  = "use1"
  region = "us-east-1"
}
