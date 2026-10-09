output "role_arn" {
  description = "ARN of the deploy role — pass this to aws-actions/configure-aws-credentials' role-to-assume input in the domain's CD workflow."
  value       = aws_iam_role.this.arn
}

output "role_name" {
  value = aws_iam_role.this.name
}
