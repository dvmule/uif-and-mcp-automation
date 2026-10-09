variable "environment" {
  type        = string
  description = "Deployment environment (picklist: src/apps/environments.json)"
  validation {
    condition     = contains(jsondecode(file("${path.module}/../../src/apps/environments.json")).environments, var.environment)
    error_message = "environment must be one of the values in src/apps/environments.json"
  }
}

variable "aws_region" {
  type    = string
  default = "eu-west-1"
}


variable "gold_bucket" {
  type        = string
  description = "The gold Iceberg warehouse (uif-echo-{env}-gold)"
}

variable "gold_artifacts_bucket" {
  type        = string
  description = "Schema, manifest and SQL artifacts the pipelines read (uif-echo-{env}-gold-artifacts)"
}

variable "athena_workgroup" {
  type        = string
  default     = "primary"
  description = "Athena workgroup the grain probe queries through"
}

# ── Alerting (UIFPE-543) ────────────────────────────────────────────────────

variable "alert_seed_subscribers" {
  type        = list(string)
  default     = []
  description = "Addresses seeded ONCE into the alert-subscribers table; afterwards the Notifications page owns the rows"
}

variable "notifications_api_url" {
  type        = string
  default     = "https://notifications-api.stg-prsn.com"
  description = "Pearson Notifications API base URL (per env: stg for dev) - the same value beacon uses"
}

# The notifier runs INSIDE the account VPC, like beacon's deliver Lambda: the
# Pearson Notifications and PI endpoints live on private addresses reachable
# only through the VPC's corporate gateway route. Found live on 2026-09-23:
# outside the VPC every send timed out.
variable "vpc_id" {
  type        = string
  description = "VPC the alert notifier runs in (dev: gip-uif-nonprod-vpc-euw1)"
}

variable "private_subnet_ids" {
  type        = list(string)
  description = "Private subnets with NAT egress and the corporate gateway route"
}

variable "secretsmanager_endpoint_sg_id" {
  type        = string
  default     = ""
  description = "Security group of the VPC's Secrets Manager interface endpoint (empty = no rule). Private DNS forces every Secrets Manager call inside the VPC through it, and it admits only listed groups"
}

variable "notifications_token_url" {
  type        = string
  default     = "https://int-piapi-internal.stg-openclass.com/tokens"
  description = "PI API v1 tokens endpoint the alert mail mints X-Authorization tokens from (per env: stg for dev)"
}

# ── The promotion model's artifact inputs (UIFPE-518, UIFPE-576) ────────────
#
# Both default empty, so an apply that has staged nothing still plans: the
# resource falls back to its plan-time zip. Same shape as the echo root's
# (stacks/echo/infrastructure/terraform/variables.tf), deliberately, so gold
# joins the existing release flow rather than growing a second one.

variable "release_artifacts_bucket" {
  description = "S3 bucket holding content-addressed release artifacts. Empty means nothing has been staged, and the plan-time zip is used."
  type        = string
  default     = ""
}

variable "artifact_shas" {
  description = "Content hash per artifact, keyed by manifest artifact name. Empty for a dev apply, which still builds at plan time."
  type        = map(string)
  default     = {}

  validation {
    # 64 lowercase hex, no algorithm prefix — the shape release-manifest.sh
    # writes and stage-artifacts.sh emits. A prefixed or truncated value would
    # build a key that resolves to nothing and fail at CreateFunction.
    condition = alltrue([
      for k, v in var.artifact_shas : can(regex("^[0-9a-f]{64}$", v))
    ])
    error_message = "Every artifact_shas value must be 64 lowercase hex characters with no algorithm prefix."
  }
}

variable "lambda_code_hashes" {
  description = "Base64 sha256 of each Lambda zip, for source_code_hash; keyed by manifest artifact name"
  type        = map(string)
  default     = {}

  validation {
    # base64 of a 32-byte digest is 43 characters plus one '=' pad. A hex
    # digest here is silently accepted by Terraform and then never matches,
    # producing a permanent diff nobody can explain.
    condition = alltrue([
      for k, v in var.lambda_code_hashes : can(regex("^[A-Za-z0-9+/]{43}=$", v))
    ])
    error_message = "Every lambda_code_hashes value must be base64 of a sha256 digest (43 characters plus '='), not hex."
  }
}

# ── The gateway hop's identity check on the gold retrieval API ──────────────
# NOT consumer auth: that is EntraID at the Mule gateway. This verifies the
# IES token the gateway mints for itself, so the subject names the gateway.

variable "gold_api_jwt_required" {
  description = "Require a verified bearer token on the gold retrieval API"
  type        = bool
  default     = true
}

variable "gold_api_jwt_issuer" {
  description = "Exact value the token's iss claim must carry"
  type        = string
  default     = ""
}

variable "gold_api_jwt_jwks_url" {
  description = "HTTPS url of the issuer's JWKS document"
  type        = string
  default     = ""

  validation {
    # http would put the verification keys themselves on the wire in clear.
    condition     = var.gold_api_jwt_jwks_url == "" || startswith(var.gold_api_jwt_jwks_url, "https://")
    error_message = "gold_api_jwt_jwks_url must be https."
  }
}

variable "gold_api_jwt_token_type" {
  description = "Required value of the token's type claim; empty skips the check"
  type        = string
  default     = ""
}

variable "gold_api_jwt_algs" {
  description = "Permitted signing algorithms"
  type        = list(string)
  default     = ["RS512"]

  validation {
    # An HMAC algorithm lets a caller sign with the public key as the secret,
    # and "none" lets them sign with nothing. RS* only.
    condition     = alltrue([for a in var.gold_api_jwt_algs : contains(["RS256", "RS384", "RS512"], a)])
    error_message = "gold_api_jwt_algs may only contain RS256, RS384 or RS512."
  }
}

variable "gold_api_jwt_allowed_subs" {
  description = "Subject claims permitted to read; this is the whole authorization decision"
  type        = list(string)
  default     = []

  validation {
    # A comma inside a value would split into two subjects once joined into
    # the environment variable, admitting a caller nobody listed.
    condition     = alltrue([for s in var.gold_api_jwt_allowed_subs : !strcontains(s, ",") && trimspace(s) != ""])
    error_message = "Each gold_api_jwt_allowed_subs entry must be non-empty and contain no comma."
  }
}
