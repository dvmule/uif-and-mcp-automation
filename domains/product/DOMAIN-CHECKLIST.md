# Domain Definition of Done

A domain is not "done" for a given Story until every applicable box is checked. This gate is what stops a new FDE from silently omitting the pieces that are easy to forget. CI enforces the starred (★) items.

## Design (before code)
- [ ] Architecture & solution design recorded in `CLAUDE.md` (or a design note linked from it).
- [ ] Canonical data model / data contract defined where the domain exchanges data.
- [ ] ★ Schema registered **before** implementation (`src/schemas/`, `../../stacks/apicurio/standards/`).
- [ ] Security posture stated — threat model, authN/authZ, secrets handling (`../../capabilities/security/`).
- [ ] Observability signals defined (`infrastructure/observability/`) — the golden signals + SLOs.

## Build
- [ ] Business logic and capability surfaces under `src/` (`src/apps/`, `src/api/`, `src/schemas/`, opt-in `src/events/`, …).
- [ ] ★ Unit + contract tests and QE strategy in `qe/`.
- [ ] UI implemented in `ui/` against specs in `ux/` (if the domain has a UI).
- [ ] ★ `src/Dockerfile` builds the one container image for the domain.
- [ ] `infrastructure/terraform/`, `infrastructure/kubernetes/`, `infrastructure/helm/` present and valid.

## Ship
- [ ] ★ CI green (build, test, security scan, schema validation) — root `.github/workflows/` with this domain's path filter.
- [ ] Root `.github/workflows/domain-cd.yml` promotes Development → UAT → Production for this domain.
- [ ] Deployed to the target environment.

## Operate
- [ ] Operational ownership registered in JSM (`../../jira/jsm-alignment.md`).
- [ ] Runbook in `infrastructure/observability/`.

## Trace
- [ ] Work links Capability → Epic → Story in Jira (`../../jira/`).
