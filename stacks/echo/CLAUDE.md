# Echo — RELAY's replicate stack

Config-driven Oracle → data-lake ingestion. One generic Glue job per medallion
layer; all pipeline-specific behaviour lives in DynamoDB config, so registering a
new table is a config write, not a code change.

> **Migrated from the separate `uif-relay-echo` repository** (UIFPE-355) — a
> deployable stack, not a schema-owning domain. See `stacks/harbor/CLAUDE.md` for
> what distinguishes the two.
>
> **This migration reverses ADR-0004 and its ADR-0015 is not yet written.** Read
> `CHECKLIST.md` step zero before treating anything here as settled.

Deep operational detail — JDBC partitioning, watermark semantics, BigQuery merge
behaviour, the full gotcha list, the DynamoDB config schema — lives in
`docs/echo-backend-reference.md`. This file is the map; that one is the terrain.

## Boundaries (what lives where)

- **This stack**: the medallion jobs (`src/apps/pipeline/`), the control-plane API
  (`src/apps/control_panel/` + `ui/`), the dispatcher and replicators
  (`src/apps/lambdas/`), the registration and analysis tools (`src/apps/cli/`),
  shared helpers (`src/libs/`), and all Echo infrastructure
  (`infrastructure/terraform/`).
- **Consuming domains**: only their pipeline **configs**, under
  `domains/<domain>/`. They never import this stack's code. Currently empty —
  configuration lives in DynamoDB and gets exported here after the rename.
- **Gold consumers**: read via the Glue catalog and Lake Formation. Nothing
  outside this stack writes Bronze, Silver or Gold.
- **Gold domains** (`domains/product`, `program`, `price`, `stock`): own their
  schemas, mappings, pipeline config and event delivery; their workflows call
  `src/apps/cli/` by path. The gold runtime is deployed by
  `infrastructure/terraform-gold/` (`deploy-echo-gold-dev.yml`, variable
  `GOLD_INFRA`) — see terraform-gold/README.md.

## Layout

```
src/apps/pipeline/         Glue ETL — bronze ingest, silver transform, reconcile,
                           quality check, GCP-native variants, and the gold runtime
                           (generic_gold_transform/reconcile + gold_cdc, ADR-0018)
src/apps/control_panel/    the always-on API (pipeline_controller, dq_check_controller)
src/apps/lambdas/          scheduler_dispatcher, gcs_replicator, silver_bq_replicator,
                           gold_dispatcher + the Transform UI's Lambdas (submit,
                           compile_preview, grain_probe, ai_draft)
src/apps/cli/              durable tools — register_*, analyze_*, adhoc_run, and the gold
                           control plane (sync_pipelines, compile_intake_delta,
                           validate_gold_schemas, validate_silver_sources, …)
src/libs/                  echo_resources, gold_resources, gold_frequencies, sizing_policy,
                           pipeline_profiles, daily_schedule
src/schemas/               Gold master schemas + mapping manifests
qe/{pipeline,control_panel,lambdas,libs,gold}/   tests, one suite per app; gold/ is the
                           runtime + control-plane suite (run with domains/product/qe)
infrastructure/terraform/  flat .tf files, one per AWS service
infrastructure/terraform-gold/  the gold layer's own root: jobs, dispatcher, Transform UI
                           Lambdas, config table, gold buckets (ADR-0018)
infrastructure/deploy/     uif-echo-{infra,apps,ui}-deploy.sh
ui/                        React 19 + TS + Tailwind, tests collocated (declared deviation)
transform-ui/              the gold authoring wizard (/m/transform), a sibling app until
                           UIFPE-97 folds it into the shell
```

**Imports are rooted by `qe/conftest.py` and nothing else** — it puts `src/apps`
and `src/libs` on `sys.path`, so each immediate child of either is a top-level
package and `import echo_resources` resolves the same in tests as at runtime.
There is no `pyproject.toml`; do not add per-file `sys.path` hacks back.

## Commands

Run from the monorepo root, as Beacon's are.

| Task | Command |
|---|---|
| Tests | `py -m pytest stacks/echo/qe -q` — **not green yet, see CHECKLIST.md** |
| Terraform plan | `cd stacks/echo/infrastructure/terraform && terraform init -backend-config=backends/dev.hcl && terraform plan -var-file=envs/dev.tfvars` |
| Format | `cd stacks/echo/infrastructure/terraform && terraform fmt` |
| Deploy infra | `bash stacks/echo/infrastructure/deploy/uif-echo-infra-deploy.sh dev` |
| Deploy apps | `bash stacks/echo/infrastructure/deploy/uif-echo-apps-deploy.sh dev` |
| Deploy UI | `bash stacks/echo/infrastructure/deploy/uif-echo-ui-deploy.sh dev` |
| UI dev | `npm --prefix stacks/echo/ui run dev` |
| Register a pipeline | `py stacks/echo/src/apps/cli/register_pipeline.py --pipeline-id X --env dev` |
| Ad-hoc run | `py stacks/echo/src/apps/cli/adhoc_run.py --pipeline-id X --env dev` |

## Architecture

Control plane: `pipeline_controller.py` is the REST API behind API Gateway —
pipeline CRUD, config validation, Glue database and Lake Formation grant creation
at registration, and Oracle table analysis. `scheduler_dispatcher.py` is
EventBridge-triggered and dispatches Bronze/Silver/Gold/Reconcile/DQ runs by
`frequency`. `sizing_policy.py` is the source of truth for cost-aware Glue worker
sizing.

Data plane, medallion: **Bronze** Oracle JDBC → S3 Parquet, full or
watermark-incremental. **Silver** Bronze Parquet → Iceberg merge with PK dedup.
**Gold** joins N Silver tables per domain using S3-hosted SQL plus YAML mapping
manifests, conformance-checked against versioned masters in `src/schemas/gold/`
(tier invariant: event ⊂ message ⊂ api). **Reconcile** detects hard deletes
nightly by anti-join against source PKs, dry-run first, with a
`max_deletes_per_run` cap. GCP-native variants exist for pipelines with
`destination.cloud = "gcp"`.

Three choke points govern every resource name. Change one and you must change all:

| File | Governs |
|---|---|
| `infrastructure/terraform/locals.tf` | every Terraform-derived name |
| `src/libs/echo_resources.py` | all Lambda and Glue runtime lookups |
| `ui/src/lib/echoPaths.ts` | all UI-side bucket and database names |

Deploy scripts read Terraform outputs (`artifacts_bucket`, `ui_bucket_name`, etc.) — no hardcoded bucket names. See `docs/artifact-name-mapping.md`.
and `infrastructure/terraform/envs/*.tfvars`, where the five lake bucket names are
literals rather than derived from `name_prefix`.

**Naming for NEW AWS resources: the UIF Resource Name Lookup is the authority**
(`architecture-design/standards/`, v2.0.0, ADR-0014). Deployed `echo-{env}-*`
names are non-compliant *by design* until the planned rename runs — do not "fix"
them opportunistically.

## Things that will bite you

The full list is in `docs/echo-backend-reference.md`. The three that cause
outages rather than errors:

- **Never rewind `silver.last_processed_date`** to force a re-merge. Bronze
  initial dumps share one `_extract_date`, so a rewind makes every MERGE
  reprocess the entire Bronze history and die at the Glue timeout, permanently.
  Rewind `extract.watermark_value` instead, or set the sentinel `1970-01-01` for
  a full LOAD+CTAS rebuild.
- **No script protects runtime-owned fields.** Every `register_*` tool does a
  full `put_item` of the config it is given — `register_drafts_preserve_settings.py`
  included, whose "preserve" means keeping `enabled`/`schedule` *from the JSON*.
  Any config file carrying a watermark is therefore a loaded gun. ADR-0004 CP-1
  built preserve-on-write for the Gold repo; the Silver path never got it.
- **Glue scripts are not uploaded by Terraform.** It tracks them with
  `filemd5()` but does not push them. After editing anything under
  `src/apps/pipeline/`, re-run the deploy script or `aws s3 cp` manually.

## Open, and not cosmetic

- **ADR-0015** — step zero, blocking merge. See `CHECKLIST.md`.
- **`src/api/openapi.yaml` does not exist** for 28 live routes.
- **The alarm in `cloudwatch.tf` has no `alarm_actions`** — it fires and notifies
  nobody. The GOLD root's alarms do (UIFPE-543, `terraform-gold/alerts.tf`):
  they invoke `uif-echo-{env}-gold-alert-notify`, which mails the
  `uif-echo-{env}-alert-subscribers` table via the Pearson Notifications API.
  The silver alarm could point at the same function once this root is applied.
- **The Oracle Instant Client cannot be rebuilt from this repo** — a licensed
  127 MB vendor download, not in git. `uif-echo-dev-oracle-layer` is
  unreproducible until that is documented.
- **Prod is pinned to `eds-prod-*`** by three `*_override` variables in
  `envs/prod.tfvars`, and its Lambda role grants `secret:echo/*` while
  `locals.tf` and `prod.tfvars` both assert `eds/*` — so as written it cannot
  read `eds/prod/ui/login`.
