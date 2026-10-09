# Delivery

## Purpose
CI/CD for the fabric — how modules are built, verified, and promoted to production. This capability defines the standard pipeline every module uses so that shipping is repeatable and gated on quality and security, not manual.

## Standards
- **CI — GitHub Actions:** build, test, security scan (`../security/`), and schema validation (`../schema/`) run on every PR; merge only on green.
- **CD — GitHub Actions:** promotion runs Development → UAT → Production as jobs in a reusable `workflow_call` workflow; each stage gated, Stage→Prod behind a GitHub `environment:` protection rule (manual approval).
- One root workflow, `../../.github/workflows/domain-cd.yml`, matrixed per touched domain — the same shape as `domain-ci.yml` for CI; no per-domain workflow file. **Scoped to `domains/` on Kubernetes, and not yet running** (UIFPE-378).
- **`stacks/` promotion is a separate, live standard:** [`stack-promotion-model.md`](stack-promotion-model.md) — bootstrap, infra converge, promote, bound by a build tag's release manifest. Proven through dev and uat on `stacks/apicurio/`.

## Governance
- **Central:** the CI/CD workflow standards (`../../.github/workflows/`), the promotion gates, the reusable `domain-cd.yml` workflow.
- **Local (FDE):** the domain's `infrastructure/` (Terraform, Helm values per environment) that the workflow reads — no local pipeline file.

## Patterns
- Trunk-based flow with PR-gated CI.
- Progressive promotion with automated gates per environment; the same image digest is promoted, never rebuilt per environment.
- **Plan/code sync:** commits and PRs auto-transition their Jira work item — see `./jira-github-integration.md`.

## Guidance
- **The worked standard:** [`guidelines.md`](guidelines.md) — naming a workflow/pipeline stage, the CI/CD contract every domain must satisfy (`domain-ci.yml`'s structural + Entra ID/Keeper gates), the canonical lint→...→publish→promote pipeline every other capability's own §3 plugs into, and what CI actually checks vs. what human PR review checks.
- Foundation Epic: `GitHub Actions Foundation` (`../../PLANNING.md`, Sprint 0) covers both CI and CD. Deployment is steps 12–14 of `../../architecture-design/operating-model/fde-build-deploy-workflow.md`.
