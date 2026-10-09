# Infrastructure — Guidelines (the worked standard)

This is the detailed, do-this companion to the Infrastructure capability `README.md`. It tells a Forward Deployed Engineer exactly how to name, declare, test, build, secure, and deploy the runtime substrate for a domain — the container image, the cloud resources, and the Kubernetes workload — using globally recognised, publicly verifiable standards so the result is identifiable to any engineer, inside or outside UIF. Read it once; then follow the two tool-specific companions it points to: [`docker.md`](docker.md) (the image) and [`kubernetes.md`](kubernetes.md) (the deploy + GitOps sync).

This document follows the same **template shape** as every capability's `guidelines.md` (see `../api/guidelines.md`): five sections — Naming, Declaration/config documentation, Testing → automation → build, Standards & guidelines, Definition of done — so the whole fabric reads the same way.

Grounding standards (all public): OCI Image & Distribution Spec, Dockerfile reference + CIS Docker Benchmark, NIST SP 800-190 (container security), the Kubernetes API and Pod Security Standards + CIS Kubernetes Benchmark, Helm 3, Terraform, Semantic Versioning 2.0.0, SLSA (supply-chain integrity), SPDX / CycloneDX (SBOM), Sigstore/cosign (signing), the OpenGitOps principles, and the Twelve-Factor App.

UIF mandates a fixed substrate (`../../CLAUDE.md`): **AWS · Kubernetes · Terraform · Docker · Helm**, with **GitHub Actions** for both CI and CD. The enterprise cloud-native toolchain layered on top is the industry-standard, CNCF-aligned set: **Trivy** (scan), **Syft** (SBOM), **cosign** (sign), **hadolint/tflint/checkov** (lint & policy), **kubeconform + Conftest/OPA** (manifest validation & policy), **External Secrets Operator** (secrets), and **Argo CD** (GitOps sync). The same substrate hosts the AI capabilities (Agent, MCP, A2A) — it is AI-workload-ready, not a separate stack.

---

## 1. Naming

Predictable names make an image, a resource, and a workload traceable back to the domain that owns them.

**Container image.** One image per domain (the build contract). Repository `<registry>/<domain>[-<component>]`, all lowercase (OCI requires it), kebab-case, no version in the name.
- Good: `<acct>.dkr.ecr.eu-west-1.amazonaws.com/product`, `…/product-api`.
- Bad: `Product_API`, `myimage:latest` as a deploy target.

**Image tags (SemVer 2.0.0, immutable).** Tag every build with the version **and** the git sha; never deploy a mutable tag.
- `product:1.4.2` and `product:sha-9f54945`. `latest` is for humans browsing, never for a manifest.

**Kubernetes namespace.** One per domain: `<domain>` on a per-environment cluster, or `<domain>-<env>` on a shared cluster. kebab-case.

**Helm release & chart.** Release `<domain>`; chart `<domain>`. Standard labels on every object: `app.kubernetes.io/name`, `app.kubernetes.io/part-of=<domain>`, `app.kubernetes.io/version`, `app.kubernetes.io/managed-by=Helm`.

**Terraform.** Modules and resources `<domain>-<resource>`; remote state keyed by `<domain>/<env>`. Tag **every** cloud resource: `domain`, `env`, `owner`, `cost-center`, `managed-by=terraform`.

---

## 2. Declaration / config documentation (Infrastructure as Code First)

Infrastructure is declared as code and committed **before** it exists — no click-ops. The committed config is the single source of truth; the cluster and the cloud account are reconciled *to* it, never edited underneath it.

**Three artifacts, one home** — all under `domains/<domain>/infrastructure/`:
| Artifact | Tool | Path | Documents |
|---|---|---|---|
| Image | Dockerfile | `../src/Dockerfile` | how the one image is built ([`docker.md`](docker.md)) |
| Cloud substrate | Terraform | `infrastructure/terraform/` | account/network/EKS/data resources |
| Workload | Helm chart | `infrastructure/helm/` | how the image runs on Kubernetes ([`kubernetes.md`](kubernetes.md)) |
| Promotion | GitHub Actions | `.github/workflows/` (reusable `domain-cd.yml`) | Development → UAT → Production (`../delivery/`) |

**Pin everything for reproducibility.** Base image by **digest** (`@sha256:…`), Terraform `required_providers` versions, Helm chart dependency versions, Kubernetes `apiVersion`. A build must be repeatable a year later.

**Environments are overlays, not copies.** One chart with `values-<env>.yaml` (or Kustomize overlays); one Terraform module with per-env `.tfvars`. Never fork the manifest per environment.

**Every variable is documented.** Terraform `variable` blocks carry a `description`; Helm `values.yaml` keys are commented; no unexplained magic numbers.

**Domain infra vs. stack infra — a domain composes, it does not re-provision.** A domain's own `infrastructure/terraform/` should hold two things only: a parameterized **module call** into whichever stack owns the shared engine its capability rides on, and this domain's own genuinely one-off consumer-facing surfaces (a serving API, a read Lambda). It should never hand-roll a resource shape another domain already needs unchanged — that is the signal the shape belongs in a shared module instead. The real, working example is `stacks/event/infrastructure/terraform/modules/event-domain`: onboarding a new domain to eventing means instantiating that module with the domain's own name and paths, not writing a new queue/DLQ/alarm from scratch — see `stacks/event/README.md`'s own stated invariant, *"onboarding a new domain changes NOTHING in this stack; everything per-domain is data or a naming convention."* The same foundation-vs-domain split governs the medallion pipeline (Bronze/Silver/Gold is `stacks/echo`'s; a domain's gold schema, mapping and its own serving surfaces are the domain's) — see [ADR-0018](../../architecture-design/decisions/0018-gold-runtime-is-a-stack-concern.md) for the general test: *would a second domain need this exact thing, unchanged?* If yes, it's the stack's; if it encodes this domain's own data or consumers, it's the domain's.

**Shared, cross-domain platform primitives live at `platform/terraform/modules/`** (OIDC federation, IRSA — reuse them before writing the equivalent by hand in a domain's own Terraform; promote a domain-local pattern there only after a second domain needs the identical shape, not speculatively).

---

## 3. Testing → automation → build

Infrastructure is tested and gated exactly like application code. Nothing merges red; nothing deploys unscanned or unsigned.

1. **Lint** — `hadolint` (Dockerfile), `terraform fmt -check` + `tflint`, `helm lint`.
2. **Build** — `docker build` with BuildKit, multi-stage, from the pinned base. The **one image** is the artifact.
3. **Scan** — `trivy image` (CVEs) and `trivy config` / `checkov` (IaC misconfig). Fail the build on HIGH/CRITICAL.
4. **SBOM** — `syft` emits an SPDX/CycloneDX bill of materials, attached to the image.
5. **Sign (supply chain, SLSA + Sigstore)** — `cosign sign` the image digest; record provenance. Admission policy later verifies the signature so an unsigned image cannot run.
6. **Validate manifests** — `helm template | kubeconform` (schema) and `conftest`/OPA policy (non-root, resources set, no privileged, NetworkPolicy present).
7. **Plan** — `terraform validate` + `terraform plan` on the PR; `apply` only after merge, gated.

**CI gate (GitHub Actions, repo root — `../delivery/`).** On any PR touching `domains/<domain>/**`:
```
lint → build image → trivy scan → syft SBOM → cosign sign → terraform plan → helm template + kubeconform + policy → publish image
```

**Deploy & sync.** **GitHub Actions** (the reusable `domain-cd.yml` workflow) orchestrates promotion Development → UAT → Production. The actual cluster apply is **GitOps**: the workflow advances the desired state (the image digest / values in the environment overlay) and **Argo CD** reconciles the cluster to it — declarative, auto-drift-corrected, auditable. After each promotion: readiness probes green, a smoke test, and the SLO check (`../observability/`).

---

## 4. Standards & guidelines (security & operability are design inputs, not a review)

**Image (NIST SP 800-190, CIS Docker).** Minimal or distroless base pinned by digest; a non-root `USER` (`runAsNonRoot`); read-only root filesystem; drop all Linux capabilities; one process; a `HEALTHCHECK`; a `.dockerignore`; **no secrets baked into any layer**. Detail in [`docker.md`](docker.md).

**Pod & cluster (Pod Security Standards "restricted", CIS Kubernetes).**
- `securityContext`: `runAsNonRoot: true`, `readOnlyRootFilesystem: true`, `allowPrivilegeEscalation: false`, `seccompProfile: RuntimeDefault`, `capabilities.drop: [ALL]`.
- **Resource requests and limits on every container** — never unbounded.
- **Liveness, readiness, startup probes** on every workload.
- **NetworkPolicy default-deny**, with explicit ingress/egress allows.
- One **ServiceAccount** per workload, bound to a least-privilege IAM role via **IRSA** — never cluster-admin, never node credentials.

**Secrets.** Never in git, never in an image layer. Pull at runtime from AWS Secrets Manager / SSM via the **External Secrets Operator**; rotate on a schedule.

**Supply chain (SLSA, Sigstore).** Sign images; **verify signatures at admission** (Kyverno/Gatekeeper); keep provenance. An unsigned or unscanned image is not admitted.

**Availability & cost.** Prod runs ≥2 replicas across AZs with a `PodDisruptionBudget` and rolling updates; autoscale with an `HPA`; right-size from real usage; the mandatory resource tags make spend attributable.

**Observability & 12-Factor.** Config from the environment, stateless process, logs to stdout as JSON, a metrics endpoint scraped by Prometheus, traces via OpenTelemetry (`../observability/`).

---

## 5. Definition of done (Infrastructure)

- [ ] Names follow §1 (image, tags immutable + git-sha, namespace, release, tagged Terraform resources).
- [ ] All infra declared as code under `domains/<domain>/infrastructure/`; base image + providers + chart deps pinned (§2).
- [ ] Image: multi-stage, non-root, minimal base, scanned clean (no HIGH/CRITICAL), SBOM produced, **cosign-signed** (§3, §4, `docker.md`).
- [ ] Helm: `lint` + `kubeconform` + policy pass; `securityContext`, resources, probes, and `NetworkPolicy` present (§4, `kubernetes.md`).
- [ ] Secrets externalized; ServiceAccount + IRSA least-privilege; no click-ops (§4).
- [ ] CI green: lint → build → scan → SBOM → sign → terraform plan → manifest policy (§3); CD promotes via GitHub Actions; Argo CD reconciles the cluster.
- [ ] One SLO defined and checked post-deploy (`../observability/`).
- [ ] Maps to a Jira Story (`capability:infrastructure` + its `domain:*`) per `../docs/capability-domain-jira-map.md`.

---

Deploy the result into a domain: the image, chart, and Terraform all live in `domains/<domain>/infrastructure/` (stamp a new domain with `../../create-domain.sh`). Tool-specific how-to: [`docker.md`](docker.md) and [`kubernetes.md`](kubernetes.md). Golden-path narrative and CI/CD standard: `../delivery/`.
