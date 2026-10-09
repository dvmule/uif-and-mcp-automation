# Jira Workflow — WHAT & Delivery

Jira Software is where UIF work is planned and delivered. This document defines the issue hierarchy and how it maps to the repository, so that a Story an FDE picks up is always traceable up to a Domain epic (where it ships) and labelled with the Capability standard(s) it conforms to, and down to the code it produces.

## Two parents, one rule: Domain owns the parent, Capability is a label

A delivery issue has exactly **one owning parent** — its **Domain epic** (where the work ships, e.g. `Domain: product`). **Capability is always a label** (`capability:api`, `capability:schema`, …), never a parent, because one Story often conforms to more than one capability standard at once. Platform-wide groundwork that has no domain yet (e.g. standing up the schema registry) stays under a **Capability Foundation Epic** instead. Full worked example: `../capabilities/docs/capability-domain-jira-map.md`.

```
Domain epic (delivery work — the owning parent)
   └── Story  (labels: capability:<x>, domain:<name>)
         └── Task
               └── Sub-task

Capability Foundation Epic (pre-domain platform groundwork only)
   └── Story
```

Plus two supporting types:
- **Spike** — time-boxed investigation that produces a decision, not shippable code.
- **Bug** — a defect in delivered work.

| Level | Scope | Maps to |
|---|---|---|
| Domain epic | One of the seven UIF domains | `domains/<domain>/` |
| Capability (label) | One of the thirteen UIF capabilities | `capabilities/<capability>/` |
| Story | Deliverable by one FDE in a sprint | one domain increment, e.g. `domains/<domain>/src/<capability>/` |
| Task / Sub-task | A unit of work within a Story | commits |

## The thirteen Capabilities

One Jira Capability per UIF capability: API · Event · Data · Schema · Semantics · Knowledge Graph · MCP · A2A · Agent · Infrastructure · Security · Observability · Delivery. **Architecture & Design is not one of the thirteen** — it is the frame they sit in (`../architecture-design/`) and is tracked in Jira as a program/foundation concern (`program:architecture-design`), never a `capability:*` label.

## Worked example

```
Domain     : product                          (Epic, label domain:product)
Story      : Build product developer-registration API
               parent : Domain: product
               labels : capability:api, domain:product
Task       : Implement Registration Endpoint
Sub-task   : Add request validation
Spike      : Evaluate MCP Framework
Bug        : API Authentication Failure
```

This Story produces `domains/product/src/api/` following `../architecture-design/operating-model/fde-build-deploy-workflow.md`; see `../delivery-lab/reference-implementations/api/developer-registration-api/` for a worked teaching example of the same contract.

## Delivery cadence

Sprint-based execution. The initial backlog and Sprint 0 Stories are in `../PLANNING.md`.

**Capability = WHAT.**
