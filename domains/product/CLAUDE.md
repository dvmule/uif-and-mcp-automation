# product — domain skill

Context for engineers and AI assistants working in the `product` domain.

## What this domain owns

The Product entity's gold contracts and configuration: master schema, intake,
mapping manifest + SQL, pipeline config, event delivery, and two serving
surfaces (the read API and the marketing-copy serving copy). The runtime that
materialises them — transform and reconcile jobs, `gold_cdc`, the dispatcher,
the control-plane CLIs and the Transform UI — is Echo's since UIFPE-358 phase 2
([ADR-0018](../../architecture-design/decisions/0018-gold-runtime-is-a-stack-concern.md)):
`stacks/echo/src/apps/{pipeline,lambdas,cli}`, `stacks/echo/src/libs`,
`stacks/echo/transform-ui`, tests in `stacks/echo/qe/gold`. Their Terraform is
`stacks/echo/infrastructure/terraform-gold`; this domain's root holds only
event delivery, the marketing-copy service and the product API.

Architecture:
[ADR-0001](../../architecture-design/decisions/0001-gold-layer.md)
(foundations),
[ADR-0003](../../architecture-design/decisions/0003-gold-incremental-cdc.md)
(incremental CDC — read before touching the transform),
[ADR-0004](../../architecture-design/decisions/0004-independent-gold-control-plane.md)
(repo independence, control plane, cutover runbook),
[ADR-0018](../../architecture-design/decisions/0018-gold-runtime-is-a-stack-concern.md)
(the runtime is Echo's; contracts, config and event delivery are the domain's —
supersedes ADR-0009). Module
design: [LLD — Silver to Gold](docs/lld-silver-to-gold-module.md)
(provisional location; the domain root is contractually 3 files).

See `README.md` for the full component map and the list of provisional
placements and open decisions. `DOMAIN-CHECKLIST.md` is the definition of done.

## Boundaries (what lives where)

- **This domain**: the Product entity's schemas and intake (`src/schemas/`),
  mapping manifest + SQL, pipeline config (`src/apps/pipelines/`), event
  delivery (`src/events/`, `product_events*.tf`), the marketing-copy serving
  copy and the product API. This root's state (key `echo-gold/{env}/`) holds
  only those; the gold runtime's Terraform is
  `stacks/echo/infrastructure/terraform-gold`.
- **Echo (`stacks/echo`) — the pipeline for all three layers**: the gold
  transform/reconcile jobs and `gold_cdc`, the gold dispatcher, the control
  plane (`src/apps/cli/`: sync, compiler, validators, artifact builders), the
  Transform UI and its Lambdas. The steward model (UIFPE-122) is closed: no
  domain hosts runtime for another. Other domains' workflows call the CLIs at
  `stacks/echo/src/apps/cli/`; a runtime change fires `deploy-echo-gold-dev.yml`,
  not this domain's deploy.
- **No runtime dependency on their code or API** (ADR-0004). The only
  touchpoint is reading Silver Iceberg tables via the Glue catalog.

## How it builds & deploys

- CI runs at repo root (`../../.github/workflows/domain-ci.yml`) on
  `domains/product/**`, plus the ported `product-ci.yml` and
  `product-gold-schemas.yml` (also repo-root, same path scoping).
- **Deploy is ported but latched off.** `deploy-product-{dev,uat,prod}.yml`
  at the repo root are faithful ports of the source repo's five workflows,
  scoped via `domains/product/**` path filters. They're gated by the
  `PRODUCT_DEPLOY_ENABLED` repo variable — unset by default, so deploys are
  skipped and merging is safe. Before enabling: add the
  **Dev is live** (2026-08-11): `PRODUCT_DEPLOY_ENABLED` is set,
  `AWS_ROLE_ARN_DEV` is configured, the role trusts this repo's
  **ID-QUALIFIED** OIDC subjects (GHEC issues
  `repo:pearson-dnt-coe@183024320/universal-Integration-framework@1308482856:environment:dev`
  — the unqualified form never matches), the §CP-5 cutover completed in dev
  2026-08-04, and merges to `develop` deploy green. **UAT/prod stay latched**
  pending ID-qualified trust on their roles and the §CP-5 cutover for those
  envs. Two of those blockers cleared for **uat** on 2026-09-28: it has a real
  backend (`uif-echo-uat-data`) and an `overlays/uat` set, so
  `sync_pipelines.py --env uat` now validates. **prod** still has a placeholder
  backend and no overlay.
  The Jenkins CD path for this Glue/Lambda workload is still undefined and
  separate from this latch.
- This workload is not containerized. `src/Dockerfile` does not build or ship
  the Glue jobs or the Lambda; reconciling that with the one-container rule
  needs an ADR. **Open question, now shared** — the `program` domain
  (2026-08-11) is the same Glue+Lambda shape and inherits it: `domain-ci.yml`
  makes `src/Dockerfile` an unconditional gate, so program ships the
  comment-only token stub the mould emits (as `legal`/`hr` do) purely to pass
  the gate. Two domains now carry a file that builds nothing; the ADR should
  decide whether the rule is relaxed for serverless data-plane workloads or
  the gate becomes conditional.

## Common workflows

Run from the monorepo root. `py` is the Windows launcher; `python3` elsewhere.

| Task | Command |
|------|---------|
| **Test** | `py -m pytest domains/product/qe stacks/echo/qe/gold -v` — the domain's artifacts and the runtime that serves them. CI gates this domain on the first root only; the gold suite gates `deploy-echo-gold-dev.yml` |
| **Sync pipeline configs (plan)** | `py stacks/echo/src/apps/cli/sync_pipelines.py --env dev` |
| **Sync pipeline configs (apply)** | `py stacks/echo/src/apps/cli/sync_pipelines.py --env dev --apply` — the dev workflow already does this on merge; run it by hand only for an out-of-band config change or for uat/prod |
| **Generate tier subsets locally** | `py stacks/echo/src/apps/cli/generate_tier_subsets.py --master domains/product/src/schemas/gold/product/v1/master.json --entity product --version v1 --out-dir /tmp/subsets` |
| **Build mapping artifact** | `py stacks/echo/src/apps/cli/build_mapping_artifact.py --manifest domains/product/src/schemas/mapping/product/v1/manifest.yaml --entity product --version v1 --out /tmp/mapping.json` |
| **Trigger Gold transform** | `aws lambda invoke --function-name uif-echo-dev-gold-dispatch --payload $(echo -n '{"frequency":"gold-tick"}' \| base64) /tmp/r.json` — the payload must match the pipeline's `schedule.frequency`; see the cadence list under Gotchas |
| **Refresh columns after a logic change** | `aws glue start-job-run --job-name uif-echo-dev-gold-transform --arguments '{"--gold_id":"product","--refresh_columns":"colA,colB"}'` — recomputes ONLY those columns for all rows; the hashes are computed over the COMPOSITE row (fresh named columns + Gold's stored values for the rest, UIFPE-269), so pending Silver deltas on other columns are safe and simply land on the next incremental — the old "refuses while any source has pending deltas" guard is gone. op=REFRESH change-log rows per moved channel (`--refresh_mode silent` to skip). Deploy the new mapping SQL to S3 before running. |
| **Rebuild the table** | `aws glue start-job-run --job-name uif-echo-dev-gold-transform --arguments '{"--gold_id":"product","--rebuild":"true"}' --worker-type G.2X --number-of-workers 8 --timeout 180` — atomic CREATE OR REPLACE, no change-log rows (consumers resync). Needed whenever the manifest's `null_sentinels` set changes, `output_columns` order changes, or a nested type changes non-additively. Sequence: merge away from 06:30/03:30 UTC, wait for the dev deploy to finish INCLUDING the config sync, start the rebuild at once — before the next scheduled incremental, which would otherwise auto-DDL + backfill and flood the change log with every row's changed hash. Never freeze with `enabled: false` for this: it makes the transform skip the manual rebuild too. |
| **Upload Glue scripts (dev)** | not a step here any more: the gold root's apply ships them (`aws_s3_object` in `stacks/echo/infrastructure/terraform-gold/glue_jobs.tf`). By hand, only for a hotfix: `aws s3 cp stacks/echo/src/apps/pipeline/ s3://uif-echo-dev-data/code/gold/ --recursive --exclude "*" --include "*.py"` |
| **Infra (plan — do this before merging)** | `cd domains/product/infrastructure/terraform && make init ENV=dev && make plan ENV=dev` — **reviewing the plan is the only gate**: merging to `develop` applies it unattended. Don't `make apply` in dev by hand; the workflow owns that and a manual apply races it. UAT/prod are still apply-by-hand and not before the ADR-0004 cutover. |

## Key invariants (ADR-0003)

- **Every manifest source declares `key_mapping`** (changed row → Gold PK
  cols; subsets fan out). The transform refuses to run without it. **One
  exception, added 2026-08-31:** a source may declare `lookup_only: true`
  *instead* — for a pure dimension that carries NO Gold PK column, where no
  expression over a changed row could name the Gold rows it affects
  (`MTL_CATEGORIES_B` has neither an item nor an org). Such a source is
  registered as a FULL temp view for the mapping SQL and skipped by Phase 1, so
  **an edit confined to it rebuilds nothing** — values denormalized from it
  move on the next `--refresh_columns` run. Declaring both `lookup_only` and
  `key_mapping` is refused: the tempting
  `{organizationId: "CAST(NULL AS BIGINT)"}` passes the PK check, advances the
  watermark and detects nothing.
- **Hashes are schema-derived**: `_channel_hashes` map computed from
  `x-channels` tags; reserved channel `canonical` (= all properties) gates
  the MERGE. Never tag `canonical` explicitly.
- **Run ordering is fixed**: change-log append → MERGE → per-source
  watermark advance. Consumers dedupe on `(pk, run_id)`.
- **Quiet sources skip Phase 1 by snapshot (UIFPE-256)**: a source whose
  Iceberg `metadata_location` equals the one stored by the last successful
  run has had zero commits, so its delta scan is skipped — this is what stops
  a bulk load stuck inside the 15-minute lag window from re-scanning forever.
  Any doubt (first run, capture failure, legacy config) fails open to a scan.
- **Maintenance is silent by default**: new channels/attributes backfill
  without moving `_gold_updated_at` or writing log rows (modes: silent|emit).
  Since UIFPE-253 the backfill MERGE runs **after** the incremental phases
  (DDL still runs first): recomputing hashes before consuming pending deltas
  poisoned the hash gate and silently dropped them. A crash between the
  incremental and the deferred backfill is repaired with `--refresh_columns`.
- **Struct widening auto-DDLs (UIFPE-157)**: additive nested fields on an
  existing `STRUCT` / `ARRAY<STRUCT>` column evolve via nested
  `ADD COLUMN parent.child` in the same maintenance pass and backfill like new
  columns (silent or emit). A nested type CHANGE or field REMOVAL refuses the
  run before any write and requires a manual `--rebuild`. The manifest's
  `column_types` block is the declared authority the live table schema is
  diffed against — `--refresh_columns` is NOT a remediation for a struct
  mismatch (it writes the same shape and fails identically).
- **ARRAY properties need `array_sort_keys`** in the manifest (stable hashes).
- **Null sentinels are declared, not hand-coded (UIFPE-268)**: a manifest
  `null_sentinels` block (`string: [TBD, N/A]`, `numeric: [99998, 99999]`)
  makes the runtime re-project EVERY Silver view it registers so those values
  read as NULL — scalars, conditionals, nested arrays and struct members all
  see clean data with no change to the mapping SQL. Never touched: the
  columns named in a source's `key_mapping` / `sot_pk_mapping` / `sot_filter`
  (so string keys like price's `PPID` stay raw), the watermark column, names
  ending `_ID` / `_NUM`, Silver metadata, and `exclude_columns` (bare, or
  `alias.COLUMN` for one source). Adding or changing the block moves
  canonical hashes on most rows: plan a `--rebuild`, not a silent deploy — a
  partial refresh cleans only the named columns (hashes stay honest since the
  composite-row change, but the rest stays dirty until a row changes or the
  table is rebuilt).
- **Product's row domain is two predicates, in two places (UIFPE-271)**:
  `SEGMENT1 LIKE 'A%'` AND `INVENTORY_ITEM_STATUS_CODE NOT IN ('DRAFT',
  'ERROR', 'DELETE', 'RETIRED', 'TEMPLATE')`, verbatim in product.sql's WHERE and the
  manifest `sot_filter` (a test pins them equal). A status moving INTO the set
  is removed by reconcile, not the transform, and product's reconcile was off
  from the first load until 2026-09-29 — so those rows accumulated. **Measured
  then: 269,479 orphans, 12.4% of the table, and 267,601 of them RETIRED.**
  That is the predicted consequence, at its full size.
  Reconcile is now `enabled: true` with `dry_run: true` and the cap raised to
  300,000, which is step one of three: observe the count from the job itself,
  then a supervised delete, then the cap back to 5,000. It cannot delete until
  `dry_run` flips. **The backlog blocks the product API** — 494 pairs collide
  on `(productId, organizationCode)`, the key the API must address rows by, and
  every collision involves an orphan; among live rows the pair is unique.
  The committed intake carries no `drivingFilter`: the compiler refuses that
  field on a shipped entity by design.
- **BOM arrays read master-org primary bills (UIFPE-269)**: bills on the
  master org only (99.99% in dev), `ALTERNATE_BOM_DESIGNATOR IS NULL`,
  structure types by name, UDA groups by id (1184 / 1185 — `EGO_ATTR_GROUPS_V`
  is not in Silver). Component / UDA / structure-type sources are
  `lookup_only`; the `schedule.recompute` leg is what moves a component-only
  edit — it rides the 06:30 run itself, once every `every_n_runs` runs.
- **Metadata columns stay last (UIFPE-268)**: the maintenance auto-DDL adds a
  new business column AFTER the last business column, so `_channel_hashes`,
  `_gold_updated_at`, `_gold_run_id` remain the final three however many
  columns are added after the first load. A table that already has a column
  behind them (product's `categories`, 2026-08-31) is put right by the next
  `--rebuild`; the maintenance path never moves existing columns.
- **Nested fields can carry their own `x-channels`** (inside array items /
  object properties): untagged fields inherit the parent; tagged fields must
  be a SUBSET of the parent's channels (`validate_nested_channels`). Channel
  hashes are computed over the PROJECTED element — a hidden-field change
  never pings that channel — and tier subsets filter nested fields recursively.
- **`--rebuild` writes no change-log rows** — consumers resync via full load.

## Gotchas

- **Scripts MUST live under the `gold/` prefix** in the shared scripts
  bucket — the platform deploy runs `s3 sync glue_jobs/ --delete` and will
  purge anything of ours placed under `glue_jobs/`. Blast radius is theirs,
  not ours.
- **Logical names are camelCase, physical columns are snake_case** (Schema
  Standards N7/N8, UIFPE-437, 2026-09-11 — supersedes the 2026-07-22 camelCase
  column decision recorded in ADR-0005). master.json, the intake, the mapping
  SQL aliases, events and the API all carry `inventoryItemId`; the Iceberg
  column is `inventory_item_id`, struct members included. The runtime generates
  the physical name (`gold_cdc.physical_name`, `to_physical()` at the write
  boundary) and renames a table written before the rule with metadata-only
  `ALTER TABLE … RENAME COLUMN` on its next run — no rebuild. Anything that
  reads the table by column name (Athena SQL, the event processor's
  `--join_key`, beacon export sources, the marketing-copy sync) names the
  physical column.
- **Nested member names are part of the channel hash.** Top-level hash keys
  are logical, but a struct or array column a channel sees in full goes into
  `to_json` whole, so its member names — physical, since the rename — are
  hashed too. The stage-2 rename therefore moved every hash on a table with
  nested columns, once (2026-09-12): product's scheduled emit refresh wrote a
  REFRESH row for all 2.14M products (purged); program and price were
  re-baselined with `--refresh_mode silent` first. Rule for next time: after
  any change to a nested column's physical name, run a silent refresh of that
  column BEFORE the next scheduled run, or expect a change-log flood. This is
  ADR-0020's operational rule, and the recompute leg makes it sharper — the leg
  rides an ordinary run and announces, so there is no maintenance-shaped run to
  hide the flood in.
- **Gold stores carry the UIF standard names**: database
  `uif_echo_{env}_gold_{domain}`, data at `s3://uif-echo-{env}-gold/{entity}/`,
  artifacts in `uif-echo-{env}-gold-artifacts`. The transform creates a missing
  database itself; the config sync tags it `uif-echo-gold-layer={env}`, and
  every consumer grant (marketing-copy sync, processor, grain probe, Beacon,
  the shared Glue role itself in `gold_executor_access.tf`) is tag-based, so a
  new database needs no grant work beyond one sync.
- **`sync_pipelines.py` never touches runtime fields** (watermarks, channel
  registry, cursors) and never deletes unmanaged items — deletion is manual.
- **Scheduling is PER-PIPELINE config-as-code since UIFPE-149** (2026-08-11),
  not a property of the EventBridge rules. Each pipeline declares both halves in
  `pipelines/base/<domain>.yaml`, and the Transform UI writes them — no manual
  step and no Terraform change to move a table's schedule:
  - `schedule.frequency` — how OFTEN. Eleven rungs: `gold-5m` · `gold-10m` ·
    `gold-15m` · `gold-30m` · `gold-1h` · `gold-2h` · `gold-4h` · `gold-6h` ·
    `gold-8h` · `gold-12h` · `gold-24h`.
  - `schedule.start_time` — from WHEN, as `"HH:MM"` UTC. **Quote it**: YAML 1.1
    parses an unquoted `6:30` as the integer 390.
  - Two daily tables can therefore run at different times. That was impossible
    before, when each cadence was one shared rule.
  - **One definition:** `stacks/echo/src/libs/gold_frequencies.py` — intervals,
    default start times, labels and the slot arithmetic.
    `qe/test_gold_frequency_parity.py` fails CI if Terraform or the TypeScript
    copy drifts, and `qe/fixtures/schedule-slots.json` is a shared vector both
    language implementations assert. Add a rung THERE first.
- **Only TWO EventBridge rules:** `…-gold-dispatch-tick` (every 5 minutes) and
  `…-gold-dispatch-reconcile` (03:30, a different Glue job, not laddered). The
  sweep must tick at least as often as the finest rung.
- **Slots, not elapsed time.** A pipeline's slots are its start time plus
  multiples of its interval; it is due when its last scheduled run started
  before the most recent slot. Every interval divides 1440 minutes, so the slots
  tile the day with no short gap at midnight. Consequences worth knowing:
  - a **skipped sweep loses nothing** — the next one computes the same slot and
    dispatches, just late;
  - the **phase never drifts**, because it comes from the start time rather than
    from the previous run's finish;
  - a job that **overruns its interval** builds no backlog; it misses the slots
    it was still running through.
  - `start_time` **phases the grid, it does not delay the first run** — 14:45 on
    `gold-4h` means 02:45/06:45/…/22:45.
- **The old cadence names still work.** `gold` → `gold-24h`, `gold-hourly` /
  `gold-60m` → `gold-1h`, normalized on both sides, and the default start times
  preserve the historical clocks (06:30 daily, :45 hourly). That is what stops
  the deploy-order window (Terraform at step 2, config sync at step 4) from
  silently skipping product's live 06:30 run. The alias table can go once EVERY
  env has been re-synced — not before.
- **The in-flight guard fails CLOSED, for every cadence.** The dispatcher
  refuses to start a run for a `gold_id` that already has one unfinished
  (`STARTING`/`RUNNING`/`STOPPING`/`WAITING`), across BOTH job names — so a
  40-minute job on a 5-minute cadence just skips slots, and a manual
  `start-job-run` blocks the scheduled one too (it reads `--gold_id` off the run
  arguments). When the guard cannot be VERIFIED (Glue throttling, IAM), nothing
  dispatches; alarm on `GOLD_DISPATCH_GUARD_FAIL_CLOSED`. This inverted the old
  fail-OPEN posture, and it is only safe BECAUSE of slot-based due-ness: a
  refused sweep costs minutes of lateness instead of a whole run.
- **Maintenance runs block but do not satisfy.** A `--refresh_columns` or
  `--rebuild` run blocks the guard (two writers is two writers) but never counts
  as a schedule slot's run — otherwise a 02:00 column repair would silently
  cancel that day's 06:30 incremental.
- **The recompute leg never rides a first load.** A first load computes every
  column from scratch, so a leg inside it is pure waste. The sharper reason it
  is worth stating: the retired refresh sweep read only the maintenance
  history, so a never-run pipeline that shipped a refresh block looked
  "refresh overdue" and that sweep dispatched BEFORE the incremental — the
  transform refused (no Gold table) and the cooldown replayed it (price
  arming, 2026-09-06). With one sweep (ADR-0020 decision 4) there is no second
  dispatcher left to race the first load; the run goes ahead and the leg simply
  does not ride it.
- **Failure cooldown** (`schedule.failure_cooldown_minutes`, default 30, `0`
  disables): after a `FAILED`/`TIMEOUT`/`ERROR`/`STOPPED` run the next sweep is
  held off. The guard blocks unfinished runs, never failed ones, so without this
  a broken pipeline on `gold-5m` starts 288 runs/day (×2, `max_retries=1`) at
  8–16 DPU. It EXPIRES — a transient failure still self-heals. A manual stop
  also starts it; to override, run `start-job-run` yourself (that path does not
  go through the dispatcher).
- **KNOWN LIMITATION — duplicate delivery can still stack a run.** The guard is
  check-then-act with no lock and EventBridge is at-least-once, so two
  deliveries of one sweep can both see "nothing in flight". The window is
  seconds. Damage is bounded: the MERGE is hash-gated and idempotent, so the
  cost is a doubled change-log append and a raced watermark advance, and
  consumers dedupe on `(pk, run_id)`. The fix would be a conditional-write
  lease in the config table, which would make the dispatcher a config WRITER —
  it is deliberately read-only (`Scan` + `GetItem`, no `UpdateItem` in its IAM
  policy). Not taken; revisit if it is ever actually observed.
- **Dual-import fallback**: `gold_cdc.py` ships via `--extra-py-files`; jobs
  import it with a try/except fallback (`import gold_cdc` on Glue,
  `from glue_jobs import gold_cdc` in tests). Keep it free of Spark/boto3
  imports — it must stay unit-testable.
- **The Glue scripts ship with the gold root's Terraform, not with this
  domain's deploy.** `stacks/echo/infrastructure/terraform-gold/glue_jobs.tf`
  declares `generic_gold_transform.py`, `generic_gold_reconcile.py` and
  `gold_cdc.py` as `aws_s3_object` with `etag = filemd5(...)` under the
  `gold/glue_jobs/` prefix, and `deploy-echo-gold-dev.yml` is their only
  writer since ADR-0018 step 2 (this domain's `s3 sync` step went with it).
  Consequence worth knowing: runtime code and this domain's artifacts now
  deploy from two workflows, so a runtime change that also needs a manifest,
  schema or config change is two merges — ship the backwards-compatible half
  first.
- **Infra cutover DONE in dev (2026-08-04, ADR-0004 §CP-5)**: gold resources
  are state-moved into `echo-gold/dev` and `terraform apply` is the legitimate
  dispatcher/IAM deploy path there. UAT/prod cutovers are still pending —
  the do-not-apply rule stands for those envs. Watch for the platform-era
  `echo-{env}-gold-{transform,reconcile}-*` EventBridge rules being re-enabled
  by platform deploys (observed 2026-08-07 in dev) — re-disable them.
- **DEV DEPLOYS ITSELF ON MERGE — the sequence below is not something you run.**
  Corrected 2026-08-12 (UIFPE-149): this entry used to read as a manual runbook,
  and following it in dev would apply Terraform a second time. `deploy-product-dev.yml`
  triggers on `push: branches: [develop]` and, with `PRODUCT_DEPLOY_ENABLED=true`
  (set for dev), performs the whole sequence unattended:

  test gate (`pytest domains/product/qe`) → OIDC → `make init/plan/apply`
  (event delivery, marketing-copy, product API) → `aws s3 sync` schema +
  mapping artifacts per entity → `sync_pipelines.py --env dev --apply`. The
  Glue scripts, the dispatcher and the Transform UI ship from
  `deploy-echo-gold-dev.yml` (ADR-0018 step 2).

  That IS the mandatory order, and the reason it is mandatory has not changed
  (version skew: an old dispatcher feeds the lean `glue` block to whatever runs
  next, and a config naming a cadence the deployed dispatcher does not know is
  skipped silently). Two consequences of it being automatic:
  - **`terraform apply` runs with no human reviewing the plan.** A change that
    replaces or destroys infrastructure — an EventBridge rule, the Lambda — does
    it at whatever moment the merge lands. Read `make plan` locally BEFORE
    merging, not after.
  - **Time the merge, since you cannot time the deploy.** Merge away from the
    enabled cadences' ticks: 03:30 reconcile, plus each pipeline's own
    `schedule.start_time` (product 06:30 UTC), plus the 5-minute transform sweep.
    A rule torn down as it fires loses that run. To be certain, freeze with
    `enabled: false` for the window.
  - There is a real skew window on EVERY dev deploy, not just manual ones:
    between `terraform apply` and `sync_pipelines --apply` the new dispatcher
    reads pre-sync configs. Alias normalization and per-cadence default start
    times are what make that window safe — do not remove them while any env is
    un-synced.

  **UAT and PROD are still manual** (`workflow_dispatch` only, and latched off
  by `PRODUCT_DEPLOY_ENABLED`), so for those envs the sequence above is genuinely
  yours to run, in that order, in one window.
- Backticks only in Spark SQL, never in anything sent to Oracle.
- The concentric tier invariant (event ⊂ message ⊂ api) is still enforced by
  `generate_tier_subsets.py`; relax per ADR-0003 D10 when the first
  non-concentric channel arrives.
- **`lambda_dispatcher/` is imported by bare module name**, not as a package —
  the deployed artifact is a flat Lambda zip and `gold_dispatcher.py` does
  `from echo_resources import echo`. `qe/conftest.py` puts the dir on
  `sys.path` to match. (The dir was `lambda/` upstream; "lambda" is a Python
  keyword, hence the rename.)
- **No `pyproject.toml`** — `qe/conftest.py` is the only thing putting
  `src/apps/` on `sys.path`. Don't add per-file `sys.path` hacks back.

## Conventions

- API First + Schema Registry First: register `src/schemas/` before implementing.
- `src/schemas/mapping/product/v1/product.mapping.json` is the **authored
  intake** (co-located with the compiled manifest/SQL since 2026-08-17);
  `src/schemas/gold/product/v1/master.json` + the mapping manifest/SQL are the
  **compiled runtime artifacts**. They are not yet reconciled — see
  `src/schemas/gold/product/v1/README.md` for the open deltas.
- Security & observability are design inputs (`infrastructure/observability/`,
  `../../capabilities/security/`).
- Follow the FDE workflow:
  `../../architecture-design/operating-model/fde-build-deploy-workflow.md`.

## Ownership (Four-in-a-Box)
FDE: `src/`, `infrastructure/` · QE Lead: `qe/` · UX Lead: `ui/`, `ux/` · PM: scope & Jira trace.
