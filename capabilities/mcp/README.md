# MCP (Model Context Protocol)

## Purpose
Tool connectivity for agents. MCP is how UIF exposes capabilities — APIs, data, tools — to AI agents through a standard protocol, so an agent consumes a governed capability the same way any other client would, not through a bespoke integration.

## Standards
- **MCP Ready:** capabilities exposed to agents are exposed through governed MCP servers, not ad-hoc endpoints.
- An MCP server is subject to the same auth, observability, and lifecycle as any other module (`../security/`, `../observability/`).
- Tools exposed over MCP have registered contracts (`../schema/`).

## Governance
- **Central:** the MCP server pattern, auth model for agent access, the catalog of exposed tools.
- **Local (FDE):** which tools a given MCP server exposes.

## Patterns
- One MCP server per bounded capability; tools map to governed operations.
- Least-privilege access per agent.

## Guidance
- **The worked standard:** [`guidelines.md`](guidelines.md) — how an FDE names an MCP server and its tools, documents the tool contract (reusing registered API/event schemas), tests it from unit through protocol conformance, and builds it, on the public Model Context Protocol spec. Same five-section shape as `../api/guidelines.md`.
- Foundation Epic: `MCP Foundation`; framework choice is a Spike (`../../PLANNING.md`). Reference module: `../../delivery-lab/reference-implementations/mcp/`.
