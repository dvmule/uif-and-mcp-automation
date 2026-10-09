locals {
  # UIF Resource Naming Convention: uif-{stack}-{env}-{purpose}[-{type}].
  #
  # The earlier comment here cited stacks/beacon/CLAUDE.md as saying new AWS
  # resources DROP the uif- prefix. That was the 2026-08-26 <project>-<env>
  # decision, which the owner instruction of 2026-09-07 superseded — see
  # stacks/beacon/CLAUDE.md "Conventions": "Naming for NEW AWS resources: the
  # UIF Resource Name Lookup is the authority". Nothing in this stack is
  # deployed yet, so every name here is new and takes the current convention.
  #
  # The `apicurio` stack token is sanctioned: added to the `{stack}` slot in
  # architecture-design/standards/rules/naming/00-core.md by owner decision on
  # 2026-09-08. Every other name in this stack derives from this one.
  name = "uif-apicurio-${var.env}"

  # No Secrets Manager secret is owned by this stack any more. The DB
  # credential is RDS-managed (rds.tf), and RDS names its own secret
  # rds!db-<resource-id>, which cannot be set. The slash-style
  # uif/{stack}/{env}/{system-type}/{system} form in identity-secrets.md
  # applies the moment this stack does own a secret -- Keeper Universal
  # Secret Sync matches vault records to AWS secrets by name, which is why
  # that form exists -- so it is recorded here rather than removed with the
  # local it used to build.

  # null means "non-prod", resolved here rather than in ecs.tf so the
  # env-dependent default is stated once. See the variable's description.
  registry_deletion_enabled = coalesce(var.registry_deletion_enabled, var.env != "prod")

  # ECS must pull by digest, so registry_image's tag is stripped and the digest
  # appended: quay.io/apicurio/apicurio-registry:3.3.2 becomes
  # quay.io/apicurio/apicurio-registry@sha256:...
  #
  # The regex removes a trailing :<tag> only when that segment contains no "/",
  # so a registry host carrying a port (host:5000/image:tag) keeps its port and
  # loses only the tag.
  registry_image_ref = "${replace(var.registry_image, "/:[^:/]+$/", "")}@${var.registry_image_digest}"

  # split(",") keeps surrounding whitespace, so "a.com, b.com" yields a second
  # entry of " b.com" that no Origin header can ever equal -- CORS fails for
  # every origin after the first, and nothing reports it. Trimmed and compacted
  # once here, because apigw.tf and bff.tf both consume it and drifting between
  # two copies of the parsing is the same bug twice.
  cors_origins = compact([for o in split(",", var.cors_origins) : trimspace(o)])

  # t_environment takes the Pearson vocabulary (Dev/STG/Prod), which is not
  # this module's `env` vocabulary (dev/uat/prod), so it is mapped explicitly
  # rather than derived with title() or upper(). A string function looks like
  # it works because `prod` -> `Prod` is correct, but `uat` -> `Uat` is not a
  # value Pearson recognises: one env in three would carry a silently invalid
  # mandatory tag, and nothing would report it.
  #
  # JUDGEMENT CALL, not something the standard states: `uat` -> `STG`. The
  # Pearson list offers exactly Dev, STG and Prod -- there is no UAT value in
  # it -- so this stack's uat environment is tagged as staging, that being the
  # only non-dev non-prod value available. Recorded here rather than in a
  # tfvars comment because a reader who finds `STG` on a UAT resource will
  # otherwise assume Pearson sanctioned the mapping. Revisit if a UAT value is
  # added to the standard.
  t_environment = {
    dev  = "Dev"
    uat  = "STG"
    prod = "Prod"
  }[var.env]

  # The Pearson mandatory resource tags -- the seven `t_` keys, and only those.
  #
  # This is a deliberate divergence from this repository's own standard.
  # capabilities/infrastructure/guidelines.md:28 mandates a different five
  # keys on every cloud resource (domain, env, owner, cost-center,
  # managed-by=terraform), repeats them at :88 and carries them as a
  # definition-of-done checkbox at :96. Framework-owner decision of 2026-09-14
  # is "Pearson 7 only" for this stack, and the repo's five are superseded
  # here.
  #
  # The Pearson set wins because it is the vocabulary the enterprise actually
  # bills, classifies and pages against outside this repository: t_AppID ties
  # a resource to its ServiceNow service record, t_cost_centre to a real cost
  # centre, t_dcl to a stated data classification level, and the two people
  # keys to a named owner and an on-call distribution list. The repo's five
  # are a local convention with no consumer beyond this repo, and carrying
  # both sets would put two tags on every resource for each single idea --
  # `env` and `t_environment`, `owner` and `t_owner_individual` -- which is
  # the four-spellings-of-one-concept problem the repo standard was written to
  # solve, reintroduced.
  #
  # Spelling is exact and case matters, because AWS tag keys are
  # case-sensitive. The AWS key is `t_AppID` while the Terraform variable is
  # `t_app_id` (HCL convention); the other six keys match their variable
  # names. t_environment is derived above, not supplied.
  #
  # `Project` is not a Pearson tag and is kept anyway, because here it is
  # load-bearing beyond reporting. The ModifyOwnSecurityGroupRules statement
  # in capabilities/infrastructure/uif-apicurio-dev-deploy.policy.json allows
  # ec2:ModifySecurityGroupRules on security-group/* only where
  # aws:ResourceTag/Project = uif-apicurio. Dropping or renaming this key
  # turns every security-group rule change into an opaque 403 part-way
  # through an apply.
  #
  # `component` is NOT here on purpose. It is the one key that is not constant
  # across this stack -- see the per-resource tags. It used to be in
  # default_tags as a hardcoded "registry", which tagged the BFF, the UI
  # bucket, the CloudFront distribution and the public API as part of the
  # registry: wrong on 31 of the 52 taggable resources, and wrong in the
  # direction that makes a cost split by component silently misleading.
  default_tags = {
    t_name                    = var.t_name
    t_AppID                   = var.t_app_id
    t_environment             = local.t_environment
    t_cost_centre             = var.t_cost_centre
    t_dcl                     = var.t_dcl
    t_owner_individual        = var.t_owner_individual
    t_responsible_individuals = var.t_responsible_individuals
    Project                   = "uif-apicurio"
  }
}
