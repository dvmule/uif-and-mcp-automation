# Event stack Definition of Done

A stack is not "done" for a given Story until every applicable box is
checked. CI enforces the starred (★) items (`.github/workflows/stacks-ci.yml`
runs `qe/ci.sh`).

> **Split out of `domains/product`** (UIFPE-184, 2026-09-21) — the shared
> eventing platform was domain-agnostic by design and now deploys once, for
> every domain. The stack invariant is stated in [README.md](README.md):
> onboarding a new domain changes nothing here.

> **The rule-3 deviation (container images) is CLOSED — this stack ships
> zero images.** Single-file Lambdas as deterministic zips and Glue
> Python-shell scripts, all read from the release-artifacts bucket at
> content-hashed keys (docs/uif-event-uat-rollout-plan.md); declared machine-readably in
> [`src/NO-IMAGE.md`](src/NO-IMAGE.md).

## Design (before code)
- [x] Architecture recorded — the numbered flow + component contracts live
      in `docs/` (four table contracts) and
      `architecture-design/event-centric-publish-reference.md`; the trigger
      contract's authority is `src/handler.py` (named there).
- [x] Canonical data contracts defined — envelope profile pinned by
      `qe/test_event_fabric_processor.py::ENVELOPE_SCHEMA`; per-domain event
      schemas are domain property (★ registered under
      `domains/{d}/src/schemas/`, see [`src/schemas/README.md`](src/schemas/README.md)).
- [x] Security posture stated — IAM wildcarded per naming convention and
      least-actioned per component (each `.tf` states why); subscription
      queue policies are Sid-scoped per subscription; LF grants to the two
      shared roles are the documented per-domain onboarding step.
- [ ] Observability — DLQ depth alarm exists per domain (gated on the
      deploy role gaining `cloudwatch:PutMetricAlarm`); golden signals /
      SLOs not yet written down.

## Build
- [x] Business logic under `src/` (controller, publish job, provisioner,
      DLQ recorder).
- [x] ★ Unit tests + QE strategy in `qe/` (71 tests; `qe/ci.sh` is the
      gate).
- [x] ★ No container images (`src/NO-IMAGE.md`).
- [x] Infrastructure as code under `infrastructure/terraform/` (flat files
      per component; per-env tfvars; partial backends; the `event-domain`
      module is the domain-side plug).

## Ship
- [x] Deploy workflow (`deploy-event-dev.yml`: unconditional test gate;
      Terraform latched behind `EVENT_DEPLOY_ENABLED`).
- [ ] State migration executed in dev (ordered runbook:
      [MIGRATION.md](MIGRATION.md)) and the post-apply live checks re-run.
- [ ] uat/prod: backends + tfvars exist for dev only; add per environment
      (delete `imports.tf` first — dev-only).

## Operate
- [x] Runbook for the migration (MIGRATION.md); table contracts double as
      operational docs (recovery/redrive write rules).
- [x] Redrive Lambda (`uif-event-{env}-redrive`, 2026-09-21) — failed
      deliveries re-sent to the subscription's current target.
- [x] Recovery Glue job (`uif-event-{env}-fabric-recovery`, 2026-09-24) —
      PUBLISH_FAILED rows re-published byte-identical, settled on the same
      rows. The recovery plane is complete.
