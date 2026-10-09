# Engineering Principles

These are the non-negotiable principles that shape every UIF domain and capability. Read them before designing anything — they are the rules a design is checked against, not aspirations. Each states the rule (the *what*) and why it exists (the *why*); the *how* lives in the relevant `../../capabilities/` doc and `../../delivery-lab/blueprints/`.

They are consolidated here rather than split into one thin file each; split a principle into its own file only when it grows enough content to warrant it (`../CLAUDE.md`, "Don't create empty stub files").

## Design principles

- **API First** — Design and register the contract before implementation. The contract is the product; the code serves it. → `../../capabilities/api/`
- **Event Driven** — Prefer asynchronous, decoupled events where latency and coupling matter; batch is a deliberate exception. → `../../capabilities/event/`
- **Data Centric** — Treat data as an owned product with a published contract, not a byproduct. → `../../capabilities/data/`
- **Schema Registry First** — No API, event, or data contract is implemented before its schema is registered; compatibility is enforced in CI. → `../../stacks/apicurio/standards/`
- **Semantic By Design** — Reference the canonical vocabulary rather than redefining terms per domain. → `../../capabilities/semantics/`
- **Knowledge Graph Enabled** — Represent entities and relationships in a governed graph so agents and apps reason over connected context. → `../../capabilities/knowledge-graph/`

## Agent principles

- **MCP Ready** — Expose capabilities to agents through governed MCP servers, not ad-hoc endpoints. → `../../capabilities/mcp/`
- **A2A Ready** — Inter-agent collaboration uses the governed routing pattern, not point-to-point coupling. → `../../capabilities/a2a/`
- **Agent Ready** — Agents are first-class participants under the same governance, security, and observability as any other capability surface. → `../../capabilities/agent/`

## Platform principles

- **Secure By Default** — Authenticated, authorised, least-privilege from the start; security is a design input. → `../../capabilities/security/`
- **Observable By Default** — Metrics, logs, traces, and SLOs on every domain from day one. → `../../capabilities/observability/`
- **Cloud Native** — Built for AWS/Kubernetes, not lifted onto it. → `../../capabilities/infrastructure/`
- **Container First** — Every domain builds to exactly one container image (`src/Dockerfile`) — the single build/deploy contract; capability surfaces inside `src/` are not independently deployable. → `../../capabilities/infrastructure/`

## Ways of working

- **Self Service First** — An FDE can go from Story to a running domain using this repository, without gatekept handoffs. → `../operating-model/fde-build-deploy-workflow.md`
- **DevSecOps** — Security and quality are automated into the pipeline, not bolted on. → `../../capabilities/delivery/`, `../../capabilities/security/`
- **Engineering Excellence** — Reference-quality first, production-quality once proven; no speculative abstraction. → `../CLAUDE.md`
