# FDE Build & Deploy Workflow

This is the golden path an FDE follows to take a unit of work from a Jira Story to a running, operated capability. It is the single reference an FDE needs to go from "I have a Story" to "it is live and owned." Every step maps to a place in this repository, a capability in `../jira/capability-model.md`, or a tool in the UIF toolchain.

A PM or UX Lead can read the flow to understand how work moves; an FDE follows it literally.

## Prerequisites

Read `CLAUDE.md`, `README.md`, and `PLANNING.md` once before your first domain.

## The flow

```
JPD Initiative            WHY (../jira/jpd-alignment.md)
    ↓
Capability (label)        WHAT — the standard(s) the work conforms to (../jira/capability-model.md)
    ↓
Domain epic                the owning parent — where it ships (../../capabilities/docs/capability-domain-jira-map.md)
   [or: Foundation Epic — pre-domain platform groundwork only, no domain yet]
    ↓
Story                      parent: Domain epic · labels: capability:<x>, domain:<name>
    ↓
Architecture & Design     domain model, solution design
    ↓
Canonical Data Model      Data capability — information modelling
    ↓
Data Contract             producer/consumer contract
    ↓
Schema Definition         Schema capability — representation (register first)
    ↓
Semantics                 meaning layer / canonical vocabulary
    ↓
Knowledge Graph           relationships (where applicable)
    ↓
MCP                       expose tools to agents (where applicable)
    ↓
A2A                       agent collaboration (where applicable)
    ↓
Agent Workflow            intelligence & automation (where applicable)
    ↓
Domain Implementation     domains/<domain>/{src,api,events,...}
    ↓
GitHub Actions            CI — build, test, scan, validate
    ↓
GitHub Actions            CD — Development → UAT → Production
    ↓
AWS / Kubernetes          RUN
    ↓
JSM                       OPERATE (../jira/jsm-alignment.md)
```

## Steps

1. **Read the foundations** — `CLAUDE.md`, `README.md`, `PLANNING.md`.
2. **Start from a JPD Initiative.** Confirm the WHY.
3. **Locate the Capability** for the work in `../jira/capability-model.md` and its **Domain epic** (where it ships). Create the Jira trace with the Domain epic as parent and the capability as a label — Story: parent `Domain: <name>`, labels `capability:<x>`, `domain:<name>` (`../jira/jira-workflow.md`). Only pre-domain platform groundwork parents to a Capability Foundation Epic instead.
4. **Read the capability documentation** and its playbook (`../../capabilities/`, `../../delivery-lab/blueprints/`).
5. **Design the architecture** — solution design against the domain model (Architecture & Design).
6. **Define the canonical data model** for the information exchanged (Data — information modelling).
7. **Publish the data contract** between producer and consumer (Data).
8. **Define and register the schema first** — Avro / JSON Schema / Protobuf / OpenAPI / AsyncAPI in Apicurio (Schema). Registration precedes implementation.
9. **Apply semantics** — reference the canonical vocabulary, don't redefine terms (Semantics).
10. **Wire the knowledge graph, MCP, A2A, and agent workflow** where the Story requires them — each through its governed interface.
11. **Design security and observability** as inputs, not additions (Security, Observability).
12. **Work inside your domain.** A domain is stamped once from the template with `../../create-domain.sh`; you implement into its capability folders:

    ```
    domains/<domain>/
    ```

    Example: the product API lives in `domains/product/src/api/`, and its business logic in `domains/product/src/apps/`.

13. **Implement** under `domains/<domain>/src/` (apps + the capability folders it touches, all built by `src/Dockerfile`) and **add tests + quality strategy** under `domains/<domain>/qe/`.
14. **CI — GitHub Actions:** build, test, security scan, schema validation. Merge only on green.
15. **CD — GitHub Actions:** promote Development → UAT → Production (reusable `domain-cd.yml`).
16. **Run** on AWS / Kubernetes.
17. **Register operational ownership in JSM** and operate — feed defects back as Jira Bugs.

## Implementation rule

All code must live under a domain: `domains/<domain>/`. **Never create a root-level `src/` folder** (`../../CLAUDE.md`, rule 1). Each domain is a deployable, ownable unit; the horizontal capabilities appear as same-named folders inside it.

## Domain anatomy

A domain is stamped from `../../_domain-template/` and builds to **one container image** — the single build/deploy contract. The root stays minimal; code lives under `src/`, tests in `qe/`, experience in `ui/` + `ux/`, and everything that runs the image under `infrastructure/`:

```
domains/product/
├── README.md · CLAUDE.md · DOMAIN-CHECKLIST.md   # what it owns, skill, DoD gate
├── src/                  # all code → one image
│   ├── apps/             # business logic
│   ├── api/              # API surface (openapi.yaml)              (core)
│   ├── schemas/          # registered contracts                    (core)
│   ├── events/  mcp/  …  # opt-in capability surfaces
│   └── Dockerfile        # the one build context → one image
├── qe/                   # QE Lead — quality strategy + tests
├── ui/  ux/              # UX Lead owns
└── infrastructure/       # FDE — how the image runs
    └── terraform/ kubernetes/ helm/ observability/
```
CD (Development → UAT → Production) runs from the root `.github/workflows/domain-cd.yml`.

Capability surfaces a domain does not use (e.g. `agents/`, `a2a/`) are omitted at stamp time and added on demand — see `../../create-domain.sh` and `../../_domain-template/README.md`.

## Definition of Done

A Story is done only when the domain-level Definition of Done in `../../PLANNING.md` and the domain's `DOMAIN-CHECKLIST.md` are met: schema registered first (`src/schemas/`); code + tests in place (`src/`, `qe/`); `src/Dockerfile` builds the one image; `infrastructure/` (terraform/kubernetes/helm/observability) present; CI green; deployed via the root `domain-cd.yml` to the target environment; operational ownership registered in JSM.

## Where this path is proven

Phase 5 proves the whole path end-to-end on a pilot domain's API (e.g. `domains/product/src/api/`) (`../../PLANNING.md`).
