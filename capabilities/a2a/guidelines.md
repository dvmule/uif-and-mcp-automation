# A2A — Guidelines (the worked standard)

This is the detailed, do-this companion to the A2A capability `README.md`. It tells a Forward Deployed Engineer exactly how to name an agent's public capability advertisement, document a delegation contract, test cross-agent handoff, and build it — using the public **Agent2Agent (A2A) protocol** so one UIF agent can discover and delegate to another (or to an agent outside UIF) without a hard-wired, point-to-point integration.

This document follows the same **template shape** as every capability's `guidelines.md`: five sections — Naming, Contract documentation, Testing → automation → build, Standards & guidelines, Definition of done. A2A sits **above** MCP and Agent (`../mcp/`, `../agent/`): MCP governs an agent calling a *tool*; Agent governs one agent's own task/prompt/eval contract; A2A governs one *agent* calling another *agent*.

Grounding standards (all public): the **Agent2Agent (A2A) protocol** (AgentCard capability advertisement, Task lifecycle, JSON-RPC 2.0 transport — an open protocol, not a UIF invention), the **Model Context Protocol** (`../mcp/`, for the tools an agent itself exposes), **OAuth 2.1** (inter-agent authentication), and Semantic Versioning 2.0.0.

> **Protocol version.** The deployable UIF orchestrator stack (`../../stacks/uif-orchestrator/`) implements **A2A 1.0** (Agent Card at `/.well-known/agent-card.json` with `supportedInterfaces`, JSON-RPC and HTTP+JSON bindings, `TASK_STATE_*` lifecycle states). The examples below predate it and use A2A 0.3-era field names (`url`, `authentication`, lower-case states); read them for the governance rules, and the orchestrator stack for the current wire shapes. A full modernisation of this document is future work.

Worked example: `../../domains/hr/src/a2a/hr-routing.json` — a screening agent delegating a scheduling task to a scheduling agent.

---

## 1. Naming

**AgentCard.** One per agent module, kept alongside it: `agent-card.json` in `domains/<domain>/src/agents/`, cross-referenced from `domains/<domain>/src/a2a/`. This is the agent's public capability advertisement — the A2A analogue of an OpenAPI document or an MCP tool manifest.

**Skills.** `snake_case`, and **identical to the agent's task name** (`../agent/guidelines.md` §1) — an AgentCard never advertises a skill under a different name than the task it actually runs.

**Routing/delegation file.** `kebab-case`, `<domain>-routing.json`, kept in `domains/<domain>/src/a2a/` — declares which skills this domain's agents may delegate out for, not which specific agent instance (capability-based routing, §4).

**Tasks (A2A sense).** A delegated unit of work carries an opaque `taskId`; state names follow the protocol's own vocabulary — `submitted`, `working`, `input-required`, `completed`, `failed`, `canceled` — never a UIF-invented status set.

---

## 2. Contract documentation (capabilities are advertised, delegation is scoped)

**AgentCard — a minimal worked example** (the smallest advertisement that satisfies the protocol):
```json
{
  "name": "hr-scheduling-agent",
  "description": "Schedules interviews once a candidate has been screened.",
  "url": "https://hr-scheduling-agent.internal/a2a",
  "skills": [
    {
      "id": "schedule_interview",
      "name": "Schedule interview",
      "description": "Books an interview slot for a screened candidate.",
      "tags": ["hr", "scheduling"]
    }
  ],
  "authentication": { "schemes": ["oauth2"] }
}
```

**Delegation is scoped, not a blank handoff.** The delegating agent's own `agent-manifest.yaml` (`../agent/guidelines.md` §2) declares which skills it is allowed to delegate to, referencing the target by **skill, not by a specific agent instance**:
```json
{
  "delegatesTo": [
    { "skill": "schedule_interview", "onCompletionOf": "screen_candidate" }
  ]
}
```
The full, runnable version of both is `../../domains/hr/src/a2a/hr-routing.json`; read the snippets above as its smallest possible slice.

**Task lifecycle is the contract, not the payload shape alone.** A caller must handle every lifecycle state the protocol defines — in particular `input-required` (the delegate needs more information before it can complete) — not just `completed`/`failed`. A delegating agent that assumes a task is done as soon as it's accepted is not A2A-conformant.

**Discovery.** An agent's AgentCard is published at a well-known location (per the A2A protocol's discovery convention) so another agent — inside or outside UIF — can find it without a hardcoded endpoint baked into the caller.

---

## 3. Testing → automation → build

1. **AgentCard validation** — the card validates against the A2A protocol's own schema; every advertised skill matches a real task in the agent's `agent-manifest.yaml`.
2. **Routing tests** — given a skill request, the router resolves to an agent that actually advertises it (capability-based match); a request for an unadvertised skill is rejected, not silently forwarded.
3. **Task lifecycle tests** — drive a mock delegate through `submitted → working → input-required → completed` and `submitted → working → failed`; the caller's state machine handles both paths correctly, including the `input-required` round-trip.
4. **Trust-boundary tests** — a delegating agent's OAuth 2.1 token is scoped to only the skills in its `delegatesTo` list (§2); a call for an out-of-scope skill is rejected at the delegate's boundary, not just skipped by convention on the caller's side.
5. **Timeout/cancellation tests** — a delegate that never responds is timed out and the task is marked `canceled`, not left open indefinitely.
6. **End-to-end / smoke** — one real cross-agent delegation run against a deployed environment after CD, checked against its expected final task state.

**Automation & build gate (GitHub Actions, at repo root — `../delivery/`).** On every PR touching `domains/<domain>/src/a2a/**` or `domains/<domain>/src/agents/**`:
```
lint (AgentCard/routing schema)  →  unit  →  routing tests  →  lifecycle tests  →  trust-boundary tests  →  build container (src/Dockerfile)  →  security scan
```
The build artifact is still the **one container image** — A2A is a routing surface under `src/`, not a second image (`../infrastructure/docker.md`).

---

## 4. Standards & guidelines (non-negotiable design inputs)

- **A2A Ready.** Inter-agent communication routes through the governed capability-based pattern (§1–2), never a point-to-point call hardcoded to one agent's URL.
- **Capability-based routing.** Route to *whichever* agent advertises the needed skill — the caller does not need to know which specific agent instance will handle it, which is what makes a delegate replaceable without a caller change.
- **Delegation with bounded, traceable authority (`../security/`).** A delegating agent passes a scoped task and a scoped token — never its own full credential — and the `taskId` is the traceable link between the two agents' logs for the same unit of work.
- **Every hop is observable (`../observability/`).** Propagate the caller's correlation id/trace context across the A2A boundary; log skill requested, agent resolved, and final task state as structured JSON.
- **Agent capabilities are described by registered contracts (`../schema/`).** An AgentCard is registered the same way an OpenAPI document or MCP manifest is — not a document that only lives at runtime.

---

## 5. Definition of done (A2A)

- [ ] Naming follows §1 (AgentCard skills match task names; routing file `<domain>-routing.json`).
- [ ] AgentCard + delegation manifest follow §2: skills advertised match real tasks, delegation is skill-scoped not instance-hardcoded, task lifecycle states follow the protocol.
- [ ] AgentCard validation, routing, lifecycle, and trust-boundary tests pass (§3).
- [ ] Delegating agent's token is scoped to its declared `delegatesTo` list; out-of-scope calls are rejected at the delegate (§4).
- [ ] Correlation id/trace context propagated across the A2A hop; task outcome logged (§4).
- [ ] CI green: lint → unit → routing → lifecycle → trust-boundary → container build → scan (§3).
- [ ] Maps to a Jira Story (`capability:a2a` + its `domain:*`) per `../docs/capability-domain-jira-map.md`.

---

Worked instance: `../../domains/hr/src/a2a/hr-routing.json`. Works with `../mcp/` (the tools an agent exposes) and `../agent/` (the agent's own task/prompt/eval contract); deploys exactly like any other domain surface via `../infrastructure/`.
