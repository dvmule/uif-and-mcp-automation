# Standards

## What this is

The single home for cross-cutting engineering standards — the ones that span capabilities and domains. Capability-specific standards stay under `../../capabilities/<capability>/`; anything broader than one capability lives here so there is exactly one place to look and no duplicate seam.

## Scope

- Naming conventions (repos, domains, services, resources).
- Versioning and compatibility rules across APIs, events, and schemas.
- Error-handling and result-shape conventions.
- Service Level Objectives and the golden signals every domain reports.
- Review gates and definition-of-done that CI enforces.

## Governance

Central. A change to a standard here is an ADR in `../decisions/`, not a local override.
