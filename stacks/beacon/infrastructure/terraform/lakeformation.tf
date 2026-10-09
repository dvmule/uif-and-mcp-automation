# Lake Formation grants for the Gold-layer sources this stack reads.
#
# LF is a second permission layer ON TOP of IAM: iam.tf's Glue/S3 statements
# alone are not enough — without these grants the pipeline fails with
# "Insufficient Lake Formation permission(s)" at read time, and the control
# panel's live column introspection falls back to the registry.
#
# TAG-BASED, not named-resource (UIFPE-229 deploy fix, 2026-09-02). The
# account admin granted the deploy role DESCRIBE+SELECT with grant option on
# tables tagged `uif-echo-gold-layer={env}` — an LF-Tag policy, the account's
# pattern for the whole gold layer. LF permissions received through a tag can
# only be re-granted through that SAME tag, never re-shaped into a
# named-database grant, so granting by database name here fails with
# "Insufficient Glue permissions to access database <db>". One tag grant per
# role covers every gold table, current and future: a NEW gold database needs
# no grant work at all (provided the platform tags it), only the
# gold_databases tfvars line that puts it in the wizard's picker.
#
# gold_databases still gates these grants: empty (a fresh environment, the
# sandbox) creates nothing — the tag may not even exist there.

variable "gold_databases" {
  description = "Glue database names of the Gold sources exports read (e.g. the product domain's gold database). Drives the wizard's domain picker (BEACON_GOLD_DATABASES); empty also skips the LF grants."
  type        = list(string)
  default     = []
}

locals {
  # The account's gold-layer LF-Tag (owned by the platform/data-lake team).
  gold_lf_tag_key   = "uif-echo-gold-layer"
  gold_lf_tag_value = var.env
}

# The transform's own grant (SELECT + DESCRIBE) lives with its role in
# glue.tf (glue_transform_gold). The Fargate pipeline's grant was deleted
# with the pipeline at UIFPE-215's probation exit.

# The control panel introspects schemas (live column lists: DESCRIBE) and,
# since UIFPE-441, reads gold DATA through Athena for the wizard's live filter
# preview and value suggestions (SELECT). UIFPE-226: granted to the API
# Lambda's role (was the ECS task role — this grant moving is what keeps the
# wizard's live column picker working; without it introspection silently
# degrades to the registry YAML fallback).
#
# Every argument of this resource is ForceNew, so widening DESCRIBE to
# SELECT+DESCRIBE plans as a REPLACE: revoke, then grant. For those seconds
# the API role holds no grant (columns fall back to the registry, the picker
# may 503) — apply off-peak, and do NOT add create_before_destroy: the
# deferred destroy would revoke DESCRIBE out of the merged grant and leave
# SELECT alone. Verify after apply:
#   aws lakeformation list-permissions --principal DataLakePrincipalIdentifier=<api role arn>
resource "aws_lakeformation_permissions" "api_gold" {
  count       = length(var.gold_databases) > 0 ? 1 : 0
  principal   = aws_iam_role.api_lambda.arn
  permissions = ["SELECT", "DESCRIBE"]

  lf_tag_policy {
    resource_type = "TABLE"
    expression {
      key    = local.gold_lf_tag_key
      values = [local.gold_lf_tag_value]
    }
  }
}
