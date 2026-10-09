terraform {
  # >= 1.10.0 for the S3 backend's use_lockfile (same pin as the other roots).
  required_version = ">= 1.10.0"

  # Partial backend config — init with:
  #   terraform init -backend-config=backends/dev.s3.tfbackend
  backend "s3" {
    encrypt = true
  }

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      project = "uif-eventing-poc2-delivery-fabric"
      jira    = "UIFPE-453"
      owner   = "srinivasan.narayanaswamy1@pearson.com"
      purpose = "delivery-fabric-poc-observed-outcomes"
    }
  }
}
