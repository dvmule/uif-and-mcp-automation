# Schema

## Purpose
The register-first backbone for all contracts — API, event, and data. Schemas are registered before implementation so that contracts are the source of truth and compatibility is enforced automatically rather than discovered in production.

## Standards
- **Schema Registry First:** no API, event, or data contract is implemented before its schema is registered.
- Compatibility rules (backward/forward) are enforced in CI (`../delivery/`); an incompatible change fails the build.
- Every schema is versioned and owned.

## Governance
- **Central:** the registry itself, compatibility policy, schema naming.
- **Local (FDE):** the content of a specific schema.

## Patterns
- Register → validate in CI → implement → publish.
- Consumer-driven compatibility checks.

## Guidance
- **The worked standard:** [`guidelines.md`](guidelines.md) — the general registry mechanics (naming a registry coordinate, register-before-implement, compatibility enforcement in CI) every other capability's own `guidelines.md` §2 builds on. Registry tooling (Apicurio) is documented in `../api/guidelines.md` §2 — linked, not duplicated.
- **Apicurio as a guardrail & governance engine:** [`apicurio-governance.md`](apicurio-governance.md) — the three-plane model (Git authoring / Apicurio governance / AWS runtime), canonical shared schemas by registry coordinate, the CI guardrail lifecycle, and the Dual-Sync Bridge that keeps AWS Glue Schema Registry in lockstep with Apicurio for Kinesis/EventBridge consumers. Reference implementation: `../../delivery-lab/reference-implementations/schema-governance/`.
- **Worked artifact-type instance:** [`gold-attribute-mapping.md`](gold-attribute-mapping.md) — how a domain declares Silver→Gold attribute mappings as a config-only JSON change, validated against the central [`gold-attribute-mapping.schema.json`](gold-attribute-mapping.schema.json); mapping instances live in `domains/<domain>/src/schemas/`. Migrated from `pearson-dnt-coe/gip-schema`; this repository is now the source of truth.
- **Subject naming:** [`subject-naming.md`](subject-naming.md) — the registry coordinate for the five schema types UIF registers per gold entity (`mapping`, `gold`, and the `api`/`event`/`message` tier projections), which no Pearson standard names. Decision: [ADR-0023](../../../architecture-design/decisions/0023-schema-subject-naming.md); machine-readable corpus: [`subject-naming.cases.json`](subject-naming.cases.json), enforced by [`test_subject_naming.py`](test_subject_naming.py).

## The governing standards are in Confluence

**Read these before proposing anything about schema naming or registration.** They are
normative and this repository does not restate them — and nothing here linked to them until
ADR-0023, which is why that decision's first draft was built on a wrong premise: it read the
repository's compliant kebab coordinates as drift.

| Page | Space | Covers |
|---|---|---|
| [Schema Standards](https://pearson.atlassian.net/wiki/spaces/UIF/pages/1493565886/Schema+Standards) | UIF | Common Rules **N1–N12** — the elements *inside* a contract. Scope explicitly **excludes** "resource and infrastructure naming", which is why no N-rule governs subjects |
| [Common Rules](https://pearson.atlassian.net/wiki/spaces/UIF/pages/1493664180/Common+Rules) | UIF | the full N1–N12 text |
| [Gold](https://pearson.atlassian.net/wiki/spaces/UIF/pages/1493795302/Gold) | UIF | mapping-file contents, column and table naming. **No** registry artifact naming |
| [Event Envelope & Naming Standards](https://pearson.atlassian.net/wiki/spaces/ITSOC/pages/1277100355/Event+Envelope+Naming+Standards) | ITSOC | **APICURIO artifact naming** — group, envelope, data schema, AsyncAPI document, artifact version |
| [Versioning, Registry Workflow & Contract Testing](https://pearson.atlassian.net/wiki/spaces/ITSOC/pages/1276903795/Versioning+Registry+Workflow+Contract+Testing) | ITSOC | semver mapping, compatibility modes, the seven-step workflow, the step-2 lint rule set |
| [Eventing Schema Standards (D1–D21)](https://pearson.atlassian.net/wiki/spaces/ITSOC/pages/1276215560/Eventing+Schema+Standards+Guidelines+Universal+Integration+Fabric) | ITSOC | the D-register. **"Deviation requires ARB approval"** |

What they fix, which this repository must not re-decide: `group = <domain>`; the
per-operation data schema is `<entity>-<action>-data`; artifact versions are semver
(**no `.vN` artifact suffix**); compatibility is `BACKWARD_TRANSITIVE` on data schemas and
`FULL_TRANSITIVE` on the envelope; and every artifact declares a **D21 tier** (T1/T2/T3) at
registration.

- Foundation Epic: `Schema Registry Foundation` — stand up the registry and the register-first workflow (`../../PLANNING.md`, Sprint 0). This is step 9 of `../../architecture-design/operating-model/fde-build-deploy-workflow.md`.
