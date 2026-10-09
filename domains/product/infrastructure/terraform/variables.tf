variable "environment" {
  type        = string
  description = "Deployment environment — valid values come from stacks/echo/src/apps/environments.json (the picklist)"

  validation {
    condition     = contains(jsondecode(file("${path.module}/../../../../stacks/echo/src/apps/environments.json")).environments, var.environment)
    error_message = "environment must be one of the values in environments.json"
  }
}

variable "aws_region" {
  type    = string
  default = "eu-west-1"
}

# ── Gold-owned buckets (created by the platform stack today; ownership moves
#    here via `terraform import` at cutover — see README) ────────────────────

variable "gold_bucket" {
  type        = string
  description = "The gold Iceberg warehouse this domain's consumers read (uif-echo-{env}-gold)"
}

variable "gold_artifacts_bucket" {
  type        = string
  description = "Gold artifacts bucket this domain ships to and its serving copies write under (uif-echo-{env}-gold-artifacts)"
}

# ── The containerised-API substrate switch (UIFPE-154) ──────────────────────


# ── The gateway hop's identity check on this domain's APIs ──────────────────
# NOT consumer auth: that is EntraID at the Mule gateway. This verifies the
# IES token the gateway mints for itself. Same shape and the same JWT_*
# environment variables as the gold retrieval API, so one configuration
# covers both.

variable "api_jwt_required" {
  description = "Require a verified bearer token on this domain's APIs"
  type        = bool
  default     = true
}

variable "api_jwt_issuer" {
  description = "Exact value the token's iss claim must carry"
  type        = string
  default     = ""
}

variable "api_jwt_jwks_url" {
  description = "HTTPS url of the issuer's JWKS document"
  type        = string
  default     = ""

  validation {
    # http would put the verification keys themselves on the wire in clear.
    condition     = var.api_jwt_jwks_url == "" || startswith(var.api_jwt_jwks_url, "https://")
    error_message = "api_jwt_jwks_url must be https."
  }
}

variable "api_jwt_token_type" {
  description = "Required value of the token's type claim; empty skips the check"
  type        = string
  default     = ""
}

variable "api_jwt_algs" {
  description = "Permitted signing algorithms"
  type        = list(string)
  default     = ["RS512"]

  validation {
    # An HMAC algorithm lets a caller sign with the public key as the secret,
    # and "none" lets them sign with nothing. RS* only.
    condition     = alltrue([for a in var.api_jwt_algs : contains(["RS256", "RS384", "RS512"], a)])
    error_message = "api_jwt_algs may only contain RS256, RS384 or RS512."
  }
}

variable "api_jwt_allowed_subs" {
  description = "Subject claims permitted to read; this is the whole authorization decision"
  type        = list(string)
  default     = []

  validation {
    # A comma inside a value would split into two subjects once joined into
    # the environment variable, admitting a caller nobody listed.
    condition     = alltrue([for s in var.api_jwt_allowed_subs : !strcontains(s, ",") && trimspace(s) != ""])
    error_message = "Each api_jwt_allowed_subs entry must be non-empty and contain no comma."
  }
}
