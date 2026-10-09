variable "domain" {
  type        = string
  description = "The domain this instance onboards (product, price, program, ...) - composes every resource name."
}

variable "environment" {
  type        = string
  description = "Deployment environment (dev / uat / prod)."
}

variable "artifacts_bucket" {
  type        = string
  description = "The gold artifacts bucket (uif-echo-{env}-gold-artifacts) - the domain's mapping/seed files land under event-pipeline/domains/{domain}/."
}

variable "mapping_source" {
  type        = string
  description = "Local path to the domain's registered gold attribute mapping (the channel-gating source of truth)."
}

variable "subscription_seeds_source" {
  type        = string
  description = "Local path to the domain's bootstrap subscription seeds (JSON list of DynamoDB-typed items). Empty = no bootstrap: records count no_subscribers until real subscriptions exist."
  default     = ""
}

variable "enable_dlq_alarm" {
  type        = bool
  description = "Create the DLQ non-zero-depth alarm (needs cloudwatch:PutMetricAlarm on the deploy role)."
  default     = false
}

variable "tags" {
  type        = map(string)
  description = "Tags applied to every resource in this instance."
  default     = {}
}
