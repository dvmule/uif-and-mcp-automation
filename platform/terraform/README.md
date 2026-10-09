# infrastructure/terraform — central base modules

Reusable Terraform modules that every domain's own `domains/<domain>/infrastructure/terraform/` composes from (`../../capabilities/infrastructure/kubernetes.md` "Substrate first — composed from the central base modules, never click-ops"). This is central, shared code — the domain owns its own module *call* (which policies, which namespace); it never re-implements the module itself.

- [`modules/irsa/`](modules/irsa/) — IAM Roles for Service Accounts: the least-privilege binding every UIF workload needs between its Kubernetes ServiceAccount and its AWS permissions.
- [`modules/github-oidc-provider/`](modules/github-oidc-provider/) — one-per-account trust in GitHub Actions as an OIDC issuer.
- [`modules/github-oidc-deploy-role/`](modules/github-oidc-deploy-role/) — the least-privilege binding a domain's CI/CD workflow needs between its GitHub Actions run (scoped by repo+branch/environment) and its AWS permissions; no long-lived AWS keys stored in GitHub.

## Adding a module here

Per `CLAUDE.md` "No speculative abstraction": a module lands here only once a real domain needs it — IRSA and the GitHub OIDC deploy role both came from `product`'s actual workload/CD identity requirements, not a hypothetical. Don't pre-build a module for a capability no domain has exercised yet.
