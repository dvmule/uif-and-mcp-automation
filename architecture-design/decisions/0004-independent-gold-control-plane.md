> Migrated from uif-dal-gold@cba9af0 (2026-07-31). Numbering inherited from the source repo (0002 was never used).
> **Naming note (2026-08-09, UIFPE-103):** resource names written here as `eds-{env}-*` / `eds_{env}_*` are historical — the platform convention is now `echo-{env}-*` / `echo_{env}_*`, and the environment set is dev/uat/prod (uat replaced staging). Names below are preserved as written at decision time.

# ADR-0004 — Independent Gold Control Plane on Shared EDS Infrastructure

**Status:** Draft (for review)
**Date:** 2026-07-21
**Deciders:** reyasahamed.habibrahman@pearson.com
**Scope:** How the Gold module operates independently of the platform repo (uif-relay-echo) after the repo split — control plane, scheduling, resource ownership, naming.
**Supersedes:** none. Builds on ADR-0001 (gold foundations) and ADR-0003 (incremental CDC).

---

## Context

The UIF platform split into two repos: **uif-relay-echo** (UI + Bronze/Silver,
owned by another team) and **uif-dal-gold** (this repo — Silver→Gold, our
scope). Team decision: the Gold module must run **end-to-end without depending
on the other module**, while following the **same infra design approach** —
repo and logic separation, not infrastructure separation. relay-echo's
greenfield stack uses `eds-{env}-*` naming (Terraform `name_prefix`,
`eds_resources.py` helper) with isolated state per stack.

Dependencies to sever (all previously served by relay-echo): the
`/gold-pipelines` API routes in its controller Lambda, its scheduler
dispatcher + EventBridge gold rules, and Terraform ownership of the gold
Glue jobs / config table / buckets.

## Decisions

| # | Decision | Choice |
|---|---|---|
| CP-1 | Control plane | **Config-as-code first** (Git YAML → validate → DynamoDB); API Lambda deferred until a gold UI exists |
| CP-2 | Scheduling | **Own `gold_dispatcher` Lambda + EventBridge rules**; fixes the upstream `gold-hourly` routing bug |
| CP-3 | IAM | **Reuse the shared `eds-{env}-glue-role`** for Glue jobs (team decision: shared infra primitives); dispatcher gets its own minimal role |
| CP-4 | Scripts bucket | **Shared `eds-{env}-glue-scripts`, distinct `gold/glue_jobs/` prefix** — outside the platform deploy's `s3 sync --delete` blast radius on `glue_jobs/` |
| CP-5 | Resource ownership | Gold Glue jobs, gold config table, gold buckets move to THIS repo's state via **state-rm/import cutover, keeping existing `eds-{env}-*` names** |
| CP-6 | Naming & state | Full EDS naming parity (`eds_resources.py` ported verbatim; same tags/name_prefix); isolated state key `eds-gold/{env}/` in the shared tfstate bucket |

### CP-1 — Config-as-code control plane

Gold pipeline definitions live in Git: `pipelines/base/{gold_id}.yaml`
(env-agnostic) deep-merged with `pipelines/overlays/{env}/{gold_id}.yaml`
(env-specific). `scripts/sync_pipelines.py` validates (`scripts/gold_config.py`
— a superset of the legacy API's server-side rules plus the ADR-0003 fields),
auto-initializes defaults, and writes to DynamoDB. Properties:

- **Plan by default, `--apply` to write** (repo rule: state changes need an explicit flag).
- **Runtime-owned fields are never clobbered**: watermarks, channel registry,
  materialized_properties, publish_cursors, reconcile stats, and run metadata
  are preserved from the existing item on every sync. The transform owns them.
- **Unmanaged items are reported, never deleted.**
- `scripts/register_gold_pipeline.py` remains for ad-hoc/dev registration —
  now writing DynamoDB directly through the same validation, no HTTP.

**Rejected:** building our own API Lambda now (no UI consumer; API Gateway/
auth surface for nothing) — revisit when a gold UI materializes; nothing in
config-as-code blocks adding it (option "both, config-as-code first").

### CP-2 — Own dispatcher

`lambda/gold_dispatcher.py` (EventBridge → Lambda → Glue `StartJobRun`):
scans enabled configs, matches `schedule.frequency` (`gold` daily 06:30 /
`gold-hourly` :45 / reconcile 03:30 — same cadences as the platform),
in-flight guard on `--gold_id` (fail-open), worker sizing from `cfg.glue`.

**Deliberate fix vs upstream:** relay-echo's handler routes only
`("gold", "gold-reconcile")` into its gold branch, while its hourly rule
emits `"gold-hourly"` — hourly gold configs were silently never dispatched.
Our handler routes all three frequencies.

> **Superseded in part — [UIFPE-149](https://pearson.atlassian.net/browse/UIFPE-149),
> 2026-08-11.** Scheduling moved OUT of the EventBridge rules and INTO the
> pipeline configs. The decision this section records — own the dispatcher rather
> than depend on the platform's — is unchanged, but three details above are not:
>
> * **Cadence is no longer a property of a rule.** The two per-cadence rules were
>   replaced by ONE 5-minute sweep (`gold-tick`) plus the unchanged nightly
>   reconcile. Each pipeline declares `schedule.frequency` (eleven rungs, 5m→24h)
>   and `schedule.start_time` (a UTC time of day), and the dispatcher works out
>   which pipelines are due. The reason: with one rule per cadence, every pipeline
>   on a cadence shared one clock — two daily tables could not run at different
>   times. Vocabulary and slot arithmetic are defined once in
>   `domains/product/src/apps/scripts/gold_frequencies.py`.
> * **`gold` and `gold-hourly` are aliases now** (→ `gold-24h`, `gold-1h`), and
>   the per-cadence default start times preserve the identical clocks, so a
>   deployed config keeps dispatching at the same time across the deploy-order
>   window.
> * **The in-flight guard's fail-OPEN posture is inverted.** It now fails CLOSED
>   for every cadence, which slot-based due-ness makes safe: a refused sweep is
>   retried five minutes later against the same slot, so nothing is lost, whereas
>   dispatching blind still risks two writers on one table. See
>   `domains/product/CLAUDE.md`.

**Rule naming:** `eds-{env}-gold-dispatch-{hourly,daily,reconcile}` —
deliberately different from the platform's rule names so both stacks can
coexist during transition. The platform's gold rules must be disabled before
ours are enabled (cutover step), or both dispatchers fire the same jobs.

### CP-3 / CP-4 — Shared primitives

The Glue jobs run as the shared `eds-{env}-glue-role` (data-source lookup by
name; already holds Silver read + Gold bucket + gold config table grants —
no new Lake Formation work). Scripts ship to the shared scripts bucket under
`gold/glue_jobs/` (+ `gold/tmp/` for Glue temp), which the platform deploy's
`aws s3 sync glue_jobs/ --delete` cannot touch.

### CP-5 — Ownership cutover (runbook)

Order matters; do per environment:

1. Platform team disables their three gold EventBridge rules and removes
   gold resources from their Terraform **state**
   (`terraform state rm` on: both gold Glue jobs, both gold script S3
   objects, gold_pipelines_config table, gold + gold-artifacts buckets, the
   gold EventBridge rules/targets) and deletes the gold code/TF from their repo.
2. This repo: `make init ENV=dev`, then `terraform import` the pre-existing
   resources (jobs, table, buckets — names unchanged), including the gold
   buckets:
   `terraform import aws_s3_bucket.gold eds-{env}-gold` and
   `terraform import aws_s3_bucket.gold_artifacts eds-{env}-gold-artifacts`
   (see `infra/s3.tf`); then `make plan`
   (should show only: script objects at the new `gold/` prefix, dispatcher
   Lambda/role/rules, in-place job updates for script_location) and `apply`.
3. Sync configs: `python3 scripts/sync_pipelines.py --env dev --apply`.
4. Verify: invoke `eds-dev-gold-dispatcher` with `{"frequency":"gold-tick"}`
   (`"gold"` still works — it normalizes to the same rung),
   confirm a transform run succeeds end-to-end; then delete the legacy
   script objects under `glue_jobs/generic_gold_*.py`.

Until step 1 completes, `terraform apply` here MUST NOT run.

## Consequences

- Gold ships on its own cadence: our CI deploys scripts/infra/configs with
  no coordination beyond the one-time cutover.
- The only remaining touchpoint is reading Silver Iceberg via the Glue
  catalog — the layer data contract.
- The shared role and buckets remain platform-owned; changes to them (e.g.
  role policy tightening) are a cross-team communication item.
- Metrics rows go to the shared `eds-{env}-glue-job-metrics` table (their
  UI may still display gold runs; harmless either way).

## Revisit triggers

| Trigger | Decision |
|---|---|
| A gold management UI is greenlit | CP-1 (add the API Lambda) |
| Platform tightens/renames the shared glue role | CP-3 (own role becomes simpler than coordination) |
| Platform deploy sync scope changes beyond `glue_jobs/` | CP-4 (move to own bucket) |
| Gold needs cadences the platform doesn't have | CP-2 (freely — rules are ours now) |
