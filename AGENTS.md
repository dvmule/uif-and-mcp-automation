# AGENTS.md — system instructions for AI coding agents

This file tells **AI agents** (Cursor, Claude Code, Copilot, etc.) how to work in the Unified Integration Fabric (UIF) repository across sessions. Human engineers read `CLAUDE.md` and `README.md` first; agents read **this file** plus the **PRD** for the active story.

**Canonical human docs:** `CLAUDE.md` · `README.md` · `PLANNING.md` · [`jira/uif-faq.md`](jira/uif-faq.md)

**Active work:** always follow the story's [`PRD.md`](PRD.md) (or `domains/<domain>/PRD-<UIFPE-n>.md`) — **one task at a time**, verify each step before the next.

---

## Session startup (every time)

1. Read `CLAUDE.md` repository rules (domains × capabilities).
2. Read the **PRD** linked to the Jira key (e.g. UIFPE-39).
3. Confirm **branch** matches `feature/UIFPE-<n>-<slug>` and **parent** is the correct Domain epic.
4. Identify **domain** (`domains/<name>/`) and **capabilities** (`capabilities/<c>/` standards to conform to).
5. Use **MCP tools** for GitHub/Jira/Confluence when available — do not hardcode one-off API scripts. See [`delivery-lab/blueprints/agent-mcp-setup.md`](delivery-lab/blueprints/agent-mcp-setup.md).

---

## Why two axes — for agents

UIF is **T-shaped on purpose**: **domain** = where you ship (vertical); **capability** = standards you conform to (horizontal). This is **good for agentic work** because it gives you bounded context — not because simpler single-folder repos are better.

**Your session bounds (state these before coding):**

```
Domain:     <product|customer|…>     → write only under domains/<domain>/src/
Capabilities: <api, schema, …>       → read capabilities/<c>/guidelines.md first
Jira:       parent = Domain epic     → labels = capability:*
```

**Why agents fail here:** fuzzy ownership (capability as parent), writing under `capabilities/`, or skipping the PRD sequential tasks. **Why agents succeed:** explicit domain + capability list in the PRD, read standards then implement once in the domain tree.

Human-oriented explanation (T-diagram, leadership framing): [`jira/uif-faq.md` § Why two axes](jira/uif-faq.md#why-two-axes-t-shaped-at-all).

---

## Architectural rules (non-negotiable)

| Rule | Detail |
|---|---|
| **Code location** | All implementation under `domains/<domain>/src/`. Never root `src/`, never `modules/`, never executable code in `capabilities/` or `architecture-design/`. |
| **Two axes** | **Domain** = who ships (vertical). **Capability** = how (horizontal standard). Jira parent = Domain epic; capability = label. |
| **One image** | One `src/Dockerfile` per domain — the single build/deploy contract. |
| **Schema first** | Register contracts in `domains/<domain>/src/schemas/` before code that depends on them. |
| **API first** | OpenAPI (or registered event/MCP contract) before handlers. |
| **Security & observability** | Design inputs, not afterthoughts — authN/authZ, secrets, golden signals stated before merge. |
| **Link don't duplicate** | Point to `capabilities/<c>/guidelines.md`; do not copy standards into domain code comments. |
| **No speculative abstraction** | Three similar domains before extracting a shared library. |
| **Minimal diff** | Only change what the current PRD task requires. |

---

## Preferred design patterns

| Pattern | Where | Reference |
|---|---|---|
| **Capability standard + domain implementation** | `capabilities/api/` → `domains/<d>/src/api/` | [`capability-domain-jira-map.md`](capabilities/docs/capability-domain-jira-map.md) |
| **Contract-first** | `src/schemas/` → `src/api/{entity}/openapi.yaml` → the shared reader | `stacks/apicurio/standards/`, `capabilities/api/` |
| **Channel mapping gate** | API returns only the entity's `x-channels: api` properties | the generated projection under `src/schemas/channels/{entity}/` |
| **RFC 7807 errors** | `application/problem+json` | `capabilities/api/guidelines.md` |
| **Multi-stage non-root Docker** | `src/Dockerfile` | `capabilities/infrastructure/docker.md` |
| **Structural DoD** | checklist, schemas, Dockerfile, `qe/` | `domains/<domain>/DOMAIN-CHECKLIST.md` |
| **Smart Commits** | `UIFPE-<n>` + `#comment` on **every** commit; `#transition` at lifecycle points | `.github/workflows/smart-commit.yml` |
| **Per-engineer commit identity** | author = the engineer who ran the session; agent in `Co-Authored-By:` | `.github/workflows/commit-identity.yml` |
| **Reviewable pull request** | title = `UIFPE-<n>` + what it delivers; body = `## Summary` + `## Verification` | `.github/workflows/pull-request.yml` |

---

## Prohibited actions and packages

### Repository actions — never

- Commit secrets, `.env`, tokens, or credentials (including in tests/fixtures meant for prod).
- Add executable code under `capabilities/`, `architecture-design/`, or `delivery-lab/` outside `reference-implementations/`.
- Introduce root-level `src/` or revive `modules/` paths.
- Force-push `main` or skip git hooks unless the user explicitly requests it.
- **Commit under any identity except the engineer running the session.** Check `git config user.email` before the first commit in a session; a shared machine may carry someone else's address. If it is not the engineer's own Jira-account email, pass theirs explicitly (`git -c user.name=... -c user.email=... commit`) rather than committing and fixing it later. Never invent an agent alias or service address — credit the agent in a `Co-Authored-By:` trailer. `commit-identity.yml` fails the PR on a mismatch.
- Large unverified code dumps — follow PRD tasks sequentially.
- Duplicate capability standards inside domain folders.

### Dependencies — avoid unless PRD + security review

| Category | Prohibited / discouraged | Use instead |
|---|---|---|
| **Secrets in images** | Baking API keys, PATs, Jira tokens into Dockerfile layers | Runtime injection (External Secrets / Keeper per `capabilities/security/`) |
| **Secret stores in Helm** | AWS Secrets Manager, HashiCorp Vault, Azure Key Vault in domain Helm templates | Keeper (`keeper-secrets-manager`) per CI gate |
| **Container base** | `:latest` tags, root `USER`, full OS images in runtime stage | Pinned digest, distroless/non-root (`docker.md`) |
| **HTTP clients in agents** | Ad-hoc curl scripts to Jira/GitHub when MCP server exists | MCP tools (see agent-mcp-setup.md) |
| **Auth bypass** | `verify=False`, disabled TLS, wildcard CORS in production paths | OIDC via Entra (`login.microsoftonline.com` in OpenAPI) |
| **Dynamic code execution** | `eval`, `exec`, unpickling untrusted data in domain services | Typed parsers, JSON Schema validation |
| **Orphan dependencies** | Packages not used or not pinned in domain manifest | Domain `pyproject.toml` / `package.json` with lockfile |

Domain-specific bans are listed in each domain's `CLAUDE.md` when they exist.

---

## Testing commands (by domain)

Run from the domain's documented path. CI target state is in Sprint 0 (UIFPE-37); local commands work today.

### Product domain (pilot)

From `domains/product/src/`:

```bash
py -m pytest domains/product/qe stacks/echo/qe/gold   # the domain's artifacts and the runtime serving them
```

A gold domain ships no service of its own to read its entity: the shared
serving copy does (ADR-0026), so there is no local API to run and no image to
build.

Optional (when installed — matches `capabilities/infrastructure/docker.md`):

```bash
hadolint Dockerfile
trivy image product:local
```

### New domain (from mould)

```bash
./create-domain.sh <domain>
# Then follow domains/<domain>/DOMAIN-CHECKLIST.md and domain CLAUDE.md
```

### Repo-level CI (what PRs trigger)

- Changes under `domains/**` → `.github/workflows/domain-ci.yml` (structural DoD + Entra/Keeper gates when API/Helm exist).
- Changes under `capabilities/**` only → no domain CI (expected).

---

## Git · Jira traceability (agents must follow)

| Action | Convention |
|---|---|
| Branch | `feature/UIFPE-<n>-<short-desc>` |
| Commit | `UIFPE-<n> #comment <what> #<transition>` |
| PR title | `UIFPE-<n> · <summary>` |
| PR scope | One story / one PRD unless user splits |

Smart Commits are the **working** Jira integration. Do not rely on `jira-transition.yml` until Atlassian admin resolves runner 401.

---

## PRD-driven workflow (mandatory for implementation)

1. PM/FDE creates or updates a **PRD** from the Jira story ([`PRD.md`](PRD.md) template).
2. Agent executes **Task 1 only** → runs verification → marks complete.
3. Agent proceeds to Task 2 only after Task 1 passes.
4. No task skipping. No "while I'm here" refactors.
5. Update PRD checkboxes in the same commit series as the work.

---

## MCP (Model Context Protocol)

Two different meanings in UIF — do not conflate:

| Meaning | Purpose | Doc |
|---|---|---|
| **IDE agent MCP** | Connect Cursor/agent to GitHub, Jira, Confluence | [`delivery-lab/blueprints/agent-mcp-setup.md`](delivery-lab/blueprints/agent-mcp-setup.md) |
| **UIF MCP capability** | Domain exposes governed tools to agents (`domains/<d>/src/mcp/`) | `capabilities/mcp/guidelines.md` |

Prefer **MCP servers** over custom API glue in agent sessions.

---

## When stuck

| Question | Read |
|---|---|
| Domains vs capabilities? | [`jira/uif-faq.md`](jira/uif-faq.md) |
| GitHub + Jira alignment? | [`capabilities/docs/capability-domain-jira-map.md`](capabilities/docs/capability-domain-jira-map.md) |
| Jira hierarchy? | [`jira/jira-workflow.md`](jira/jira-workflow.md) |
| Commit / PR / CI? | [`.claude/skills/fde/SKILL.md`](.claude/skills/fde/SKILL.md) |
| Create Jira issues? | [`.claude/skills/pm/SKILL.md`](.claude/skills/pm/SKILL.md) |

---

## Drift rule

If `AGENTS.md` and `CLAUDE.md` disagree, **`CLAUDE.md` wins** — open a PR to fix `AGENTS.md`.

*Last updated: 2026-08-06*
