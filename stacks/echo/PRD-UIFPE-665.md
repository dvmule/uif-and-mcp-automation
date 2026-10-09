# PRD — UIFPE-665: Watermark support for `extract.custom_sql` (Bronze)

> **Status:** planned, no code written. Both original blockers are settled:
>
> | # | Decision | Answer |
> |---|---|---|
> | B1 | Jira key | **UIFPE-665** (2026-09-28). Parent epic still to be set in Jira; Echo is a stack, so there is no domain epic to default to. |
> | B2 | Terraform change acceptable? | **Yes — option (a)** (2026-09-28): extract the builder into an importable module and register it as a Glue artifact. Option (b), source-text tests with no Terraform, and option (c), a second copy, are not taken. |
>
> **Scope additions — decided Yes by the product owner (2026-09-28):**
>
> | # | Decision | Lands in |
> |---|---|---|
> | D1 | The job itself raises when an incremental run (non-sentinel watermark) has `custom_sql` without the token. `register_pipeline.py --custom-sql` writes straight to DynamoDB (`register_pipeline.py:137`) and skips the controller's checks, so the controller alone can't guarantee this. | Task 1 (done) |
> | D2 | When `watermark_fallback_col` is set, the fail-loud check also requires the fallback column to be in the projection. Otherwise `run_quality_checks` silently computes the max from the primary column only while the SQL uses `COALESCE`. | Task 2 |
> | D3 | The controller rejects near-miss tokens such as `{{ watermark }}` with a message naming the exact form (handover §4.7). | Task 3, AC6 |
>
> Sources: the spec `echo-custom-sql-watermark-spec.md` (design intent, rationale, rejected alternatives) and the handover `echo-custom-sql-watermark-handover.md` (line numbers, deployment mechanism, amended ACs). Neither is committed to the repo; the "spec §n" and "handover §n" references below point into them. The handover wins on line numbers and mechanisms; the spec wins on design intent (handover §11).

| Field | Value |
|---|---|
| **Jira** | [UIFPE-665](https://pearson.atlassian.net/browse/UIFPE-665) |
| **Parent epic** | to be set in Jira |
| **Capabilities (labels)** | `capability:data` (Bronze extract), `capability:security` (`custom_sql` validation) |
| **GitHub path** | `stacks/echo/src/apps/pipeline/`, `stacks/echo/src/apps/control_panel/`, `stacks/echo/infrastructure/terraform/`, `stacks/echo/scripts/`, `stacks/echo/qe/`, `.github/workflows/release-echo.yml` |
| **Branch** | `feature/UIFPE-665-A&Q-filter-custom-sql` (quote it in shells because of the `&`), cut from `origin/develop` @ `1f4ab313` into the worktree `C:/Vaibhav/CursorWorkspaces/uif-665-aq-filter` |
| **PRD file** | `stacks/echo/PRD-UIFPE-665.md` |
| **Standards to read first** | `capabilities/data/guidelines.md`; spec §3 (design), §8 (limitation), §9 (Oracle landmines); `src/apps/pipeline/silver_routing.py:1-20` (why two copies fail) |

## Problem

`extract.custom_sql` lets a Bronze pipeline read an arbitrary Oracle query instead of a table. It is the only design the product owner accepted for the IPM assets extract, which must exclude projects based on a second view (spec §1). But `build_extract_sql` returns the custom query before any watermark logic runs (`bronze-ingest.py:172-174`), so every `custom_sql` pipeline does a full Oracle read on every run, and nothing reports it.

This story adds a `{{WATERMARK}}` token that the engineer places inside their own SQL. The job renders it per phase:
- `1=1` on initial and full loads;
- on incremental loads, the same `TO_TIMESTAMP` predicate the table path emits, from one shared implementation.

It also adds controller validation, so a forgotten token is rejected rather than silently becoming a full scan, and it fixes a latent bug where a missing watermark column silently stops the watermark advancing — a bug `custom_sql` makes easy to trigger.

No live pipeline uses `custom_sql` today (0 across dev, uat and the two surviving legacy tables; handover §6.2), so no existing behaviour changes.

## Out of scope

- **Rejected designs — do not revisit** (spec §1): filtering in Gold, a BigQuery/Athena view, filtering in Silver, or two Bronze pipelines with a join.
- **Fixing `silver-reconcile` to honour `custom_sql`.** Deferred by the product owner. **Document it, don't fix it** (Task 5). The limitation (spec §8) is that exclusions are not retroactive. A project already in Silver that is later excluded is never removed, and reloading doesn't help, for three reasons:
  - every Silver variant only inserts;
  - `silver-reconcile.py:232` reads `extract.table`, not the filtered query;
  - the `bq_native` initial rebuild reloads every Bronze partition (`silver-transform-bq-native.py:395`) and keeps the newest row from before the exclusion (`:444`).

  Removing such a row needs a Bronze purge, a full re-extract and a Silver rebuild, or a reconcile fix. Whether it matters is spec §14 Q3.
- **ISO-8601 validation of `watermark_value`** (spec §5, "optional hardening"). This is an existing exposure on the table path, not something this story introduces. Raise it as a separate story.
- **Legacy `aws_s3_object` upload for the new module.** `silver_routing.py` has none either, and every uif stack uses the SHA-pinned release path.
- **Replacing the existing hand-written copies** in `TestQualityChecks` (`test_bronze_ingest.py:137`), `TestWatermarkAdvance` (226) and `TestCustomSchemaResolution` (443). New tests don't go into them; rewriting them is follow-up work.
- **The copy of the phase logic in `src/libs/pipeline_profiles.py`** (`SENTINEL_WATERMARKS`, `resolve_phase`), which `bronze-ingest.py:128-131` already documents as a known duplicate.
- **UI.** `custom_sql` appears only as a type at `ui/src/lib/types.ts:35`; no form exposes it (handover §4.5).
- **DynamoDB schema, Silver, Gold, the scheduler, or any existing config.**
- **The legacy `echo-dev-*` stack** and every legacy config table: read-only, never touched (handover §10).
- **The stale Glue-delivery note in `stacks/echo/CLAUDE.md`** ("Glue scripts are not uploaded by Terraform"). Separate docs fix.

## Acceptance criteria (story level)

From **handover §9**, which supersedes spec §13, with AC7a restored now that option (a) is chosen.

- [ ] **AC1** `custom_sql` + `mode: incremental` emits a real watermark predicate, and row counts prove a delta. *(Predicate proven by unit tests; the row-count half is post-merge, against Oracle.)*
- [x] **AC2** `custom_sql` on the initial load (sentinel watermark) emits `1=1` and **no** `TO_TIMESTAMP`.
- [x] **AC3** `custom_sql` + `mode: full` is unchanged from today.
- [x] **AC4** Custom and table paths produce byte-identical predicates from a single implementation, checked by a test that fails if either side drifts.
- [x] **AC5** A missing watermark column on incremental **fails the run**, and the error names both the missing column and the available columns.
- [x] **AC6** The controller rejects the following, **verified on both create and update** (`pipeline_controller.py:1080` and `:1154`):
  - incremental + `custom_sql` without `watermark_col`;
  - incremental + `custom_sql` without the token;
  - near-miss tokens such as `{{ watermark }}` (D3);
  - any `;` in `custom_sql`.
- [x] **AC7** The duplicated builder at `qe/pipeline/test_bronze_ingest.py:40` is **gone**.
- [x] **AC7a** The builder tests call the real `build_extract_sql` (restored from spec §13; handover §9 had dropped it).
- [x] **AC8** The full unit suite is green. *(13 pre-existing failures in 4 unrelated files, identical at `1f4ab313`; see Task 5.)*
- [ ] **AC9** The target pipeline completes an initial run and then an incremental run against real Oracle, with the watermark advancing. *(post-merge)*
- [ ] **AC10** Spec §8's limitation is documented in the code comment and in the pipeline's `draft_notes`. *(The `draft_notes` half is post-merge.)*
- [x] **AC11** Terraform changes are limited to registering the new module as a Glue artifact (Task 1 table). No change to the DynamoDB schema, Silver, Gold, the scheduler, or any existing config.
- [x] **AC12** No UI change.

---

## Implementation tasks (sequential — complete in order)

The order follows **handover §8**. Phase 0 is folded into Task 1. Tasks 1–5 are §8 steps 6a–6e; Close-out is §8 steps 7–8.

**Test-run rule (binding):** run Echo tests **one file or one directory at a time**. Never run `pytest stacks/echo/qe` as one command; it dies with `MemoryError`. Commands run from the monorepo root. On this machine use `py -3.11 -m pytest`: the default `py` (3.14) has no pytest.

**Git rule (binding):**
- use explicit `git add <paths>`, never `git add -A`; files already dirty in the tree are not this story's (handover §10);
- ask before every commit and push;
- one commit per task: `UIFPE-665 #comment <what>`, adding `#in-progress` on the first.

### Task 1 — Phase 0 checks, the new module, token rendering, artifact registration

**Goal:** `build_extract_sql` renders `{{WATERMARK}}` from one predicate implementation shared with the table path. It lives in a module tests can import, and Glue receives that module.

**Phase 0 (no code):**
1. `git pull --ff-only origin develop`. Local `develop` was 80 commits behind at `956886e9` on 2026-09-28; `origin/develop` was `1f4ab313`. No planned file had changed and no dirty file overlapped. Then cut the branch.
2. Check that `git config user.email` is the engineer's own Jira address.
3. Re-check handover §3's line numbers if any touched file has moved since `1f4ab313`.
4. UIFPE-423 needs no action: PR #283 merged it on 2026-09-14, and `git cherry` shows nothing outstanding.
5. Re-run the config scan to confirm `custom_sql` is still unused. `scan_pipeline_configs.py` did not arrive with the handover, so ask for it or scan by hand, read-only.
6. Dev `terraform plan` is **skipped** by product-owner decision (2026-09-28). The post-merge dev deploy is therefore also dev's first apply from `develop`; see Close-out.

**Code — new module `src/apps/pipeline/bronze_extract_sql.py`.** No Spark, boto3 or Glue imports; the module docstring explains why it exists, as `silver_routing.py:1-20` does.

| Name | Source |
|---|---|
| `SENTINEL_WATERMARKS` | moved from `bronze-ingest.py:138` |
| `WATERMARK_TOKEN = "{{WATERMARK}}"` | new |
| `optional_watermark_fallback(ext)` | moved from 153-160, unchanged |
| `effective_watermark_sql(wm_col, wm_fallback)` | moved from 163-167, unchanged |
| `watermark_predicate(ext)` | **new**: the only place that knows the `<expr> > TO_TIMESTAMP('<wm>', 'YYYY-MM-DD"T"HH24:MI:SS')` shape, lifted from 190-193 |
| `build_extract_sql(ext)` | moved from 170-195, changed as below |

What `build_extract_sql` does after the change:
- **Table path:** uses `watermark_predicate(ext)`. Its output must be byte-identical to today's.
- **Custom path, no token:** returns `({custom}) t`, as today, on full and sentinel runs. **D1:** on an incremental run with a non-sentinel watermark, raises `ValueError` naming `{{WATERMARK}}`.
- **Custom path, with token:** the phase test is the same as the table path's, `mode == "incremental"` and the watermark not a sentinel. If incremental, replace **every** token with `watermark_predicate(ext)`; otherwise replace every token with `1=1`. Never render the epoch timestamp; the ORA-01427 comment at 183-185 explains why, and it moves into the module next to this logic.
- Token matching is exact `{{WATERMARK}}` only.

**Code — `bronze-ingest.py`:** replace lines 138 and 153-195 with a single `from bronze_extract_sql import …  # noqa: E402` in the same section. `_resolve_phase` (198), `run_quality_checks` (561) and `build_sql` (432) keep their call sites. `_LEAN_*` and `_SOURCE_CONTRACT` stay where they are.

**Artifact registration.** Each edit mirrors the existing `silver_routing` entry:

| File | Change |
|---|---|
| `infrastructure/terraform/locals.tf:216` (`glue_scripts`) | add `bronze_extract_sql = { file = "bronze_extract_sql.py", legacy = "bronze_extract_sql.py" }` |
| `infrastructure/terraform/glue.tf:62` | `--extra-py-files` becomes `join(",", [local.glue_script_uri["silver_routing"], local.glue_script_uri["bronze_extract_sql"]])` |
| `scripts/stage-artifacts.sh:125` | add `glue_script bronze_extract_sql "$P/bronze_extract_sql.py"`; this also puts its SHA into `artifact_shas` for dev and release |
| `scripts/stage-artifacts.sh:308` | row assertion 44 → 45, and the message's Glue count 26 → 27 |
| `.github/workflows/release-echo.yml:296` | row assertion 45 → 46, plus its comments (lines 26 and 290-293) |
| `qe/pipeline/test_glue_artifact_sets.py:121` | Glue count 26 → 27; check whether `test_lambda_artifact_sets.py` derives its count or needs the same bump |
| `qe/pipeline/test_copy_artifacts.py` | *(found during Task 1)* hardcodes `[ "$rows" -eq 44 ]` against `stage-artifacts.sh`; bumped to 45 |
| `scripts/copy-artifacts.sh` | *(found during Task 1)* comment 44 → 45 only |

`test_lambda_artifact_sets.py` derives its counts from the staging calls and needed no change. The legacy `uif_echo_sync_glue_scripts` syncs the whole `src/apps/pipeline/` directory, so it picks up the new file without an edit.

**Tests — new file `qe/pipeline/test_bronze_extract_sql.py`,** following the shape of `test_bq_partition_ddl.py`. Covers spec §6's builder cases:

| Case | Expected |
|---|---|
| token + incremental + real watermark | predicate rendered, correct `TO_TIMESTAMP` format |
| token + incremental + `watermark_fallback_col` | `COALESCE(...)` form |
| token + sentinel watermark | `1=1`, and no `TO_TIMESTAMP` anywhere |
| token + `mode: full` | `1=1` |
| token appearing twice | both rendered |
| no token + `mode: full` | verbatim |
| **AC4**, table vs custom for the same config | `build_extract_sql({"table": T, …})` equals `build_extract_sql({"custom_sql": "SELECT * FROM T WHERE {{WATERMARK}}", …})` as whole strings |
| **D1** no token + incremental + real watermark | raises `ValueError` mentioning `{{WATERMARK}}` |
| **D1** no token + incremental + sentinel | verbatim, no raise |

**Verification:**
```bash
py -m pytest stacks/echo/qe/pipeline/test_bronze_extract_sql.py -q
```
```bash
py -m pytest stacks/echo/qe/pipeline/test_glue_artifact_sets.py -q
```
```bash
py -m pytest stacks/echo/qe/pipeline/test_lambda_artifact_sets.py -q
```
```bash
py -m pytest stacks/echo/qe/pipeline/test_bronze_ingest.py -q
```
```bash
terraform -chdir=stacks/echo/infrastructure/terraform fmt -check
```
```bash
terraform -chdir=stacks/echo/infrastructure/terraform validate
```

- [x] Phase 0 recorded (develop fast-forwarded, branch cut, config scan re-run)
  - Branch cut in a separate worktree from `origin/develop` @ `1f4ab313` (the main checkout is shared with another agent, so it was not switched). `origin/develop` has since moved to `485f56f4`; its 2 commits touch only uat deploy workflows, with no overlap. Rebase before the PR.
  - Author email is `vaibhav.yenorkar@pearson.com`. Handover §3 line numbers were confirmed against the code.
  - Config scan (read-only, top level plus `extract.initial`/`extract.incremental`): `custom_sql` is used by 0/98 configs in `uif-echo-dev-pipeline-config`, 0/99 in `uif-echo-uat-pipeline-config`, 0/65 in `echo-uat-glue-job-config` and 0/42 in `glue_job_config_dev`.
- [x] Task 1 complete (2026-09-28, run on the final code):
  - `test_bronze_extract_sql` 20 passed (new);
  - `test_bronze_ingest` 42 passed, same as baseline;
  - `test_glue_artifact_sets` 11, `test_lambda_artifact_sets` 8, `test_copy_artifacts` 24 and `test_job_name_drift` 10 passed;
  - the 9 table/full cases are byte-identical to the pre-change function;
  - `terraform fmt -check` and `validate` pass (init with `-backend=false` and the local provider cache);
  - `bash -n` passes on both scripts;
  - **ruff was not run** (not installed).
  - Tests ran with `py -3.11`; the default `py` is 3.14 and has no pytest.

---

### Task 2 — Fail loudly when the watermark column is missing

**Goal:** the watermark can no longer silently fail to advance (spec §4).

**Steps:**
1. Add `require_watermark_column(wm_col, columns)` to `bronze_extract_sql.py`. It raises `ValueError`, naming the missing column and listing the available ones (AC5). **D2:** also take `wm_fallback` and require it when set.
2. In `run_quality_checks` (`bronze-ingest.py:561`), call it right after line 563 whenever `wm_col` is set. Then remove the `wm_col in df.columns` checks at 571 and 580, which are now redundant.
3. Comment that the guard keys off `ext["mode"] == "incremental"` (563), **not** the phase, so it also fires on the initial run. This is intended: the watermark must advance after the initial load (handover §4.4).
4. **Do not change** the `min_rows_expected` skip on incremental (588-599).

**Tests:** in `test_bronze_extract_sql.py`, calling the real function:
- column present → no error;
- column absent → raises, and the message contains the column name and the available columns;
- **D2:** fallback absent → raises.

Do **not** add cases to the `TestQualityChecks` copy.

**Verification:**
```bash
py -m pytest stacks/echo/qe/pipeline/test_bronze_extract_sql.py -q
```
```bash
py -m pytest stacks/echo/qe/pipeline/test_bronze_ingest.py -q
```

- [x] Task 2 complete (2026-09-28):
  - `require_watermark_column(wm_col, wm_fallback, columns)` is in the module;
  - `run_quality_checks` calls it, and the `in df.columns` guards are gone;
  - a `ValueError` falls through to `except Exception`, so the run is marked FAILED with the error in the metrics row;
  - 5 new tests, `test_bronze_extract_sql` 25 passed, `test_bronze_ingest` 42 passed.

---

### Task 3 — Controller validation

**Goal:** a config that would silently full-scan, or fail with `ORA-00911`, is rejected when it is created and when it is updated (spec §5, re-based by handover §3).

**Steps.** All in `_validate_extract_config` (`pipeline_controller.py:437`). Handover §4.1: `_validate_watermark_config` does not exist.
1. Drop `and not custom` at **452**, and rewrite the message at **453**, which currently advertises the loophole.
2. When `mode == "incremental"` and `custom_sql` is set, require `{{WATERMARK}}`. The message shows the expected form: `… AND {{WATERMARK}}`.
3. Reject `;` anywhere in `custom_sql`. The reason given is `ORA-00911` from `(SELECT … ;) t` (handover §4.8).
4. Accept `mode: full` + token + no `watermark_col` (handover §4.7).
5. **D3:** reject near-miss tokens, matched case-insensitively with optional inner whitespace but not exactly `{{WATERMARK}}`.

**Tests:** extend `TestValidateExtractConfig` (`test_pipeline_controller.py:33`):

| Case | Expected |
|---|---|
| incremental + custom_sql + no `watermark_col` | rejected (**was allowed**) |
| incremental + custom_sql + no token | rejected |
| `;` in custom_sql | rejected |
| no `mode` field + custom_sql + no token | rejected; line 439 treats a missing mode as incremental |
| full + custom_sql + no token | accepted |
| full + custom_sql + token + no `watermark_col` | accepted |
| incremental + custom_sql + token + `watermark_col` | accepted |
| **D3:** `{{ watermark }}` | rejected |
| **update path:** `update_pipeline` with incremental + custom_sql + no token, mocked like the tests at 701+ | 400; the validator is reached from `update_pipeline` (1141/1154) as well as `create_pipeline` (1060/1080) |

**Verification:**
```bash
py -m pytest stacks/echo/qe/control_panel/test_pipeline_controller.py -q
```

- [x] Task 3 complete (2026-09-28):
  - `;` and near-miss tokens are rejected in **every** mode, since a full config can later be flipped to incremental;
  - the controller keeps its own `_WATERMARK_TOKEN`, because it is a separate Lambda artifact, and a drift test compares it with `bronze_extract_sql.WATERMARK_TOKEN`;
  - new classes `TestValidateExtractCustomSql` and `TestCustomSqlValidatedOnCreateAndUpdate`: 11 tests, create and update both return 400 without writing;
  - `test_pipeline_controller` 115 passed.

---

### Task 4 — Remove the duplicated builder

**Goal:** no second copy of the SQL builder anywhere in the tree (AC7, AC7a).

**Steps:**
1. Delete `TestBuildSql._build_sql` (`test_bronze_ingest.py:40`). Move the remaining `TestBuildSql` cases (class at 25) into `test_bronze_extract_sql.py`, calling the real function through `import bronze_extract_sql`; `qe/conftest.py` puts `src/apps/pipeline` on the path, as `TestResolveSilverTrigger` (318/336) relies on.
2. Update `test_custom_sql_used_verbatim` (125-128): verbatim now means **full mode, no token**. Update it, don't delete it.
3. Delete the now-empty `TestBuildSql` class and its `_make_ext` helper if nothing else uses them.

**Verification:**
```bash
py -m pytest stacks/echo/qe/pipeline/test_bronze_extract_sql.py -q
```
```bash
py -m pytest stacks/echo/qe/pipeline/test_bronze_ingest.py -q
```
```bash
git grep -n "def _build_sql" -- "stacks/echo/*.py"
```
The grep must return nothing. It is limited to `.py` files, so this PRD's own copy of the command doesn't match.

- [x] Task 4 complete (2026-09-28):
  - `TestBuildSql` moved to `test_bronze_extract_sql.py` with the copy deleted, so its 9 cases now call the real builder;
  - `test_custom_sql_used_verbatim` is kept and commented as full mode with no token;
  - `test_bronze_extract_sql` 34 passed, `test_bronze_ingest` 33 passed (42 − 9 moved);
  - the grep returns nothing.

---

### Task 5 — Document the limitation, run the full suite, open the PR

**Goal:** the non-obvious limitation is written where the next engineer will look, the suite is green, and the work is ready for review.

**Steps:**
1. Rewrite the STEP 5 comment (`bronze-ingest.py:421-431`) and the module docstring to cover:
   - the token and its rendering rules;
   - that incremental requires the token and `watermark_col`;
   - the `;` rule;
   - **spec §8's limitation** in full: not retroactive, why reloading doesn't help, and the recovery path;
   - the existing injection warning (427-429), kept.
2. Draft the target pipeline's `draft_notes`:
   - the limitation;
   - use `NOT EXISTS`, not `NOT IN`;
   - no trailing `;`;
   - `min_rows_expected >= 1`;
   - put the token in the outer `WHERE`.

   These are registered in Close-out. Draft text:

   > Bronze filter via extract.custom_sql (UIFPE-665): excludes assets of projects that FPI_REP_PRD_PRJ_HEADER_V classifies as classification_type_code '20' with business_type_code '140'/'180'. Written as NOT EXISTS, not NOT IN, so a NULL header project_id cannot empty the extract. {{WATERMARK}} sits in the outer WHERE. No ';'. quality.min_rows_expected >= 1. KNOWN LIMITATION: the filter is not retroactive. A project excluded after its rows loaded stays in Silver, and reloading does not remove it (Silver only inserts/updates; reconcile checks extract.table). Any change to the filter or its codes needs a full re-extract: watermark back to the sentinel, Bronze prefix purged, Silver rebuilt. Can projects be reclassified after load? <answer from product owner>.
3. Run the full suite **one file at a time** (below). Record pre-existing failures separately from this story's.
4. Commit with explicit paths, after asking. Open the PR `UIFPE-665 · Watermark support for extract.custom_sql` with `## Summary` and `## Verification` sections. The last commit is `UIFPE-665 #comment Ready for review #code-review`.

**Verification:**
```bash
py -m pytest stacks/echo/qe/pipeline -q
```
```bash
py -m pytest stacks/echo/qe/control_panel -q
```
```bash
py -m pytest stacks/echo/qe/lambdas -q
```
```bash
py -m pytest stacks/echo/qe/libs -q
```

**Suite result (2026-09-28).** All 85 files under `qe/{pipeline,control_panel,lambdas,libs,gold}` were run one file at a time: **2022 passed, 4 skipped, 13 failed/errored in 4 files**:
- `pipeline/test_promote_workflow` (1 failure);
- `gold/test_compile_intake_delta` (5);
- `gold/test_compile_preview` (3);
- `gold/test_ui_classification_parity` (4 errors).

**All 13 are pre-existing:** the same four files give identical results at the base commit `1f4ab313`, and none of them touches this story's code. `ruff` was not run (not installed); `py_compile` passes on every changed file.

- [x] Task 5 steps 1–3 complete (2026-09-28): the STEP 5 comment covers the token, validation, the limitation (in both directions: newly excluded rows stay, newly included rows are never picked up incrementally) and the injection warning; `draft_notes` is drafted; the suite is green apart from the pre-existing failures.
- [ ] Step 4 (**waiting on the product owner's review**): commit, rebase on `origin/develop` (now `485f56f4`, no overlap), push, open the PR.
- [ ] PR opened; the `smart-commit`, `commit-identity`, `standards-review` and `pull-request` checks are green
- [ ] Task 5 complete

---

### Close-out (post-merge — AC9, AC10's `draft_notes` half)

This is spec §11 steps 2–7 against real Oracle. **Do not skip it or guess the outcome:** `ORA-01427` only shows up against the real view, and dev schedules are disabled, so every run is a manual `start_job_run`.

**Before starting:**
- Answer spec §14 Q2 (AWS or GCP?), Q3 (can a project be reclassified?) and Q4 (the assets view's row count and primary key).
- Decide whether an asset with a NULL `project_id` should be kept (`NOT EXISTS`) or dropped (the original `NOT IN`).

Because the dev plan was skipped, this deploy is also dev's first apply from `develop`, which moves Glue scripts to the SHA-pinned release bucket (handover §5). If a run fails, check the deploy before blaming this change.

1. Deploy to dev.
2. Register `tep-ipm-ipmrep-fpi-rep-prd-prj-assets-v` through `POST /pipelines`, **not** `register_pipeline.py`:
   - watermark at the sentinel;
   - `metadata.domain = IPM`;
   - the `draft_notes` from Task 5;
   - `custom_sql` at the top level only, never in a phase block.

   Confirm that dropping the token or `watermark_col` is rejected.
3. **Initial run:** the log shows `1=1` and no `TO_TIMESTAMP`, and the excluded projects are absent (check the count against a direct Oracle query).
4. The watermark **advanced**. Read it back from `uif-echo-dev-pipeline-config`.
5. **Incremental run:** the log shows the real predicate and the row count is a delta. If the runtime matches a full read, predicate pushdown was blocked (spec §9).
6. **Negative test:** a projection that omits `LAST_UPDATE_DATE` fails loudly.
7. Silver runs, and the table is created.

- [ ] Close-out complete — AC9 and AC10 ticked

---

## Conflicts — how each was settled

| # | Conflict | Settled |
|---|---|---|
| C1 | Which tasks the Terraform decision blocked | Moot: option (a) is chosen, and each task has a single path. |
| C2 | Handover §9 dropped "tests exercise the real function" from spec §13 | Restored as AC7a. |
| C3 | `TestQualityChecks` and `TestWatermarkAdvance` are hand-written copies too, which neither doc noticed | New tests target the module; replacing the copies is out of scope. |
| C4 | Spec §12 and `stacks/echo/CLAUDE.md` describe Glue delivery differently from handover §5 | Handover followed; the CLAUDE.md fix is out of scope. |
| C5 | Near-miss token rejection | Decided Yes as D3 (Task 3, AC6). |
| C6 | `scan_pipeline_configs.py` did not arrive with the handover | Settled: scan done by hand during Task 1, 0 configs use `custom_sql`. |
| C7 | The spec's line numbers are stale, and the spec says UIFPE-423 is in flight | Handover §3 numbers used throughout; UIFPE-423 merged 2026-09-14 (PR #283). |
| C8 | Spec §12 says legacy `echo-dev-*` is live, but its config table is gone | Doesn't affect the code; it stays untouched regardless. |
