# UIF FAQ — domains × capabilities, GitHub, Jira, Confluence

This FAQ answers the questions teams ask most when they first work in the Unified Integration Fabric (UIF). UIF uses **two crossing axes** — **domains** (vertical, where code ships) and **capabilities** (horizontal, how things are built). The same model appears in **GitHub**, **Jira**, and the **Confluence pointer**. If this FAQ and the repo disagree, **GitHub wins** — reconcile using [`capability-domain-jira-map.md`](../capabilities/docs/capability-domain-jira-map.md).

**Audience:** FDE, PM, QE Lead, UX Lead, engineering managers.  
**Agents:** also read [`AGENTS.md`](../AGENTS.md) and the story [`PRD.md`](../PRD.md).

---

## Why two axes (T-shaped) at all?

Most confusion is not *where* things go — it is *why* UIF uses two axes instead of one simple tree. This section answers that.

### Two questions every enterprise must answer

| Question | Axis | Without this axis… |
| --- | --- | --- |
| **Who ships and owns runtime?** | **Domain** (vertical) | Standards teams “own” deployables — unclear who runs prod |
| **How must it be built to be acceptable?** | **Capability** (horizontal) | Every domain reinvents API, security, and schema rules |

One folder tree cannot answer both. UIF uses **two crossing axes** so **ownership** and **standards** stay explicit.

### T-shaped — what that means

```
     API · Event · Schema · Security · Observability · …
     ────────────────────────────────────────────────────  ← capabilities (horizontal bar)
                           │
                           │  product domain
                           │  (ship & own end-to-end)
                           ▼
                      vertical stem
```

- **Vertical (domain):** go deep in one deployable unit — code, image, deploy, operate, Jira backlog parent.
- **Horizontal (capability):** conform to shared disciplines — read the standard, apply it in your domain.

People — and agents — are **T-shaped**: deep in a **domain**, broad across **capabilities** they touch on that story. You do not pick one axis; you pick **one domain to ship in** and **one or more capabilities to conform to**.

### What goes wrong with only one axis

| Single-axis attempt | Failure mode |
| --- | --- |
| **Domains only** (no shared standards) | Seven different API styles, seven schema registries, audit pain |
| **Capabilities only** (no clear deployable owner) | `modules/` sprawl — who builds the image? who is on-call? |
| **Duplicate docs** (Confluence copies standards) | Drift — “which doc is true?” |

The two-axis model is how UIF avoids repeating those failures.

### Is T-shaped bad for agentic engineering?

**No — ambiguity is bad for agents. Explicit bounds are good.**

Agents struggle when context is fuzzy (two `api` folders with no rule, unclear Jira parent, “implement the API” with no domain named). They work well when bounds are explicit:

> **Domain:** product · **Capabilities:** api, schema · **Write:** `domains/product/src/` · **Read standards:** `capabilities/api/`, `stacks/apicurio/standards/`

| Good for agents | Bad for agents |
| --- | --- |
| `capabilities/` = read-only standards (bounded context) | Two valid Jira parents |
| `domains/` = sole write target | Agent edits `capabilities/` or root `src/` |
| PRD lists domain + capability labels per task | One prompt: “build the whole system” |
| MCP tools on governed interfaces | Ad-hoc curl scripts and duplicated docs |

**Agentic engineering needs governance and bounded context.** The two-axis model supplies both — if teams enforce parent = domain, label = capability, code in `domains/`.

### One sentence for leaders to repeat

> **Domains own delivery; capabilities own the rules. T-shaped teams — and agents — go deep where they ship and wide across the standards they must follow.**

---

## The two axes

### What are the two axes?

| Axis | Answers | GitHub | Jira |
| --- | --- | --- | --- |
| **Domain** | *Who owns this? Where does it deploy?* | `domains/<domain>/` — **all code** | **Parent** = Domain epic (UIFPE-51..57) |
| **Capability** | *What standard must we follow?* | `capabilities/<capability>/` — **standards only** | **Label** `capability:<name>` |

### One sentence to remember

**You work in a domain. You conform to capabilities. Capabilities do not own your backlog — your domain does.**

### Why two folders both named `api`?

| Path | Role |
| --- | --- |
| `capabilities/api/` | *How* any API is built (standard) |
| `domains/product/src/api/` | *The* product API (implementation) |

### How do I choose domain vs capability?

| Field | Choose |
| --- | --- |
| Jira **parent** | Domain epic (e.g. UIFPE-51) |
| Jira **labels** | `capability:api`, `capability:schema`, … |
| **Code** | `domains/<domain>/src/` |
| **Read first** | `capabilities/<capability>/` |

---

## GitHub

### Where does code live?

**Always** `domains/<domain>/src/`. Never `capabilities/`, `modules/`, or root `src/`.

### What branch?

**`validate`** until merged to `main` — confirm with your lead.

---

## Jira

### Single-parent rule

Delivery stories: parent = **Domain epic**. Capability = **label only**.

### When is Foundation Epic the parent?

Pre-domain platform work only (registry, CI template, CD skeleton) — e.g. UIFPE-36, 37, 38.

### Domain epics

| Domain | Key |
| --- | --- |
| Product | UIFPE-51 |
| Customer | UIFPE-52 |
| SKU | UIFPE-53 |
| Finance | UIFPE-54 |
| HR | UIFPE-55 |
| Legal | UIFPE-56 |
| Identity | UIFPE-57 |
| Orders | UIFPE-109 |
| Tax | UIFPE-110 |
| Subscriptions | UIFPE-111 |

---

## Smart Commits

```
UIFPE-<n> #comment <what> #<transition>
```

Branch: `feature/UIFPE-<n>-<slug>`. Engineers rely on Smart Commits; Phase 2 workflow 401 is Atlassian admin scope.

---

## Confluence

**Pointer only** — links to GitHub and Jira, does not duplicate standards.  
[Live page](https://pearson.atlassian.net/wiki/spaces/UIF/pages/1261241098/Git+Jira+Integration+Claude+GitHub+Jira+Confluence)

---

## Common mistakes

| Mistake | Fix |
| --- | --- |
| Code in `modules/` | `domains/<domain>/src/` |
| Capability as Jira parent | Domain epic parent + capability labels |
| Smart Commit without `#comment` | Include `#comment` and `#transition` |

---

## Related

| Doc | Use |
| --- | --- |
| [`capability-domain-jira-map.md`](../capabilities/docs/capability-domain-jira-map.md) | Worked GitHub + Jira example |
| [`team-integration-pointer.md`](team-integration-pointer.md) | Team onboarding one-pager |
| [`AGENTS.md`](../AGENTS.md) | AI agent rules |
| [`PRD.md`](../PRD.md) | Sequential story tasks for agents |

*Last updated: 2026-08-06 (added § Why two axes)*
