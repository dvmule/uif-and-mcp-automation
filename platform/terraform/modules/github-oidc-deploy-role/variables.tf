variable "name" {
  description = "Name of the IAM role, e.g. \"product-cd\"."
  type        = string
}

variable "oidc_provider_arn" {
  description = "ARN of the account's GitHub Actions IAM OIDC provider (../github-oidc-provider)."
  type        = string
}

variable "github_org" {
  description = "GitHub organization that owns the repo, e.g. \"pearson-dnt-coe\"."
  type        = string
}

variable "github_repo" {
  description = "Repository name, e.g. \"universal-Integration-framework\"."
  type        = string
}

variable "allowed_branches" {
  description = "Branch names allowed to assume this role via push (e.g. [\"main\"]). Scopes the sub claim to \"repo:<org>/<repo>:ref:refs/heads/<branch>\" — never wildcard the whole repo."
  type        = list(string)
  default     = []
}

variable "allowed_environments" {
  description = "GitHub Environment names allowed to assume this role (e.g. [\"production\"]). Required for any promotion step gated behind a GitHub Environment's manual-approval protection rule — scopes the sub claim to \"repo:<org>/<repo>:environment:<name>\"."
  type        = list(string)
  default     = []
}

variable "policy_arns" {
  description = "Managed IAM policy ARNs to attach (least-privilege — scoped to this domain's own resources, never AdministratorAccess)."
  type        = list(string)
  default     = []
}

variable "tags" {
  description = "Tags applied to the IAM role."
  type        = map(string)
  default     = {}
}
