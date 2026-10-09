terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
  # Intentionally local backend — this stack creates the remote backend,
  # so it cannot itself reference it. Commit bootstrap/terraform.tfstate to git.
}

provider "aws" {
  region = "eu-west-1"
}

locals {
  account_id = "283556840805"
  bucket     = "pearson-ingestion-framework-tfstate-${local.account_id}"
}

resource "aws_s3_bucket" "tfstate" {
  bucket = local.bucket

  lifecycle {
    prevent_destroy = true
  }

  tags = {
    Project   = "ingestion-framework"
    ManagedBy = "terraform-bootstrap"
    Purpose   = "remote-state"
  }
}

resource "aws_s3_bucket_versioning" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id
  versioning_configuration {
    status = "Enabled"
  }
}

# SSE-S3, not a customer-managed key. trivy reports AVD-AWS-0132 HIGH here,
# accepted for the same reason as the release-artifacts bucket in ../s3.tf,
# where the full reasoning lives: no stack in this repo uses a CMK anywhere,
# and whether one is required follows from Echo's t_dcl classification, which
# does not exist yet.
#
# This bucket has a second reason to stay on SSE-S3. It holds Terraform state,
# so it is read by `terraform init` before any of this stack's own IAM exists.
# A CMK here adds a key policy to the bootstrap path, which is the one path
# that cannot rely on anything this Terraform creates -- a misconfigured grant
# would lock the state file away from the only tool that can fix it.
# trivy:ignore:AVD-AWS-0132
resource "aws_s3_bucket_server_side_encryption_configuration" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "tfstate" {
  bucket                  = aws_s3_bucket.tfstate.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_policy" "tfstate_https_only" {
  bucket = aws_s3_bucket.tfstate.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "DenyNonHTTPS"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource = [
        aws_s3_bucket.tfstate.arn,
        "${aws_s3_bucket.tfstate.arn}/*"
      ]
      Condition = {
        Bool = { "aws:SecureTransport" = "false" }
      }
    }]
  })
  depends_on = [aws_s3_bucket_public_access_block.tfstate]
}

resource "aws_dynamodb_table" "tfstate_locks" {
  name         = "ingestion-framework-tfstate-locks"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "LockID"

  attribute {
    name = "LockID"
    type = "S"
  }

  tags = {
    Project   = "ingestion-framework"
    ManagedBy = "terraform-bootstrap"
    Purpose   = "state-locking"
  }
}

output "state_bucket" {
  value       = aws_s3_bucket.tfstate.bucket
  description = "S3 bucket name for Terraform remote state"
}

output "lock_table" {
  value       = aws_dynamodb_table.tfstate_locks.name
  description = "DynamoDB table name for Terraform state locking"
}
