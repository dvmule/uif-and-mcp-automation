# api_gateway.tf
# HTTP API v2 that exposes pipeline_controller Lambda to the React UI.
# Uses Lambda proxy integration — all routing is handled inside the Lambda.
# JWT authorizer (Cognito) is optional: set enable_jwt_authorizer = true
# and supply cognito_user_pool_endpoint + cognito_user_pool_client_id.

# ── Variables ─────────────────────────────────────────────────────





# ── CloudWatch log group for API access logs ──────────────────────

resource "aws_cloudwatch_log_group" "api_access_logs" {
  name              = "/aws/apigateway/${local.name_prefix}"
  retention_in_days = 30

  tags = {
    Project     = local.project_tag
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

# ── HTTP API ──────────────────────────────────────────────────────

resource "aws_apigatewayv2_api" "ingestion" {
  name          = "${local.name_prefix}-api"
  protocol_type = "HTTP"
  description   = "EDS pipeline config, trigger, and monitoring API"

  cors_configuration {
    allow_origins = var.cors_allow_origins
    allow_methods = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
    allow_headers = ["Content-Type", "Authorization"]
    max_age       = 300
  }

  tags = {
    Project     = local.project_tag
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

# ── Lambda proxy integration ──────────────────────────────────────

resource "aws_apigatewayv2_integration" "pipeline_controller" {
  api_id                 = aws_apigatewayv2_api.ingestion.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.pipeline_controller.invoke_arn
  payload_format_version = "2.0"
}

# ── JWT authorizer (optional) ─────────────────────────────────────

resource "aws_apigatewayv2_authorizer" "cognito" {
  count            = var.enable_jwt_authorizer ? 1 : 0
  api_id           = aws_apigatewayv2_api.ingestion.id
  authorizer_type  = "JWT"
  identity_sources = ["$request.header.Authorization"]
  name             = "cognito-jwt"

  jwt_configuration {
    issuer   = var.cognito_user_pool_endpoint
    audience = [var.cognito_user_pool_client_id]
  }
}

locals {
  # Resolves to the authorizer ID when enabled, or null when disabled.
  # Routes reference this local so toggling the variable protects all routes at once.
  authorizer_id = var.enable_jwt_authorizer ? aws_apigatewayv2_authorizer.cognito[0].id : null
}

# ── Routes ────────────────────────────────────────────────────────
# All routes share the same Lambda integration.
# authorization_type = "JWT" when authorizer is enabled, "NONE" otherwise.

# Interim UI login (pre-SSO). authorization_type is hardcoded "NONE" ON PURPOSE:
# every other route below resolves through local.authorizer_id, and if this one
# did too, enabling the authorizer would require a token in order to get a
# token. The handler checks the credential against secret echo/{env}/ui/login.
# Delete this route when Pearson SSO lands.
resource "aws_apigatewayv2_route" "auth_login" {
  api_id             = aws_apigatewayv2_api.ingestion.id
  route_key          = "POST /auth/login"
  target             = "integrations/${aws_apigatewayv2_integration.pipeline_controller.id}"
  authorization_type = "NONE"
}

resource "aws_apigatewayv2_route" "create_pipeline" {
  api_id             = aws_apigatewayv2_api.ingestion.id
  route_key          = "POST /pipelines"
  target             = "integrations/${aws_apigatewayv2_integration.pipeline_controller.id}"
  authorization_type = local.authorizer_id != null ? "JWT" : "NONE"
  authorizer_id      = local.authorizer_id
}

resource "aws_apigatewayv2_route" "list_pipelines" {
  api_id             = aws_apigatewayv2_api.ingestion.id
  route_key          = "GET /pipelines"
  target             = "integrations/${aws_apigatewayv2_integration.pipeline_controller.id}"
  authorization_type = local.authorizer_id != null ? "JWT" : "NONE"
  authorizer_id      = local.authorizer_id
}

resource "aws_apigatewayv2_route" "get_pipeline" {
  api_id             = aws_apigatewayv2_api.ingestion.id
  route_key          = "GET /pipelines/{pipeline_id}"
  target             = "integrations/${aws_apigatewayv2_integration.pipeline_controller.id}"
  authorization_type = local.authorizer_id != null ? "JWT" : "NONE"
  authorizer_id      = local.authorizer_id
}

resource "aws_apigatewayv2_route" "update_pipeline" {
  api_id             = aws_apigatewayv2_api.ingestion.id
  route_key          = "PUT /pipelines/{pipeline_id}"
  target             = "integrations/${aws_apigatewayv2_integration.pipeline_controller.id}"
  authorization_type = local.authorizer_id != null ? "JWT" : "NONE"
  authorizer_id      = local.authorizer_id
}

resource "aws_apigatewayv2_route" "toggle_pipeline" {
  api_id             = aws_apigatewayv2_api.ingestion.id
  route_key          = "PATCH /pipelines/{pipeline_id}/toggle"
  target             = "integrations/${aws_apigatewayv2_integration.pipeline_controller.id}"
  authorization_type = local.authorizer_id != null ? "JWT" : "NONE"
  authorizer_id      = local.authorizer_id
}

resource "aws_apigatewayv2_route" "trigger_run" {
  api_id             = aws_apigatewayv2_api.ingestion.id
  route_key          = "POST /pipelines/{pipeline_id}/run"
  target             = "integrations/${aws_apigatewayv2_integration.pipeline_controller.id}"
  authorization_type = local.authorizer_id != null ? "JWT" : "NONE"
  authorizer_id      = local.authorizer_id
}

resource "aws_apigatewayv2_route" "list_runs" {
  api_id             = aws_apigatewayv2_api.ingestion.id
  route_key          = "GET /pipelines/{pipeline_id}/runs"
  target             = "integrations/${aws_apigatewayv2_integration.pipeline_controller.id}"
  authorization_type = local.authorizer_id != null ? "JWT" : "NONE"
  authorizer_id      = local.authorizer_id
}

resource "aws_apigatewayv2_route" "trigger_analysis" {
  api_id             = aws_apigatewayv2_api.ingestion.id
  route_key          = "POST /analyze"
  target             = "integrations/${aws_apigatewayv2_integration.pipeline_controller.id}"
  authorization_type = local.authorizer_id != null ? "JWT" : "NONE"
  authorizer_id      = local.authorizer_id
}

resource "aws_apigatewayv2_route" "trigger_async_analysis" {
  api_id             = aws_apigatewayv2_api.ingestion.id
  route_key          = "POST /analyze-async"
  target             = "integrations/${aws_apigatewayv2_integration.pipeline_controller.id}"
  authorization_type = local.authorizer_id != null ? "JWT" : "NONE"
  authorizer_id      = local.authorizer_id
}

resource "aws_apigatewayv2_route" "get_async_analysis" {
  api_id             = aws_apigatewayv2_api.ingestion.id
  route_key          = "GET /analyze-async/{run_id}"
  target             = "integrations/${aws_apigatewayv2_integration.pipeline_controller.id}"
  authorization_type = local.authorizer_id != null ? "JWT" : "NONE"
  authorizer_id      = local.authorizer_id
}

resource "aws_apigatewayv2_route" "get_run" {
  api_id             = aws_apigatewayv2_api.ingestion.id
  route_key          = "GET /pipelines/{pipeline_id}/runs/{run_id}"
  target             = "integrations/${aws_apigatewayv2_integration.pipeline_controller.id}"
  authorization_type = local.authorizer_id != null ? "JWT" : "NONE"
  authorizer_id      = local.authorizer_id
}

resource "aws_apigatewayv2_route" "list_connections" {
  api_id             = aws_apigatewayv2_api.ingestion.id
  route_key          = "GET /connections"
  target             = "integrations/${aws_apigatewayv2_integration.pipeline_controller.id}"
  authorization_type = local.authorizer_id != null ? "JWT" : "NONE"
  authorizer_id      = local.authorizer_id
}

resource "aws_apigatewayv2_route" "list_gcs_connections" {
  api_id             = aws_apigatewayv2_api.ingestion.id
  route_key          = "GET /connections/gcs"
  target             = "integrations/${aws_apigatewayv2_integration.pipeline_controller.id}"
  authorization_type = local.authorizer_id != null ? "JWT" : "NONE"
  authorizer_id      = local.authorizer_id
}

resource "aws_apigatewayv2_route" "list_silver_bq_connections" {
  api_id             = aws_apigatewayv2_api.ingestion.id
  route_key          = "GET /connections/silver-bq"
  target             = "integrations/${aws_apigatewayv2_integration.pipeline_controller.id}"
  authorization_type = local.authorizer_id != null ? "JWT" : "NONE"
  authorizer_id      = local.authorizer_id
}

# UIFPE-543: gold notifications. Subscribers are rows in the gold root's
# alert-subscribers table (self-service per table from the console); the
# rules are the gold CloudWatch alarms, read-only. Replaces GET/PUT /alerts.
resource "aws_apigatewayv2_route" "list_alert_subscribers" {
  api_id             = aws_apigatewayv2_api.ingestion.id
  route_key          = "GET /notifications/subscribers"
  target             = "integrations/${aws_apigatewayv2_integration.pipeline_controller.id}"
  authorization_type = local.authorizer_id != null ? "JWT" : "NONE"
  authorizer_id      = local.authorizer_id
}

resource "aws_apigatewayv2_route" "add_alert_subscriber" {
  api_id             = aws_apigatewayv2_api.ingestion.id
  route_key          = "POST /notifications/subscribers"
  target             = "integrations/${aws_apigatewayv2_integration.pipeline_controller.id}"
  authorization_type = local.authorizer_id != null ? "JWT" : "NONE"
  authorizer_id      = local.authorizer_id
}

resource "aws_apigatewayv2_route" "set_alert_subscriber" {
  api_id             = aws_apigatewayv2_api.ingestion.id
  route_key          = "PATCH /notifications/subscribers/{email}"
  target             = "integrations/${aws_apigatewayv2_integration.pipeline_controller.id}"
  authorization_type = local.authorizer_id != null ? "JWT" : "NONE"
  authorizer_id      = local.authorizer_id
}

resource "aws_apigatewayv2_route" "remove_alert_subscriber" {
  api_id             = aws_apigatewayv2_api.ingestion.id
  route_key          = "DELETE /notifications/subscribers/{email}"
  target             = "integrations/${aws_apigatewayv2_integration.pipeline_controller.id}"
  authorization_type = local.authorizer_id != null ? "JWT" : "NONE"
  authorizer_id      = local.authorizer_id
}

resource "aws_apigatewayv2_route" "list_alert_rules" {
  api_id             = aws_apigatewayv2_api.ingestion.id
  route_key          = "GET /notifications/rules"
  target             = "integrations/${aws_apigatewayv2_integration.pipeline_controller.id}"
  authorization_type = local.authorizer_id != null ? "JWT" : "NONE"
  authorizer_id      = local.authorizer_id
}

resource "aws_apigatewayv2_route" "delete_pipeline" {
  api_id             = aws_apigatewayv2_api.ingestion.id
  route_key          = "DELETE /pipelines/{pipeline_id}"
  target             = "integrations/${aws_apigatewayv2_integration.pipeline_controller.id}"
  authorization_type = local.authorizer_id != null ? "JWT" : "NONE"
  authorizer_id      = local.authorizer_id
}

# ── Gold pipeline routes (see ADR-0001) ──────────────────────────

resource "aws_apigatewayv2_route" "create_gold_pipeline" {
  api_id             = aws_apigatewayv2_api.ingestion.id
  route_key          = "POST /gold-pipelines"
  target             = "integrations/${aws_apigatewayv2_integration.pipeline_controller.id}"
  authorization_type = local.authorizer_id != null ? "JWT" : "NONE"
  authorizer_id      = local.authorizer_id
}

resource "aws_apigatewayv2_route" "list_gold_pipelines" {
  api_id             = aws_apigatewayv2_api.ingestion.id
  route_key          = "GET /gold-pipelines"
  target             = "integrations/${aws_apigatewayv2_integration.pipeline_controller.id}"
  authorization_type = local.authorizer_id != null ? "JWT" : "NONE"
  authorizer_id      = local.authorizer_id
}

resource "aws_apigatewayv2_route" "get_gold_pipeline" {
  api_id             = aws_apigatewayv2_api.ingestion.id
  route_key          = "GET /gold-pipelines/{gold_id}"
  target             = "integrations/${aws_apigatewayv2_integration.pipeline_controller.id}"
  authorization_type = local.authorizer_id != null ? "JWT" : "NONE"
  authorizer_id      = local.authorizer_id
}

resource "aws_apigatewayv2_route" "update_gold_pipeline" {
  api_id             = aws_apigatewayv2_api.ingestion.id
  route_key          = "PUT /gold-pipelines/{gold_id}"
  target             = "integrations/${aws_apigatewayv2_integration.pipeline_controller.id}"
  authorization_type = local.authorizer_id != null ? "JWT" : "NONE"
  authorizer_id      = local.authorizer_id
}

resource "aws_apigatewayv2_route" "delete_gold_pipeline" {
  api_id             = aws_apigatewayv2_api.ingestion.id
  route_key          = "DELETE /gold-pipelines/{gold_id}"
  target             = "integrations/${aws_apigatewayv2_integration.pipeline_controller.id}"
  authorization_type = local.authorizer_id != null ? "JWT" : "NONE"
  authorizer_id      = local.authorizer_id
}

resource "aws_apigatewayv2_route" "toggle_gold_pipeline" {
  api_id             = aws_apigatewayv2_api.ingestion.id
  route_key          = "PATCH /gold-pipelines/{gold_id}/toggle"
  target             = "integrations/${aws_apigatewayv2_integration.pipeline_controller.id}"
  authorization_type = local.authorizer_id != null ? "JWT" : "NONE"
  authorizer_id      = local.authorizer_id
}

# UIFPE-513: the one console-owned schedule field. Its own literal segment,
# like /toggle, so the PUT/DELETE {gold_id} branches never see it.
resource "aws_apigatewayv2_route" "set_gold_start_time" {
  api_id             = aws_apigatewayv2_api.ingestion.id
  route_key          = "PATCH /gold-pipelines/{gold_id}/schedule"
  target             = "integrations/${aws_apigatewayv2_integration.pipeline_controller.id}"
  authorization_type = local.authorizer_id != null ? "JWT" : "NONE"
  authorizer_id      = local.authorizer_id
}

resource "aws_apigatewayv2_route" "trigger_gold_run" {
  api_id             = aws_apigatewayv2_api.ingestion.id
  route_key          = "POST /gold-pipelines/{gold_id}/run"
  target             = "integrations/${aws_apigatewayv2_integration.pipeline_controller.id}"
  authorization_type = local.authorizer_id != null ? "JWT" : "NONE"
  authorizer_id      = local.authorizer_id
}

resource "aws_apigatewayv2_route" "list_gold_runs" {
  api_id             = aws_apigatewayv2_api.ingestion.id
  route_key          = "GET /gold-runs"
  target             = "integrations/${aws_apigatewayv2_integration.pipeline_controller.id}"
  authorization_type = local.authorizer_id != null ? "JWT" : "NONE"
  authorizer_id      = local.authorizer_id
}

# The fleet Overview (UIFPE-544). Its own collection, not under
# /gold-pipelines: it is fleet-wide, and a literal segment there would shadow
# the {gold_id} variable.
resource "aws_apigatewayv2_route" "gold_stats_overview" {
  api_id             = aws_apigatewayv2_api.ingestion.id
  route_key          = "GET /gold-stats"
  target             = "integrations/${aws_apigatewayv2_integration.pipeline_controller.id}"
  authorization_type = local.authorizer_id != null ? "JWT" : "NONE"
  authorizer_id      = local.authorizer_id
}

# ── Catalog routes ────────────────────────────────────────────────

resource "aws_apigatewayv2_route" "get_catalog_lineage" {
  api_id             = aws_apigatewayv2_api.ingestion.id
  route_key          = "GET /catalog/lineage"
  target             = "integrations/${aws_apigatewayv2_integration.pipeline_controller.id}"
  authorization_type = local.authorizer_id != null ? "JWT" : "NONE"
  authorizer_id      = local.authorizer_id
}

# ── Operations Dashboard (Home) routes ────────────────────────────

resource "aws_apigatewayv2_route" "home_summary" {
  api_id             = aws_apigatewayv2_api.ingestion.id
  route_key          = "GET /home/summary"
  target             = "integrations/${aws_apigatewayv2_integration.pipeline_controller.id}"
  authorization_type = local.authorizer_id != null ? "JWT" : "NONE"
  authorizer_id      = local.authorizer_id
}

resource "aws_apigatewayv2_route" "home_recent_activity" {
  api_id             = aws_apigatewayv2_api.ingestion.id
  route_key          = "GET /home/recent-activity"
  target             = "integrations/${aws_apigatewayv2_integration.pipeline_controller.id}"
  authorization_type = local.authorizer_id != null ? "JWT" : "NONE"
  authorizer_id      = local.authorizer_id
}

# ── Default stage (auto-deploy) ───────────────────────────────────

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.ingestion.id
  name        = "$default"
  auto_deploy = true

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.api_access_logs.arn
    format = jsonencode({
      requestId        = "$context.requestId"
      sourceIp         = "$context.identity.sourceIp"
      requestTime      = "$context.requestTime"
      httpMethod       = "$context.httpMethod"
      routeKey         = "$context.routeKey"
      status           = "$context.status"
      protocol         = "$context.protocol"
      responseLength   = "$context.responseLength"
      integrationError = "$context.integrationErrorMessage"
    })
  }

  tags = {
    Project     = local.project_tag
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

# ── Outputs ───────────────────────────────────────────────────────


