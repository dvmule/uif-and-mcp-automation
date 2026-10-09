# Beacon Definition of Done

A stack is not "done" for a given Story until every applicable box is checked. This gate is what stops a new FDE from silently omitting the pieces that are easy to forget. CI enforces the starred (★) items.

> **Migrated from `domains/beacon/` to `stacks/beacon/`** (ADR-0010) — a
> deployable stack, not a schema-owning domain. This file was
> `DOMAIN-CHECKLIST.md`; renamed to match the convention `stacks/harbor/`
> already established.

> **The rule-3 deviation (container images) is CLOSED — this stack ships
> zero images.** `src/Dockerfile.api` went at UIFPE-226's probation exit
> (2026-08-28): the control-panel API runs as a Lambda zip behind API
> Gateway. `src/Dockerfile` (the PySpark batch pipeline, ~800 MB) went at
> UIFPE-215's probation exit (2026-09-02): the transform runs as a managed
> Glue job shipping a script + engine zip. The one-image rule's open
> question for serverless data-plane workloads (see
> `domains/product/CLAUDE.md`) still stands for the wider framework.

## Design (before code)
- [x] Architecture & solution design recorded in `CLAUDE.md` → `docs/beacon-export-design.md` (v0.7, decisions D1–D23).
- [x] Canonical data model / data contract defined where the domain exchanges data — the mapping/export config pair (`docs/beacon-export-design.md` §config contract; worked example in `src/schemas/examples/`).
- [x] ★ Schema registered **before** implementation (`src/schemas/`, `../../stacks/apicurio/standards/`).
- [x] Security posture stated — LF/Glue gates metadata, task-role IAM gates data; secrets via Secrets Manager refs, never stored (design doc D16); four-eyes approval on every publish.
- [ ] Observability signals defined (`infrastructure/observability/`) — runs table + Step Functions states exist; golden signals + SLOs not yet written down.

## Build
- [x] Business logic and capability surfaces under `src/` (`src/apps/beacon_export/`, `src/apps/control_panel/`, `src/libs/dynamic_mapping/`, `src/api/`, `src/schemas/`).
- [x] ★ Unit + contract tests and QE strategy in `qe/` (engine / export / control_panel suites).
- [x] UI implemented in `ui/` (`ux/` specs not yet extracted — the design pack HTML is the current source).
- [x] ★ No container images: the API ships as a Lambda zip, the transform as a Glue script + engine zip (see note above).
- [x] `infrastructure/terraform/` present and valid (no kubernetes/helm — this workload is Glue/Lambda, not EKS).

## Ship
- [ ] ★ CI green (build, test, security scan, schema validation) — root `.github/workflows/` with this stack's path filter.
- [ ] Root CD workflow promotes Development → UAT → Production for this stack.
- [x] Deployed to the sandbox account (061206535585) end to end; no managed environment yet.

## Operate
- [ ] Operational ownership registered in JSM (`../../jira/jsm-alignment.md`).
- [ ] Runbook in `infrastructure/observability/`.

## Trace
- [ ] Work links Capability → Epic → Story in Jira (`../../jira/`).
