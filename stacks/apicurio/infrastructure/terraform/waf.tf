# Restricts the UI's CloudFront distribution to the same source ranges as
# the API Gateway authorizer, and carries Pearson's account-wide "WAF & IP
# Sanctions" compliance program's per-web-ACL requirements (owner: PCM/SRE/
# Security; due 2026-09-30). Confirmed live in THIS account before writing
# any of this -- `aws wafv2 list-rule-groups --scope CLOUDFRONT` returns
# `PCMSanctionsRuleGroupCloudFront`, Firewall-Manager-seeded, so the program
# applies here, not only to the reference implementation this was modelled
# on. Rule shapes and ordering below intentionally mirror that already-
# reviewed reference (a `/security-review`-cleared pattern for the same
# compliance program), not reinvented independently.
#
# CLOUDFRONT-scope WAFv2 resources -- and, empirically, their logging
# configuration and destination log group -- must be created in us-east-1
# regardless of where the distribution's origin lives (providers.tf's
# aws.use1 alias).
#
# Rule order matters: WAF short-circuits on the first rule with an action
# (allow/block), so priority is evaluation order, not just a tiebreaker.
#   0  pcm-sanctions          enforcing, always first -- nothing, including
#                             the allow-list below, may exempt itself from it
#   5  allow-configured-cidrs allow -- exempts the Pearson range from the
#                             rate limit and the four managed groups below,
#                             never from sanctions (it already ran)
#   10 rate-limit-per-ip      count -- no real traffic baseline for this
#                             stack yet; matches the reference's own
#                             "count pending real traffic data" starting
#                             posture rather than guessing a safe limit
#   20 AWSManagedRulesAmazonIpReputationList   count
#   30 AWSManagedRulesCommonRuleSet            count
#   40 AWSManagedRulesKnownBadInputsRuleSet    block (the one group the
#                             compliance program requires in block mode
#                             unconditionally; the other three are count
#                             "unless block is cleared through SOC" -- no
#                             SOC clearance exists for this stack)
#   50 AWSManagedRulesSQLiRuleSet              count
#
# Deliberately excluded: AWSManagedRulesAdminProtectionRuleSet. Its one rule
# blocks admin-looking URI paths at the edge, before CloudFront's SPA
# rewrite to /index.html runs, which would break client-side routes like
# /schemas/admin-something. Not on the compliance program's required list.
#
# Open items, NOT implemented here (both out of scope for a per-stack
# Terraform module, both recorded in DEPLOY-DEV.md rather than silently
# dropped): Shield Advanced (a paid AWS Organization-level subscription,
# not a per-resource Terraform change) and Chronicle log shipping (the
# compliance program's required destination for WAF logs; CloudWatch here
# is a real destination, not the final one).

resource "aws_wafv2_ip_set" "ui_allowed" {
  provider           = aws.use1
  name               = "${local.name}-ui-allowed-ips"
  description        = "Source ranges allowed to load the Apicurio UI, mirrors var.api_allowed_cidrs"
  scope              = "CLOUDFRONT"
  ip_address_version = "IPV4"
  addresses          = var.api_allowed_cidrs

  tags = { component = "ui" }
}

# Firewall-Manager-seeded, not declared by this stack -- looked up by name,
# same as the reference implementation does. Its content and updates are
# PCM's, not ours.
data "aws_wafv2_rule_group" "pcm_sanctions" {
  provider = aws.use1
  name     = "PCMSanctionsRuleGroupCloudFront"
  scope    = "CLOUDFRONT"
}

resource "aws_wafv2_web_acl" "ui" {
  provider    = aws.use1
  name        = "${local.name}-ui"
  description = "Blocks everything except var.api_allowed_cidrs in front of the UI CloudFront distribution"
  scope       = "CLOUDFRONT"

  tags = { component = "ui" }

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
        arn = data.aws_wafv2_rule_group.pcm_sanctions.arn
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${local.name}-ui-pcm-sanctions"
      sampled_requests_enabled   = true
    }
  }

  rule {
    name     = "allow-configured-cidrs"
    priority = 5

    action {
      allow {}
    }

    statement {
      ip_set_reference_statement {
        arn = aws_wafv2_ip_set.ui_allowed.arn
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${local.name}-ui-allow-configured-cidrs"
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
      metric_name                = "${local.name}-ui-rate-limit-per-ip"
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
      metric_name                = "${local.name}-ui-aws-ip-reputation-list"
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
      metric_name                = "${local.name}-ui-aws-common-rule-set"
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
      metric_name                = "${local.name}-ui-aws-known-bad-inputs"
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
      metric_name                = "${local.name}-ui-aws-sqli-rule-set"
      sampled_requests_enabled   = true
    }
  }

  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = "${local.name}-ui-web-acl"
    sampled_requests_enabled   = true
  }
}

# The "aws-waf-logs-" prefix is not a naming preference -- WAF refuses any
# other prefix for a CloudWatch Logs destination.
resource "aws_cloudwatch_log_group" "waf_ui" {
  provider          = aws.use1
  name              = "aws-waf-logs-${local.name}-ui"
  retention_in_days = 30

  tags = { component = "ui" }
}

resource "aws_wafv2_web_acl_logging_configuration" "ui" {
  provider                = aws.use1
  resource_arn            = aws_wafv2_web_acl.ui.arn
  log_destination_configs = [aws_cloudwatch_log_group.waf_ui.arn]

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
