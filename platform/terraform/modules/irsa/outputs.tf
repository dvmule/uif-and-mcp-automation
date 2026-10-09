output "role_arn" {
  description = "ARN of the IRSA role — annotate the Kubernetes ServiceAccount with eks.amazonaws.com/role-arn set to this value."
  value       = aws_iam_role.this.arn
}

output "role_name" {
  value = aws_iam_role.this.name
}
