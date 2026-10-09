# A2A (Agent-to-Agent)

## Purpose
Agent-to-agent collaboration and routing — how UIF agents discover, delegate to, and coordinate with each other, so that a task spanning several agents is handled through a governed collaboration pattern rather than hard-wired agent-to-agent calls.

## Standards
- **A2A Ready:** inter-agent communication uses the governed A2A routing pattern, not point-to-point coupling.
- Agents authenticate and authorise to each other (`../security/`); every hop is observable (`../observability/`).
- Agent capabilities are described by registered contracts (`../schema/`).

## Governance
- **Central:** the routing/collaboration pattern, agent discovery, trust model between agents.
- **Local (FDE):** which agents a given agent delegates to.

## Patterns
- Capability-based routing: route to the agent that advertises the needed capability.
- Delegation with traceable, bounded authority.

## Guidance
Foundation Epic: `A2A Foundation` (`../../PLANNING.md`). Platform runtime: `../../stacks/uif-orchestrator/` (A2A **1.0**: the deployable UIF orchestrator that selects MCP servers and A2A agents from one governed capability registry; its Agent Card skills are governed configuration, never derived from MCP discovery). Works with `../mcp/` (tools) and `../agent/` (agents).
