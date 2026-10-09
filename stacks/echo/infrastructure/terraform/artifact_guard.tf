# Refuse to plan or apply a uif stack without the release artifact hashes.
#
# Nothing in a uif-echo stack reads its code from this repository. Every Lambda,
# layer and Glue script reads it from a release-artifact key named after a
# content hash, and only the deploy scripts' staging step supplies those hashes
# (artifacts.auto.tfvars.json). Skip it and every key resolves to the sentinel,
# a path no object is at, and the apply then:
#
#   - DELETES each Lambda layer version before failing to publish its
#     replacement, leaving live functions on versions that no longer exist;
#   - repoints all ten bronze and silver Glue jobs at a missing script, so the
#     next dispatched run fails;
#   - stops part-way, leaving the root half-converged.
#
# That happened twice on 2026-09-24 (runs 36025076885 and 36034078478) because
# the only thing guarding it was a human noticing the sentinel in a long plan.
# This makes it a hard stop at plan time, before anything changes. The cause is
# fixed too -- uif-echo-infra-deploy.sh now stages, as the apps script does --
# so this should never fire from the pipeline; it catches a hand-run terraform.
#
# `terraform validate` does not evaluate preconditions, so fmt/validate in CI
# are unaffected. UIFPE-608.
#
# The hashes reach a plan two different ways, and this guard covers both:
# dev-uif STAGES them from source, every other uif env is PROMOTED and gets them
# in an artifacts.auto.tfvars.json built from the promoted digests (UIFPE-555).
# A promoted env has no staging step to fall back on, so a missing or unwritten
# file there is exactly the case worth stopping.
resource "terraform_data" "artifact_shas_present" {
  count = local.is_uif_stack ? 1 : 0

  # Kept in state so a regression is a visible diff, not just an error string.
  input = length(local.artifacts_missing_sha)

  lifecycle {
    precondition {
      condition     = length(local.artifacts_missing_sha) == 0
      error_message = <<-EOT
        Release artifact hashes are missing, so these S3 keys point at nothing:

          - ${join("\n  - ", local.artifacts_missing_sha)}

        Nothing has been changed. Applying a key that points at nothing destroys
        what is there and cannot replace it: a layer version is deleted before
        its replacement fails to publish, and a Glue job is repointed at a
        script that does not exist (UIFPE-608).

        For dev-uif the hashes come from staging the release artifacts, which
        both deploy scripts do before they plan:

          bash stacks/echo/infrastructure/deploy/uif-echo-infra-deploy.sh dev-uif

        Calling terraform by hand skips that step. Use the script, or run the
        staging step yourself first.

        For a promoted env (uat-uif and anything after it) nothing stages: the
        hashes belong in a promoted artifacts.auto.tfvars.json, regenerated from
        the digests of the artifacts actually promoted. If that file is absent,
        the promotion is what is incomplete -- do not stage from source to fill
        the gap, or the release tag will attest to bytes nothing is running.

        A legacy echo-* stack expects no hashes at all and never reaches here.
      EOT
    }
  }
}
