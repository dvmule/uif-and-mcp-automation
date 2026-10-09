# beacon — stack skill

Context for engineers and AI assistants working in the `beacon` stack.

> **Migrated from `domains/beacon/` to `stacks/beacon/`** (ADR-0010) — a
> deployable stack, not a schema-owning domain. See `stacks/harbor/CLAUDE.md`
> for what distinguishes the two.

## What this stack owns

The self-service outbound export platform (RELAY **Beacon**): the control-panel
API + UI where a domain team authors an export config, the approval/versioning
lifecycle, and the runtime that executes published exports (Step Functions →
Glue PySpark → delivery Lambdas). Design:
[docs/beacon-export-design.md](docs/beacon-export-design.md) (v0.7, D1–D23).
Standard: [capabilities/data/beacon.md](../../capabilities/data/beacon.md).

## Boundaries (what lives where)

- **This stack**: engine (`src/libs/dynamic_mapping/`), pipeline
  (`src/apps/beacon_export/`), control panel (`src/apps/control_panel/` + `ui/`),
  all Beacon infrastructure (`infrastructure/terraform/`).
- **Consuming domains** (product today): only their export **configs**, under
  their own `src/schemas/beacon/<job>/v<N>/{mapping,export}.json`, plus tests
  that exercise their data through this platform. They never import our code.
- **Gold tables**: read-only inputs via Glue catalog + Lake Formation. This
  stack never writes Gold.

## Key invariants (from the design doc — verify against it before changing)

- **D4 — gates fail closed.** A quality-gate breach aborts BEFORE rendering;
  nothing reaches the destination; the failure is recorded in the runs table.
- **D22 — config versions are immutable.** Publishing writes
  `configs/<job>/v<N>/{mapping,export}.json` to S3 with an overwrite guard
  (publisher head-checks) and a conditional DynamoDB put. A change is a new
  version; rollback is running an earlier one. Never regenerate an existing
  version's files.
- **D16 — destinations are author-owned; credentials are never stored.** Only
  a `secret_ref` (Secrets Manager name) is kept.
- **D17 — exactly one rendering per export.** The same data in another format
  is a separate export job.
- **D20 — strict runtime split.** Heavy transform in a PySpark job; light
  steps (deliver, record_run, emit_event) in Lambda. The *split* is the
  invariant; which service runs the heavy half is not — since UIFPE-209 it is
  a **managed Glue 5.0 job** (the runtime comparison and its evidence are on
  UIFPE-205), entry point `glue_entrypoint.py`, which injects a session into
  the same engine. The Fargate pipeline was **deleted at UIFPE-215's
  probation exit (2026-09-02)** after a week of clean Glue runs — restoring
  it is a git revert of that commit plus an apply. `fargate_entrypoint.py`
  keeps its name for history but now holds only the shared arg parsing and
  `main()` that `glue_entrypoint` calls.
- **pyspark imports stay quarantined inside function bodies**
  (`beacon_export_job.py`, `glue_entrypoint.py`) — everything else must
  import without Spark so the Lambdas (512 MB, no JVM), the API Lambda, and
  the test suite work without it. Do not add a module-level pyspark import.
- **The engine never creates a SparkSession** — sessions are injected by the
  entrypoint. Keep it that way or the API Lambda breaks.

## Gotchas

- **The API runs on Lambda behind API Gateway (UIFPE-226, the Echo
  pattern).** `control_panel.lambda_api.handler` wraps the same FastAPI app
  in Mangum; the zip is built by the deploy workflow (code + vendored deps,
  content-hashed per UIFPE-200) at `code/beacon-api.zip`. The API's ECS
  service, ALB, image, ECR repo and CodeBuild project were **deleted at
  probation exit (owner call, 2026-08-28)** — restoring ECS is a git revert
  plus apply, not a tfvars flip. The Lambda's IAM role references the
  `api_task` policy document (the name survives for grep history), and the
  Lake Formation DESCRIBE grant for live column introspection points at the
  Lambda role.
- **Zero container images.** `Dockerfile.api` went at UIFPE-226's probation
  exit (the API ships as a Lambda zip); the pipeline `Dockerfile`, its ECR
  repos and CodeBuild project went at UIFPE-215's probation exit
  (2026-09-02). Nothing is built into an image and nothing runs on ECS; the
  `CHECKLIST.md` one-image deviation is closed.
- **Glue needs third-party deps declared.** The retired pipeline image
  installed `requirements.txt`; the Glue job has to be told — Python packages
  via `--additional-python-modules` (sqlglot), Java libraries via
  `--extra-jars` (spark-xml + deps for XML rendering, UIFPE-250, staged to
  S3 by the deploy with pinned SHA-256). Adding a runtime dependency to the
  engine means updating those arguments too, or the job dies on import
  (UIFPE-205 run 001).
- **Backend state keys differ per env, deliberately.** Sandbox keeps the
  historical `beacon-control-panel/sandbox/terraform.tfstate` because that
  state EXISTS and renaming it orphans the deployed stack. Dev uses
  `uif-beacon/dev/terraform.tfstate` in the account's shared state bucket
  (`pearson-ingestion-framework-tfstate-283556840805`, the same one product
  uses) — renamed while nothing was applied, so it cost nothing.
- **Lambda handler strings** are `beacon_export.lambdas.<name>.handler` and
  must match the zip layout built by `infrastructure/deploy/build-lambda-zip.ps1`.
- **Live source introspection**: `/registries/sources/{id}/columns` reads the
  Glue catalog (iceberg) or the CSV header at request time and strips
  `_`-prefixed metadata columns; the registry YAML is only the offline
  fallback. A new Gold column needs no deployment. **Since UIFPE-229 the
  whole picker is live too**: `/registries/domains` +
  `/registries/domains/{d}/tables` list gold databases/tables straight from
  the catalog — the offered domains come from `BEACON_GOLD_DATABASES`
  (Terraform's `var.gold_databases`). Read access is separate: the LF grants
  are **tag-based** (`uif-echo-gold-layer={env}`, the account's gold-layer
  LF-Tag) because the deploy role's grantable permission is tag-scoped and
  LF never lets a tag permission be re-granted by database name — so one
  grant per role covers every gold table and a new domain needs only the
  tfvars line (plus the platform tagging its database).
  Incremental eligibility = the platform CDC column (`_gold_updated_at`)
  being present; `watermark_column_for_path` checks the YAML row first,
  then the live catalog. Adding a domain to the UI = one tfvars line.
- **The runs table is written by the record_run Lambda only.** The state
  machine still discards the transform's task output (`ResultPath = null` on
  the Transform step), but since UIFPE-168 the transform writes its summary
  to `<canonical>/_report/report.json` and record_run merges the row counts
  into the run row on the success branch (best-effort — enrichment must
  never lose the run row). Runs from pre-168 engine versions still lack
  counts, so the UI's `hasRowCounts` adaptation stays.
- **Multi-source feeds (UIFPE-237, generic end-to-end since UIFPE-167).** A
  mapping config is `sources[]` + `joins[]`; any gold table the picker shows,
  from any configured domain, can drive a feed or join onto it, on whatever
  columns the author picks. `joins[0].left` seeds the engine's frame and the
  rest fold in declaration order, so a join's `left` names the source it
  attaches to (the one prior alias its condition references, else the
  driving alias) and `validate_structure` refuses a chain that references a
  source before it is joined, joins one twice, or leaves one out. A `cross`
  join carries `condition: ""` (missing/null normalise to it); any other
  type needs a condition. The engine accepts inner/left/right/full/
  left_semi/left_anti/cross; the wizard offers left/inner/full/cross (right
  = make the other table the driver; semi/anti add no columns). Submit-time
  column checks resolve a source path through its `sources.yaml` row OR the
  gold catalog (`registries.source_id_for_path`), all-or-nothing per config
  — one unresolvable source disables them for the whole feed, because a
  partial schema map would raise false "unknown source" errors. The
  publisher refuses `extraction.mode: incremental` for a feed with more than
  one source (UIFPE-238's single-source window; the wizard forces full load
  too). The wizard's feed/join rules are pure functions in
  `ui/src/lib/feedSources.js` with vitest tests (`npm test` in `ui/`, run by
  the deploy workflow's test gate on every push). Adding a domain to the
  picker is still one tfvars line; a configured database that does not
  exist yet lists zero tables (Glue's EntityNotFoundException is tolerated)
  rather than 503-ing.
- **Joins through a list column, and `unnest` (UIFPE-580).** Product relates
  to program and marketing copy only through lists on the product row
  (`associated_programs` = program PPIDs; `marketing_copy[].content_id`), and
  price keeps its prices inside `price_specifications`. A join may carry
  `through: {list: "product.marketing_copy", alias: "product_copy"}`: the
  engine spreads the list (OUTER explode — an empty list keeps its row) and
  then does an ordinary equality join on the element, addressed as
  `alias.field` (list of structs) or `alias.value` (list of scalars). Never
  write `array_contains(...)` as the join condition — it is a nested-loop
  join over 2.17M × 6.5M rows. The top-level `unnest: [{list, alias}]` runs
  after every join and before the filters (one row per price), so a filter
  on the element narrows rows — the owner's model: joins give FLAT rows, one
  per match, for every format; the Filter step does the narrowing; left join
  is the default. Element fields are validated like source columns: the
  engine reads them from the Spark schema (`engine.element_schemas`), the
  panel from the catalogued Glue type (`dynamic_mapping/lists.py`). The four
  product relationships are DECLARED in `registry_data/relationships.yaml`
  (`GET /registries/relationships`, `{table}` = feed alias) — never inferred
  from column names. **UIFPE-581:** the wizard prefills the declared join
  when a table is added (a sentence plus "Advanced: edit the join"; a
  wizard-built join carries `auto: true` and is upgraded when the
  relationships land, an author's edit never is), `buildMappingConfig`
  emits `through`/`unnest`, the element's fields appear on the Filter and
  Mapping steps folded into the joined TABLE's card — one card per table,
  only the fields the table lacks, heading "<Table> · <row>" from
  `relationships.yaml` (UIFPE-592; `lib/feedSources.js` `listColumnsFor`,
  `elementFields` parses the Glue array type; no value suggestions inside a
  list), and Preview plans the spread as `LEFT JOIN UNNEST(…) AS alias
  (fields) ON TRUE` — the outer form, so an empty list keeps its row like
  `explode_outer`. For a list of STRUCTS the arguments are one
  `transform(list, x -> x.field)` per field, zipped (UIFPE-601): Athena
  returns an unnested record as ONE ROW column, so naming its fields on the
  alias fails with MISMATCHED_COLUMN_ALIASES. Identifier lists cannot
  target an element.
- **Row filters (D18; structured builder in the wizard's Filter step).** A
  mapping config's `filters` is a list of Spark SQL boolean strings; the
  engine ANDs them and applies them to the **joined** frame before mapping
  (`engine.apply_filters`, directly — UIFPE-620 removed the persist-then-
  count of the unfiltered join that materialised the whole join and list
  spread for a two-product export; `filtered_rows` stays in the report as 0
  for the run-row shape), and excluded rows never reach the rejected path —
  filtering is scope, validations are quality.
  Columns must be alias-qualified (`product.isbn13`), like join conditions;
  the reference check skips bare names, which then fail at run time. The
  wizard's Filter step (after Mapping) compiles three clause kinds to those
  strings: identifier lists (PPID / ISBN-13) inline as `alias.col IN ('..',
  ..)` — at most 5,000 values per condition — typed attribute conditions
  whose operators follow the Glue type the columns route now returns
  (`types`, live iceberg only; absent = unknown = string operators), and
  custom Spark SQL. There is no engine-side list type; "Check filter" is
  `POST /validate/mapping-config` with a stub literal mapping, and the live
  data beside it is the Athena preview below (UIFPE-441). Escape literals
  with a backslash (`'O\'Brien'`): Spark
  reads `'O''Brien'` as two adjacent literals and yields `OBrien`. Limits
  are bytes, layered: the validator refuses one filter over 200 KB
  (`MAX_FILTER_BYTES`), lifecycle refuses a draft over 384 KB
  (`MAX_RECORD_BYTES` — a draft/version is ONE DynamoDB item; a 5,000-value
  clause costs ~165 KB because `settings.filter_model`, the builder's own
  model, UI-only, stored opaquely, never copied to export.json, repeats the
  list). The rules are pure functions in `ui/src/lib/rowFilter.js` (vitest).
  Post-join filters deliberately do not push down (see
  `docs/beacon-incremental-extraction.md`): a filter never narrows the scan.
- **The attribute picker offers only attributes a person could pick a value
  for (UIFPE-514, owner decision 2026-09-22, superseding the 2026-09-15
  "unique per product" rule).** Rules in `ui/src/lib/rowFilter.js`
  `attributeColumnsFor`: identifiers by NAME are never offered
  (`IDENTIFIER_COLUMN_RE`: product_id, parent_product_id, inventory_item_id,
  source_item_number, program_id, content_id, isbn13, isbn10 — the PPID is
  physically `product_id`; no Gold table has a column named ppid, which is
  why the old regex hid nothing in dev); text columns with more than
  `TEXT_CARDINALITY_CAP` (1,000) distinct values are never offered; text at or
  under the cap is offered with a "~N values" label; numbers, decimals,
  dates, timestamps, booleans, arrays and structs are always offered; text
  whose table is not yet measured is HELD BACK ("measuring N more
  attributes…"), and a failed or unavailable measurement is said out loud and
  falls back to the name rule. Dates default to "is between". The
  measurement is the cardinality profile, now ASYNCHRONOUS and PERSISTED
  (`preview.py`: `GET /registries/sources/{id}/profile` reads
  `preview/profiles/<db>.<table>.json` or says absent/unavailable and never
  scans; `POST` starts one execution — `count(*)` + `approx_distinct` per
  scalar column, MARKER + prefix fences — idempotent on a fresh object;
  `GET …/profile/{id}` polls and persists on SUCCEEDED; freshness
  `PROFILE_PERSIST_TTL_S` one day, and s3.tf's lifecycle expires the prefix
  on the same cadence). Wizard.jsx starts it when a source joins the feed so
  it is usually done by Filter. The old synchronous 20 s profile and its
  per-instance cache are gone: on the 2.17M-row dev product table it timed
  out silently and the picker offered everything. The API mirrors the rules
  for value suggestions (identifiers and over-cap columns refused without an
  Athena call); `_projection` keeps the OLD narrow `SAMPLE_IDENTIFIER_RE` so
  the preview sample and CSV columns did not change. `unique_like` stays in
  the payload but decides nothing (it measured an item × organisation grain
  with NULLs in the denominator).
- **Attribute meanings come from the Gold master, never from Beacon
  (UIFPE-562).** `GET /registries/sources/{id}/columns` carries
  `descriptions` — each catalogued column's `description` from
  `schemas/gold/<entity>/v1/master.json` in the gold artifacts bucket,
  verbatim, matched camelCase→snake_case by `channel_eligibility._canonical`
  and read through the same cached master as the UIFPE-540 channel guard
  (one S3 read serves both). The wizard shows it in one line under the
  Mapping step's attribute chips (hover/focus) and under a Filter attribute
  clause. Absent = unknown (`types` convention): no
  `BEACON_GOLD_ARTIFACTS_BUCKET`, a non-Gold source, an unreadable master or
  an undocumented property. The bucket variable (`var.gold_artifacts_bucket`)
  is what switches BOTH features on per environment — set for dev on
  2026-09-24 (owner decision, `envs/dev.tfvars`), still unset for uat/prod.
- **Live filter preview on Athena (UIFPE-441) — the control panel's first
  read of gold DATA.** `control_panel/preview.py`: value suggestions
  (`GET /registries/sources/{id}/columns/{col}/values`, top-50 by frequency,
  synchronous with a 20 s in-Lambda cap, cached 10 min per instance), the
  cardinality profile and the asynchronous preview (`POST /preview/filter` → 202 + id, `GET/DELETE
  /preview/{id}`) that returns rows before/after the filter, a 10-row sample
  and a pasted list's identifiers that match nothing. Runs on the account's
  shared `primary` workgroup (`var.athena_workgroup` — the deploy role has no
  `athena:*`, so no workgroup of our own and no per-query scan caps); results
  go to the data bucket's `preview/` prefix, expired after a day (s3.tf).
  **Athena is the state store**: the preview id IS the dotted execution ids;
  every query we start opens with `MARKER` and writes to our prefix, and
  status/cancel 404 an id whose execution lacks either — the role can read
  any primary result, this API must not. Nothing unvalidated reaches
  Athena: parse_config + the three static layers first, then each clause is
  transpiled Spark→Trino with sqlglot; a clause Athena cannot run (left_semi
  /left_anti, CSV source, `hash`/`format_number`, a reserved-word alias)
  refuses the whole preview with the validator's `{where, message}` shape —
  never a preview of different semantics. Trino needs three things sqlglot
  does not do: `LIKE … ESCAPE '\'` when the pattern carries a backslash
  (Spark's default escape, none on Trino), `JSON_FORMAT(CAST(x AS JSON))`
  for array/struct columns (CAST to VARCHAR is a type error), and un-quoting
  backticked identifiers (quoted = case-sensitive; Glue stores lowercase).
  Every query is measured against Athena's 256 KB ceiling before it starts.
  IAM/LF: `api_task` gained Athena on the workgroup, `glue:GetPartition(s)`,
  S3 read on `var.gold_data_buckets`, write on `preview/*`,
  `lakeformation:GetDataAccess`; the API role's LF grant became
  SELECT+DESCRIBE — `aws_lakeformation_permissions` is all-ForceNew, so that
  plans as a replace (revoke, then grant: seconds without a grant — apply
  off-peak, never `create_before_destroy`). The endpoints are as
  unauthenticated as the rest of the API (dev-MVP posture, Entra ID slice
  pending), which is why the marker/prefix check and the validator gate are
  not optional. Tests: `qe/control_panel/test_preview.py` (FakeAthena).
  **The sample IS the export (UIFPE-603, owner decision 2026-09-25):** the
  wizard sends the real mappings and `_mapping_projection` renders them as
  the sample's columns — target names as headers, a column by its Glue type
  (complex → JSON), a literal as is, an expression transpiled Spark → Trino
  like a filter; one Athena cannot run shows its first source column and is
  named in `untransformed` (the UI says so). `data_type` casts are not
  applied. Only the `_filter_check` stub keeps the old diagnostic projection.
  **Complex values → JSON (UIFPE-605):** Athena cannot cast a date or
  timestamp to JSON (Iceberg timestamps arrive as `timestamp(6) with time
  zone`), so `_json_value` walks the Glue type and makes those fields text
  first — `transform` over arrays, structs rebuilt as `CAST(ROW(…) AS
  ROW(name type, …))` so the JSON stays an object — for a directly mapped
  complex column and for `to_json(col)` in a computed field; an unreadable
  type is selected raw. The drift check (`drift.py`) adds list-element
  schemas like `validation.py` does, or every `price_spec.*` mapping reads
  as false drift.
  **The matched rows as one CSV (UIFPE-490)** sit beside the preview:
  `POST /preview/download` runs the sample query with `DOWNLOAD_ROW_CAP`
  (100,000) in place of its `LIMIT 10` — one execution, same fences — and
  a finished `GET /preview/download/{id}` answers with a 15-minute
  presigned GET on the CSV Athena wrote to `preview/` (Content-Disposition
  set server-side so the browser saves a readable name), the ONLY time this
  API points a browser at S3. Nothing downloads by itself: the wizard shows
  a button, then a link, and hides both the moment the filter changes (the
  download carries the preview's request signature). The file is the raw
  Gold rows after join and filter in the preview's columns, NOT the
  rendered export — the copy says so — and the status route 404s a
  preview's own sample execution (it checks for the capped LIMIT) so only a
  download can be presigned through that door. No ORDER BY: a capped file
  is "some" of the matches, and the UI says which count it is.

## Conventions

- Contract-first: `src/api/openapi.yaml` is the registered API contract.
- **Naming for NEW AWS resources: the UIF Resource Name Lookup is the
  authority** (owner instruction 2026-09-07, superseding the 2026-08-26
  `<project>-<env>` decision): `uif-{stack}-{env}-{operation}` hyphenated,
  with per-type suffixes (`-sg`, `-role`/`-policy`, `-api`) and **Secrets
  Manager names in slash style: `uif/beacon/{env}/...`**
  (`local.secrets_prefix`). Deployed names are frozen only where renaming
  orphans data — the three `beacon-dev-*` DynamoDB tables and the
  `beacon-dev-export-*` schedule prefix. **The IAM roles were renamed at
  UIFPE-365**: a role carries no data, so that rationale never applied. Renaming
  `scheduler_invoke` does cost something, though — Scheduler stores the role ARN
  on every schedule, so existing schedules need re-PUTting after the apply.
- **Schedules are runtime data, not Terraform (UIFPE-206).** One EventBridge
  Scheduler schedule per export, named `beacon-<env>-export-<id>` in the
  account's shared **default** group (the deploy role has no scheduler:*
  permissions to create a group of our own; isolation is the name prefix,
  which is also the API role's permission scope), created/updated/deleted by
  the API (`scheduler.py`); the record the UI reads lives in
  `beacon-<env>-export-schedules`. The target is the scheduled-starter
  Lambda, which resolves the LATEST published version at fire time, skips
  (and records `skipped`) when a batch for that export is still running, and
  starts the same execution Run-now starts. Missed triggers are never caught
  up (owner decision 2026-08-27: skip and record). The state machine's input
  contract now includes `trigger` (`manual` | `scheduled`) alongside
  `job_config` — every starter must supply both. `skipped` rows, like
  `stopped`, are neutral for health. **Pause windows (UIFPE-231)**: pausing
  stores `paused_from`/`paused_until` (calendar dates in the schedule's OWN
  timezone) on the record; the AWS schedule stays ENABLED and the starter
  skips fires inside the window — so auto-resume is the window passing,
  never a re-enable step that could fail. An unreadable window FAILS OPEN
  (the feed keeps flowing). `enabled=false` remains the indefinite pause.
- **Runtime knobs that operators tune live belong in DynamoDB, not tfvars.**
  Transform sizing (worker type, count, timeout) lives in
  `beacon-<env>-glue-job-config`; Terraform seeds the `default` row and then
  ignores changes to it. The API reads it when starting a run and passes the
  values in the execution input, which the state machine forwards to
  StartJobRun as overrides — so retuning a slow export needs no deploy.
- **Incremental extraction (UIFPE-168).** The MODE is versioned config
  (`settings.extraction` → an explicit `extraction` block in export.json;
  missing = full, per the UIFPE-172 convention); the WATERMARK is runtime
  state in `beacon-<env>-extraction-state`, advanced ONLY by record_run on
  the success branch (monotonic conditional — never regress), reset from the
  panel to force a re-baseline. The state machine input contract now carries
  `watermark` ("" = full/baseline) alongside `job_config` and `trigger` —
  every starter must supply all of them, and `publisher.start_run` +
  `scheduled_starter.py` must stay in lockstep. Design:
  [docs/beacon-incremental-extraction.md](docs/beacon-incremental-extraction.md).
- UI mirrors the platform shell (Tailwind v4, platform tokens, `/m/beacon/*`
  routes) so it can be lifted into the RELAY shell — see `ui/src/theme.css`
  for the token names. It ships hash-routed with a relative asset base
  (vite `base: "./"`), which is what lets it live under the shared
  distribution's `/m/beacon/` prefix — the transform UI's pattern (UIFPE-99).
- **Deploys are variable-driven for now** (`BEACON_INFRA`,
  `BEACON_DEPLOY_ENABLED`, `BEACON_API_URL_DEV`) because GitHub only offers
  the "Run workflow" button for workflows present on the default branch, and
  the deploy workflows live on `develop` only. See the header comment in
  `.github/workflows/deploy-beacon-dev.yml`.
- Follow the FDE workflow:
  `../../architecture-design/operating-model/fde-build-deploy-workflow.md`.

## Ownership (Four-in-a-Box)
FDE: `src/`, `infrastructure/` · QE Lead: `qe/` · UX Lead: `ui/` · PM: scope & Jira trace.
