# Reference Implementations

## What this is and why it matters

Production-grade, runnable examples that teach the golden path. Where `../domains/` holds real business units and `../capabilities/` holds the standards, this folder holds worked references an FDE can read end-to-end to see how a capability is done well — the canonical "good API," "good MCP server," "good event producer."

A reference implementation is held to the same bar as production code: tests, security scan, IaC, and pipeline. An example that models bad practice teaches bad practice.

## Reference implementation vs domain

- A **domain** (`../domains/<domain>/`) is a deployable business unit that ships real value.
- A **reference implementation** here is a teaching artifact — proven, minimal, and complete — that a domain team copies patterns from.

Extract a reference implementation only after a pattern has proven itself; don't pre-build one speculatively.

## Layout

```
reference-implementations/<capability>/<example-name>/
```

`<capability>` is one of the thirteen in `../capabilities/`. Each example is independently buildable and owns its full lifecycle:

```
reference-implementations/api/<example-name>/
├── src/          # implementation
├── tests/        # unit + contract tests
├── openapi.yaml  # registered contract (asyncapi.yaml for events)
├── Dockerfile
├── terraform/
└── helm/
```

## Rules

1. **No root-level `src/`.** Code lives only under a domain or a reference implementation (`../CLAUDE.md`, rule 1).
2. A contract (API/event/data schema) is **registered before** it is implemented (`../stacks/apicurio/standards/`).
3. Security and observability are designed in, not added later (`../capabilities/security/`, `../capabilities/observability/`).
4. CI must be green before merge; CD promotes Development → UAT → Production (`../capabilities/delivery/`).

## Starting one

Copy the matching starter kit from `../delivery-lab/starter-kits/`, then follow the capability blueprint (`../delivery-lab/blueprints/`) and the FDE workflow (`../architecture-design/operating-model/fde-build-deploy-workflow.md`).
