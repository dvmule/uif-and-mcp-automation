# Release artifacts — the immutable, retrievable copy of each release's UI
# bundle, keyed by its content hash.
#
# WHY THIS IS NOT THE UI BUCKET (frontend.tf). Reusing that bucket has two
# bugs, not one preference:
#
#   1. deploy-apicurio-dev.yml syncs dist/ to that bucket's ROOT with --delete,
#      so anything stored alongside the bundle is removed on the next UI
#      deploy. An archive kept there would survive exactly until someone
#      shipped a UI change.
#   2. That bucket is a public CloudFront origin, so release evidence would be
#      downloadable by anyone who guessed the key.
#
# WHY THE KEYS ARE CONTENT HASHES. <sha256>.tar.gz is a content address, not a
# version token, so .claude/rules/naming/00-core.md's ban on version tokens is
# not engaged -- and neither is ADR-0019's open question about whether a
# versioned S3 prefix (jobs/v1.4.3/) is compatible with that rule. Echo has to
# resolve that question; Apicurio does not.
#
# No lifecycle rule, deliberately. These objects are what a rollback restores,
# so an expiry would quietly set a horizon on how far back a release can be
# rolled. Add one when there is a retention requirement to encode, not before.

resource "aws_s3_bucket" "release_artifacts" {
  # uif-{stack}-{env}-{purpose}, same as every other name in this stack.
  # 35 characters at the longest env, well inside the 63-char limit, and
  # globally unique because the stack token is.
  bucket = "${local.name}-release-artifacts"

  # NOT force_destroy. Unlike the UI bucket, whose contents are a rebuildable
  # bundle, these objects are release evidence -- a destroy that silently took
  # them with it would remove the thing a rollback needs.
  force_destroy = false

  tags = { component = "release" }
}

resource "aws_s3_bucket_public_access_block" "release_artifacts" {
  bucket                  = aws_s3_bucket.release_artifacts.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Versioning on, so an accidental overwrite of a content-addressed key is
# recoverable and the manifest can record a version_id alongside the hash.
resource "aws_s3_bucket_versioning" "release_artifacts" {
  bucket = aws_s3_bucket.release_artifacts.id
  versioning_configuration {
    status = "Enabled"
  }
}

# SSE-S3 rather than a CMK. These archives contain exactly the bytes CloudFront
# already serves to every browser, so there is no confidentiality requirement a
# customer-managed key would protect -- it would add a key policy and a
# rotation obligation for no gain. The bucket is private because release
# evidence should not be enumerable, not because the bytes are secret.
# trivy:ignore:AVD-AWS-0132 See the comment above.
resource "aws_s3_bucket_server_side_encryption_configuration" "release_artifacts" {
  bucket = aws_s3_bucket.release_artifacts.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}
