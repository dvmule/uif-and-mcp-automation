# Echo Definition of Done

Same shape as `stacks/harbor/CHECKLIST.md` and `stacks/beacon/CHECKLIST.md` —
Design → Build → Ship → Operate → Trace. A stack is not "done" for a given Story
until every applicable box is checked. The starred (★) items are the ones
`.github/workflows/stacks-ci.yml` is meant to enforce — but **that workflow is
currently `disabled_manually`** (disabled 2026-08-28, 53 minutes after its only
run), so nothing is enforcing them today. See ADR-0015 decision 1.

> **Step zero is closed. ADR-0015 is Accepted (2026-09-09)** — `stacks/echo/` is
> Echo's home, superseding ADR-0004's premise of two repos with two owners
> (its CP-1 – CP-6 stand). The code arrived via UIFPE-355 so the decision could
> be taken against a concrete diff rather than a description.
>
> **Nothing in that ADR now blocks a responsible merge.** Ownership is settled
> — the author owns `stacks/echo/` (D10) and `.github/CODEOWNERS` carries an
> explicit entry. The Gold coupling is **deferred, not answered** (D11): its
> owner has still not been consulted and Echo's Gold rules still dispatch
> nothing. Two operational items remain open — re-enabling `Stacks CI`, and
> booking the apply window.

## Step zero — blocking

- [x] **ADR-0015 drafted** —
      `architecture-design/decisions/0015-echo-into-the-monorepo.md`. It
      supersedes ADR-0004's *premise* (two repos, two owners),
      not its decisions CP-1 – CP-6, which are about how Gold runs independently
      and are unaffected by where Echo's source sits.
- [x] **ADR-0015 accepted** (2026-09-09) — the move. Eleven decisions are
      recorded as taken (D1–D11). **Two remain open**, both operational, listed
      under Decision Required. The two that used to gate the merge:
  - [x] **Ownership** (D10) — the author owns `stacks/echo/`;
        `.github/CODEOWNERS` has an explicit `/stacks/echo/` entry, placed last
        so it actually wins. Verifying it found that **all five `@uif-*`
        placeholder handles 404**, so until this entry no path in the repo had
        working coverage at all.
  - [~] **The Gold coupling** — **parked** (D11), not resolved. ADR-0004 CP-3
        reuses this stack's Glue role and CP-4 shares its scripts bucket, so the
        two repos stay coupled at the infrastructure layer. Effort is on Bronze
        and Silver; the evidence is preserved in the ADR.

## Design

- [x] Architecture and solution design recorded — `CLAUDE.md`, plus
      `docs/echo-component-diagram.md` and ADR-0001/0016.
- [x] Data definition and data contract — `src/schemas/gold/product/v1/master.json`
      with the mapping manifest beside it.
- [x] ★ Schema registered **before** implementation (`src/schemas/`).
- [ ] Security posture stated. Carried over unreviewed from relay-echo; the
      Lake Formation dual-layer model and the secret-prefix grants need writing up.
- [ ] Observability signals defined (`infrastructure/observability/`). Two
      CloudWatch dashboards and one alarm exist in `cloudwatch.tf`; **the alarm
      has no `alarm_actions`, so it fires and notifies nobody.**

## Build

- [x] Business logic and capability surfaces under `src/` —
      `src/apps/{pipeline,control_panel,lambdas,cli}/`, `src/libs/`, `src/schemas/`.
- [x] ★ **No container images** — the API and the four executors ship as Lambda
      zips, the transforms as Glue scripts. **Rule-3 deviation, same as Beacon's
      and closed on the same grounds:** this stack ships zero images.
      See the CI note below.
- [x] `infrastructure/terraform/` present and valid — 16 flat files, one per AWS
      service, `terraform fmt` clean. No `kubernetes/` or `helm/`: this workload
      is Glue and Lambda, not EKS.
- [ ] ★ `src/api/openapi.yaml` — **does not exist.** 28 API Gateway routes are
      live with no registered contract; the UI's Axios client is currently the
      only description of it.
- [x] ★ **Unit + contract tests in `qe/` — GREEN.** 373 passed, 0 failed
      (UIFPE-355), split into `qe/{pipeline,control_panel,lambdas,libs}/` with a
      single `qe/conftest.py` as the sys.path authority. Arrived with 16
      failures and two modules documented as hanging forever; all fixed. Three
      root causes, not sixteen bugs:
  - 15 tests: fixtures omitted `extract`, and `pipeline_controller.py:398`
    defaults an absent `extract.mode` to `"incremental"`, which then requires a
    `watermark_col` — so requests 400'd before reaching the behaviour under
    test. The `ensure_*` "called 0 times" failures were symptoms of that same
    400. Settled as a test problem because the same file has a *passing* test
    asserting the rejection is correct, and PUT is a documented full replace.
  - 1 test asserted `num_workers == 1`, which AWS Glue rejects on any G-series
    worker type (`sizing_policy.py:29`). It was asserting a config that cannot
    launch.
  - `moto` was never declared as a test dependency. Installing it fixed 20
    collection errors AND the two "hanging" modules, which do not hang — without
    moto they fell through to real boto3 clients retrying against AWS.
  - Plus `os.environ` pollution across files: `test_home_summary.py` set
    `METRICS_TABLE` at module level while two siblings overwrite it in `setUp`,
    so the handler queried an empty table. Those four tests passed alone and
    failed in a directory run. Now set per-test via a base class.
- [ ] **Known, unresolved: a single `pytest stacks/echo/qe` over all files dies
      with `INTERNALERROR> MemoryError`.** Per-suite is clean, and `pytest.ini`
      says per-suite is the repo convention, so CI runs it that way — but the
      root cause is not established. Hypothesis: moto's botocore patching
      against each file independently swapping `boto3.resource` for a
      `MagicMock` at import.
- [ ] **Latent bug surfaced by the move: the DQ dispatcher's kind imports cannot
      work on Glue.** `generic_silver_quality_check.py` imported
      `from glue_jobs.dq_kinds.reconcile import ...`, which resolved in
      relay-echo's tests only because `glue_jobs/` was a namespace package
      relative to the old working directory. At runtime,
      `data_quality.tf`'s `--extra-py-files` ships the three files
      **individually**, so Glue puts them on the path as flat top-level modules —
      `reconcile` and `null_rate`, not `dq_kinds.reconcile`. The import is now
      `from dq_kinds.reconcile import ...`, which fixes the tests and is
      **neutral at runtime** (neither spelling resolves there), so this is not a
      regression — but the DQ job's reconcile and null-rate kinds are very likely
      falling into their `except ImportError` path in every real run. Confirm
      against a live run, then either ship `dq_kinds` as a zip or flatten the
      imports to match what `--extra-py-files` actually provides.
- [x] UI implemented in `ui/` — React 19 + TS + Tailwind. **Deviation:** its 113
      vitest files stay in collocated `__tests__/` rather than moving to `qe/`.
      Beacon's "tests only in `qe/`" rule is easy for it to keep because it ships
      no UI tests at all; moving 113 files away from their components would trade
      real maintainability for uniformity.
- [ ] `src/requirements.txt` versions pinned. The package list is derived from
      actual imports; relay-echo never had this file, so the versions must be
      confirmed against the deployed layer zips rather than invented.

### Declared deviations from Beacon's layout

| Deviation | Why |
|---|---|
| No `src/Dockerfile` | Zero images, as above — declared in `src/NO-IMAGE.md`, which `stacks-ci.yml`'s build-contract check now accepts in its place (amended under UIFPE-355; Beacon carries the same declaration). The gate still fails closed: shipping neither is an omission. Note it is academic today — the workflow is `disabled_manually`. |
| `infrastructure/terraform/Makefile` kept | Beacon drives env selection with raw `terraform init -backend-config=`. The Makefile is a usable wrapper; keeping it is cheaper than retraining. |
| `data_quality.tf` not split by service | It is a feature bundle — DynamoDB table, Lambda, Glue job and API routes — not one AWS service. Splitting it four ways would scatter one feature. |
| `infrastructure/deploy/uif-echo-*-deploy.sh` is bash | Beacon's deploy tooling is PowerShell. Not worth converting. |
| `src/apps/cli/` exists | Beacon has no CLI app. Echo has 16 durable registration/analysis tools that need a home and import `src/libs/`. |
| UI tests collocated | See above. |

## Ship

- [ ] ★ CI green — `stacks-ci.yml` picks this stack up automatically by folder
      name. Its "Build / test / scan" step is still the placeholder no-op and
      needs this stack's real steps wired in.
- [ ] A CD workflow promotes Dev → UAT → Prod.
      `.github/workflows/deploy-uif-echo-dev.yml` **exists** and is now the
      only workflow that deploys this stack (shared OIDC role, gated off by
      default via `UIF_ECHO_DEV_INFRA` / `UIF_ECHO_DEV_APPS`; its own header
      records the remaining Lambda-layer bootstrap blocker). The legacy
      `deploy-echo-dev.yml` was retired under UIFPE-510, once its `echo-dev-*`
      estate had been destroyed and its Terraform state held zero resources.
      **uat and prod workflows do not exist yet.**
- [x] **The move is verified safe.** Run 2026-09-09 against
      `echo/dev/terraform.tfstate`. The pre-rename tree plans to
      `7 add / 38 change / 7 destroy` — not empty, so the original
      `uif-relay-echo` tree was planned against the *same state* as a control. It
      produces the **identical 3 Lake Formation replacements**, which proves
      those are pre-existing drift and not caused by the move. Everything the
      move itself causes is benign: in-place `source` path updates on S3 objects
      (same bytes, new repo path) plus 4 `aws_lambda_layer_version` replacements
      cascading from those re-uploads — layer versions are immutable and
      accumulate. **No bucket, table, Glue job, Lambda or EventBridge rule is
      touched by the relocation.**
- [x] **Pre-existing Lake Formation drift — DECIDED 2026-09-09: accept the
      narrowing.** AWS holds `ALTER`/`DROP` and several grant options on the
      three analyst database grants that `lakeformation.tf` does not declare.
      The apply narrows them to config. No change needed; recorded so the
      permission reduction is a choice rather than a surprise in the plan.
- [ ] **Two config defects the window planning surfaced** — neither caused by
      this work, neither fixed by it:
  - [ ] One enabled pipeline has `schedule.frequency = "12 hr"`, which matches
        no EventBridge rule (there is no 12-hour rule, and the string carries a
        space). It is enabled and has never been dispatched.
  - [ ] All 5 enabled Gold pipelines use `frequency = "gold-24h"`, matching none
        of Echo's three Gold rules. That is consistent with **ADR-0004 CP-2**
        moving Gold's dispatcher to `uif-dal-gold` — and with **CP-5**, whose
        runbook says the platform team should *disable those three rules and
        remove Gold from their state*. They are still here, dispatching
        nothing. Ask whether they should be renamed or deleted.
- [ ] **Apply the Tier 1 rename via
      `infrastructure/deploy/tier1-rename-runbook.md`.** Measured, not estimated:
      the 47 renames cascade to **176 replacements** (33 rule targets, 35 Lambda
      permissions, 14 LF grants, 5 SG rules, 22 S3 objects). All 33 EventBridge
      rules are destroyed and recreated, so **Bronze ingestion stops for the
      duration** — this is a maintenance window. Pick a slot outside
      05:30–12:00 UTC.
      Correcting an earlier claim in this file's history: the API Gateway is
      updated **in place** (id `dqtw191jq9` survives), so the invoke URL does
      **not** change and the UI does not need rebuilding for it.

## Operate

- [ ] Operational ownership registered in JSM.
- [ ] Runbook in `infrastructure/observability/`.
- [ ] **Get a known-good copy of the Oracle Instant Client, and stage it.**
      The 127 MB client backing `uif-echo-dev-oracle-layer` is a licensed vendor
      download, has never been in version control, and cannot be rebuilt from
      this repo. Run the runbook in
      `infrastructure/deploy/layers/README.md` before anyone applies.
      **Corrected 2026-09-09** — this was previously written as "the rename
      destroys it", which is wrong: `force_destroy` is unset and `filemd5()`
      fails at plan time, so a missing zip aborts the plan rather than deleting
      anything. The real risks are narrower and worth stating accurately:
  - [ ] **It blocks the CI plan.** A clean runner has no local zips, so
        `filemd5()` fails at plan time. This is the one hard blocker.
  - [ ] **A stale local zip silently overwrites the good S3 copy.** On apply,
        Terraform re-uploads from the deploying machine. A wrong or partial file
        in `deploy/layers/` becomes the layer content and the known-good object
        it replaced is gone.
  - [ ] **The client is unreproducible regardless of any rename** — a licensed
        download with perhaps two copies in existence. Without it the
        control-plane Lambda loses Oracle access, and with it table analysis and
        pipeline registration.
- [ ] **Then make the bucket the source of truth.** Switch the four
      `aws_lambda_layer_version` resources to `s3_bucket` / `s3_key` and drop the
      four `aws_s3_object` uploads, so Terraform stops needing local files.
      Ordering matters — deleting an `aws_s3_object` from the config makes
      Terraform want to **delete the object**, so pair the change with
      `terraform state rm` for each of the four before applying. That ends the
      "only copy is on someone's laptop" failure mode permanently.

## Trace

- [x] Work links to Jira — UIFPE-355.
- [ ] ADR-0015 recorded (step zero).

---

## Not in this migration

- **The rename to `uif-echo-{env}-*`.** Tracked separately. Deliberately
  sequenced after the move so that an empty `terraform plan` can prove the move
  in isolation — doing both at once makes a plan unreadable, because a
  rename-induced replacement is indistinguishable from a move-induced one.
- **The 181 pipeline drafts.** Configuration stays in DynamoDB; the live configs
  are exported into `domains/` after the rename, so the repo records current
  state rather than a stale snapshot. The export must exclude runtime-owned
  fields — watermarks especially — because every `register_*` script does a full
  overwrite with no runtime-field protection, which would make committed
  watermarks a loaded gun.
- **Anything GCP.** GCS buckets, BigQuery projects, datasets and tables are out
  of scope by decision: the UIF Resource Name Lookup is AWS-only, so there is
  nothing for them to conform to.
