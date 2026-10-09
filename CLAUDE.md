# CLAUDE.md

Guidance for AI assistants and engineers contributing to this repository.

## What this repository is

This is the **Unified Integration Fabric (UIF)** — the **Engineering Operating System** for building enterprise APIs, Events, Data Products, Schema Registries, Semantics, Knowledge Graphs, MCP capabilities, A2A capabilities, and AI Agents. Unlike a pure documentation framework, this repository holds **both** the engineering standards *and* the implementation code: capability documentation defines how things are built, and `domains/` is where they are actually built, tested, containerised, and shipped.

UIF is organised on two crossing axes. **Domains are vertical** — deployable, ownable business units (`product`, `program`, `customer`, `sku`, `price`, `stock`, `finance`, `hr`, `legal`, `identity`, `orders`, `tax`, `subscriptions`); code lives in `domains/<domain>/`. **Stacks are the RELAY application units** (`echo`, `beacon`, `harbor`) — independently deployable, never domains; code lives in `stacks/<stack>/` (ADR-0009, ADR-0010). **Capabilities are horizontal** — reusable disciplines (API, Event, Schema, MCP, …) defined once as central standards in `capabilities/<capability>/` and realized as same-named folders inside each domain or stack. The standard is central; the code is in the domain or stack.

**Why UIF exists.** UIF is Pearson's mission-critical integration transformation, built and operated through domain modelling with AI/agent-assisted orchestration. The target state it drives toward is schema-first, event-driven integration: batch ETL pipelines replaced by governed, schema-registered event streams that consumers subscribe to as data changes (**ETL → Zero-ETL**), and ESB-style central routing replaced by services publishing and subscribing to typed events (**ESB → eventing**). The Event, Data, and Schema capabilities carry this transformation; the Agent, MCP, and A2A capabilities make the workflow that builds and operates the fabric itself agent-assisted, with agents as first-class producers and consumers of the same governed contracts.

**The target is concrete.** Pearson's Enterprise Blueprint roadmap targets a **66% cumulative reduction** in legacy integrations — **1,220 → 435** — via reusable APIs/MCP tools/event streams, reaching that target at the Release 3 Go Live milestone where ESB, ETL, and SSDP are formally exited. *Source: Pearson Enterprise Blueprint, "UIF — Integration Reduction by Quarter" roadmap table, shared 2026-08-11.*

UIF standardises how a **Four-in-a-Box** team — Product Manager, Forward Deployed Engineer (FDE), UX Lead, QE Lead — discovers, designs, builds, deploys, and operates software, aligned end-to-end with JPD, Jira Software, and JSM.

> **Naming note.** This program is titled **Unified Integration Fabric (UIF)** here. It was previously documented under the engineering name *Universal* Integration Fabric; both names refer to the same initiative. Use **Unified Integration Fabric (UIF)** as the canonical name in this repository, and treat "Universal" as a prior synonym if it appears in older material.

Read `README.md` and `PLANNING.md` before contributing. **AI coding agents** read [`AGENTS.md`](AGENTS.md) and the active story [`PRD.md`](PRD.md). **Team FAQ:** [`jira/uif-faq.md`](jira/uif-faq.md).

> **Before your first pull request, read [`DEFINITION-OF-DONE.md`](DEFINITION-OF-DONE.md) — mandatory, humans and agents alike.** It states what each board status means, who moves it (CI owns two transitions, humans own the rest), and when a work item is actually finished. Note that **"Definition of Done" means three different things in this repository** — a CI structural gate, each capability's §5 craft checklist, and that document's workflow rules. §0 of it disambiguates them; getting them confused is the most common early mistake here.

## The mental model

| Layer | Meaning |
|---|---|
| **JPD** | WHY — product discovery: Idea → Opportunity → Initiative |
| **Capability** | WHAT — the platform capability a piece of work belongs to |
| **Four-in-a-Box** | WHO — PM / FDE / UX Lead / QE Lead |
| **FDE** | HOW — end-to-end technical execution |
| **`domains/`** | BUILD — deployable business units; where implementation code lives |
| **GitHub Actions** | CI — build, test, scan, validate — and CD — Development → UAT → Production (reusable `workflow_call` workflows) |
| **AWS / Kubernetes** | RUN |
| **JSM** | OPERATE — incident, problem, change, service request |

## Repository layout

```
uif/
├── AGENTS.md · CLAUDE.md · PRD.md · README.md · PLANNING.md
├── create-domain.sh          bootstrap a new domain from the mould
├── architecture-design/      the frame: principles, standards, patterns, decisions,
│                             reference-architectures, operating-model — NO code
├── capabilities/             13 horizontal disciplines — standards only, NO code
├── delivery-lab/             starter material (templates, accelerators, blueprints,
│                             starter-kits) + reference-implementations/ (runnable, teaching-only)
├── jira/                     JPD / Jira Software / JSM alignment
├── domains/                  deployable business units — ALL implementation code lives here
├── _domain-template/         the mould every domain is stamped from
├── platform/                 shared, cross-stack code: terraform/ (OIDC federation, IRSA) and
│                             python/ (stdlib-only runtime packages every stack's Lambda zips
│                             copy in, e.g. the Pearson Notifications client) — the actual
│                             code; capabilities/infrastructure/ stays docs-only
└── .github/                  CODEOWNERS + root CI workflows (monorepo path filters)
```

## Repository rules (non-negotiable)

1. **No root-level `src/`.** All implementation code lives under `domains/<domain>/` (or `delivery-lab/reference-implementations/` for teaching examples).
2. **`capabilities/` and `architecture-design/` never contain executable code.** They hold Standards, Principles, Governance, Patterns, and Guidance only.
3. **Every domain builds to ONE container image — the single build/deploy contract.** A domain keeps a minimal root (`README.md`, `CLAUDE.md`, `DOMAIN-CHECKLIST.md`); all code and capability surfaces live under `src/` (`apps/`, `api/`, `schemas/`, opt-in `events/`, `mcp/`, …) with `src/Dockerfile` producing the image; tests + quality in `qe/`; experience in `ui/` + `ux/`; everything that runs the image in `infrastructure/` (`terraform/`, `kubernetes/`, `helm/`, `observability/`). Stamp a new domain with `create-domain.sh`; the mould is `_domain-template/`.
4. **Delivery-lab holds starter material (never running) and reference implementations (runnable, teaching-only, not deployed as a domain).** Templates, accelerators, blueprints, and starter-kits are never running services; `reference-implementations/` is runnable code for teaching but is not stamped or deployed like a `domains/<domain>/`.
5. **Every capability maps to a Jira Capability; every domain to a deployable unit.** Work traces JPD Initiative → Capability → Epic → Story → Task/Sub-task (plus Spike, Bug). Operational work traces to JSM.

## The thirteen capabilities

Use these names exactly; don't introduce synonyms. Each has a folder under `capabilities/`, a matching Jira Capability, and a same-named folder inside each domain that uses it.

API · Event · Data · Schema · Semantics · Knowledge Graph · MCP · A2A · Agent · Infrastructure · Security · Observability · Delivery

**Architecture & Design** is not one of the thirteen — it is the frame they sit in, and lives at the top level in `architecture-design/`.

## Writing style (for all documentation)

- **Lead with a plain-language paragraph** stating what the document is and why it matters — readable by a PM or UX Lead without prior context — then let technical depth build after it for FDEs and QE.
- **Plain, precise, and dense.** This domain is thick with buzzwords (agentic, semantic, event-driven, MCP, A2A). Use a term only when it does real work, and ground it on first use. Never use a term as a substitute for explaining the mechanism.
- **Active voice, declarative sentences. No marketing register** — no superlatives, no rhetorical questions, say it once.
- **State the why before the what, and the what before the how.**
- **Structure over prose for reference material** (catalogs, comparisons, checklists → lists/tables); prose for arguments and explanation.
- **Link, don't duplicate.** If a concept is defined elsewhere, reference it. There's no build step to catch drift between two copies.

## Architectural thinking

Check any pattern, standard, or module design against these before writing it down:

- **Which capability(ies) does this touch?** Most real work crosses capability boundaries (an Agent consumes APIs, Events, and Data). Name the capabilities it spans rather than forcing it into one.
- **Distributed execution vs. shared standard.** Be explicit about what an FDE decides locally versus what must conform to a UIF standard. Silence here creates either sprawl or a bottleneck.
- **Reusability.** If a pattern only serves one module's one use case, say so — don't dress a one-off as a platform capability.
- **Security and Observability are inputs to the design, not a later review.** A design that doesn't state its security posture and its observability signals isn't finished. (Secure By Default, Observable By Default.)
- **Schema Registry first.** APIs, events, and data contracts are registered before implementation, not after.
- **Agents (MCP/A2A) are first-class participants.** Reason about them the way you'd reason about any other system consuming or exposing a capability — same governance, same observability, same lifecycle.

## Engineering principles for `domains/` code

- **Reference-quality, then production-quality.** A starter-kit or the first domain to exercise a capability should teach the golden path clearly. Once the pattern is proven, domains built from it are held to production standards (tests, scans, IaC, pipelines).
- **Avoid tool lock-in in guidance; be explicit where UIF mandates a tool.** UIF *does* standardise on AWS, Kubernetes, Terraform, and GitHub Actions for both CI and CD — state that as the mandate it is. Elsewhere in `capabilities/` guidance, illustrate patterns portably.
- **No speculative abstraction.** Three similar domains are better than a premature framework-within-the-framework. Extract shared code into a reference implementation or shared-library only after the third repetition. Likewise, a domain only carries the capability folders it actually uses (selective scaffold — see `create-domain.sh`).
- **Security and governance non-negotiables apply to example code exactly as to production code.** An example that models bad practice teaches bad practice.

## Tech stack & tooling

> **Working default, not a verified Pearson policy.** There is no confirmed Pearson standards citation for this section (compare the Apicurio section in `capabilities/api/guidelines.md`, which does carry one). This is a sensible default derived from what's actually used across this repo's domains, reference implementations, and feature branches — kept consistent with "Avoid tool lock-in in guidance" above. Replace it wholesale if/when a real Pearson IDE/tooling standard is confirmed, citing the source.

UIF does not mandate one implementation language for domain business logic — the API capability's own worked example ships the same contract in both TypeScript and Python to prove this. What follows is the stack observed in actual use, not a new lock-in.

**Languages/runtimes in use:**
- **Python 3.9–3.12** — the FastAPI reference implementation, and the AWS Glue/Lambda ETL apps built for the product domain's data-lake work (`boto3`, tested with `pytest`, linted with `ruff`).
- **TypeScript / Node 20** — the Express reference implementation (`vitest` for tests, ESLint + Prettier for lint/format).
- **SQL** — Athena/Glue queries against the data lake (e.g. `src/schemas/mapping/<domain>/*/sql/*.sql`).
- **Contract languages** — OpenAPI 3.1, AsyncAPI 3.0, JSON Schema 2020-12 for every registered API/event/data contract, per the capability guidelines.

**Infrastructure tooling** is already mandated, not optional — see "Engineering principles for `domains/` code" above: AWS, Kubernetes, Terraform, Docker, Helm, GitHub Actions (CI and CD). This section cross-references that list; it doesn't add to it.

**IDE:** no editor is prohibited. **VS Code is the recommended default** — it has first-class support for every tool in this stack (Python/Ruff, ESLint/Prettier, Terraform, Docker, YAML/OpenAPI). The actual requirement isn't the editor, it's that whatever you use can run the same checks CI enforces before you push: `ruff` (Python), `eslint`/`prettier` (TypeScript), `tflint`/`checkov` (Terraform), `hadolint` (Dockerfile), and the domain's `pytest`/`vitest` suite. An IDE that can't run these locally just means you find out from CI instead of before it.

## Before creating structure

- **Propose new top-level structure and wait for confirmation** — the same way you'd propose a plan before a large code change. The thirteen capabilities, the thirteen domains, the top-level folders, and the rules above are settled; new *categories* beyond them are not. The three RELAY stacks (`echo`, `beacon`, `harbor` — ADR-0009, ADR-0010) are settled the same way, tracked separately from the domain count above since a stack is not a domain. (`program` was added as the 8th domain on 2026-08-11 and `price` and `stock` on 2026-08-14, all by framework-owner decision — see [ADR-0005](architecture-design/decisions/0005-gold-layer-shape.md) for program, which is the worked example of what adding a domain requires. An `income` domain was scaffolded and removed on 2026-08-14: it was an experiment, and a scaffold with no work behind it is drift. `beacon` was migrated from `domains/` to `stacks/` on 2026-08-28 per ADR-0010 — it was never really a domain, being a deployable stack.)
- **Don't create empty stub files "for completeness."** An empty scaffold implies a decision about what belongs there. Create a file when it has real content, a domain via `create-domain.sh` when there's work to build, and only the capability folders that domain actually uses.

## Brand alignment (visual artifacts only)

When a diagram, HTML artifact, slide, or dashboard is produced, use Plus Jakarta Sans and the Pearson palette — Pearson Purple `#0D004D` (primary), Light Purple `#C1BFFF`, Mist Purple `#EDECF6`, Amethyst `#512EAB` (secondary), accents Fuchsia `#DF41CF`, Amber `#FFCE00`, Turquoise `#56E2E1` used sparingly. Treat brand colors as HEX in digital work. Prefer text-renderable diagrams (Mermaid) over images so they stay reviewable in version control.

## When in doubt

Ask before restructuring, before inventing new categories beyond the thirteen capabilities or the domains, and before resolving an apparent contradiction between two pieces of existing guidance. This repository is meant to be picked up and trusted by engineers who weren't in the room when it was written — a clarifying question costs less than a silent wrong turn.
