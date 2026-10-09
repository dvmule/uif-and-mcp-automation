output "provider_arn" {
  description = "ARN of the GitHub Actions IAM OIDC provider — pass this to every github-oidc-deploy-role module."
  value       = aws_iam_openid_connect_provider.github.arn
}
