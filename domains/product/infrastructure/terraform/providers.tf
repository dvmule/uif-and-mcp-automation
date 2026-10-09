terraform {
  # >= 1.10.0: the per-env backend configs (backends/*.s3.tfbackend) set
  # use_lockfile = true (S3-native state locking), which Terraform only
  # understands from 1.10 onward — older versions would reject/ignore it.
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
  # State key namespace is echo-gold/{env}/ — isolated from the platform
  # repo's echo/{env}/ keys in the same state bucket.
  backend "s3" {
    encrypt = true
  }
}

provider "aws" {
  region = var.aws_region
}
