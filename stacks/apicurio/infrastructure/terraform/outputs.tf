output "registry_url" {
  description = "Value to set as the APICURIO_REGISTRY_URL repo variable (.github/workflows/contract-governance.yml, generate-sdks.yml)."
  value       = "http://${aws_lb.registry.dns_name}"
}

# Named rather than reconstructed in the workflow. deploy-apicurio-dev.yml
# hard-codes ECR_REPO because it must work before any state exists to read; the
# smoke job has no such constraint -- it runs after an apply -- so it reads
# these instead of duplicating the "${local.name}-..." convention a third time.
output "bff_service_name" {
  description = "ECS service name for the BFF, for `aws ecs wait services-stable`."
  value       = aws_ecs_service.bff.name
}

output "registry_service_name" {
  description = "ECS service name for the registry, for `aws ecs wait services-stable`."
  value       = aws_ecs_service.registry.name
}

output "ecs_cluster_name" {
  value = aws_ecs_cluster.this.name
}

output "db_secret_arn" {
  description = <<-DESC
    ARN of the RDS-managed master-user secret the registry task reads. The
    name is AWS's (rds!db-<resource-id>) and cannot be set, so this output is
    the only way to find it without going through the RDS console. Not
    sensitive -- it is an identifier, not the credential.
  DESC
  value       = aws_db_instance.this.master_user_secret[0].secret_arn
}

# ── BFF + UI (UIFPE-36) ───────────────────────────────────────────────────────

output "bff_api_url" {
  description = <<-DESC
    Public API Gateway endpoint for the BFF. Written into the UI bundle's
    config.json at publish time (../../ui), fetched by the browser at boot, so
    this must exist before the UI is PUBLISHED — the build itself no longer
    depends on it.
  DESC
  value       = aws_apigatewayv2_api.bff.api_endpoint
}

output "bff_alb_dns_name" {
  description = "Internal ALB in front of the BFF. In-VPC callers only."
  value       = aws_lb.bff.dns_name
}

output "bff_ecr_repository_url" {
  description = "Push the image built from ../../src/Dockerfile here."
  value       = aws_ecr_repository.bff.repository_url
}

output "ui_bucket" {
  description = "Sync the built UI bundle (../../ui/dist) to this bucket."
  value       = aws_s3_bucket.frontend.bucket
}

output "ui_url" {
  description = "CloudFront URL for the governance UI."
  value       = "https://${aws_cloudfront_distribution.frontend.domain_name}"
}

output "ui_distribution_id" {
  description = "Invalidate /index.html here after a UI deploy."
  value       = aws_cloudfront_distribution.frontend.id
}

# ── Phase 1 auth stop-gap (authorizer.tf) ─────────────────────────────────────

output "authorizer_ci_secret_name" {
  description = <<-DESC
    Secrets Manager secret holding the CI shared secret for the API Gateway
    authorizer. Terraform creates the CONTAINER ONLY -- there is no
    aws_secretsmanager_secret_version, deliberately, so the value never enters
    Terraform state or a saved plan. Until an operator sets it by hand the
    header credential path does not work, and only that path: source-IP
    allowlisted callers are unaffected.

      aws secretsmanager put-secret-value --secret-id <this> \
        --secret-string "$(openssl rand -hex 32)"

    Then give the same value to CI as the `X-UIF-CI-Key` request header (see
    ../../DEPLOY-DEV.md Phase 5, the smoke job). Not sensitive -- this is the
    secret's name, not its value.
  DESC
  value       = aws_secretsmanager_secret.authorizer_ci.name
}

output "authorizer_ci_secret_arn" {
  description = "ARN of the same secret, for an IAM statement or an `aws secretsmanager` call that wants the ARN. An identifier, not the credential."
  value       = aws_secretsmanager_secret.authorizer_ci.arn
}

output "authorizer_ci_header" {
  description = "Request header the authorizer reads the CI shared secret from. A source-IP allowlisted caller sends nothing; this header is the second credential path only."
  value       = local.authorizer_ci_header
}

output "release_artifacts_bucket" {
  description = "Bucket holding each release's UI bundle archive, keyed by content hash."
  value       = aws_s3_bucket.release_artifacts.id
}
