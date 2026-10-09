variable "env" {
  description = <<-DESC
    Deploy environment. Closed vocabulary, not free text: the same three
    tokens the `env` slot sanctions in
    architecture-design/standards/rules/naming/00-core.md:42 and
    validate_naming.py enforces in ENVS. Every name in this module derives
    from it via local.name, so a value outside the set would also produce
    names the naming guard denies.
  DESC
  type        = string

  validation {
    condition     = contains(["dev", "uat", "prod"], var.env)
    error_message = "env must be exactly one of dev, uat or prod. Four safety switches are written as 'var.env == prod' or '!= prod' -- deletion_protection, the final snapshot, backup retention, and the registry DELETE API. An unrecognised value does not fail, it silently takes the non-prod branch and disarms all four at once, so this vocabulary is checked rather than assumed."
  }
}

variable "region" {
  description = "AWS region."
  type        = string
}

# -- networking (supplied by the platform team's shared-services VPC —
#    same convention as stacks/beacon/infrastructure/terraform/variables.tf,
#    which also takes an existing VPC as input rather than creating one) --
variable "vpc_id" {
  description = "VPC for the Fargate service and the RDS instance."
  type        = string
}

variable "private_subnet_ids" {
  description = "Private subnets for Fargate tasks and the RDS instance."
  type        = list(string)
}

variable "alb_internal" {
  description = "Whether the ALB is internal-only. Apicurio holds internal schema metadata, not a public surface — defaults true."
  type        = bool
  default     = true
}

variable "alb_subnet_ids" {
  description = "Subnets for the ALB. Falls back to private_subnet_ids when empty (mirrors beacon's convention)."
  type        = list(string)
  default     = []
}

variable "assign_public_ip" {
  type    = bool
  default = false
}

# -- Fargate sizing --
variable "registry_cpu" {
  description = "Fargate task CPU units. Apicurio Registry is a JVM (Quarkus) service."
  type        = number
  default     = 1024
}

variable "registry_memory" {
  description = "Fargate task memory (MiB)."
  type        = number
  default     = 2048
}

variable "registry_desired_count" {
  description = <<-DESC
    Registry task count. 2, not 1, and deliberately unlike bff_desired_count.

    The registry is stateless -- its artifacts live in RDS Postgres -- so a
    second task behind the ALB serves identical data rather than divergent
    data. At 1, every deploy and every task replacement is a full outage of
    the registry for the duration, which is the thing a rolling deploy exists
    to avoid.

    The BFF cannot follow until its compliance cache and Delivery Lab
    watermark store move out of process memory; see bff_desired_count.
  DESC
  type        = number
  default     = 2
}

variable "registry_image" {
  description = <<-DESC
    Apicurio Registry container image. Pin to a specific version, never :latest —
    update deliberately per environment.

    Must be the `apicurio/apicurio-registry` image, not `apicurio-registry-sql`.
    The `-sql` variant was a v2-era split and has no 3.x tags at all (its newest
    is 2.6.13.Final); v3 consolidated to a single image whose storage backend is
    selected by APICURIO_STORAGE_KIND in ecs.tf. The previous default here,
    `apicurio-registry-sql:3.0.6`, was an image that does not exist — a `plan`
    would have succeeded and the ECS task would then have failed to pull.

    Qualified to quay.io deliberately, though Docker Hub does carry a 3.3.2 tag
    (both were checked). Two reasons: quay.io is where Apicurio publishes, and
    it is the registry ../../docker-compose.yml uses, so the image the ECS task
    pulls is byte-identical to the one qe/tests/integration/ was proven
    against. An unqualified name resolves to Docker Hub, whose anonymous pull
    limit is per source IP — shared across everything behind this account's NAT
    — so a task pull can fail for reasons that have nothing to do with this
    stack.
  DESC
  type        = string
  default     = "quay.io/apicurio/apicurio-registry:3.3.2"
}

variable "registry_image_digest" {
  description = <<-DESC
    Digest of the Apicurio Registry image to run, as sha256:<64 hex>.

    `registry_image` above records WHAT WE MEAN -- 3.3.2, from quay.io, and the
    reasons for both. This records what ECS actually pulls. The two are not
    redundant: a tag is mutable, so upstream can repoint 3.3.2 at any time, and
    an environment pinned only to the tag cannot answer "which image is
    running" -- nor guarantee that the image QE proved is the image the next
    task pulls.

    NO DEFAULT, for the same reason bff_image_digest has none: an environment
    should have to say which artifact it runs, and be unable to answer
    "whatever upstream is serving today".

    Resolve it with:
      curl -sSI \
        -H 'Accept: application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json' \
        https://quay.io/v2/apicurio/apicurio-registry/manifests/3.3.2 \
        | grep -i docker-content-digest
  DESC
  type        = string

  validation {
    condition     = can(regex("^sha256:[0-9a-f]{64}$", var.registry_image_digest))
    error_message = "registry_image_digest must be a full digest of the form sha256:<64 lowercase hex chars>, not a tag."
  }
}

# -- RDS sizing --
variable "db_instance_class" {
  type    = string
  default = "db.t4g.micro"
}

variable "db_allocated_storage" {
  description = "Allocated storage, GiB."
  type        = number
  default     = 20
}

# ── BFF ───────────────────────────────────────────────────────────────────────
# Added when the governance BFF and UI were folded into this stack (UIFPE-36).

variable "bff_port" {
  description = "Port the BFF (uvicorn) listens on."
  type        = number
  default     = 8000
}

variable "bff_cpu" {
  description = "Fargate task CPU units for the BFF. Python/async — far lighter than the JVM registry."
  type        = number
  default     = 512
}

variable "bff_memory" {
  description = "Fargate task memory (MiB) for the BFF."
  type        = number
  default     = 1024
}

variable "bff_desired_count" {
  description = <<-DESC
    BFF task count. Keep at 1 until the compliance cache and the Delivery Lab
    watermark store move out of process memory — two tasks behind the ALB would
    serve divergent compliance and CDC state depending on which one answered.
    See ../../ARCHITECTURE.md "Known gaps".
  DESC
  type        = number
  default     = 1
}

variable "bff_image_digest" {
  description = <<-DESC
    Digest of the BFF image to run, as sha256:<64 hex>. Built from
    ../../src/Dockerfile and pushed by deploy-apicurio-dev.yml, which resolves
    the digest and passes it here.

    NO DEFAULT, deliberately. The variable it replaced defaulted to the tag
    "latest", which meant an apply that did not build an image silently
    repointed the running task definition from a pinned sha back to a moving
    tag -- a real change presented as a no-op. An environment should have to
    say which artifact it runs, and be unable to answer "whatever is newest".

    Getting one by hand for a local apply:
      aws ecr describe-images --repository-name uif-apicurio-dev-bff-ecr-repo \
        --image-ids imageTag=<commit-sha> \
        --query 'imageDetails[0].imageDigest' --output text
  DESC
  type        = string

  validation {
    condition     = can(regex("^sha256:[0-9a-f]{64}$", var.bff_image_digest))
    error_message = "bff_image_digest must be a full digest of the form sha256:<64 hex chars>, not a tag. Resolve it with `aws ecr describe-images --image-ids imageTag=<sha> --query 'imageDetails[0].imageDigest'`."
  }
}

variable "bff_log_level" {
  type    = string
  default = "INFO"
}

variable "cmdb_base_url" {
  description = <<-DESC
    CMDB endpoint. `mock://cmdb` selects the mock client, which is the only
    implementation that exists — no ServiceNow client has been written, so every
    ownership answer the service returns is fixture data.
  DESC
  type        = string
  default     = "mock://cmdb"
}

variable "cors_origins" {
  description = <<-DESC
    Comma-separated browser-facing origins allowed to call the BFF API -- this
    stack's own CloudFront domain, plus any local dev origin. Two consumers
    depend on the comma-separated contract: apigw.tf splits it for its CORS
    config, and bff.tf splits + JSON-encodes it for the BFF's own Settings.

    Required on purpose, with no default: an empty value survives bff.tf's
    jsonencode(split(",", ...)) as the JSON array [""], which pydantic-settings
    accepts -- so the BFF starts healthily with one allowed origin no browser
    can send, closing CORS silently instead of failing loudly. Every
    environment must set a real value.
  DESC
  type        = string

  validation {
    condition     = length(compact([for o in split(",", var.cors_origins) : trimspace(o)])) > 0
    error_message = "cors_origins must name at least one non-blank origin. A blank or whitespace-only value leaves the BFF starting healthily with no allowed origin, so every browser request fails CORS and no signal says why."
  }

  validation {
    condition = alltrue([
      for o in compact([for s in split(",", var.cors_origins) : trimspace(s)]) :
      can(regex("^https?://[^/]+$", o))
    ])
    error_message = "Each cors_origins entry must be a bare scheme://host[:port] with no path and no trailing slash. A browser Origin header never carries either, so 'https://example.com/' is compared against 'https://example.com' and matches nothing -- CORS then fails for exactly the origin someone took the trouble to configure."
  }
}

variable "cdc_tables_ssm_parameter" {
  description = "SSM parameter holding the CDC enrollment config. Must begin with '/'. Empty disables the parameter and leaves the Delivery Lab surface unconfigured."
  type        = string
  default     = ""

  validation {
    condition     = var.cdc_tables_ssm_parameter == "" || startswith(var.cdc_tables_ssm_parameter, "/")
    error_message = "cdc_tables_ssm_parameter must begin with '/'. iam.tf builds the grant by concatenating the name onto ':parameter', so 'cdc/tables' produces the ARN suffix 'parametercdc/tables' instead of 'parameter/cdc/tables'. That ARN is well-formed enough to apply and matches nothing, so the failure arrives as an AccessDenied in the BFF at runtime rather than as an error at plan."
  }
}

variable "cdc_enrollment_json" {
  description = "Seed value for the CDC enrollment config. Terraform writes it once, then ignores changes — operators edit it through the BFF at runtime."
  type        = string
  default     = "{}"
}

# ── UI ────────────────────────────────────────────────────────────────────────

variable "frontend_price_class" {
  description = "CloudFront price class. PriceClass_100 is EU + North America."
  type        = string
  default     = "PriceClass_100"
}

variable "frontend_force_destroy" {
  description = <<-DESC
    Allow `terraform destroy` to empty the UI bucket before deleting it.
    Defaults to false: a default that makes data easier to lose is the wrong
    way round, because the environment that most needs the guard (prod) is the
    one most likely to be applied without every variable reviewed. dev opts in
    explicitly in its own tfvars.
  DESC
  type        = bool
  default     = false
}

# ── Network reachability (UIFPE-36 dev deploy) ────────────────────────────────

variable "secretsmanager_endpoint_sg_id" {
  description = <<-DESC
    Security group of the account's Secrets Manager interface VPC endpoint.
    Empty means no rule is created, which is what a sandbox or a VPC without
    the endpoint wants.

    Why this exists: the endpoint in gip-uif-nonprod-vpc-euw1 has private DNS
    enabled, so secretsmanager.eu-west-1.amazonaws.com resolves VPC-wide to
    the endpoint's ENI. A task cannot fall back to the public endpoint over
    NAT even though a NAT route exists -- DNS never hands it a public address.
    The registry task's execution role fetches the DB credential at task
    start, so without an ingress seat on that endpoint the task never starts,
    and the symptom is a fetch timeout rather than a denial.

    Surveyed 2026-09-14: vpce-0742f8ea5aa68967e, sole security group
    sg-0b45c554b51638786. Two things about it that are not obvious and are
    the reason this is a variable rather than a hardcoded id:
      - That group is not a dedicated endpoint group. It is echo-uat-glue-eni,
        the Glue worker group, reused as the endpoint's group. Adding a rule
        to it is a change to a group another workload owns. Beacon set the
        precedent (aws_vpc_security_group_ingress_rule.deliver_secretsmanager_access
        in stacks/beacon/infrastructure/terraform/lambda.tf), and the rule is
        additive, so it grants without revoking anything.
      - The endpoint has an ENI in one subnet only, private-glue-az1. A task
        in private-css-az2 reaches it cross-AZ. That routes, and it is a
        single-AZ dependency for credential resolution.
  DESC
  type        = string
  default     = ""
}

variable "vpn_client_cidrs" {
  description = <<-DESC
    Client address ranges of connect.pearson.com, allowed inbound on the two
    internal ALBs. This is the range the VPN adapter itself presents, which is
    what an internal ALB sees -- not the external range, which only API
    Gateway sees. Putting the external range here, or this range in
    api_allowed_cidrs, produces a rule that can never match.

    Empty means the ALBs accept in-VPC traffic only, and an FDE on the VPN
    cannot reach the registry or the BFF directly.
  DESC
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for c in var.vpn_client_cidrs : can(cidrnetmask(c))])
    error_message = "Every entry must be a valid IPv4 CIDR block. A hostname or a bare address would silently widen or void the rule."
  }
}

variable "api_allowed_cidrs" {
  description = <<-DESC
    Source ranges the API Gateway authorizer accepts, as the public internet
    sees them -- connect.pearson.com's external range. This is the only
    authentication in front of the BFF until Entra federation lands, so it is
    a stop-gap, and ../../DEPLOY-DEV.md says so where it is set.

    Empty means the authorizer rejects every source address, which fails
    closed. That is the correct default for a variable whose whole purpose is
    to restrict.
  DESC
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for c in var.api_allowed_cidrs : can(cidrnetmask(c))])
    error_message = "Every entry must be a valid IPv4 CIDR block; a wildcard or a hostname here would reopen the only control in front of the BFF."
  }
}

variable "registry_deletion_enabled" {
  description = <<-DESC
    Whether the registry accepts DELETE on artifacts, versions and groups.

    Apicurio 3.3.2 ships apicurio.rest.artifact.deletion.enabled=false, so
    every DELETE returns 405 unless these are set -- verified against the
    image. With deletion off, no cleanup path works and the seed scripts
    cannot be re-run idempotently, which is what Phase 5b of
    ../../DEPLOY-DEV.md needs in order to populate dev at all.

    Defaults to non-prod only. A governance registry that permits deleting a
    registered contract is not what prod should be; dev has to be able to
    reset.
  DESC
  type        = bool
  default     = null
}

# ── Pearson mandatory resource tags ──────────────────────────────────────────
# The seven `t_` keys every AWS resource must carry. Framework-owner decision
# of 2026-09-14 is "Pearson 7 only" for this stack, superseding the five
# lower-case keys that capabilities/infrastructure/guidelines.md:28 mandates.
# See local.default_tags for the full reasoning and for why `Project` survives
# alongside them.
#
# Six variables, not seven: t_environment is derived from var.env in
# locals.tf, because its vocabulary (Dev/STG/Prod) is not this module's
# (dev/uat/prod) and hand-typing it per env is how one env ends up mislabelled.
#
# Only t_name carries a default. The other five have none, deliberately: they
# come from the app's ServiceNow service record and cannot be derived from
# anything in this repository. A mandatory tag with a default is how a whole
# estate ends up correctly tagged with the wrong value -- a wrong
# t_cost_centre misbills another team, and a wrong t_dcl misstates the data
# classification of everything this stack holds. So the failure is loud:
# Terraform refuses the plan and names the variable that is missing.

variable "t_name" {
  description = <<-DESC
    Value for the mandatory `t_name` tag: the short application name, lower
    case.

    This is the one Pearson tag with a default, because it is derivable and
    unambiguous -- the stack is `apicurio` everywhere else in this module,
    including local.name and the `{stack}` slot sanctioned in
    architecture-design/standards/rules/naming/00-core.md. It is not a value
    only the service record knows.
  DESC
  type        = string
  default     = "apicurio"

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]*$", var.t_name))
    error_message = "t_name is the short application name in Cost Explorer and the estate tag inventory, and must be lower case: letters, digits and hyphens only, starting with a letter or digit. AWS tag keys and values are case-sensitive, so `Apicurio` and `apicurio` become two separate applications in every report that groups by this tag."
  }
}

variable "t_app_id" {
  description = <<-DESC
    Value for the mandatory `t_AppID` tag: this application's ServiceNow id.

    Note the deliberate spelling difference -- the AWS tag key is `t_AppID`,
    this variable is `t_app_id` to follow HCL convention. locals.tf maps one
    to the other.

    No default. Take it from the app's ServiceNow service record; do not
    guess. This tag is what links an AWS resource back to the service record
    that says who runs it and what it is for, so a wrong value points
    incident response at the wrong application.
  DESC
  type        = string

  validation {
    condition     = can(regex("^SVC[0-9]{5}$", var.t_app_id))
    error_message = "t_AppID must be the literal prefix SVC followed by exactly 5 digits, e.g. SVC12345, taken from this app's ServiceNow service record. It is the join key between this AWS resource and that record: a malformed or invented id resolves to no service, so anyone triaging an incident from the resource tag finds nothing, and the estate compliance scan counts the resource as untagged."
  }
}

variable "t_cost_centre" {
  description = <<-DESC
    Value for the mandatory `t_cost_centre` tag: the cost centre that owns
    this stack's spend.

    No default, and no value invented: there is no cost-centre code anywhere
    in this repository to copy, so it has to come from the app's service
    record or whoever owns the budget. Supply it in envs/<env>.tfvars.
  DESC
  type        = string

  validation {
    # Dot-separated digit groups, two or more, no empty segment and no leading
    # or trailing dot. Deliberately NOT a fixed segment count.
    #
    # This rule used to demand nnnn.nnnn.nnnnnn -- 4, then 4, then 6 digits --
    # on the authority of the Pearson tag reference. The real code for this app
    # is 10.365.74172.40313.0000.35: six groups of 2/3/5/5/4/2. The documented
    # shape is simply not the shape in use, so the stricter rule refused the
    # only correct value there is, which is the worst outcome for a guard --
    # it blocks the true value and teaches the reader a false format.
    #
    # Only ONE real code has been observed, so no segment count or per-group
    # width is asserted from it. Inferring "six groups of 2/3/5/5/4/2" from a
    # single sample would just relocate the same mistake. What is checked is
    # what a typo actually looks like: blank, letters, whitespace, a doubled
    # or trailing dot.
    condition     = can(regex("^[0-9]+(\\.[0-9]+)+$", var.t_cost_centre))
    error_message = "t_cost_centre must be dot-separated digit groups, e.g. 10.365.74172.40313.0000.35, taken from this app's service record -- no letters, spaces, empty segments or trailing dot. It is what makes this stack's spend attributable, and the failure mode is silent: a wrong but well-formed code does not break anything, it quietly misbills another team every month until someone reconciles a budget, and a malformed one leaves the spend attributed to nothing. The segment COUNT is not checked, because only one real code has been seen and a rule inferred from one sample would refuse the next legitimate one -- as the previous nnnn.nnnn.nnnnnn rule did, which rejected the real value."
  }
}

variable "t_dcl" {
  description = <<-DESC
    Value for the mandatory `t_dcl` tag: the data classification level, 1-4,
    of the data this stack holds.

    No default. This is a classification decision recorded in the app's
    service record, not something a Terraform module may assume on the
    owner's behalf.
  DESC
  type        = string

  validation {
    condition     = contains(["1", "2", "3", "4"], var.t_dcl)
    error_message = "t_dcl must be exactly one of \"1\", \"2\", \"3\" or \"4\" -- the data classification level from this app's service record, as a string. It states the sensitivity of everything this stack holds, and the controls and audit scope applied to these resources follow from it: understating it exempts protected data from controls it is required to have, and overstating it pulls the stack into an audit and handling regime it does not need."
  }
}

variable "t_owner_individual" {
  description = <<-DESC
    Value for the mandatory `t_owner_individual` tag: the named owner's
    Pearson email address, as registered for this app.

    No default, and specifically not the address of whoever happens to be
    applying this module. Unlike the repo standard's team-level `owner` tag
    that this replaces, the Pearson tag asks for a named individual, and the
    name has to be the one on the app's service record -- so it is supplied,
    not inferred.
  DESC
  type        = string

  validation {
    # pearson.com OR any subdomain of it. The bare-domain form was too narrow:
    # the sibling t_responsible_individuals rule, written identically, refused
    # the real distribution list uifbaa@grp.pearson.com. Corrected here too
    # rather than waiting for an owner on a subdomain to hit the same wall.
    condition     = can(regex("^[a-zA-Z0-9._%+-]+@([a-zA-Z0-9-]+\\.)*pearson\\.com$", var.t_owner_individual))
    error_message = "t_owner_individual must be a single Pearson email address, e.g. first.last@pearson.com, naming the individual registered as this app's owner. It is the address escalation reaches when this stack is the cause of something: an address that does not resolve, or one belonging to someone who is not the registered owner, means the escalation is silently delivered to no one accountable."
  }
}

variable "t_responsible_individuals" {
  description = <<-DESC
    Value for the mandatory `t_responsible_individuals` tag: the on-call or
    team distribution list responsible for this stack day to day.

    A distribution list rather than a person, so it survives someone changing
    team. Accepts one address or a comma-separated list, since the tag name is
    plural and the standard describes it as an on-call or team list.

    No default -- the list that is actually paged for this stack is an
    operational fact about the app, held in its service record.
  DESC
  type        = string

  validation {
    # One address, or a comma-separated list, each at pearson.com or any
    # subdomain of it. The bare-domain version refused uifbaa@grp.pearson.com
    # -- a real group list, and exactly the kind of value this tag is FOR --
    # because Pearson puts distribution lists on grp.pearson.com. A rule that
    # rejects the correct value pushes people toward a named individual, which
    # is the opposite of what the tag is for.
    condition     = can(regex("^[a-zA-Z0-9._%+-]+@([a-zA-Z0-9-]+\\.)*pearson\\.com(,[ ]*[a-zA-Z0-9._%+-]+@([a-zA-Z0-9-]+\\.)*pearson\\.com)*$", var.t_responsible_individuals))
    error_message = "t_responsible_individuals must be one Pearson email address, or several separated by commas, e.g. uif-fde@pearson.com -- the on-call or team distribution list responsible for this stack. This is the tag an alert or an abuse report is routed to, so a wrong or unresolvable list means nobody is paged and the failure looks like silence rather than an error."
  }
}
