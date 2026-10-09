# s3_buckets.tf
# Creates the Glue scripts bucket, the Bronze data lake bucket, and the Silver data lake bucket.




resource "aws_s3_bucket" "artifacts" {
  bucket = var.artifacts_bucket

  tags = {
    Project   = local.project_tag
    ManagedBy = "terraform"
  }
}

resource "aws_s3_bucket_versioning" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  versioning_configuration {
    status = "Enabled"
  }
}

# ── Public access blocks (UIFPE-515) ─────────────────────────────────────────
#
# None of these three buckets had one, which trivy reports as four HIGH
# findings each -- AWS-0086, -0087, -0091 and -0093, one per flag. Twelve
# findings, one missing resource, three times over. They were found by the
# first trivy run this stack has ever had (qe/ci.sh gained the gate in this
# same change); nothing suggests these buckets were ever meant to be
# reachable publicly.
#
# SAFE ON A LIVE BUCKET, which is why this is done here rather than deferred:
# a public access block is a SEPARATE resource attached to an existing bucket.
# It changes no bucket name and moves no data, so 00-core.md's "never rename a
# deployed resource" does not apply -- unlike the CMK finding (AWS-0132), which
# stays open deliberately. See the note on the release bucket above.
#
# What each bucket holds, and so why none of them is public:
#   artifacts  Terraform state, pipeline configs, Glue scripts, layer zips
#   bronze     the Oracle replica, landed as Parquet
#   silver     the Iceberg tables built from it

resource "aws_s3_bucket_public_access_block" "artifacts" {
  bucket                  = aws_s3_bucket.artifacts.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Release artifacts (UIFPE-515): content-addressed Lambda zips, layer zips,
# Glue scripts and the UI bundle. The key IS the sha256, so an object is never
# overwritten -- a changed artifact lands at a new key and the old one stays
# reachable for as long as a release references it.
#
# DELIBERATELY NO LIFECYCLE EXPIRY. The `artifacts` bucket above has none for
# the same reason; `bronze` and `silver` below expire noncurrent versions at 90
# days, and a release artifact on such a bucket becomes unresolvable the day a
# rollback needs it. Growth here is bounded by how often artifacts actually
# change, not by deploy frequency, because an unchanged artifact re-uploads to
# the same key and is skipped.
# ONLY for a uif-echo-* stack. The legacy echo-* stacks keep uploading scripts
# to their own artifacts bucket and zipping Lambdas at plan time, so they have
# no release artifacts to hold and no bucket to create.
resource "aws_s3_bucket" "release_artifacts" {
  count  = local.is_uif_stack ? 1 : 0
  bucket = var.release_artifacts_bucket

  lifecycle {
    precondition {
      condition     = var.release_artifacts_bucket != ""
      error_message = "release_artifacts_bucket must be set for a uif-echo-* stack: its Lambda zips, layer zips, Glue scripts and UI bundle are all read from it. Add it to this environment's tfvars."
    }

    # THE MANDATORY TAGS, ASSERTED HERE BECAUSE THEY CANNOT BE ASSERTED WHERE
    # THEY ARE DECLARED. local.default_tags is gated on local.is_uif_stack so
    # the legacy stacks stay untagged, which forces the five variables to
    # default to "" -- and a variable validation cannot see is_uif_stack, so it
    # cannot demand a value on a uif stack only.
    #
    # This resource can: it exists only when is_uif_stack is true, so a
    # precondition on it fires exactly for the stacks that must carry tags, at
    # plan time, before anything is created. Without it the relaxed validation
    # would let a uif stack apply with seven empty tag values and look fine.
    #
    # t_name is not checked -- it has a real default ("echo"). t_environment is
    # derived from var.environment, which is already validated.
    precondition {
      condition = alltrue([
        var.t_app_id != "",
        var.t_cost_centre != "",
        var.t_dcl != "",
        var.t_owner_individual != "",
        var.t_responsible_individuals != "",
      ])
      error_message = "A uif-echo-* stack must carry the Pearson mandatory tags, and at least one of t_app_id, t_cost_centre, t_dcl, t_owner_individual or t_responsible_individuals is empty. They default to \"\" so the legacy echo-* stacks can plan without them; a uif stack has to set all five in its tfvars. See envs/dev-uif.tfvars."
    }
  }

  tags = {
    Project   = local.project_tag
    ManagedBy = "terraform"
  }
}

# Never public. trivy flags every bucket in this file for having no public
# access block, and this is the one that can be fixed without touching a
# deployed bucket's configuration: the other three are live and their blocks
# are a separate, deliberate change. Same shape beacon uses on its data bucket
# (stacks/beacon/infrastructure/terraform/s3.tf).
resource "aws_s3_bucket_public_access_block" "release_artifacts" {
  count                   = local.is_uif_stack ? 1 : 0
  bucket                  = aws_s3_bucket.release_artifacts[0].id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# SSE-S3, matching beacon's data bucket. NOT a customer-managed key: trivy asks
# for one and this deliberately does not provide it, because a CMK here would
# be the only one in the stack -- the other three buckets hold the actual lake
# data and use the same default. A key for release artifacts alone would add
# cost and a key policy to maintain while leaving the data buckets as they are,
# which is the wrong place to start.
#
# Suppressed on owner instruction rather than left failing, following
# stacks/apicurio, which carries the same directive for the same check on its
# own release-artifacts bucket (release-artifacts.tf:60) and its UI bucket
# (frontend.tf:46). No stack in this repo uses a CMK anywhere, so this is the
# estate's consistent position, not an Echo exception. Whether a CMK is
# REQUIRED follows from Echo's t_dcl data classification, which does not exist
# yet; when it does, this directive is the thing to revisit.
#
# The bootstrap module's tfstate bucket carries the identical directive for the
# identical reason (bootstrap/main.tf).
# trivy:ignore:AVD-AWS-0132
resource "aws_s3_bucket_server_side_encryption_configuration" "release_artifacts" {
  count  = local.is_uif_stack ? 1 : 0
  bucket = aws_s3_bucket.release_artifacts[0].id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

# Versioning is on so the manifest can record a version_id per artifact as
# evidence. Nothing READS that id -- the content-addressed key is the identity,
# and a copy into another environment gets a different id by construction.
resource "aws_s3_bucket_versioning" "release_artifacts" {
  count  = local.is_uif_stack ? 1 : 0
  bucket = aws_s3_bucket.release_artifacts[0].id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket" "bronze" {
  bucket = var.bronze_bucket

  tags = {
    Project   = local.project_tag
    ManagedBy = "terraform"
    Layer     = "bronze"
  }
}

resource "aws_s3_bucket_versioning" "bronze" {
  bucket = aws_s3_bucket.bronze.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_public_access_block" "bronze" {
  bucket                  = aws_s3_bucket.bronze.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "bronze" {
  bucket = aws_s3_bucket.bronze.id

  rule {
    id     = "expire-old-partitions"
    status = "Enabled"

    filter {}

    noncurrent_version_expiration {
      noncurrent_days = 90
    }
  }

  # Managed out-of-band by PCM ops; imported into TF to prevent drift removal.
  rule {
    id     = "pcm-mpu-fragment-policy"
    status = "Enabled"

    filter {}

    abort_incomplete_multipart_upload {
      days_after_initiation = 14
    }
  }
}

resource "aws_s3_bucket" "silver" {
  bucket = var.silver_bucket

  tags = {
    Project   = local.project_tag
    ManagedBy = "terraform"
    Layer     = "silver"
  }
}

resource "aws_s3_bucket_versioning" "silver" {
  bucket = aws_s3_bucket.silver.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_public_access_block" "silver" {
  bucket                  = aws_s3_bucket.silver.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "silver" {
  bucket = aws_s3_bucket.silver.id

  rule {
    id     = "expire-old-versions"
    status = "Enabled"

    filter {}

    noncurrent_version_expiration {
      noncurrent_days = 90
    }
  }

  # Managed out-of-band by PCM ops; imported into TF to prevent drift removal.
  rule {
    id     = "pcm-mpu-fragment-policy"
    status = "Enabled"

    filter {}

    abort_incomplete_multipart_upload {
      days_after_initiation = 14
    }
  }
}
