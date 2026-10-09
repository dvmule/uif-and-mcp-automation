# Refuse to plan or apply without the release artifact hashes.
#
# Every Lambda and Glue job in this root reads its code from a release-artifact
# key named after a content hash (locals.tf). Only the staging step (dev) or
# the promotion (later environments) supplies those hashes, in a gitignored
# artifacts.auto.tfvars.json. Skip it and every key resolves to
# ARTIFACT-SHA-NOT-SUPPLIED -- a path no object is at -- and the apply would
# repoint the publish and recovery jobs at a missing script and fail each
# Lambda update part-way.
#
# Ported from echo's artifact_guard.tf, which exists because exactly that broke
# echo's dev twice on 2026-09-24 (UIFPE-608). No legacy-stack gate here: every
# event environment is uif-event-*, so the guard always applies.
#
# `terraform validate` does not evaluate preconditions, so CI's fmt/validate
# are unaffected. A targeted apply that excludes this resource (the release
# bucket's first creation) is unaffected too.
resource "terraform_data" "artifact_shas_present" {
  # Kept in state so a regression is a visible diff, not only an error string.
  input = length(local.artifacts_missing_sha)

  lifecycle {
    precondition {
      condition     = length(local.artifacts_missing_sha) == 0
      error_message = <<-EOT
        Release artifact hashes are missing, so these S3 keys point at nothing:

          - ${join("\n  - ", local.artifacts_missing_sha)}

        Nothing has been changed.

        In dev the hashes come from staging, which deploy-event-dev.yml does
        before it plans:

          bash stacks/event/scripts/stage-release-artifacts.sh \
            --bucket ${local.release_artifacts_bucket} \
            --tfvars stacks/event/infrastructure/terraform/artifacts.auto.tfvars.json

        (or --hash-only --tfvars ... to read a plan before the bucket exists).

        In a promoted environment nothing stages: the hashes come from the
        build tag's manifest. If artifacts.auto.tfvars.json is absent there, the
        promotion is incomplete -- do not stage from source to fill the gap, or
        the tag will attest to bytes nothing is running.
      EOT
    }
  }
}
