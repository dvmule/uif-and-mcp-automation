# Echo

Echo is RELAY's **replicate** stack — the third of the three named throughout this
repo's design work (`echo` replicate, `beacon` export, `harbor` import). It pulls
change data out of source systems and lands it in the data lake: Oracle into
Bronze, Bronze merged into Silver, Silver joined into Gold entities.

The idea it is built on: **one generic Glue job per medallion layer, with all
pipeline-specific behaviour held in DynamoDB config.** Onboarding a new source
table is a config write through the API, not a code change — the controller
creates the Glue database and its Lake Formation grants at registration time.

> **Status: migrated, not yet accepted.** The code arrived from the separate
> `uif-relay-echo` repository under UIFPE-355, with full history preserved. That
> migration reverses **ADR-0004**, which had deliberately severed this monorepo
> from that repository, and the ADR recording the reversal (**ADR-0015**) is not
> yet written. `CHECKLIST.md` step zero is the gate; this branch should not merge
> before it closes.

## What this stack owns

| Component | Path | Shape |
|---|---|---|
| Medallion jobs | `src/apps/pipeline/` | Glue — Bronze ingest, Silver/Gold transform, reconcile, quality check, plus GCP-native variants |
| Control-plane API | `src/apps/control_panel/` | Lambda zip behind API Gateway — pipeline CRUD, validation, Oracle table analysis |
| Dispatcher + replicators | `src/apps/lambdas/` | Lambda zips — EventBridge-driven scheduling, S3→GCS and Silver→BigQuery replication |
| Registration tooling | `src/apps/cli/` | 16 durable CLI tools |
| Shared helpers | `src/libs/` | Imported by the apps, never deployed alone |
| Data contracts | `src/schemas/` | Gold master schemas and mapping manifests |
| Infrastructure | `infrastructure/terraform/` | 16 flat files, one per AWS service |
| Control panel UI | `ui/` | React 19 + TypeScript + Tailwind |
| Tests | `qe/` | One suite per app |

**Ships zero container images** — the API and executors are Lambda zips, the
transforms are Glue scripts. This is the same rule-3 deviation Beacon declared
and closed, recorded in `CHECKLIST.md`.

## Working commands

Run from the monorepo root.

| Task | Command |
|---|---|
| Tests | `py -m pytest stacks/echo/qe -q` |
| Plan (live dev) | `cd stacks/echo/infrastructure/terraform && make plan ENV=dev` |
| Plan (parallel uif-echo-dev) | `make plan ENV=dev-uif` |
| Deploy infra | `bash stacks/echo/infrastructure/deploy/uif-echo-infra-deploy.sh dev-uif` |
| Deploy apps | `bash stacks/echo/infrastructure/deploy/uif-echo-apps-deploy.sh dev-uif` |
| Deploy UI | `bash stacks/echo/infrastructure/deploy/uif-echo-ui-deploy.sh dev-uif` |
| CI (UIF dev) | `.github/workflows/deploy-uif-echo-dev.yml` |
| UI dev server | `npm --prefix stacks/echo/ui run dev` |

Deploys are manual and need active AWS credentials in the shell. Environments are
selected by the two Terraform flags — `-backend-config=backends/<env>.hcl` at
init, `-var-file=envs/<env>.tfvars` at plan and apply. Four exist: `dev`,
`staging`, `uat`, `prod`.

## Where to read next

- `CLAUDE.md` — the stack map: boundaries, layout, the naming choke points, and
  the three mistakes that cause outages rather than errors.
- `docs/artifact-name-mapping.md` — every AWS/repo artifact: existing `echo-dev-*`
  names vs proposed `uif-echo-dev-*`, plus deploy script reference.
- `docs/uif-echo-dev-rollout-plan.md` — phased rollout plan (Phases 0–9); pick
  a starting phase before execution.
- `docs/echo-backend-reference.md` — the operational detail: DynamoDB config
  schema, watermark semantics, JDBC partitioning, BigQuery merge behaviour, and
  the full gotcha list.
- `docs/echo-component-diagram.md` — how the pieces connect.
- `architecture-design/decisions/0001-gold-layer.md` and `0016-gcp-native-mode.md`
  — the two ADRs behind the Gold and GCP-native designs.
- `CHECKLIST.md` — definition of done, and every deviation from Beacon's layout
  with its reason.

## Not done here

Three things are deliberately out of scope for the migration and tracked
separately in `CHECKLIST.md`: the rename of deployed resources to
`uif-echo-{env}-*`, the export of live pipeline configuration from DynamoDB into
`domains/`, and anything GCP-hosted — GCS buckets, BigQuery projects, datasets
and tables all stay as they are, because the naming standard is AWS-only.

## Ownership

FDE: `src/`, `infrastructure/` · QE Lead: `qe/` · UX Lead: `ui/` · PM: scope and
Jira trace. **Note** — `.github/CODEOWNERS` assigns `/stacks/` to `@uif-fde` by
glob, which silently reassigns this stack away from the team ADR-0004 names as
its owner. ADR-0015 should name the accountable owner explicitly rather than let
the glob decide.
