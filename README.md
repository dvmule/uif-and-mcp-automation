# Unified Integration Fabric (UIF)

## Mission

UIF is the **Engineering Operating System** for building enterprise APIs, Events, Data Products, Schema Registries, Semantics, Knowledge Graphs, MCP capabilities, A2A capabilities, and AI Agents. It standardises how a team discovers, designs, builds, deploys, and operates software — so a new engineer can pick up a Jira Story, follow UIF guidance, build inside a domain, ship it through CI/CD, run it on the cloud, and operate it, without needing documentation that lives outside this repository.

**Why UIF exists.** UIF is Pearson's mission-critical integration transformation: it exists to move enterprise integration off two legacy patterns — **batch ETL** (scheduled, transform-then-load pipelines that lag the source system) and the **ESB** (a central bus mediating point-to-point integrations through custom routing logic) — onto schema-first, event-driven integration, built and operated through domain modelling (the `domains/` layer, one bounded business unit at a time) with AI/agent-assisted orchestration. Data moves as governed events the moment it changes, so consumers subscribe to a canonical, schema-registered stream instead of waiting on a batch job (**ETL → Zero-ETL**), and services communicate by publishing and subscribing to typed events rather than through a central broker's bespoke routing (**ESB → eventing**). The **Event**, **Data**, and **Schema** capabilities carry this transformation (event-driven contracts, canonical data models, a schema registry that gates every change); the **Agent**, **MCP**, and **A2A** capabilities make the workflow that builds and operates the fabric itself agent-assisted, with agents as first-class producers and consumers of the same governed contracts, not a system bolted on afterward.

**The target is concrete, not aspirational.** Pearson's Enterprise Blueprint roadmap tracks a reduction from **1,220 legacy, ESB/ETL/SSDP-mediated integrations to 435 reusable integration points** (APIs, MCP tools, event streams) — a **66% cumulative reduction** — reaching that target at the Release 3 Go Live milestone, the point at which ESB, ETL, and SSDP are formally exited. *Source: Pearson Enterprise Blueprint, "UIF — Integration Reduction by Quarter" roadmap table, shared 2026-08-11.*

UIF aligns the discovery, delivery, and operations toolchain into one operating model:

- **JPD** (Jira Product Discovery) — WHY
- **Jira Software** — WHAT / delivery
- **JSM** (Jira Service Management) — OPERATE
- **Four-in-a-Box** — the delivery team shape
- **Forward Deployed Engineering (FDE)** — HOW
- **AWS · Kubernetes · Terraform** — run and infrastructure
- **GitHub Actions** (CI and CD)
- **DevSecOps** — security throughout

> **Naming note.** Titled **Unified Integration Fabric (UIF)** here; previously documented as *Universal* Integration Fabric. Same initiative — use "Unified" as canonical in this repository.

## The two axes: domains × capabilities

UIF is organised on two crossing axes. Getting this distinction right is the whole point of the repository layout:

- **Domains are vertical** — deployable, ownable business units: `product`, `program`, `customer`, `sku`, `price`, `stock`, `finance`, `hr`, `legal`, `identity`, `orders`, `tax`, `subscriptions`. An FDE works inside one domain and ships it end to end. Code lives here, under `domains/<domain>/`.
- **Stacks are RELAY's application units** — `echo`, `beacon`, `harbor` — independently deployable, never domains (ADR-0009, ADR-0010). Code lives under `stacks/<stack>/`.
- **Capabilities are horizontal** — reusable technical disciplines (API, Event, Schema, MCP, …). Each is defined once as a central standard in `capabilities/<capability>/`, and appears inside a domain or stack as a same-named folder where that standard is implemented.

`capabilities/api/` says *how* an API is built; `domains/product/src/api/` is *the* product API. The standard is central; the code is in the domain.

## Who this is for

| Role | Owns | Reads first |
|---|---|---|
| **Product Manager** | WHY — vision, roadmap, OKRs, JPD Initiatives | `jira/jpd-alignment.md`, Mission above |
| **Forward Deployed Engineer (FDE)** | HOW — end-to-end delivery of a domain | `architecture-design/operating-model/fde-build-deploy-workflow.md`, then the relevant `capabilities/` + `delivery-lab/blueprints/` |
| **UX Lead** | EXPERIENCE — journeys, accessibility, design system | `architecture-design/operating-model/four-in-a-box.md` |
| **QE Lead** | QUALITY — test strategy, reliability, automation | `capabilities/observability/`, `architecture-design/operating-model/four-in-a-box.md` |

Ownership is enforced in `.github/CODEOWNERS`.

## Engineering principles

API First · Event Driven · Data Centric · Schema Registry First · Semantic By Design · Knowledge Graph Enabled · MCP Ready · A2A Ready · Agent Ready · Secure By Default · Observable By Default · Cloud Native · Container First · Self Service First · DevSecOps · Engineering Excellence

Each is defined in `architecture-design/principles/` — read it before designing anything.

## The thirteen capabilities

API · Event · Data · Schema · Semantics · Knowledge Graph · MCP · A2A · Agent · Infrastructure · Security · Observability · Delivery

Each has a folder under `capabilities/` (Standards, Governance, Patterns, Guidance — **no code**) and a matching Jira Capability. **Architecture & Design** is not in this list — it is the frame the capabilities sit in and lives at the top level in `architecture-design/`.

## Repository structure

```
uif/
├── CLAUDE.md · README.md · PLANNING.md
├── create-domain.sh          bootstrap a new domain from the mould
├── architecture-design/      the frame: principles, standards, patterns, decisions,
│                             reference-architectures, operating-model — NO code
├── capabilities/             13 horizontal disciplines — standards only, NO code
├── delivery-lab/             starter material FDEs copy from (templates, accelerators,
│                             blueprints, starter-kits — never running) + reference-implementations/
│                             (runnable, production-grade teaching examples — not deployed as a domain)
├── jira/                     JPD / Jira Software / JSM alignment
├── domains/                  deployable business units — ALL source code lives here
│   ├── product/  customer/  sku/  finance/  hr/  legal/  identity/  orders/  tax/  subscriptions/
├── _domain-template/         the mould every domain is stamped from
└── .github/                  CODEOWNERS + root CI workflows (monorepo path filters)
```

**Rule:** no root-level `src/`. All code lives under `domains/<domain>/` (or `delivery-lab/reference-implementations/` for teaching examples). Each domain builds to **one container image** from `src/Dockerfile` (the single build/deploy contract): code and capability surfaces under `src/`, tests in `qe/`, experience in `ui/` + `ux/`, and everything that runs the image under `infrastructure/` (`terraform/`, `kubernetes/`, `helm/`, `observability/`) — promotion runs from the root `.github/workflows/domain-cd.yml`, not a domain-local pipeline file. See `CLAUDE.md` for the full repository rules.

## How an FDE works (the golden path)

1. Read `CLAUDE.md`, `README.md`, `PLANNING.md`.
2. Start from a JPD Initiative → create/pick a Jira Capability → Epic → Story.
3. Read the relevant `capabilities/<capability>/` standard and its `delivery-lab/blueprints/` entry.
4. If the domain doesn't exist yet, stamp it: `./create-domain.sh <domain> --caps <c1,c2,...>`.
5. Work inside `domains/<domain>/` — design API, events, data contracts, schemas, security, and observability; register schemas first.
6. Implement under `src/` (apps + capability folders) with `src/Dockerfile`, add tests + quality in `qe/`, build & scan via **GitHub Actions**, deploy Development → UAT → Production via the same **GitHub Actions** (`.github/workflows/domain-cd.yml`) to **AWS/Kubernetes**.
7. Register operational ownership in **JSM** and operate.

Full detail: `architecture-design/operating-model/fde-build-deploy-workflow.md`. Every domain carries a `DOMAIN-CHECKLIST.md` gate.

## Commit, build & deploy — practical guide

This section answers the questions that come up most for someone new to the repo: *what goes where, what gets committed, and how does a change actually reach a running system?*

### What each top-level folder means, and what gets committed there

| Folder | What it holds | Committed? |
|---|---|---|
| `architecture-design/` | Principles, standards, patterns, ADRs, operating model | Docs only |
| `capabilities/` | The 13 capability standards (`guidelines.md` + tool how-tos) | Docs only — **no code, ever** |
| `delivery-lab/` | Templates, blueprints, starter-kits, accelerators (never running) + `reference-implementations/` (runnable, production-grade teaching examples, not deployed as a domain) | Starter material/teaching only — Yes for `reference-implementations/`, otherwise never a running service |
| `domains/` | Actual business-unit code | **Yes — this is where PRs land** |
| `_domain-template/` | The mould `create-domain.sh` stamps a new domain from | Yes |
| `jira/` | JPD/Jira/JSM alignment docs | Yes |
| `.github/` | CODEOWNERS + root CI workflows | Yes |

**The rule that resolves most "where does this go" questions:** if it's code that runs somewhere, it goes in `domains/<name>/` (or `delivery-lab/reference-implementations/` for teaching examples). If it's a standard, pattern, or checklist that no server ever executes, it goes in `capabilities/` or `architecture-design/`. Capabilities are **guard rails** — read and followed by an engineer at build time — never imported or executed at runtime; nothing in a built container image references `capabilities/`.

### Inside a domain — what you actually commit

```
domains/product/
├── README.md · CLAUDE.md · DOMAIN-CHECKLIST.md   # scope, build guidance, DoD gate
├── src/                       # ALL code lives here
│   ├── apps/                  # business logic
│   ├── api/                   # openapi.yaml + handlers               (core)
│   ├── schemas/                # registered contracts, Schema Registry First (core)
│   ├── events/  mcp/  agents/  # opt-in — only if this domain uses them
│   └── Dockerfile              # THE single build contract — one image
├── qe/                        # tests + quality strategy
├── ui/ + ux/                  # experience layer
└── infrastructure/            # everything that runs the image
    ├── terraform/               # cluster/network/IAM substrate
    ├── helm/                    # Kubernetes chart
    └── observability/           # dashboards, alerts
```
Promotion (Development → UAT → Production) runs from the root `.github/workflows/domain-cd.yml` — no domain-local pipeline file.

Every domain has `src/apps/`, `src/api/`, `src/schemas/`, `src/Dockerfile`, `qe/`, `infrastructure/`. Opt-in capability folders (`events/`, `mcp/`, `a2a/`, `agents/`, `knowledge-graph/`, `semantics/`) are added only if that domain uses them — `create-domain.sh --caps events,mcp` scaffolds selectively.

### Containerize — one Dockerfile, one image

Every domain builds to exactly **one container image** from `src/Dockerfile`. That image — not the source — is what CI scans, signs, and CD promotes. See `capabilities/infrastructure/docker.md` for the hardened multi-stage pattern (non-root, pinned digests, minimal runtime layer).

### CI — GitHub Actions (domains only, never capabilities)

CI triggers only on `domains/**` — a commit touching only `capabilities/` produces zero CI runs, because there is nothing there to build.

```
push/PR → domains/<domain>/**
    │
    ▼
lint (hadolint, tflint, checkov)
    │
    ▼
unit + contract tests (qe/)
    │
    ▼
docker build src/Dockerfile        →  the one image
    │
    ▼
Trivy scan (CRITICAL/HIGH fails build)
    │
    ▼
Syft SBOM  +  helm template | kubeconform | conftest
    │
    ▼
push to ECR + cosign sign            (main branch only)
```

The workflow lives at `.github/workflows/domain-ci.yml` and matrices across every domain via path filters — no per-domain CI config needed.

### CD — GitHub Actions → Kubernetes/AWS

CI stops at the image. The reusable `.github/workflows/domain-cd.yml` promotes that image digest through environments by updating `values-<env>.yaml` in git (Stage → Prod gated behind a GitHub `environment:` manual approval); **Argo CD** reconciles each cluster to match:

```
GitHub Actions CD: Development → UAT → Production
  each step: bump image digest in values-<env>.yaml → git commit
    │
    ▼
Argo CD syncs the target environment's cluster to match git
    │
    ▼
Kubernetes (AWS EKS) runs the pinned-digest, hardened Helm chart
```

Identity and secrets at runtime are **Entra ID** (OIDC identity provider) and **Keeper** (secrets vault via External Secrets Operator) — non-negotiable, CI-enforced (`capabilities/infrastructure/kubernetes.md`).

**GCP/other clouds:** not a UIF target — AWS is mandated. The pattern is portable at the Docker/Kubernetes/Helm layer, but the Terraform substrate and identity/secrets integration (Entra ID, Keeper, IRSA) assume AWS and would need to be swapped for another cloud.

Full detail on each step: `capabilities/infrastructure/docker.md`, `kubernetes.md`, and `architecture-design/operating-model/fde-build-deploy-workflow.md`.

## FAQ — Jira, GitHub, automation, and where to focus

The questions above ("what goes where") are about the repo. These are about the whole loop — Jira design, GitHub design, what's actually automated between them, and what a developer does day to day.

### How is Jira designed?

One rule, for one reason: **an FDE should never have to ask "which parent do I pick?"** — there is exactly one correct parent, always. This is the same choice Google's Buganizer and team-based trackers like Linear make: every issue has exactly **one owning parent** (a Component at Google, a Team/Project in Linear) plus any number of cross-cutting **labels**. Two parents for one issue is what creates the "which axis wins" confusion this repo explicitly avoids.

Applied to UIF's two axes:

- **The owning parent is always a Domain** — `domain:product`, `domain:customer`, etc. A **Domain epic** is where delivery Stories live, because a domain is *where the work ships and who owns it* (one team, one container image, one PR).
- **Capability is always a label, never a parent** — `capability:api`, `capability:event`, etc. Capability tells you *which standard the work must conform to* (`capabilities/<name>/guidelines.md`), which is cross-cutting: one Story can carry `capability:api` and `capability:schema` at once, the way one Buganizer bug can sit in multiple hotlists but only one Component.
- **Foundation Epics exist only for platform-wide groundwork that has no domain yet** — e.g. "stand up the Schema Registry" before any domain uses it. The moment work ships in a domain, it moves under that Domain epic.

```
Domain epic (owning parent — one per domain, e.g. "Domain: product")
    ↓
Story / Task / Sub-task / Spike / Bug
    labels: capability:<name> [, capability:<name2>, ...]
```

**A worked example, product's developer-registration API:**

| Field | Value |
|---|---|
| Parent | **Domain: product** epic |
| Issue | "Build product developer-registration API" (Story) |
| Labels | `capability:api`, `capability:schema` |
| Reads for guidance from | `capabilities/api/guidelines.md` + `stacks/apicurio/standards/guidelines.md` |
| Ships in | `domains/product/` |

An FDE working this Story asks one question ("what domain am I shipping in?") to find the parent, and a second, separate question ("what standards apply?") to set labels — never "which Epic do I attach to, the domain's or the capability's?" Full detail and the Buganizer-style rationale: `capabilities/docs/capability-domain-jira-map.md`, `jira/capability-model.md`, `jira/jira-workflow.md`.

*Note: the UIFPE backlog seeded on 2026-07-23 (Capabilities UIFPE-8..21, Foundation Epics UIFPE-22..35, Sprint-0 Stories UIFPE-36..41) predates this rule and still parents those Stories to Foundation Epics because no domain existed yet to own them. That's expected for pre-domain groundwork, not a violation — but any new Story that ships inside a domain should parent to that Domain epic, not a Foundation Epic, from here on.*

### How is the GitHub repository designed?

One repo, two crossing axes (see "The two axes" above): **capabilities** (13 folders, standards only, no code, never built) cross **domains** (7 folders, all the code, each building to one container image). CI (`domain-ci.yml`) is path-filtered on `domains/**` only — a commit that only touches `capabilities/` triggers zero CI runs, because Docker's build context never points at `capabilities/` either. `.github/CODEOWNERS` assigns each domain's `src/`+`infrastructure/` to its FDE, `qe/` to QE, `ui/`+`ux/` to UX, and scope/README to the PM.

**How a Jira issue connects to the repo, worked through UIFPE-39 end to end:**

1. **Branch name carries the key:** `feature/UIFPE-39-developer-registration-api`.
2. **Where the code actually lands:** the Story's `capability:api` label says *which guidance applies* (`capabilities/api/guidelines.md`), not *where the code goes* — the code itself lands in a domain, e.g. `domains/product/src/api/`, because domains are where code lives and capabilities are guard rails (see "Where should a developer focus" below).
3. **Commit message:** `Add developer registration endpoint contract\n\nUIFPE-39 #code-review` — the free-text part explains *why*; the `UIFPE-39 #code-review` token is the optional Smart Commit command.
4. **What fires automatically the moment that branch/PR exists** (no token needed): push → UIFPE-39 moves to **In Progress**; opening the PR (non-draft) → **Code Review**; merging it → **Ready for QA**. This is `.github/workflows/jira-transition.yml` reading `UIFPE-39` straight out of the branch name.
5. **What CI does at the same time:** because the PR touches `domains/product/**`, `domain-ci.yml`'s matrix job for `product` runs — structural DoD gate, then the Entra ID + Keeper gate if `src/api/openapi.yaml` or Helm templates changed, then build/test/scan.

So the connection isn't a separate integration step to configure per-issue — it's two things that both key off the same string (`UIFPE-<n>`) appearing in a branch name: Jira's own Smart Commit parsing, and this repo's own `jira-transition.yml` workflow.

### What's automated between Git and Jira, and what isn't (yet)?

| Automated today | Status |
|---|---|
| Push to a branch named `UIFPE-<n>-...` → issue moves to In Progress | ✅ `.github/workflows/jira-transition.yml` |
| PR opened (non-draft) → issue moves to Code Review | ✅ same workflow |
| PR merged → issue moves to Ready for QA | ✅ same workflow |
| Smart Commit tokens (`UIFPE-n #code-review`) as a manual alternative | ✅ Atlassian GitHub-for-Jira app |

| Drafted by a skill, confirmed by a human, not yet wired into CI | Status |
|---|---|
| A comment on the Jira issue explaining *why* it moved (not just that it moved) | ⚠️ gap — see `.claude/skills/fde/` |
| A structured PR review-summary comment (which gates passed, contract diff) | ⚠️ gap |
| Notifications (Slack/Teams) on CI failure, deploy promotion, Jira transition | ⚠️ gap — nothing notifies today |
| Filing a Jira Bug from a failing CI run | ⚠️ drafted by `.claude/skills/qe/`, never auto-filed |
| Creating/parenting a new Jira issue, trace-gate audit | ⚠️ drafted by `.claude/skills/pm/`, never auto-created |

The dividing line is deliberate: **status transitions are mechanical** (a push happened, a PR merged — no judgment required), so they're fully automated. **Everything that writes content to a shared system** (a comment, a Bug, a new issue, a Confluence page) is drafted by a skill and always shown to a human before it's actually written — see each skill's "what this does NOT do" section.

### Where should a developer focus — Domain or Capability?

**Domain.** That's where 100% of the code lives, where CI runs, where a PR lands, and where a container image gets built and deployed. A developer's daily loop is entirely inside `domains/<domain>/`.

Capabilities are consulted, not worked in — they're the guard rails a developer reads at design time (`capabilities/<name>/guidelines.md`) before and while building, but nothing in `capabilities/` is ever imported, executed, or shipped. If you find yourself about to write code under `capabilities/`, that's the signal something's misplaced — it belongs in a domain.

### How do developers actually develop?

The golden path, concretely:

1. Get a Story (traced to a Capability — `pm` skill helps here).
2. Read that capability's `guidelines.md` + the matching `delivery-lab/blueprints/` entry.
3. If the domain doesn't exist yet, stamp it: `./create-domain.sh <domain> --caps <c1,c2>`.
4. **Register the schema before writing code** (`src/schemas/`, Apicurio) — Schema Registry First is non-negotiable.
5. Implement in `src/` (business logic in `apps/`, contract in `api/`), write tests in `qe/`.
6. Commit/push/PR using the `UIFPE-<n>` branch convention (`fde` skill helps here) — status transitions happen automatically.
7. CI runs the domain's matrix job; fix anything the structural or Entra ID/Keeper gate flags.
8. On merge, GitHub Actions (`domain-cd.yml`) promotes the image Development → UAT → Production; register ownership in JSM.

Full detail: `architecture-design/operating-model/fde-build-deploy-workflow.md`.

### How do they build and deploy?

Already covered step by step above in "Containerize," "CI — GitHub Actions," and "CD — GitHub Actions → Kubernetes/AWS." One line summary: **one `src/Dockerfile` → one image, scanned and signed by GitHub Actions → promoted Development→UAT→Production by GitHub Actions bumping `values-<env>.yaml` → Argo CD reconciles the cluster to match git.**

## Getting started

- Standing up the program / creating the Jira backlog → `PLANNING.md`
- Building your first domain → `architecture-design/operating-model/fde-build-deploy-workflow.md`
- Contributing docs or reasoning about architecture → `CLAUDE.md`

## Brand alignment

Anything that renders visually (diagrams, artifacts, decks) follows Pearson's visual identity — Plus Jakarta Sans and the Pearson purple palette. See `CLAUDE.md` for the specifics. Pearson's brand guide covers visual identity only; the writing voice here is this program's own engineering convention.
