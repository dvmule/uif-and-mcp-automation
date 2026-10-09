> **Superseded by [ADR-0012](0012-schema-registry-moves-to-stacks-apicurio.md) (2026-09-07).** The service no longer lives under `capabilities/`. It moved to `stacks/apicurio/`, which removes the need for the rule-2 exception this ADR argued for — `stacks/` did not exist as a category when this was written (it arrived with [ADR-0009](0009-gold-placement.md) and [ADR-0010](0010-beacon-migration-plan.md)) and is precisely the home this ADR could not find. The reasoning below is preserved as written: it is the record of why the exception was taken and what was weighed, and ADR-0012 depends on it. Do not act on its location decision.

> **Amended by [ADR-0007](0007-remove-oxigraph-knowledge-graph.md) (2026-08-10).** The RDF Knowledge Graph described below as part of this service — Oxigraph, the `g:*` named graphs, the extractors, the SPARQL passthrough, the Twin explorer, and D3 drift — was subsequently **removed**, and two Terraform modules were renamed (`ecs-apicurio` → `apicurio-rds`, `ecs-bff` → `apicurio-bff`). Descriptions of the service's scope and the module inventory below are preserved **as written at decision time**; read ADR-0007 for what the service is now. The location decision this ADR makes is unaffected.

# ADR-0006 — The Pearson Schema Registry service lives under `capabilities/schema/`, not `domains/`

**Status:** Accepted
**Date:** 2026-08-10
**Decider:** Rajnivas Karunakaran (repository owner)
**Scope:** Where the Pearson Schema Registry — the running platform service (BFF, web app, AWS hosting) — lands in this repository, migrated from the standalone `pearson-schema-registry` repo.
**Numbering:** 0006. Follows ADR-0005; unrelated to the Gold-layer sequence (0001, 0003, 0004, 0005).

---

## Context

The Pearson Schema Registry is Wave 1 of the Enterprise Digital Twin programme. It is a real, deployed service: a FastAPI BFF and rich UI in front of a self-hosted Apicurio Registry, mirroring all schema state into an RDF Knowledge Graph (Oxigraph) and exposing it to humans (the UI) and to the Business Assurance Agent (SPARQL). Wave 1 (W1-S1…S8) is complete; Wave 2 is in flight. It needed to move into this repository.

**The repository rules do not provide a home for it.** Three rules collide:

| Rule | Source | Effect here |
|---|---|---|
| 1. All implementation code lives under `domains/<domain>/` (or `delivery-lab/reference-implementations/`) | `CLAUDE.md` rule 1, `README.md` "the rule that resolves most 'where does this go' questions" | The seven domains are *business units* — `product`, `customer`, `hr`, `legal`, `finance`, `assessment`, `identity`. A registry serving all seven is not one of them. |
| 2. Capability folders never contain executable code | `CLAUDE.md` rule 2, `capabilities/README.md` | Closes `capabilities/schema/` to the service. |
| 4. `delivery-lab/reference-implementations/` is runnable but **teaching-only, not deployed** | `CLAUDE.md` rule 4 | The registry is a live service the BAA consumes. Filing it here would misdescribe it, and it would receive no CI — `domain-ci.yml` triggers only on `domains/**`. |

The gap is structural, not incidental. **Every capability's "domain folder" in `capabilities/README.md` is where a domain *consumes* the standard** — Schema maps to `domains/<d>/src/schemas/`, which is where a domain keeps *its registered contracts*. None of the thirteen capabilities has a slot for *the shared platform service that implements the capability centrally*.

Yet UIF already asserts that this service is central, in three places:

- `capabilities/schema/README.md` — Governance: "**Central: the registry itself**, compatibility policy, schema naming."
- `capabilities/knowledge-graph/README.md` — Governance: "**Central: the graph store**, the ingestion pattern, the query interface." (The Oxigraph twin.)
- `capabilities/api/guidelines.md` §"Schema registry tooling: Apicurio" — "Apicurio Registry… **is the concrete tool behind the `../schema/` capability's Schema Registry First principle**."
- `README.md` and `capabilities/docs/capability-domain-jira-map.md` — "Foundation Epics exist only for platform-wide groundwork that has no domain yet — e.g. *stand up the Schema Registry*." Tracked as UIFPE-36 (S0-1) under the Schema Foundation Epic, deliberately **not** parented to a Domain epic.

So the service's *governing capability* is unambiguous (Schema). Only its *location* was undecided.

## Decision

**The registry service lands under `capabilities/schema/` in three folders: `api/`, `webapp/`, `infra/`.**

| Folder | Migrated from | Contents |
|---|---|---|
| `capabilities/schema/api/` | `pearson-schema-registry/backend/` | FastAPI BFF — `app/{api,services,clients,schemas,extractors,core}`, `tests/`, `Dockerfile`, `pyproject.toml` |
| `capabilities/schema/webapp/` | `pearson-schema-registry/frontend/` | React + Vite governance dashboard — `src/{pages,features,components,services,lib}`, `e2e/`, `Dockerfile` |
| `capabilities/schema/infra/` | `pearson-schema-registry/terraform/` + `cloudformation/` | Terraform modules (`ecs-apicurio`, `ecs-bff`, `ecs-oxigraph`, `s3-frontend`, `apigw-bff`, `ecs-cdc-poller`, `bootstrap`) + `cloudformation/ecs-cluster.yaml` |

**This is an explicit, scoped exception to rule 2 — not a repeal of it.** It applies to the Schema Registry service alone, on the grounds that it *is* the central realization of this capability. For every other capability, and for every domain's own schemas, the rule is unchanged: the standard lives in `capabilities/`, the code lives in `domains/<domain>/src/`.

### Alternatives rejected

| Option | Why not |
|---|---|
| **`domains/platform/`** — an eighth domain | Cheapest *rule-compliant* path: inherits `domain-ci.yml` path filters, the one-image contract, `Jenkinsfile`, and Jira's Domain-epic-as-owning-parent rule for free. Rejected because "domains are deployable, ownable **business units**" and a shared registry is not one; adding a non-business eighth domain would dilute the definition that makes the Domain-epic parent rule unambiguous. **This remains the strongest candidate if the exception above is ever revisited.** |
| **New top-level `platform/`** | Most honest description, but a brand-new top-level category (`CLAUDE.md` "Before creating structure" requires confirmation) *and* new CI workflows, since `domain-ci.yml` matrices only over `domains/**`. Larger structural change than the exception it avoids. |
| **`delivery-lab/reference-implementations/schema/`** | The only option needing zero new structure, but rule 4 defines that folder as teaching-only and not deployed. Filing a live, BAA-consumed service there misdescribes it and yields no CI. |
| **Split across the seven domains** | It is one service with one datastore. Not viable. |

## Consequences

### Easier

- The standard and its reference implementation sit together. An FDE reading *Schema Registry First* can see the enforcement code — the 9 metadata rules in `api/app/services/metadata_validator.py` — beside the rule.
- No new top-level category, and the seven-domain definition stays clean.
- Jira alignment is already correct: UIFPE-36 sits under the Schema Foundation Epic with no `domain:*` label, which is exactly what this location expresses.

### Harder — accepted, with follow-up required

1. **Rule 2 is no longer absolute.** Mitigated by scoping the exception to this service, documenting it in `capabilities/schema/README.md`, and requiring a superseding ADR to widen it. The risk is precedent: a future agent or engineer reading only the folder tree may infer capability folders are a general code home. Both the README and this ADR state they are not.
2. **CI needed its own workflow — done.** `.github/workflows/domain-ci.yml` triggers on `domains/**` only and matrices over domain folder names, so it can never cover this path. Resolved by [`.github/workflows/schema-registry-ci.yml`](../../.github/workflows/schema-registry-ci.yml): pytest (236 tests), vitest (165) + `npm run build`, `terraform fmt`/`validate` per root module, hadolint + docker build + Trivy + Syft, and a gate asserting this ADR still exists and is linked from `capabilities/schema/README.md`. Two knowingly non-blocking parts, both with a documented path to blocking: `ruff` (185 pre-existing findings, 31 of them `B008` false positives from FastAPI's `Depends()`) and Trivy `HIGH` (README's chain specifies CRITICAL/HIGH; CRITICAL blocks today, HIGH reports until the `python:3.12-slim` base-image backlog is triaged). Release steps (ECR push, cosign) are omitted pending consequence 3.
3. **Hosting does not conform to the mandated platform.** `infra/` is **ECS/Fargate + CloudFormation**; UIF mandates **EKS + Helm + Argo CD + Jenkins** (`capabilities/infrastructure/kubernetes.md`, `README.md` CD section). ECS and CloudFormation appear nowhere in `capabilities/infrastructure/`. Porting is a rewrite of the deploy layer, deferred to a follow-up story — not a folder move.
4. **The one-image contract does not hold.** Three artifacts: the `api` container, the `webapp` static bundle (S3 + CloudFront), and the `ecs-cdc-poller` module. `webapp` could fold into the `api` image (FastAPI serving built assets) if the one-image contract is applied here.
5. **`infra/terraform/ecs-cdc-poller/` is dangling — and provably broken.** The Terraform module migrated; the `cdc-poller/` source did not (it sits outside the api/webapp/infra mapping and is undocumented in the source repo's own `CLAUDE.md`). `ecr.tf` resolves `filemd5("${path.module}/../../cdc-poller/Dockerfile")`, which no longer exists, so the module **fails `terraform validate`** — confirmed, and it is the one module CI skips. Note the relative path lands at `capabilities/schema/cdc-poller/` under the new layout, so migrating the source there would fix it without editing the module. Otherwise delete it. No other module references a sibling source directory.
6. **Secrets and state were deliberately excluded.** Live local `terraform.tfstate` (7 modules, plus 6 timestamped rotations under `ecs-cdc-poller`), `terraform.tfvars`, `.env`, and `.env.local` were **not** migrated — state can carry plaintext secrets and its authoritative home is the S3 backend + DynamoDB lock table provisioned by `infra/terraform/bootstrap/`. `*.tfvars.example` **was** migrated. The root `.gitignore` was hardened (`terraform.tfstate*`, `*.tfvars`, `.env*`) so this cannot recur.

### Standards still to extract

The migration moved code. The source repo's `docs/` also holds material that is *genuinely* standards-layer and belongs in capability folders under the original rule — not yet done:

- Schema naming convention (`<domain>.<entity>.<event>-value`, `-key`, `.v2`) and the 12 domain names → `capabilities/schema/naming.md` (this folder's Governance already names "schema naming" as central).
- Metadata block spec (`owner`/`domain`/`dataClassification`/`piiFields`/`lifecycle`) + the 9 validation rules → `capabilities/schema/metadata-block.md`.
- Canonical IRI bases, named graphs, and the `/v1` ontology migration rule → `capabilities/knowledge-graph/` + `capabilities/semantics/`.
- ~~The SPARQL query library (`docs/sparql-queries/`) → `capabilities/knowledge-graph/`.~~ Moot — see ADR-0007.
- Source-repo ADR-0001 (schema body authoritative, Apicurio `properties` derived) → this folder as **ADR-0008**. (Source-repo ADR-0002, the Oxigraph dev server, is moot — see ADR-0007. Note 0007 is taken by the removal itself; this list originally reserved 0007/0008 for these two.)
