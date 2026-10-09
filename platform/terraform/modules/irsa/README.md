# modules/irsa

Reusable Terraform module: an IAM role trusted only by one Kubernetes ServiceAccount, via the cluster's OIDC provider. Every domain's own `infrastructure/terraform/` composes from this module instead of hand-writing the trust policy per workload (`../../../capabilities/infrastructure/kubernetes.md` "Substrate first").

## Usage

```hcl
module "product_api_irsa" {
  source                = "../../../../infrastructure/terraform/modules/irsa"
  name                  = "product-api"
  oidc_provider_arn     = var.cluster_oidc_provider_arn
  oidc_provider_url     = var.cluster_oidc_provider_url
  namespace             = "product"
  service_account_name  = "product-api"
  policy_arns           = [aws_iam_policy.product_api_s3_read.arn]
  tags                  = { domain = "product" }
}
```

Consumer wires the output into the Helm chart's ServiceAccount:
```yaml
serviceAccount:
  annotations:
    eks.amazonaws.com/role-arn: <module.product_api_irsa.role_arn>
```

Worked instance: `domains/product/infrastructure/terraform/`.
