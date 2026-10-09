# Jira Backlog Seed

This is the create-ready backlog for standing up UIF in Jira: the exact Initiative, Capabilities, Epics, and Sprint 0 Stories to create, with parents and acceptance criteria. A Product Manager or FDE works top-to-bottom to populate JPD and Jira Software. It expands the summary in `../PLANNING.md`; the hierarchy rules are in `jira-workflow.md`.

> **Confluence:** thin pointer page draft lives at `confluence-pointer-draft.md` in this folder — publish to [UIF space](https://pearson.atlassian.net/wiki/spaces/UIF/overview) when onboarding docs need a wiki entry point.

> **Status (2026-08-05):** most of this is now live in **UIFPE** — the legacy Phase-0 issues are reconciled (labelled `phase:0`, not reused), all 13 Capabilities and Foundation Epics exist (UIFPE-8..35), Sprint 0 Stories/Spikes exist (UIFPE-36..43), and all 7 Domain epics exist (UIFPE-51..57, §3a below). UIFPE-39 (S0-4) and UIFPE-40 (S0-5) are reparented to their Domain epic per the single-owning-parent rule; UIFPE-36/37/38/41 correctly stay under their Foundation Epics as pre-domain groundwork. Anything not yet created still needs explicit sign-off before writing to live Jira — see `PLANNING.md`'s reconciliation notes for the authoritative log of what's been done.

## Conventions

- **Hierarchy:** Initiative (JPD) → Capability → Foundation Epic → Story (pre-domain groundwork only) **or** Domain epic → Story (delivery work, the common case) → Task/Sub-task; plus Spike, Bug (`jira-workflow.md`).
- **Single owning parent:** a delivery Story's parent is its **Domain epic** (where it ships); capability is always a `capability:<x>` label, never a parent. See `../capabilities/docs/capability-domain-jira-map.md`.
- **Traceability:** every Capability rolls up to the UIF Initiative; every delivery Story links to the `domains/<domain>/src/` path it produces.

---

## 1. JPD Initiative (WHY)

| Field | Value |
|---|---|
| Type | Initiative (JPD) |
| Name | Unified Integration Fabric |
| Summary | The Engineering Operating System for enterprise APIs, events, data, semantics, knowledge graphs, MCP, A2A, and agents. |

---

## 2. Capabilities (WHAT) — create all thirteen

Each is a Jira Capability under the Initiative. Doc: `../capabilities/<folder>/`. Architecture & Design is **not** one of these thirteen — it's the frame they sit in (`../architecture-design/`); track it as a program/foundation concern (`program:architecture-design`), not a Capability.

| # | Capability | Label | Doc |
|---|---|---|---|
| 1 | API | `capability:api` | `../capabilities/api/` |
| 2 | Event | `capability:event` | `../capabilities/event/` |
| 3 | Data | `capability:data` | `../capabilities/data/` |
| 4 | Schema | `capability:schema` | `../stacks/apicurio/standards/` |
| 5 | Semantics | `capability:semantics` | `../capabilities/semantics/` |
| 6 | Knowledge Graph | `capability:knowledge-graph` | `../capabilities/knowledge-graph/` |
| 7 | MCP | `capability:mcp` | `../capabilities/mcp/` |
| 8 | A2A | `capability:a2a` | `../capabilities/a2a/` |
| 9 | Agent | `capability:agent` | `../capabilities/agent/` |
| 10 | Infrastructure | `capability:infrastructure` | `../capabilities/infrastructure/` |
| 11 | Security | `capability:security` | `../capabilities/security/` |
| 12 | Observability | `capability:observability` | `../capabilities/observability/` |
| 13 | Delivery | `capability:delivery` | `../capabilities/delivery/` |

---

## 3. Foundation Epics

Create under the matching Capability. Each establishes that capability's golden path. These are for **pre-domain platform groundwork only** — once a capability's golden path ships in a real domain, further work parents to that Domain epic instead (`capability:*` stays as a label).

| Epic | Parent Capability | Outcome |
|---|---|---|
| API Foundation | API | Repeatable path from Story to a running, governed API surface in a domain. |
| Event Foundation | Event | Repeatable path to a published/consumed event with a registered schema. |
| Schema Registry & Representation Foundation | Schema | Running registry (Apicurio) + register-first, compatibility-enforced workflow. |
| Semantics Foundation | Semantics | Canonical vocabulary + extension process. |
| Knowledge Graph Foundation | Knowledge Graph | Graph store, ingestion pattern, query interface. |
| MCP Foundation | MCP | Reference MCP server + pattern. |
| A2A Foundation | A2A | Agent routing/collaboration pattern + reference. |
| Agent Framework Foundation | Agent | Agent module anatomy, prompts, evals, governance. |
| GitHub Actions Foundation | Delivery | Standard CI: build, test, scan, validate. |
| Delivery Foundation | Delivery | Standard CD (GitHub Actions): Development → UAT → Production. |

**Architecture Foundation** (end-to-end golden path proven on one real domain) is a **program concern**, not a Capability Foundation Epic — track it under `program:architecture-design`, no Capability parent.

---

## 3a. Domain epics

Top-level, no parent of their own — each is the **owning parent** for delivery Stories/Tasks/Sub-tasks/Bugs shipping in that domain (`jira-workflow.md`). Capability stays a label on the child issue, never a parent.

| # | Domain | Label | Repo path | Jira key (live) |
|---|---|---|---|---|
| 1 | Product | `domain:product` | `domains/product/` | UIFPE-51 |
| 2 | Customer | `domain:customer` | `domains/customer/` | UIFPE-52 |
| 3 | SKU | `domain:sku` | `domains/sku/` | UIFPE-53 |
| 4 | Finance | `domain:finance` | `domains/finance/` | UIFPE-54 |
| 5 | HR | `domain:hr` | `domains/hr/` | UIFPE-55 |
| 6 | Legal | `domain:legal` | `domains/legal/` | UIFPE-56 |
| 7 | Identity | `domain:identity` | `domains/identity/` | UIFPE-57 |
| 8 | Orders | `domain:orders` | `domains/orders/` | UIFPE-109 |
| 9 | Tax | `domain:tax` | `domains/tax/` | UIFPE-110 |
| 10 | Subscriptions | `domain:subscriptions` | `domains/subscriptions/` | UIFPE-111 |

---

## 4. Sprint 0 Stories

Ready to pick up. Each names its Epic/Capability, what it produces, and acceptance criteria.

### S0-1 · Stand up the Schema Registry (dev)
- **Epic / Capability:** Schema Registry & Representation Foundation / Schema
- **Produces:** running dev registry (Apicurio)
- **Acceptance:** registry runs in dev; an FDE can register a schema and fetch it; compatibility policy documented.

### S0-2 · Publish CI workflow template
- **Epic / Capability:** GitHub Actions Foundation / Delivery
- **Produces:** `.github/workflows/`
- **Acceptance:** on PR, a domain is built, tested, security-scanned, and its schema validated; merge blocked on failure.

### S0-3 · Publish CD workflow
- **Epic / Capability:** Delivery Foundation / Delivery
- **Produces:** `.github/workflows/domain-cd.yml` (reusable root workflow, matrixed per touched domain)
- **Acceptance:** a domain promotes Development → UAT → Production with a gate per stage.

### S0-4 · Build the first API end-to-end
- **Parent / Capability:** Domain: Product (UIFPE-51) / `capability:api` — live as UIFPE-39, reparented from API Foundation once the product domain existed to consume it.
- **Produces:** `domains/product/src/api/` (teaching reference: `../delivery-lab/reference-implementations/api/developer-registration-api/`)
- **Acceptance:** contract designed and registered first; implemented in `src/` with `qe/`; `src/Dockerfile` + `infrastructure/` (terraform/helm) present; CI green; deployed via the root `.github/workflows/domain-cd.yml` to dev; ownership registered in JSM.

### S0-5 · Build a reference MCP server
- **Parent / Capability:** Domain: Product (UIFPE-51) / `capability:mcp` — live as UIFPE-40, reparented from MCP Foundation once the product domain existed to consume it.
- **Produces:** `domains/product/src/mcp/<name>/`
- **Acceptance:** exposes at least one tool over MCP with auth and tests; tool contract registered.

### S0-6 · Author API playbook
- **Program concern (not a Capability):** Architecture Foundation / `program:architecture-design`
- **Produces:** `../delivery-lab/blueprints/api-playbook.md` (delivered)
- **Acceptance:** walks an FDE from Story to running API; matches the FDE workflow. *(Done in foundation.)*

---

## 5. Spikes & defects

| Type | Summary | Feeds |
|---|---|---|
| Spike | Evaluate MCP framework / SDK choice | MCP Foundation |
| Spike | Select Knowledge Graph store | Knowledge Graph Foundation |
| Bug | Filed against any shipped domain defect | the owning Domain epic, labelled with the relevant `capability:*` |
| JSM Incident | Any production incident | `jsm-alignment.md` |

---

## 6. Creating these in live Jira

When you give the go, these can be created in **UIFPE** via the Atlassian tooling. Sequence: create the Initiative (JPD) → 13 Capabilities → Foundation Epics → Sprint 0 Stories → Spikes. Reconcile or migrate the legacy Phase-0 issues first so the two models don't coexist unlabeled.
