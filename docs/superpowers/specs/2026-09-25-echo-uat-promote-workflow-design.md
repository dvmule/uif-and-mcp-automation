# Design Spec: Echo uat promote workflow

**Ticket:** UIFPE-555 · **Date:** 2026-09-25 · **Status:** for review

> **SUPERSEDED IN PART, 2026-09-25.** The workflow this designs was **not**
> merged. UIFPE-576 landed its own `promote-echo.yml` on `develop` at the same
> time, independently, and that is the one that survived — it is multi-environment
> (`uat|prod`), covers the gold root as well as the main one, and derives the
> app-only address list with a script rather than a hardcoded list.
>
> What still stands, and is why this document is kept rather than deleted:
>
> - **The asymmetry in "The asymmetry this design is built on"** — Echo must
>   promote *before* it converges because Lambda validates its S3 object at
>   `CreateFunction`, where apicurio can converge first because an ECS task
>   definition does not. UIFPE-576's workflow independently arrives at the same
>   order, which is corroboration rather than coincidence.
> - **The eight verified constraints**, each with the file and line that shows it.
> - **The UI publish gap.** This design's `UIF_ECHO_UI_BUNDLE` path was built and
>   merged; UIFPE-576's workflow records `ui_bundle: NOT promoted` in its release
>   tag, so the gap this closes is still open on the workflow side.
> - **The smoke.** `smoke-uat.sh` was built and merged. UIFPE-576's workflow runs
>   `pytest stacks/echo/qe/integration` and fails by design until that exists, so
>   the two do not yet connect.
>
> What is obsolete: the gating section (UIFPE-576 uses `skip|run`, with no `copy`
> mode), the job/step layout, and the uat-only scoping.


Echo's uat rollout (phases 2–4.1) is merged as of `c9d53a98`. The uat stack's
identity, IAM, state bucket and release bucket exist; `copy-artifacts.sh`
promotes artifacts and is proven against real S3. What is missing is the
workflow that runs it, so that a promotion is a recorded, repeatable event
rather than something a person does from a laptop.

## Purpose

`promote-echo.yml` takes a build tag whose gate passed, copies the artifacts it
names into a target environment's release bucket, converges the application
layer onto them, checks the result is reachable, and records the outcome as a
`release/uat/<date>.n` tag.

It replaces a manual `copy-artifacts.sh` invocation. That manual step was a
sequencing artefact of the rollout plan, not a requirement: apicurio's first uat
deployment is documented as running entirely through `promote-apicurio.yml`
rather than by hand, and there is no reason Echo's first one should not. (How
apicurio's runs were *triggered* is a separate question, and constraint 2
records that this repository cannot answer it.)

## The asymmetry this design is built on

**Apicurio promotes after converging; Echo must promote before.**

Apicurio ships a container image. An ECS task definition may reference a digest
that does not exist yet — the reference is metadata, and the failure surfaces at
task launch. So apicurio applies 77 resources first
(`APICURIO_UAT_INFRA=bootstrap`) and promotes the image afterwards
(`APICURIO_UAT_PROMOTE=run`).

Echo ships Lambda zips and Glue scripts. `CreateFunction` and
`PublishLayerVersion` validate the S3 object at apply time and fail with
`NoSuchKey`. So Echo's artifacts must be in the destination bucket **before**
the converge, and the promote is therefore not one indivisible step:

| Step | Runs |
|---|---|
| Copy 27 artifacts into the release bucket | before the converge (Phase 4.6) |
| App-only apply, UI publish, smoke, release tag | after the converge (Phase 5) |

Glue differs again and the difference is a trap: `script_location` is stored as
a plain string, so a missing Glue script applies cleanly and fails at run time,
showing `ARTIFACT-SHA-NOT-SUPPLIED` in the plan as a sentinel meant to be read.
**A clean Glue plan is not evidence the apply will work.** Lambda is the service
that fails loudly; Glue is the one that fails later.

## What already exists (verified, not assumed)

| Piece | State |
|---|---|
| `copy-artifacts.sh` | Merged. Server-side `copy-object` with a version-id proof, two-pass (verify all, then copy), idempotent. Dry-run verified all 27 artifacts of `echo/v0.1.0` against live S3 on 2026-09-24. |
| `read-manifest.sh` | Merged, with `--list` and `--dump`. The only thing in the repo that parses manifest shape. |
| `restore-artifacts.sh` | Verifies a downloaded artifact per class. `ui_bundle` extracts and re-hashes with `config.json` excluded. |
| `assert-app-only-plan.sh` | Exists. Not yet wired into anything for Echo. |
| `next-release-tag.sh`, `release-manifest.sh` | Exist, used by `release-echo.yml`. |
| `uif-echo-uat-deploy-role` | Created. Two packed policies + backend policy. All 188 derived actions `allowed`. |
| `uif-echo-uat-data`, `uif-echo-uat-release-artifacts` | Both exist, versioned. |
| `uat` GitHub Environment | `tag:echo/v*` admitted, 2 reviewers, `AWS_ROLE_ARN_ECHO_UAT` set. |

## Verified constraints

1. **A `GITHUB_TOKEN`-pushed tag triggers nothing.** `release-echo.yml:329`
   pushes the build tag with `github.token`, so `on: push: tags:` can never
   fire for a release-cut tag. `workflow_dispatch` against the tag ref is the
   only real entry point.
2. **On a dispatch run the repo variable is never consulted.**
   `resolve-deploy-mode.sh` is explicit: `if event = workflow_dispatch then
   candidate=input else candidate=var`. And a re-run of a dispatch run reuses
   its original inputs. So for a dispatch-triggered run the **input is the only
   gate**, and a phase transition is a **fresh dispatch**, not a re-run.

   This corrects an earlier draft of this spec, which had it backwards and
   specified a variable gate that would have been unreachable. It also means
   apicurio's documented "set the variable, re-run the run" cannot be confirmed
   from this repository: nothing chains these workflows by `workflow_call`, and
   `release-apicurio.yml:397` pushes its tag with `github.token` too, so its
   push trigger is as dead as Echo's would be. Either those runs were
   human-pushed tags, or the phase table describes something that does not
   happen. **Not relied on here.**
3. **`actions/checkout` on a tag ref discards the annotation**, producing a
   lightweight tag locally, which carries no manifest. Re-fetch is mandatory.
4. **Echo has no integration or smoke suite.** `qe/` is unit suites only;
   `qe/ci.sh:38` records the integration tier as deliberately absent.
5. **There is no health route** in `apigateway.tf`. ~~and the API is behind
   auth~~ — **this second half was wrong and is corrected here.**
   `enable_jwt_authorizer = false` in **all five** tfvars (`dev`, `dev-uif`,
   `uat`, `uat-uif`, `prod`), so `local.authorizer_id` is null and every route's
   `authorization_type` resolves to `NONE`. The API is *not* behind auth in any
   environment today; `pipeline_controller.py` enforces credentials only on
   `POST /auth/login`. An authenticated smoke would therefore need the
   authorizer switched on *and* a new credential in the shared `uat`
   environment — a larger change than this spec originally assumed.

   I asserted "behind auth" from the existence of a `uif/echo/{env}/ui/login`
   secret without reading the flag that gates the authorizer. The Smoke section
   below records what that cost.
6. **`uif-echo-ui-deploy.sh` always builds** (`npm ci`, `npm run build`) — it
   has no no-build path, so a promote cannot reuse it without rebuilding, which
   would destroy the digest identity the whole model rests on.
7. **The UI sync is `--delete --exclude "m/*"`.** The UI bucket hosts beacon's
   and transform's apps under `m/*`, and that one exclude is what stops
   `--delete` removing them. This repo has form: a `--delete` sync removed
   166 MB of layer zips on 2026-09-12.
8. **`artifact-hash.sh` excludes `config.json`**, described in its own comment
   as load-bearing — precisely so environment config can be written into a
   restored bundle without changing its identity.

## Gating

`workflow_dispatch` against the build tag ref is the operative entry point, and
the **dispatch input is the gate**. `on: push: tags: echo/v*` is **kept**, but
only because it costs nothing and makes the repo variable meaningful for a
human-pushed tag; a release-cut tag will never fire it (constraint 1). A comment
in the workflow must say so, or the next reader will assume promotion is
automatic.

Resolution goes through `stacks/echo/scripts/resolve-deploy-mode.sh` unchanged,
passing both `--input` and `--var` exactly as apicurio does. The script then
picks correctly per event: dispatch → input, push → variable. No change to a
shared script, and no pretending the variable governs a dispatch run.

**A phase transition is a fresh dispatch, not a re-run.** Re-running reuses the
original inputs, so a re-run cannot move `copy` to `run`. This is one extra
dispatch rather than a `gh variable set`, and it is the honest mechanism.

Vocabulary:

| Mode | Does |
|---|---|
| `skip` (default) | Nothing. `resolve` still reports, so the run is a record. |
| `copy` | Manifest read + `copy-artifacts.sh`. Stops. The greenfield path. |
| `run` | `copy`, then app-only plan + assertion, apply, UI publish, smoke, release tag. |

Default-skip means a stray dispatch or an accidental re-run does nothing. Note
the deploy window does **not** close by unsetting the variable, because the
variable does not govern dispatch runs — what protects a dispatch is the `uat`
environment's reviewer and its `tag:echo/v*` policy, both already in place.

**uat only in v1.** Fixed `environment: uat` and `secrets.AWS_ROLE_ARN_ECHO_UAT`.
Apicurio's takes `uat|prod`, but Echo has no prod deploy role, and supporting an
unexercisable path would mean dynamic secret indexing for no benefit. CLAUDE.md
is explicit about no speculative abstraction. Adding prod later is small and
obvious.

## Jobs

**`resolve`** — no AWS, no environment. Outputs the tag, the mode and a summary
of what will happen. Always runs, so even a `skip` leaves a readable record.

**`promote`** — `needs: resolve`, `if: mode != 'skip'`, `environment: uat` so
the reviewer and the tag policy both apply, `concurrency: echo-promote-uat` with
`cancel-in-progress: false` because cancelling mid-apply is worse than queueing.

Permissions are **job-level**, matching apicurio: the workflow declares
`contents: read`, and the `promote` job alone raises it to `contents: write`
(the release tag) plus `id-token: write` (OIDC). `resolve` keeps the read-only
default, so the job that needs no privilege holds none.

## Steps

### `copy` and `run`

1. `actions/checkout` with `fetch-depth: 0`.
2. Re-fetch the annotated tag object, and assert it is a tag object
   (constraint 3).
3. Configure AWS credentials via OIDC from `AWS_ROLE_ARN_ECHO_UAT`.
4. Read `release_artifacts_bucket` from `envs/uat-uif.tfvars` with the same
   `sed` `uif_echo_import_phase5_artifacts` uses — not a second derivation.
5. `copy-artifacts.sh --tag <tag> --to-bucket <bucket> --tfvars ... --tsv ...`
6. Summarise: how many copied, how many already present.

### `run` only

7. `restore-artifacts.sh --class ui_bundle` into a working directory.
8. `terraform init` with `backends/uat-uif.hcl`, then `plan` with
   `envs/uat-uif.tfvars` and the promoted tfvars.
9. `terraform show -json tf.plan | assert-app-only-plan.sh <allowed-address>...`
   — **piped, never written to a file.** The script's own header states why: a
   saved plan contains every generated value, so the JSON must never reach a log
   or an artifact; piping keeps it in memory and only resource addresses are
   printed. The workflow must not `tee` it, save it, or upload it.

   **Three outcomes, not two**, and the workflow must branch on all three:

   | Exit | Meaning | Workflow does |
   |---|---|---|
   | `0` | app-only confirmed | apply |
   | `3` | plan is empty — explicitly not a failure | **skip the apply, continue to the UI publish and smoke** |
   | `1` | touches something outside the application layer, or unparseable input | fail the run |

   Exit 3 is the expected outcome of the **first** `run`, because Phase 4.8's
   local converge will already have applied the promoted artifacts. So an empty
   plan is positive evidence the converge picked up exactly what was promoted —
   and the script already models this, which is why the workflow must not treat
   a non-zero exit as failure indiscriminately.
10. `terraform apply` the saved plan — **only on exit 0**.
11. `uif-echo-ui-deploy.sh --from-bundle <tar.gz>`.
12. Smoke (below).
13. `next-release-tag.sh` then `release-manifest.sh`, and push
    `release/uat/<date>.n`.

## The tfvars and the local converge

Phase 4.3–4.5 ran locally; 4.7–4.8 will too. A tfvars written inside a CI runner
does not reach a local checkout.

This design does **not** upload it as a run artifact for download — that
reintroduces the manual step the workflow exists to remove. Instead, after the
workflow copies for real, the local converge regenerates the file with a
**dry run**, which is read-only:

```bash
copy-artifacts.sh --tag echo/v0.1.0 \
  --to-bucket uif-echo-uat-release-artifacts \
  --tfvars stacks/echo/infrastructure/terraform/artifacts.auto.tfvars.json
```

That does double duty: it writes the hashes, and because every key already
exists in uat it reports all 27 as `=`, confirming the CI copy landed
completely.

Moving the converge into CI (an `INFRA` mode, as apicurio has) is the natural
follow-up and is **out of scope here** — building a promote workflow and a uat
infra workflow at once is how both end up half-tested.

## UI publish

Add `--from-bundle <tar.gz>` to `uif-echo-ui-deploy.sh`: skip `npm ci` and
`npm run build`, populate `ui/dist` from the restored bundle, and leave the
`config.json` write, the `--delete --exclude "m/*"` sync and the CloudFront
invalidation untouched. Absent the flag, dev's behaviour is unchanged.

The logic is **not** inlined into the workflow, though apicurio inlines its
equivalent. Constraint 7 is the reason: reimplementing that sync in YAML puts
the `m/*` exclude one forgotten line away from deleting another team's site.
`stage-artifacts.sh`'s header already argues this in the repo's own words —
"Written twice, they drift, and the failure is invisible" — and here the
invisible failure is beacon's UI.

## Smoke

Minimal and unauthenticated, requiring no new secret in the shared `uat`
environment (constraints 4 and 5). Three assertions:

1. `ui_cloudfront_url` returns **200** with a non-empty body.
2. `api_endpoint` on a real route returns **200, 401 or 403** — all passes,
   because any of them proves the route, the integration and the Lambda exist.
   **404 fails** (the route is missing), **5xx fails** (the Lambda is broken)
   and **000 fails** (unreachable).

   **Corrected 2026-09-25, after the final review.** This originally said 401 or
   403 pass and treated a 2xx as a failure meaning "the authorizer is not
   enforcing". That rested on constraint 5 below, which was **wrong**:
   `enable_jwt_authorizer = false` in *all five* tfvars, so
   `apigateway.tf`'s `local.authorizer_id != null ? "JWT" : "NONE"` resolves to
   `NONE` and an unauthenticated `GET /pipelines` returns 200. As first written
   the smoke could never pass — every `run` promote would have applied,
   published the UI, then died at this assertion with no release tag cut.

   The lesson is narrower than "the constraint was wrong": this assertion's job
   is **reachability**, and it had an authorization-posture claim smuggled into
   it. Those are different properties, and the second one was never true of this
   stack. The 401/403 arm stays only so the test still passes if an authorizer
   is ever switched on.
3. `aws glue get-job` on one job shows a `script_location` containing the
   **promoted sha**.

Assertion 3 is the one that earns its place: 1 and 2 prove the stack exists,
while 3 proves *this promotion* landed. Without it a promote could copy nothing
and still go green.

An authenticated suite mirroring apicurio's tier 2 is the right eventual answer
and belongs in its own ticket, developed against a live uat rather than guessed
at.

## Refusals

| Condition | Refused by |
|---|---|
| Mode outside `skip\|copy\|run` | `resolve` |
| Lightweight tag, or manifest not describing this tag | `read-manifest.sh` |
| `gate.conclusion != success` | `read-manifest.sh`, on every read |
| Source version no longer present | `copy-artifacts.sh`, before copying anything |
| Destination bucket missing or unversioned | `copy-artifacts.sh` |
| App-only plan touches infrastructure | `assert-app-only-plan.sh` |
| Smoke fails | no release tag is cut |

The last one is the point of the ordering: a `release/uat/*` tag always means
something was checked.

## Testing

`stacks/echo/qe/pipeline/test_promote_echo_workflow.py`, asserting on the YAML:

- mode vocabulary is exactly `skip|copy|run`, and the default is `skip`
- `environment:` is present on the `promote` job — without it the reviewer and
  the tag policy silently do not apply, and the run still succeeds
- `concurrency` is present with `cancel-in-progress: false`
- `permissions` includes `id-token: write`
- the workflow calls `resolve-deploy-mode.sh` with **both** `--input` and
  `--var`, so the event decides which applies rather than the workflow guessing
- `resolve-deploy-mode.sh` itself is **unmodified** — it is shared with apicurio
  by convention, and a behaviour change here would drift the two copies
- the workflow reads `secrets.AWS_ROLE_ARN_ECHO_UAT`, and **not**
  `AWS_ROLE_ARN_UAT` (product's) or bare `AWS_ROLE_ARN` (apicurio's) — the
  existing `test_deploy_role_secret_name.py` already covers the class
- `copy` mode reaches no `terraform apply`
- `run` calls `assert-app-only-plan.sh` **before** `terraform apply`
- the workflow **branches on exit 3** and does not treat it as a failure —
  without this the first promote of every environment breaks, and it breaks in
  the way that looks like a real defect
- the plan JSON is **piped**, never redirected to a file, `tee`'d or uploaded —
  it carries every generated value
- **no inline `aws s3 sync` with `--delete` appears anywhere in the workflow**

The last is the highest-value case. It is the one failure in this design that
harms another team, and the one a reviewer would not catch by reading the diff.

Every assertion verified to fail under mutation, as with the four test files
merged in `c9d53a98`.

## Risks

| Risk | Mitigation |
|---|---|
| First `run` on a stack whose converge was local — plan will be empty | `assert-app-only-plan.sh` exits 3 for exactly this and calls it not a failure. The workflow skips the apply and proceeds. A workflow that treated any non-zero exit as failure would break every first promote. |
| Credentials expire mid-run (role default 1 hour) | The copy is ~27 API calls. A long apply is the exposure; `-a 4h` if the role permits. |
| Secrets Manager endpoint SG not yet opened (Phase 4.9) | Smoke assertion 2 passes regardless — a 200 needs no secret on the read path — so it will **not** catch this. Runtime failure remains a known gap until 4.9 is done. Stated here so it is not mistaken for coverage. |
| `m/*` exclude regressed | Test case above, plus the logic staying in one script. |

## Explicitly rejected

- **Authenticated smoke in v1** — needs a new suite and a credential in a
  shared environment before the stack is proven. Own ticket.
- **Omitting `on: push: tags:`** — an earlier draft rejected the trigger; it is
  kept, because it is the only event under which the repo variable has any
  effect. It stays dead for release-cut tags and the workflow says so.
- **A repo variable as the gate for dispatch runs** — unreachable
  (constraint 2). The input is the gate; the variable serves push runs only.
- **Inlining the UI sync** — constraint 7.
- **Uploading the tfvars as a run artifact** — reintroduces a manual step.
- **prod support** — no prod deploy role exists.
- **Converge in CI** — right, but not at the same time as this.
