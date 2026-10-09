# Deploying the event stack to UAT

This is the runbook for putting the shared eventing stack (`uif-event-uat-*`) into
UAT and keeping it there. It follows the mandated
[stack promotion model](../../capabilities/delivery/stack-promotion-model.md):
dev is the only environment that builds, a build tag `event/vX.Y.Z` records the
exact bytes that were built, and UAT receives those bytes and converges its
infrastructure from the same tag. The design, its phases and its history are in
[`docs/uif-event-uat-rollout-plan.md`](docs/uif-event-uat-rollout-plan.md);
this file is what to run. Tracked on **UIFPE-744**.

## Phase table

| # | Phase | Who / how | Command or workflow | Done when |
|---|---|---|---|---|
| 0 | Dev on the release-artifact shape | Reyas, GitHub Actions | `deploy-event-dev.yml`, `infra=plan` then `infra=apply` | one publish run end to end in dev |
| 1 | Build tag with artifacts | PR bumping `stacks/event/VERSION` | `release-event.yml` fires on merge | `git cat-file -t event/vX.Y.Z` prints `tag`; `scripts/read-manifest.sh event/vX.Y.Z --dump` lists 6 artifacts |
| 2 | Bootstrap (once) | a person with IAM admin rights in 283556840805 | `infrastructure/deploy/bootstrap-environment.sh uat --render-policy`, then `--dry-run`, then without flags | exit 0: state bucket `uif-event-uat-data` exists, `uif-event-uat-deploy-policy` attached to `uif-echo-uat-deploy-role`, every statement simulates allowed |
| 3 | Read the first plan | Reyas | dispatch `deploy-event-infra.yml` **from the tag**, `environment=uat`, `infra=plan` | run summary lists the creates (see *Expected first plan*); nothing changed |
| 4 | First converge | Reyas; the `uat` reviewer approves | same, `infra=bootstrap` | apply completes; `uif-event-uat-dlq-recorder` exists |
| 5 | Product's UAT event resources | Reyas | `deploy-product-uat.yml`, `terraform=plan` then `apply`, from `product/v0.1.1` | plan passes the `uif-event-uat-dlq-recorder` lookup; apply completes |
| 6 | Close the window | Reyas | `gh variable delete EVENT_UAT_INFRA` (if it was set) | variable absent |
| — | Every later tag | Reyas | `deploy-event-infra.yml`, `infra=plan`, read, then `infra=apply` | — |

**Latch** (model §5): repository variable `EVENT_UAT_INFRA` = `skip` (default,
also for unset or unrecognised values) | `plan` | `apply` | `bootstrap`. A dispatch
input overrides it. Dispatching with the input is the normal path: tags pushed by
`release-event.yml` use `GITHUB_TOKEN` and trigger nothing (model §7).

**Reading the plan before approving it** (model §3): the reviewer approves the
*intent* (this tag, this environment). To see the plan first, run with `plan`,
read the summary, then re-run from the same tag with `apply` (or `bootstrap` the
first time). The tag is immutable, so both runs describe the same release.

### Expected first plan

**37 creates** in the phase-3 `plan` run: the release bucket and its 3
settings, 4 Lambdas, 2 Glue jobs, 6 DynamoDB tables, the trigger DLQ and its
policy, the provisioner's stream mapping, 7 IAM roles and 7 inline policies, the
GoldMergeCompleted rule, target and Lambda permission on `uif-event-uat-bus`, and
the artifact guard. In the phase-4 `bootstrap` run the 4 bucket resources are
created first by the targeted apply, so its plan shows the remaining **33**. **No
change and no destroy** on a first converge. If the plan wants to change or
destroy anything, stop and ask.

## Recovery

| Situation | What you see | What to do |
|---|---|---|
| **Locked state** | `Error acquiring the state lock` at plan or apply | A run was cancelled mid-apply, or two runs overlapped (the concurrency group should prevent this). Confirm no `deploy-event-infra.yml` or `promote-event.yml` run is active. Then remove the lock object `tfstate/event-terraform.tfstate.tflock` in `s3://uif-event-uat-data` (S3-native locking, `use_lockfile = true`), or run `terraform force-unlock <id>` with the id from the error. Never delete the state object itself. |
| **Partial apply** | apply failed half way; some `uif-event-uat-*` exist | Nothing to roll back by hand. Fix the cause (usually a missing IAM action: add it to `infrastructure/deploy/terraform-iam-map.json`, re-run the bootstrap), then re-run `infra=apply` from the same tag. Terraform continues from the state it wrote. If the failure was on the first converge after the bucket was created, use `apply`, not `bootstrap`, since the bucket exists now. |
| **IAM denial** | `AccessDenied` / `not authorized to perform` naming an action | Expected on the first rounds (apicurio needed five, echo nine). Add the action to the map entry for that resource type, re-run `bootstrap-environment.sh uat`, re-run the workflow. A green simulation is not proof. |
| **Function or job broken after apply** | publish or controller errors after a new tag | Roll forward, or back, by **tag**: dispatch `deploy-event-infra.yml` (or later `promote-event.yml`) from the previous good `event/v*` tag. Its artifacts are still in both release buckets, because keys are content hashes and the buckets never expire objects. |
| **Missing artifact hash** | plan fails with `Release artifact hashes are missing` (artifact_guard.tf) | The copy step did not write `artifacts.auto.tfvars.json`: usually a branch dispatch or a tag without an artifacts block. Dispatch from a tag cut after release-event.yml gained its artifacts job. |
| **Copy refused** | `copy-artifacts.sh` says a version is not listed | The object version the tag names was deleted from `uif-event-dev-release-artifacts`. It cannot be promoted; cut a new tag. |

## Deviations from the model, recorded

| Model | Here | Why |
|---|---|---|
| A deploy role per stack, secret `AWS_ROLE_ARN` (§3 step 1, §6) | echo's `uif-echo-uat-deploy-role`, secret `AWS_ROLE_ARN_ECHO_UAT`, extended with a separate derived policy `uif-event-uat-deploy-policy` | Owner decision 2026-09-30. The policy is still derived from the Terraform and pinned to `uif-event-uat-*`. **Consequence:** `promote-event.yml` expects `AWS_ROLE_ARN_EVENT_UAT` and must be reconciled before the first promote. |
| Bootstrap has 7 steps incl. creating the GitHub Environment and the secret | 5 steps; the `uat` Environment (tag policy `event/v*`) and the secret already exist and are shared | Nothing to create. Not re-verified by the script. |
| `backends/<env>.hcl` | `backends/<env>.s3.tfbackend` | The convention dev and `promote-event.yml` already use. |
| Phase 3 promote with smoke and a `release/uat/*` tag | not yet | Needs `next-release-tag.sh`, `assert-app-only-plan.sh`, `app-only-addresses.sh` and a `qe/integration` smoke suite. Until then UAT meets the model's definition of done for infrastructure only. |

UIFPE-692 asked for plan and apply to be separate jobs. The model requires them
in the same job with the saved plan never uploaded, and echo's converge does it
that way; the model is followed here.
