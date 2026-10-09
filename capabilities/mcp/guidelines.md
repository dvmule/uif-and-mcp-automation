# MCP — Guidelines (the worked standard)

This is the detailed, do-this companion to the MCP capability `README.md`. It tells a Forward Deployed Engineer exactly how to name an MCP server and its tools, document the tool contract in YAML/JSON Schema, test it from unit through automation, and build it — using the public **Model Context Protocol** specification so the result is identifiable and callable by any MCP-compliant client, inside or outside UIF.

This document follows the same **template shape** as every capability's `guidelines.md` (see `../api/guidelines.md`): five sections — Naming, Contract documentation, Testing → automation → build, Standards & guidelines, Definition of done. Where API exposes a capability over HTTP for any client, MCP exposes the *same governed capability* to an AI agent as a **tool** — same contract discipline, a different, agent-native transport.

Grounding standards (all public): the **Model Context Protocol** specification (JSON-RPC 2.0 based; `tools`, `resources`, `prompts` primitives), JSON Schema 2020-12 (`inputSchema`/`outputSchema`), the MCP Authorization spec (OAuth 2.1), and Semantic Versioning 2.0.0.

---

## 1. Naming

**MCP server.** One server per bounded capability (`README.md` §Patterns) — never a single god-server exposing every domain's tools. `kebab-case`, `<domain>-mcp-server`.
- Good: `product-mcp-server`, `finance-mcp-server`.

**Tools.** `snake_case`, verb-first, matching the governed operation it wraps: `register_developer`, `get_developer`, `list_developers`. A tool name is part of the contract an agent (and its user) reasons about — name it like an action, not an internal method.

**Resources** (if the server exposes MCP `resources`, not just `tools`). A URI per resource: `<domain>://<resource>/{id}`, e.g. `product://developers/{developerId}`.

**Prompts** (if the server exposes reusable prompt templates). `kebab-case`, e.g. `summarize-developer-activity`.

---

## 2. Contract documentation (Schema Registry First — the tool wraps a registered operation, it never re-defines one)

**No tool is hand-invented.** Every MCP tool wraps a capability that already has a registered contract — usually an API operation (`../api/`) or an event (`../event/`). The tool's `inputSchema`/`outputSchema` **`$ref`s the same registered JSON Schema components** the API's `openapi.yaml` uses; it is never a second, drifting copy of the request/response shape.

**Document the server's tool manifest** in `mcp-manifest.yaml` (kept in `domains/<domain>/src/mcp/`) as the reviewable source of truth for what `tools/list` returns at runtime:
```yaml
tools:
  - name: register_developer
    description: Register a new developer account. Idempotent on email.
    inputSchema:
      $ref: "../api/openapi.yaml#/components/schemas/RegisterDeveloperRequest"
    outputSchema:
      $ref: "../api/openapi.yaml#/components/schemas/Developer"
    annotations:
      readOnlyHint: false
      destructiveHint: false
      idempotentHint: true
      openWorldHint: false
```
**Tool annotations are mandatory**, not decoration — `readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint` (MCP spec). A client (and a human-in-the-loop policy, `../agent/`) uses these to decide whether a call needs confirmation before it runs.

**Transport is declared, not assumed.** `stdio` for a local/dev server; **Streamable HTTP** for a remote, shared server (the current MCP transport — SSE-only transport is legacy and not used for new servers). State the choice in the domain's `README.md`.

**Versioning (SemVer 2.0.0).** Additive (new tool, new optional input field) is a minor/patch bump on the server; removing or renaming a tool, or tightening a schema, is a major bump with the old tool kept and marked deprecated in its `description` for a documented window.

---

## 3. Testing → automation → build

1. **Unit tests** — each tool's handler logic, no protocol, no network.
2. **Contract tests** — call each tool through an in-process MCP server and validate the response against the **registered** `outputSchema` (same Ajv 2020 / `jsonschema` Draft 2020-12 technique as the API contract test, `../api/guidelines.md` §3) — proving the manifest and the running server have not drifted.
3. **Protocol conformance** — drive the server with an MCP client SDK (or the MCP Inspector) and assert `tools/list`, `tools/call`, and error responses conform to the spec's JSON-RPC 2.0 envelope.
4. **Integration tests** — the server against its real dependency (the API/DB it wraps), via the same ephemeral-container approach as `../api/guidelines.md` §3.
5. **Authorization tests** — an unauthenticated or under-scoped call is rejected (§4); a destructive tool without the required confirmation flag is rejected.
6. **End-to-end / smoke** — a real MCP client (or the Inspector) calls one read tool and one write tool against a deployed environment after CD.

**Automation & build gate (GitHub Actions, at repo root — `../delivery/`).** On every PR touching `domains/<domain>/**`:
```
lint  →  unit  →  contract (vs mcp-manifest.yaml)  →  protocol conformance  →  build container (src/Dockerfile)  →  security scan  →  publish tool catalog
```
The build artifact is still the **one container image** — an MCP server is one more surface under `src/`, not a second image (`../infrastructure/docker.md`).

---

## 4. Standards & guidelines (non-negotiable design inputs)

- **Security by default (`../security/`, MCP Authorization spec).** A remote server is an OAuth 2.1 resource server: every call carries a bearer token, scoped per tool (least privilege — a token that can `get_developer` should not also authorize `register_developer`). A local `stdio` server inherits the invoking process's identity; it is never run with broader privilege than the agent needs.
- **Human-in-the-loop for destructive tools.** Any tool annotated `destructiveHint: true` requires an explicit confirmation step in the calling agent/client before it executes (`../agent/`) — the annotation is the contract that makes this enforceable, not a convention the agent has to infer.
- **Observability by default (`../observability/`).** Every tool call is logged as structured JSON with the tool name, a correlation id, and duration (RED signals per tool); propagate the caller's trace context if present.
- **One server per bounded capability.** Keeps the blast radius, the auth scope, and the tool catalog reviewable; a server that accumulates every domain's tools is a governance and security anti-pattern.
- **The tool is a thin wrapper.** Business logic and validation live in the API/service the tool calls, not duplicated in the tool handler — the tool's job is protocol translation and authorization, nothing else.

---

## 5. Definition of done (MCP)

- [ ] Name follows §1 (server `<domain>-mcp-server`, tools `snake_case` verb-first).
- [ ] `mcp-manifest.yaml` follows §2: every tool's schemas `$ref` a registered API/event schema; annotations present; transport declared.
- [ ] Unit + contract tests pass; contract test validates tool output against the registered schema (§3).
- [ ] Protocol conformance verified (MCP Inspector or client SDK) (§3).
- [ ] Auth: OAuth 2.1 bearer + per-tool scope enforced; destructive tools require confirmation (§4).
- [ ] Observability signals + one SLO defined (§4).
- [ ] CI green: lint → unit → contract → conformance → container build → scan → catalog published (§3).
- [ ] Maps to a Jira Story (`capability:mcp` + its `domain:*`) per `../docs/capability-domain-jira-map.md`.

---

Deployment is identical to any other domain surface — one image, one Helm chart (`../infrastructure/docker.md`, `../infrastructure/kubernetes.md`); an MCP server introduces no separate deploy path. Consumed by agents (`../agent/`) and, for inter-agent delegation, exposed onward via `../a2a/`.
