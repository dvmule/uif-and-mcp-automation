---
name: architect
description: Use this skill whenever the user (a Solution Architect / FDE doing architecture work) wants to design a domain, model a bounded context, decide which UIF capabilities a piece of work touches, write an Architecture Decision Record (ADR), or check a design against UIF's engineering principles before building. Trigger on "design this domain", "which capabilities does this touch", "write an ADR", "does this follow our principles", "solution design for", or requests to pick a reference architecture pattern.
---

# architect — design-time checklist for UIF Solution Architects

Capabilities in UIF are guard rails, read and applied at build/design time — they are never imported at runtime. This skill's job is to make that design-time check fast and consistent: name every capability a piece of work spans, surface the relevant principles and existing patterns, and scaffold the ADR — while leaving the actual modelling and pattern choice to the architect.

## Workflow

1. **Name every capability the work spans.** Most real work crosses more than one of the 14 capabilities (an Agent consumes APIs, Events, and Data). List all of them — don't force a design into a single capability for tidiness. Cross-check against `jira/capability-model.md`.
2. **Run the principles pass.** Read `architecture-design/principles/` and check the design states, explicitly, its position on: API First, Event Driven, Schema Registry First, Secure By Default, Observable By Default, and (where relevant) MCP/A2A/Agent Ready. This is a *presence* check — does the design doc say anything about security and observability at all — not a judgment of whether the answer is good. Flag any principle the design is silent on.
3. **Look for an existing pattern.** Search `architecture-design/reference-architectures/` and `architecture-design/patterns/` for something close before designing from scratch. Recommend, don't decide — the architect picks.
4. **Scaffold the ADR.** Create it under `architecture-design/decisions/` using this shape:
   ```markdown
   # ADR-<n>: <title>

   ## Status
   Proposed | Accepted | Superseded by ADR-<n>

   ## Context
   <the problem, and the capabilities it touches>

   ## Decision
   <what was decided>

   ## Consequences
   <what this makes easier, harder, or forecloses>
   ```
   Leave Context/Decision/Consequences for the architect to fill in — don't invent content to fill the template.
5. **Once the design is fixed**, registering the finalized schema in Apicurio is mechanical — hand off to the schema-registration step in `stacks/apicurio/standards/` (or note it as the next action).

## What this skill does NOT do (stays with the architect)

- **Domain modelling and canonical/semantic data model content** — the skill can point at where these live, never author them.
- **Pattern selection** — recommend candidates, the choice is the architect's.
- **Resolving contradictions between two pieces of existing guidance** — surface the conflict, don't silently pick a side (per `CLAUDE.md`, "When in doubt").
