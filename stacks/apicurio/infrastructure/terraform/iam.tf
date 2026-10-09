data "aws_iam_policy_document" "ecs_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

# Execution role: pulls the image, writes logs, and — because this is an
# off-the-shelf image with no code of ours to self-fetch a secret at
# startup the way stacks/beacon/infrastructure/terraform/iam.tf's own app
# does — resolves the DB credential ECS injects via the task definition's
# `secrets` block. That's an ECS platform mechanic (the execution role
# fetches it before the container starts), not an application permission,
# which is why it's granted here and not on the task role below.
resource "aws_iam_role" "execution" {
  name               = "${local.name}-registry-execution-role"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json

  tags = { component = "registry" }
}

resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

data "aws_iam_policy_document" "execution_secrets" {
  statement {
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_db_instance.this.master_user_secret[0].secret_arn]
  }
}

# Named for the role it sits on, per identity-secrets.md's role/policy pairing
# rule: uif-apicurio-dev-registry-execution-role takes
# uif-apicurio-dev-registry-execution-policy. The previous
# `-registry-secrets-policy` described the grant instead of the holder, which
# is what lets a policy and its role drift apart unnoticed.
resource "aws_iam_role_policy" "execution_secrets" {
  name   = "${local.name}-registry-execution-policy"
  role   = aws_iam_role.execution.id
  policy = data.aws_iam_policy_document.execution_secrets.json
}

# Task role: the running container needs no AWS API access of its own —
# Apicurio Registry only talks to its Postgres backend over the JDBC URL.
# Kept as a near-empty role, rather than omitted, so a future need (e.g.
# an S3-backed artifact store) has somewhere to attach a policy without
# introducing a second role later.
resource "aws_iam_role" "task" {
  name               = "${local.name}-registry-task-role"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json

  tags = { component = "registry" }
}

# ── BFF roles ─────────────────────────────────────────────────────────────────
# Added with the BFF fold-in (UIFPE-36). Separate roles from the registry's:
# the two containers have genuinely different needs, and sharing one role would
# grant the off-the-shelf registry image the BFF's SSM access for no reason.

resource "aws_iam_role" "bff_execution" {
  name               = "${local.name}-bff-execution-role"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json

  tags = { component = "bff" }
}

resource "aws_iam_role_policy_attachment" "bff_execution" {
  role       = aws_iam_role.bff_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# No execution-role secrets grant: the BFF task definition declares no
# `secrets`. It holds no credentials — the registry is reached unauthenticated
# in-VPC and the CMDB client is a mock.

resource "aws_iam_role" "bff_task" {
  name               = "${local.name}-bff-task-role"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json

  tags = { component = "bff" }
}

# One inline policy per role, not two.
#
# identity-secrets.md pairs a policy name to its role by the last token, so
# both of this role's former policies — `-bff-execution-policy` (which named
# the wrong role entirely; it was never on the execution role) and
# `-bff-ssm-policy` — resolve to the same compliant name,
# `uif-apicurio-dev-bff-task-policy`. Two aws_iam_role_policy resources with
# one name on one role is not an error Terraform reports: the second
# PutRolePolicy simply overwrites the first, and whichever grant lost is gone
# with no plan diff to show it. Merging the documents removes the collision
# instead of renaming around it.
data "aws_iam_policy_document" "bff_task" {
  # ECS Exec, so `aws ecs execute-command` works against a BFF task. These
  # four actions take no resource, so "*" is the only form AWS accepts.
  statement {
    sid = "EcsExec"
    actions = [
      "ssmmessages:CreateControlChannel",
      "ssmmessages:CreateDataChannel",
      "ssmmessages:OpenControlChannel",
      "ssmmessages:OpenDataChannel",
    ]
    resources = ["*"]
  }

  # The Delivery Lab reads and writes the CDC enrollment config in SSM. Scoped
  # to the one parameter, and present only when that parameter is configured.
  dynamic "statement" {
    for_each = var.cdc_tables_ssm_parameter != "" ? [1] : []
    content {
      sid       = "CdcEnrollmentParameter"
      actions   = ["ssm:GetParameter", "ssm:PutParameter"]
      resources = ["arn:aws:ssm:${var.region}:${data.aws_caller_identity.current.account_id}:parameter${var.cdc_tables_ssm_parameter}"]
    }
  }
}

resource "aws_iam_role_policy" "bff_task" {
  name   = "${local.name}-bff-task-policy"
  role   = aws_iam_role.bff_task.id
  policy = data.aws_iam_policy_document.bff_task.json
}

data "aws_caller_identity" "current" {}
