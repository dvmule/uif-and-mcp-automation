# Data bucket: configs/, code/, staging/, pickup/, rejected/ prefixes.
# Versioned (config history, batch audit), SSE-S3, private.

resource "aws_s3_bucket" "data" {
  bucket = local.data_bucket
}

resource "aws_s3_bucket_versioning" "data" {
  bucket = aws_s3_bucket.data.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_public_access_block" "data" {
  bucket                  = aws_s3_bucket.data.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "data" {
  bucket = aws_s3_bucket.data.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

# Rejected rows and batch staging expire automatically (30-day audit window —
# same policy the sandbox/demo used).
resource "aws_s3_bucket_lifecycle_configuration" "data" {
  bucket = aws_s3_bucket.data.id

  rule {
    id     = "expire-rejected"
    status = "Enabled"
    filter { prefix = "rejected/" }
    expiration { days = 30 }
  }
  rule {
    id     = "expire-staging"
    status = "Enabled"
    filter { prefix = "staging/" }
    expiration { days = 14 }
  }
  # UIFPE-441: Athena result files from the wizard's live filter preview —
  # samples of gold data, re-creatable on demand, gone the next day. The
  # bucket is VERSIONED, so `expiration` alone only writes a delete marker;
  # the noncurrent rule is what actually removes the bytes.
  rule {
    id     = "expire-preview"
    status = "Enabled"
    filter { prefix = "${local.preview_prefix}/" }
    expiration { days = 1 }
    noncurrent_version_expiration { noncurrent_days = 1 }
    abort_incomplete_multipart_upload { days_after_initiation = 1 }
  }
}

# UI bucket: private; served exclusively through CloudFront (OAC).
resource "aws_s3_bucket" "ui" {
  bucket = local.ui_bucket
}

resource "aws_s3_bucket_public_access_block" "ui" {
  bucket                  = aws_s3_bucket.ui.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
