# Harbor Definition of Done

Same shape as a domain's `DOMAIN-CHECKLIST.md` — Design → Build → Ship →
Operate → Trace — adapted for a deployable stack rather than a domain. A
stack is not "done" for a given Story until every applicable box is checked.

## Design (before code)

- [ ] Architecture & solution design recorded in `CLAUDE.md` (or a design
      note linked from it) — including which domain(s) this stack's first
      real import job serves.
- [ ] Ingest template contract defined for the first real import — the
      same data-contract discipline domains use (owner, SLA, quality rules,
      classification, lineage, per `../../capabilities/data/guidelines.md`
      §2), not just a bare schema.
- [ ] Schema registered **before** implementation (`src/schemas/`,
      `../../stacks/apicurio/standards/`).
- [ ] Security posture stated — partner credential handling (secrets
      referenced, never stored, matching Beacon's D16), authN/authZ,
      threat model (`../../capabilities/security/`).
- [ ] Observability signals defined (`infrastructure/observability/`) — the
      golden signals + SLOs.

## Build

- [ ] Business logic and capability surfaces under `src/`
      (`src/apps/{pipeline,control_panel,lambdas}/`, `src/api/`,
      `src/libs/`, `src/schemas/`, opt-in `src/events/`).
- [ ] Unit + contract tests and QE strategy in `qe/`.
- [ ] UI implemented in `ui/` (if this stack's control panel needs one).
- [ ] `src/Dockerfile` (or the declared multi-image split, if this stack
      needs one — state the rationale here the way Beacon's
      `DOMAIN-CHECKLIST.md` does for its two-image deviation).
- [ ] `infrastructure/terraform/` present and valid.

## Ship

- [ ] CI green (build, test, security scan, schema validation) — root
      `.github/workflows/` with this stack's path filter.
- [ ] A CD workflow promotes Development → UAT → Production for this stack.
- [ ] Deployed to the target environment.

## Operate

- [ ] Operational ownership registered in JSM (`../../jira/jsm-alignment.md`).
- [ ] Runbook in `infrastructure/observability/`.

## Trace

- [ ] Work links Capability → Epic → Story in Jira (`../../jira/`).
