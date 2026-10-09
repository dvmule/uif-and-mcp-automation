# Semantics

## Purpose
Shared meaning across the fabric — ontologies, vocabularies, and canonical models — so that "customer," "order," or "enrolment" mean the same thing across APIs, events, data products, and agents. Semantics turn syntactically-valid contracts into semantically-consistent ones.

## Standards
- **Semantic By Design:** contracts reference the canonical vocabulary rather than redefining terms locally.
- New terms are proposed to the canonical model, not invented per module.

## Governance
- **Central:** the canonical vocabulary/ontology and the process to extend it.
- **Local (FDE):** mapping a module's terms to the canonical model.

## Patterns
- Canonical model + per-module mappings.
- Semantic annotations on schemas in the registry (`../schema/`).

## Guidance
Foundation Epic: `Semantics Foundation` (`../../PLANNING.md`). Consumed by `../knowledge-graph/` and `../agent/`.
