# Capabilities

Capabilities are the **horizontal** axis of UIF — the reusable technical disciplines every domain draws on. Each folder here holds the **Standards, Governance, Patterns, and Guidance** for one capability: the definition of how work in that discipline is done well, once, for the whole fabric.

A capability is realized inside a domain as a same-named folder under `src/`. `capabilities/api/` defines the API standard; `../domains/<domain>/src/api/` is where a domain implements it. The standard lives here; the code lives in the domain (all under `src/`, built into the domain's one container image). Infrastructure, Security, Observability, and Delivery are cross-cutting — they surface in `infrastructure/` and the repo-root CI, not as a `src/` folder.

**Capability folders never contain executable code** (`../CLAUDE.md`, rule 2). Implementation lives only under `../domains/<domain>/` and, for teaching examples, `../delivery-lab/reference-implementations/`.

Architecture & Design is deliberately **not** in this list — it is the frame the capabilities sit in and lives at the top level in `../architecture-design/`.

Every capability doc follows the same shape:

- **Purpose** — plain-language: why the capability exists.
- **Standards** — the non-negotiable rules for this capability.
- **Governance** — what is centrally decided vs. what an FDE decides locally.
- **Patterns** — the recommended, reusable approaches.
- **Guidance** — practical how-to and the entry point to the delivery-lab blueprint.

## Shared homes (across all capabilities)

Alongside the capability folders sit four cross-capability homes for material that spans more than one capability:

- [`docs/`](docs/) — connective documentation between capabilities.
- [`playbooks/`](playbooks/) — step-by-step golden-path guides.
- [`templates/`](templates/) — non-runnable document/artifact templates (runnable starter-kits stay in `../delivery-lab/`).
- [`assets/`](assets/) — shared diagrams and images referenced by the above.

## The thirteen

| Capability | Folder | Owns | Domain folder |
|---|---|---|---|
| API | [`api/`](api/) | Request/response capabilities as products | `domains/<d>/src/api/` |
| Event | [`event/`](event/) | Event-driven, asynchronous integration | `domains/<d>/src/events/` |
| Data | [`data/`](data/) | Canonical/semantic data models and data contracts | `domains/<d>/src/schemas/` (contracts) |
| Schema | [`schema/`](schema/) | Register-first contracts, versioning, compatibility | `domains/<d>/src/schemas/` |
| Semantics | [`semantics/`](semantics/) | Ontologies, vocabularies, canonical meaning | `domains/<d>/src/semantics/` |
| Knowledge Graph | [`knowledge-graph/`](knowledge-graph/) | Linked, queryable enterprise knowledge | `domains/<d>/src/knowledge-graph/` |
| MCP | [`mcp/`](mcp/) | Model Context Protocol — tool connectivity | `domains/<d>/src/mcp/` |
| A2A | [`a2a/`](a2a/) | Agent-to-Agent collaboration | `domains/<d>/src/a2a/` |
| Agent | [`agent/`](agent/) | AI agents and agentic workflows | `domains/<d>/src/agents/` |
| Security | [`security/`](security/) | Identity, access, DevSecOps | cross-cutting — `infrastructure/` + repo-root CI |
| Observability | [`observability/`](observability/) | Metrics, logs, traces, SLOs | `domains/<d>/infrastructure/observability/` |
| Infrastructure | [`infrastructure/`](infrastructure/) | AWS, Kubernetes, Terraform substrate | `domains/<d>/infrastructure/{terraform,kubernetes,helm}/` |
| Delivery | [`delivery/`](delivery/) | CI/CD — GitHub Actions | root `.github/workflows/` (`domain-ci.yml`, `domain-cd.yml`) |
