# apicurio

The Apicurio Registry deployment, plus the governance layer in front of it —
the enforced, single source of truth Schema Registry First (`standards/`) and
the Apicurio guardrail governance layer (`standards/apicurio-governance.md`,
[ADR-0011](../../architecture-design/decisions/0011-apicurio-schema-governance.md))
both depend on. Nothing else in this repo runs the registry today; this is
where that changes.

**This is a deployable stack, not a domain — and not a RELAY stack either.**
`echo`, `beacon`, and `harbor` are RELAY's three application stacks
(each rebuilding its own API + Event + Schema Registry integration on every
release — see `../harbor/README.md`). Apicurio is a fourth, separate kind
of `stacks/` member: cross-cutting platform infrastructure that every
domain and every RELAY stack depends on, not a RELAY application in its
own right. It sits at `stacks/apicurio/` for the same reason Harbor and
Beacon do — it is an independently deployable unit, not a schema-owning
business domain — but don't read it as a fourth RELAY stack.

## Why this exists as its own top-level folder

Checked before building: `capabilities/infrastructure/` holds only
standards/reference material (`docker.md`, `kubernetes.md`, one example
IAM policy) — no repo capability folder has ever held real, applied
Terraform, and putting Apicurio's deployment there would have broken that
rule for the first time. The alternative — steward-hosting it inside an
existing domain the way Gold's shared scripts live in `domains/product/`
(UIFPE-122) — was considered and rejected: Apicurio has no conceptual
relationship to product's business domain, and would have been a worse
fit than Gold-in-product already is. A new `stacks/` member, mirroring
the shape `stacks/beacon/infrastructure/terraform/` already proves works,
was the option that broke no existing rule.

## Status

**Infrastructure, BFF, UI and standards — none of it deployed yet.**

The BFF, the dashboard and the Schema capability's standards moved here from
`capabilities/schema/` on 2026-09-07
([ADR-0012](../../architecture-design/decisions/0012-schema-registry-moves-to-stacks-apicurio.md)),
which retired the ADR-0006 exception that had put executable code under
`capabilities/`. This stack is now the single home for the registry and
everything that fronts it.

| Piece | State |
|---|---|
| `infrastructure/terraform/` | ECS Fargate + ALB + RDS + IAM, plus the folded-in BFF, API Gateway and CloudFront. `fmt` and `validate` pass. **Never applied.** |
| `src/` | FastAPI BFF, Python 3.12. `pytest` 119 passed / 6 skipped. |
| `ui/` | React 18 + Vite dashboard. `vitest` 134 passed, production build clean. |
| `standards/` | The Schema capability's standards — `guidelines.md`, `apicurio-governance.md`, `gold-attribute-mapping.*`. 82 files across the repo point here. |
| `qe/` | The BFF suite and the convention notes for both suites. |

**Not deployed anywhere**: `envs/dev.tfvars` still carries placeholder
VPC/subnet IDs that need a real account survey before the first
`terraform apply` (see `infrastructure/terraform/envs/dev.tfvars`).

> **Two things to know before deploying this.**
>
> **There is no authentication anywhere in the stack.** No API Gateway
> authorizer, no auth interceptor in the UI, and `APICURIO_AUTH_ENABLED`
> unset on the registry. The public HTTP API proxies `ANY /{proxy+}`
> straight to the BFF. Anything that can reach the URL can read and write
> the registry's governance surface. Pre-existing, carried across
> unchanged, and the highest-severity open gap.
>
> **The CMDB is a mock.** There is no ServiceNow client. Every owner, team,
> email and application the dashboard shows is fixture data, presented
> without caveat in the UI.
>
> Both are tracked in `ARCHITECTURE.md` "Known gaps", which is the full
> list rather than these two.

`.github/workflows/deploy-apicurio-dev.yml` exists — mirrors
`deploy-beacon-dev.yml`'s resolve/gate shape, repo variable
`APICURIO_INFRA` (unset|skip|plan|apply) — but is **not yet safe to run**:
even a `plan` fails against the placeholder VPC/subnet IDs, since
Terraform has to look the VPC up to read its CIDR block. Survey the real
IDs first. It also covers infrastructure only; the BFF image and the UI
bundle still need a manual ECR push and S3 sync.

Once live, exactly three things unlock the governance layer with no code
changes on the CI side — `.github/workflows/contract-governance.yml` and
`generate-sdks.yml` are already written to wait for them:

1. `APICURIO_REGISTRY_URL` repo variable → this stack's ALB DNS name (Terraform output `registry_url`)
2. `GLUE_REGISTRY_NAME` repo variable → a Glue Schema Registry (not yet provisioned — a separate, much smaller Terraform addition; AWS Glue Schema Registry is serverless, no compute of its own)
3. `AWS_ROLE_ARN_SCHEMA_GOVERNANCE` secret → an OIDC deploy role for the `register-and-sync` CI job

## Component map

| Piece | Where | Runs as |
|---|---|---|
| Registry container | `infrastructure/terraform/ecs.tf` | ECS Fargate service (image: `apicurio/apicurio-registry:3.3.2` — **v3**, see below) |
| Registry load balancer | `infrastructure/terraform/alb.tf` | Internal ALB (default) |
| Storage backend | `infrastructure/terraform/rds.tf` | RDS Postgres, credentials in Secrets Manager |
| Governance BFF | `src/` → `infrastructure/terraform/bff.tf` | ECS Fargate service on the same cluster, behind its own internal ALB |
| Public edge | `infrastructure/terraform/apigw.tf` | API Gateway HTTP API + VPC Link → the BFF's internal ALB |
| Dashboard | `ui/` → `infrastructure/terraform/frontend.tf` | Static bundle in a private S3 bucket, served by CloudFront with origin access control |
| IAM | `infrastructure/terraform/iam.tf` | Separate execution/task roles per container — the off-the-shelf registry does not get the BFF's SSM access |
| Schema standards | `standards/` | Documentation. Not deployed. |

**The registry is Apicurio v3.** The image is `apicurio/apicurio-registry`,
*not* `apicurio-registry-sql` — that variant was a v2-era split whose newest
tag is `2.6.13.Final`, so the `apicurio-registry-sql:3.0.6` this stack pinned
before ADR-0012 was an image that does not exist. v3 consolidated to one
image selecting its backend via `APICURIO_STORAGE_KIND`. The BFF client
targets `/apis/registry/v3` accordingly; `/apis/registry/v2` does not exist
on a v3 server.

## Working on this stack

### Code

```bash
cd stacks/apicurio
pytest                                  # BFF — 119 passed, 6 skipped

cd ui
npm ci && npx vitest run                # dashboard — 134 passed
npm run build                           # tsc -b && vite build
```

Layering is enforced by review, not tooling, and is the thing most often got
wrong here: `src/app/api/v1/` → `src/app/services/` → `src/app/clients/`, one
direction only. Services never import `httpx` or FastAPI. In the UI, data
flows `service` → TanStack Query hook → component — never `fetch` in a
component, and server state never enters Zustand. `CLAUDE.md` has the full
set; `ARCHITECTURE.md` explains why each boundary exists.

### Infrastructure

Nothing is deployed yet. Before the first `terraform apply`:
1. Survey the real VPC/subnet IDs for the target account (`ReadOnly`
   credentials are enough), the same way
   `stacks/beacon/infrastructure/terraform/envs/dev.tfvars` documents doing
   for Beacon — replace the `vpc-UPDATE-ME`/`subnet-UPDATE-ME-*`
   placeholders in `infrastructure/terraform/envs/dev.tfvars`.
2. `cd infrastructure/terraform && terraform init -backend-config=backends/dev.hcl`
3. `terraform plan -var-file=envs/dev.tfvars` — **review the plan before
   ever running apply**; nothing here is wired to a CI deploy workflow
   yet, so a plan/apply today is a manual, human-run action.
4. Push the BFF image built from `src/Dockerfile` to the ECR repository the
   apply creates (`bff_ecr_repository_url`), then force a new ECS deployment.
5. Build the UI, write `dist/config.json` from the `bff_api_url` output, then
   sync `ui/dist` to the `ui_bucket` output and invalidate `/index.html` and
   `/config.json`. The origin is fetched at boot rather than compiled in, so
   the bundle itself does not depend on step 3 — only the `config.json` written
   beside it does.

## Ownership (Four-in-a-Box)

Not yet assigned — no FDE/UX/QE/PM has been named. Assign before the
first real `terraform apply`, per the same model every domain and stack
uses.
