# Deliberately NO per-domain variables: domain facts (gold database, mapping
# location, event type, business keys, bus name, seeds) live in each domain's
# pipeline-configs record and its event-domain module instance. A variable
# that would need a new value per domain does not belong in this file.

variable "environment" {
  type        = string
  description = "Deployment environment (dev / uat / prod)."
}

variable "aws_region" {
  type        = string
  description = "AWS region."
  default     = "eu-west-1"
}

variable "gold_bucket" {
  type        = string
  description = "The account's gold data bucket (uif-echo-{env}-gold) - one bucket for every domain's gold tables, read by the publish job for IAM-governed tables."
}

variable "gold_artifacts_bucket" {
  type        = string
  description = "The account's gold artifacts bucket (uif-echo-{env}-gold-artifacts). The stack's working files live under the event-pipeline/ prefix; domains upload their mapping/seed files under event-pipeline/domains/{domain}/."
}

variable "athena_workgroup" {
  type        = string
  description = "Athena workgroup for the controller's delta count and the publish job's run query."
  default     = "primary"
}

variable "gold_merge_bus_name" {
  type        = string
  description = "Bus carrying GoldMergeCompleted (the echo gold announcer's control bus). Empty = the convention uif-event-{env}-bus."
  default     = ""
}

# The release identity of this deployment, keyed by the manifest's artifact
# names (locals.tf glue_scripts + lambda_artifacts). NOT set in envs/*.tfvars:
# a tracked file would pin every plan to one build. It arrives in a gitignored
# artifacts.auto.tfvars.json --
#   dev    written by scripts/stage-release-artifacts.sh, which hashes and uploads
#          from source (dev is the only environment that builds);
#   later  written from the build tag's manifest by the promotion.
# Empty is not an error here; artifact_guard.tf is, at plan time.
variable "artifact_shas" {
  type        = map(string)
  description = "Content hash per release artifact (64 lowercase hex), keyed by manifest artifact name."
  default     = {}

  validation {
    condition     = alltrue([for k, v in var.artifact_shas : can(regex("^[0-9a-f]{64}$", v))])
    error_message = "Every artifact_shas value must be 64 lowercase hex characters with no algorithm prefix."
  }
}

# Base64 sha256 of each Lambda zip's own bytes, for source_code_hash. A
# different fact from artifact_shas (the tree hash naming the key): this is
# what lets a plan notice a hand-run UpdateFunctionCode.
variable "lambda_code_hashes" {
  type        = map(string)
  description = "Base64 sha256 of each Lambda zip, keyed by manifest artifact name."
  default     = {}

  validation {
    condition     = alltrue([for k, v in var.lambda_code_hashes : can(regex("^[A-Za-z0-9+/]{43}=$", v))])
    error_message = "Every lambda_code_hashes value must be base64 of a sha256 digest (43 characters plus '='), not hex."
  }
}

# PutRule on a missing bus fails the whole apply (ResourceNotFoundException,
# 2026-09-02), so the rule/target/permission stay gated per environment; the
# bus is created by the echo gold stack (terraform-gold/announce.tf).
variable "enable_gold_merge_trigger" {
  type        = bool
  description = "Create the GoldMergeCompleted rule/target/permission (requires the bus uif-event-{env}-bus to exist)."
  default     = false
}
