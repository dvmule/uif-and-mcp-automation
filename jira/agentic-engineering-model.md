# Agentic Engineering, End to End — Jira, Confluence, GitHub, Deploy

This document answers one question a new engineer (human or agent) keeps asking across four systems: **where does a piece of work actually live, and how does it move from an idea to something running in production?** It ties together the two axes UIF is built on, why that structure is specifically useful for building MCP/A2A/Agent capabilities (not just APIs), how Confluence and the repo avoid duplicating each other, what GitHub actually ships, and the Jira mechanics (Story → Task → Sub-task → QE → Deploy) with two real, live UIFPE issues as worked examples. It links to the documents that already own each piece rather than re-explaining them — see "Sources" at the end.

---

## 1. The two axes, and why they exist

UIF is organised on two crossing axes (`capabilities/docs/capability-domain-jira-map.md`, `CLAUDE.md`):

- **Domains are vertical** — deployable, ownable business units (`product`, `program`, `customer`, `sku`, `price`, `stock`, `finance`, `hr`, `legal`, `identity`, `orders`, `tax`, `subscriptions`). Code lives here. One domain → one container image → one deploy.
- **Stacks are RELAY's application units** (`echo`, `beacon`, `harbor`) — independently deployable, never domains (ADR-0009, ADR-0010). Code lives under `stacks/<stack>/`.
- **Capabilities are horizontal** — reusable disciplines (API, Event, Schema, MCP, A2A, Agent, …), each a central standard in `capabilities/<capability>/`, realized as a same-named folder inside every domain or stack that uses it.

`capabilities/api/` says *how* an API is built; `domains/product/src/api/` is *the* product API. **The standard is central; the code is in the domain.** The same crossing repeats in Jira: a **Domain epic** is the one owning parent for delivery work; **capability is always a label**, never a parent, because one Story can conform to more than one discipline at once (`jira/jira-workflow.md`).

## 2. Why this is specifically useful for agent-centric engineering

An MCP server, an A2A routing rule, and an agent module are **not a third axis** — they are domain code, exactly like an API or an event producer, governed by a horizontal capability standard:

```
domains/product/src/
├── api/        ← capability: API
├── mcp/        ← capability: MCP   (tools that wrap product's own API/data)
├── a2a/        ← capability: A2A   (how product's agent is discovered/routed to)
└── agents/     ← capability: Agent (prompts/, evals/, agent-manifest.yaml)
```

That gives an agent the exact same things an API gets for free: one Domain epic to trace to, one `capability:*` label, one CI/CD gate, one container image, one observability contract. There is no separate "agent lifecycle" to invent — this is the point of the two-axis model, not an accident. `capabilities/agent/guidelines.md` and `capabilities/mcp/guidelines.md` state this explicitly, and it is now grounded in real Pearson precedent rather than only public standards (added this session, commit `b298f53`):

- **Cookie Process Registry** (Confluence `PSW`, page "Cookie Process Registry (pilot)") — a real enterprise MCP server (`cookie-mcp-server`) grounds itself in a written, reviewable registry, not runtime-only state. That is the same discipline `mcp-manifest.yaml` enforces here: the manifest is the source of truth, not a description that can drift from the running server.
- **AgentOps / AXIS** (Confluence `AGTOPS` space overview) — Pearson's own agent platform states "AI-native," "prompts as code," "eval-driven," "human in the loop" as commitments — independently matching this repo's Agent capability design (evals as the regression gate, versioned prompts, `humanInTheLoop` policy keyed off MCP's `destructiveHint`).

Practically: an FDE building an agent doesn't get a looser path. It ships in a domain, traces to that Domain epic, is labelled `capability:agent` (+ `capability:mcp`/`capability:a2a` as needed), and is gated by the same `domain-ci.yml`/`domain-cd.yml` as everything else.

## 3. Confluence — what's there, and why it doesn't duplicate the repo

There is one Confluence space, **UIF** (id `1260880027`), with one page an engineer needs to know about: a pointer page (id `1261241098`) linking out to the repo and to the UIFPE Jira project. It deliberately does **not** reproduce standards content. The rule (decided 2026-07-29, applies to every capability doc since):

- `capabilities/*.md` stays grounded in **public standards** (OpenAPI, AsyncAPI, RFC, OWASP, MCP spec, …) by default.
- Any **Pearson-specific fact** (a real tool, a real internal convention — Apicurio-as-actually-deployed, the Cookie Process Registry, AgentOps) is added as its own subsection with an explicit citation: `Source: Confluence <space> page "<title>", verified <date>`.

This makes drift **visible** instead of silent: a stale citation date is a reviewable signal that something should be re-checked, rather than two copies of the same fact slowly diverging. "Link, don't duplicate" (`CLAUDE.md`) is the same rule applied to the repo itself — this document links to `capability-domain-jira-map.md` and `jira-workflow.md` rather than re-stating them.

## 4. GitHub — what's tracked, what actually ships

Everything in the repo is tracked in git — `capabilities/` is not gitignored, it's just **out of the build path**:

| Folder | Contains | Ships? |
|---|---|---|
| `capabilities/<c>/` | Standards, guidelines, playbooks — no code | Never. Not inside any domain's Docker build context; `.github/workflows/domain-ci.yml`'s `paths: ['domains/**']` filter means a commit touching only `capabilities/` triggers zero CI runs. |
| `architecture-design/` | Principles, patterns, ADRs — the frame | Never. Read by an FDE at design time, imported by nothing at runtime. |
| `delivery-lab/` | Templates/accelerators/blueprints (never running) + `reference-implementations/` (runnable, teaching-only) | Never deployed as a domain. |
| `domains/<domain>/src/` | The one thing that ships | **Yes.** `src/Dockerfile` is the single build/deploy contract — one image per domain. |
| `domains/<domain>/infrastructure/` | Terraform/Helm/K8s/observability for that image | Not shipped itself; it's what runs the shipped image. |

An FDE reviewing a PR should look at exactly one thing to know if it's shippable: does it touch `domains/<domain>/src/**`, does `src/Dockerfile` build, and is `qe/` green. Everything else in the diff is either infra-for-that-image or capability guidance that informed the design but never runs.

## 5. Jira — creating a Story under a domain

Full model: `jira/jira-workflow.md`. Recap + the mechanics an FDE actually does (the `pm` skill automates the creation/labelling step):

```
Domain epic (e.g. "Domain: Product", UIFPE-51)   ← the owning parent, where it ships
   └── Story        labels: capability:<x> [, capability:<y>…], domain:<name>
         └── Task              (optional decomposition layer — see §6)
               └── Sub-task     (the actual unit of execution — see §7)
```

To create a Story: **parent = the Domain epic** (never a Capability — capability is always a label), add one `capability:*` label per discipline it touches, add `domain:<name>`. If there is no domain yet for the work (e.g., standing up the schema registry itself), it stays under a **Capability Foundation Epic** instead — that's the one accepted exception, reserved for pre-domain platform groundwork (`jira/capability-model.md`'s reconciliation note).

## 6. What is a Task, and how is it built?

A **Task** is an optional decomposition layer between a Story and its Sub-tasks — used when a Story is big enough that grouping several Sub-tasks under a named piece of engineering work ("Implement Registration Endpoint") is clearer than a flat list. It's created the same way as any issue: `issuetype = Task`, `parent = <the Story>`.

**In practice on this project, Task is often skipped.** Jira's Sub-task issue type can only parent to a Story (not an Epic), so when a team has a Story but hasn't broken it into named Tasks yet, Sub-tasks parent straight to the Story. That's not a deviation — see UIFPE-68/UIFPE-69/UIFPE-73 in §7/§10, all Sub-tasks of a Story directly. Use a Task layer when it adds clarity; skip it when the Sub-task list is self-explanatory.

## 7. What is a Sub-task, and how is it defined?

A **Sub-task** is the smallest tracked unit — it maps to roughly one commit or one small PR, and is where the day-to-day engineering detail actually lives: evidence, acceptance criteria, blocking notes. Real example, `UIFPE-68` (Sub-task of Story `UIFPE-58`, "Gold Schema Framework"):

> *"Silver prerequisite: ingest `mtl_system_items_b` and `ego_mtl_sy_items_ext_b` into `eds_{env}_silver_product`. **Blocks UIFPE-59.**"* — includes the actual failing Spark error, a table of which sources are present/missing, and acceptance criteria ("All three sources resolvable... a Product Gold transform run completes... watermarks advance").

This is the real shape a Sub-task should take: concrete, falsifiable acceptance criteria, not a vague restatement of the Story.

## 8. QE — is it its own Sub-task?

**Both**, depending on size — QE is not forced into one shape:

- **Small/standard testing** (contract tests, unit tests for one endpoint) is folded into the Story's own acceptance criteria and the domain's `qe/` deliverable — it is not spun into a separate ticket. `UIFPE-39`'s acceptance criteria are exactly this: *"implemented in `src/` with tests/; ... CI green"* — no separate QE ticket exists or is needed.
- **Substantial testing work** gets its own Sub-task when it's big enough to track independently — real example: `UIFPE-76`'s Definition of Done lists `qe/` with **186 tests** including "intake↔compiled parity and event-schema drift guards" as a first-class deliverable of the Story, on par with the pipeline code itself, and its own sub-tasks (e.g. `UIFPE-75`, code-review remediation) carry QE-flavoured work explicitly.

Rule of thumb: if the testing work fits inside "implement + test this," it's acceptance criteria on the Story. If it's a distinct effort (a new test harness, a drift guard, a coverage gate), give it its own Sub-task labelled the same `capability:*` as the Story. QE Lead owns `qe/` per `.github/CODEOWNERS` either way; the `qe` skill generates contract tests and coverage checks against the registered schema.

## 9. When do we say "deploy," and what actually happens?

**"Deploy" means a `domain-cd.yml` promotion job has actually run against a real environment — not that CI passed, and not that a PR merged.** CI (`domain-ci.yml`) building, testing, scanning, and validating a schema only proves the image is buildable and correct; it does not touch any environment. Deploy is a distinct, separately gated event.

The real mechanics, from `UIFPE-69` ("Enable automated deploys for the product domain"), a live Sub-task documenting exactly what has to be true before a deploy can run at all:

1. **Repo secrets/vars set** — an `AWS_ROLE_ARN_<ENV>` per environment; a registry base URL for schema publish.
2. **OIDC trust configured** — the environment's IAM role trusts `repo:<org>/<repo>:environment:<env>:ref:refs/heads/<branch>` (no long-lived AWS keys).
3. **A deploy "latch"** — a repo variable (e.g. `PRODUCT_DEPLOY_ENABLED`) that must be explicitly opened; until then the workflow triggers, runs, and **explicitly skips with a notice** rather than deploying. This is the actual moment a team decides "we're live now," separate from "the pipeline exists."
4. **The run itself**, once the latch is open: assume the OIDC role → `terraform apply` / `sync_pipelines.py --apply` (idempotent — safe to be a no-op against already-applied infra) → health/watermark check → observability confirms.
5. **Per-environment gates**: dev deploys automatically on push; staging/prod stay **manual-dispatch only**, with prod additionally requiring a reviewed plan artifact before an explicit apply — GitHub Environment protection rules provide the required-reviewer gate.

So: say "deployed to dev" only once step 4 has actually run with the latch open in that environment — not when the Story moves to "Ready for QA," and not when the PR merges to `main`/`develop`.

## 10. Worked example — API: `UIFPE-39`

> **[UIFPE-39](https://pearson.atlassian.net/browse/UIFPE-39) · "S0-4 · Build the first API end-to-end"**
> Parent: `Domain: Product` (UIFPE-51). Labels: `capability:api`, `domain:product`.
> Produces: `domains/product/src/api/` (teaching reference: `delivery-lab/reference-implementations/api/developer-registration-api/`).
> Acceptance: contract designed and registered first; implemented in `src/` with tests; `src/Dockerfile`/terraform/helm present; CI green; deployed via the root `domain-cd.yml` to dev; ownership registered in JSM.

This is the canonical shape: one Domain-owned Story, one capability label, contract-first, and a deploy step that is a distinct, checkable line in the acceptance criteria — not implied by "CI green."

## 11. Worked example — Schema: `UIFPE-58` / `UIFPE-68`

There is no pure "build a Schema domain story" example yet the way there is for API — worth saying plainly rather than inventing one. The real, live Schema-flavoured work is `UIFPE-58` ("Gold Schema Framework (Silver → Gold)"), labelled `capability:data, capability:schema`, whose Definition of Done is schema-representation work end to end:

> *"Gold schemas registered as JSON Schema and consumable by the API (UIFPE-23) and Event (UIFPE-24) foundations... at least one serving projection (Athena/DynamoDB/OpenSearch) wired to a Gold view as a worked example."*

Its Sub-task `UIFPE-68` (§7 above) shows the Schema/Data boundary in practice: the blocker is a missing *Silver* source table, and the Sub-task's own note flags that this is arguably Data/ingestion work, not Gold/Schema work — "please re-parent if there is a better home." That's a healthy, visible example of the Data-vs-Schema line (`jira/capability-model.md` §"Data vs. Schema") being actively worked out on real work, rather than only existing as a paragraph in a standards doc.

---

## Sources (link, don't duplicate)

- Two axes, capability↔domain worked mapping: `capabilities/docs/capability-domain-jira-map.md`
- Jira issue hierarchy, Domain-owns-parent rule: `jira/jira-workflow.md`
- Capability definitions, Foundation Epics, Data-vs-Schema split: `jira/capability-model.md`
- Agent/MCP/A2A standards + Pearson precedent citations: `capabilities/agent/guidelines.md`, `capabilities/mcp/guidelines.md`, `capabilities/a2a/guidelines.md`
- Build/deploy mechanics for one domain: `architecture-design/operating-model/fde-build-deploy-workflow.md`
- Real Jira issues referenced: UIFPE-39, UIFPE-40, UIFPE-51, UIFPE-58, UIFPE-68, UIFPE-69, UIFPE-73, UIFPE-76 (all live in project UIFPE).
