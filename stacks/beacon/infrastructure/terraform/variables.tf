variable "env" {
  description = "Environment name (dev | staging | prod)"
  type        = string
}

variable "region" {
  description = "Primary AWS region (RELAY shared services: eu-west-1)"
  type        = string
  default     = "eu-west-1"
}

variable "name_prefix" {
  description = "Resource name prefix"
  type        = string
  default     = "uif-beacon"
}

# -- networking (supplied by the platform team's shared-services VPC) --------
variable "vpc_id" {
  description = "VPC for the Fargate service"
  type        = string
}

variable "private_subnet_ids" {
  description = "Private subnets for Fargate tasks"
  type        = list(string)
}

# The API service variables (api_cpu/memory/desired_count/image_tag,
# assign_public_ip, alb_internal, alb_subnet_ids, api_origin_protocol) were
# deleted with the ECS service and ALB at UIFPE-226's probation exit.

# UIFPE-441: the Athena workgroup the wizard's live filter preview runs on.
# The account's shared `primary` — every Athena user in the repo shares it,
# and the beacon deploy role has no athena:* to create one of our own.
# Results still land in OUR data bucket (preview/): the client-side
# OutputLocation holds because primary does not enforce a result location
# (EnforceWorkGroupConfiguration=false, verified in dev 2026-08-31). Per-query
# scan caps are a workgroup setting we therefore cannot set.
variable "athena_workgroup" {
  description = "Athena workgroup for the wizard's live filter preview (UIFPE-441)"
  type        = string
  default     = "primary"
}


# UIFPE-487: the RELAY shell's login secret. Beacon's API verifies shell
# session tokens against its token_secret — one login covers the shell and
# Beacon. The shell (stacks/echo) owns the secret and the users map; Beacon
# only reads it. Empty would disable auth, so the default is the live name.
variable "shell_login_secret_name" {
  description = "Secrets Manager name of the RELAY shell login secret (token_secret is used to verify session tokens)"
  type        = string
  default     = "uif/echo/dev/ui/login"
}
