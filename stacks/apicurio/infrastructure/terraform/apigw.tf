# Public edge for the BFF — API Gateway HTTP API reaching the internal ALB
# through a VPC Link.
#
# Folded in from the pre-migration `apigw-bff` root module (UIFPE-36). It read
# the BFF ALB listener ARN and SG id from a local state file; both are now
# direct references.
#
# AUTHORIZER: a source-IP allowlist plus a CI header secret, defined in
# authorizer.tf. Both routes below carry `authorization_type = "CUSTOM"` — a route
# left on NONE is the whole control defeated, because either route reaches the same
# integration and the same BFF.
#
# What it protects: the public HTTP API, and therefore every path on the BFF, which
# still has no authentication of its own. Nothing that fails the authorizer reaches
# the VPC.
#
# What it does NOT do, and this matters more than what it does: it authenticates a
# NETWORK LOCATION and a SHARED SECRET, not a user. There is no identity behind an
# allowed request, so there is no per-user authorization and no audit trail of who
# registered, changed or deleted which contract — the access log records a source IP
# and nothing else. Anyone on the Pearson VPN is indistinguishable from anyone else
# on it, and anyone holding the CI secret is indistinguishable from CI.
#
# It is a stop-gap pending Entra federation (UIFPE-36, ../../DEPLOY-DEV.md Phase 1),
# not a fix: ../../ARCHITECTURE.md gap 1 narrows from "no authentication anywhere" to
# "no user identity", and does not close.
#
# Preflight is unaffected: with `cors_configuration` set, API Gateway answers OPTIONS
# preflight itself and never invokes the authorizer, so the browser's preflight
# succeeds from any address and the real request is still checked.

resource "aws_apigatewayv2_api" "bff" {
  name          = "${local.name}-bff-api"
  protocol_type = "HTTP"
  description   = "Public HTTP API proxy for the Apicurio governance BFF"

  cors_configuration {
    allow_origins = local.cors_origins
    allow_methods = ["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"]
    allow_headers = ["Content-Type", "Authorization", "X-Requested-With"]
    max_age       = 300
  }

  tags = { component = "edge" }
}

# Places ENIs in the private subnets so API Gateway can reach the internal ALB.
resource "aws_security_group" "vpc_link" {
  name        = "${local.name}-vpc-link-sg"
  description = "API Gateway VPC Link ENIs"
  vpc_id      = var.vpc_id

  tags = { component = "edge" }

  # No inbound rules: API Gateway controls what enters the VPC Link.
}

resource "aws_vpc_security_group_egress_rule" "vpc_link_to_bff_alb" {
  security_group_id            = aws_security_group.vpc_link.id
  description                  = "VPC Link to the BFF internal ALB"
  ip_protocol                  = "tcp"
  from_port                    = var.bff_port
  to_port                      = var.bff_port
  referenced_security_group_id = aws_security_group.bff_alb.id

  tags = { component = "edge" }
}

resource "aws_apigatewayv2_vpc_link" "bff" {
  name               = "${local.name}-bff-vpc-link"
  security_group_ids = [aws_security_group.vpc_link.id]
  subnet_ids         = var.private_subnet_ids

  tags = { component = "edge" }
}

resource "aws_apigatewayv2_integration" "bff" {
  api_id             = aws_apigatewayv2_api.bff.id
  integration_type   = "HTTP_PROXY"
  integration_uri    = aws_lb_listener.bff.arn
  integration_method = "ANY"
  connection_type    = "VPC_LINK"
  connection_id      = aws_apigatewayv2_vpc_link.bff.id

  # Preserve the original request path — do not strip a stage prefix.
  payload_format_version = "1.0"
}

resource "aws_apigatewayv2_route" "proxy" {
  api_id    = aws_apigatewayv2_api.bff.id
  route_key = "ANY /{proxy+}"
  target    = "integrations/${aws_apigatewayv2_integration.bff.id}"

  authorization_type = "CUSTOM"
  authorizer_id      = aws_apigatewayv2_authorizer.bff.id
}

# Requests to / carry no trailing path segment and so miss {proxy+}.
resource "aws_apigatewayv2_route" "root" {
  api_id    = aws_apigatewayv2_api.bff.id
  route_key = "ANY /"
  target    = "integrations/${aws_apigatewayv2_integration.bff.id}"

  # Same authorizer as the proxy route. Both, or neither is protected.
  authorization_type = "CUSTOM"
  authorizer_id      = aws_apigatewayv2_authorizer.bff.id
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.bff.id
  name        = "$default"
  auto_deploy = true

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.apigw.arn
    format = jsonencode({
      requestId      = "$context.requestId"
      httpMethod     = "$context.httpMethod"
      path           = "$context.path"
      status         = "$context.status"
      responseLength = "$context.responseLength"
      requestTime    = "$context.requestTime"
      sourceIp       = "$context.identity.sourceIp"
    })
  }

  tags = { component = "edge" }
}

resource "aws_cloudwatch_log_group" "apigw" {
  name              = "/aws/apigateway/${local.name}-bff"
  retention_in_days = 30

  tags = { component = "edge" }
}
