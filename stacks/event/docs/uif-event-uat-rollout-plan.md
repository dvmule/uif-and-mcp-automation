# UIF Event uat rollout — adopting the stack promotion model

This plan gets the shared eventing stack (`stacks/event`) into **uat** the same
way Echo got there: dev is the only environment that builds, a build tag
(`event/vX.Y.Z`) records exactly which bytes were built, and uat receives those
bytes rather than rebuilding them from whatever is on `develop`. It matters now
for two reasons. The event stack has never been deployed to uat, and that
absence is what blocks product's uat Terraform (UIFPE-669 — product's
`event-domain` module reads `uif-event-uat-dlq-recorder`, a Lambda that does
not exist, so its plan fails). And UIFPE-690's `promote-event.yml` cannot
consume the tags `release-event.yml` already cuts, because those tags carry no
artifacts.

It is modelled on [`stacks/echo/docs/uif-echo-uat-rollout-plan.md`](../../echo/docs/uif-echo-uat-rollout-plan.md)
and the model it adopts, [`capabilities/delivery/stack-promotion-model.md`](../../../capabilities/delivery/stack-promotion-model.md).
Where Echo's plan already explains a mechanism, this one links to it rather
than repeating it.

## Status: Phase 1 written on the branch, not yet deployed

Tracked on **UIFPE-744** (Story under epic UIFPE-35, created 2026-09-30).
Branch `feature/UIFPE-744-event-uat-deployment`, cut from `develop` at
`464af0d1`.
Deployments are run by hand from GitHub Actions (Reyas).

| Phase 1 step | State |
|---|---|
| Scripts (hash, zip, stage, manifest write/read) | written, tested (`qe/test_release_artifacts.py`) |
| Terraform conversion, `release.tf`, `artifact_guard.tf` | written; `terraform fmt -check` and `terraform validate` clean on 1.15.8 (2026-09-30). The `for_each` import-on-a-singular-resource pattern in `release.tf` was probed with a local plan: imports when the set has one element, creates when empty |
| `deploy-event-dev.yml` staging + first-run bucket creation | written |
| `release-event.yml` artifacts job | written, separate commit (UIFPE-690's file) |
| `resolve-deploy-mode.sh` (fixes the red promote runs) | written, separate commit |
| Dev IAM for `uif-event-dev-*` buckets on the shared role | **unverified** — simulate before the first apply (below) |
| First dev plan / apply, next `event/v*` tag | not run |

### Before the first dev run

The shared `AWS_ROLE_ARN_DEV` role has only been simulated against
`uif-echo-dev-*` buckets (echo plan, *Deploy roles*). Check the new name:

```bash
aws iam simulate-principal-policy --policy-source-arn <AWS_ROLE_ARN_DEV role arn> \
  --action-names s3:CreateBucket s3:PutBucketVersioning s3:PutBucketPublicAccessBlock \
    s3:PutEncryptionConfiguration s3:ListBucket s3:GetObject s3:PutObject \
  --resource-arns arn:aws:s3:::uif-event-dev-release-artifacts \
    "arn:aws:s3:::uif-event-dev-release-artifacts/*" \
  --query "EvaluationResults[].[EvalActionName,EvalDecision]" --output table
```

Then, in order: `deploy-event-dev.yml` with `infra=plan` (stages with
`--hash-only` because the bucket does not exist yet — read the plan: 4
Lambdas updated in place to `s3_key`, 2 Glue jobs repointed, 4 bucket
resources created, 2 `aws_s3_object` in `uif-echo-dev-gold-artifacts`
destroyed, 1 `terraform_data` created); then `infra=apply` (creates the bucket
by targeted apply, stages for real, applies); then one publish run end to end.
Only after that, bump `stacks/event/VERSION` so `release-event.yml` cuts the
first tag that carries artifacts.

---

## What existed before this branch (UIFPE-690, 2026-09-30)

| Piece | State |
|---|---|
| `stacks/event/VERSION` | `0.10.0`; tag `event/v0.10.0` cut |
| `scripts/check-version.sh` | echo's validator, default component changed to `event` |
| PR version check | `stacks-ci.yml` finds `stacks/event/VERSION` automatically |
| `release-event.yml` | validate → gate → annotated tag → GitHub Release. **No artifacts job**; the manifest records only commit and gate |
| `promote-event.yml` | echo's control flow, skip by default. Calls six scripts under `stacks/event/scripts/` that do not exist, so **every run fails at the resolve step** (`resolve-deploy-mode.sh`, line 206) — nothing is applied, but the run is red |
| GitHub `uat` environment | already admits `event/v*` tags (recorded in echo's plan, Phase 3 row) |

## How event differs from Echo — why this is smaller

| | Echo | Event |
|---|---|---|
| Terraform roots | 2 (main + gold), ordered | **1** |
| Artifacts | 45 (Lambdas, layers, Glue, UI) | **6** — 4 single-file Lambdas, 2 Glue Python-shell scripts |
| Vendored artifacts | Oracle client, GCP jars | **none** — every artifact is built from `src/` |
| Lambda layers | 4 (ForceNew on promote) | **none** |
| UI bundle / runtime config | yes | **none** |
| VPC, security groups, endpoints | yes (4.9's manual SG rule) | **none** |
| Legacy stack to coexist with | `echo-uat-*` → gated on `is_uif_stack` | **none known** — the stack only ever existed as `uif-event-*` (verify, open question 6) |

The last row is the biggest simplification: echo had to keep two deployment
shapes alive side by side (correction 5 in its plan). Event can convert
outright.

### The 6 artifacts

| Class | Name | Source before Phase 1 |
|---|---|---|
| Lambda | `pipeline_controller` | `src/handler.py` — `controller.tf` `archive_file` |
| Lambda | `dlq_recorder` | `src/dlq_recorder.py` — `dlq-recorder.tf` |
| Lambda | `subscription_provisioner` | `src/subscription_provisioner.py` — `provisioner.tf` |
| Lambda | `event_redrive` | `src/event_redrive.py` — `redrive.tf` |
| Glue script | `event_fabric_processor` | `src/glue/event_fabric_processor.py` — `glue.tf`, fixed key in **echo's** `uif-echo-{env}-gold-artifacts` |
| Glue script | `event_fabric_recovery` | `src/glue/event_fabric_recovery.py` — `recovery.tf`, same bucket |

**Not artifacts:** the `event-domain` module's mapping and seed uploads. They
are domain data, applied from each domain's own root, and stay outside the
event release unit (see open question 5).

---

## Decisions carried over from Echo unchanged

Each is argued in echo's plan (*Decisions already taken*); not re-litigated here.

- Dev builds; uat promotes. Tag namespace `event/v*`.
- **Content-addressed S3 keys** — the key is the sha256, identical in every
  environment. Glue's `script_location` takes a plain URI with no version id,
  so a fixed key is mutable and a past tag's recorded identity would silently
  change; today's fixed key in `glue.tf:6-11` is exactly that problem.
- Hash the file tree, not the zip; deterministic zips (`zip-artifact.sh`).
- `head-object` and skip if present; read back after upload.
- Lambdas stay on `$LATEST`, no aliases.
- `version_id` recorded, never read back.
- Per-environment release bucket; uat copies from dev's with the version id
  pinned.
- A dedicated uat deploy role, not a shared one.

---

## Files — new, and existing ones this changes

Most of the work is **new files**. Five existing files must change; two of them
are UIFPE-690's and should be coordinated with its author before editing.

| File | New / changed | Phase |
|---|---|---|
| `scripts/artifact-hash.sh`, `zip-artifact.sh`, `stage-release-artifacts.sh` | new (port from echo, event's 6-row set) | 1 |
| `scripts/release-manifest.sh`, `read-manifest.sh` | new — **adapted**, not verbatim: echo's `read-manifest.sh --dump` requires `layers` and `ui_bundle` classes event does not have | 1 |
| `infrastructure/terraform/release.tf` | new — `uif-event-{env}-release-artifacts`, versioned, no expiry | 1 |
| `infrastructure/terraform/artifact_guard.tf` | new — plan-time stop on `ARTIFACT-SHA-NOT-SUPPLIED` | 1 |
| `controller.tf`, `dlq-recorder.tf`, `provisioner.tf`, `redrive.tf` | **changed** — `archive_file` removed; `s3_bucket` + `s3_key` | 1 |
| `glue.tf`, `recovery.tf` | **changed** — `aws_s3_object` removed; `script_location` → release bucket, content-addressed | 1 |
| `variables.tf`, `locals.tf` | **changed** — `artifact_shas`, `lambda_code_hashes`; the bucket name is derived in `locals.tf` (`uif-event-{env}-release-artifacts`), not a variable | 1 |
| `.github/workflows/deploy-event-dev.yml` | **changed** — stage artifacts before plan | 1 |
| `.github/workflows/release-event.yml` | **changed — UIFPE-690's file** — add the `artifacts` job; manifest gains `artifacts:` | 1 |
| `envs/uat.tfvars`, `backends/uat.s3.tfbackend` | new | 2 |
| `infrastructure/deploy/bootstrap-environment.sh`, `required-actions.py`, `terraform-iam-map.json` | new (port echo's) | 3 |
| `.github/workflows/deploy-event-infra.yml` | new — converge from a tag | 4 |
| `scripts/resolve-deploy-mode.sh`, `copy-artifacts.sh`, `app-only-addresses.sh`, `assert-app-only-plan.sh`, `next-release-tag.sh` | new | 4–5 |
| `.github/workflows/promote-event.yml` | **changed — UIFPE-690's file** — only if its script calls need adjusting to the adapted manifest | 5 |
| `qe/integration/` | new — smoke suite promote needs | 5 |
| `qe/pipeline/…` tests pinning scripts and workflows | new | 1–5 |
| `README.md`, `CHECKLIST.md` | changed — the "uat/prod: dev only" box | end |

`resolve-deploy-mode.sh` alone would turn every `promote-event.yml` run from
red to a clean skip. It is small and worth landing first, separately.

---

## Phase 1 — build identity in dev

1. **Release bucket** `uif-event-dev-release-artifacts` (versioned, no
   lifecycle expiry — a release artifact must stay resolvable forever).
2. **Scripts** — `artifact-hash.sh`, `zip-artifact.sh`, `stage-release-artifacts.sh`
   with event's 6-row set as an explicit file list, never a directory scan.
3. **Terraform conversion** — the 4 Lambdas read `s3_key =
   lambdas/<sha>.zip`; the 2 Glue jobs read `glue/<sha>/<file>.py` from the
   release bucket. Decide whether to keep `source_code_hash` (echo's 1a.4 open
   detail — without it, out-of-band `UpdateFunctionCode` drift is invisible).
4. **IAM** — the publish and recovery Glue roles and the Lambda service need
   read on the new bucket; the deploy role needs write. Verify with
   `simulate-principal-policy` against the attached role, not the document.
5. **`artifact_guard.tf`** — echo's broke dev twice without it (2026-09-24).
6. **`deploy-event-dev.yml`** stages before its plan, as echo's dev deploy does.
7. **`release-event.yml` artifacts job** — `environment: dev`, stage, then
   `release-manifest.sh` writes the `artifacts:` block into the tag annotation.
8. Cut the next `event/v*`; verify `git cat-file -t` prints `tag` and all 6
   artifacts read back through `read-manifest.sh`.

**Moving the Glue scripts out of echo's bucket** is a side effect worth having:
today event's code sits at mutable keys in `uif-echo-{env}-gold-artifacts`, a
bucket another stack owns. The Athena results and domain uploads under
`event-pipeline/` stay where they are — they are data, not artifacts.

**Exit:** dev runs the converted shape (a real publish run end to end); two
tags of unchanged source produce identical hashes; the manifest refuses a
truncated hash, an uppercase hash and a failed gate.

## Phase 2 — uat configuration

`envs/uat.tfvars`:

| Variable | Value | Evidence |
|---|---|---|
| `environment` | `uat` | produces `uif-event-uat-*` via `locals.tf` |
| `gold_bucket` | `uif-echo-uat-gold` | `stacks/echo/infrastructure/terraform-gold/envs/uat-uif.tfvars:19` |
| `gold_artifacts_bucket` | `uif-echo-uat-gold-artifacts` | same file, line 20 |
| `enable_gold_merge_trigger` | `true` from the first converge | `uif-event-uat-bus` (created by echo's gold root, `announce.tf`) **confirmed present** in account 283556840805 from the EventBridge console, 2026-09-30. `PutRule` on a missing bus would fail the whole apply, which is why this was checked first |

The same console listing shows **no `uif-event-uat-product`** — expected: that
domain bus belongs to product's root (`product_events.tf`), which is blocked on
UIFPE-669 until this stack exists in uat. Until product's uat Terraform runs,
merges reach the controller but have no domain bus to publish to.

`backends/uat.s3.tfbackend`: bucket `uif-event-uat-data`, key
`tfstate/event-terraform.tfstate` -- the event stack's own uat state bucket
(owner decision 2026-09-30, and UIFPE-692's "each root's state inside its own
environment's data bucket").

**Written:** `envs/uat.tfvars`, `backends/uat.s3.tfbackend`.

## Phase 3 — bootstrap (a human, with privileged IAM)

**Owner decision 2026-09-30: extend ECHO's uat deploy role** (`uif-echo-uat-deploy-role`,
secret `AWS_ROLE_ARN_ECHO_UAT`) rather than create a second role.

**Written:** `infrastructure/deploy/bootstrap-environment.sh`, `required-actions.py`,
`terraform-iam-map.json`. Run once by someone with IAM admin rights:

```bash
stacks/event/infrastructure/deploy/bootstrap-environment.sh uat --render-policy   # read it
stacks/event/infrastructure/deploy/bootstrap-environment.sh uat --dry-run
stacks/event/infrastructure/deploy/bootstrap-environment.sh uat
```

It creates the state bucket `uif-event-uat-data` (versioned, SSE-S3, public
access blocked), derives the deploy policy from the Terraform -- the root **and**
`modules/event-domain`, so product's uat event resources are covered by the same
role -- and attaches it as a separate managed policy `uif-event-uat-deploy-policy`
on echo's role. It does not edit echo's own derived policy (echo's bootstrap
regenerates that wholesale and would drop anything added to it). It then
simulates every statement against the attached role.

The derived policy is one document, ~5.4k of 6.1k characters, 17 statements,
every ARN pinned to `uif-event-uat-*` except three hand-added grants no
Terraform declares: the state bucket, the copy into this environment's release
bucket, and the **read** of `uif-event-dev-release-artifacts` (the promotion
source echo's plan calls "the one grant the derivation cannot predict"). Budget
apply-and-fix IAM rounds all the same -- apicurio needed five, echo nine.

## Phase 4 — infra converge

**Written:** `.github/workflows/deploy-event-infra.yml`, aligned to the stack
promotion model (2026-10-01) and to echo's working `deploy-echo-infra.yml`:

- triggers: push of `event/v*` or dispatch (in practice dispatch -- tags pushed
  with `GITHUB_TOKEN` trigger nothing, model section 7); a branch dispatch is
  refused before any environment approval or role;
- latch `EVENT_UAT_INFRA` = `skip|plan|apply|bootstrap`, resolved by
  `scripts/resolve-deploy-mode.sh`; unset or unrecognised is `skip`; a dispatch
  input overrides it;
- plan and apply in **one job**; the saved plan is never uploaded. To read the
  plan first, run `plan`, then re-run from the same tag;
- the first converge is `infra=bootstrap`: a targeted apply creates the release
  bucket, then the artifacts are copied and everything is planned and applied in
  the same job (the model's "FIRST APPLY ONLY" mode);
- same concurrency group as `promote-event.yml` (`event-deploy-<env>`).

**An earlier revision followed UIFPE-692's "split plan from apply"** (separate
jobs, uploaded plan) and had the bootstrap create the release bucket for
Terraform to import. Both contradicted the model's section 3 and were removed.

**Run order:** [`../DEPLOY-UAT.md`](../DEPLOY-UAT.md), the phase table.
Product's UAT event resources (`deploy-product-uat.yml` from `product/v0.1.1`)
follow the first converge in this iteration; they use the same
`AWS_ROLE_ARN_ECHO_UAT` role, whose new policy covers the `event-domain`
module's bus, DLQ, mapping and upload.

`promote-event.yml` **cannot** do this first converge: its app-only assertion
refuses a plan that creates infrastructure, and an empty uat is nothing but
infrastructure creates.

**Conflict to settle before Phase 5.** `promote-event.yml` (UIFPE-690) assumes
`secrets.AWS_ROLE_ARN_EVENT_UAT` and refuses any role whose ARN lacks
`uif-event-uat`. With the decision to extend echo's role, that secret does not
exist and the check would refuse `uif-echo-uat-deploy-role`. Either
`promote-event.yml` changes to the echo role, or a dedicated event role is
created after all. Not blocking this iteration -- `deploy-event-infra.yml` does
not depend on it.

## Phase 5 — first promote

Land the remaining scripts `promote-event.yml` calls, and a `qe/integration`
smoke suite (its smoke step fails by design until one exists). Then the steady
state: copy, app-only apply, smoke, `release/uat/<date>.<n>`.

## Phase 6 — domain onboarding in uat (price, stock, program — next iteration)

Not stack work, but nothing publishes until it is done:

- Seed `uif-event-uat-pipeline-configs` for each domain. **No workflow loads
  these today** — in dev they were put by hand, and the price/program/stock
  publish-metrics rows reading `domain: product` point at a copy-paste of
  product's row (inference; see the 2026-09-30 investigation). uat should load
  them from the repo files, by a workflow, not by hand.
- Lake Formation grants for the publish role on each `uif_echo_uat_gold_*`
  database — the documented per-domain onboarding step.
- One end-to-end replay per domain, checking publish-metrics `domain` is right.

---

## Open questions — each needs an answer before its phase

1. ~~State location~~ -- **decided 2026-09-30:** own bucket `uif-event-uat-data`.
2. ~~Deploy role~~ -- **decided 2026-09-30:** extend echo's
   `uif-echo-uat-deploy-role` (`AWS_ROLE_ARN_ECHO_UAT`). **Still open:** who
   with IAM admin rights runs the bootstrap; and the `promote-event.yml` role
   conflict (Phase 4, last paragraph).
3. **UIFPE-690 coordination.** Phase 1 edits `release-event.yml`; Phase 5 may
   edit `promote-event.yml`. Agree with the author whether those changes land
   here or in a UIFPE-690 follow-up.
4. ~~Jira~~ -- **UIFPE-744** created 2026-09-30 for this implementation, under
   UIFPE-35. **UIFPE-692** (raised by Reyas 2026-09-29, assigned to Panikiran)
   describes the same gap and its acceptance criteria; the two are not linked
   in Jira. Decide whether UIFPE-692 is closed against UIFPE-744 or kept.
5. **Domain uploads.** Should mapping/seed uploads stay in the domains' roots
   (today) — and therefore be refused by the promote's app-only assertion if a
   tag ever changes one — or be documented as out of the release unit?
6. **Legacy.** Confirm nothing `echo-uat-*`-era publishes events in uat, so
   there is no cutover or retire phase.
7. **Tag counter.** `next-release-tag.sh` emits `release/<env>/<date>.<n>`,
   shared with echo and apicurio. Accept the shared counter, or namespace it?

## Traps carried forward

From echo's and apicurio's first runs (echo plan, last section): a
`GITHUB_TOKEN`-pushed tag triggers nothing; `actions/checkout` on a tag ref
discards the annotation — re-fetch and assert the object type; a tfvars change
needs a new tag, not a re-run; never leave two latches open for one push; set
`timeout-minutes` everywhere; a green `--verify-only` is not evidence.
