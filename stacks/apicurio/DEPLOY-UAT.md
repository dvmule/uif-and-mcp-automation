# Deploying apicurio to uat

The first deployment of any environment is not the steady-state path. It
crosses both workflows and has a second pass, because the stack cannot know
its own CloudFront domain until the distribution exists. Steady state is one
promote run.

## Prerequisites

| | Check |
|---|---|
| Environment bootstrapped | `bootstrap-environment.sh uat --verify-only` reports all steps green (the environment is positional, not a flag) |
| Both workflows on `develop` | `deploy-apicurio-infra.yml` and `promote-apicurio.yml` |
| A build tag cut AFTER they landed | see "Why v0.1.1 cannot be used" |
| `AWS_ROLE_ARN` on the uat environment | `gh secret list --env uat` — currently empty; set it with `gh secret set AWS_ROLE_ARN --env uat --body "arn:aws:iam::283556840805:role/uif-apicurio-uat-deploy-role"` |
| `uif-apicurio-uat-deploy-policy` carries `PromotionSourceRead` | `bootstrap-environment.sh uat --verify-only` reports it attached. Without it, `promote-apicurio.yml` can log in to ECR but is denied on both `crane copy` and `aws s3 cp`, and the failure surfaces at promote's first AWS call as an opaque registry `denied` rather than a permissions error naming the resource. There is no narrower flag to push just this one statement -- `bootstrap-environment.sh` has no "update the policy only" mode, so apply it by re-running the same bootstrap command already used to create the role and environment: `bootstrap-environment.sh uat --role uif-apicurio-uat-deploy-role --reviewer <existing reviewer(s)>`. With the role and the GitHub Environment already in place, this re-run is idempotent everywhere except step 2/6, where it detects that `capabilities/infrastructure/uif-apicurio-uat-deploy.policy.json` (now carrying `PromotionSourceRead`) differs from the policy's current default version and publishes a new one. |
| Echo's security group is ready to change | The first apply adds an ingress rule to `sg-0b45c554b51638786`, which Echo owns, not this stack. Arrange that with Echo before phase 2, not discover it after a failed apply. |

### Why `apicurio/v0.1.1` cannot be used

A push-triggered run uses the workflow file from the **pushed ref**.
`apicurio/v0.1.1`'s tree carries only `deploy-apicurio-dev.yml` and
`release-apicurio.yml`, so pushing it triggers nothing new. Cut a fresh build
tag once both workflows are on `develop`.

## First deployment

| Phase | Do | Expect |
|---|---|---|
| 1 | `gh variable set APICURIO_UAT_INFRA --body plan`, cut the build tag | a plan in the log, 77 to add. Read it. |
| 2 | `gh variable set APICURIO_UAT_INFRA --body bootstrap`, re-run the run | ECR repository created, then 77 resources applied |
| 3 | add the CloudFront domain from the run summary to `envs/uat.tfvars` `cors_origins`, merge to `develop`, then **cut a new build tag** (same action as phase 1 — not a re-run) and run plan/apply from that tag with `APICURIO_UAT_INFRA=apply` | a small plan, CORS-dominated, on the new tag's run |
| 4 | uat's smoke credential, if not already set — see "The smoke credential" below | `gh secret list --env uat` shows `APICURIO_CI_KEY` |
| 5 | `gh variable set APICURIO_UAT_PROMOTE --body run`, re-run | image copied, bundle restored, app-only apply, tier 2 green, `release/uat/<date>.1` pushed |
| 6 | `gh variable unset APICURIO_UAT_INFRA APICURIO_UAT_PROMOTE` | the deploy window is closed again |

Phase 3 is a **new tag**, not a re-run of phase 2's. `envs/uat.tfvars` is
git-tracked, so its contents are frozen in whichever commit the run's tag
points at; merging the `cors_origins` edit to `develop` does not reach back
into a tag already pushed, and re-running phase 2's run re-checks-out that
same pre-merge tree. Re-running it would produce a plan with no
`cors_origins` change at all — not "CORS only", nothing — and phase 5 would
then promote with `cors_origins = "http://localhost:5173"` still in effect;
tier 2 would go green anyway, since smoke never exercises the browser's
origin, leaving a "green uat" whose UI cannot call its API. See "Re-running
rather than re-tagging" below for the general rule this follows.

uat was deployed this way for the first time on 2026-09-21. Every failure it
hit, with resolutions, is recorded in
[`UAT-DEPLOYMENT-RECORD.md`](UAT-DEPLOYMENT-RECORD.md) -- read it before
planning prod, since most of those failures are first-run failures rather than
uat-specific ones.

Phase 6 is not optional. These variables are a deploy-window latch, not
configuration: left on, every build-tag push deploys uat unattended.

**Never leave both `APICURIO_UAT_INFRA` and `APICURIO_UAT_PROMOTE` open (not
`skip`) for the same tag push.** Both workflows trigger on the same
`apicurio/v*` push, and both a terraform job and a promote job enter the same
`apicurio-deploy-uat` concurrency group, so which one runs first is not
guaranteed even though this table's phasing assumes infra-then-promote. A
promote that runs before infra applies a tag touching any tracked infra file
(`authorizer.tf`'s `archive_file` re-hash is the realistic case, since it
shows up as a Lambda update on every promotion) makes
`assert-app-only-plan.sh` exit 1 and the promote fail. Separately, GitHub
Actions keeps at most one PENDING run per concurrency group: if a run of one
workflow is still queued (not started) when a run of the other reaches the
same group, the queued one is cancelled outright with no error naming why.
Open one latch, let that run finish, then open the other -- which is what the
phase table above already does implicitly by never setting both variables at
once.

### The smoke credential

Tier 2 authenticates against the Lambda authorizer's shared CI key
(`infrastructure/terraform/authorizer.tf`). Terraform creates the Secrets
Manager entry `uif/apicurio/uat/auth/ci` on the first apply (phase 2 above)
but never its value, so a fresh environment has a secret with nothing in it
and tier 2 has nothing to present. The value must be identical in Secrets
Manager and in the Actions secret, since the authorizer compares them:

```bash
key="$(openssl rand -hex 32)"

AWS_REGION=eu-west-1 aws-runas pearson-esb-dev \
  aws secretsmanager put-secret-value \
    --secret-id uif/apicurio/uat/auth/ci --secret-string "$key"

gh secret set APICURIO_CI_KEY --env uat --body "$key"

unset key
```

Set it as an environment-scoped secret on `uat`, not a second repo-level name
such as `APICURIO_CI_KEY_UAT`. An environment-scoped secret shadows the
repository one for jobs declaring `environment: uat`, so the workflow refers
to `secrets.APICURIO_CI_KEY` with no branching and dev's value is never sent
to uat. A repo-level per-environment name would need an `if:` in the workflow
for every environment added.

## Re-running rather than re-tagging

Under tag-only deployment, **changing a variable costs a re-run; changing a
file costs a new tag.** The two look similar — both are "run the workflow
again on the same tag" — but they answer different questions, and confusing
them is exactly how phase 3 goes wrong (above).

- **Latch variables are read at run time.** `APICURIO_UAT_INFRA` and
  `APICURIO_UAT_PROMOTE` are repository variables, not part of the checked-out
  tree, so "Re-run all jobs" on the most recent run picks up a new value with
  no new commit and no new tag. This is why phases 2 and 5 work by re-running.
- **Tracked file contents are pinned to the tag.** `envs/uat.tfvars`, either
  workflow file, any script — anything that lives in the repository tree — is
  frozen at whatever commit the tag points at. The tag is immutable and
  `actions/checkout@v4` defaults to `github.sha`, which for a `push: tags` run
  is the tag's own commit, so no re-run of an existing tag can ever see an
  edit to a tracked file. That needs a new build tag, the same action as
  phase 1.

This is a property of the design, not an exception to remember for phase 3
alone: the next phase anyone adds to this runbook needs the same check —
does it change a latch variable, or a tracked file? — before assuming a
re-run is enough.

## Recovery

**A cancelled apply leaving state locked.** `deploy-apicurio-infra.yml`'s
`terraform` job comments that a cancelled apply can leave state locked, and
the uat Environment's required reviewer makes this reachable in practice --
approve the deployment, then cancel the run before it finishes, or let its
job timeout expire mid-apply. Identify it by the next run's `terraform init`
or `plan` step failing with `Error acquiring the state lock`, naming a Lock
ID, who holds it and since when. There is no unlock mode in this workflow --
it runs `terraform apply -input=false tf.plan` with no `-lock-timeout` and no
force-unlock step, so say that plainly rather than implying one exists. The
human must run `terraform force-unlock <LOCK_ID>` by hand, from
`stacks/apicurio/infrastructure/terraform` after `terraform init
-backend-config="backends/uat.hcl"`, with credentials for uat's account
(283556840805), having first confirmed the run that held the lock is
actually dead (not merely slow) -- force-unlocking a lock a live apply still
holds corrupts state.

**A denial partway through 77 resources.** Re-running is safe: Terraform is
idempotent, so a re-run re-plans against the state as it now stands and
continues from there rather than repeating what already applied. Before
re-running, check what actually changed in the account against what the plan
said would -- a partial apply can leave a resource created but not yet
tagged, or a policy attached but its role not yet updated, and the next
plan's diff is the place that shows up, not a separate report.

**A failed ECS deployment that will not retry after the image arrives.** On a
first deployment the infra apply creates a task definition pinned to a digest
the promote has not delivered yet, so tasks cannot pull. `wait_for_steady_state`
is unset on every service, so the apply still reports success. ECS's deployment
circuit breaker then fails the deployment and **stops retrying** -- it does not
notice when the image later appears, so the service stays at 0 running and
smoke fails with 503 on every BFF test. Identify it by
`aws ecs describe-services` showing `deployments[0].rolloutState: FAILED` with
reason `ECS deployment circuit breaker: tasks failed to start`, and events
timestamped BEFORE the promote's image push. Recover with
`aws ecs update-service --cluster uif-apicurio-<env>-cluster --service
uif-apicurio-<env>-bff-service --force-new-deployment`: same task definition,
no Terraform-managed attribute changed, so no state drift. This happened on
uat's first deployment -- see UIFPE-500 and
[`UAT-DEPLOYMENT-RECORD.md`](UAT-DEPLOYMENT-RECORD.md).

**A 503 from smoke is not a credential problem.**
`test_allows_request_with_ci_key` fails with a message telling you to check the
CI secret's value, but it fires on any non-200. A credential mismatch is **403**
from the authorizer; a **503** means the authorizer allowed the request and the
backend is down -- look at the ECS service, not the secret. If
`test_denies_request_without_credential` passes in the same run, the authorizer
is working.

**There is no rollback on a first run.** No previous `release/uat/*` tag
exists yet, so there is nothing to roll back *to* even if a rollback path
were wanted. Separately, `frontend_force_destroy` is deliberately `false`
for uat (see `envs/uat.tfvars`), so a `terraform destroy` fails on the
non-empty UI bucket rather than silently emptying it. A first-run failure is
recovered forward -- fix the cause and re-run or re-tag -- not backward.

## What uat does and does not prove

uat shares dev's VPC, both `private-css` subnets, dev's single-AZ Secrets
Manager endpoint, and dev's sizing defaults. Isolation is by resource name.
A green uat therefore exercises the application and says nothing about prod's
network or capacity shape.
