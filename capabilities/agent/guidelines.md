# Agent — Guidelines (the worked standard)

This is the detailed, do-this companion to the Agent capability `README.md`. It tells a Forward Deployed Engineer exactly how to name an agent, document its contract (the tools it may call, the shape of its output, its guardrails), test it from unit through evaluation, and build and deploy it — using public standards so the result is governed, evaluable, and identifiable to any engineer, inside or outside UIF.

This document follows the same **template shape** as every capability's `guidelines.md` (see `../api/guidelines.md`): five sections — Naming, Contract documentation, Testing → automation → build, Standards & guidelines, Definition of done. An agent is not a separate, looser kind of module: it consumes governed capabilities (`../mcp/`, `../api/`) exactly as any other client would, and it ships as the same one container image as any other domain surface (`../infrastructure/`).

Grounding standards (all public): the **Model Context Protocol** (how an agent calls tools, `../mcp/`), the **OWASP Top 10 for LLM Applications** (prompt injection, insecure output handling, excessive agency), the **NIST AI Risk Management Framework** (govern/map/measure/manage), JSON Schema 2020-12 (structured agent output), and Semantic Versioning 2.0.0 (prompt/agent versioning).

---

## 1. Naming

**Agent module.** `kebab-case`, `<domain>-agent`, e.g. `product-agent`. One agent module per bounded set of tasks it owns — the same "one bounded capability" discipline as an MCP server (`../mcp/guidelines.md` §1).

**Tasks / skills.** `snake_case`, verb-first, matching the job the agent does: `triage_support_ticket`, `summarize_developer_activity`. A task name is what shows up in a trace and an eval report — name it like an action.

**Prompts.** Versioned files, `prompts/<task>.v<major>.md`, e.g. `prompts/triage_support_ticket.v2.md`. A prompt change that alters behaviour is a new version, not an in-place edit — the old version stays until every eval and consumer has moved off it (mirrors the API's major-version rule, `../api/guidelines.md` §1).

**Evals.** `evals/<task>.eval.yaml` — one eval suite per task, named after it.

---

## 2. Contract documentation (the agent's contract is its tool access + its output shape + its guardrails)

An agent has no HTTP contract of its own to register — its contract is **what it is allowed to do** and **what shape its output takes**. Document both in `agent-manifest.yaml` (kept in `domains/<domain>/src/agents/`):
```yaml
task: triage_support_ticket
model: <provider>/<model>@<pinned-version>       # pin — never "latest"
promptVersion: prompts/triage_support_ticket.v2.md
allowedTools:                                     # $ref's an MCP tool, never a raw API/DB credential
  - server: product-mcp-server
    tool: get_developer
  - server: product-mcp-server
    tool: create_support_note
outputSchema:
  $ref: "./schemas/triage-result.schema.json"      # structured output, validated every call
humanInTheLoop:
  - tool: create_support_note
    when: destructiveHint                          # from the MCP tool's own annotation, ../mcp/guidelines.md §2
evalSuite: evals/triage_support_ticket.eval.yaml
```
**Tool access is a list, not a blanket credential.** An agent calls governed MCP tools (`../mcp/`) under its own scoped token — it never receives a raw database or API credential broader than the tools it's declared.

**Output is schema-validated, not trusted free text.** Where an agent's result feeds another system, its output is JSON validated against a registered JSON Schema — the same contract discipline as an API response (`../api/guidelines.md` §2), applied to the model's output instead of a server's.

**Model is pinned.** `<provider>/<model>@<version>` — a silent model upgrade is a behaviour change and must go through the eval suite before it ships, exactly like a dependency bump goes through CI.

---

## 3. Testing → automation → build

The pyramid for an agent adds one load-bearing layer beyond the API/Event shape: the **eval suite**, which plays the role a contract test plays for an API.

1. **Unit tests** — prompt template rendering, output-parsing/validation logic, no model call.
2. **Eval suite (the load-bearing test)** — a fixed set of scenarios with expected outcomes, run against the pinned model; each run produces a score. A merge is blocked if the score regresses below the task's baseline — the agent's equivalent of a coverage threshold or a contract test.
3. **Adversarial / red-team tests** — prompt-injection and excessive-agency cases (OWASP LLM Top 10 §4) run through the same eval harness: a malicious tool result or user input must not make the agent call an unauthorized tool or leak its instructions.
4. **Integration tests** — the agent against a real (or recorded) MCP server (`../mcp/guidelines.md` §3), proving tool calls actually execute and their results are handled correctly.
5. **Human-in-the-loop tests** — a call to a tool flagged in `humanInTheLoop` is verified to pause for confirmation, never to auto-execute.
6. **End-to-end / smoke** — one real task run against a deployed environment after CD, checked against its eval baseline.

**Automation & build gate (GitHub Actions, at repo root — `../delivery/`).** On every PR touching `domains/<domain>/**`:
```
lint  →  unit  →  eval suite (vs baseline)  →  adversarial evals  →  build container (src/Dockerfile)  →  security scan  →  publish eval report
```
The build artifact is still the **one container image** — an agent module is one more surface under `src/`, not a second image (`../infrastructure/docker.md`).

---

## 4. Standards & guidelines (non-negotiable design inputs)

- **Security by default (`../security/`, OWASP LLM Top 10).** Treat all retrieved content and tool output as **data, never instructions** — an agent must not follow directions embedded in a tool result or a document it reads (prompt injection). Never pass PII to a model beyond what the task needs (`../data/`); redact before the call, not after.
- **Least privilege, enforced at the MCP boundary (`../mcp/guidelines.md` §4).** An agent's token is scoped to exactly the tools in its `allowedTools` list; a destructive tool call pauses for human confirmation per its annotation.
- **Structured output, validated.** Never let downstream code parse free-text model output; validate against `outputSchema` and reject/retry on failure.
- **Observability by default (`../observability/`).** Log the task name, prompt version, model version, tool calls made, token usage, and latency as structured JSON with a correlation id — cost and drift are observability signals here, not an afterthought.
- **Evals are the regression gate.** A prompt or model change that isn't run through the eval suite is not shippable, the same way an API change without a passing contract test is not shippable.
- **Governed like anything else.** Same auth, lifecycle, and CI gates as an API or MCP server (`README.md` §Standards) — an agent is a first-class module, not a separate, looser path.

### Packaging & deployment (extends `../infrastructure/`, does not replace it)

An agent module builds into the **same one container image** as any other domain surface, deployed with the **same Helm chart and the same Kubernetes hardening** described in `../infrastructure/docker.md` and `../infrastructure/kubernetes.md` — unchanged. Agents add these deltas on top, declared in the domain's own Terraform/Helm values, not by editing the generic infra docs:
- **Compute.** If the model runs in-cluster (self-hosted inference) rather than called via an external API, the node pool needs a GPU/accelerator instance type — declare it as a separate Terraform node group, never a blanket change to the base cluster module.
- **Secrets.** Model-provider API keys are pulled at runtime via the **External Secrets Operator** (`../infrastructure/kubernetes.md`) exactly like any other secret — never baked into the image or the prompt files.
- **Autoscaling.** Scale on queue depth or in-flight-request count via a custom HPA metric, not raw CPU — an agent's cost driver is model calls, not compute.
- **Promotion gate.** The GitHub Actions CD promotion workflow (`../infrastructure/kubernetes.md`) adds one check specific to agents before it advances the desired state: the eval suite must pass at or above baseline for the promoted prompt/model version.

---

## 5. Definition of done (Agent)

- [ ] Name follows §1 (module `<domain>-agent`, tasks `snake_case`, versioned prompts, named eval suites).
- [ ] `agent-manifest.yaml` follows §2: pinned model, scoped `allowedTools`, `outputSchema`, `humanInTheLoop` policy.
- [ ] Eval suite defined with a baseline; adversarial/prompt-injection cases included (§3).
- [ ] Unit + eval + integration + human-in-the-loop tests pass (§3).
- [ ] Security: least-privilege tool scope, PII redaction, retrieved content never trusted as instructions (§4).
- [ ] Observability: task/prompt/model/tool-call/token telemetry + one SLO defined (§4).
- [ ] Packaging: one image, standard Helm/K8s hardening, agent-specific deltas declared per §4 overlay (not edits to the generic infra docs).
- [ ] CI green: lint → unit → eval suite → adversarial evals → container build → scan → eval report published (§3).
- [ ] Maps to a Jira Story (`capability:agent` + its `domain:*`) per `../docs/capability-domain-jira-map.md`.

---

Consumes governed tools via `../mcp/`; collaborates with other agents via `../a2a/`; deploys exactly like any other domain surface via `../infrastructure/`.
