# ADR-0011 — Apicurio Registry as a Guardrail & Governance Engine

**Status:** Proposed
**Date:** 2026-08-31
**Author:** satyanarayana.gunavanthan@pearson.com
**Deciders:** framework owner
**Scope:** How Apicurio Registry is used as the enforced, single source of truth for every domain's sync (OpenAPI), async (AsyncAPI/Avro/JSON Schema), and agent (MCP) contracts, and how it stays in lockstep with AWS Glue Schema Registry for Kinesis/EventBridge runtime consumers. Does **not** revisit *whether* Apicurio is the registry — that's already settled (`../../capabilities/api/guidelines.md` §2, `../../stacks/apicurio/standards/guidelines.md`); this ADR is about the governance layer built on top of it.
**Supersedes:** none.

---

## Context

Contract registration in Apicurio is documented policy today (`../../stacks/apicurio/standards/guidelines.md`, `../../capabilities/api/guidelines.md` §2) but not mechanically enforced in CI, and there is no bridge keeping the Apicurio-registered artifact and AWS Glue Schema Registry's runtime copy in lockstep — a domain could register a schema in Apicurio and never propagate it to Glue, silently leaving a Kinesis/EventBridge consumer validating against a stale or absent schema.

Two structural questions were resolved before building this:

1. **Capability home.** Apicurio-as-guardrail extends the existing, settled `../../stacks/apicurio/standards/` capability — one of UIF's 13. It is not a new 14th capability; MCP and Agent artifact types were already listed in `guidelines.md` line 5, this work is where they get their own worked mechanics.
2. **Domain folder shape.** A proposed `src/contracts/{sync,async,agents}/` taxonomy (replacing today's `src/api/`, `src/events/`, `src/schemas/`, `src/mcp/`, `src/agents/`) was investigated and explicitly rejected for this work. A three-agent inventory found the rename would touch 40+ files including `product`'s live dev/uat/prod deploy pipelines, a generated Terraform file (`domains/product/infrastructure/terraform/product_events_clients.generated.tf`), `product`'s Python package name (literally coupled to the `api` folder name via `pyproject.toml`, every `from api.app import ...`, and the Dockerfile `CMD`), and three `import.meta.glob("../../../../*/src/schemas/...")` calls in the shared Transform UI that glob every domain at once — a non-atomic migration would silently drop a migrated domain out of that UI's cross-domain view. None of that blast radius touches the actual governance problem. **Every template, script, and workflow this ADR covers targets the domain shape that already exists and is live today.**

**Canonical shared schemas** (Address, Money, Metadata, …) have no physical home — there is no `domains/common/` domain, and creating one would be a new domain, gated the same way any new domain is (`../../CLAUDE.md` "Before creating structure"). This ADR's decision is that they don't need one.

---

## Decision

1. **Apicurio is the single enforced source of truth**, per artifact `group:artifact` coordinate (`../../capabilities/api/guidelines.md` §2), for API, Event, MCP, and Agent-output artifacts alike. A contract is authored in Git, admitted only through Apicurio's compatibility gate, and never considered "live" until it is the version Apicurio has registered.
2. **Canonical shared entities are registered in Apicurio under a `common` group**, referenced by every domain via registry-coordinate `$ref` — either resolved at build/CI time against the registry URL, or vendored as a pinned, script-regenerated copy for a runtime path that can't take a network dependency on the registry at request time (`../../stacks/apicurio/standards/apicurio-governance.md` §3). No shared physical folder is created.
3. **A Dual-Sync Bridge** (`../../delivery-lab/reference-implementations/schema-governance/scripts/sync_to_glue_schema_registry.py`) pushes every newly-registered Apicurio artifact into AWS Glue Schema Registry under a matching name and the equivalent compatibility mode, triggered by a successful registration on merge to `main`. This is the only path that writes to Glue for a governed artifact — nothing hand-authors a second copy there.
4. **The guardrail runs as CI, not convention**: lint (Spectral, `.../schema-governance/spectral/`) on every PR; a compatibility dry-run against live Apicurio on every PR (`.github/workflows/contract-governance.yml`); register + dual-sync only on merge to `main`, gated behind the `APICURIO_REGISTRY_URL` and `SCHEMA_GOVERNANCE_DEPLOY_ENABLED` repo variables so the workflow ships and runs green today and starts actually gating only once the team turns those on — the same "scaffold-safe until wired" posture already used for `domain-ci.yml`'s CI-telemetry step and Beacon's `BEACON_DEPLOY_ENABLED` latch.
5. **No domain folder moves.** `src/api/`, `src/events/`, `src/schemas/`, `src/mcp/`, `src/agents/` stay exactly where they are.
6. **Generated SDKs are CI build artifacts, never committed** into a domain (`.github/workflows/generate-sdks.yml`), following the drift-risk lesson already learned from the committed, script-regenerated `product_events_clients.generated.tf`.

---

## Consequences

**Becomes easier**

- Every contract type (API, Event, MCP, Agent) has one enforced lifecycle instead of documented-but-unchecked policy; a breaking change is rejected in CI, not discovered by a consumer in production.
- Glue Schema Registry can never silently drift from Apicurio — the bridge is the only writer.
- Canonical entities (Address, Money, Metadata) are defined once and referenced everywhere by coordinate, with no shared folder to keep in sync by hand.

**Becomes harder**

- A domain's contract must carry a `registry`/`x-registry` coordinate block before it can pass the lint gate — existing contracts (`domains/product/src/api/openapi.yaml`, `domains/product/src/events/asyncapi.yaml`, and others) do not yet have one, and adopting this gate as a *required* check will need those retrofitted first (confirmed by running the new Spectral ruleset against them: both currently fail on the missing `x-registry` block, as expected for contracts written before this ADR).

**Accepted**

- The `src/contracts/{sync,async,agents}/` taxonomy remains an open, separate question. If the team wants it in the future, it needs its own ADR and its own dedicated migration plan — scoped, staged, and reviewed on its own, not bundled into this or any other unrelated change.

---

## Decision Required

1. **Adoption sequencing** — retrofit existing contracts (`product`, `beacon`, others with real `src/api`/`src/events` content) with a `registry`/`x-registry` block before making the lint/compatibility checks required status checks, or land the workflow as informational-only until that retrofit happens domain by domain.
2. **Who provisions** `APICURIO_REGISTRY_URL`, the shared `uif-events` Glue Schema Registry, and the AWS role `AWS_ROLE_ARN_SCHEMA_GOVERNANCE` the `register-and-sync` job assumes — none exist yet.
3. **Naming** for the `common` group's first canonical entities (Address, Money, Metadata) and who owns authoring them.
