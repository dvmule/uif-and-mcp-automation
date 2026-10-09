# Agent

## Purpose
AI agents and agentic workflows. This capability defines how an agent is built, governed, and operated in UIF — as a first-class integration participant that consumes and exposes capabilities under the same rules as any other module, not through a separate, looser path.

## Standards
- **Agent Ready:** agents consume capabilities through governed interfaces — APIs, MCP tools (`../mcp/`), the knowledge graph (`../knowledge-graph/`) — never ungoverned access.
- Every agent module has evaluations (`evals/`) alongside `tests/`; prompts are versioned (`prompts/`).
- Agents are authenticated, authorised, and observable like any other module (`../security/`, `../observability/`).

## Governance
- **Central:** agent module anatomy, evaluation bar, the tool/data access model, human-in-the-loop policy.
- **Local (FDE):** the agent's task logic and prompts.

## Patterns
- Retrieval-augmented generation over governed data/knowledge graph.
- Tool use via MCP; collaboration via A2A (`../a2a/`).

## Guidance
- **The worked standard:** [`guidelines.md`](guidelines.md) — how an FDE names an agent, documents its contract (scoped tool access, structured output, human-in-the-loop policy), tests it through an eval suite, and builds and deploys it — on the same one-container-image substrate as any other domain surface (`../infrastructure/`). Same five-section shape as `../api/guidelines.md`.
- Foundation Epic: `Agent Framework Foundation` (`../../PLANNING.md`). Reference module: `../../delivery-lab/reference-implementations/agents/`. Module anatomy adds `prompts/` and `evals/` — see `../../delivery-lab/reference-implementations/README.md`.
