# modules/github-oidc-deploy-role

Reusable Terraform module: an IAM role that only specific GitHub Actions workflow runs — identified by exact repo+branch or repo+environment, never a wildcard — may assume via OIDC, with no long-lived AWS access keys stored anywhere in GitHub. This is the CI/CD-runner analogue of `../irsa` (which scopes a *workload's* AWS identity); this module scopes the *pipeline's* AWS identity.

## Usage

```hcl
module "product_cd_role" {
  source               = "../../../../infrastructure/terraform/modules/github-oidc-deploy-role"
  name                 = "product"
  oidc_provider_arn    = module.github_oidc.provider_arn   # ../github-oidc-provider, applied once per account
  github_org           = "pearson-dnt-coe"
  github_repo          = "universal-Integration-framework"
  allowed_branches     = ["main"]           # push-to-main triggers Dev/QA/Stage
  allowed_environments = ["production"]     # only the Prod job, gated by the GitHub Environment's manual approval
  policy_arns          = [aws_iam_policy.product_terraform_apply.arn, aws_iam_policy.product_ecr_push.arn]
  tags                 = { domain = "product" }
}
```

Consumer wires the output into the workflow:
```yaml
permissions:
  id-token: write   # required for GitHub to mint the OIDC token
  contents: read
steps:
  - uses: aws-actions/configure-aws-credentials@v4
    with:
      role-to-assume: <module.product_cd_role.role_arn>
      aws-region: us-east-1
```

**Scoping rule:** `policy_arns` must be least-privilege — the exact Terraform state/ECR repo/EKS namespace this domain owns, never a broad or account-wide policy. A compromised workflow run should only ever be able to touch its own domain's resources.

Worked instance: `domains/product/infrastructure/terraform/`.
