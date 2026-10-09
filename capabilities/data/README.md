# Data

## Purpose
Data products and data contracts — treating data as a governed, owned product with an explicit contract, rather than a byproduct extracted ad hoc. This capability defines how data is modelled, contracted, and made consumable.

## Standards
- **Data Centric:** every data product has an owner and a published data contract.
- Data contracts are registered before consumers depend on them (`../schema/`).
- Access to data is governed by default (`../security/`).

## Governance
- **Central:** data contract standard, product ownership model, classification/handling rules.
- **Local (FDE):** the internal model of a specific data product.

## Patterns
- Data-as-a-product with a versioned contract.
- Change data capture and event-driven propagation over batch extraction where feasible (`../event/`).
- Outbound partner exports as governed, versioned configs — never ad-hoc extraction ([beacon.md](beacon.md)).
- One shared Silver→Gold control plane, stewarded by the domain that built it and consumed by every gold domain at sanctioned paths ([gold-control-plane.md](gold-control-plane.md)).

## Guidance
Design data contracts during step 8 of `../../architecture-design/operating-model/fde-build-deploy-workflow.md`. Semantics and Knowledge Graph consume from here (`../semantics/`, `../knowledge-graph/`).
