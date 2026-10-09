# ADR-0012 — The Schema Registry service moves to `stacks/apicurio/`, and the Schema standards move with it

**Status:** Accepted
**Date:** 2026-09-07
**Decider:** Rajnivas Karunakaran (repository owner)
**Scope:** Where the Pearson Schema Registry service and the Schema capability's standards live; which Apicurio major version the stack runs; and the shape of its Terraform.
**Supersedes:** [ADR-0006](0006-schema-registry-under-capabilities.md) — the rule-2 exception that put executable code under `capabilities/schema/`.
**Relates to:** [ADR-0007](0007-remove-oxigraph-knowledge-graph.md) (the service's scope), [ADR-0009](0009-gold-placement.md) and [ADR-0010](0010-beacon-migration-plan.md) (which established `stacks/`), [ADR-0011](0011-apicurio-schema-governance.md) (the governance layer that consumes this registry), [ADR-0001](0001-gold-layer.md) (unrelated numbering lineage).
**Numbering:** 0012. Follows ADR-0011.

---

## Context

ADR-0006 put the registry service under `capabilities/schema/{api,webapp,infra}` as an explicit, scoped exception to repository rule 2 (*"`capabilities/` and `architecture-design/` never contain executable code"*). It did so because no existing category fit: the thirteen domains are business units, `delivery-lab/reference-implementations/` is teaching-only and undeployed, and a new top-level `platform/` was a larger structural change than the exception it would avoid. Its own rejected-alternatives table named `domains/platform/` as the strongest fallback "if the exception is ever revisited."

**Three things changed since.**

1. **`stacks/` now exists.** ADR-0009 and ADR-0010 established `stacks/<name>/` for units that are independently deployable but *not* schema-owning business domains, and migrated Beacon out of `domains/` on exactly that reasoning. This is the category ADR-0006 needed and could not find. `stacks-ci.yml` was built to cover it before Beacon moved, so the CI gap that ruled out other options is also closed.

2. **`stacks/apicurio/` was already created** (UIFPE-36 / ADR-0011) as an infrastructure-only stack — Terraform for ECS Fargate, an ALB and RDS — to stand up the registry the governance layer depends on. Its `README.md` stated *"No `src/`, `ui/`, or `qe/` content — there is no application code of ours here, only the deployment of an off-the-shelf image."* That left the repository with **two homes for one service**: the deployment in `stacks/apicurio/`, and the BFF and UI that front it in `capabilities/schema/`, each with its own Terraform for the same Apicurio instance.

3. **The two Terraform implementations had diverged, and the newer one could not deploy.** `stacks/apicurio/` pinned `apicurio/apicurio-registry-sql:3.0.6` — an image that **does not exist**. The `-sql` variant was a v2-era split whose newest tag is `2.6.13.Final`; v3 consolidated to a single `apicurio/apicurio-registry` image selecting its backend via `APICURIO_STORAGE_KIND`. A `terraform plan` would have succeeded and the ECS task would then have failed to pull. Meanwhile `capabilities/schema/infra/` carried six root modules coupled by `terraform_remote_state` against **local** state paths, with no S3 backend wired, so it could only be applied by one operator from one working copy.

## Decision

**Four decisions, taken together.**

### 1. The service moves to `stacks/apicurio/`, adopting the standard stack layout

| Was | Now | Why this name |
|---|---|---|
| `capabilities/schema/api/` | `stacks/apicurio/src/` | Rule 3 — all code under `src/`, with `src/Dockerfile` producing the image |
| `capabilities/schema/api/tests/` | `stacks/apicurio/qe/tests/` | Rule 3 — tests and quality in `qe/` |
| `capabilities/schema/webapp/` | `stacks/apicurio/ui/` | Rule 3 — experience in `ui/` |
| `capabilities/schema/infra/` | folded into `stacks/apicurio/infrastructure/terraform/` | Rule 3 — everything that runs the image in `infrastructure/` |
| `capabilities/schema/ARCHITECTURE.md` | `stacks/apicurio/ARCHITECTURE.md` | It documents the service, not the standard |

This **retires the rule-2 exception rather than relocating it.** Rule 2 is now unbroken everywhere in the repository, and `stacks/apicurio/CLAUDE.md`'s existing warning — that real applied Terraform belongs with its deployable unit and never under `capabilities/` — applies without a carve-out.

`stacks/apicurio/` is a fourth, non-RELAY member of `stacks/`: cross-cutting platform infrastructure every domain and every RELAY stack depends on, not a RELAY application. That framing predates this ADR and is unchanged.

### 2. The Schema capability's standards move too, emptying `capabilities/schema/`

`README.md`, `guidelines.md`, `apicurio-governance.md`, `gold-attribute-mapping.md` and `gold-attribute-mapping.schema.json` move to `stacks/apicurio/standards/`. `capabilities/schema/` no longer exists.

**This one cuts against the grain of the repository and was taken deliberately.** The stated model is *"The standard is central; the code is in the domain or stack"* — one of thirteen horizontal capabilities, each with a folder under `capabilities/`, a matching Jira Capability, and a same-named folder inside every domain that consumes it. Schema is the most heavily referenced of the thirteen: 82 files across ten domains, the governance reference implementation, two CI workflows and ADR-0011 pointed into it. The costs accepted:

- **Schema is now the only one of the thirteen capabilities with no `capabilities/` folder.** The capability still exists conceptually — domains still keep `src/schemas/`, the Jira Capability is untouched — but the central standard no longer sits with its twelve peers, so the symmetry that makes the model self-explaining is broken at exactly one point.
- **The standards are now colocated with one implementation of them.** `stacks/apicurio/standards/guidelines.md` is the general registry standard every other capability's `guidelines.md` §2 builds on; it now reads as belonging to the Apicurio stack specifically. A future non-Apicurio registry would have to reach into an Apicurio stack for the vendor-neutral standard.
- **`stacks/apicurio/CLAUDE.md` said the governance *policy* is explicitly not owned by this stack.** That boundary is now blurred: the policy documents live here while the tooling that enforces them stays in `delivery-lab/reference-implementations/schema-governance/`.

All 82 inbound references were repointed as part of this change, so nothing dangles. Reversing this half of the decision is a `git mv` plus the same 82-file repoint — cheap to undo, which is part of why it was acceptable to try.

### 3. The stack runs Apicurio **v3**, image `apicurio/apicurio-registry:3.3.2`

This fixes the non-existent `-sql:3.0.6` pin, and it means the BFF client was **ported from the v2 REST API to v3** rather than the registry being pinned back to v2 to suit the client. The v3 changes were verified against the upstream OpenAPI document for tag `v3.3.2`, not from memory.

The port is confined to `src/app/clients/apicurio_client.py`, which normalises the v3 wire shape into the internal shape every service already consumed:

| Internal key (unchanged) | v2 wire | v3 wire |
|---|---|---|
| `id` | `id` | `artifactId` |
| `type` | `type` | `artifactType` |
| `properties` | `properties` | `labels` |

Because `clients/` is the only layer permitted to know an external system's shape, no service, router or Pydantic model changed. Endpoint and payload changes absorbed there: metadata moved off `/meta` to the artifact path; content moved to `/versions/{versionExpression}/content`, so "latest" is now the explicit expression `branch=latest`; `POST /groups/{g}/artifacts` takes a JSON `CreateArtifact` body instead of `X-Registry-*` headers; `ifExists=RETURN_OR_UPDATE` became `FIND_OR_CREATE_VERSION`; and health left the API entirely, to Quarkus at `/q/health/ready`.

**The ADR-0001 derived index is now stored in v3 `labels`.** The validation rule id stays `properties_mismatch`: it is a published contract the UI and the governance docs key on, and renaming it to describe a transport detail would break consumers to no benefit.

### 4. One flat Terraform module, not six

`stacks/apicurio/infrastructure/terraform/` keeps the stack convention — flat resource files, `envs/<env>.tfvars`, partial backend config in `backends/<env>.hcl` — and the BFF, API Gateway and UI delivery were folded in as `bff.tf`, `apigw.tf` and `frontend.tf`. The six pre-migration root modules are gone.

Two consequences worth naming:

- **The local-state coupling is fixed as a side effect.** Modules no longer read each other's `terraform.tfstate` from sibling paths; they are one module with one state key (`uif-apicurio/dev/terraform.tfstate`) in the shared bucket Beacon and Product already use. The `bootstrap` module, which created a state bucket nothing consumed, is dropped.
- **Autoscaling was simplified, not ported.** Step scaling plus two CloudWatch alarms plus a hand-rolled autoscaling IAM role became one target-tracking policy using Application Auto Scaling's service-linked role.

## Alternatives rejected

| Option | Why not |
|---|---|
| **Keep the service in `capabilities/schema/`** | Leaves rule 2 broken, leaves two homes and two Terraform implementations for one service, and leaves the undeployable image pin in place. The reason ADR-0006 gave for the exception — no fitting category — expired when `stacks/` arrived. |
| **`domains/platform/`** — ADR-0006's strongest fallback | Still requires an eighth non-business domain, diluting the definition that makes the Domain-epic parent rule unambiguous. `stacks/` now gives the same CI and deployability benefits without that cost. |
| **Pin the registry back to Apicurio v2 (`2.6.2.Final`)** | The cheapest option: the BFF and its tests would have migrated untouched. Rejected by the owner in favour of v3. It would also have reverted a deliberate v3 choice already made in `stacks/apicurio/`, and left the stack a major version behind on a service other capabilities are about to build on. |
| **Keep Apicurio v3 but leave the client on v2 paths** | Not an option, only a bug: `/apis/registry/v2` does not exist on a v3 server. |
| **Port the six Terraform modules as-is** | No rewrite needed, but carries the local-state coupling and the apply-order trap into the new home, and collides with the module already in `stacks/apicurio/`. |
| **Leave the Schema standards in `capabilities/schema/`** | The structurally consistent choice, and what rule 2 and the thirteen-capability model imply. Rejected by the owner; the costs are recorded under decision 2 above rather than argued away. |

## Consequences

### Easier

- Rule 2 holds everywhere. No capability folder contains executable code.
- One home, one Terraform state, one image pin for the registry.
- `stacks-ci.yml` picks the stack up automatically from its folder name — no workflow edit was required for detection, and the definition-of-done gate (`CHECKLIST.md`, `qe/`, and once real code lands `src/Dockerfile` and `src/schemas/`) now passes on real content rather than being deferred.
- The undeployable image pin is gone, and `terraform validate` passes on the folded module.

### Harder

- **Schema is the only capability without a `capabilities/` folder** (decision 2). Anyone learning the repository from `capabilities/` will find twelve of thirteen.
- **The vendor-neutral registry standard now lives inside a vendor-specific stack.** If a second registry implementation ever appears, `standards/guidelines.md` has to move again or be referenced across a stack boundary.
- **The v3 port is verified against unit tests and the upstream OpenAPI document, not against a running registry.** No Apicurio 3.3.2 instance was available; `branch=latest` path handling and the `CreateArtifact` body shape are asserted with `respx` mocks built from the spec. First contact with a real v3 server is still ahead.
- **Nothing is deployed.** `envs/dev.tfvars` still carries `vpc-UPDATE-ME` placeholders, inherited from the pre-existing stack. This ADR does not change that; the account survey is still owed before a first `apply`.

### Unchanged

- ADR-0007 stands: no knowledge graph, no Business Assurance Agent. `capabilities/knowledge-graph/` and `capabilities/agent/` remain in place and remain unrealized here.
- ADR-0011's governance layer and its two CI workflows still consume this registry; only their documentation paths were repointed.
- The Delivery Lab / CDC arm migrated **as-is, still half-built** — the `cdc-poller` source remains absent, so `infrastructure/` has no module for it and the Product tab still has no data producer. Carried forward deliberately rather than fixed or deleted in a migration; see `stacks/apicurio/ARCHITECTURE.md`.
- **There is still no authentication anywhere in the stack** — no API Gateway authorizer, no auth interceptor in the UI, and `APICURIO_AUTH_ENABLED` unset on the registry. Pre-existing, unaddressed by this move, and the highest-severity open gap.

## Verification

Executed on the migration branch, not estimated:

- `pytest` — **119 passed, 6 skipped**. Up from 104: the client's own contract test grew from 4 tests to 19, covering the v3 translation that previously had no direct coverage.
- `vitest` — **134 passed**; `tsc -b && vite build` clean.
- `terraform fmt -check -recursive` clean (it also fixed `rds.tf`, which was already unformatted on `develop`); `terraform validate` passes on the folded module.
- `ruff check src` — 82 findings, all pre-existing and all outside the rewritten client. Report-only, per the pre-migration posture.
- The one genuinely functional inbound reference — `domains/product/qe/test_marketing_copy_entity.py` loading `gold-attribute-mapping.schema.json` from disk — was repointed and re-run green.
