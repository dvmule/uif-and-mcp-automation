# UIF Echo uat rollout — adopting the stack promotion model

Echo adopts the promotion model that `stacks/apicurio/` proved through dev and
uat ([`capabilities/delivery/stack-promotion-model.md`](../../../capabilities/delivery/stack-promotion-model.md)),
so that a `uif-echo-uat-*` stack can be deployed from an immutable build tag
rather than from whatever is on `develop`. `echo-uat-*` keeps running
throughout; it is retired only after `uif-echo-uat-*` is verified against it.

## Status: Phases 1–4 are done. `uif-echo-uat-*` exists.

Converged from **`echo/v0.8.0`**, both Terraform roots, on 2026-09-27
([run 36339681333](https://github.com/pearson-dnt-coe/universal-Integration-framework/actions/runs/36339681333)).
The main root reported `0 added, 0 changed, 0 destroyed` — it had completed on
the previous run — and the gold root `20 added, 0 changed, 4 destroyed`, those
four being the Lambdas left tainted when `PutFunctionConcurrency` was denied.

**It took nine dispatches.** What stopped each one is recorded in the phase
sections below rather than summarised here, because the value is in the
individual cause, not the count. The short version, for anyone about to
bootstrap **prod**: two failures were structural and are now fixed for every
environment (a Terraform graph-ordering gap, and a build cache that cannot
survive an ephemeral runner); one was a genuine bug (S3's ten-tag limit on
objects); one was a race that had hidden through four successful runs; and the
rest were IAM actions a derivation cannot see. Expect the IAM rounds again in
prod. Do not expect the others.

Still open after the green converge:

- **4.9**, the security-group rule the apply cannot catch — see that section.
- **Phase 5**, where two gaps are known and neither is subtle: there is no
  `stacks/echo/qe/integration` suite, so `promote-echo.yml`'s smoke step fails
  by design, and nothing passes `UIF_ECHO_UI_BUNDLE`, so a release tag records
  `ui_bundle: NOT promoted`.

Phase 1 happened entirely in **dev**, because the model requires that dev is the
only environment that builds: uat receives artifacts, it never makes them.

**Scope now covers BOTH roots.** This document was approved for
`infrastructure/terraform/` only, with `infrastructure/terraform-gold/` deferred
by owner instruction as a separate release unit. UIFPE-576 reversed that: one
workflow applies both roots in one dispatch, main before gold, because main
creates the shared Glue role that gold reads with `data.aws_iam_role`. The
deploy policy is derived from both roots for the same reason. `transform-ui/`
came with the gold root and is therefore in scope too, though nothing has
deployed it to uat yet.

---

## Corrections since this plan was approved

Nine things turned out differently once Phase 1 was built. They are listed
here rather than quietly edited into the sections below, because the plan was
reviewed in its previous form and a silent rewrite would hide what changed.

| # | What changed | How it was found |
|---|---|---|
| 1 | **There are FIVE Lambda functions, not four**, so the artifact count is **27, not 26**. `dq_check_controller` is declared in `data_quality.tf`, not `lambda.tf` — which is why `lambda.tf`'s own header says "all five Lambda functions" while only four resources sit in it | a `grep` for `aws_lambda_function` across the whole root, after the first inventory grepped `lambda.tf` alone and read the other files' Lambda mentions as references |
| 2 | **A tenth script was needed: `zip-artifact.sh`.** `zip` and even `zip -X` embed mtimes, so `source_code_hash` would change on every build and two tags of identical source would each show a Lambda update — a diff with no change behind it | `zip` was absent from the dev machine; chasing that exposed the determinism requirement |
| 3 | **The artifact TSV needs an eighth column, `code_sha256`.** The seven-column format had nowhere to put the value Terraform's `source_code_hash` needs, and it is also how a copied archive is verified without extracting it | writing `restore-artifacts.sh`, which had nothing to verify a zip against |
| 4 | **All four Lambda layers are `origin: vendored`, hashed from S3, never rebuilt.** Three have `make` targets that end in `zip -qr`; a pip tree is not byte-stable (`RECORD`, `__pycache__`); and `oracle_instant_client` is a licensed ~127 MB download with no build target at all | reading the Makefile targets before wiring them into the release |
| 5 | **The conversion is GATED on `local.is_uif_stack`, not wholesale.** This Terraform root is shared by `dev`, `dev-uif`, `uat` and `prod`, so converting it outright would have made the next `echo-prod` apply fail on missing required variables. `uif-echo-*` takes the new path; `echo-*` keeps today's until it retires | checking which tfvars carried the new variables — none did |
| 6 | **No `removed` blocks.** They were the plan for forgetting the 16 `aws_s3_object` uploads without destroying them, but `removed` cannot be conditional, and correction 5 means the legacy stacks still need those resources. Their `count` is gated instead | `removed` blocks have no `count` |
| 7 | **Staging is one shared script, `stage-artifacts.sh`.** `release-echo.yml` and the dev deploy both call it. Written twice it drifts, and the failure is invisible: a function whose source list is stale ships without a module it imports, and fails at runtime rather than at apply | about to write the same 26-artifact list a second time |
| 8 | **`copy-artifacts.sh` is deferred to the promote work.** Only `promote-echo.yml` exercises it and it cannot be tested without AWS; writing it now would mean shipping untested code | — |
| 9 | **A UI WAF was added to Phase 1, written but switched OFF** (`waf.tf`, `var.enable_ui_waf = false`). Not in the original plan at all. trivy's `AWS-0011` on the UI distribution looked like a finding to accept until apicurio turned out to carry a named Pearson compliance program for it — `PCMSanctionsRuleGroupCloudFront`, Firewall-Manager-seeded, due **2026-09-30**. Confirmed live in Echo's own account, so the program reaches Echo too. It is off because `simulate-principal-policy` returned **implicitDeny for nine of the ten `wafv2` actions** on `echo-dev-github-actions`: true would render a plan and fail the apply at `CreateIPSet`, blocking the very tag Phase 1 exists to cut | reading apicurio's `waf.tf` while answering "what WAF and CMK does apicurio use?" — the finding had already been triaged as an accept, wrongly |

Two defects were found in passing and fixed, neither of them this plan's subject:

- **`required_version` was `>= 1.5.0` while all four backends set `use_lockfile`**, a 1.10 feature — so an older binary ran without state locking on every environment including prod. Raised to `>= 1.10.0`. The same defect apicurio recorded and fixed.
- **`deploy-uif-echo-dev.yml` pinned Terraform 1.15.3** where every other workflow pins 1.15.8. A saved plan is only valid for the version that wrote it.

And one left alone deliberately: **three `dq-kinds` S3 objects carry no `count` at all**, so prod has been uploading them despite `upload_glue_scripts = false`. Gating them on it now would make Terraform **delete** them there, so only the `is_uif_stack` gate was added.

### Deploy roles, and a correction

**Owner decision: dev keeps the shared role, uat gets a dedicated one.** This
mirrors apicurio exactly, which was confirmed against the account rather than
read off a document:

| Environment | Role | Created by |
|---|---|---|
| dev | `echo-dev-github-actions` — shared with Beacon, product and apicurio | already exists |
| uat | `uif-echo-uat-deploy-role` — dedicated | `bootstrap-environment.sh uat`, Phase 3 |

**An earlier version of this section claimed the shared role needed
`s3:GetObject` on the release bucket and that the first dev apply would fail
without it. That was wrong.** `echo-dev-deploy-2`'s `StackBuckets` and
`StackBucketObjects` statements are already scoped to `arn:aws:s3:::uif-echo-dev-*`
and `.../uif-echo-dev-*/*`, which `uif-echo-dev-release-artifacts` matches. All
six actions the pipeline needs — `CreateBucket`, `PutBucketVersioning`,
`ListBucket`, `GetObject`, `PutObject`, `DeleteObject` — return **allowed** from
`aws iam simulate-principal-policy` against the live role. The mistake was
reading the absence of an explicit release-bucket grant as a gap instead of
simulating it, which is what `capabilities/infrastructure/README.md` asks for
before merging any grant.

So **no dev-side IAM change is required**, and none should be added: a
module-scoped policy on the shared role is what that README forbids, and what
apicurio's own `DEPLOY-DEV.md` records as its most serious error
(`uif-apicurio-dev-deploy-policy`, still attached as the fifth managed policy on
that role).

**What uat still needs is real.** `uif-echo-uat-deploy-role` does not exist —
the account holds ten `uif-echo-dev-*` roles and every one is a runtime role
(glue, lambda, per-function). Echo has no deployment role of its own anywhere.
Budget the apply-and-fix rounds UIFPE-496 describes: apicurio needed five, and a
green `--verify-only` is not evidence.

> **Worth carrying to whoever picks up infra remediation.** The shared role now
> holds five managed and three inline policies across four stacks, and
> apicurio's `DEPLOY-DEV.md` records that it "can delete itself, and any
> `uif-*-dev*` role", granted **four times over** — so narrowing any single
> document achieves nothing. Echo's uat role deliberately does not add to that
> pile; Echo's dev deployment still depends on it.

---

## Decisions already taken

Recorded so they are not re-litigated, and so the reasoning survives.

| Decision | Choice | Why |
|---|---|---|
| Build source | `uif-echo-dev-*` builds; uat promotes | The model's rule: dev is the only environment that builds |
| Release units | One — `infrastructure/terraform/` | Owner instruction. Gold decided separately |
| Tag namespace | `echo/v*` | Owner instruction |
| Artifact bucket | New `uif-echo-dev-release-artifacts` | Owner instruction: separate from `uif-echo-dev-data`, which holds Terraform state, pipeline configs and `code/` |
| Artifact identity | **Content-addressed S3 keys** — the key IS the sha256 | Same pattern apicurio's `ui_bundle` already ships. Identity is derivable from the bytes, the key is identical in every environment, and no version token appears in an S3 key path — so `00-core.md`'s banned-tokens rule needs no ruling |
| Glue script keys | **Per-file**, not one set-level prefix | Only changed scripts move and re-upload. Safe because `--extra-py-files` lists each file by full S3 URI and Glue **flattens** them into the job's working directory — which is why `_module_loader.load_dq_kind_module` tries `dq-kinds/<stem>.py` *then* `<stem>.py`; the fallback is the path that fires on Glue |
| Hash source — built artifacts | the bytes **as built on the runner**, verified against S3 after upload | With content-addressed keys the key *is* the hash, so the hash must exist before there is a key to upload to. Hashing S3 first is impossible for anything the build produces. Verification after upload is apicurio's pattern: `copy-image.sh` checks the expected digest before copying and reads the destination back after |
| Hash source — vendored artifacts | **the existing S3 object** | `oracle_instant_client.zip` and the GCP jars cannot be rebuilt from the repo. Apicurio's precedent is the registry image: third-party, not built, pinned by digest |
| Re-upload behaviour | `head-object` on the content-addressed key; skip if present | Unchanged content → same hash → same key → the upload is a no-op. "Only changed artifacts are written" falls out by construction |
| Hash target | The **file tree**, not the zip | A zip embeds timestamps and permissions, so hashing the archive gives different hashes for identical source across machines. `stacks/apicurio/scripts/bundle-hash.sh` is the precedent |
| `version_id` | Recorded in the manifest, **never read back** | Audit evidence only. Apicurio reads `ui.sha256` and `ui.archive`, never `version_id`, which is why a copy getting a different id in the destination is harmless |
| Lambda versions/aliases | **Not used.** Functions stay on `$LATEST` | Owner instruction. Avoids rewiring 9 `aws_lambda_permission` blocks and every event source onto an alias — a step that silently invokes `$LATEST` if missed |
| UI | **Runtime `config.json`, in Phase 1** | Owner instruction: the UI must work at cutover. Today `uif-echo-ui-deploy.sh` bakes `VITE_API_URL` in at build time, so a dev bundle carries dev's API URL and is not promotable |
| `var.upload_glue_scripts` | Kept until prod migrates | `prod.tfvars` sets it `false` and the shell script is prod's only uploader; removing the variable would change prod behaviour |

### Deviations from ADR-0019, recorded

[ADR-0019](../../../architecture-design/decisions/0019-build-tag-release-manifest-for-non-container-artifacts.md)'s
**rule** is Accepted and is honoured here: version at the deployable capability
boundary, one release tag as the only unit anyone consumes.

Its **mechanics** are explicitly "open for review", and three are departed from:

1. **Mechanic 1**, for Lambda functions, proposes `PublishVersion` plus an
   alias. This plan uses a content-addressed S3 key instead, and no alias.
2. **Mechanic 4** assumes promotion moves a Lambda alias to a version number.
   Here promotion re-points `s3_key` at the target tag's hash.
3. **Mechanic 2's example** records `layers: { version: 17, … }`. This plan
   records no layer version, because `release-echo.yml` uploads the zip but does
   not apply Terraform — `PublishLayerVersion` happens at apply time, per
   environment, so at manifest-writing time no layer version exists for that zip
   anywhere. `release-manifest.sh` guards every input with `: "${VAR:?}"`, so an
   unfillable field would fail the build. It is also environment-local, the same
   portability problem as a Lambda version number.

Mechanic 1 is followed as drafted for **Lambda layers** (`PublishLayerVersion`
still supplies the AWS-side version) and for **Glue scripts** (S3,
content-addressed). Mechanic 3 is followed in full.

`glue.tf`'s `script_location` is why content-addressing wins outright: it takes
a plain `s3://` URI with no version-id parameter, so a fixed key would be
mutable and every past release's recorded identity would silently change on the
next deploy. Identity has to live in the key path.

**Outstanding:** these deviations have not been reviewed by ADR-0019's author.
Step 1h records them; that record needs the framework owner's sign-off before
Phase 3, because by then it is deployed reality rather than a proposal.

---

## The 36 artifacts

| Class | Count | Notes |
|---|---|---|
| Lambda zips | 14 | **5 for this root** — `pipeline_controller`, `scheduler_dispatcher`, `gcs_replicator`, `silver_bq_replicator`, and `dq_check_controller`, the fifth in `data_quality.tf` — and **9 for the gold root** (UIFPE-576), one of which, `compile_preview`, is staged from a pip-built tree rather than a file list |
| Lambda layers | 4 | `gcs_client`, `silver_bq_client`, `controller_bq_client` built by `make`; `oracle_instant_client` (~127 MB) **vendored** |
| Glue scripts | 17 | 16 from `src/apps/pipeline/` (incl. `dq-kinds/`), 1 from `src/libs/bq_partition_ddl.py` |
| UI bundle | 1 | `ui/` only. `transform-ui/` is still not built here — `qe/ci.sh` records that as an open gap |

**Gold is part of THIS release unit** — owner decision 2026-09-24. `gold` is a
`{layer}` in the naming vocabulary and not a `{stack}`, the promotion model is
stack-scoped, and the two halves already share source: `pipeline_controller`'s
zip vendors `gold_resources`, `gold_frequencies`, `gold_stats` and
`gold_telemetry`, so two manifests could disagree about the same file.

The text here previously said the opposite, and drew the 27 from it.

Still excluded: **gold's Glue scripts and libraries** — `generic_gold_transform.py`,
`generic_gold_reconcile.py`, `gold_cdc.py` among them — and `gcp_glue_jars`
(vendored, `for_each` over a local jar cache).

So **the Glue set is still an explicit file list, never a directory scan** — but
for a different reason than before. Gold's scripts are sequenced into phase 1b,
where their S3 key moves from a fixed path to a content hash and the Glue jobs'
`script_location` resolution changes with it. A scan would pull them into a tag
before that work lands.
---

## Manifest layout

Stored as the annotated tag's message, as apicurio does. Every opaque identifier
quoted so a YAML parser cannot retype an all-digit value; `run_id` deliberately
unquoted because it is meant to read back as an integer.

```yaml
echo/v0.1.0:
  git_commit: "8f31a2c9b4e7d1f6a3c8e5b2d9f4a7c1e6b3d8f5"
  built_at: "2026-09-22T10:14:02Z"
  gate:
    check: stacks-ci gate
    run_id: 35628548880
    conclusion: success
  artifacts:
    bucket: uif-echo-dev-release-artifacts

    ui_bundle:
      sha256: "523a46c5cd80b03eb774d1a206773ffec46bcc90f86b67b3745342f6e31be026"
      key: ui/523a46c5cd80b03eb774d1a206773ffec46bcc90f86b67b3745342f6e31be026.tar.gz
      version_id: "HGXYGj_L3mhibVtFPXOGcJV3g77B2Pwr"

    lambdas:
      pipeline_controller:
        sha256: "d4e5f6..."
        key: lambdas/d4e5f6....zip
        version_id: "..."
      scheduler_dispatcher: { … }
      gcs_replicator: { … }
      silver_bq_replicator: { … }

    layers:
      oracle_instant_client:
        file: oracle_instant_client.zip
        sha256: "0d1e2f..."
        key: layers/0d1e2f....zip
        version_id: "..."
        origin: vendored
      gcs_client:
        file: gcs_layer.zip
        origin: built
        # sha256, key, version_id
      silver_bq_client: { file: silver_bq_layer.zip, origin: built, … }
      controller_bq_client: { file: controller_bq_layer.zip, origin: built, … }

    glue_scripts:
      bronze_ingest:
        file: bronze-ingest.py
        sha256: "a1b2c3..."
        key: glue/a1b2c3.../bronze-ingest.py
        version_id: "..."
      silver_routing:      { file: silver_routing.py, … }
      silver_transform:    { file: silver-transform.py, … }
      silver_transform_gcs_iceberg: { file: silver-transform-gcs-iceberg.py, … }
      silver_transform_bq: { file: silver-transform-bq.py, … }
      silver_transform_bq_native:   { file: silver-transform-bq-native.py, … }
      silver_reconcile:    { file: silver-reconcile.py, … }
      silver_reconcile_gcs: { file: silver-reconcile-gcs.py, … }
      silver_reconcile_bq: { file: silver-reconcile-bq.py, … }
      silver_quality_check: { file: silver-quality-check.py, … }
      analyze_table:       { file: analyze-table.py, … }
      iceberg_gcs_probe:   { file: iceberg-gcs-probe.py, … }
      module_loader:       { file: _module_loader.py, … }
      dq_kinds_init:       { file: dq-kinds/__init__.py, … }
      dq_kinds_reconcile:  { file: dq-kinds/reconcile.py, … }
      dq_kinds_null_rate:  { file: dq-kinds/null-rate.py, … }
      bq_partition_ddl:    { file: bq_partition_ddl.py, … }
```

### Two structural choices

**Keys are underscore-safe identifiers; the real path lives in `file`.** Forced,
not stylistic: a dotted field-path accessor cannot address `bronze-ingest.py`
(the dot parses as a separator) and `dq-kinds/__init__.py` adds a slash. So
`read-manifest.sh echo/v0.1.0 glue_scripts.bronze_ingest.sha256` stays clean.
Layers key on the Terraform resource name because that is what `artifact_shas`
indexes, and the zip name genuinely differs (`gcs_client` → `gcs_layer.zip`).

**`bucket` appears once; keys are relative.** A deliberate deviation from
apicurio, which inlines full `s3://` URIs for its three artifacts. At 36
artifacts that would add ~1.6 KB of duplication. `bucket` is the structural
analogue of apicurio's `bff.repository` — a separate field, not inlined. The
promote reads `bucket` as its copy source and derives the destination as
`uif-echo-<env>-release-artifacts`.

### Which fields are read

| Field | Role |
|---|---|
| `sha256` | **read** — feeds `-var artifact_shas=…` in both deploy workflows |
| `key`, `bucket`, `file` | **read** — the promote's copy source and destination filename |
| `git_commit`, `built_at`, `gate.*` | evidence |
| `version_id`, `origin` | evidence only, never read back |

---

## Phase 1 — build identity in dev

### 1a · Artifact conversion (Lambda, layers, Glue)

| Step | File | Change |
|---|---|---|
| 1a.1 | `scripts/artifact-hash.sh` *(new)* | Tree hash, 64 lowercase hex. Must carry `bundle-hash.sh`'s three guards: `-print0` / `sort -z` / `xargs -0` so a filename with a space or newline cannot split into two entries; `LC_ALL=C` to fix collation order, which otherwise varies by locale and gives a different hash on a different machine; `cd` into the directory first so every path is relative and the hash is independent of where the tree sits on disk. Must exclude `config.json` for the UI bundle |
| 1a.2 | `infrastructure/terraform/s3.tf` | New `aws_s3_bucket.release_artifacts` + `aws_s3_bucket_versioning` Enabled. **No lifecycle expiry** — the existing `artifacts` bucket has none, deliberately; `bronze` and `silver` expire noncurrent versions at 90 days, and a release artifact on such a bucket becomes unresolvable after 90 days |
| 1a.3 | `variables.tf`, `envs/dev-uif.tfvars` | `release_artifacts_bucket`; `artifact_shas` map (26 entries) |
| 1a.4 | `lambda.tf` | 4 `aws_lambda_function`: drop `filename` + `source_code_hash`; add `s3_bucket` + `s3_key`. No `publish`, no alias. **Open detail:** with `archive_file` gone there is no `source_code_hash`. Content-addressing means Terraform still detects real changes (the key moves when content moves), but out-of-band drift — someone calling `UpdateFunctionCode` by hand — becomes invisible. Decide here whether to compute the zip's `base64sha256` and keep the attribute |
| 1a.5 | `lambda.tf` | **Delete** 4 `data.archive_file` blocks (`pipeline_controller` 8 source files, `scheduler_dispatcher` 6, `gcs_replicator` 2, `silver_bq_replicator` 2). The zip-assembly logic moves into 1e's `artifacts` job |
| 1a.6 | `lambda.tf`, `locals.tf` | 4 `aws_lambda_layer_version`: `s3_key` → content-addressed. `PublishLayerVersion` still supplies the AWS-side version; drop the reliance on `terraform apply -replace` |
| 1a.7 | `glue.tf` | 11 `script_location` / `--extra-py-files` references across 10 jobs → per-file content-addressed keys |
| 1a.8 | `glue.tf` | **Delete** 11 `aws_s3_object` (10 scripts + `bq_partition_ddl_lib`). **Keep** `gcp_glue_jars` — vendored, out of scope |
| 1a.9 | `data_quality.tf` | 1 `script_location` (`generic_silver_quality_check`); **delete** 5 `aws_s3_object` (`silver-quality-check.py`, `_module_loader.py`, `dq-kinds/{__init__,reconcile,null-rate}.py`) |
| 1a.10 | `locals.tf` | `glue_scripts_prefix`, `glue_temp_prefix`, `glue_jars_prefix`, `lambda_layer_keys` |
| 1a.11 | `iam.tf` + deploy-role policy | Glue execution role and Lambda execution role need read on the new bucket; deploy role needs write. Verify with `simulate-principal-policy` against the **attached** role, not the document |
| 1a.12 | `_uif-echo-deploy-common.sh` | Delete `uif_echo_sync_glue_scripts` (~20 lines); rework `uif_echo_upload_layers`; `uif_echo_glue_scripts_prefix` becomes unused |

**`silver_routing.py` joins the managed set.** It is referenced at `glue.tf:62`
as `--extra-py-files` but has **no `aws_s3_object`** — only the shell sync puts
it in S3, so deleting the sync would break `generic_bronze_ingest`. It is also
load-bearing for parallel-stack safety: its docstring records that deriving job
names from `--env` "is what made the uif-echo-dev stack's Bronze trigger the
echo-dev stack's Silver: that job name exists, so StartJobRun succeeded against
the wrong stack and Silver reported SUCCEEDED over an empty read." Keep
`qe/pipeline/test_job_name_drift.py` green.

**Untouched:** 9 `aws_lambda_permission` blocks (they reference `function_name`,
and with no alias nothing changes), and Lambda references in `apigateway.tf`,
`data_quality.tf`, `outputs.tf`, `scheduler.tf`.

### 1b · UI runtime config

Today the bundle is environment-specific: `uif-echo-ui-deploy.sh` exports
`VITE_API_URL` and `VITE_ECHO_ENV` before `npm run build`, and
`ui/src/lib/envConfig.ts` reads `import.meta.env.VITE_API_URL`. A dev bundle
carries dev's API URL, so it cannot be promoted, and its hash would be
environment-specific — which breaks the digest-equality claim the model rests
on. (`VITE_API_URL_DEV`/`_UAT`/`_PROD` hint at one bundle serving several
environments, but `VITE_ECHO_ENV` is itself baked in, so the mechanism is
half-built.)

| Step | File | Change |
|---|---|---|
| 1b.1 | `ui/src/lib/envConfig.ts` | Fetch `/config.json` at boot instead of reading `import.meta.env` |
| 1b.2 | `ui/src/lib/api.ts`, `ui/src/lib/auth.ts` | Consume the resolved config; update the "set `VITE_API_URL` at UI build time" error messages |
| 1b.3 | `ui/src/main.tsx` | Await config before first render, and **fail loudly** if it is missing rather than looking like a CORS failure |
| 1b.4 | `uif-echo-ui-deploy.sh` | Drop both `VITE_*` exports; write `config.json` at deploy time from the `api_endpoint` output |
| 1b.5 | `qe/` | Tests for the config loader, including the missing-config path |

The UI bucket also hosts sibling apps under `m/*` (beacon, transform), which is
why the sync uses `--exclude "m/*"`. **Every publish must preserve that.**

### 1c · Build identity

`stacks/echo/VERSION` = `0.1.0`, plus:

| Script | Origin |
|---|---|
| `check-version.sh` | port verbatim |
| `next-release-tag.sh` | port verbatim |
| `resolve-deploy-mode.sh` | port verbatim |
| `assert-app-only-plan.sh` | ported, then diverged (UIFPE-555 final review) — exact-address matching is artifact-agnostic, but the action allow-list is **not**: apicurio's ECS task definition updates in place (`create\|update\|no-op`), while promoting a Lambda layer version ForceNew-replaces it (`create,delete`), because every layer-version argument is ForceNew and `lambda.tf` keys `s3_key` off the promoted digest with no `create_before_destroy`. Echo's copy allows `create,delete`/`delete,create` for `aws_lambda_layer_version.*` addresses only; apicurio's copy is unchanged, so the two scripts now differ by design rather than by drift |
| `read-manifest.sh` | port, new field paths |
| `release-manifest.sh` | port the **refusals**, new fields |
| `artifact-hash.sh` | new (1a.1) |
| `copy-artifacts.sh` | new — replaces `copy-image.sh` |
| `restore-artifacts.sh` | new — replaces `restore-bundle.sh` |

`release-manifest.sh` enforces: every `sha256` matches `^[0-9a-f]{64}$` (full
length, lowercase only, so one artifact cannot appear under two spellings);
`gate.conclusion` must equal `success` exactly; every input guarded by
`: "${VAR:?}"` so unset *or empty* fails immediately; every opaque identifier
quoted.

### 1d · Strengthen the gate

`qe/ci.sh` runs pytest only today — its own header admits "no vitest, no
`terraform fmt`, no `terraform validate`, no tflint, no trivy… roots are
unchecked." **A build tag's evidence is the CI gate**, so a tag cut against a
pytest-only gate is weak evidence. Add `terraform fmt -check`, `terraform init`,
`terraform validate`, `tflint`, `trivy config`, and `vitest` + `tsc -b` for
`ui/` — separate gates, because esbuild strips types without checking them. Pin
Terraform to `1.15.8`, matching apicurio and `stacks-ci.yml`, because a saved
plan is only valid for the version that wrote it.

### 1e · `release-echo.yml`

Four jobs, apicurio's shape:

1. **validate** — `check-version.sh` against every existing `echo/v*` tag; `fetch-depth: 0`
2. **gate** — poll the `stacks-ci gate` **job** (not the run conclusion) for up to 30 minutes, resolving the evidence commit by `--first-parent` and *verifying* the trees are identical under `stacks/` rather than asserting it
3. **artifacts** — `environment: dev`, `secrets.AWS_ROLE_ARN_DEV` (the auth `deploy-uif-echo-dev.yml` already uses). Build 4 Lambda zips, 3 layers and the UI bundle; hash each tree; `head-object` and skip if present, else upload to the content-addressed key and read back to verify; hash `oracle_instant_client.zip` and the jars **from S3**
4. **tag** — `release-manifest.sh` → `git tag -a "$TAG" -F manifest.yaml` → push → `gh release create` (not a third-party action: only `actions`, `github`, `hashicorp`, `aws-actions`, `aquasecurity`, `sonarsource`, `opentofu`, `pearson*` pass the enterprise allow-list, and one disallowed `uses:` fails the whole workflow at startup with no logs)

### 1f · Deploy the converted shape to dev

This changes how every Lambda, Glue job and the UI in a **running**
`uif-echo-dev-*` is deployed, so it is not a free step.

1. `UIF_ECHO_DEV_INFRA=plan` — read the plan. Expect: 4 functions, 4 layers, 11 jobs, 1 new bucket, ~16 `aws_s3_object` destroyed
2. `UIF_ECHO_DEV_INFRA=apply`
3. `UIF_ECHO_DEV_UI=true` — verify the UI boots from `config.json`
4. Verify a pipeline runs end to end
5. **Close the latches.** They are a deploy-window latch, not configuration

### 1g · Cut `echo/v0.1.0` and verify

```bash
git show echo/v0.1.0
git cat-file -t refs/tags/echo/v0.1.0   # must print "tag", not "commit"
```

An annotated tag is required — the manifest *is* the annotation, and a
lightweight tag has no message.

### 1h · Record the mechanics

Update ADR-0019, or add a new ADR (next free number is **0022**), recording the
three deviations above. The ADR is the design of record; if the implementation
and the ADR disagree, the ADR stops being usable by the next reader. This record
needs the framework owner's sign-off before Phase 3.

### Phase 1 exit criteria

- [ ] `echo/v0.1.0` exists, annotated, all 27 artifacts readable via `read-manifest.sh` — 27, per correction 1; this line said 26 until the fifth Lambda was found
- [ ] `uif-echo-dev-*` runs the converted shape, verified by a real pipeline run
- [ ] The UI boots from `config.json` in dev, and fails loudly when it is absent
- [ ] **Four consecutive build tags of unchanged source produce identical hashes** — §6's reproducibility requirement, and the claim the whole model rests on
- [ ] `release-manifest.sh` refuses a truncated hash, an uppercase hash, and a gate that did not pass — each proven failing before it is trusted
- [ ] `qe/pipeline/test_job_name_drift.py` green
- [ ] All latches closed
- [ ] Every job has `timeout-minutes`; every `uses:` is on the allow-list
- [ ] `var.enable_ui_waf` is still `false`, and a plan with it `true` fails only on IAM — i.e. the WAF is *ready*, not *on*. Turning it on is deliberately NOT a Phase 1 exit criterion; it needs a grant on a shared role and a confirmed CIDR range, neither of which is this phase's to decide

**Not exit criteria, but tracked against the 2026-09-30 compliance date** — both belong to whoever owns the WAF & IP Sanctions program, not to Phase 1:

1. Apply `infrastructure/deploy/uif-echo-dev-waf-grant.json` to `echo-dev-github-actions` (shared with the live `echo-dev` stack — not a private change). Re-run the simulation first; a role's policies move.
2. Confirm the allow-list. apicurio uses `159.182.0.0/16`; Echo's UI population has not been confirmed as that range, and the ACL blocks by default, so a wrong value locks out real users. The web ACL refuses at plan time on an empty list rather than taking the UI dark at apply.

Then `enable_ui_waf = true` + `ui_allowed_cidrs = [...]` in `envs/dev-uif.tfvars`. Nothing else changes.

**Correction: `trivy`'s `AWS-0011` does NOT stay open, and this section said it would.** It went from 1 finding to 0 on the commit that added `waf.tf`, with nothing suppressed — `web_acl_id = one(aws_wafv2_web_acl.ui[*].arn)` is an expression trivy cannot resolve to null, so it reads the distribution as protected while no web ACL exists anywhere. A false green on a compliance item, worse than the accurate red it replaced.

`qe/pipeline/test_ui_waf.py` is the replacement signal (8 assertions, each verified to fail under mutation). It pins the flag off, asserts the `is_uif_stack && enable_ui_waf` gate cannot be loosened to an `OR`, asserts all five `waf.tf` blocks carry the count gate, and asserts **`AVD-AWS-0011` is never suppressed** — so if the wiring changes and the finding returns, it returns honestly.

`test_the_waf_is_live_before_the_compliance_deadline` is a **deliberate deadline**: it passes until 2026-09-30 and fails after it unless some environment has the flag on. Everything else here passes while the WAF is off, which is correct but means nothing goes red for a WAF that never ships. "Accepted until the grant lands" is only honest if the acceptance expires.

---

## Phases 2–9 — outline

| Phase | Work | Inputs still needed |
|---|---|---|
| 2 · uat config | `envs/uat-uif.tfvars`, `backends/uat-uif.hcl`; `name_prefix_override = "uif-echo-uat"`, `glue_db_prefix_override = "uif_echo_uat"`, **`event_rule_state = "DISABLED"`**, all-new buckets | subnet(s); whether VPC endpoints/SGs are shared with `echo-uat-*`; `T_DCL` value |
| 3 · bootstrap | `bootstrap-environment.sh` + `terraform-iam-map.json` written; **run by a human** with privileged IAM. Creates **`uif-echo-uat-deploy-role`** (dedicated — dev keeps the shared role, see above), its env-scoped policy, the backend policy (**both** Terraform roots — main and gold), the **promotion-source policy** (the read of dev's release bucket, which no derivation can see — see "the one grant the derivation cannot predict"), the GitHub Environment with a reviewer and an `echo/v*` tag policy, and the CI secret container. `AWS_ROLE_ARN_ECHO_UAT` set **environment-scoped** on uat to that role's ARN — stack-qualified so it cannot shadow apicurio's unsuffixed `AWS_ROLE_ARN` on the same environment, and so the workflow file alone answers whose role it is. (This row used to add that the unqualified `AWS_ROLE_ARN_UAT` was product's. It is not, since PR #666 / UIFPE-656 — every domain UAT workflow now assumes `AWS_ROLE_ARN_ECHO_UAT`, and **nothing in the repository assumes `AWS_ROLE_ARN_UAT` any more**; it survives only in comments warning against it, so grep the `role-to-assume:` lines and not the whole file.) | who runs it; who reviews. **DONE:** the `uat` environment now admits eight tag patterns — `apicurio/v*`, `beacon/v*`, `echo/v*`, `event/v*` and the four domains — so the admin step this row warned about has already happened |
| 4 · infra converge | **`deploy-echo-infra.yml`** (UIFPE-576), modes `skip\|plan\|apply\|bootstrap`, dispatched **against the build tag ref** with `environment=uat`. Copies the artifacts the tag names -- 45 for `echo/v0.3.0`, which is the first tag carrying the gold set; **not** v0.1.0's 27, which predate it -- writes `artifacts.auto.tfvars.json` into both roots, then plans and applies the main root and the gold root in that order — main creates the shared Glue role, gold looks it up with `data.aws_iam_role`, so gold-first cannot even produce a plan. `bootstrap` is accepted and is identical to `apply`; no step gates on it alone. Prerequisites done locally: 4.3–4.5 and `bootstrap-environment.sh`. See 4.6–4.8 below | **DONE 2026-09-27** at `echo/v0.8.0`, after nine dispatches. Close the window afterwards: `gh variable delete ECHO_UAT_INFRA` — `unset` is not a `gh variable` subcommand, and this row said `unset` until the day it was needed |
| 5 · first promote | **`promote-echo.yml`**, modes `skip\|run`: copy, app-only apply of both roots, smoke, then `release/uat/<date>.1`. **It cannot do the first converge** — `assert-app-only-plan.sh` refuses any plan touching infrastructure, and on an empty uat the plan is hundreds of infrastructure creates, so it exits 1 before applying. Phase 4's workflow converges; this one only changes which version runs. Its release tag records `ui_bundle: NOT promoted`, and its smoke fails by design until a suite exists | a suite at `stacks/echo/qe/integration`; a caller for `UIF_ECHO_UI_BUNDLE` |
| 6 · migration | secrets → pipeline configs → secret-ARN repoint → metrics → catalogs and data. The three existing scripts are all hardcoded to dev and need parameterising | **Iceberg**: silver/gold metadata embeds absolute `s3://` paths, so a bucket copy does not move a table — reload from source, or an Iceberg-native migration? Does uat have working Oracle/GCP source access? Bronze (plain Parquet): copy or reload? Migrate metrics history or start fresh? **ADR-0021** is Proposed and you are a decider — do pipeline configs arrive as git-authored YAML or by direct DynamoDB write? Which pipelines, in what order? |
| 7 · parallel verification | Compare `uif-echo-uat-*` output against `echo-uat-*` | which pipelines; what "good" means |
| 8 · cutover | Flip `event_rule_state` to ENABLED **in the same change** that disables the old stack's rules — both stacks dispatch by `schedule.frequency`, so two enabled rule sets double-dispatch every pipeline | window; go/no-go owner; does `echo-uat`'s UI bucket host anything else, as dev's hosts `m/beacon` and `m/transform`? external consumers to repoint? |
| 9 · retire | Destroy `echo-uat-*` | — |

---

## Phase 4 in detail

The one-line outline above was written before anyone tried it. Two things it did
not anticipate: the deploy path did not accept `uat-uif` at all, and the
converge cannot precede the promote.

### 4.1 — Admit `uat-uif` (done, UIFPE-555)

Echo's deploy path grew around one uif stack, so `dev-uif` appeared as a literal
in nine places. Fixed 2026-09-24; [`test_uif_env_gates.py`](../qe/pipeline/test_uif_env_gates.py)
pins all of them and fails on any new `== "dev-uif"` equality gate. Recorded
because the *shape* of the misses matters more than the count — they failed in
three different ways:

- **Hard stop, misleading message.** `uif_echo_validate_env` and the Makefile's
  `ENVS` held the same list independently and disagreed, so the shell path died
  at preflight talking about `ENV` while `make` died later talking about `ENVS`.
- **Silently not running.** `uif_echo_import_phase5_artifacts` was
  parameterised to accept any `-uif` env, but its only caller still gated on
  `dev-uif`, so the fix was unreachable. A callee that handles a case its caller
  never passes it is not fixed, and nothing about the code looks wrong.
- **Wrong path taken successfully.** `uif_echo_sync_glue_scripts` returned early
  for `dev-uif`; `uat-uif` fell through and would have synced Glue scripts into
  `uif-echo-uat-data/jobs/` — the state bucket — and reported success.

### 4.2 — Delete the stale `artifacts.auto.tfvars.json`

It is gitignored, it holds **dev's** hashes, and `*.auto.tfvars.json` is loaded
automatically for every plan and apply. Left in place it pins uat's Glue jobs and
Lambdas to dev's artifact SHAs, and nothing warns you.

### 4.3–4.4 — Init, then import the data bucket

`make init ENV=uat-uif`, then `uif_echo_import_phase5_artifacts uat-uif` to
import `aws_s3_bucket.artifacts` = `uif-echo-uat-data`, which
`bootstrap-environment.sh` pre-creates because `terraform init` needs the state
bucket to exist before Terraform can evaluate the resource declaring it. Skip the
import and the apply fails `BucketAlreadyOwnedByYou`, naming the bucket and
nothing naming the missing import. Look for `Imported aws_s3_bucket.artifacts`.

### 4.5 — Targeted apply for the release bucket

`uif-echo-uat-release-artifacts` is declared by Terraform, and nothing can be
copied into it until it exists:

```bash
terraform -chdir=stacks/echo/infrastructure/terraform apply \
  -var-file=envs/uat-uif.tfvars \
  -target=aws_s3_bucket.release_artifacts \
  -target=aws_s3_bucket_versioning.release_artifacts
```

Versioning is not optional — `stage-artifacts.sh` fails closed without a
version id. A targeted apply is an escape hatch, not a pattern: that bucket has
no dependencies and nothing else is half-created by it.

### 4.6–4.8 — One workflow, not three manual steps

**Rewritten 2026-09-25.** This section previously told you to run
`copy-artifacts.sh` by hand for 4.6 and to converge locally for 4.7–4.8. Both
are wrong now: UIFPE-576 landed **two** workflows, and between them they do all
three steps.

- **`deploy-echo-infra.yml`** — modes `skip|plan|apply|bootstrap`. Copies the
  artifacts the tag names, writes `artifacts.auto.tfvars.json` into both roots,
  then plans and applies the main root and the gold root, in that order.
- **`promote-echo.yml`** — modes `skip|run`. The steady state: copy, app-only
  apply, smoke, release tag.

**They are sequential, not alternatives, and `promote-echo.yml` cannot do a
first converge.** Its `assert-app-only-plan.sh` refuses any plan touching
infrastructure, and on an empty uat the plan is hundreds of infrastructure
creates — so it exits 1 and refuses before applying anything. The infra
workflow is what converges; the promote only changes which version runs in an
already-converged environment. The infra workflow's own header puts it well:
"those are different authorities over the same state."

#### The sequence

Prerequisite, already done: `bootstrap-environment.sh` (the environment, the
derived deploy policy, the role secret), 4.3–4.5 locally (init, the data-bucket
import, and the targeted apply that created the release bucket). **4.5 was
genuinely required** — the workflow copies artifacts *before* its first plan, and
`copy-artifacts.sh` refuses when the destination bucket does not exist.

**Step 1 — read the plans.**

```
gh variable set ECHO_UAT_INFRA --body plan
```

Then dispatch `deploy-echo-infra.yml` with `environment=uat`, **against the
`echo/v0.3.0` tag ref**. It copies the 45 artifacts, writes the hashes into both
roots, and plans both. Nothing is applied.

**Not `echo/v0.1.0`, and the difference is the whole gold root.** v0.1.0 carries
27 artifacts — 17 Glue scripts, 5 Lambdas, 4 layers and the UI bundle — and was
cut before the gold artifacts existed. v0.3.0 carries **45**: the same set plus
9 gold Glue scripts (`gold_transform`, `gold_cdc`, `gold_reconcile`,
`gold_stats`, `gold_telemetry`, `gold_report`, `gold_frequencies`,
`gold_ops_export`, `gold_ops_summarize`) and 9 gold and Transform-UI Lambdas
(`gold_dispatcher`, `gold_serving_api`, `gold_serving_sync`,
`gold_alert_notify`, `gold_announce`, `submit`, `compile_preview`,
`grain_probe`, `ai_draft`).

Converging both roots from v0.1.0 would leave every gold key resolving to
`ARTIFACT-SHA-NOT-SUPPLIED`, which is the exact condition `artifact_guard.tf`
hard-stops on — so the gold plan would be refused rather than applied. That is
the guard working, but it is a wasted deploy window, and an earlier version of
this section named v0.1.0 because it was written before v0.3.0 was cut.

**Check the tag rather than trusting this document**, since the set grows:

```
stacks/echo/scripts/read-manifest.sh <tag> --list glue_scripts | wc -l
stacks/echo/scripts/read-manifest.sh <tag> --list lambdas      | wc -l
stacks/echo/scripts/read-manifest.sh <tag> gate.conclusion
```

Use the newest tag whose gate reads `success` and whose `glue_scripts` list
contains the `gold_*` names. v0.3.0's gate is run `36113749402`.

**Dispatching against a branch instead of the tag silently skips the copy.**
That step is gated on `startsWith(github.ref, 'refs/tags/')`, so a branch
dispatch produces no `artifacts.auto.tfvars.json`, and
`terraform_data.artifact_shas_present` then fails the plan. That guard is the
only thing between you and the failure described below, so read its message
rather than working around it.

**Step 2 — apply.**

```
gh variable set ECHO_UAT_INFRA --body apply
```

and dispatch again from the same tag. `bootstrap` is accepted too and is
**identical**: no step in that workflow gates on `bootstrap` alone. Apicurio's
`bootstrap` created its ECR repository first; Echo's equivalent chicken-and-egg
is handled outside the workflow by 4.3–4.5, so the mode is vestigial here. Use
`apply` and do not read `bootstrap` as doing something extra.

**Step 3 — close the window.**

```
gh variable delete ECHO_UAT_INFRA
```

Leaving it on `apply` means the next tag push converges uat unattended.

#### What to check in the plan

`name_prefix_override = uif-echo-uat`, `glue_db_prefix_override = uif_echo_uat`,
`event_rule_state = DISABLED`, and `create_secretsmanager_endpoint = false`.
That last differs from `uat.tfvars` deliberately: AWS permits one private-DNS
endpoint per service per VPC and `echo-uat` already owns it.

Expect the plan to be **larger than Phase 2 described**. `develop` moved 116
commits while this work was open — UIFPE-543's five notification routes,
UIFPE-544's gold statistics, UIFPE-608's staging changes — and the gold root is
now in scope as a second state.

#### Why the roots run in that order, and why the artifacts come first

Neither is a preference, and both are recorded in the workflow's header:

- The **main root creates the shared Glue role**; the **gold root looks it up**
  with `data.aws_iam_role`. A data source that resolves to nothing fails the
  *plan*, so a first converge with gold first cannot even produce one.
- **Nothing in a uif-echo stack reads its code from this repository.** Every
  Lambda, layer and Glue script resolves a content-addressed key, so the hashes
  must exist before the first plan.

`artifact_guard.tf` exists because skipping them **broke dev twice on
2026-09-24** (runs `36025076885` and `36034078478`): the apply deleted each
Lambda layer version before failing to publish its replacement, leaving live
functions on versions that no longer existed, and repointed all ten bronze and
silver Glue jobs at a missing script. Until then the only thing guarding it was
a human noticing the sentinel in a long plan. It is now a hard stop at plan
time.

That is the same `ARTIFACT-SHA-NOT-SUPPLIED` failure this document reasoned
about in the abstract before the promote work started — except it actually
happened, in dev, twice.

#### The second pass

Some values are not knowable until after the first apply — the CloudFront domain
and the API endpoint that feeds UI runtime config. Apicurio needed **five**
apply-and-fix rounds here (UIFPE-496). Budget for it, and expect to return to
`bootstrap-environment.sh` when an apply names an action the derivation did not
predict.

#### The one grant the derivation cannot predict — read this before bootstrapping prod

The first uat converge cleared the bootstrap's verify step with **225 of 225
actions allowed**, cleared the reviewer gate, assumed the role, and died at the
artifact copy with `cannot list s3://uif-echo-dev-release-artifacts`.

A promoted environment reads its artifacts out of the environment that **built**
them, and `uif_echo_stage_release_artifacts` refuses to stage anywhere but dev.
That bucket appears nowhere in uat's Terraform, so `required-actions.py` cannot
see it, and `s3_stack_buckets`' scope is `arn:aws:s3:::{prefix}-*` — this
environment's own buckets only.

**A verification built from a derivation can only confirm the derivation.** The
one grant that could not be derived was the one it could not check, which is why
225/225 was simultaneously true and worthless.

The near-miss worth knowing: that scope's note in `terraform-iam-map.json` said
the grant already reached `uif-echo-dev-release-artifacts` without a change.
True — *for dev*, where `{prefix}` happens to **be** `uif-echo-dev`. Correct in
one environment by coincidence is the hardest kind of wrong to see, because the
evidence for it is real.

Now handled by step **`3b/6`** of the bootstrap, which writes a second,
hand-written `{prefix}-promotion-source-policy` — separate from the derived one
because that one is regenerated wholesale every run, so a statement added inside
it would be silently dropped on the next bootstrap. Read-only, four actions,
both ARN forms. **Skipped for dev**, which *is* the source. Verified by five
explicit probes rather than by the derivation, giving **231/231**.

Two of those four actions are ones apicurio does not need: `copy-artifacts.sh`
pins a version id on every copy — that is what proves the promoted bytes are the
ones the release gate saw — and a version cannot be pinned without listing it.
Apicurio copies with `crane` and `aws s3 cp`, which never version-list.

**For prod:** nothing here is prod-specific. `bootstrap-environment.sh prod`
grants it the same way; the source stays dev's bucket, because dev stays the only
environment that builds.


### 4.9 — One manual step that the apply cannot catch

`echo-uat`'s Secrets Manager VPC endpoint security group must admit 443 from the
new stack's SG. Because uat reuses that endpoint (see 4.6–4.8), a missing rule leaves the
apply **green** and fails `GetSecretValue` at run time with a timeout. This is
UIFPE-543's shape exactly, where the gold notifier needed a seat on the same SG.

---

## Traps carried forward from apicurio's first run

Each cost real time and none was found by review.

- **A `GITHUB_TOKEN`-pushed tag triggers nothing.** The tag exists, no workflow runs. Deliberate anti-recursion behaviour.
- **`actions/checkout` on a tag ref discards the annotation**, producing a lightweight tag locally. Re-fetch with `git fetch --force origin "+refs/tags/<t>:refs/tags/<t>"` and assert the object type.
- **A tfvars change does not arrive on a re-run.** Tracked files are frozen into the tag's commit: changing a variable costs a re-run, changing a file costs a new tag.
- **Never leave both latches open for one tag push.** Both workflows enter the same concurrency group, and GitHub keeps at most one *pending* run per group — the second is cancelled with no message.
- **Job logs are unreachable until a job ends**, so an unbounded hang shows nothing. `timeout-minutes` is what makes such a failure readable.
- **A green `--verify-only` is not evidence.** Apicurio needed **five** rounds of IAM apply-and-fix; the derivation only sees resources the stack manages and is blind to post-create reads and tags. Budget the rounds (UIFPE-496).
