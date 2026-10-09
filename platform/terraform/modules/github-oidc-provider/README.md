# modules/github-oidc-provider

Establishes the AWS account's trust in GitHub Actions as a federated OIDC identity issuer (`token.actions.githubusercontent.com`). This is a **one-per-AWS-account** resource, not a per-domain one — apply it once (e.g. from a platform/root Terraform root module), then every domain's `github-oidc-deploy-role` module (`../github-oidc-deploy-role/`) references its output.

Without this, no GitHub Actions workflow can legitimately assume an AWS role — there is nothing for AWS to validate the workflow's OIDC token against.

## Usage

```hcl
module "github_oidc" {
  source = "../../../../infrastructure/terraform/modules/github-oidc-provider"
}
```

Then, per domain:
```hcl
module "product_cd_role" {
  source            = "../../../../infrastructure/terraform/modules/github-oidc-deploy-role"
  oidc_provider_arn = module.github_oidc.provider_arn
  ...
}
```

Do not create a second provider for the same issuer URL in the same account — AWS rejects it, and there is only ever one GitHub Actions issuer to trust.
