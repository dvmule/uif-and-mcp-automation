# Schema — Guidelines (the worked standard)

This is the detailed, do-this companion to the Schema capability `README.md`. It tells a Forward Deployed Engineer exactly how to name a registry coordinate, document any contract's registration, test and enforce compatibility, and publish it — the register-first backbone every other capability's own `guidelines.md` builds on.

This document follows the same **template shape** as every capability's `guidelines.md`: five sections — Naming, Contract documentation, Testing → automation → build, Standards & guidelines, Definition of done. **Schema is not one more contract type — it's the mechanics every contract type uses to register, version, and check compatibility.** An `openapi.yaml` (`../api/`), an event schema (`../event/`), a vocabulary (`../semantics/`), a data contract (`../data/`), a knowledge-graph shape (`../knowledge-graph/`), an MCP tool schema (`../mcp/`), or an AgentCard (`../a2a/`) are all *artifact types*; this document is the one set of rules that governs how any of them gets registered.

Grounding standards (all public): JSON Schema 2020-12 (the meta-schema language most artifact types are expressed or validated in — even a non-JSON-Schema artifact, like the gold-attribute-mapping template, is validated against one), Semantic Versioning 2.0.0, and **Apicurio Registry** (the concrete, currently-deployed registry — see the worked "Schema registry tooling: Apicurio" subsection in `../api/guidelines.md` §2 for what it is and how it works; not re-explained here — link, don't duplicate).

Worked example: [`gold-attribute-mapping.md`](gold-attribute-mapping.md) — a full worked registration of one artifact type (Silver→Gold data mappings) end to end. Read this document for the *general* rule, that one for a *complete instance* of applying it.

---

## 1. Naming

**Registry coordinates.** Every artifact registers under `group:artifact`, exactly the Apicurio coordinate model already established in `../api/guidelines.md` §2:
- `group` = the owning domain (`product`, `legal`, `hr`, …) — matches `<domain>` in `domains/<domain>/`.
- `artifact` = the contract's own name within that domain (`developer-registration-api`, `product-created-data`, `vocabulary`, `product-master-data-contract`, `knowledge-graph`, `a2a-routing`) — matches the naming convention each owning capability's own `guidelines.md` §1 already defines. Schema does not invent a second naming scheme; it enforces that every artifact *declares* its coordinate.

**Declaring the coordinate in the artifact itself.** Every registered artifact carries its own `registry: { group, artifact }` block (or the OpenAPI/AsyncAPI equivalent already shown in `../api/guidelines.md` §2) so the coordinate is reviewable in the file, not only known to whoever ran the registration command. This is the convention already used by every artifact built for Semantics, Knowledge Graph, A2A, and Data — Schema is what formalizes it as a rule, not a one-off habit.

**Version.** Apicurio versions by content hash; where a human-readable label is still useful (release notes, a consumer-facing changelog), pair it with SemVer 2.0.0 as an alias, never as a replacement for the content-hash version.

---

## 2. Contract documentation (Schema Registry First — register before you implement)

**No API, event, or data contract is implemented before its schema is registered.** This is the one rule every other capability's §2 inherits from here. Concretely: the PR that adds `openapi.yaml`, an event's data schema, a vocabulary file, a data contract, or an AgentCard is the PR that registers it — application code that depends on the shape comes in a later PR, never the same one or an earlier one.

**Every registered artifact declares, at minimum:**
- Its registry coordinate (§1).
- Its compatibility mode — `BACKWARD`, `FORWARD`, `FULL`, or `NONE` — set once per artifact and enforced by the registry on every subsequent version push (Apicurio rule configuration, per `../api/guidelines.md` §2).
- Its owning capability and domain, so a reviewer knows which `guidelines.md` governs its shape (an `openapi.yaml` is reviewed against `../api/guidelines.md`; a vocabulary file against `../semantics/guidelines.md`; and so on — Schema governs *that* it's registered and *how* compatibility is checked, not the internal shape rules of each artifact type).

**Worked instance of every rule above:** `gold-attribute-mapping.md` §"The contract" — one meta-schema owned centrally, one mapping file per domain, `$schema` pointed at the central definition so the artifact self-documents what it must validate against.

---

## 3. Testing → automation → build

1. **Meta-schema validation** — every artifact validates against its own type's meta-schema (JSON Schema 2020-12 for most; the gold-attribute-mapping `oneOf` discriminated union for that artifact type) before it can be pushed.
2. **Registry dry-run (check mode)** — on every PR, push the candidate version to the registry in check/dry-run mode; the registry evaluates it against the artifact's configured compatibility rule without actually publishing. An incompatible change fails the build here, not after merge.
3. **Consumer regression** — any registered consumer of the artifact (a contract test per `../api/guidelines.md` §3, `../event/` §3, or the capability-specific equivalent) re-runs against the candidate schema before merge.
4. **Publish on merge** — merging to the domain's main line is the only path that actually publishes a new version to the registry; no artifact is published from a feature branch or a local run.

**Automation & build gate (GitHub Actions, at repo root — `../delivery/`).** On every PR touching any `domains/<domain>/src/**/*.{json,yaml}` registered artifact:
```
lint (meta-schema)  →  registry dry-run (compatibility check)  →  consumer contract tests  →  merge  →  publish version
```

---

## 4. Standards & guidelines (non-negotiable design inputs)

- **Schema Registry First.** Stated once, enforced everywhere: no artifact type gets an exception to register-before-implement.
- **Compatibility rules are enforced in CI (`../delivery/`), not by convention.** A reviewer should never have to manually check whether a change is backward-compatible — the registry's dry-run check is the source of truth.
- **Every schema is versioned and owned.** An artifact's owning domain (its `group`) is also its owning capability's accountable team — Schema doesn't introduce a second ownership model.
- **One central meta-schema per artifact type, many domain instances.** The pattern `gold-attribute-mapping.md` establishes — one schema in `stacks/apicurio/standards/`, N mapping files in `domains/*/src/schemas/` — is the template for any future artifact type that needs the same central-schema/local-instance shape.
- **Apicurio is the concrete registry** (`../api/guidelines.md` §2); this document states the rules Apicurio enforces, it does not restate how Apicurio itself works.

---

## 5. Definition of done (Schema)

- [ ] Artifact declares its registry coordinate per §1 (`group:artifact`, matching its owning domain and its own capability's naming rule).
- [ ] Artifact is registered (dry-run passed, published on merge) before any code depends on its shape (§2).
- [ ] Compatibility mode is set and enforced by the registry, not assumed (§2–3).
- [ ] Meta-schema validation and consumer regression tests pass in CI before merge (§3).
- [ ] Owning capability and domain are identifiable from the artifact or its adjacent README (§2).
- [ ] CI green: lint → registry dry-run → consumer contract tests → publish (§3).
- [ ] Maps to a Jira Story (`capability:schema` + its `domain:*`) per `../docs/capability-domain-jira-map.md`.

---

Worked artifact-type instance: [`gold-attribute-mapping.md`](gold-attribute-mapping.md). Registry tooling: `../api/guidelines.md` §2 ("Schema registry tooling: Apicurio"). Every other capability's `guidelines.md` §2 builds its own artifact's contract documentation on top of the rules stated here.
