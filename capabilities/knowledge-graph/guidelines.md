# Knowledge Graph — Guidelines (the worked standard)

This is the detailed, do-this companion to the Knowledge Graph capability `README.md`. It tells a Forward Deployed Engineer exactly how to name a node/relationship type, document the graph's shape as a contract, test it, and build it — grounded in public graph standards so the result is queryable by any RDF- or property-graph-capable client, inside or outside UIF.

This document follows the same **template shape** as every capability's `guidelines.md`: five sections — Naming, Contract documentation, Testing → automation → build, Standards & guidelines, Definition of done.

Grounding standards (all public): **W3C RDF 1.1** and **JSON-LD 1.1** (triple-based representation), **W3C SHACL** (Shapes Constraint Language — validating a graph's shape, the graph analogue of JSON Schema), **W3C PROV-O** (provenance — where a fact in the graph came from), and **SPARQL 1.1** / the property-graph query family (openCypher) as the two portable query-interface options. Node/relationship types trace to `../semantics/` concepts — a Knowledge Graph is Semantics made queryable, not an independent taxonomy.

Worked example: `../../domains/legal/src/knowledge-graph/legal-graph.json` — a small real graph over the `../../domains/legal/src/semantics/legal-vocabulary.json` concepts.

---

## 1. Naming

**Node (entity) types.** `PascalCase`, and **identical to the Semantics concept it represents** (`../semantics/` §1) — `Contract`, `Clause`, `Obligation`, `Party`. A node type that isn't a registered concept is not allowed; propose the concept first.

**Relationship (edge) types.** `UPPER_SNAKE_CASE`, verb-phrase, direction implied by the phrase — the common property-graph convention (Cypher/Gremlin): `HAS_CLAUSE`, `CREATES_OBLIGATION`, `BOUND_BY`.
- `(Contract)-[:HAS_CLAUSE]->(Clause)`, `(Clause)-[:CREATES_OBLIGATION]->(Obligation)`, `(Obligation)-[:BOUND_BY]->(Party)`.

**Node identifiers.** `<domain>:<EntityType>:<naturalOrOpaqueId>` — stable, and reused as the RDF subject/JSON-LD `@id` if the graph is also exposed as triples: `legal:Contract:c-4471`.

**Graph artifact file.** One per domain-subgraph, `kebab-case`: `<domain>-graph.json`, kept in `domains/<domain>/src/knowledge-graph/`.

---

## 2. Contract documentation (populated from governed sources, not scraped)

**The graph is a projection, not a source.** Every node and edge is populated from a governed data product or event (`../data/`, `../event/`) — never scraped or hand-entered — so the graph is always traceable back to the contract that produced it.

**Shape contract: SHACL-style constraints per node/relationship type**, documenting: which relationship types a node type may participate in, cardinality (e.g. a `Clause` has exactly one `HAS_CLAUSE` incoming edge), and which properties are required. This is registered in the schema registry (`../schema/`) the same as any other contract, so a graph consumer can validate against it before querying.

**A minimal worked example** (property-graph-shaped JSON-LD, the smallest slice that satisfies every rule above):
```json
{
  "@context": {"@vocab": "urn:uif:semantics:legal:"},
  "graph": "legal",
  "version": "1.0.0",
  "nodes": [
    { "id": "legal:Contract:c-4471", "type": "Contract", "properties": { "effectiveDate": "2026-01-01", "governingLaw": "NY" } },
    { "id": "legal:Clause:cl-1",     "type": "Clause",    "properties": { "title": "Confidentiality" } }
  ],
  "relationships": [
    { "type": "HAS_CLAUSE", "from": "legal:Contract:c-4471", "to": "legal:Clause:cl-1" }
  ],
  "provenance": {
    "cl-1": { "sourceEvent": "legal.contract.clause-added", "ingestedAt": "2026-01-01T00:00:00Z" }
  }
}
```
The full, runnable version is `../../domains/legal/src/knowledge-graph/legal-graph.json`; read the snippet above as its smallest possible slice. `provenance` is a minimal PROV-O-style record (what produced this fact, when) — required per fact, not optional metadata.

**Query interface is declared, not assumed.** State in the domain's `README.md` whether the graph is exposed via SPARQL (RDF/triple store) or an openCypher-compatible endpoint (property-graph store) — both are acceptable, portable choices; UIF does not mandate one graph database product.

---

## 3. Testing → automation → build

1. **Shape validation** — the graph artifact validates against its registered SHACL-style constraints (§2): every node's `type` is a registered node type, every relationship's `from`/`to` types are allowed to participate in that relationship type, cardinality rules hold.
2. **Semantic consistency** — every node `type` resolves to a concept `urn:` in the referenced vocabulary (`../semantics/` §3's semantic lint, applied to graph node types instead of schema fields).
3. **Provenance check** — every node/relationship carries a `provenance` entry pointing to a real, registered source event or data product; a fact with no traceable origin fails the build.
4. **Entity resolution tests** — where the same real-world entity can arrive from two sources (e.g. a `Party` mentioned in two contracts), the resolution rule (exact match on a governed identifier, never fuzzy string match alone) is unit tested.
5. **Query smoke tests** — one representative SPARQL or openCypher query per relationship type returns the expected shape against a seeded test graph.

**Automation & build gate (GitHub Actions, at repo root — `../delivery/`).** On every PR touching `domains/<domain>/src/knowledge-graph/**`:
```
lint (graph JSON Schema)  →  shape validation (SHACL-style)  →  semantic consistency  →  provenance check  →  publish graph artifact
```

---

## 4. Standards & guidelines (non-negotiable design inputs)

- **Knowledge Graph Enabled.** Entities and relationships use the canonical semantics (`../semantics/`) — never a locally invented node type.
- **Populated from governed sources only.** Ingestion reads from a registered data product or event stream (`../data/`, `../event/`); it never reaches into another domain's database or scrapes a UI.
- **Provenance is mandatory per fact**, not per batch — a fact without a traceable source cannot be trusted for agent reasoning or audit.
- **Security and observability by default (`../security/`, `../observability/`).** Query access is authorized per node/relationship type (least privilege — not every caller sees every subgraph); query latency and result-set size are RED-signal metrics per query pattern.
- **Consumed by Agent (`../agent/`) as retrieval context** — graph-backed retrieval (RAG over the graph) is the primary consumption pattern; design the query interface for that access shape, not only for human exploration.

---

## 5. Definition of done (Knowledge Graph)

- [ ] Node/relationship type names follow §1; node types are registered Semantics concepts.
- [ ] Graph artifact follows §2 (shape contract registered, provenance per fact) and is registered as a versioned artifact (`../schema/`).
- [ ] Shape validation, semantic consistency, and provenance checks pass (§3).
- [ ] Entity resolution rule is documented and unit tested where applicable (§3).
- [ ] Query access is authorized per type; RED signals defined for query patterns (§4).
- [ ] CI green: lint → shape validation → semantic consistency → provenance check → artifact published (§3).
- [ ] Maps to a Jira Story (`capability:knowledge-graph` + its `domain:*`) per `../docs/capability-domain-jira-map.md`.

---

Worked instance: `../../domains/legal/src/knowledge-graph/legal-graph.json`, built over `../../domains/legal/src/semantics/legal-vocabulary.json`. Consumed by `../agent/` for graph-backed retrieval.
