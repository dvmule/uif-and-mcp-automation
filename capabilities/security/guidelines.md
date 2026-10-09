# Security — Guidelines (the worked standard)

This is the detailed, do-this companion to the Security capability `README.md`. It tells a Forward Deployed Engineer exactly how to name a classification tier and a security scope, document a resource's field-level access contract, test that access controls actually hold, and what the CI scanning gate enforces — so "secure by default" is a checked property of every module, not a claim in a README.

This document follows the same **template shape** as every capability's `guidelines.md`: five sections — Naming, Contract documentation, Testing → automation → build, Standards & guidelines, Definition of done. **This document does not restate identity/secrets tooling already mandated in `../infrastructure/kubernetes.md` §"Identity & secrets"** (Entra ID as the only permitted IdP, Keeper as the only permitted secret store) — link, don't duplicate. What's covered here and not there: the **classification scheme** and **field-level access control**, which apply above the network/secret layer, to the data itself.

Grounding standards (all public): the **OWASP Application Security Verification Standard (ASVS)** (control baseline by verification level), **NIST SP 800-207** (Zero Trust Architecture — verify explicitly, least privilege, assume breach), OAuth 2.1/OIDC (the scope model), and the CIS Benchmarks referenced by `../infrastructure/docker.md`/`kubernetes.md` for the container/cluster layer.

Worked example: `../../domains/hr/src/schemas/hr-employee.classification.json` (field-level classification for HR's most sensitive record) and `../../domains/hr/infrastructure/kubernetes/` (a real default-deny `NetworkPolicy` + Keeper-backed `ExternalSecret` for the HR API).

---

## 1. Naming

**Classification tiers.** Exactly four, lowercase: `public`, `internal`, `confidential`, `restricted` — the same scheme `../data/guidelines.md` §2 uses for a data contract's `classification` field. Security and Data share one scheme; a capability does not invent its own.

**Security scopes (OAuth 2.1/OIDC).** `<domain>.<resource>.<action>[.<tier>]`, matching the scope pattern already shown in `../infrastructure/kubernetes.md` (`api://.../product.read`) and extended with an optional tier suffix when a scope must gate a classification, not just a resource: `hr.employee.read`, `hr.employee.read.restricted`.

**Secrets.** `<domain>-<purpose>-secrets`, matching the existing convention (`product-api-secrets`).

**IAM / IRSA roles and ServiceAccounts.** `<domain>-<workload>`, matching the existing convention (`product-api`) — one role per workload, least privilege, never a shared cluster-wide role.

---

## 2. Contract documentation (classification and access are declared, not inferred)

**A resource's sensitive fields are documented once, centrally to the resource, as a classification manifest** — not left for each consumer to guess from the field name. Kept as `<resource>.classification.json` in `domains/<domain>/src/schemas/` (or alongside the data contract it classifies, `../data/guidelines.md` §2).

**A minimal worked example** (the smallest classification manifest that satisfies every rule above):
```json
{
  "resource": "hr.employee",
  "fields": [
    { "name": "employeeId",   "classification": "internal" },
    { "name": "name",         "classification": "internal" },
    { "name": "salary",       "classification": "confidential", "requiredScope": "hr.employee.read.confidential" },
    { "name": "ssn",          "classification": "restricted",  "requiredScope": "hr.employee.read.restricted" }
  ]
}
```
The full version is `../../domains/hr/src/schemas/hr-employee.classification.json`; read the snippet above as its smallest possible slice.

**The API enforces the manifest, it does not restate it.** `openapi.yaml`'s `security` requirement for an operation returning `confidential`/`restricted` fields lists the matching scope (§1) — the classification manifest is the source of truth the API's declared scopes must match, checked in CI (§3).

**Network and secret controls are declared per the infrastructure standard, not here.** A `NetworkPolicy` and `ExternalSecret` for a domain follow `../infrastructure/kubernetes.md` exactly — this document only adds the classification layer on top.

---

## 3. Testing → automation → build

1. **Classification manifest validation** — every field in a registered data contract/schema has a classification entry; `confidential`/`restricted` fields declare a `requiredScope`.
2. **Scope-to-schema consistency check** — every operation in `openapi.yaml` that returns a `confidential`/`restricted` field lists the matching scope in its `security` requirement (§2); a mismatch fails the build, not a manual review.
3. **Access-control tests** — a token missing the required scope gets `403` (RFC 7807 Problem, `../api/guidelines.md` §2) when calling an operation that would return a classified field; a token with only `hr.employee.read` never sees a `restricted` field's real value.
4. **Static/dependency/container scanning (DevSecOps gate, `../delivery/`).** SAST (e.g. CodeQL/Semgrep-class tooling), dependency scanning, secret-leak scanning (e.g. gitleaks/trufflehog-class tooling) on every PR; container/IaC scanning per `../infrastructure/docker.md` (Trivy, hadolint) and `../infrastructure/kubernetes.md` (Conftest/OPA, checkov). A failing scan fails the build — no severity is waved through silently.
5. **Zero Trust smoke check** — a call from outside the declared `NetworkPolicy` allow-list is rejected; a call with a valid but out-of-scope token is rejected — both verified against a deployed environment, not assumed from the manifest alone.

**Automation & build gate (GitHub Actions, at repo root — `../delivery/`).** On every PR touching `domains/<domain>/**`:
```
lint  →  SAST + dependency + secret scan  →  classification/scope consistency check  →  access-control tests  →  container + IaC scan (../infrastructure/)  →  build
```

---

## 4. Standards & guidelines (non-negotiable design inputs)

- **Secure By Default.** Every operation is authenticated and authorised; deny by default, least privilege — at the network layer (`NetworkPolicy`, `../infrastructure/kubernetes.md`) and at the field layer (classification scopes, §2).
- **DevSecOps.** Security scanning runs in CI (`../delivery/`); a failing scan fails the build — this is not a separate, optional pipeline.
- **Secrets are never in code or images.** Enforced via Keeper + External Secrets Operator per `../infrastructure/kubernetes.md` — restated here only as the rule, not the mechanism.
- **Classification is set at contract creation** (`../data/guidelines.md` §4), never retrofitted after an access incident, and it is what a consumer's scope request is checked against.
- **Zero Trust.** No implicit trust from network position alone — a request inside the cluster still carries and is checked against a token; "internal" traffic is not exempt.
- **Applies equally to agent modules (`../agent/`, `../mcp/`).** An MCP tool or agent that can return a classified field is bound by the same scope requirement as an API operation returning it.

---

## 5. Definition of done (Security)

- [ ] Classification tiers and scopes follow §1 (`public`/`internal`/`confidential`/`restricted`; scope pattern `<domain>.<resource>.<action>[.<tier>]`).
- [ ] Resource carries a classification manifest per §2; API's declared scopes match it (checked in CI, §3).
- [ ] Access-control tests prove a missing/insufficient scope is actually rejected, not just documented (§3).
- [ ] SAST, dependency, secret, container, and IaC scans all pass and are gates, not warnings (§3–4).
- [ ] `NetworkPolicy`, Entra ID auth, and Keeper-backed secrets follow `../infrastructure/kubernetes.md` unchanged.
- [ ] CI green: lint → security scans → classification/scope consistency → access-control tests → container/IaC scan → build (§3).
- [ ] Maps to a Jira Story (`capability:security` + its `domain:*`) per `../docs/capability-domain-jira-map.md`.

---

Worked instance: `../../domains/hr/src/schemas/hr-employee.classification.json` + `../../domains/hr/infrastructure/kubernetes/` (NetworkPolicy + ExternalSecret, HR's first real infra artifacts). Identity/secrets mechanics: `../infrastructure/kubernetes.md` §"Identity & secrets". Applies equally to `../agent/` and `../mcp/`.
