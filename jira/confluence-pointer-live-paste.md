**Rule:** If this page and GitHub disagree, **GitHub wins.** Reconcile using [capability-domain-jira-map.md](https://github.com/pearson-dnt-coe/universal-Integration-framework/blob/validate/capabilities/docs/capability-domain-jira-map.md).

## What this page is

| System | Role | Source of truth |
| --- | --- | --- |
| **GitHub** | Code, standards, CI/CD definitions | [universal-Integration-framework](https://github.com/pearson-dnt-coe/universal-Integration-framework) |
| **Jira (UIFPE)** | Work, traceability, workflow | [UIFPE project](https://pearson.atlassian.net/jira/software/projects/UIFPE) |
| **Confluence** | Links and cheat-sheets only | [UIF space](https://pearson.atlassian.net/wiki/spaces/UIF/overview) — updated when repo or Jira model changes |

This page is a **pointer only**. Authoritative docs live in GitHub on branch **validate**.

**Why two axes?** See GitHub [`jira/uif-faq.md` § Why two axes (T-shaped)](https://github.com/pearson-dnt-coe/universal-Integration-framework/blob/validate/jira/uif-faq.md#why-two-axes-t-shaped-at-all).

---

## Start here (by role)

| Role | Read first (in GitHub) |
| --- | --- |
| **FDE** | README → api-playbook → domains/\<domain\>/DOMAIN-CHECKLIST.md |
| **PM** | jira-workflow → backlog-seed → PLANNING |
| **QE** | domain qe/ + capabilities/observability |
| **Everyone** | capabilities/delivery/jira-github-integration.md |

---

## One rule (Jira + GitHub must match)

**Parent = Domain epic. Capability = label. Never two parents.**

| Question | Answer |
| --- | --- |
| Where does code go? | `domains/<domain>/src/` — never `modules/`, never `capabilities/` |
| Jira parent for delivery work? | **Domain epic** (e.g. Product — **UIFPE-51**) |
| How to tag capability? | Labels: `capability:api`, `capability:schema`, etc. |
| When is Foundation Epic parent? | Pre-domain platform work only (registry, CI template, CD skeleton) |

**Worked example:** parent **UIFPE-51**, labels `domain:product` + `capability:api`, code in `domains/product/src/api/`.

---

## Domain epics (live in UIFPE)

| Domain | Label | GitHub path | Jira key |
| --- | --- | --- | --- |
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

---

## Git ↔ Jira daily loop (FDE)

### Branch and PR naming

```
feature/UIFPE-<n>-<short-description>
```

Example: `feature/UIFPE-39-product-data-api` · PR title: `UIFPE-39 · Product Data API`

### Smart Commits (primary — use this)

**Both** `#comment` and `#transition` are required:

```
UIFPE-<n> #comment <what you did and why> #<transition>
```

| Moment | Example commit message | Jira result |
| --- | --- | --- |
| Start work | `UIFPE-39 #comment Starting API handler #in-progress` | Comment + → In Progress |
| PR ready | `UIFPE-39 #comment PR opened for review #code-review` | Comment + → Code Review |
| Merged | `UIFPE-39 #comment Merged to main #ready-for-qa` | Comment + → Ready for QA |
| Blocked | `UIFPE-39 #comment Waiting on registry access #blocked` | Comment + → Blocked |

Full token table: [jira-github-integration.md](https://github.com/pearson-dnt-coe/universal-Integration-framework/blob/validate/capabilities/delivery/jira-github-integration.md)

**Caveat:** transition may be named `In Progress (1)` — verify `#in-progress` in a test commit.

### GitHub Actions auto-transition (Phase 2 — admin-owned; do not rely on yet)

**Engineers:** use Smart Commits only. **Atlassian admin:** resolve runner **401** (IP allowlist) so `jira-transition.yml` can serve as a backup later.

| GitHub event | Target status |
| --- | --- |
| Push to branch (not main) | In Progress |
| PR opened / ready for review | Code Review |
| PR merged to main | Ready for QA |

---

## What happens on a PR (GitHub)

| Step | What runs |
| --- | --- |
| 1 | Jira key from branch → status (Smart Commits) |
| 2 | Touch `domains/<domain>/**` → Domain CI |
| 3 | Structural gates: checklist, schemas, Dockerfile, qe/ |
| 4 | API/Helm present → Entra ID + Keeper gate |

Pilot domain: **domains/product/** (Product Data API).

---

## Sprint 0 pointers

| Story | Parent | Notes |
| --- | --- | --- |
| UIFPE-36 | Foundation Epic | Schema registry (Apicurio) |
| UIFPE-37 | Foundation Epic | CI template |
| UIFPE-38 | Foundation Epic | CD skeleton (Jenkins) |
| **UIFPE-39** | **Domain: Product (UIFPE-51)** | `domains/product/src/api/` |
| **UIFPE-40** | **Domain: Product (UIFPE-51)** | `domains/product/src/mcp/` |
| UIFPE-41 | Program / Architecture | API playbook (done) |

---

## Jira Automation (required — one-time admin setup)

A status change **does not notify anyone** by itself. Configure in **UIFPE → Project settings → Automation**:

| When issue transitions to | Action (required) |
| --- | --- |
| **Code Review** | Assign / mention PR reviewer(s) |
| **Ready for QA** | Assign / mention domain QE Lead |

Without these rules, the Git ↔ Jira hand-off is incomplete.

---

## Legacy — do not use

| Do not | Use instead |
| --- | --- |
| Parent delivery under Foundation Epic | Parent = **Domain epic** |
| Paths under `modules/` | `domains/<domain>/src/` |
| Label `capability:schema-registry` | `capability:schema` |
| Parent delivery to UIFPE-8 | Domain epic + labels |

---

## Maintenance

**Last updated:** 2026-08-06 · **Aligned to:** GitHub `validate`

- [UIF Confluence space](https://pearson.atlassian.net/wiki/spaces/UIF/overview)
- [Team onboarding pointer](https://github.com/pearson-dnt-coe/universal-Integration-framework/blob/validate/jira/team-integration-pointer.md)
- [UIF FAQ — Why two axes](https://github.com/pearson-dnt-coe/universal-Integration-framework/blob/validate/jira/uif-faq.md#why-two-axes-t-shaped-at-all)
- [Repo README](https://github.com/pearson-dnt-coe/universal-Integration-framework/blob/validate/README.md)
- [Jira ↔ GitHub integration](https://github.com/pearson-dnt-coe/universal-Integration-framework/blob/validate/capabilities/delivery/jira-github-integration.md)
- [Source draft in repo](https://github.com/pearson-dnt-coe/universal-Integration-framework/blob/validate/jira/confluence-pointer-draft.md)
