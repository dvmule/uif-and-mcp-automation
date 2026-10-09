# providers.tf
# Terraform and AWS provider configuration.
# Credentials are NOT hardcoded here — they are injected at runtime via
# aws-runas (or any other mechanism that populates the standard AWS
# environment variables: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY,
# AWS_SESSION_TOKEN).

terraform {
  # RAISED FROM >= 1.5.0 (UIFPE-515), for two reasons that are both already
  # true of this module rather than anything new:
  #
  #   1. All four backends set `use_lockfile`, which is a 1.10 feature. Under
  #      the old floor an older binary ran happily and silently WITHOUT state
  #      locking -- on dev, dev-uif, uat and prod alike. That is the failure
  #      the floor exists to prevent.
  #   2. `removed` blocks (1.7+) are how this module forgets the aws_s3_object
  #      script uploads without destroying them. See the block at the foot of
  #      glue.tf.
  #
  # CI pins 1.15.8 everywhere that plans or applies; this is the floor, not the
  # pin. A saved plan is only valid for the version that wrote it, so the pin
  # is what must not drift -- the floor only stops a binary that cannot
  # honour this configuration from running at all.
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
    null = {
      source  = "hashicorp/null"
      version = "~> 3.0"
    }
  }

  # Partial backend — bucket, key, region, and dynamodb_table are supplied
  # via -backend-config=backends/{env}.hcl at init time.
  backend "s3" {
    encrypt = true
  }
}







provider "aws" {
  region = var.aws_region

  # The seven Pearson mandatory tags, on every taggable resource this root
  # creates, without touching any resource declaration. See local.default_tags
  # and the variable descriptions in variables.tf.
  #
  # Per-resource `tags` blocks override these on a key collision rather than
  # conflicting, which is what lets bronze and silver keep their own Layer key.
  default_tags {
    tags = local.default_tags
  }

  # Credentials resolved from environment in this order:
  #   1. AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_SESSION_TOKEN  (set by aws-runas)
  #   2. ~/.aws/credentials profile (AWS_PROFILE env var)
  #   3. EC2/ECS/Lambda instance metadata (for CI runners)
  #
  # Do NOT set access_key / secret_key here — aws-runas handles injection.
}

# CLOUDFRONT-scope WAFv2 resources -- and, empirically, their logging
# configuration and its destination log group -- must be created in us-east-1
# regardless of where the distribution's origin lives. That is an AWS
# constraint on the scope, not a choice made here. Same alias name Apicurio
# and Beacon use for the same reason.
#
# Declared unconditionally, used only by waf.tf, which is gated off by
# default (var.enable_ui_waf). An aliased provider nothing references
# configures no credentials and makes no API call, so this costs the legacy
# echo-dev / echo-uat / eds-prod plans nothing.
provider "aws" {
  alias  = "use1"
  region = "us-east-1"

  # The same tags. An aliased provider does NOT inherit the default provider's
  # default_tags, so omitting this block would leave the WAFv2 web ACL, its IP
  # set and its log group as the only untagged resources in the root -- and
  # they are the ones a compliance programme is most likely to audit.
  default_tags {
    tags = local.default_tags
  }
}
