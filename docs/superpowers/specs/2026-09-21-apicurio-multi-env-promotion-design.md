# Design Spec: Apicurio multi-environment promotion

**Date:** 2026-09-21
**Author:** Rajnivas Karunakaran
**Status:** Approved — ready for implementation

---

## Purpose

Apicurio can be deployed to `dev` and nowhere else. `deploy-apicurio-dev.yml` is the
only workflow that deploys the stack, and it is hardcoded to dev throughout —
`ENV`, every `environment:`, the backend, the tfvars, the role secret and the ECR
repository name. The
`uat` environment now exists — IAM role, two policies, GitHub Environment with a
required reviewer and a tag-only ref policy, `AWS_ROLE_ARN_UAT`, backend and tfvars —
but nothing can deploy into it.

This spec defines how the stack reaches `uat` and then `prod`, and splits that into two
workflows with one shared identity:

- **`deploy-apicurio-infra.yml`** — owns the Terraform lifecycle per environment.
- **`promote-apicurio.yml`** — owns artifact promotion and the release tag.

It does not change `deploy-apicurio-dev.yml`. Dev keeps working exactly as it does.

---

## The asymmetry this design is built on

"Promotion" names two different mechanisms, and conflating them is what makes
multi-environment pipelines rot.

| | Application | Infrastructure |
|---|---|---|
| What moves | **bytes** — an image digest, a bundle hash | **code** — the same `.tf` files, already merged |
| How it moves | copy, then prove the digest did not change | apply again with that environment's inputs |
| Identity across envs | preserved — dev's digest *is* prod's digest | deliberately different per environment |
| Honest verb | promote | converge |

An application artifact promotes because it is immutable; `copy-image.sh` and
`restore-bundle.sh` exist to prove it stayed immutable. Infrastructure never promotes,
because there is no artifact to carry — what reaches `uat` is the same code already on
`develop`, run with `envs/uat.tfvars`.

They are different operations, so they get different workflows. They are bound together
by a single identity: the **build tag's release manifest**. Every infra apply resolves
its image digests from the tag being deployed, so even a full 77-resource apply lands a
known application version rather than whatever happened to be latest.

---

## What already exists (verified, not assumed)

**The Terraform root is already environment-agnostic.** One root module, 14 `.tf` files,
no per-env directories and no workspaces. The environment is *data*:
`backends/<env>.hcl` selects the state key, `envs/<env>.tfvars` selects the inputs.
`dev.tfvars` and `uat.tfvars` differ in four lines (`env`, `cors_origins`,
`frontend_force_destroy`, and dev's extra CloudFront origin). Adding `prod` costs two
files, not a pipeline.

**Infra and app are already coupled at the variable level.** `bff_image_digest` and
`registry_image_digest` have no default and are validated against
`^sha256:[0-9a-f]{64}$`. Terraform cannot apply without being told an application
version, so "separate app promotion" can never mean "infra is unaware of the app".

**App-only deploy already exists.** `infra=app` plans, asserts the diff touches nothing
but the BFF task definition and service, and then applies that plan. The promote
workflow reuses this rather than inventing a second blast-radius check.

**The authorizer needs no promotion.** `authorizer.tf` packages `authorizer/index.py`
with `archive_file` at apply time, from the tree being applied. It is tag-identified by
construction; there is no artifact to copy.

---

## Verified constraints

Six findings from live reads. The first four are blocking; they set the trigger design.

### F1 — `main` is 1,019 commits behind `develop`

`main` was last touched 2026-07-26 and carries 2 of the repository's 29 workflows
(`domain-ci.yml`, `jira-transition.yml`). Zero commits exist on `main` that are not on
`develop`, so it is a clean fast-forward — but it is 1,019 commits wide.

`workflow_dispatch` only fires for workflows present on the default branch. So a
dispatch-driven design is unavailable today. This also means
`deploy-apicurio-dev.yml`'s own `workflow_dispatch` block is currently dead code — its
comment anticipates exactly this ("once this file reaches main"), and dev is
latch-variable-driven in practice.

### F2 — `uat` accepts tag refs only

Read live from the environments API:

| Environment | Reviewers | Ref policy |
|---|---|---|
| `dev` | none | branches `develop`, `main` |
| `uat` | `Raj-Karunakaran_pearson` | **tag `apicurio/v*`** |

A job declaring `environment: uat` can only run from a ref matching `apicurio/v*`.

Separately, `uif-apicurio-uat-deploy-role`'s trust policy admits exactly one subject —
`repo:pearson-dnt-coe@183024320/universal-Integration-framework@1308482856:environment:uat`
— with no ref condition at all. So IAM constrains the *environment* and GitHub
constrains the *ref*. Two independent gates; both must be satisfied.

### F1 + F2 decide the trigger

`on: push: { tags: ['apicurio/v*'] }`. A push-triggered run uses the workflow file from
the pushed ref, so this requires nothing on `main`.

This is better than dispatch, not merely a workaround. `uat`'s required reviewer becomes
the human gate: pushing a build tag queues the deployment, a human approves it, and only
then does it run. Latch variables supply the plan/apply/skip choice, matching the
`BEACON_INFRA` and `GOLD_INFRA` idiom already used in this repository.

Both workflows still declare a `workflow_dispatch` block. It costs nothing while
inert and becomes live the moment `main` catches up.

### F3 — `apicurio/v0.1.1` cannot be the first promotion

Its tree carries only `deploy-apicurio-dev.yml` and `release-apicurio.yml`. Since a
push-triggered run uses the ref's own copy of the workflow, the first real run requires
a build tag cut *after* both new workflows land on `develop`.

### F4 — the build tag's UI archive lives in dev's bucket

`release-apicurio.yml`'s `artifacts` job runs with `environment: dev` and
`AWS_ROLE_ARN_DEV`, writing to `uif-apicurio-dev-release-artifacts`. Promotion therefore
needs an S3 copy into the target environment's own bucket, and the target's
`version_id` will differ from the manifest's by construction. That is data, not drift —
the per-environment-store decision this design inherits.

### G1 — `uat` has no smoke credential

`authorizer.tf` creates `uif/apicurio/${var.env}/auth/ci`, but Terraform creates the
secret, never its value. `gh secret list --env uat` is empty, and `APICURIO_CI_KEY`
exists only repository-wide holding dev's value. QE tier 2 authenticates with it, so uat
smoke has nothing to present.

**Resolution:** an **environment-scoped** `APICURIO_CI_KEY` on `uat` — same secret name,
different value, resolved automatically because the job declares `environment: uat`. No
workflow branching, and no credential shared across environments.

### G2 — `aws_ecr_repository.base_python` will be created and never used

`bff.tf` declares a second repository for the SIF base-image mirror. A uat apply creates
`uif-apicurio-uat-base-python-ecr-repo` empty, because uat never builds and
`SIF_MIRROR_PREFIX` points at dev's. Harmless, but it is a resource with no consumer.

---

## Phasing

`wait_for_steady_state` is not set on any ECS service, so it defaults false and an apply
returns without waiting for tasks to become healthy. The full apply can therefore run
*before* the image exists in the target ECR, which collapses what would otherwise be an
interleaving of the two workflows.

| # | Phase | Owner | Why it sits here |
|---|---|---|---|
| 0 | `bootstrap-environment.sh --env <env>` | manual, one-off | role, policies and Environment must exist before any OIDC job runs. **Complete for uat.** |
| 0b | set the CI key value; add env-scoped `APICURIO_CI_KEY` | manual, one-off | Terraform creates the secret, not its value (G1) |
| 1 | targeted apply of `aws_ecr_repository.bff`, guarded by `describe-repositories` | infra | promotion needs a push destination; the guard makes it a no-op once it exists |
| 2 | full apply — 77 resources, digests from the manifest | infra | service stays unhealthy until phase 4; expected |
| 3 | `cors_origins` second pass, on a **new build tag** | infra | the CloudFront domain does not exist until phase 2; `envs/uat.tfvars` is git-tracked and pinned to the tag's tree, so a re-run of phase 2's tag can never see the edit — it needs a fresh tag the same way phase 1 did |
| 4 | copy image → copy and restore bundle → app-only apply → sync UI | promote | ECR and both buckets must exist first; the UI bundle publishes last because its `config.json` points at the live API, so the API must already be up |
| 5 | QE tier 2 smoke → cut `release/<env>/<date>.<n>` | promote | needs a running stack |

Phases 1 and 2 run as two jobs of a single `bootstrap` run. Phase 3 is a second infra
run, and it is a run against a **new build tag**, not a re-run of phase 2's — a
latch variable is read at run time and a re-run picks up a new value with no new
tag, but tracked file contents such as `envs/uat.tfvars` are pinned to the tag's
tree, so only a new tag can carry the edit. Phase 4–5 are one promote run. The
workflows do not interleave.

`prod` is the same seven phases with `backends/prod.hcl`, `envs/prod.tfvars`, and a
phase 0 run against account `216064088366`.

---

## `deploy-apicurio-infra.yml`

**Trigger:** `push: { tags: ['apicurio/v*'] }` plus an inert `workflow_dispatch`.

**Latch:** `APICURIO_<ENV>_INFRA` ∈ `{skip, plan, apply, bootstrap}`, default `skip`, so
a tag push deploys nothing unless a deploy window is open. `bootstrap` runs the targeted
ECR apply and then the full apply in one run; it exists only for a first apply.

**Jobs**

| Job | Environment | Does |
|---|---|---|
| `resolve` | none | reads the latch, decides the action, resolves digests from the tag |
| `ecr` | `<env>` | `bootstrap` only: idempotent `apply -target=aws_ecr_repository.bff` |
| `terraform` | `<env>` | `plan -out=tf.plan` then apply that file, **in the same job** |

**Identity:** `bff.digest` and `registry.digest` come from `read-manifest.sh` against the
pushed tag. The workflow never resolves "latest" anything.

**`applied == reviewed`, and why the plan file must not be uploaded.** The apply
consumes the plan file rather than re-planning, which is the property dev's `infra=app`
mode already holds. But plan and apply must stay in the **same job** — same runner, same
filesystem, nothing persisted between them. `deploy-apicurio-dev.yml` documents three
load-bearing reasons, and this design inherits all three:

1. A saved plan is only valid for the Terraform version that wrote it.
2. **A saved plan contains every generated value.** It must never be uploaded as an
    artifact, and `terraform show -json` must never reach a log or an artifact — `grep`
    finds nothing in `tf.plan` only because it is compressed. RDS-managed credentials keep
    the database password out of it, but `authorizer.tf` still generates values. This is
    why the infra workflow cannot use `deploy-product-prod.yml`'s upload/download plan
    shape.
3. No `-auto-approve` on the plan-file path: it is not a valid pairing, and Terraform does
    not prompt when handed a plan.

**How a human reads the plan, then.** They do not read it inside one run. The reviewer gate
on `uat` blocks the job before the plan exists, so approving a deployment approves the
*intent* — this tag, this environment. Reading the plan text is the two-run discipline dev
already uses: run with the latch on `plan`, read it in the log, then set `apply` and re-run.
The tag is immutable, so the second run deploys the same identity as the first.

Where the app-only assertion does read the plan, it pipes `terraform show -json` straight
into `jq` and prints only resource **addresses** — never the document, never a value.

**Concurrency:** both workflows share `group: apicurio-deploy-<env>` with
`cancel-in-progress: false`, so an infra apply and a promote cannot interleave on one
state file, and a cancelled apply cannot leave state locked.

**Credentials resolve by environment, not by name.** Both workflows reference
`secrets.AWS_ROLE_ARN`, satisfied by an **environment-scoped** secret of that name on
each environment. One file then serves every environment with no `if:` per environment.
The repository-level `AWS_ROLE_ARN_UAT` stays as it is, because `deploy-product-uat.yml`
uses it. The same pattern carries `APICURIO_CI_KEY` (G1).

---

## `promote-apicurio.yml`

**Trigger and latch:** as above, `APICURIO_<ENV>_PROMOTE` ∈ `{skip, run}`.

**Steps**

1. Check out the tag's commit; read every input through `read-manifest.sh` (which
    refuses any field from a manifest whose gate did not pass).
2. `copy-image.sh` dev → target ECR, expected digest asserted before the copy and read
    back after.
3. `aws s3 copy` the archive into the target's release-artifacts bucket, then
    `restore-bundle.sh` asserting `ui.sha256`.
4. App-only apply, reusing dev's blast-radius assertion, so promotion can only move the
    BFF task definition and service — **before** the UI publishes, not after: the bundle's
    `config.json` names the live `bff_api_url`, so the API must already be the one this
    promotion is deploying by the time the UI can call it.
5. Write the target environment's own `config.json` — never the source environment's —
    and sync the restored tree to the UI bucket; invalidate CloudFront.
6. QE tier 2 smoke.
7. Cut `release/<env>/<date>.<n>` **only on green smoke**.

The release tag is the evidence that a version ran somewhere and passed. It is never cut
speculatively.

---

## Reuse for prod

| Needed for prod | Cost |
|---|---|
| `backends/prod.hcl`, `envs/prod.tfvars` | two files |
| `bootstrap-environment.sh --env prod` against `216064088366` | one run |
| env-scoped `APICURIO_CI_KEY` on `prod` | one secret |
| `prod` Environment with reviewers and `apicurio/v*` tag policy | created by phase 0 |
| resource policies on dev's promotion sources, granting prod's role read | `aws_ecr_repository_policy` + `aws_s3_bucket_policy`, both new |

Reuse is achieved because the environment is data. Two things must be decided rather
than inherited:

**Cross-account promotion needs a resource policy on the dev side, not only an identity
policy on prod's role.** uat's fix for CRITICAL 1 (`PromotionSourceRead` on
`uif-apicurio-uat-deploy-policy`) is an identity-policy-only grant, and it is sufficient
for uat because uat shares dev's account (`283556840805`): a same-account identity policy
is the whole answer. Prod is a *different* account (`216064088366`), and AWS evaluates a
cross-account request against **both** sides — the requesting principal's identity policy
**and** a resource policy on the resource being read — denying by default unless both
say yes. So an identity-policy-only fix, copied verbatim from uat, is provably
insufficient for prod: it grants the prod role permission to *ask*, but dev's ECR
repository and S3 bucket have no resource policy admitting any principal outside their
own account, and neither has one today. (The stack's only `aws_s3_bucket_policy` is
`frontend.tf`'s CloudFront OAC grant on the UI bucket — nothing touches the
release-artifacts bucket or the BFF ECR repository.) The prod plan must add an
`aws_ecr_repository_policy` on `uif-apicurio-dev-bff-ecr-repo` and an
`aws_s3_bucket_policy` on `uif-apicurio-dev-release-artifacts`, each admitting the prod
deploy role for exactly the read actions `PromotionSourceRead` already lists (`ecr:BatchGetImage`,
`ecr:GetDownloadUrlForLayer`, `ecr:BatchCheckLayerAvailability`, `ecr:DescribeImages`,
`s3:ListBucket`, `s3:GetObject`, `s3:GetObjectVersion`), alongside an identity policy on
the prod role mirroring `PromotionSourceRead` itself. Both sides own resources in dev's
account, which is a cost to sequence into the prod plan rather than discover mid-promote.

**`prod.tfvars` must override, not inherit.** `db_instance_class = "db.t4g.micro"`,
`bff_desired_count = 1` and `registry_desired_count = 2` are variable defaults. They are
defensible for uat and wrong for prod. `frontend_force_destroy` is safe by omission
(defaults false).

**Where prod's state lives.** dev and uat share
`pearson-ingestion-framework-tfstate-283556840805`, one bucket per account. Prod is a
different account, so it needs its own bucket — confirm before phase 0, not mid-apply.

**Prod should gate on uat's release tag.** Prod promotion refuses a build tag with no
green `release/uat/*`. This turns the uat release tag from a record into a precondition
and is the cheapest real safety this design can buy.

---

## Testing

Every script in this chain was mutation-tested, and each one had a real bug or a real
test gap found that way. The workflows get the same treatment where they can:

- **Unit tests** (`qe/tests/unit/`) for each new shell step extracted from the workflows,
  following `test_copy_image_sh.py` and `test_read_manifest_sh.py`.
- **`workflow-lint.yml`** already validates workflow syntax; both new files must pass it.
- **Enterprise allow-list**: every `uses:` must resolve to `actions`, `github`,
  `hashicorp`, `aws-actions`, `aquasecurity`, `sonarsource`, `opentofu` or `pearson*`.
  One disallowed action fails the whole workflow at startup with no logs.
- **The first real run is the test that matters.** `simulate-principal-policy` evaluates
  identity policies only; an SCP or a permissions boundary can still deny at runtime, so
  "all 125 actions allowed" is not proof the apply succeeds.

---

## Risks

- **uat rehearses the application, not the infrastructure.** It shares dev's VPC, both
  `private-css` subnets, dev's single-AZ Secrets Manager endpoint, and dev's sizing.
  Isolation is by resource name. A green uat therefore says nothing about prod's network
  or capacity shape. This spec assumes uat is an *integration* environment, which is what
  `uat.tfvars` currently describes; making it a prod rehearsal changes the tfvars and the
  network and would require revising this section.
- **The first uat apply adds an ingress rule to `sg-0b45c554b51638786`, which Echo owns.**
  A change to another team's security group.
- **RDS costs from phase 2 onward**, whether or not anything is promoted onto it.
- **`main` being 1,019 commits behind** is a latent hazard beyond this spec: any future
  design needing `workflow_dispatch`, or any branch protection keyed to `main`, hits it.

---

## Explicitly rejected

**Generalising `deploy-apicurio-dev.yml` with an `env` input.** One file and one mental
model, but it is ~1,100 lines with dev baked into every job; this would make the stack's
highest-risk file also its most-edited, where a mistake in prod's path is a mistake in
dev's working path. It also re-merges the two operations this design separates.

**Terraform workspaces.** All three environments in one state file, which puts dev inside
prod's blast radius and defeats the per-environment IAM scoping the uat role was built
around.

**A root module per environment.** Forks the code and guarantees drift. The current
one-root, per-environment-inputs shape is already the better answer.

**`workflow_dispatch` as the primary trigger.** Blocked by F1, and worse than the tag
trigger even once unblocked: a tag push carries the identity being deployed, whereas a
dispatch input can name a tag that has nothing to do with the ref the workflow ran from.
