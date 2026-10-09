# UIF — Git · Jira · Confluence pointer (draft)

This is the **source draft** for the thin Confluence pointer page in space **UIF**. It links engineers to GitHub and Jira — it does not duplicate capability standards, playbooks, or runbooks. Publish manually or via the confluence skill (`.claude/skills/confluence/SKILL.md`); get explicit approval before updating live Confluence.

| Field | Value |
|---|---|
| **Confluence space** | [UIF](https://pearson.atlassian.net/wiki/spaces/UIF/overview) (id `1260880027`) |
| **Live page** | [Git ↔ Jira Integration](https://pearson.atlassian.net/wiki/spaces/UIF/pages/1261241098/Git+Jira+Integration+Claude+GitHub+Jira+Confluence) — id `1261241098` |
| **Published live** | **2026-08-06 — full republish verified** (includes Why two axes → FAQ) |
| **Last aligned to repo** | 2026-08-06 |
| **Git branch** | `validate` (update links when `main` catches up) |
| **Publish sources** | `confluence-pointer-live-paste.md` (one-step paste) · `confluence-pointer-publish-wiki.md` · `confluence-pointer-publish.html` |
| **Authoritative repo docs** | `jira/backlog-seed.md`, `jira/jira-workflow.md`, `capabilities/delivery/jira-github-integration.md`, `capabilities/docs/capability-domain-jira-map.md` |

### Live page self-check (verified 2026-08-06)

| Check | Repo source | Live Confluence |
|---|---|---|
| Hub row (GitHub + Jira + UIF space link) | Yes | ✅ |
| **Why two axes** link → `jira/uif-faq.md` | Yes | ✅ |
| Domain epic table (UIFPE-51..57) | Yes | ✅ |
| Smart Commit cheat-sheet (`#comment` + `#transition`) | Yes | ✅ |
| Phase 2 / 401 note (`jira-transition.yml`) | Yes | ✅ |
| Jira Automation (required) | Yes | ✅ |
| Old "Four-way loop" content removed | — | ✅ |
| Old bare `#code-review` example removed | — | ✅ |

**Refresh:** diff `confluence-pointer-live-paste.md` against the [live page](https://pearson.atlassian.net/wiki/spaces/UIF/pages/1261241098/Git+Jira+Integration+Claude+GitHub+Jira+Confluence) when repo changes.

> **Rule:** If this page and GitHub disagree, **GitHub wins**. Reconcile using `capabilities/docs/capability-domain-jira-map.md`.

---

## What this page is (info panel)

| System | Role | Source of truth |
|---|---|---|
| **GitHub** | Code, standards, CI/CD definitions | [universal-Integration-framework](https://github.com/pearson-dnt-coe/universal-Integration-framework) |
| **Jira (UIFPE)** | Work, traceability, workflow | [UIFPE project](https://pearson.atlassian.net/jira/software/projects/UIFPE) |
| **Confluence** | Links and cheat-sheets only | [UIF space](https://pearson.atlassian.net/wiki/spaces/UIF/overview) — updated when repo or Jira model changes |

**Why two axes?** See GitHub [`jira/uif-faq.md` § Why two axes (T-shaped)](https://github.com/pearson-dnt-coe/universal-Integration-framework/blob/validate/jira/uif-faq.md#why-two-axes-t-shaped-at-all).

---

## Start here (by role)

| Role | Read first (in GitHub) |
|---|---|
| **FDE (developer)** | `README.md` → `delivery-lab/blueprints/api-playbook.md` → `domains/<domain>/DOMAIN-CHECKLIST.md` |
| **PM** | `jira/jira-workflow.md` → `jira/backlog-seed.md` → `PLANNING.md` |
| **QE** | Domain `qe/` + `capabilities/observability/` |
| **Everyone — Jira ↔ Git** | `capabilities/delivery/jira-github-integration.md` |

---

## One rule (Jira + GitHub must match)

**Parent = Domain epic. Capability = label. Never two parents.**

| Question | Answer |
|---|---|
| Where does code go? | `domains/<domain>/src/` — never `modules/`, never `capabilities/` |
| Which Jira parent for delivery work? | **Domain epic** (e.g. Domain: Product — **UIFPE-51**) |
| How do I tag capability standards? | Labels: `capability:api`, `capability:schema`, etc. |
| When is Foundation Epic the parent? | **Pre-domain platform work only** (registry stand-up, CI template, CD skeleton) |

Worked example (product API): parent **UIFPE-51**, labels `domain:product` + `capability:api` (+ `capability:schema` if applicable), code in `domains/product/src/api/`.

Full diagram: [`capabilities/docs/capability-domain-jira-map.md`](https://github.com/pearson-dnt-coe/universal-Integration-framework/blob/validate/capabilities/docs/capability-domain-jira-map.md)

---

## Domain epics (live in UIFPE)

Each domain epic is the **owning parent** for stories that ship code in that domain.

| Domain | Label | GitHub path | Jira key |
|---|---|---|---|
| Product | `domain:product` | `domains/product/` | **UIFPE-51** |
| Customer | `domain:customer` | `domains/customer/` | **UIFPE-52** |
| SKU | `domain:sku` | `domains/sku/` | **UIFPE-53** |
| Finance | `domain:finance` | `domains/finance/` | **UIFPE-54** |
| HR | `domain:hr` | `domains/hr/` | **UIFPE-55** |
| Legal | `domain:legal` | `domains/legal/` | **UIFPE-56** |
| Identity | `domain:identity` | `domains/identity/` | **UIFPE-57** |
| Orders | `domain:orders` | `domains/orders/` | **UIFPE-109** |
| Tax | `domain:tax` | `domains/tax/` | **UIFPE-110** |
| Subscriptions | `domain:subscriptions` | `domains/subscriptions/` | **UIFPE-111** |

**13 capabilities** use labels `capability:<name>` (API, Event, Data, Schema, Semantics, Knowledge Graph, MCP, A2A, Agent, Infrastructure, Security, Observability, Delivery). Architecture & Design is **not** a capability — use `program:architecture-design` for program/foundation work.

---

## Git ↔ Jira daily loop (FDE)

### Branch and PR naming

```
feature/UIFPE-<n>-<short-description>
```

Example: `feature/UIFPE-39-product-data-api`

PR title should include the key: `UIFPE-39 · Product Data API`

### Smart Commits (primary mechanism — use this)

Commit message format:

```
UIFPE-<n> #comment <what you did and why> #<transition>
```

| Moment | Example commit message | Jira result |
|---|---|---|
| Start work | `UIFPE-39 #comment Starting API handler #in-progress` | Comment + → In Progress |
| PR ready | `UIFPE-39 #comment PR opened for review #code-review` | Comment + → Code Review |
| Merged | `UIFPE-39 #comment Merged to main #ready-for-qa` | Comment + → Ready for QA |
| Blocked | `UIFPE-39 #comment Waiting on registry access #blocked` | Comment + → Blocked |

**Prerequisites:** GitHub for Jira app connected; Smart Commits enabled; commit-author email = licensed Jira user.

Full token table and caveats: [`capabilities/delivery/jira-github-integration.md`](https://github.com/pearson-dnt-coe/universal-Integration-framework/blob/validate/capabilities/delivery/jira-github-integration.md)

### GitHub Actions auto-transition (Phase 2 — admin-owned; do not rely on yet)

Repo workflow: `.github/workflows/jira-transition.yml` (push/PR/merge → status).

**Engineer standard:** Smart Commits only — every commit that should comment or transition must include `#comment` and `#transition`. **Atlassian admin:** resolve runner **401** (IP allowlist rejecting GitHub Actions egress) before Phase 2 can act as a safety net.

---

## What happens on a PR (GitHub)

| Step | What runs |
|---|---|
| 1 | Jira key read from branch name → status update (if Smart Commits / workflow works) |
| 2 | If PR touches `domains/<domain>/**` → **Domain CI** runs (`.github/workflows/domain-ci.yml`) |
| 3 | Structural gates: `DOMAIN-CHECKLIST.md`, `src/schemas/`, `src/Dockerfile`, `qe/` |
| 4 | When API/Helm exist: Entra ID + Keeper compliance gate |

CI runs on **`domains/**` only** — doc-only changes under `capabilities/` do not trigger domain builds (by design).

Pilot domain with runnable code: **`domains/product/`** (Product Data API).

---

## Sprint 0 pointers (live backlog)

| Story | Parent type | Notes |
|---|---|---|
| Schema registry (dev) — UIFPE-36 | Foundation Epic | Pre-domain; Apicurio |
| CI template — UIFPE-37 | Foundation Epic | `.github/workflows/` |
| CD skeleton — UIFPE-38 | Foundation Epic | Jenkins |
| Pilot API — **UIFPE-39** | **Domain: Product (UIFPE-51)** | `domains/product/src/api/` |
| Reference MCP — **UIFPE-40** | **Domain: Product (UIFPE-51)** | `domains/product/src/mcp/` |
| API playbook — UIFPE-41 | Program / Architecture | Doc delivered |

Details: [`jira/backlog-seed.md`](https://github.com/pearson-dnt-coe/universal-Integration-framework/blob/validate/jira/backlog-seed.md)

---

## Jira Automation (required — one-time admin setup)

Smart Commits and GitHub Actions move issues through workflow states, but **a status change alone does not notify anyone**. A Jira project admin must configure **Automation** rules once in **UIFPE → Project settings → Automation**:

| When issue transitions to | Action (required) |
|---|---|
| **Code Review** | Assign to and/or comment-mention the PR reviewer(s) |
| **Ready for QA** | Assign to and/or comment-mention the domain's QE Lead |

Without these rules, the Git ↔ Jira hand-off is incomplete — reviewers and QE must poll the board. Details: [`jira-github-integration.md` § Notifying QE and reviewers](../capabilities/delivery/jira-github-integration.md#notifying-qe-and-reviewers-at-each-hand-off).

---

## What this page deliberately omits

Do **not** duplicate on Confluence:

- Capability standards (`capabilities/*/guidelines.md`)
- API/event/schema how-tos
- Infrastructure runbooks (Docker, K8s, Terraform)
- Full Definition of Done checklists

All of that lives in **GitHub**.

---

## Legacy — do not use (warning panel)

| Do not | Use instead |
|---|---|
| Parent stories under Foundation Epic when shipping in a domain | Parent = **Domain epic** |
| Paths under `modules/` | `domains/<domain>/src/` |
| Label `capability:schema-registry` | `capability:schema` |
| Parent new delivery work to UIFPE-8 (Architecture & Design Capability) | Superseded — use Domain epic + labels |

---

## Maintenance

| Field | Value |
|---|---|
| **Last updated** | 2026-08-05 (live page verified same day) |
| **Aligned to** | GitHub `validate` — Confluence page id `1261241098` in sync |
| **UIFPE state** | Domain epics UIFPE-51..57; UIFPE-39/40 reparented to Product |
| **Refresh trigger** | After merges that change Jira model, CI gates, or domain layout |

**Canonical repo links (`validate` branch):**

- [UIF Confluence space](https://pearson.atlassian.net/wiki/spaces/UIF/overview)
- [README](https://github.com/pearson-dnt-coe/universal-Integration-framework/blob/validate/README.md)
- [Jira ↔ GitHub integration](https://github.com/pearson-dnt-coe/universal-Integration-framework/blob/validate/capabilities/delivery/jira-github-integration.md)
- [Capability × Domain map](https://github.com/pearson-dnt-coe/universal-Integration-framework/blob/validate/capabilities/docs/capability-domain-jira-map.md)
- [Backlog seed](https://github.com/pearson-dnt-coe/universal-Integration-framework/blob/validate/jira/backlog-seed.md)
- [This draft (source)](https://github.com/pearson-dnt-coe/universal-Integration-framework/blob/validate/jira/confluence-pointer-draft.md)

---

## Publishing notes (for confluence skill)

When converting to Confluence HTML via `updateConfluencePage`:

- Wrap "What this page is" in `<div data-type="panel-info">`.
- Wrap "Legacy — do not use" in `<div data-type="panel-warning">`.
- Keep Smart Commit and domain-epic tables as native Confluence tables.
- Preserve thin-pointer style — add sections, do not paste full repo docs.
- Show the diff to the user and get explicit confirmation before publishing.
