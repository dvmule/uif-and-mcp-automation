# GitHub Actions -> AWS trust for CI/CD. Same OIDC-federation mechanism as ../irsa
# (workload identity), applied to the CI/CD runner instead of a Pod: a workflow run
# presents its GitHub-issued OIDC token, AWS validates it against the account's
# GitHub OIDC provider (../github-oidc-provider), and only the exact repo+ref or
# repo+environment combinations listed below may assume this role. No long-lived
# AWS access keys are stored as a GitHub secret — this is what makes that possible.
# See ../../../../capabilities/infrastructure/guidelines.md and kubernetes.md.

locals {
  branch_subjects      = [for b in var.allowed_branches : "repo:${var.github_org}/${var.github_repo}:ref:refs/heads/${b}"]
  environment_subjects = [for e in var.allowed_environments : "repo:${var.github_org}/${var.github_repo}:environment:${e}"]
  allowed_subjects     = concat(local.branch_subjects, local.environment_subjects)
}

data "aws_iam_policy_document" "trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [var.oidc_provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = local.allowed_subjects
    }
  }
}

resource "aws_iam_role" "this" {
  name               = "${var.name}-gha-deploy"
  assume_role_policy = data.aws_iam_policy_document.trust.json
  tags               = var.tags
}

resource "aws_iam_role_policy_attachment" "managed" {
  for_each   = toset(var.policy_arns)
  role       = aws_iam_role.this.name
  policy_arn = each.value
}
