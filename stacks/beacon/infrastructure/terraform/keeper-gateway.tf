# keeper-gateway.tf — the account's Keeper Gateway (UIFPE-259), adapted from
# github.com/pearson-dnt-cybersecurity/keeper-gateway-deployment
# (gateway_creation_fargate) per the security team's "Keeper Gateway and AWS
# Secret Sync" guide.
#
# WHAT IT IS: one egress-only ECS Fargate task that connects OUT to the
# Keeper Cloud and pushes vault-managed secrets INTO AWS Secrets Manager
# (Universal Secret Sync — one-way, vault -> cloud, matched by name). Beacon's
# runtime keeps reading Secrets Manager unchanged; this task is what makes
# Keeper the system of record.
#
# WHY IT LIVES IN BEACON'S STACK: a gateway is per AWS account and serves ALL
# UIF stacks (the PAM configuration accepts multiple folder locations), but
# the deploy role's powers are fenced to uif-beacon-* names — the only fence
# available today. The gateway is STATELESS (its real config lives encrypted
# in Keeper; the guide: "you can redeploy at any time without reconfiguring"),
# so re-homing it under platform ownership later is a redeploy, not a
# migration. Recorded as an interim in the UIFPE-259 ADR.
#
# TWO DELIBERATE CHANGES from the source repo:
#   1. Names inside our fence and per the UIF Resource Name Lookup
#      (-role / -sg suffixes).
#   2. The task-role policy is NARROWED from the repo's secretsmanager:* on *
#      to uif/* secrets only — wide enough for every UIF stack's slash-style
#      secrets, fenced off from the other teams in this shared account. The
#      actions stay secretsmanager:* per the guide's own recommendation.
#
# DISABLED until the bootstrap exists: keeper_gateway_enabled stays false
# until the gateway token secret (uif/beacon/dev/keeper/gateway, created by
# hand in the console per the guide's Step 7) is in place — the ECS task
# cannot start without it.

variable "keeper_gateway_enabled" {
  description = "Deploy the Keeper Gateway (requires the token secret to exist first)"
  type        = bool
  default     = false
}

variable "keeper_gateway_secret_name" {
  description = "Name of the gateway's bootstrap token secret (convention: uif/beacon/{env}/keeper/gateway)"
  type        = string
  default     = ""
}

variable "keeper_gateway_secret_arn" {
  description = "ARN of the gateway's bootstrap token secret"
  type        = string
  default     = ""
}

variable "keeper_gateway_cpu" {
  description = "Fargate CPU units (2048 = 2 vCPU; raise only for SSH/RDP tunnelling features)"
  type        = string
  default     = "2048"
}

variable "keeper_gateway_memory" {
  description = "Fargate memory MiB (must pair validly with the CPU value)"
  type        = string
  default     = "8192"
}

locals {
  keeper_gateway_count = var.keeper_gateway_enabled ? 1 : 0
  keeper_gateway_name  = "${local.name}-keeper-gateway"
}

# -- IAM -----------------------------------------------------------------------

data "aws_iam_policy_document" "keeper_gateway_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

# Execution role: container plumbing only — pull the image, write logs, and
# read the ONE bootstrap secret injected as GATEWAY_CONFIG.
resource "aws_iam_role" "keeper_gateway_exec" {
  count              = local.keeper_gateway_count
  name               = "${local.keeper_gateway_name}-exec-role"
  assume_role_policy = data.aws_iam_policy_document.keeper_gateway_assume.json
}

resource "aws_iam_role_policy_attachment" "keeper_gateway_exec_managed" {
  count      = local.keeper_gateway_count
  role       = aws_iam_role.keeper_gateway_exec[0].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "keeper_gateway_exec" {
  count = local.keeper_gateway_count
  name  = "keeper-gateway-bootstrap"
  role  = aws_iam_role.keeper_gateway_exec[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { # the trailing * absorbs the 6-char suffix AWS appends to secret ARNs
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"]
        Resource = "arn:aws:secretsmanager:*:${data.aws_caller_identity.current.account_id}:secret:${var.keeper_gateway_secret_name}*"
      },
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup"]
        Resource = "arn:aws:logs:*:*:*"
      },
    ]
  })
}

# Task role: what Keeper may MANAGE. Narrowed to UIF's slash-style secrets —
# the repo ships * and its own comment says to narrow it here.
resource "aws_iam_role" "keeper_gateway_task" {
  count              = local.keeper_gateway_count
  name               = "${local.keeper_gateway_name}-task-role"
  assume_role_policy = data.aws_iam_policy_document.keeper_gateway_assume.json
}

resource "aws_iam_role_policy" "keeper_gateway_task" {
  count = local.keeper_gateway_count
  name  = "keeper-gateway-secret-sync"
  role  = aws_iam_role.keeper_gateway_task[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["secretsmanager:*"]
      Resource = "arn:aws:secretsmanager:*:${data.aws_caller_identity.current.account_id}:secret:uif/*"
    }]
  })
}

# -- network + logs ------------------------------------------------------------

# Egress only: the gateway phones OUT to the Keeper Cloud and rides those
# connections for everything; nothing ever connects in.
resource "aws_security_group" "keeper_gateway" {
  count  = local.keeper_gateway_count
  name   = "${local.keeper_gateway_name}-sg"
  vpc_id = var.vpc_id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_cloudwatch_log_group" "keeper_gateway" {
  count             = local.keeper_gateway_count
  name              = "/ecs/${local.keeper_gateway_name}"
  retention_in_days = 7
}

# -- ECS -----------------------------------------------------------------------

resource "aws_ecs_cluster" "keeper_gateway" {
  count = local.keeper_gateway_count
  name  = local.keeper_gateway_name

  setting {
    name  = "containerInsights"
    value = "disabled"
  }
}

resource "aws_ecs_task_definition" "keeper_gateway" {
  count                    = local.keeper_gateway_count
  family                   = local.keeper_gateway_name
  task_role_arn            = aws_iam_role.keeper_gateway_task[0].arn
  execution_role_arn       = aws_iam_role.keeper_gateway_exec[0].arn
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = var.keeper_gateway_cpu
  memory                   = var.keeper_gateway_memory

  container_definitions = jsonencode([{
    name      = "keeper-gateway"
    image     = "keeper/gateway:latest" # a redeploy always pulls latest (guide)
    essential = true
    environment = [
      { name = "ACCEPT_EULA", value = "Y" }
    ]
    secrets = [
      { name = "GATEWAY_CONFIG", valueFrom = var.keeper_gateway_secret_arn }
    ]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.keeper_gateway[0].name
        awslogs-create-group  = "true"
        awslogs-region        = var.region
        awslogs-stream-prefix = "ecs"
      }
    }
  }])
}

resource "aws_ecs_service" "keeper_gateway" {
  count                   = local.keeper_gateway_count
  name                    = "${local.keeper_gateway_name}-service"
  cluster                 = aws_ecs_cluster.keeper_gateway[0].id
  task_definition         = aws_ecs_task_definition.keeper_gateway[0].arn
  desired_count           = 1
  launch_type             = "FARGATE"
  scheduling_strategy     = "REPLICA"
  enable_ecs_managed_tags = true
  propagate_tags          = "SERVICE"
  enable_execute_command  = false

  network_configuration {
    subnets          = var.private_subnet_ids
    security_groups  = [aws_security_group.keeper_gateway[0].id]
    assign_public_ip = false # egress rides the platform NAT
  }

  # a failing deploy reverts itself rather than crash-looping — check the
  # CloudWatch logs, not just the service status (guide's gotcha)
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  deployment_maximum_percent         = 200
  deployment_minimum_healthy_percent = 100
}
