# Agent MCP setup — IDE agents connected to external systems

AI coding agents (Cursor, Claude Code, etc.) work best when they reach **GitHub, Jira, and Confluence** through **Model Context Protocol (MCP) servers** — standardized tool interfaces — instead of one-off shell scripts, hardcoded REST calls, or copy-pasted API tokens in chat.

This blueprint is for **IDE agent tooling**. It is separate from the **UIF MCP capability** (`capabilities/mcp/`), which defines how **domains expose governed tools** to agents at runtime (`domains/<domain>/src/mcp/`).

Read [`AGENTS.md`](../../AGENTS.md) for when agents must prefer MCP over custom integrations.

---

## Why MCP for UIF agents

| Without MCP | With MCP |
|---|---|
| Agent runs fragile `curl` to Jira | Agent calls typed `issues_get_detail`, `issues_create` tools |
| GitHub state read by guessing paths | Agent uses repository and PR tools with auth handled by the IDE |
| Confluence updated by pasted HTML only | Agent uses Confluence MCP when Atlassian connector is available |
| Credentials scattered in prompts | OAuth / IDE-managed secrets |

**Rule for agents:** if an MCP tool exists for the operation, **use it**. Shell and custom scripts are fallback only.

---

## Recommended MCP servers (UIF workflow)

| System | Purpose | Typical tools |
|---|---|---|
| **GitHub** | Branches, PRs, files, checks | `git_*`, `pull_request_*`, `repository_get_file_content` |
| **Jira (UIFPE)** | Stories, transitions, comments | `issues_get_detail`, `issues_create`, `issues_add_comment` |
| **Confluence (UIF space)** | Read/update pointer page | `getConfluencePage`, `updateConfluencePage` (when Atlassian MCP enabled) |

Exact server names depend on your IDE (Cursor GitKraken/GitLens MCP, Atlassian Rovo MCP, official GitHub MCP, etc.). Configure in the IDE — **not** in domain code.

---

## Cursor setup (pattern)

1. Open **Cursor Settings → MCP** (or `.cursor/mcp.json` in user/project config).
2. Enable servers your org approves (GitHub, Atlassian/Jira, Confluence).
3. Authenticate once via the IDE OAuth flow — never commit tokens to the repo.
4. Verify: ask the agent to read `UIFPE-<n>` or list open PRs via MCP, not raw API.

**Do not** store MCP credentials in:

- `domains/*/src/`
- `.env` committed to git
- Agent prompt files in the repo

---

## Agent session checklist (MCP)

Before implementing a story:

- [ ] Jira story fetched via MCP (`issues_get_detail`) — confirm parent Domain epic and labels.
- [ ] GitHub branch status via MCP or git — on `feature/UIFPE-<n>-…`.
- [ ] PRD task list read from repo file (`domains/<domain>/PRD-UIFPE-<n>.md`).
- [ ] Standards read from `capabilities/<c>/` (repo file read — MCP optional).

After implementation:

- [ ] Smart Commit pushed (GitHub for Jira app — not MCP).
- [ ] PR created via MCP or `gh pr create` with UIFPE key in title.

---

## UIF MCP capability (domain runtime — different concern)

When **building** an MCP server as product code (e.g. UIFPE-40):

| Layer | Location |
|---|---|
| Standard | `capabilities/mcp/guidelines.md` |
| Implementation | `domains/product/src/mcp/<name>/` |
| Contract | Registered in `src/schemas/` first |

IDE MCP connects the **assistant** to GitHub/Jira. Domain MCP connects **downstream agents** to governed product tools. Both use the same protocol name; different ownership and lifecycle.

---

## Anti-patterns

| Do not | Do instead |
|---|---|
| Embed Jira REST calls in `.claude/skills/` as the only path | Document MCP + link `jira-github-integration.md` |
| Create `scripts/jira-transition.sh` with PAT in repo | Use Smart Commits + optional `jira-transition.yml` |
| Scrape Confluence HTML in agent loops | MCP page fetch or human-approved publish via confluence skill |
| Build a custom "UIF MCP server" for git operations | Use GitHub MCP / native git |

---

## Related

- [`AGENTS.md`](../../AGENTS.md) — agent architectural rules
- [`PRD.md`](../../PRD.md) — sequential task workflow
- [`capabilities/mcp/guidelines.md`](../../capabilities/mcp/guidelines.md) — domain MCP servers
- [`capabilities/delivery/jira-github-integration.md`](../../capabilities/delivery/jira-github-integration.md) — Smart Commits (primary Jira link)

*Last updated: 2026-08-06*
