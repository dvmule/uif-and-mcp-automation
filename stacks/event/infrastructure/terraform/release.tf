# The stack's release-artifacts bucket: uif-event-{env}-release-artifacts.
#
# Holds every Lambda zip and Glue script this root deploys, each at a key named
# after its content hash (lambdas/<sha>.zip, glue/<sha>/<file>). In dev,
# scripts/stage-release-artifacts.sh uploads into it from source; in every later
# environment the promotion copies into it from dev's, pinned to the version the
# build tag recorded. See docs/uif-event-uat-rollout-plan.md.
#
# Hardening mirrors echo's release bucket (stacks/echo/infrastructure/terraform/
# s3.tf) and apicurio's, deliberately:
#   - versioned, so the manifest can record a version_id per artifact;
#   - NO lifecycle expiry -- a release artifact must stay resolvable for as long
#     as anything might roll back to the tag that names it;
#   - never public; SSE-S3, not a CMK (the estate's position, see echo's note).
#
# WHO CREATES IT FIRST. Artifacts go into this bucket BEFORE the plan (staged in
# dev, copied in a promoted environment), so it must exist before the first
# plan -- a chicken-and-egg Terraform alone cannot solve. Both workflows resolve
# it with a targeted apply of the four resources below, run once:
#
#   dev       deploy-event-dev.yml, on its first apply run (echo's 4.5).
#   promoted  deploy-event-infra.yml with infra=bootstrap -- the stack promotion
#             model's "FIRST APPLY ONLY" mode, in the same job as the plan and
#             apply that follow (capabilities/delivery/stack-promotion-model.md
#             sections 3 and 5).

resource "aws_s3_bucket" "release_artifacts" {
  bucket = local.release_artifacts_bucket
  tags   = local.common_tags
}

resource "aws_s3_bucket_public_access_block" "release_artifacts" {
  bucket                  = aws_s3_bucket.release_artifacts.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# trivy:ignore:AVD-AWS-0132
resource "aws_s3_bucket_server_side_encryption_configuration" "release_artifacts" {
  bucket = aws_s3_bucket.release_artifacts.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

# Versioning is what gives each staged object a version_id to record. Nothing
# READS that id back -- the content-addressed key is the identity.
resource "aws_s3_bucket_versioning" "release_artifacts" {
  bucket = aws_s3_bucket.release_artifacts.id
  versioning_configuration {
    status = "Enabled"
  }
}
