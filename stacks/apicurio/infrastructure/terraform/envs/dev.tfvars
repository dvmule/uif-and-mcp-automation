env    = "dev"
region = "eu-west-1"

# Surveyed 2026-09-14 from account 283556840805 with read-only calls, the same
# way stacks/beacon/infrastructure/terraform/envs/dev.tfvars was. Every value
# below was observed, not inferred -- the reasoning is inline so the next
# reader does not "tidy" it.
vpc_id = "vpc-0287e62dc748ffab8" # gip-uif-nonprod-vpc-euw1, 10.116.48.0/21

# The only two private-css subnets in the VPC; there is no az3. Both were
# empty at survey time (59 of 59 addresses free in each /26) and both have an
# active NAT default route, so the image pull from quay.io works.
private_subnet_ids = [
  "subnet-086ed7f5de377bb91", # private-css-az1  10.116.54.128/26  eu-west-1a
  "subnet-0b323a493f21f4a87", # private-css-az2  10.116.54.192/26  eu-west-1b
]

alb_internal = true

# Sole security group of vpce-0742f8ea5aa68967e, the account's Secrets Manager
# interface endpoint. Not a dedicated endpoint group -- it is echo-uat-glue-eni,
# the Glue worker group, reused. See the variable's description in
# ../variables.tf before changing this; the endpoint also has an ENI in
# private-glue-az1 only, so credential resolution is a single-AZ dependency.
secretsmanager_endpoint_sg_id = "sg-0b45c554b51638786"

# connect.pearson.com. Two ranges that are not interchangeable: the external
# range is the public address API Gateway sees, the client range is the VPN
# adapter's own address, which only the internal ALBs ever see. Swapping them
# produces rules that can never match. See ../../DEPLOY-DEV.md Phase 1.
# Pearson CloudPC egresses from Azure, not from 159.182.0.0/16, so a CloudPC
# session needs its own entries. These are the three regional EXTERNAL ranges
# (UIFPE-763). CloudPC's internal 10.x subnets are deliberately absent: API
# Gateway and CloudFront only ever see a public source address.
api_allowed_cidrs = [
  "159.182.0.0/16",     # Pearson corporate / connect.pearson.com
  "104.43.130.76/30",   # CloudPC US
  "40.112.91.44/30",    # CloudPC EMEA
  "137.116.140.120/30", # CloudPC APAC
]
vpn_client_cidrs = ["10.122.0.0/15"]

# ── BFF + UI (UIFPE-36) ──
# A real origin, not "". An empty value reaches the BFF as the JSON array [""]
# (bff.tf jsonencodes the split), which pydantic-settings accepts -- so the
# container starts healthily with one allowed origin no browser can send and
# CORS is closed with nothing saying so. Updated 2026-09-15 to the real
# ui_url the first apply published (see ../../DEPLOY-DEV.md Phase 4);
# localhost:5173 stays alongside it for local UI development against the
# deployed BFF.
cors_origins = "https://d23plq69auhgm9.cloudfront.net,http://localhost:5173"
# bff_image_digest is deliberately NOT set here. It is per-build, not
# per-environment: deploy-apicurio-dev.yml resolves it and passes -var. The
# line that used to live here was `bff_image_tag = "latest"`, which is exactly
# the fallback that let an infra-only apply repoint the running service off its
# pinned sha. See the variable's own comment.

# quay.io/apicurio/apicurio-registry:3.3.2 as resolved on 2026-09-19. Bump this
# deliberately, together with the tag in registry_image, never on its own: the
# two disagreeing means the task runs an image the file does not name.
registry_image_digest = "sha256:e9476584866f9cbfd6b11c1fbdd0df64976277e069f54d537572032751a4b6fc"

# Was previously unset, relying on the variable's default in ../variables.tf.
# Set explicitly here so read-tfvar.sh in the release workflow (Task 7) finds
# both registry_image and registry_image_digest in one place -- it reads the
# tfvars file, not Terraform's variable defaults.
registry_image = "quay.io/apicurio/apicurio-registry:3.3.2"

# Delivery Lab / CDC. Empty leaves the surface unconfigured -- the cdc-poller
# that produces its data has no source in this repo, so nothing writes to it
# yet regardless. See ../../ARCHITECTURE.md.
cdc_tables_ssm_parameter = ""

# Opt in explicitly, because the variable now defaults to false. This lets
# `terraform destroy` empty the UI bucket instead of failing on a non-empty
# one, which is what a dev environment that gets torn down repeatedly wants
# and is exactly what prod must not have by default.
frontend_force_destroy = true

# ── Pearson mandatory resource tags ──────────────────────────────────────────
# This stack carries the Pearson 7 (`t_` keys) and not the five lower-case
# keys of capabilities/infrastructure/guidelines.md:28 -- framework-owner
# decision of 2026-09-14. See ../locals.tf for why.
#
# Two of the seven are already settled and are not set here: `t_name` defaults
# to "apicurio" in ../variables.tf, and `t_environment` is derived from `env`
# above (dev -> Dev) in ../locals.tf.
#
# The five below are UNSET ON PURPOSE, and every one is commented out.
# They come from this application's ServiceNow service record, and there is
# nothing in this repository to copy them from. Inventing a value would make
# the stack look compliant while attributing spend to nothing, pointing
# incident response at no service, and stating a data classification nobody
# decided. Terraform refuses the plan and names each missing variable, which
# is the correct behaviour for a tag the standard calls mandatory.
#
# CI DOES NOT READ THEM FROM HERE. They are org facts from the service record,
# not per-branch config, so deploy-apicurio-dev.yml passes them as workflow-
# level TF_VAR_* environment variables sourced from repo variables
# (APICURIO_T_APP_ID, APICURIO_T_COST_CENTRE, APICURIO_T_DCL,
# APICURIO_T_OWNER_INDIVIDUAL, APICURIO_T_RESPONSIBLE_INDIVIDUALS). That keeps
# one place to change them for every branch and environment, and keeps a cost
# centre and two mail addresses out of git history.
#
# So these lines exist for a LOCAL plan, where no repo variable is in scope.
# Uncomment one only when you have the real value from the service record, and
# prefer exporting TF_VAR_t_cost_centre in your shell over committing it -- the
# variable name is case-sensitive and must match the Terraform variable
# exactly (TF_VAR_t_app_id, not TF_VAR_t_AppID; t_AppID is the AWS tag key).
#
# t_app_id                  = "<SVCnnnnn>"          # ServiceNow id, SVC + exactly 5 digits
# t_cost_centre             = "<nn.nnn.nnnnn...>"   # owning cost centre, dot-separated digit groups
# t_dcl                     = "<1-4>"               # data classification level of the data held here
#
# The registered owner of this app in the service record, NOT whoever is
# applying this module. The framework owner's address is known but is not
# assumed to be the registered owner, so it is left unset like the rest:
# t_owner_individual        = "<registered.owner>@pearson.com"
#
# The list actually paged for this stack. .github/CODEOWNERS maps /stacks/ to
# @uif-fde, which names the accountable team but is a GitHub handle, not a
# mail-enabled distribution list -- so the address still has to be confirmed
# rather than derived from it:
# t_responsible_individuals = "<team-list>@pearson.com"
