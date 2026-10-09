data "aws_vpc" "this" {
  id = var.vpc_id
}

resource "aws_security_group" "alb" {
  name        = "${local.name}-registry-alb-sg"
  description = "Registry ALB"
  vpc_id      = var.vpc_id

  tags = { component = "registry" }
}

# Who may reach the registry ALB directly. The VPC CIDR covers in-VPC callers
# (the BFF task reaches it through a rule declared in bff.tf); the VPN client
# range is what a person on connect.pearson.com presents when they hit an
# internal ALB by its DNS name, which is how an FDE verifies the registry and
# how the tier-3 integration suite reaches it.
#
# Note this is the CLIENT range, not the external one. api_allowed_cidrs
# carries the external range, and only API Gateway ever sees that — putting
# 10.122.0.0/15 there would be a rule nothing could ever match.
locals {
  registry_alb_ingress_cidrs = var.alb_internal ? distinct(concat([data.aws_vpc.this.cidr_block], var.vpn_client_cidrs)) : ["0.0.0.0/0"]
}

resource "aws_vpc_security_group_ingress_rule" "registry_alb_http" {
  for_each = toset(local.registry_alb_ingress_cidrs)

  security_group_id = aws_security_group.alb.id
  description       = "HTTP to the registry ALB from ${each.value}"
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
  cidr_ipv4         = each.value

  tags = { component = "registry" }
}

# An ALB only ever talks to its targets, so egress is enumerable here in a way
# it is not on the task security group: the API port and the management port
# the health check uses, both to the registry task's group and nowhere else.
# The previous all-protocol 0.0.0.0/0 egress was inherited boilerplate.
resource "aws_vpc_security_group_egress_rule" "registry_alb_to_task_api" {
  security_group_id            = aws_security_group.alb.id
  description                  = "Registry ALB to the task API port"
  ip_protocol                  = "tcp"
  from_port                    = 8080
  to_port                      = 8080
  referenced_security_group_id = aws_security_group.registry.id

  tags = { component = "registry" }
}

resource "aws_vpc_security_group_egress_rule" "registry_alb_to_task_management" {
  security_group_id            = aws_security_group.alb.id
  description                  = "Registry ALB to the task management port for the health check"
  ip_protocol                  = "tcp"
  from_port                    = 9000
  to_port                      = 9000
  referenced_security_group_id = aws_security_group.registry.id

  tags = { component = "registry" }
}

resource "aws_lb" "registry" {
  name               = "${local.name}-registry-alb"
  internal           = var.alb_internal
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = length(var.alb_subnet_ids) > 0 ? var.alb_subnet_ids : var.private_subnet_ids

  # AVD-AWS-0052. Costs nothing here -- Apicurio speaks ordinary HTTP -- and drops
  # malformed headers at the load balancer instead of passing them to the target.
  drop_invalid_header_fields = true

  tags = { component = "registry" }
}

resource "aws_lb_target_group" "registry" {
  name        = "${local.name}-registry-tg"
  port        = 8080
  protocol    = "HTTP"
  vpc_id      = var.vpc_id
  target_type = "ip" # required for Fargate awsvpc mode

  health_check {
    # Readiness is on the management interface (9000) at /health/ready in
    # Apicurio 3.3.2; /q/health/ready 404s. Traffic keeps going to 8080 above —
    # only the health probe is redirected to the management port.
    port                = "9000"
    path                = "/health/ready"
    healthy_threshold   = 2
    unhealthy_threshold = 3
    interval            = 30
  }

  tags = { component = "registry" }
}

# Internal ALB in private subnets; TLS terminates at API Gateway for
# the public path. Note the check is cruder than it looks -- AVD-AWS-0053 ("exposed to the
# internet") PASSES on this same resource because it reads internal = var.alb_internal, and
# 0054 fires anyway with no internal/external distinction.
# trivy:ignore:AVD-AWS-0054 See the comment above.
resource "aws_lb_listener" "registry" {
  load_balancer_arn = aws_lb.registry.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.registry.arn
  }

  tags = { component = "registry" }
}
