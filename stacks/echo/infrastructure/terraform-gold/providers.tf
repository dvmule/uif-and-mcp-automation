terraform {
  # >= 1.10: the per-env backend configs set use_lockfile = true (S3-native
  # state locking); >= 1.5 for import blocks.
  required_version = ">= 1.10.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.0"
    }
  }

  # Partial S3 backend — per-env config supplied at init:
  #   terraform init -backend-config=backends/{env}.s3.tfbackend
  # State key namespace is uif-echo-gold/{env}/. The gold resources were
  # adopted here from product's echo-gold/{env}/ state (ADR-0018 step 2).
  backend "s3" {
    encrypt = true
  }
}

provider "aws" {
  region = var.aws_region
}
