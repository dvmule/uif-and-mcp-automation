terraform {
  # >= 1.10.0 because the S3 backend's use_lockfile only exists from 1.10
  # (same pin as domains/product and stacks/echo/terraform-gold).
  required_version = ">= 1.10.0"

  # Partial backend config — init with:
  #   terraform init -backend-config=backends/dev.s3.tfbackend
  backend "s3" {
    encrypt = true
  }

  required_providers {
    # >= 6.6.0 is MANDATORY, not housekeeping: log_config on
    # aws_cloudwatch_event_bus was added in provider 6.6.0 (2025-07-28),
    # and event-bus logging is the whole mechanism this POC tests. On ~> 5.0
    # the log_config block is an unknown argument and terraform fails at
    # validate. The bump to the 6.x line is safe here because this POC holds
    # its own state (delivery-poc/dev/terraform.tfstate) and touches nothing
    # the 6.x upgrade guide flags for the resources used below.
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.6"
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
      project = "uif-eventing-poc"
      jira    = "UIFPE-453"
      owner   = "srinivasan.narayanaswamy1@pearson.com"
      purpose = "observed-delivery-outcomes-from-event-bus-logs"
    }
  }
}
