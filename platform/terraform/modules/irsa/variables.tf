variable "name" {
  description = "Name of the IAM role, e.g. \"product-api\"."
  type        = string
}

variable "oidc_provider_arn" {
  description = "ARN of the EKS cluster's IAM OIDC provider (aws_iam_openid_connect_provider)."
  type        = string
}

variable "oidc_provider_url" {
  description = "OIDC issuer URL of the EKS cluster, without the leading \"https://\" (e.g. oidc.eks.<region>.amazonaws.com/id/<id>)."
  type        = string
}

variable "namespace" {
  description = "Kubernetes namespace the ServiceAccount lives in, e.g. \"product\"."
  type        = string
}

variable "service_account_name" {
  description = "Kubernetes ServiceAccount name this role trusts, e.g. \"product-api\"."
  type        = string
}

variable "policy_arns" {
  description = "Managed IAM policy ARNs to attach (least-privilege — no AdministratorAccess, no *:* actions)."
  type        = list(string)
  default     = []
}

variable "tags" {
  description = "Tags applied to the IAM role."
  type        = map(string)
  default     = {}
}
