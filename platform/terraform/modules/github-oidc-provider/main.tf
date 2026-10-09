# One IAM OIDC provider per AWS account, trusting GitHub Actions as a federated
# identity issuer. This is the CI/CD-runner analogue of the EKS cluster's own OIDC
# provider consumed by ../irsa — same federation mechanism, different issuer, and
# it establishes the trust that every domain's github-oidc-deploy-role role uses.
# Apply this once per AWS account/environment; do not create it per domain.

data "tls_certificate" "github" {
  url = "https://token.actions.githubusercontent.com"
}

resource "aws_iam_openid_connect_provider" "github" {
  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = [data.tls_certificate.github.certificates[0].sha1_fingerprint]
}
