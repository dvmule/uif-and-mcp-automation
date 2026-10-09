# BFF — the governance API in front of the registry.
#
# Folded in from the pre-migration `apicurio-bff` root module (UIFPE-36).
# Two deliberate changes made while folding, both away from that module:
#   1. It shares this module's ECS cluster directly instead of reading a
#      `terraform_remote_state` local state file — the coupling that made the
#      old six-module layout undeployable from a clean clone.
#   2. Autoscaling is gone entirely, not migrated. The old module had step
#      scaling plus two CloudWatch alarms plus a hand-rolled autoscaling IAM
#      role; the fold-in replaced that with target tracking, and this change
#      removes even that. With bff_max_count = 1 it could not scale anything,
#      and the dev deploy role holds no application-autoscaling grant — two
#      reasons to delete rather than request a permission for a no-op.

# IMMUTABLE, and the `trivy:ignore:AVD-AWS-0031` that used to sit here is gone
# with it -- the gap it excused is closed rather than re-excused. What that
# suppression described was real: MUTABLE was required by the
# `bff_image_tag = "latest"` fallback, because the second push of a moving tag
# fails against an immutable repository. It also warned what MUTABLE still
# permitted, which is the thing this change removes: `latest` moving under a
# local apply, silently repointing the task definition off the pinned sha.
#
# Three changes had to land together for this line to flip, and they did:
# deploy-apicurio-dev.yml no longer pushes `latest`; var.bff_image_digest
# replaced var.bff_image_tag and has no default; and the task definition below
# references the image by @digest rather than by tag.
#
# Note this does NOT stop a re-run of the deploy workflow on the same commit:
# an immutable repository refuses a second push of the same tag, so that job
# detects the sha tag already present and reuses the digest already there
# instead of rebuilding. See its own comment.
#
# The mirror repository below stays MUTABLE on purpose -- different problem,
# different answer, and its suppression is a genuine exemption. Read its
# comment before assuming this line should be copied down there.
resource "aws_ecr_repository" "bff" {
  name                 = "${local.name}-bff-ecr-repo"
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = { component = "bff" }
}

# The SIF base image, mirrored into this account. src/Dockerfile's
# ARG BASE_IMAGE points at a tag in here, so `docker build` pulls the base from
# our own registry rather than reaching cross-account into SIF's.
#
# WHY A MIRROR AND NOT SIF DIRECTLY. Cross-account pull from the SIF registry
# needs a resource policy on SIF's repository. A local `docker login` as
# PCMPowerUser does reach it -- verified on 2026-09-14, the manifest for
# python:3.12-alpine3.24 reads back with digest sha256:7c499675... matching the
# SIF portal -- but the GitHub Actions deploy role is a different principal and
# cannot be assumed to inherit that. Mirroring removes the question: CI only
# ever pulls from an account it already authenticates to, and
# deploy-apicurio-dev.yml's existing `docker login` against the registry HOST
# (not a specific repository) already covers it, before `docker build` runs.
#
# Declared here rather than created with `aws ecr create-repository`, which is
# what the Phase 7 runbook originally said, because
# capabilities/infrastructure/guidelines.md requires infrastructure to be
# declared as code and committed before it exists -- no click-ops. It is
# bootstrapped with `-target` exactly as aws_ecr_repository.bff is, since the
# image build needs the repository before a full apply has run.
#
# The suppression below is a genuine exemption, not deferred work -- unlike the
# one on aws_ecr_repository.bff above, which excuses a gap that is still open.
# MUTABLE is the CORRECT setting here for the reason spelled out on
# image_tag_mutability inside the resource: SIF re-pushes the same tag as
# packages are patched, so re-mirroring must stay possible, and src/Dockerfile
# pins BASE_IMAGE by @sha256 so a moved tag cannot change what a build
# produces. The digest is the contract; the tag is a pointer.
#
# It was missing, and that single omission failed `trivy config (terraform)` --
# the one HIGH in the whole module -- which in turn failed the ci job and the
# stacks-ci gate. The directive must be the LAST comment line before the block
# or trivy drops it silently, so nothing may be added between this line and the
# resource.
# trivy:ignore:AVD-AWS-0031
resource "aws_ecr_repository" "base_python" {
  name = "${local.name}-base-python-ecr-repo"

  # MUTABLE, unlike a first instinct to freeze a base image. SIF rebuilds the
  # same tag as packages are patched -- every Python tag in their catalogue was
  # re-pushed on 2026-09-13 -- so re-mirroring a patched 3.12-alpine3.24 has to
  # be possible, and IMMUTABLE would refuse it. Reproducibility does not depend
  # on the tag here: src/Dockerfile pins BASE_IMAGE by @sha256, so a moved tag
  # cannot silently change what a build produces. The digest is the contract,
  # the tag is a pointer.
  image_tag_mutability = "MUTABLE"

  # ECR's own scan of the base, independent of the `trivy image` gate in
  # qe/ci.sh. Two scanners disagreeing about a base image is information worth
  # having, and this one runs on push without anybody invoking it.
  image_scanning_configuration {
    scan_on_push = true
  }

  tags = { component = "bff" }
}

# NO aws_ecr_lifecycle_policy for base_python, deliberately -- do not copy the
# bff one below.
#
# Its first rule expires untagged images after 7 days. Applied here that is a
# guaranteed build break: re-mirroring a patched tag leaves the PREVIOUS digest
# untagged, and src/Dockerfile pins a digest, so seven days later every build
# fails with a manifest-not-found on an image that was deliberately pinned. Its
# second rule, keeping only the last 10 tagged images, has the same shape for
# any pin older than ten mirrors.
#
# A base image is referenced by digest for as long as the Dockerfile names it,
# which is not a retention window anyone can predict. If this repository ever
# needs pruning, prune by "not referenced by any Dockerfile in the repo", which
# is a question a lifecycle policy cannot ask.

resource "aws_ecr_lifecycle_policy" "bff" {
  repository = aws_ecr_repository.bff.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Expire untagged images after 7 days"
        selection = {
          tagStatus   = "untagged"
          countType   = "sinceImagePushed"
          countUnit   = "days"
          countNumber = 7
        }
        action = { type = "expire" }
      },
      {
        rulePriority = 2
        description  = "Keep last 10 tagged images"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = 10
        }
        action = { type = "expire" }
      },
    ]
  })
}

resource "aws_cloudwatch_log_group" "bff" {
  name              = "/ecs/${local.name}-bff"
  retention_in_days = 30

  tags = { component = "bff" }
}

# ── Network ───────────────────────────────────────────────────────────────────
resource "aws_security_group" "bff_alb" {
  name        = "${local.name}-bff-alb-sg"
  description = "Internal ALB fronting the BFF"
  vpc_id      = var.vpc_id

  tags = { component = "bff" }
}

# In-VPC callers plus the VPN client range. API Gateway does not appear here:
# it reaches this ALB through the VPC Link, whose ENIs sit in the private
# subnets and therefore inside the VPC CIDR. The VPN range is what lets an FDE
# call the BFF directly, bypassing the authorizer, to tell an authorizer
# rejection apart from a BFF fault.
resource "aws_vpc_security_group_ingress_rule" "bff_alb_from_vpc" {
  for_each = toset(distinct(concat([data.aws_vpc.this.cidr_block], var.vpn_client_cidrs)))

  security_group_id = aws_security_group.bff_alb.id
  description       = "BFF ALB from ${each.value}"
  ip_protocol       = "tcp"
  from_port         = var.bff_port
  to_port           = var.bff_port
  cidr_ipv4         = each.value

  tags = { component = "bff" }
}

resource "aws_vpc_security_group_egress_rule" "bff_alb_to_task" {
  security_group_id            = aws_security_group.bff_alb.id
  description                  = "BFF ALB to the BFF task"
  ip_protocol                  = "tcp"
  from_port                    = var.bff_port
  to_port                      = var.bff_port
  referenced_security_group_id = aws_security_group.bff.id

  tags = { component = "bff" }
}

resource "aws_security_group" "bff" {
  name        = "${local.name}-bff-sg"
  description = "BFF task"
  vpc_id      = var.vpc_id

  tags = { component = "bff" }
}

resource "aws_vpc_security_group_ingress_rule" "bff_from_alb" {
  security_group_id            = aws_security_group.bff.id
  description                  = "Only its own ALB"
  ip_protocol                  = "tcp"
  from_port                    = var.bff_port
  to_port                      = var.bff_port
  referenced_security_group_id = aws_security_group.bff_alb.id

  tags = { component = "bff" }
}

# Same reasoning as the registry task's egress in ecs.tf: image pull through
# NAT, CloudWatch Logs, SSM for the CDC parameter, the registry ALB, and DNS
# for all of them.
# Same reasoning as registry_all in ecs.tf: image pull, CloudWatch
# Logs, SSM and the registry ALB all need DNS, which a 443+5432 rule drops.
# trivy:ignore:AVD-AWS-0104 See the comment above.
resource "aws_vpc_security_group_egress_rule" "bff_all" {
  security_group_id = aws_security_group.bff.id
  description       = "Image pull, CloudWatch Logs, SSM, the registry ALB, DNS"
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"

  tags = { component = "bff" }
}

# The BFF calls the registry over the registry ALB, so that ALB has to accept it.
resource "aws_vpc_security_group_ingress_rule" "registry_alb_from_bff" {
  security_group_id            = aws_security_group.alb.id
  description                  = "BFF task to the registry ALB"
  ip_protocol                  = "tcp"
  from_port                    = 80
  to_port                      = 80
  referenced_security_group_id = aws_security_group.bff.id

  tags = { component = "registry" }
}

resource "aws_lb" "bff" {
  name               = "${local.name}-bff-alb"
  internal           = true # always internal — public access is API Gateway's job
  load_balancer_type = "application"
  security_groups    = [aws_security_group.bff_alb.id]
  subnets            = length(var.alb_subnet_ids) > 0 ? var.alb_subnet_ids : var.private_subnet_ids

  # AVD-AWS-0052, same reasoning as the registry ALB in alb.tf.
  drop_invalid_header_fields = true

  tags = { component = "bff" }
}

resource "aws_lb_target_group" "bff" {
  name        = "${local.name}-bff-tg"
  port        = var.bff_port
  protocol    = "HTTP"
  vpc_id      = var.vpc_id
  target_type = "ip" # required for Fargate awsvpc mode

  health_check {
    path                = "/api/v1/health"
    port                = "traffic-port"
    protocol            = "HTTP"
    healthy_threshold   = 2
    unhealthy_threshold = 3
    interval            = 30
    timeout             = 10
    matcher             = "200"
  }

  deregistration_delay = 30

  tags = { component = "bff" }

  lifecycle {
    create_before_destroy = true
  }
}

# Internal ALB (internal = true, hardcoded); the public path
# terminates TLS at API Gateway. Revisit if the BFF ever forwards a bearer token or Basic
# credential to the registry over this listener -- that credential would be on the wire in
# cleartext inside the VPC, which is the one real thing this check points at and is not
# something API Gateway TLS covers.
# trivy:ignore:AVD-AWS-0054 See the comment above.
resource "aws_lb_listener" "bff" {
  load_balancer_arn = aws_lb.bff.arn
  port              = var.bff_port
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.bff.arn
  }

  tags = { component = "bff" }
}

# ── Service ───────────────────────────────────────────────────────────────────

resource "aws_ecs_task_definition" "bff" {
  family                   = "${local.name}-bff-task-definition"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.bff_cpu
  memory                   = var.bff_memory
  execution_role_arn       = aws_iam_role.bff_execution.arn
  task_role_arn            = aws_iam_role.bff_task.arn

  container_definitions = jsonencode([{
    name = "bff"
    # By DIGEST, not by tag. A tag is a pointer that can be repointed; a digest
    # names one specific image and cannot. This is what makes "what is running
    # in prod" answerable from the task definition alone.
    image        = "${aws_ecr_repository.bff.repository_url}@${var.bff_image_digest}"
    essential    = true
    portMappings = [{ containerPort = var.bff_port, protocol = "tcp" }]

    environment = [
      { name = "APICURIO_BASE_URL", value = "http://${aws_lb.registry.dns_name}" },
      # v3 REST path. The registry image is Apicurio 3.x, whose API is served at
      # /apis/registry/v3 — /apis/registry/v2 does not exist on a v3 server.
      { name = "APICURIO_API_PATH", value = "/apis/registry/v3" },
      { name = "CMDB_BASE_URL", value = var.cmdb_base_url },
      { name = "LOG_LEVEL", value = var.bff_log_level },
      # pydantic-settings parses a `list[str]` field's env value as JSON, not
      # as a delimited string -- a bare comma-separated value raises
      # SettingsError at import (app.main:create_app -> get_settings), which
      # kills the container before Uvicorn binds. jsonencode(split(...)) turns
      # the same comma-separated var.cors_origins that apigw.tf already splits
      # (for its own CORS config) into the JSON array Settings expects.
      # Do not "simplify" this back to a bare `var.cors_origins`.
      # Covered by qe/tests/test_config_cors_origins.py on the Python side only
      # -- nothing in that suite can see this line.
      { name = "CORS_ORIGINS", value = jsonencode(local.cors_origins) },
      { name = "CDC_TABLES_SSM_PARAMETER", value = var.cdc_tables_ssm_parameter },
    ]

    # No task secrets. The BFF holds no credentials of its own: the registry is
    # reached unauthenticated in-VPC, and the CMDB client is a mock.

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.bff.name
        awslogs-region        = var.region
        awslogs-stream-prefix = "bff"
      }
    }

    healthCheck = {
      command     = ["CMD-SHELL", "curl -f http://localhost:${var.bff_port}/api/v1/health || exit 1"]
      interval    = 30
      timeout     = 10
      retries     = 3
      startPeriod = 30 # Python is up in <5s; generous
    }
  }])

  tags = { component = "bff" }
}

resource "aws_ecs_service" "bff" {
  name                              = "${local.name}-bff-service"
  cluster                           = aws_ecs_cluster.this.id
  task_definition                   = aws_ecs_task_definition.bff.arn
  desired_count                     = var.bff_desired_count
  launch_type                       = "FARGATE"
  health_check_grace_period_seconds = 60
  enable_execute_command            = true # `aws ecs execute-command` for live debugging

  # Tags the tasks and their ENIs, not just the service object -- same reason as
  # the registry service in ecs.tf, same precedent in
  # stacks/beacon/infrastructure/terraform/keeper-gateway.tf.
  enable_ecs_managed_tags = true
  propagate_tags          = "SERVICE"

  # Same automatic rollback as the registry service (see ecs.tf). It matters
  # more here, not less: desired_count is pinned at 1, so a task definition
  # that cannot start takes the whole BFF down rather than degrading it, and
  # there is no second task still serving while somebody notices.
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  network_configuration {
    subnets          = var.private_subnet_ids
    security_groups  = [aws_security_group.bff.id]
    assign_public_ip = var.assign_public_ip
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.bff.arn
    container_name   = "bff"
    container_port   = var.bff_port
  }

  tags = { component = "bff" }

  depends_on = [aws_lb_listener.bff]
}

# ── CDC enrollment config ─────────────────────────────────────────────────────
# The BFF reads this at startup via CDC_TABLES_SSM_PARAMETER to discover which
# Oracle tables the CDC poller should read. Created only when a parameter name
# is set, so the Delivery Lab surface can be left unconfigured.

resource "aws_ssm_parameter" "cdc_enrollment" {
  count       = var.cdc_tables_ssm_parameter != "" ? 1 : 0
  name        = var.cdc_tables_ssm_parameter
  type        = "String"
  description = "CDC enrollment configuration for ${local.name}"
  value       = var.cdc_enrollment_json

  tags = { component = "bff" }

  lifecycle {
    # Operators edit this through the BFF's Delivery Lab API at runtime;
    # Terraform seeds it once and then stops owning the value.
    ignore_changes = [value]
  }
}
