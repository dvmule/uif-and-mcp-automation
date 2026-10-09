# The gold jobs read Silver, but the role that runs them is the platform
# stack's (echo-{env}-glue-role) and its policy names only that stack's own
# stores. ../terraform/lakeformation.tf does grant Silver, to that root's own
# uif-echo-{env}-glue-role — a different principal — so the jobs themselves
# have no read on the standard-named Silver databases.
#
# Granted by LF tag rather than per database: Lake Formation has no
# name-pattern grant, so a tag is the only form in which "every
# uif_echo_{env}_silver_* database" is ONE rule, which a new source system
# joins by carrying the tag with no grant change here.
#
# The tag KEY itself is not declared here, exactly as uif-echo-gold-layer is
# not: the deploy role holds AddLFTagsToResource and GrantPermissions but not
# CreateLFTag, so a tag resource in this root fails at apply. The key is an
# account-level object created once by an LF admin; everything repeatable —
# which databases carry it, and who it grants — is Terraform's.

# A tag attaches to a resource, never to a name pattern, so the databases are
# enumerated once, here and nowhere else. Adding a source system is one line.
resource "aws_lakeformation_resource_lf_tags" "silver_databases" {
  for_each = toset(local.silver_source_databases)

  database {
    name = each.value
  }

  lf_tag {
    key   = local.silver_layer_tag_key
    value = var.environment
  }
}

resource "aws_lakeformation_permissions" "glue_silver_databases" {
  principal   = data.aws_iam_role.glue_execution.arn
  permissions = ["DESCRIBE"]

  lf_tag_policy {
    resource_type = "DATABASE"
    expression {
      key    = local.silver_layer_tag_key
      values = [var.environment]
    }
  }

  depends_on = [aws_lakeformation_resource_lf_tags.silver_databases]
}

# Read only. The gold jobs never write Silver, and DESCRIBE alone lists a
# database while showing none of its tables — the shape that has bitten this
# estate twice.
resource "aws_lakeformation_permissions" "glue_silver_tables" {
  principal   = data.aws_iam_role.glue_execution.arn
  permissions = ["SELECT", "DESCRIBE"]

  lf_tag_policy {
    resource_type = "TABLE"
    expression {
      key    = local.silver_layer_tag_key
      values = [var.environment]
    }
  }

  depends_on = [aws_lakeformation_resource_lf_tags.silver_databases]
}
