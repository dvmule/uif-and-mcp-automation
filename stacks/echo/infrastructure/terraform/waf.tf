# Restricts the UI's CloudFront distribution to var.ui_allowed_cidrs, and
# carries Pearson's account-wide "WAF & IP Sanctions" compliance program's
# per-web-ACL requirements (owner: PCM/SRE/Security; due 2026-09-30).
#
# CONFIRMED LIVE IN THIS ACCOUNT before writing any of this, rather than
# inherited from the stack this is modelled on -- on 2026-09-22,
# `aws wafv2 list-rule-groups --scope CLOUDFRONT --region us-east-1` against
# 283556840805 returned `PCMSanctionsRuleGroupCloudFront`, Firewall-Manager-
# seeded. Echo's dev AND uat backends are both that account
# (backends/dev-uif.hcl, backends/uat.hcl), so the program reaches
# uif-echo-dev and uif-echo-uat alike. Prod is 216064088366 and was NOT
# checked -- it is gated out structurally (local.is_uif_stack), so nothing
# here asserts anything about it.
#
# Rule shapes and ordering deliberately mirror stacks/apicurio's waf.tf, a
# `/security-review`-cleared implementation of the same compliance program in
# the same account, rather than being reinvented. Where this diverges from it,
# the divergence is commented.
#
# Rule order matters: WAF short-circuits on the first rule with an action
# (allow/block), so priority is evaluation order, not a tiebreaker.
#   0  pcm-sanctions          enforcing, always first -- nothing, including
#                             the allow-list below, may exempt itself from it
#   10 rate-limit-per-ip      count -- no traffic baseline for this stack yet,
#                             so a limit here would be a guess; count first,
#                             read the metric, then choose one
#   20 AWSManagedRulesAmazonIpReputationList   count
#   30 AWSManagedRulesCommonRuleSet            count
#   40 AWSManagedRulesKnownBadInputsRuleSet    block (the one group the
#                             compliance program requires in block mode
#                             unconditionally; the other three are count
#                             "unless block is cleared through SOC" -- no SOC
#                             clearance exists for this stack)
#   50 AWSManagedRulesSQLiRuleSet              count
#   90 allow-configured-cidrs allow -- LAST, so every enforcing rule above has
#                             already run against the request. It sat at
#                             priority 5 until UIFPE-756, where it
#                             short-circuited the rate limit AND the one group
#                             the program requires in block mode, for any
#                             allowed source -- which made that requirement
#                             decorative for exactly the traffic that reaches
#                             the UI. Anything this rule does not match falls
#                             through to default_action = block.
#
# Deliberately excluded: AWSManagedRulesAdminProtectionRuleSet. Its one rule
# blocks admin-looking URI paths at the edge, BEFORE cloudfront.tf's 403/404
# -> /index.html rewrite runs, so it would break client-side routes. Echo's UI
# is a React SPA with exactly that rewrite (cloudfront.tf's two
# custom_error_response blocks). Not on the program's required list.
#
# Open items, NOT implemented here, both out of scope for a per-stack module:
# Shield Advanced (a paid AWS-Organization-level subscription, not a
# per-resource Terraform change) and Chronicle log shipping (the program's
# required destination for WAF logs; the CloudWatch group below is a real
# destination, not the final one).
#
# EVERYTHING IN THIS FILE IS OFF BY DEFAULT -- see var.enable_ui_waf. The
# count gate is local.ui_waf_enabled, which is that flag AND
# local.is_uif_stack. The data source is gated too, not just the resources:
# wafv2:GetRuleGroup is implicitDeny on the deploy role, so an ungated lookup
# would fail every PLAN in every environment, including the legacy ones this
# change must not touch.

resource "aws_wafv2_ip_set" "ui_allowed" {
  count              = local.ui_waf_enabled ? 1 : 0
  provider           = aws.use1
  name               = "${local.name_prefix}-ui-allowed-ips"
  description        = "Source ranges allowed to load the Echo UI, mirrors var.ui_allowed_cidrs"
  scope              = "CLOUDFRONT"
  ip_address_version = "IPV4"
  addresses          = var.ui_allowed_cidrs

  tags = {
    Project     = local.project_tag
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

# Firewall-Manager-seeded, not declared by this stack -- looked up by name, as
# Apicurio does. Its content and its updates are PCM's, not ours.
data "aws_wafv2_rule_group" "pcm_sanctions" {
  count    = local.ui_waf_enabled ? 1 : 0
  provider = aws.use1
  name     = "PCMSanctionsRuleGroupCloudFront"
  scope    = "CLOUDFRONT"
}

resource "aws_wafv2_web_acl" "ui" {
  count       = local.ui_waf_enabled ? 1 : 0
  provider    = aws.use1
  name        = "${local.name_prefix}-ui"
  description = "Blocks everything except var.ui_allowed_cidrs in front of the UI CloudFront distribution"
  scope       = "CLOUDFRONT"

  # NOT in Apicurio's version, and the reason is specific to turning this on
  # later rather than at stack creation. default_action below is block, so an
  # empty allow-list is not a weak ACL -- it is a total blackhole, including
  # for whoever ran the apply. Failing here costs a plan; failing at apply
  # costs the UI until someone reverts and waits out a CloudFront deploy.
  lifecycle {
    precondition {
      condition     = length(var.ui_allowed_cidrs) > 0
      error_message = "enable_ui_waf is true but ui_allowed_cidrs is empty: this web ACL blocks by default, so an empty allow-list denies every request to the UI. Set ui_allowed_cidrs in this environment's tfvars."
    }
  }

  tags = {
    Project     = local.project_tag
    Environment = var.environment
    ManagedBy   = "terraform"
  }

  default_action {
    block {}
  }

  rule {
    name     = "pcm-sanctions"
    priority = 0

    override_action {
      none {}
    }

    statement {
      rule_group_reference_statement {
        arn = data.aws_wafv2_rule_group.pcm_sanctions[0].arn
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${local.name_prefix}-ui-pcm-sanctions"
      sampled_requests_enabled   = true
    }
  }

  rule {
    name = "allow-configured-cidrs"
    # LAST, not 5: see the order in this file's header. Every enforcing rule
    # must run before a request is allowed through on its source address.
    priority = 90

    action {
      allow {}
    }

    statement {
      ip_set_reference_statement {
        arn = aws_wafv2_ip_set.ui_allowed[0].arn
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${local.name_prefix}-ui-allow-configured-cidrs"
      sampled_requests_enabled   = true
    }
  }

  rule {
    name     = "rate-limit-per-ip"
    priority = 10

    action {
      count {}
    }

    statement {
      rate_based_statement {
        limit              = 500
        aggregate_key_type = "IP"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${local.name_prefix}-ui-rate-limit-per-ip"
      sampled_requests_enabled   = true
    }
  }

  rule {
    name     = "aws-ip-reputation-list"
    priority = 20

    override_action {
      count {}
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesAmazonIpReputationList"
        vendor_name = "AWS"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${local.name_prefix}-ui-aws-ip-reputation-list"
      sampled_requests_enabled   = true
    }
  }

  rule {
    name     = "aws-common-rule-set"
    priority = 30

    override_action {
      count {}
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesCommonRuleSet"
        vendor_name = "AWS"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${local.name_prefix}-ui-aws-common-rule-set"
      sampled_requests_enabled   = true
    }
  }

  rule {
    name     = "aws-known-bad-inputs"
    priority = 40

    override_action {
      none {}
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesKnownBadInputsRuleSet"
        vendor_name = "AWS"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${local.name_prefix}-ui-aws-known-bad-inputs"
      sampled_requests_enabled   = true
    }
  }

  rule {
    name     = "aws-sqli-rule-set"
    priority = 50

    override_action {
      count {}
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesSQLiRuleSet"
        vendor_name = "AWS"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${local.name_prefix}-ui-aws-sqli-rule-set"
      sampled_requests_enabled   = true
    }
  }

  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = "${local.name_prefix}-ui-web-acl"
    sampled_requests_enabled   = true
  }
}

# The "aws-waf-logs-" prefix is not a naming preference -- WAF refuses any
# other prefix for a CloudWatch Logs destination, so this name cannot take
# observability.md's platform-prefixed form. The UIF stem follows the
# mandatory prefix, which is the closest the platform allows.
resource "aws_cloudwatch_log_group" "waf_ui" {
  count             = local.ui_waf_enabled ? 1 : 0
  provider          = aws.use1
  name              = "aws-waf-logs-${local.name_prefix}-ui"
  retention_in_days = 30

  tags = {
    Project     = local.project_tag
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

resource "aws_wafv2_web_acl_logging_configuration" "ui" {
  count                   = local.ui_waf_enabled ? 1 : 0
  provider                = aws.use1
  resource_arn            = aws_wafv2_web_acl.ui[0].arn
  log_destination_configs = [aws_cloudwatch_log_group.waf_ui[0].arn]

  redacted_fields {
    single_header {
      name = "authorization"
    }
  }

  redacted_fields {
    single_header {
      name = "cookie"
    }
  }
}
