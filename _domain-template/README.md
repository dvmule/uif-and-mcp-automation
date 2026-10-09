# Domain Template (the mould)

## What this is and why it matters

Every domain in UIF is stamped from this mould by `../create-domain.sh`. It exists so a Forward Deployed Engineer starting a new business unit gets the same minimal, ownership-clear shape every time — and cannot silently forget the pieces new engineers most often miss (tests, security, observability, the deploy wiring). This is the single definition of "what a domain looks like."

A **domain** is a deployable, ownable business unit (product, customer, sku, finance, hr, legal, identity, orders, tax, subscriptions) that builds to **one container image** — the single build/deploy contract. A **capability** (API, Event, Schema, MCP, …) is a horizontal discipline; inside a domain it appears as a folder under `src/`. Domains are vertical; capabilities are horizontal; they cross inside `src/`.

## The shape

```
domains/<domain>/
├── README.md              what the domain owns
├── CLAUDE.md              domain skill — context for engineers + AI
├── DOMAIN-CHECKLIST.md    definition-of-done gate
├── src/                   FDE — all code; builds to one image
│   ├── apps/              deployable application code
│   ├── api/               API surface: openapi.yaml + handlers   (core)
│   ├── schemas/           registered contracts                    (core)
│   ├── events/  …         opt-in capability surfaces (see below)
│   └── Dockerfile         the one build context → one image
├── qe/                    QE Lead — quality strategy + test suites
├── ui/                    UX Lead — UI implementation
├── ux/                    UX Lead — specs, flows, research
└── infrastructure/        FDE — how the image runs
    ├── terraform/         cloud IaC
    ├── kubernetes/        manifests
    ├── helm/              chart
    └── observability/     SLOs, dashboards, alerts
```
CD (Development → UAT → Production) runs from the root `.github/workflows/domain-cd.yml` — no domain-local pipeline file.

## Why this shape is easy to build and deploy

- **One container is the whole contract.** Whatever language or authorized library lives in `src/`, the `src/Dockerfile` produces one image. Everything downstream (GitHub Actions CD, Helm, K8s) only deals with that image — the platform is language-agnostic.
- **One deployable per domain → one pipeline, one PR per unit of work.** CODEOWNERS adds the right reviewers (QE for `qe/`, UX for `ui/ux/`); it never forces the work into multiple PRs.
- **Code in one root (`src/`), runtime in one root (`infrastructure/`).** The image is the single handoff between them.

If a domain ever needs a second independently-deployable app, split under `src/apps/<app>/` with its own `Dockerfile` — not before.

## Selective scaffold

`create-domain.sh` always stamps the core (`src/{apps,api,schemas}`, `qe/`, `ui/`, `ux/`, `infrastructure/*`) and lets you opt in to capability surfaces so a domain never carries empty stubs:

| Opt-in (under `src/`) | Capability |
|---|---|
| `events/` | Event |
| `semantics/` | Semantics |
| `knowledge-graph/` | Knowledge Graph |
| `mcp/` | MCP |
| `a2a/` | A2A |
| `agents/` (adds `prompts/`, `evals/`) | Agent |

Add one later by re-running the script with that capability, or by copying the standard from `../capabilities/<x>/`.

## Monorepo CI note

GitHub only auto-runs workflows under the repo-root `.github/workflows/`. A domain has **no** per-domain CI or CD folder; the root `domain-ci.yml` triggers the right domain via path filters (`domains/<domain>/**`) and gates on the container building, and the root `domain-cd.yml` promotes it Development → UAT → Production the same way.

## Ownership (Four-in-a-Box → CODEOWNERS)

FDE owns `src/` and `infrastructure/`; QE Lead owns `qe/`; UX Lead owns `ui/` and `ux/`; PM owns scope (in `README.md`/`CLAUDE.md`) and the Jira trace. Wildcard rules in `../.github/CODEOWNERS` apply to every domain automatically.
