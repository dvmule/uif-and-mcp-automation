# First RDS instance anywhere in this repo — checked before writing this:
# no aws_db_instance / aws_rds anywhere in domains/ or stacks/. Apicurio
# Registry needs a real relational backend for its SQL storage variant
# (APICURIO_STORAGE_KIND=sql); Postgres is Apicurio's own tested storage
# backend, so this establishes the pattern rather than mirroring one.

resource "aws_db_subnet_group" "this" {
  name       = "${local.name}-registry-db-subnet-group"
  subnet_ids = var.private_subnet_ids

  tags = { component = "registry" }
}

resource "aws_security_group" "db" {
  name        = "${local.name}-db-sg"
  description = "Apicurio registry Postgres, reachable only from the registry task"
  vpc_id      = var.vpc_id

  tags = { component = "registry" }
}

# Separate rule resources, not inline ingress/egress blocks. Inline blocks are
# authoritative: the aws_security_group resource treats any rule it does not
# declare as drift and revokes it on the next apply. That is not a style
# preference here — alb.tf carried inline blocks while bff.tf added a separate
# ingress rule to the same group, so the two would have taken turns deleting
# each other's rule on every apply after the first.
resource "aws_vpc_security_group_ingress_rule" "db_from_registry" {
  security_group_id            = aws_security_group.db.id
  description                  = "Registry task to Postgres, never the ALB and never the internet"
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
  referenced_security_group_id = aws_security_group.registry.id

  tags = { component = "registry" }
}

# No egress rule, deliberately. A Postgres instance answers connections; it
# does not open them. RDS performs backups, log export and credential rotation
# through the service, not from this ENI. The previous all-protocol
# 0.0.0.0/0 egress granted reachability nothing in this stack uses.

resource "aws_db_instance" "this" {
  identifier     = "${local.name}-registry-db"
  engine         = "postgres"
  engine_version = "16"
  instance_class = var.db_instance_class

  allocated_storage      = var.db_allocated_storage
  storage_encrypted      = true
  db_subnet_group_name   = aws_db_subnet_group.this.name
  vpc_security_group_ids = [aws_security_group.db.id]
  publicly_accessible    = false

  db_name  = "apicurio"
  username = "apicurio"

  # RDS owns the password, not Terraform.
  #
  # The previous shape generated it with random_password and stored it in a
  # secret this stack owned. Terraform persists every generated value in
  # state, so the password was in state regardless of what the variable was
  # called — `sensitive = true` masks display, not storage. With
  # manage_master_user_password, RDS creates and rotates the credential and
  # Terraform only ever reads the resulting secret's ARN.
  #
  # `password` must not be set alongside this; setting both is an error.
  #
  # Three consequences, recorded here because none of them is visible at the
  # call site:
  #
  #  - Rotation can break a running task. Apicurio reads the datasource
  #    password once, at container start. Rotation changes it in the database;
  #    pooled connections keep working and new ones fail to authenticate, so
  #    the symptom appears later as a task that cannot reconnect, not at
  #    rotation time. Mitigation is a forced new deployment after a rotation.
  #  - The secret's name is AWS's. RDS calls it rds!db-<resource-id>, which
  #    cannot be set and does not follow identity-secrets.md's
  #    uif/{stack}/{env}/{system-type}/{system} form. It is an AWS-owned name
  #    in the same class as the $default API Gateway stage.
  #  - The secret uses the Secrets Manager default KMS key unless one is named
  #    here, and the key cannot be changed afterwards. Default is correct for
  #    dev. If the ECS task fails to fetch the secret, a timeout is the VPC
  #    endpoint seat and a denial is KMS.
  manage_master_user_password = true

  # AWS requires final_snapshot_identifier whenever skip_final_snapshot is
  # false, so prod without it could not be replaced OR destroyed -- the apply
  # would fail on a missing argument, which reads as a bug rather than as the
  # guard it accidentally was. It must be null when skipping: RDS rejects both
  # an identifier with skip, and a skip without one.
  #
  # The name is static on purpose. timestamp() would make it unique but also
  # produce a perpetual diff, and the static name means a SECOND prod destroy
  # fails while the first snapshot still exists. That is the behaviour to want:
  # deleting a prod database twice should require a human to look at what the
  # first attempt left behind.
  skip_final_snapshot       = var.env != "prod"
  final_snapshot_identifier = var.env == "prod" ? "${local.name}-registry-db-final" : null
  deletion_protection       = var.env == "prod"
  backup_retention_period   = var.env == "prod" ? 7 : 1

  # Off by default in the AWS API, so automated and manual snapshots -- the
  # things that outlive the instance and keep costing -- would carry no tags at
  # all and be invisible to any Project-scoped query.
  copy_tags_to_snapshot = true

  tags = { component = "registry" }
}
