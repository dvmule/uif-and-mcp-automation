# Data — Guidelines (the worked standard)

This is the detailed, do-this companion to the Data capability `README.md`. It tells a Forward Deployed Engineer exactly how to name a data product, document it as a data contract, test and build it, and register it — so consumers depend on a governed, owned contract instead of reverse-engineering a table or an extract.

This document follows the same **template shape** as every capability's `guidelines.md`: five sections — Naming, Contract documentation, Testing → automation → build, Standards & guidelines, Definition of done. **Data is the information-modelling layer — the contract for what a data product means and who owns it.** It is not the registry mechanics (that's `../schema/`, which enforces how any contract — API, event, or data — is versioned and checked for compatibility) and it is not data governance/quality/lineage tooling in the broad enterprise sense; it is the contract discipline a domain applies to its own data products.

Grounding standards (all public): the **Open Data Contract Standard (ODCS)** (Bitol / Linux Foundation AI & Data — the shape a data contract's metadata follows: owner, SLA, quality rules, classification), **Data Mesh** principles (data-as-a-product, federated ownership — Zhamak Dehghani), **W3C DCAT** (Data Catalog Vocabulary, for how a data product is discoverable), JSON Schema 2020-12 (the physical schema `$ref`d by the contract), and ISO 8000 (data quality dimensions: completeness, uniqueness, timeliness).

Worked example: `../../domains/product/src/schemas/data/product-master.data-contract.json` — the Product domain's master data product, built on top of the existing `../../domains/product/src/schemas/mapping/product/v1/product.mapping.json` Silver→Gold mapping.

---

## 1. Naming

**Data product.** `<domain>.<subject>`, lowercase dot-delimited, a noun — a data product is a thing, not an action: `product.master`, `hr.employee`, `legal.contract-register`.
- Bad: `product.sync` (a verb — that's a pipeline job, not the product).

**Data contract file.** `<subject>.data-contract.json`, `kebab-case` subject, kept in `domains/<domain>/src/schemas/data/`.

**Owner.** Every data product names a real owning team (not a person) in its contract — matches the Data Mesh federated-ownership principle: the domain that produces the data owns its contract.

---

## 2. Contract documentation (Data Centric — owned, versioned, SLA-bearing)

**A data contract documents four things an event or API schema alone does not:** who owns it, how fresh/available it must be, what quality it guarantees, and how it's classified for access. The physical shape (fields, types) is still a registered JSON Schema (`../schema/`) — the data contract wraps that schema with the product-level guarantees around it.

**A minimal worked example** (ODCS-shaped, the smallest contract that satisfies every rule above):
```json
{
  "id": "product.master",
  "version": "1.0.0",
  "status": "active",
  "owner": "product-domain-team",
  "description": "Governed master record for a product, sourced from the Silver→Gold mapping.",
  "schema": { "$ref": "../events/product-created-data.json" },
  "classification": "internal",
  "sla": { "freshness": "PT1H", "availability": "99.9%" },
  "qualityRules": [
    { "dimension": "completeness", "rule": "ppid is never null", "threshold": "100%" },
    { "dimension": "uniqueness", "rule": "ppid is unique", "threshold": "100%" }
  ],
  "lineage": { "sourceEvents": ["com.pearson.product.product.created.v1"] }
}
```
The full, runnable version is `../../domains/product/src/schemas/data/product-master.data-contract.json`; read the snippet above as its smallest possible slice.

**Registration.** The data contract is registered in the schema registry (`../schema/`) as its own versioned artifact — before any consumer is allowed to depend on the data product, per Schema Registry First.

**Classification is mandatory, not optional metadata** — `public` / `internal` / `confidential` / `restricted` — and drives the access control a consumer must satisfy (`../security/`).

---

## 3. Testing → automation → build

1. **Contract shape validation** — the data contract file validates against an ODCS-shaped JSON Schema (owner, SLA, classification, and a resolvable `schema` reference are all required).
2. **Quality rule tests** — each declared quality rule (completeness, uniqueness, and any domain-specific rule) is run against a representative sample/fixture; a rule that can't actually be evaluated against real data is not shippable.
3. **Lineage traceability check** — every `sourceEvent`/source system named in `lineage` is a real, registered event or contract (`../event/`, `../schema/`) — a data contract can't claim a source that doesn't exist.
4. **SLA monitor test** — a freshness/availability check runs against the live pipeline in a non-prod environment and alerts before the declared SLA is breached, not after.
5. **Consumer compatibility check** — the same backward/forward compatibility gate as any registered schema (`../schema/` §3) applies to a data contract's schema changes.
6. **Access-control test** — a consumer without the classification-appropriate grant is denied (`../security/`), verified as part of the build, not assumed.

**Automation & build gate (GitHub Actions, at repo root — `../delivery/`).** On every PR touching `domains/<domain>/src/schemas/data/**`:
```
lint (data-contract schema)  →  quality rule tests  →  lineage check  →  compatibility check (../schema/)  →  publish data contract artifact
```

---

## 4. Standards & guidelines (non-negotiable design inputs)

- **Data Centric.** Every data product has a named owner and a published, registered data contract — never an unowned table or extract that consumers depend on informally.
- **Data contracts are registered before consumers depend on them (`../schema/`).** Schema Registry First applies to data exactly as it applies to API/event contracts.
- **Access to data is governed by default (`../security/`).** Classification is set at contract creation, not retrofitted after an access incident.
- **Prefer event-driven propagation over batch extraction where feasible (`../event/`).** A data product's `lineage.sourceEvents` should trace to registered domain events (CDC/outbox pattern, `../event/guidelines.md` §4) rather than a nightly batch pull, when the source supports it.
- **Consumed by Semantics and Knowledge Graph (`../semantics/`, `../knowledge-graph/`).** A data product's fields should map to canonical Semantics concepts, and a Knowledge Graph populated from this domain should cite this contract as its `provenance` source (`../knowledge-graph/guidelines.md` §2).

---

## 5. Definition of done (Data)

- [ ] Data product and contract file named per §1 (`<domain>.<subject>`, noun not verb; owner is a team).
- [ ] Data contract follows §2 (ODCS-shaped: owner, SLA, classification, quality rules, resolvable schema reference) and is registered (`../schema/`).
- [ ] Contract shape validation, quality rule tests, lineage check, and compatibility check pass (§3).
- [ ] SLA monitor and access-control checks are in place, not just declared (§3).
- [ ] Classification is set and drives an actual access-control grant (`../security/`).
- [ ] CI green: lint → quality rules → lineage check → compatibility check → artifact published (§3).
- [ ] Maps to a Jira Story (`capability:data` + its `domain:*`) per `../docs/capability-domain-jira-map.md`.

---

Worked instance: `../../domains/product/src/schemas/data/product-master.data-contract.json`, built over `../../domains/product/src/schemas/events/product-created-data.json`. Consumed by `../semantics/` and `../knowledge-graph/`; registered and version-checked via `../schema/`.
