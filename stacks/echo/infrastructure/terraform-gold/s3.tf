resource "aws_s3_bucket" "gold" {
  bucket = var.gold_bucket
  tags = merge(local.common_tags, {
    Layer = "gold"
  })
  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_versioning" "gold" {
  bucket = aws_s3_bucket.gold.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "gold" {
  bucket = aws_s3_bucket.gold.id

  rule {
    id     = "expire-old-versions"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration {
      noncurrent_days = 90
    }
  }

  # Managed out-of-band by PCM ops; declared so a plan never removes it.
  rule {
    id     = "pcm-mpu-fragment-policy"
    status = "Enabled"
    filter {}
    abort_incomplete_multipart_upload {
      days_after_initiation = 14
    }
  }
}

# SSE-S3, not a customer-managed key, which trivy reads as AVD-AWS-0132 (HIGH).
# Suppressed rather than left failing, on the position this estate already holds:
# the main echo root carries the identical directive for the identical check
# (infrastructure/terraform/s3.tf:137), which records it as owner-instructed and
# notes that NO stack in this repo uses a CMK anywhere -- so this is the estate's
# consistent position, not a gold exception. Whether a CMK is REQUIRED follows
# from the t_dcl data classification, which does not exist yet; when it does,
# these two directives are the thing to revisit alongside the main root's
# (UIFPE-742).
#
# The directive must be the LAST comment line before the block or trivy silently
# ignores it -- apicurio established that by testing.
# trivy:ignore:AVD-AWS-0132
resource "aws_s3_bucket_server_side_encryption_configuration" "gold" {
  bucket = aws_s3_bucket.gold.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = false
  }
}

resource "aws_s3_bucket_public_access_block" "gold" {
  bucket                  = aws_s3_bucket.gold.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket" "gold_artifacts" {
  bucket = var.gold_artifacts_bucket
  tags = merge(local.common_tags, {
    Layer = "gold-artifacts"
  })
  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_versioning" "gold_artifacts" {
  bucket = aws_s3_bucket.gold_artifacts.id
  versioning_configuration {
    status = "Enabled"
  }
}

# SSE-S3, not a customer-managed key, which trivy reads as AVD-AWS-0132 (HIGH).
# Suppressed rather than left failing, on the position this estate already holds:
# the main echo root carries the identical directive for the identical check
# (infrastructure/terraform/s3.tf:137), which records it as owner-instructed and
# notes that NO stack in this repo uses a CMK anywhere -- so this is the estate's
# consistent position, not a gold exception. Whether a CMK is REQUIRED follows
# from the t_dcl data classification, which does not exist yet; when it does,
# these two directives are the thing to revisit alongside the main root's
# (UIFPE-742).
#
# The directive must be the LAST comment line before the block or trivy silently
# ignores it -- apicurio established that by testing.
# trivy:ignore:AVD-AWS-0132
resource "aws_s3_bucket_server_side_encryption_configuration" "gold_artifacts" {
  bucket = aws_s3_bucket.gold_artifacts.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = false
  }
}

resource "aws_s3_bucket_public_access_block" "gold_artifacts" {
  bucket                  = aws_s3_bucket.gold_artifacts.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
