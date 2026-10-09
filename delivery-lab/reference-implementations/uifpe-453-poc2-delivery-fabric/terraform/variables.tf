variable "region" {
  description = "AWS region (dev eventing stack lives in eu-west-1)"
  type        = string
  default     = "eu-west-1"
}

variable "prefix" {
  description = "Resource name prefix — poc2 namespace, fully isolated from uif-event-poc-* and the live stack"
  type        = string
  default     = "uif-event-poc2"
}

variable "domain" {
  type    = string
  default = "product"
}

variable "detail_type" {
  description = "The single detail-type both POC subscriptions listen to"
  type        = string
  default     = "product.created.v1"
}

# One entry per POC consumer. target_type drives which target resource is
# wired and which SDK call the Delivery Fabric Lambda makes.
variable "customers" {
  type = map(object({
    target_type = string # SQS | KINESIS
  }))
  default = {
    pmc = { target_type = "SQS" }
    pdz = { target_type = "KINESIS" }
  }
}

variable "fabric_max_concurrency" {
  description = "ESM maximum_concurrency on the shared buffer — the fleet-wide delivery throttle"
  type        = number
  default     = 5
}

locals {
  detail_type_dash = replace(var.detail_type, ".", "-")

  t_publish       = "${var.prefix}-publish-metrics"      # the ledger (expected set + sk source)
  t_delivery      = "${var.prefix}-delivery-metrics"     # outcome rows, born DELIVERED/FAILED
  t_subscriptions = "${var.prefix}-subscription-configs" # routing: rule_arn -> customer + target

  bus_name = "${var.prefix}-${var.domain}-bus"

  sqs_customers     = { for k, v in var.customers : k => v if v.target_type == "SQS" }
  kinesis_customers = { for k, v in var.customers : k => v if v.target_type == "KINESIS" }
}
