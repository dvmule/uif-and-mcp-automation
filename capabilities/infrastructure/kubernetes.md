# Kubernetes — deploy, sync & security (the runtime how-to)

Once the image exists ([`docker.md`](docker.md)), Kubernetes is how it runs. In UIF a domain packages its workload as a **Helm** chart under `domains/<domain>/infrastructure/helm/`, the cluster substrate (EKS, networking, IAM) is **Terraform** under `infrastructure/terraform/`, and the cluster is kept in step with git by **GitOps sync (Argo CD)**. This guide is the practical companion to `guidelines.md` §1–4 for the deploy: the hardened manifest shape, the cluster substrate, and the sync/promotion mechanism that gets an FDE from a merged PR to a running, observable workload.

Grounding: the Kubernetes API, the **Pod Security Standards** ("restricted" profile), the CIS Kubernetes Benchmark, **Helm 3**, and the **OpenGitOps** principles. All public.

## Substrate first (Terraform)

The account, VPC, EKS cluster, node groups, and per-workload IAM roles are declared as Terraform in `infrastructure/terraform/` — composed from the central base modules, never click-ops (`guidelines.md` §2). The one piece each workload needs is an **IRSA** role (IAM Roles for Service Accounts): a least-privilege IAM role bound to the workload's Kubernetes ServiceAccount, so the Pod gets exactly the AWS permissions it needs and nothing more — never node credentials, never cluster-admin.

## The hardened workload (Helm)

The chart lives in `infrastructure/helm/`; environments are `values-<env>.yaml` overlays, never forked charts. The Deployment encodes the Pod Security "restricted" profile from `guidelines.md` §4:
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: product-api
  labels:
    app.kubernetes.io/name: product-api
    app.kubernetes.io/part-of: product
    app.kubernetes.io/version: "1.4.2"
    app.kubernetes.io/managed-by: Helm
spec:
  replicas: {{ .Values.replicas }}          # >=2 in prod
  selector: { matchLabels: { app.kubernetes.io/name: product-api } }
  template:
    metadata:
      labels: { app.kubernetes.io/name: product-api, app.kubernetes.io/part-of: product }
    spec:
      serviceAccountName: product-api        # bound to an IRSA role (least privilege)
      securityContext:
        runAsNonRoot: true
        seccompProfile: { type: RuntimeDefault }
      containers:
        - name: api
          image: "{{ .Values.image.repository }}@{{ .Values.image.digest }}"  # pin by digest
          ports: [{ containerPort: 8080 }]
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities: { drop: ["ALL"] }
          resources:                          # requests AND limits — never unbounded
            requests: { cpu: "100m", memory: "128Mi" }
            limits:   { cpu: "500m", memory: "512Mi" }
          livenessProbe:  { httpGet: { path: /healthz,  port: 8080 } }
          readinessProbe: { httpGet: { path: /readyz,   port: 8080 } }
          envFrom:
            - secretRef: { name: product-api-secrets }   # from External Secrets, not git
```
Ship alongside it, in the same chart:
- a **NetworkPolicy** that is **default-deny**, with explicit ingress/egress allows;
- a **PodDisruptionBudget** and an **HPA** for availability and autoscaling;
- an **ExternalSecret** (External Secrets Operator) that pulls `product-api-secrets` from **Keeper** at runtime — the cluster never stores the plaintext, git never sees it (§ "Identity & secrets — non-negotiable" below).

## Identity & secrets — non-negotiable (Pearson platform)

> **Source: mandated by the platform owner, confirmed 2026-07-29.** Not yet cross-referenced against a Confluence page — per the repo's source-of-truth convention (Option C), this citation should be upgraded to a specific Confluence page/space once one is identified. Until then, treat this section as authoritative and Pearson-specific, not a public standard.

**Entra ID (identity provider), Keeper (secrets vault), and Entra Connect Sync (hybrid identity) are mandatory for every domain.** They are not a recommended pattern among alternatives — the mandate stands regardless of what CI currently checks.

> **What is actually enforced today, corrected 2026-09-11.** Only the **Keeper** half of the gate fires. A domain or stack storing secrets in a different vault fails CI — `.github/workflows/domain-ci.yml` and `.github/workflows/stacks-ci.yml`, both "Entra ID + Keeper gate".
>
> **The Entra ID check is disabled in both workflows** (`domain-ci.yml:112`, `stacks-ci.yml:153`, each marked *"Entra ID check disabled (PR #21, entraid_remove)"*), because **Entra ID has not yet been delivered to the programme** — there is nothing to check against. This is a tracked external dependency, not a lapse: **UIFPE-289**, which gates Gate 5 of the 30 October release (UIFPE-372).
>
> **Consequence for you now:** the Entra requirement below is a design obligation held by review, not a gate that will stop you. Build to it — `stacks/echo` runs a deliberate interim seam (`ui/src/lib/auth.ts`, one file, swappable on delivery) rather than a different IdP, and that is the pattern to copy if you need to ship before Entra lands. Anything shipping to production on an interim scheme needs the framework owner's written acceptance, recorded on UIFPE-289.
>
> Re-enable both checks as part of receiving Entra ID, and delete this note.

### Microsoft Entra ID

Every API's `openapi.yaml` must declare Entra ID's OIDC discovery endpoint as its security scheme — no other identity provider is permitted:
```yaml
components:
  securitySchemes:
    EntraID:
      type: openIdConnect
      openIdConnectUrl: https://login.microsoftonline.com/<tenant-id>/v2.0/.well-known/openid-configuration
security:
  - EntraID: [api://<app-registration-id>/product.read]
```
Validate the token's `iss`, `aud`, and required scopes on every request. Workload-to-workload calls federate the Pod's ServiceAccount token to an Entra ID App Registration via OIDC — no client secrets stored anywhere.

### Keeper (secrets vault)

No secret — database credential, API key, service password — may live in git, a Dockerfile, a `values-<env>.yaml`, or a ConfigMap. Every `ExternalSecret` must point at the Keeper `ClusterSecretStore`; no other secret store (AWS Secrets Manager, HashiCorp Vault, Azure Key Vault) is permitted:
```yaml
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: product-api-secrets
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: keeper-secrets-manager     # the only permitted ClusterSecretStore
    kind: ClusterSecretStore
  target:
    name: product-api-secrets
    creationPolicy: Owner
  data:
    - secretKey: DATABASE_URL
      remoteRef:
        key: "<keeper-record-uid>"
        property: database_url
```

### Entra Connect Sync (hybrid identity)

Entra Connect Sync replicates Pearson's on-prem Active Directory into Entra ID. Consequence for an FDE: a corporate AD group (e.g. `sg-product-fde-dev`) syncs into an Entra ID group, which drives K8s RBAC and API scopes automatically. **Never create an Entra ID-only account for a Pearson employee** — it will not sync back to AD and will be orphaned. This is a platform-level identity process, not a per-domain file, so it is not CI-checked — it is enforced organizationally.

## Validate before it merges

The same checks CI runs (`guidelines.md` §3) — run them locally:
```
helm lint infrastructure/helm
helm template infrastructure/helm | kubeconform -strict          # schema-valid manifests
helm template infrastructure/helm | conftest test -              # OPA policy: non-root, resources, no privileged, NetworkPolicy present
```
A chart that renders a privileged container, an unbounded resource, or a missing probe fails the gate.

## Sync & promote (GitOps with Argo CD)

Deployment is **declarative**: git holds the desired state, and **Argo CD** continuously reconciles the cluster to it and flags drift (the OpenGitOps model). **GitHub Actions** (the mandated CD tool, `../delivery/`) drives promotion by advancing the desired state per environment via the reusable `domain-cd.yml` workflow — it bumps the image **digest** and the `values-<env>.yaml` in the environment overlay; Argo CD then syncs that environment. An Argo CD `Application` points at the chart + the env overlay:
```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata: { name: product-uat, namespace: argocd }
spec:
  project: product
  source:
    repoURL: https://github.com/pearson-dnt-coe/universal-Integration-framework
    path: domains/product/infrastructure/helm
    helm: { valueFiles: ["values-uat.yaml"] }
  destination: { server: https://kubernetes.default.svc, namespace: product }
  syncPolicy:
    automated: { prune: true, selfHeal: true }     # auto-correct drift
```
Promotion path, gated at each step: **Development → UAT → Production**. Prod carries ≥2 replicas across AZs, a PDB, and rolling updates. After each sync: readiness green, a smoke test, and the SLO check (`../observability/`).

**Blue/green is an explicit, opt-in strategy — not what the Deployment above does.** The manifest above uses Kubernetes' default `RollingUpdate`: pods replaced gradually, one workload the whole time. A domain that needs instant cutover and instant rollback instead of a gradual replacement (e.g. a payment-adjacent API where a failed rollout mid-cutover is unacceptable) opts into true blue/green via **Argo Rollouts**: swap `kind: Deployment` for `kind: Rollout` with `strategy: { blueGreen: { activeService: <name>-active, previewService: <name>-preview, autoPromotionEnabled: false } }`. Argo CD syncs the `Rollout` the same way; promotion (cutting live traffic from active → preview) is then a separate, explicit gate in `domain-cd.yml` — a failed smoke test against the preview service means traffic never moves, and rollback is switching the active Service's selector back, not a redeploy. Default to `RollingUpdate`; reserve blue/green for domains that state this requirement explicitly in `DOMAIN-CHECKLIST.md`'s design section.

## Security checklist (before you sync)

- [ ] `securityContext`: runAsNonRoot, readOnlyRootFilesystem, allowPrivilegeEscalation=false, seccomp RuntimeDefault, caps drop ALL.
- [ ] Resource **requests and limits** on every container; liveness + readiness probes present.
- [ ] Image referenced by **digest**; admission verifies the cosign signature (`docker.md`).
- [ ] **NetworkPolicy default-deny** with explicit allows; ServiceAccount + **IRSA** least-privilege.
- [ ] Secrets via **Keeper** (`ClusterSecretStore: keeper-secrets-manager`) — none in the chart or git; no other vault permitted.
- [ ] API authentication via **Entra ID** OIDC; `iss`/`aud`/scopes validated; security scheme declared in `openapi.yaml`. No other identity provider permitted.
- [ ] `helm lint` + `kubeconform` + `conftest` pass; prod has replicas ≥2 + PDB + HPA.

## Where this lives in a domain

Everything above is authored in `domains/<domain>/infrastructure/` — `terraform/` (substrate) and `helm/` (workload); `observability/` for dashboards/alerts. Promotion runs from `.github/workflows/` (see `../delivery/`), not a domain-local pipeline file. Stamp a new domain with `../../create-domain.sh`. Back to the image: [`docker.md`](docker.md). The full standard: [`guidelines.md`](guidelines.md). CI/CD: `../delivery/`.
