# Semantics — Guidelines (the worked standard)

This is the detailed, do-this companion to the Semantics capability `README.md`. It tells a Forward Deployed Engineer exactly how to name a canonical term, document it as a governed vocabulary entry, test that contracts stay aligned to it, and register it — so that "Contract" means the same thing in an API, an event, and a knowledge graph, instead of three teams each inventing their own definition.

This document follows the same **template shape** as every capability's `guidelines.md` (see `../api/guidelines.md`): five sections — Naming, Contract documentation, Testing → automation → build, Standards & guidelines, Definition of done.

Grounding standards (all public): **W3C SKOS** (Simple Knowledge Organization System — concepts, labels, broader/narrower/related relations), **JSON-LD 1.1** (how a concept is serialized and linked), **ISO/IEC 11179** (metadata registries — the standard discipline for naming and defining a data element once, centrally), and Semantic Versioning 2.0.0 for the vocabulary itself.

Worked example: `../../domains/legal/src/semantics/legal-vocabulary.json` — the canonical vocabulary for the Legal domain, referenced below as the smallest real instance of every rule.

---

## 1. Naming

**Concepts.** `PascalCase`, singular noun, the "thing" being defined — never an action or a field: `Contract`, `Clause`, `Obligation`, `Party`.
- Good: `EffectiveDate`, `GoverningLaw`.
- Bad: `contracts` (plural), `signContract` (a verb — that's an API operation, not a concept).

**Canonical attribute names.** `camelCase`, and this is the name every schema `$ref`s — an API's `openapi.yaml` field, an event's `data` field, and a knowledge graph property for the same meaning all use this exact string. This is what "semantically consistent" means in practice: one name, enforced everywhere.
- `effectiveDate`, `governingLaw`, `counterparty`.

**Vocabulary identifier.** Each concept gets a stable, non-resolvable identifier so it can be referenced without depending on a URL that might move: `urn:uif:semantics:<domain>:<Concept>`.
- `urn:uif:semantics:legal:Contract`

**Vocabulary file.** One per domain that needs one, `kebab-case`: `<domain>-vocabulary.json`, kept in `domains/<domain>/src/semantics/`.

---

## 2. Contract documentation (Semantic By Design)

**A concept is not a schema — it's the definition a schema must agree with.** A concept entry states what a term *means*; the JSON Schema in `../schema/` states its *shape*. Both are required; neither substitutes for the other.

**Format: SKOS-shaped JSON-LD.** Each concept is documented with, at minimum:
- `id` — the `urn:` identifier (§1).
- `prefLabel` — the canonical display name.
- `definition` — one sentence, the actual meaning, not a restatement of the label.
- `broader` / `narrower` (optional) — `skos:broader`/`skos:narrower` relations to other concepts in the same vocabulary, when the term is genuinely part of a hierarchy.
- `exactMatch` (optional) — the `urn:` of an equivalent concept in another domain's vocabulary, when two domains have independently named the same real-world thing; this is how cross-domain drift gets caught and reconciled instead of silently coexisting.

**A minimal worked example** (the smallest vocabulary entry that already satisfies every rule above):
```json
{
  "@context": "https://www.w3.org/ns/skos.jsonld",
  "vocabulary": "legal",
  "version": "1.0.0",
  "concepts": [
    {
      "id": "urn:uif:semantics:legal:Contract",
      "prefLabel": "Contract",
      "definition": "A legally binding agreement between two or more parties.",
      "narrower": ["urn:uif:semantics:legal:Clause"]
    },
    {
      "id": "urn:uif:semantics:legal:Clause",
      "prefLabel": "Clause",
      "definition": "A single provision within a Contract that creates one Obligation.",
      "broader": ["urn:uif:semantics:legal:Contract"],
      "narrower": ["urn:uif:semantics:legal:Obligation"]
    }
  ]
}
```
The full, runnable version of this same vocabulary is `../../domains/legal/src/semantics/legal-vocabulary.json`; read the snippet above as its smallest possible slice.

**Registration.** The vocabulary file is registered in the schema registry (`../schema/`) the same way an `openapi.yaml` or event schema is — as a versioned artifact under `group:artifact` coordinates (`group: legal`, `artifact: vocabulary`) — so a schema anywhere in the fabric can declare "this field's meaning is `urn:uif:semantics:legal:EffectiveDate`" and have that resolve to one governed definition.

**Proposing a new term.** A new concept, or a change to an existing `definition`, is proposed as a PR against the domain's vocabulary file and reviewed the same as a schema change (`../schema/` §3's compatibility discipline) — never invented silently inside one API or event contract.

---

## 3. Testing → automation → build

1. **Vocabulary validation** — the vocabulary file itself validates against a SKOS-shaped JSON Schema (every concept has `id`, `prefLabel`, `definition`; every `broader`/`narrower`/`exactMatch` reference resolves to a concept that actually exists in some registered vocabulary). No dangling references.
2. **Semantic lint** — every schema (`../schema/`) that claims a field maps to a canonical concept (via an `x-semantic-id` extension carrying the `urn:`) is checked against the registered vocabulary: the `urn:` must exist and the field name should match that concept's canonical attribute name (§1). A field claiming `urn:uif:semantics:legal:EffectiveDate` while calling itself `effDt` fails this check.
3. **Cross-domain consistency check** — a scheduled (not per-PR) job scans all registered schemas' `x-semantic-id` usage and flags two different `urn:`s being used for fields with the same name, or the same `urn:` mapped to two different field names — surfacing drift for a human to reconcile via `exactMatch`, not auto-resolving it.
4. **Consumer check** — Knowledge Graph and Agent builds (`../knowledge-graph/`, `../agent/`) that read from a vocabulary re-run their own tests when the vocabulary version changes, the same as any registered-schema consumer.

**Automation & build gate (GitHub Actions, at repo root — `../delivery/`).** On every PR touching `domains/<domain>/src/semantics/**` or any schema's `x-semantic-id`:
```
lint (vocabulary JSON Schema)  →  reference-resolution check  →  semantic lint (schema ↔ vocabulary)  →  publish vocabulary artifact
```

---

## 4. Standards & guidelines (non-negotiable design inputs)

- **Semantic By Design.** A schema field that represents a governed concept declares its `urn:` via `x-semantic-id`; it does not redefine the concept's meaning in its own `description`.
- **One vocabulary owner per domain.** The domain that owns a concept's canonical definition is discoverable from the vocabulary file's own metadata — other domains reference it, they don't fork it.
- **Extend before you invent.** Before adding a new concept, check whether an existing concept in any domain's vocabulary already means the same thing (`exactMatch` candidate) — Semantics exists specifically to prevent "customer" being redefined seven times.
- **Consumed by Knowledge Graph and Agent (`../knowledge-graph/`, `../agent/`).** A KG's node/relationship types and an agent's structured-output field names should trace to the same `urn:`s a schema uses — this is what "reasoning over connected, consistent context" actually depends on.

---

## 5. Definition of done (Semantics)

- [ ] Concepts and attribute names follow §1; vocabulary identifier is a stable `urn:`, not a resolvable URL.
- [ ] Vocabulary file follows §2 (SKOS-shaped JSON-LD, `id`/`prefLabel`/`definition` minimum) and is registered as a versioned artifact (`../schema/`).
- [ ] Vocabulary validation + semantic lint pass (§3); no dangling `broader`/`narrower`/`exactMatch` references.
- [ ] Any schema field claiming a concept carries a resolvable `x-semantic-id` matching the concept's canonical attribute name.
- [ ] CI green: vocabulary lint → reference-resolution → semantic lint → artifact published (§3).
- [ ] Maps to a Jira Story (`capability:semantics` + its `domain:*`) per `../docs/capability-domain-jira-map.md`.

---

Worked instance: `../../domains/legal/src/semantics/legal-vocabulary.json`. Consumed by `../knowledge-graph/` (entity/relationship types trace to these `urn:`s) and `../agent/` (structured output fields trace to these `urn:`s).
