# UIF Capability Model

This is the definitive capability model for the Unified Integration Fabric (UIF): the thirteen capabilities, what each owns, and the Foundation Epic and starter stories that seed its Jira backlog. Architecture & Design is the frame the thirteen sit in, not a fourteenth capability — it is documented here for completeness but tracked in Jira as a program/foundation concern, never a `capability:*` label or a Capability parent. A Product Manager uses it to create Jira Capabilities and Epics; an FDE uses each capability's scope, inputs/outputs, and starter stories to decompose work. It aligns with the Jira hierarchy in `jira-workflow.md` and the create-ready issue list in `backlog-seed.md`.

UIF is an AI-assistive integration platform aligned with JPD, Jira Software, JSM, the Four-in-a-Box operating model, and Forward Deployed Engineering (FDE). Its engineering principles: API First, Event Driven, Data Centric, Schema Registry First, Semantic By Design, Knowledge Graph Enabled, MCP, A2A, Agent Ready, Cloud Native, DevSecOps (`../principles/README.md`).

## The thirteen capabilities

| # | Capability | Layer | Foundation Epic |
|---|---|---|---|
| 1 | API | Integration Channel | API Foundation |
| 2 | Event | Integration Channel | Event Foundation |
| 3 | Data | Information Models | Canonical & Semantic Data Modelling Foundation |
| 4 | Schema | Representation Models | Schema Registry & Representation Foundation |
| 5 | Semantics | Meaning Layer | Semantics Foundation |
| 6 | Knowledge Graph | Relationship Layer | Knowledge Graph Foundation |
| 7 | MCP | Context & Tool Connectivity | MCP Foundation |
| 8 | A2A | Agent Communication | A2A Foundation |
| 9 | Agent | Intelligence & Automation | Agent Framework Foundation |
| 10 | Infrastructure | Platform Foundation | Cloud Platform Foundation |
| 11 | Security | Platform Foundation | Security Foundation |
| 12 | Observability | Platform Foundation | Observability Foundation |
| 13 | Delivery | Platform Foundation | Delivery Foundation |

Architecture & Design sits above these as the frame (see below) — it is not row 14; it has no `capability:*` label.

## Capability relationship hierarchy

```
Architecture & Design            Foundation
    │
    ├── API · Event              Integration Channels
    │
    ├── Data                     Information Models
    │   Schema                   Representation Models
    │   Semantics                Meaning Layer
    │   Knowledge Graph          Relationship Layer
    │
    ├── MCP                      Context & Tool Connectivity
    │   A2A                      Agent Communication
    │   Agent                    Intelligence & Automation
    │
    └── Infrastructure · Security · Observability · Delivery   Platform Foundation
```

## Data vs. Schema — a deliberate separation

Data and Schema are **separate capabilities**. Data is the *information modelling* layer (what information means and what contracts exist between producers and consumers); Schema is the *representation* layer (how that information is encoded and kept compatible on the wire).

| | Data — Information Modelling Layer | Schema — Representation Layer |
|---|---|---|
| **UIF owns** | Canonical data models, semantic data models, data contracts, information models, exchange models | Avro, Apicurio, JSON Schema, Protobuf, OpenAPI schemas, AsyncAPI schemas, schema registry, schema versioning, schema compatibility |
| **UIF does NOT own** | Data products, data governance, data quality, data lineage, data ownership, master data management, data stewardship | — |
| **Answers** | What information is exchanged? What is the canonical representation? What business contracts exist between producers and consumers? | How is information represented? How is it exchanged? How is compatibility maintained? |

---

# Architecture & Design — the frame, not a numbered capability

> Documented here for completeness. Architecture & Design is **not** one of the thirteen Jira Capabilities and carries no `capability:*` label — it is the frame the thirteen sit in (`../architecture-design/`). In Jira it is tracked as a program/foundation concern (`program:architecture-design`), and its Foundation Epic below is not parented to a Capability issue.

## Purpose
The foundation for the fabric. Defines reference architecture, solution design, and domain models so every capability composes into one coherent whole.

## Business Outcome
Coherent, reusable architecture that reduces rework and lets teams compose rather than reinvent.

## Scope
Reference architecture, solution architecture, domain modelling, bounded contexts, cross-capability design method.

## Key Responsibilities
- Maintain the UIF reference architecture and the thirteen-capability model.
- Define bounded contexts and domain models before contracts are designed.
- Review cross-capability designs for coherence.

## Inputs
JPD Initiatives, business outcomes, existing landscape.

## Outputs
Reference architecture, solution designs, domain models, architecture decision records.

## Foundation Epic
**Architecture Foundation** — establish the reference architecture and the end-to-end golden path, proven on one real domain.

## Starter Stories
1. Publish the UIF reference architecture overview.
2. Define the domain model / bounded contexts for the pilot.
3. Establish the ADR process and template.
4. Define the cross-capability design review checklist.
5. Document the target-state integration topology.
6. Prove the golden path end-to-end on `developer-registration-api`.

## Dependencies
None (foundation). Feeds every other capability.

## Ownership
FDE (solution architecture); QE (architecture testability review).

---

# 1. API

## Purpose
Synchronous, request/response capabilities exposed and consumed as governed, discoverable products.

## Business Outcome
Reusable, self-service APIs that shorten integration time and remove point-to-point sprawl.

## Scope
API design, contract registration, versioning, auth, discovery/portal.

## Key Responsibilities
- Contract-first API design (API First).
- Register OpenAPI contracts before implementation (Schema).
- Enforce auth and telemetry by default.

## Inputs
Domain models (Architecture & Design), canonical data models (Data), OpenAPI schema (Schema).

## Outputs
Governed API surfaces, published contracts, developer-portal entries.

## Foundation Epic
**API Foundation** — repeatable path from Story to a running, governed API surface in a domain.

## Starter Stories
1. Define the API design standard and contract format.
2. Stand up the API developer portal.
3. Author the API playbook (`../delivery-lab/blueprints/api-playbook.md`).
4. Build `developer-registration-api` end-to-end.
5. Define API versioning & compatibility policy.
6. Add default auth and telemetry to the API template.
7. Publish consumer-driven contract testing guidance.

## Dependencies
Architecture & Design, Data, Schema, Security, Observability.

## Ownership
FDE (design, build); UX (developer portal experience); QE (contract & integration tests).

---

# 2. Event

## Purpose
Event-driven, asynchronous integration — publish/subscribe with registered schemas so systems stay decoupled.

## Business Outcome
Loosely coupled systems that react to change in near real time instead of polling or batch.

## Scope
Event design, topics/streams, publish/subscribe patterns, event versioning.

## Key Responsibilities
- Design events against canonical models and register AsyncAPI/event schemas first.
- Define topology, naming, and versioning.
- Ensure consumers are never broken silently.

## Inputs
Domain models, canonical data models (Data), event schema (Schema).

## Outputs
Event-producing/consuming domains, registered event contracts.

## Foundation Epic
**Event Foundation** — repeatable path to a published/consumed event with a registered schema.

## Starter Stories
1. Define the event design standard and naming convention.
2. Stand up the event broker/topology in dev.
3. Define event versioning & compatibility policy.
4. Author the event playbook.
5. Build a reference event producer + consumer.
6. Add event telemetry to the template.

## Dependencies
Architecture & Design, Data, Schema, Security, Observability.

## Ownership
FDE (design, build); QE (event contract tests).

---

# 3. Data

## Purpose
The **Information Modelling Layer**. Defines what information is exchanged and its canonical representation, and the business contracts between producers and consumers. (See the Data vs. Schema table above.)

## Business Outcome
A shared, canonical understanding of business information so producers and consumers integrate against one model, not many local ones.

## Scope
Canonical data models, semantic data models, data contracts, information models, exchange models.
**Out of scope:** data products, data governance, data quality, data lineage, data ownership, MDM, data stewardship.

## Key Responsibilities
- Maintain canonical and semantic data models.
- Publish data contracts between producers and consumers.
- Define exchange models shared across API and Event.

## Inputs
Domain models (Architecture & Design), semantics (Semantics).

## Outputs
Canonical data models, data contracts, exchange models — consumed by API, Event, Schema, Knowledge Graph.

## Foundation Epic
**Canonical & Semantic Data Modelling Foundation** — establish canonical/semantic models and the data-contract standard.

## Starter Stories
1. Define the canonical data model standard and repository.
2. Publish the data-contract template and lifecycle.
3. Model the canonical entities for the pilot domain.
4. Define exchange models shared by API and Event.
5. Map canonical models to the semantic layer (Semantics).
6. Define producer/consumer contract review process.

## Dependencies
Architecture & Design, Semantics; consumed by API, Event, Schema, Knowledge Graph.

## Ownership
FDE (modelling, contracts); QE (contract conformance).

---

# 4. Schema

## Purpose
The **Representation Layer**. Defines how information is encoded and exchanged and how compatibility is maintained across versions. (See the Data vs. Schema table above.)

## Business Outcome
Contracts are enforceable and evolvable — changes are validated automatically instead of breaking consumers in production.

## Scope
Avro, Apicurio, JSON Schema, Protobuf, OpenAPI schemas, AsyncAPI schemas, schema registry, schema versioning, schema compatibility.

## Key Responsibilities
- Run the schema registry (Apicurio) as the register-first source of truth.
- Enforce compatibility (backward/forward) in CI.
- Version every API/event/data schema.

## Inputs
Canonical data models & data contracts (Data), API/event designs.

## Outputs
Registered, versioned schemas; compatibility gates in CI.

## Foundation Epic
**Schema Registry & Representation Foundation** — stand up Apicurio and the register-first, compatibility-enforced workflow.

## Starter Stories
1. Stand up the Apicurio schema registry (dev).
2. Define the register-first workflow and compatibility policy.
3. Add schema validation to the CI workflow (Delivery).
4. Register the pilot's OpenAPI and data schemas.
5. Define supported formats (Avro/JSON Schema/Protobuf/OpenAPI/AsyncAPI) and when to use each.
6. Author the schema playbook.

## Dependencies
Data (models/contracts to represent), Delivery (CI enforcement).

## Ownership
FDE (registry, schemas); QE (compatibility gate verification).

---

# 5. Semantics

## Purpose
The **Meaning Layer** — ontologies, vocabularies, and canonical meaning so terms mean the same thing across every capability.

## Business Outcome
Consistent meaning across APIs, events, data, and agents — eliminating the "same word, different meaning" class of integration defects.

## Scope
Ontologies, controlled vocabularies, canonical meaning, semantic annotations.

## Key Responsibilities
- Maintain the canonical vocabulary/ontology and its extension process.
- Annotate schemas and data models with semantics.

## Inputs
Canonical data models (Data), domain models.

## Outputs
Ontology/vocabulary, semantic annotations — consumed by Data, Knowledge Graph, Agent.

## Foundation Epic
**Semantics Foundation** — establish the canonical vocabulary and the process to extend it.

## Starter Stories
1. Define the canonical vocabulary/ontology approach.
2. Stand up the vocabulary repository.
3. Model the pilot domain's core terms.
4. Define the term-extension/approval process.
5. Annotate the pilot's data models with semantics.

## Dependencies
Data; consumed by Knowledge Graph, Agent.

## Ownership
FDE (modelling); QE (consistency checks).

---

# 6. Knowledge Graph

## Purpose
The **Relationship Layer** — a linked, queryable representation of enterprise entities and their relationships.

## Business Outcome
Agents and applications reason over connected context, enabling capabilities impossible with isolated records.

## Scope
Graph store, ingestion, entity resolution, query interface.

## Key Responsibilities
- Populate the graph from governed data/events using canonical semantics.
- Provide a query interface for agents and applications.

## Inputs
Data products/events, semantics.

## Outputs
Queryable knowledge graph; graph-backed retrieval for agents.

## Foundation Epic
**Knowledge Graph Foundation** — graph store, ingestion pattern, and query interface.

## Starter Stories
1. Spike: select the knowledge graph store.
2. Stand up the graph store (dev).
3. Define the ingestion pattern from data/events.
4. Define entity resolution rules.
5. Expose a query interface.
6. Load the pilot subgraph.

## Dependencies
Data, Semantics; consumed by Agent.

## Ownership
FDE (build); QE (query correctness).

---

# 7. MCP

## Purpose
**Context & tool connectivity** — expose UIF capabilities to AI agents through the Model Context Protocol so agents consume governed capabilities, not bespoke integrations.

## Business Outcome
Agents reach enterprise capabilities safely through one standard protocol, with governance intact.

## Scope
MCP servers, tool definitions, agent access/auth, the exposed-tool catalog.

## Key Responsibilities
- Build governed MCP servers per bounded capability.
- Register tool contracts (Schema); enforce least-privilege agent access (Security).

## Inputs
Governed capabilities (API, Data, Knowledge Graph), tool contracts.

## Outputs
MCP servers exposing governed tools.

## Foundation Epic
**MCP Foundation** — a reference MCP server and the pattern to build more.

## Starter Stories
1. Spike: evaluate MCP framework/SDK.
2. Define the MCP server pattern and auth model.
3. Build a reference MCP server.
4. Register the exposed tool contracts.
5. Publish the MCP tool catalog.
6. Author the MCP playbook.

## Dependencies
API, Data, Knowledge Graph, Schema, Security, Observability.

## Ownership
FDE (build); QE (tool contract & access tests).

---

# 8. A2A

## Purpose
**Agent communication** — how UIF agents discover, delegate to, and coordinate with each other through a governed routing pattern.

## Business Outcome
Multi-agent tasks are handled through governed collaboration instead of hard-wired agent coupling.

## Scope
Agent routing, discovery, delegation, inter-agent trust.

## Key Responsibilities
- Define the routing/collaboration pattern and agent discovery.
- Enforce inter-agent auth and traceable, bounded delegation.

## Inputs
Agent capability descriptors, MCP tools.

## Outputs
Agent routing/collaboration infrastructure and reference implementation.

## Foundation Epic
**A2A Foundation** — agent routing/collaboration pattern and reference implementation.

## Starter Stories
1. Define the A2A routing and discovery pattern.
2. Define the inter-agent trust/auth model.
3. Build a reference agent-routing pattern.
4. Define delegation and bounded-authority rules.
5. Add A2A hop tracing (Observability).

## Dependencies
Agent, MCP, Schema, Security, Observability.

## Ownership
FDE (build); QE (routing & delegation tests).

---

# 9. Agent

## Purpose
**Intelligence & automation** — AI agents and agentic workflows as first-class integration participants under the same governance as any other capability surface.

## Business Outcome
Automation of complex, cross-capability tasks with governed, observable AI.

## Scope
Agent task logic, prompts, evaluations, tool/data access, human-in-the-loop policy.

## Key Responsibilities
- Build agents that consume capabilities only through governed interfaces (API, MCP, Knowledge Graph).
- Version prompts; maintain evaluations alongside tests.

## Inputs
MCP tools, knowledge graph, semantics, data contracts.

## Outputs
Governed, evaluated agents and agentic workflows.

## Foundation Epic
**Agent Framework Foundation** — agent anatomy (`prompts/`, `evals/`), evaluation, governance.

## Starter Stories
1. Define the agent anatomy (`prompts/`, `evals/`).
2. Define the agent evaluation bar and harness.
3. Define the tool/data access and human-in-the-loop policy.
4. Build a reference agent (RAG over the knowledge graph).
5. Author the agent playbook.

## Dependencies
MCP, A2A, Knowledge Graph, Semantics, Data, Security, Observability.

## Ownership
FDE (build); UX (agent interaction experience); QE (evaluations, reliability).

---

# 10. Infrastructure

## Purpose
The runtime substrate — AWS, Kubernetes, and Terraform — every domain deploys onto.

## Business Outcome
Reproducible, consistent, cost-governed infrastructure as code across all domains.

## Scope
AWS account/cluster topology, base Terraform modules, Kubernetes/Helm, network and cost guardrails.

## Key Responsibilities
- Provide base Terraform modules and the cluster topology.
- Enforce container-first, IaC-only (no click-ops).

## Inputs
Module IaC requirements.

## Outputs
Provisioned, reproducible runtime environments; base IaC modules.

## Foundation Epic
**Cloud Platform Foundation** — the account/cluster topology and base IaC modules.

## Starter Stories
1. Define the AWS account and Kubernetes cluster topology.
2. Publish base Terraform modules.
3. Define the Helm deployment standard.
4. Establish network and cost guardrails.
5. Provision the dev environment for the pilot.

## Dependencies
Security; consumed by all domain builds and Delivery.

## Ownership
FDE (platform engineering); QE (environment reliability).

---

# 11. Security

## Purpose
Identity, access, and DevSecOps — security as a design input, applied by default to every domain and agent.

## Business Outcome
"Secure" is the starting state; risk is reduced by construction, not by late hardening.

## Scope
Identity/auth model, least-privilege access, secret management, scanning policy, data classification, policy-as-code.

## Key Responsibilities
- Define auth standards and enforce deny-by-default, least privilege (including for MCP/A2A agents).
- Run security scanning in CI; manage secrets and classification.

## Inputs
Module and agent designs, data classification.

## Outputs
Auth model, scanning gates, policy-as-code, secret management.

## Foundation Epic
**Security Foundation** — identity model, auth standards, scanning policy, secret management.

## Starter Stories
1. Define the identity and auth model.
2. Establish secret management.
3. Add security scanning to CI (Delivery).
4. Define the data classification scheme.
5. Publish policy-as-code for pipeline enforcement.
6. Define least-privilege access for agents (MCP/A2A).

## Dependencies
Feeds every capability; enforced via Delivery.

## Ownership
FDE (implementation); QE (security test verification).

---

# 12. Observability

## Purpose
Metrics, logs, traces, and SLOs — observable-by-default signals for every capability.

## Business Outcome
Live capabilities can be understood, measured, and trusted; operations and quality run on real signals.

## Scope
Telemetry standards, the three pillars (metrics/logs/traces), SLO framework, alerting, the observability platform.

## Key Responsibilities
- Provide telemetry standards and the SLO framework.
- Ensure trace propagation across API, Event, MCP, and A2A hops.

## Inputs
Module signals, SLO targets.

## Outputs
Dashboards, SLOs, alerts, traces — feeding QE and JSM.

## Foundation Epic
**Observability Foundation** — telemetry standards, SLO framework, and platform.

## Starter Stories
1. Define telemetry conventions (metrics/logs/traces).
2. Stand up the observability platform.
3. Define the SLO framework and alerting model.
4. Add standard telemetry to templates.
5. Implement trace propagation across hops.

## Dependencies
Infrastructure; feeds QE (quality bar) and JSM (operations).

## Ownership
FDE (instrumentation); QE (owns SLOs/quality bar).

---

# 13. Delivery

## Purpose
CI/CD — how domains are built, verified, and promoted to production.

## Business Outcome
Repeatable, gated shipping — quality and security enforced automatically, not manually.

## Scope
GitHub Actions for both CI and CD, promotion gates, release management.

## Key Responsibilities
- Provide the standard CI workflow (build/test/scan/schema-validate) and CD workflow (Development→UAT→Production), both GitHub Actions.
- Maintain the reusable root workflows every domain shares.

## Inputs
Module code, security policy, schema registry.

## Outputs
CI workflow, CD workflow, released domain images.

## Foundation Epic
**Delivery Foundation** — standard CI and CD (both GitHub Actions) with gates.

## Starter Stories
1. Publish the CI workflow template (`.github/workflows/domain-ci.yml`).
2. Publish the CD workflow (`.github/workflows/domain-cd.yml`).
3. Integrate security scanning (Security) and schema validation (Schema) into CI.
4. Define the environment promotion gates (GitHub Environments, per-env approvals).
5. Prove the pipeline on the pilot domain.

## Dependencies
Security, Schema, Infrastructure.

## Ownership
FDE (pipelines); QE (gate verification).

---

## Reconciliation note

**Fixed (2026-08-04):** `capabilities/schema-registry/` was renamed to `capabilities/schema/`; `CLAUDE.md` already uses "Schema" and the thirteen-capability list; this file now treats Architecture & Design as the frame (not a numbered capability), matching `../capabilities/docs/capability-domain-jira-map.md`.

**Still open:** `capabilities/data/README.md` still scopes Data to "data products and data contracts" rather than the information-modelling-only layer defined above (Data does NOT own data products/governance/quality/lineage/stewardship/MDM). Reconcile that README to match this model's Data-vs-Schema split before treating it as settled.
