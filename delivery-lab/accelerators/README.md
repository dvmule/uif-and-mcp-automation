# Accelerators

## What this is

Opinionated, larger scaffolds that assemble multiple `../templates/` into a ready-to-fill starting point for a common kind of work — for example, "an API-backed domain service with contract tests and a Helm chart" as one package rather than a dozen files gathered by hand.

## Accelerator vs template vs starter-kit

- **Template:** one file or one small unit (a Dockerfile, a GitHub Actions workflow).
- **Accelerator:** many templates composed into a coherent, opinionated whole.
- **Starter-kit:** the minimum to stand up one capability (see `../starter-kits/`).

## Governance

Central. Extract an accelerator only after the composition has proven itself in real domains.
