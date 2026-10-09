# ── Event planes for the config-only domains ────────────────────────────────
#
# A domain's eventing plane (bus uif-event-{env}-{domain}, delivery DLQ, the
# shared recorder's mapping on that DLQ, the channel-gating mapping upload)
# is the event-domain module. Product instantiates it from its OWN Terraform
# root (domains/product/infrastructure/terraform/product_events.tf). Every
# other gold domain has NO Terraform root by standard
# (capabilities/data/gold-control-plane.md §2: a second apply would race one
# state lock; state ownership is UIFPE-116) and onboarded config-only
# (price UIFPE-401, then stock and program) riding the product bus as a
# documented interim. This file is where those domains' planes live.
#
# The stack's invariant holds: nothing here names a domain. The set is
# DERIVED from the config-as-code the domains already commit - a domain that
# ships domains/{d}/src/events/config/{d}.pipeline-config.{env}.json gets its
# plane on the next apply, and product (whose config is Terraform, not JSON)
# is excluded by the same rule. Onboarding a domain still edits nothing
# under stacks/event.
#
# Flip day per domain (its src/events/README.md "Deliberate interim
# choices"): the module instance lands HERE, the same change flips the
# domain's pipeline-config event_bus_name to its own bus, then its
# subscriptions are re-provisioned (SUSPENDED -> DELETING -> re-seed PENDING)
# so their rules move off the product bus. Existing rules keep delivering
# from the bus recorded in their rule_arn until then.

locals {
  repo_root = "${path.module}/../../../.."

  # domains/{d}/src/events/config/{d}.pipeline-config.{env}.json -> {d}
  config_only_domains = toset([
    for f in fileset(local.repo_root, "domains/*/src/events/config/*.pipeline-config.${var.environment}.json") :
    regex("^domains/([^/]+)/", f)[0]
  ])
}

module "domain_events" {
  for_each = local.config_only_domains
  source   = "./modules/event-domain"

  domain           = each.key
  environment      = var.environment
  artifacts_bucket = var.gold_artifacts_bucket
  # The registered gold mapping IS the channel-gating rule; the module
  # uploads it to event-pipeline/domains/{d}/{d}.mapping.json, the key the
  # domain's pipeline-config already names (adopting the hand-uploaded copy).
  mapping_source = "${local.repo_root}/domains/${each.key}/src/schemas/mapping/${each.key}/v1/${each.key}.mapping.json"
  # No bootstrap seeds: these domains' subscribers are explicit
  # config-as-code rows (src/events/config/subscriptions/), never defaults.
  subscription_seeds_source = ""
  # Same gate as product's instance: the deploy role has no
  # cloudwatch:PutMetricAlarm yet.
  enable_dlq_alarm = false
  tags             = local.common_tags

  # The module attaches the DLQ to the recorder by a data-source lookup of
  # its conventional name; in this state the recorder is a resource, so the
  # lookup must run after it exists.
  depends_on = [aws_lambda_function.dlq_recorder]
}

output "domain_event_buses" {
  description = "Event bus per config-only domain (the value each domain's pipeline-config event_bus_name flips to)."
  value       = { for d, m in module.domain_events : d => m.bus_name }
}
