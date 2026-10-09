# Infrastructure

## Purpose
The runtime substrate every domain deploys onto — AWS, Kubernetes, Terraform, Docker, and Helm. This capability defines the standard way a domain declares, builds, secures, and provisions its runtime, so that infrastructure is code, reproducible, hardened, and consistent across the fabric. Every domain builds exactly **one container image** (the single build/deploy contract) and runs it on Kubernetes; everything that runs the image lives under `domains/<domain>/infrastructure/`.

## Standards
- **Cloud Native / Container First:** every domain is containerised (`src/Dockerfile`) and runs on Kubernetes.
- **Infrastructure as Code, committed before it exists:** cloud substrate as **Terraform** under `infrastructure/terraform/`; workload as a **Helm** chart under `infrastructure/helm/`; no click-ops.
- **Hardened by default:** minimal/distroless base pinned by digest, non-root, Pod Security "restricted", NetworkPolicy default-deny, secrets externalized.
- **Supply chain integrity:** images are scanned (Trivy), have an SBOM (Syft), and are signed (cosign); admission verifies the signature.
- **Mandated substrate:** AWS · Kubernetes · Terraform · Docker · Helm; **GitHub Actions** (CI and CD), **Argo CD** (GitOps sync).

## Governance
- **Central:** account/cluster topology, base Terraform modules, network and cost guardrails, admission policy.
- **Local (FDE):** a domain's own `terraform/` and `helm/` within those guardrails.

## Patterns
- Per-domain IaC composed from shared base Terraform modules.
- One chart with `values-<env>.yaml` overlays — never forked per environment.
- Environment promotion Development → UAT → Production: GitHub Actions advances the desired state, Argo CD reconciles the cluster (`../delivery/`).

## Guidance
The worked standard and its two tool-specific how-tos — read them in order:
- [`guidelines.md`](guidelines.md) — the detailed do-this standard (Naming · IaC declaration · Testing→build · Security & operability · Definition of done).
- [`docker.md`](docker.md) — set up your machine, write the standard multi-stage non-root Dockerfile, build/scan/sign/push the one image.
- [`kubernetes.md`](kubernetes.md) — the hardened Helm workload, cluster substrate (Terraform + IRSA), and GitOps sync/promote with Argo CD.

## The deploy role's permissions — one grant per environment

CI deploys as the environment's GitHub Actions role, so anything a stack
creates must be permitted for that role.
That grant for dev is two documents, both attached to `echo-dev-github-actions`
and both scoped by **environment** (`echo-dev-*` and `uif-*-dev*` in
`eu-west-1`) and by service, never by module:

- [`echo-dev-deploy-1.policy.json`](echo-dev-deploy-1.policy.json) — messaging:
  SQS, SNS, EventBridge buses/rules/connections/API destinations, Pipes, Kinesis.
- [`echo-dev-deploy-2.policy.json`](echo-dev-deploy-2.policy.json) — everything
  else: secrets, stack buckets and their objects, metric alarms, role lifecycle
  and `PassRole`, service-linked roles, Lake Formation tagging, Transfer Family
  connectors.

Each is a tracked copy of the live managed policy of the same name. They were
one policy, `echo-dev-deploy`, until UIFPE-360: a managed policy is capped at
6,144 characters and it had reached 6,062 with all five versions used, so a new
grant could not be added at all. IAM evaluates attached policies as one union of
Allow statements, so splitting by service family changed nothing the role can
do — it only made room. Add a new grant to whichever document its service family
belongs to. The retired `echo-dev-deploy` is detached but kept in the account as
the rollback; do not extend it.

**A module never adds its own deploy policy.** Per-module policies scoped to a
module's own name prefix are what produced the confusing failure this rule
exists to prevent: the role holds a permission, the next module's resources are
named differently, and AWS reports the action as unauthorised. Ask for these
documents to be extended instead, and derive the actions from the Terraform
(create + read-back + update + delete + tag per resource — the provider reads
every attribute back, so a bucket needs `Get*` calls it never writes), checking
each against the live role with `aws iam simulate-principal-policy` before
merging.

Each environment gets its own document when it arms (`echo-uat-*`,
`echo-prod-*`); the dev one is the template, and prod's is a separate review.
Older module-scoped policies (`uif-beacon-deploy-1/-2`) still exist and still
work — folding them in is a cleanup, not a prerequisite.

Everything above is authored into a domain under `domains/<domain>/infrastructure/` (`terraform/`, `helm/`, `observability/`); promotion runs from `.github/workflows/` (`../delivery/`), not a domain-local pipeline file. Stamp a new domain with `../../create-domain.sh`. Runnable Dockerfiles that follow this shape: `../../delivery-lab/reference-implementations/api/developer-registration-api/Dockerfile` (Node) and `../../delivery-lab/reference-implementations/api/developer-registration-api-fastapi/Dockerfile` (Python).
