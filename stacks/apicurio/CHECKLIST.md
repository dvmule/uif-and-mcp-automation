# Apicurio Definition of Done

Same shape as a domain's `DOMAIN-CHECKLIST.md` — Design → Build → Ship →
Operate → Trace — adapted for a deployable stack rather than a domain, with a
**Build & Release** section because this stack ships three artifacts rather
than the one a domain's single-image contract assumes.

Last reconciled against the repository and the live account on **2026-09-20**.
Every count and every claim below was re-run or re-read on that date; where a
thing could not be checked from here, the item says so rather than repeating
what it used to say.

## Design (before code)

- [x] Architecture & solution design recorded in `CLAUDE.md`/`README.md`.
- [x] Placement decision made deliberately, not defaulted: `stacks/apicurio/`
      chosen over `capabilities/infrastructure/` (would break the
      never-holds-code rule) and over steward-hosting inside an existing
      domain (worse conceptual fit than Gold-in-product).
- [x] The BFF and UI joined this stack from `capabilities/schema/`
      ([ADR-0012](../../architecture-design/decisions/0012-schema-registry-moves-to-stacks-apicurio.md)),
      retiring ADR-0006's rule-2 exception. The Schema standards moved with
      them to `standards/` — a deliberate cost recorded in ADR-0012 §2.
- [ ] Security posture stated in full. Two of the three parts are now settled:
      the DB credential is RDS-managed (`rds.tf`), so this stack owns no
      Secrets Manager secret and there is no rotation policy of ours to write;
      and the network exposure decision held — `alb_internal = true` in
      `envs/dev.tfvars`, with the public edge being API Gateway in front of the
      BFF rather than the ALB. Still open: least-privilege review of the
      execution and task roles (`../../capabilities/security/`).
- [ ] Observability signals defined — golden signals + SLOs for the registry
      service. `infrastructure/observability/` still does not exist (confirmed
      2026-09-20). This also gates the release tag's evidence, which per the
      two-tag model is smoke *and SLO*.

## Build

- [x] `infrastructure/terraform/` present: ECS Fargate + ALB + RDS + IAM,
      mirroring `stacks/beacon/infrastructure/terraform/`'s proven shape.
      The BFF (`bff.tf`), its public edge (`apigw.tf`) and UI delivery
      (`frontend.tf`) were folded in from six pre-migration root modules;
      `terraform fmt` and `validate` both pass.
- [x] `src/` builds one image (`src/Dockerfile`) and `src/schemas/` carries
      the metadata-block contract the validator enforces — the two strict
      `stacks-ci.yml` gates that apply once real code lands.
- [x] Registry image corrected to `apicurio/apicurio-registry:3.3.2`. The
      previous `apicurio-registry-sql:3.0.6` pin did not exist as an image
      (the `-sql` variant stops at 2.6.13.Final), so a `plan` would pass and
      the ECS task would fail to pull. BFF client ported v2 → v3 with it.
- [x] `pytest` 172 passed (unit); 15 integration tests collect; `vitest` 142
      passed across 12 files; `tsc -b && vite build` clean. Re-run 2026-09-20.
      The unit count rose and the vitest count moved as `swagger-ui-react` came
      out (see UIFPE-467 below) — the two `vi.mock('swagger-ui-react', …)`
      blocks went with it.
- [x] `ruff check src` — **All checks passed** (2026-09-20). This line
      previously recorded 82 report-only findings and asked whether the check
      should become blocking; the findings are gone, so the question is now
      only whether to require it, not whether to fix anything.
- [x] Real VPC/subnet IDs surveyed from the target account. `envs/dev.tfvars`
      carries live values for account 283556840805 — `vpc-0287e62dc748ffab8`
      and two `private-css` subnets — not the `vpc-UPDATE-ME` placeholders this
      item used to warn about. Those subnets are **not** interchangeable with
      others in the same VPC; see the tfvars' own comment.
- [x] `terraform init`/`plan` run and reviewed by a human against real values,
      and applied: several successful `apply` runs from 2026-09-15 onward, and
      the stack is now deployed to dev (see Ship).
- [ ] `GLUE_REGISTRY_NAME`'s AWS Glue Schema Registry provisioned (a
      separate, small Terraform addition — serverless, no compute).
- [ ] `AWS_ROLE_ARN_SCHEMA_GOVERNANCE` OIDC deploy role provisioned for
      `.github/workflows/contract-governance.yml`'s `register-and-sync` job.
- The registry container is still an off-the-shelf image with no code of
  ours in it. What `src/` and `ui/` hold is the **governance layer in front
  of it** — the BFF and the dashboard — not a fork of Apicurio.

## Ship

**Runbook: [`DEPLOY-DEV.md`](DEPLOY-DEV.md)** — the ordered, phase-by-phase
procedure for getting this stack onto dev with Entra deferred. The items
below are the *what*; that document is the *how*: the agreed VPC/subnet
values, the source-IP authorizer stop-gap, the deploy-role policy, and the
three QE tiers.

- [x] CI wired: `.github/workflows/deploy-apicurio-dev.yml`, mirroring
      `deploy-beacon-dev.yml`'s resolve/gate shape (repo variable
      `APICURIO_INFRA` = unset|skip|plan|apply, `terraform plan` always
      runs, `apply` only on that variable or a `workflow_dispatch` input).
      The five Pearson mandatory tag values are supplied as `TF_VAR_*` at
      workflow level and deliberately have no defaults.
- [x] `deploy-apicurio-dev.yml` extended to cover the BFF image and the UI
      bundle — it is no longer infrastructure-only. Three independently
      gated jobs exist (`bff-image`, `infra`, `ui`). The API origin is no
      longer compiled into the bundle — the `ui` job writes it to
      `config.json` at publish time — so the `ui` job still runs after `infra`
      whenever both run, but for the `config.json` write rather than the build.
- [x] `stacks-ci.yml`'s "Build / test / scan" step wired to the real
      pytest/vitest/terraform/container steps, via `qe/ci.sh` — the single
      gate definition both CI and an FDE run. No longer a documented no-op.
      `qe/ci.sh` defines 18 gate slots; how many actually run depends on which
      tools are present on the host, so the reported count is a property of the
      environment rather than of the script. An integration-import gate was
      added 2026-09-20 so that tier 2 collecting cleanly is checked at tier 1 —
      the blind spot that had let two tier-2 bugs through.
- [x] **QE tier 2 runs and passes.** 7 passed / 0 failed against the deployed
      dev stack. It had never executed before 2026-09-20: the smoke job did not
      install the BFF package, so the suite failed at import, and once that was
      fixed it caught a real bare-slash routing bug (`@router.get("/")` under a
      prefix returning a 307 for `/api/v1/schemas`). Both are fixed at source.
- [ ] `stacks-ci gate` added to `develop`'s required status checks.
      **Re-verified 2026-09-20 and still not done**: develop requires only
      `Author email matches a Jira user`, `Every commit carries a Jira key and
      a comment`, and `Title and body are reviewable`. Until the gate is
      required, this stack's whole gate is advisory at the merge boundary — a
      `startup_failure` in `stacks-ci.yml` went unnoticed for 39 consecutive
      runs (2026-09-15 → 2026-09-18) precisely because nothing required it.
      What changed is that requiring it is now *viable*: the gate went green on
      develop for the first time once UIFPE-467 was cleared, so requiring it no
      longer blocks every merge. Requiring one aggregated name rather than the
      matrix legs is deliberate — see that job's comment for why.
- [x] How application code reaches the running service without applying the
      whole stack — **decided 2026-09-18, implemented in #370**. The BFF is
      the application, so shipping an API change is the routine case, and the
      only path had been a full apply of ~68 resources.
      `infra=app` now runs the normal plan, asserts the changed set is a
      subset of `{aws_ecs_task_definition.bff, aws_ecs_service.bff}`, and
      applies the saved plan unchanged — refusing, by name, if any platform
      resource is pending. No `-target`, so no exception to this repo's own
      rule against it, and `applied == reviewed` still holds.
      Two alternatives were rejected with reasons, recorded on **UIFPE-322**:
      `update-service --force-new-deployment` is inert against a
      digest-pinned task definition, and splitting the state would need ten
      resources from the platform layer — the `terraform_remote_state`
      coupling UIFPE-36 removed because it made the old six-module layout
      undeployable from a clean clone.
      Still to do: exercise it in a real deploy window, once where it should
      apply and once where it should refuse. A guard that has never refused
      anything is not yet a guard.
- [x] **Deployed to dev.** The BFF image, the UI bundle and the registry are
      live; the UI was verified by reading the bundle back from CloudFront
      rather than from the build (`assets/index-CIleUmZ-.js`, 993,941 bytes,
      `/config.json` → 200), and tier 2 passes against it.
- [ ] `APICURIO_REGISTRY_URL` repo variable set to the real ALB URL —
      still unset (confirmed 2026-09-20). The moment this happens,
      `.github/workflows/contract-governance.yml`'s compatibility-check and
      register-and-sync jobs stop being no-ops.

## Build & Release

**Design:
[`2026-09-19-apicurio-release-identity-design.md`](../../docs/superpowers/specs/2026-09-19-apicurio-release-identity-design.md).**

Two tags answer two different questions, and that split drives everything
here. A **build tag** answers *what code went into this artifact*, so its
evidence is the CI gate. A **release tag** answers *what was deployed, where,
when*, so its evidence is smoke and SLO. Building never implies deploying.

Half 1 — build identity — is **done**. Half 2 — promotion identity — is
planned and blocked on the items below.

- [x] The version is declared in a file, not inferred. `VERSION` holds one
      line; `release-apicurio.yml` tags what it reads on a bump landing on
      develop. Semver on a capability is a claim about whether consumers
      break, so only a human can assert it.
- [x] Build tags cut: **`apicurio/v0.1.0`** and **`apicurio/v0.1.1`**, both
      2026-09-20. `v0.1.1` is a patch: no capability was added, and #384's path
      change moved the only consumer in lockstep.
- [x] **All three deployable artifacts carry an immutable reference.** Before
      this work only one did. The guide places this stack in the easy column
      because "Apicurio builds one container image" — it builds three, and two
      were soft: the UI bundle was `aws s3 sync`'d to the bucket root with
      `--delete` and no version recorded, and the registry ran the *mutable*
      upstream tag `quay.io/apicurio/apicurio-registry:3.3.2`. Now: ECR digest,
      bundle sha256 + S3 version_id, and a pinned `registry_image_digest` that
      the release re-resolves upstream and warns about on drift.
- [x] The manifest lives in the annotated tag's message, mirrored to a
      non-draft GitHub Release, so the tag and its evidence are one object
      readable offline with `git show apicurio/v0.1.1`.
      `scripts/release-manifest.sh` refuses to write a manifest recording a
      truncated digest, an uppercase digest, a mutable tag where a digest
      belongs, or a gate that did not pass — a manifest that lies is worse than
      none, because it looks exactly like evidence.
- [x] The UI bundle is environment-agnostic, which is what makes "build once,
      promote" true for all three artifacts rather than two. The API origin
      arrives at runtime in `config.json`, excluded from both the archive and
      the hash (`scripts/bundle-hash.sh`) because it is environment state.
- [x] **`v0.1.1`'s manifest independently verified against what is deployed.**
      A local rebuild at `4fdfad1` with CI's npm 10.8.2 reproduced
      `ui_bundle.sha256` exactly (`523a46c5cd80b03e…`), and its main chunk
      matched the deployed asset by name and byte count. So the Vite bundle is
      reproducible and the manifest's hash can be re-derived from source; the
      Docker image is not reproducible, which is why the image row records a
      digest rather than a rebuild recipe. `v0.1.0`'s manifest could not make
      this claim — its BFF digest was two generations stale.
- [x] Build tags are immutable and it is enforced, not just intended. Ruleset
      `immutable_release_tags` is **active** on `refs/tags/apicurio/v*` with
      `deletion` and `non_fast_forward` rules and an **empty bypass list** — so
      no one, repository admins included, can move or delete a build tag.
      (This answers UIFPE-482's open question in the strictest direction.)
- [x] The release digest walk resolves to a commit that can actually have an
      image. Images are built for pushes to develop, so the plain
      `git rev-list -- src/` walk returned feature-branch commits that never
      get built — a set disjoint from the buildable one whenever a PR lands as
      a merge commit, which made the step fail every time. It now tries HEAD
      first, then `--first-parent`.

### Environments — dev, uat, prod

Account topology, owner decision **2026-09-20**: **dev and uat share AWS
account 283556840805**; **prod is 216064088366**. Echo is the precedent and
already carries this exact shape. Same-account dev→uat removes cross-account
access from promotion entirely — no ECR repository policy, no cross-account S3
read.

**dev stays branch-based**: it is the build environment, so continuous deploy
from `develop` is correct there. **uat and prod are tag-based only** — nothing
reaches them except by naming a build tag, and that is enforced by the target
GitHub Environment's `apicurio/v*` deployment *tag* policy rather than by a
workflow `if:`, so a dispatch from a branch is refused before any step runs.

**Artifact stores are per-environment, with a copy step** (owner decision
2026-09-20). The build tag keeps the build store's references; each release tag
carries the target environment's own references, produced by the copy. Two
tags, two questions, two sets of references — so the differing S3 `version_id`
is the release tag's data rather than a defect in the build tag.

- [x] The module already accepts `env ∈ {dev, uat, prod}` — validated in
      `variables.tf`, with `t_environment` mapping `uat → STG` (the Pearson
      vocabulary has no UAT value) and four prod safety switches keyed off it.
- [x] `var.bff_image_digest` has **no default**, so an environment must say
      which artifact it runs and cannot answer "whatever is newest". Tag-based
      deploy therefore needs no module change for the BFF.
- [x] **A cross-repository image copy preserves the digest, and
      `scripts/copy-image.sh` refuses when it does not.** Measured against a
      local `registry:2` on 2026-09-20 (Docker 29.7.2, overlay2), not assumed:

      | source shape | `docker` pull+tag+push | `crane copy` |
      |---|---|---|
      | single architecture | **preserved** | preserved |
      | multi-architecture index | **changed** | preserved |

      This corrects what this item previously claimed. Plain `docker` *did*
      preserve the digest for the shape this stack builds — `src/Dockerfile`
      is built `--platform=linux/amd64`, one architecture — so the earlier
      "does not guarantee it" was speculation, and the measurement disagrees
      with it. The multi-arch row is the real warning: `docker` pulls one
      platform and pushes it as a single manifest, silently discarding the
      rest. 15 of 16 platforms vanished with no error from any command.
      Crane is still the right tool, for two reasons that hold regardless:
      it copies the manifest verbatim, and it **mounts** existing blobs
      instead of re-uploading them, so a same-account ECR copy moves no layer
      bytes. `aws ecr batch-get-image` + `put-image` remains no alternative —
      ECR stores layers per-repository, so `put-image` fails on any repository
      whose layers are absent, which is every first copy. Install crane by
      `curl` in a `run:` step, never a third-party `uses:`, which the
      enterprise allow-list rejects at workflow startup.
      The guard is mutation-tested: four separate breakages of the comparison,
      the pin check and the read-back ordering each fail the suite.
- [ ] Confirm the same on ECR itself, on the first real copy. The mechanism is
      proven against a conformant registry, but ECR's own behaviour — and
      whether it honours cross-repository blob mounts — has not been observed
      here, since these tests need no AWS account. `copy-image.sh` asserts the
      digest either way, so an ECR surprise fails the promotion rather than
      shipping quietly.
- [ ] Apply the env-scoped deploy policy.
      `../../capabilities/infrastructure/uif-apicurio-dev-deploy.policy.json`
      has **10 of its 25 resource ARNs** scoped to `uif-apicurio-dev-*`,
      covering ECR, ECS cluster and service, IAM roles, RDS instances and
      subnet groups, S3, and both WAF resources — so an `env=uat` apply 403s on
      every one of them *even in the same account*. Same account does not mean
      same permissions.
      **`infrastructure/deploy/bootstrap-environment.sh <env> --render-policy`**
      generates the target environment's document from dev's reviewed one,
      retargeting the env token and the account and adding
      `s3:GetObjectVersion` (UIFPE-481, without which a rollback 403s at the
      moment it is needed). It renders and stops; a second run creates the
      managed policy, attaches it, and then **simulates six actions against the
      attached role** — because "the policy was attached" and "the role can now
      do the thing" are different claims and only the second one matters.
      `uif-apicurio-uat-deploy.policy.json` is rendered and committed; applying
      it needs an AWS session and the target role's name.
- [x] `backends/uat.hcl` and `envs/uat.tfvars` written. **Five of the six
      network values are identical to dev's, and that is a property of the
      estate rather than a copy-paste** — an earlier note here claimed none
      were derivable, which was wrong. `gip-uif-nonprod-vpc-euw1` is *the*
      non-prod VPC in the account; its two `private-css` subnets are the only
      ones of that class, with no az3, so uat cannot have its own; the Secrets
      Manager interface endpoint is the account's single endpoint; and the two
      CIDR lists describe who may reach the API, which does not vary by
      environment. Each value carries its own reason in the file.
      Subnet capacity was checked rather than assumed, since dev's stack now
      occupies them: **53 of 59 free in az1, 55 of 59 in az2**, so a second
      stack of the same shape leaves roughly 47 and 51.
      Two deliberate divergences from dev: `frontend_force_destroy` is
      **omitted**, taking its safe default of `false`, because uat's UI bucket
      holds what a release actually published and a `terraform destroy` that
      emptied it would remove what a rollback republishes; and `cors_origins`
      is a **temporary** `http://localhost:5173` for pass 1 only, since the
      variable's own validation rejects a blank value and uat's CloudFront
      domain does not exist until the first apply. **Pass 2 must replace it
      with the uat domain alone** — nobody develops against uat, so localhost
      is a browser origin on a shared environment that no one needs.
- [ ] Run the first uat `terraform plan` and review it. Nothing has been
      applied in uat; only the IAM role, the policy, the GitHub Environment and
      the role-ARN secret exist.
- [ ] **uat shares dev's VPC, both subnets and the same single-AZ Secrets
      Manager endpoint**, so isolation between them is by resource name only —
      separate RDS, ECS cluster, ALB, buckets and ECR repository, no network or
      account boundary. Acceptable for a non-prod promotion target, and
      explicitly not the prod posture (216064088366 is a separate account).
      Recorded here so it is a known property rather than a later discovery.
- [ ] The uat deploy role itself. dev borrows the shared
      `echo-dev-github-actions`; uat gets its own `uif-apicurio-uat-deploy-role`,
      pairing with the policy name above per this stack's `-role`/`-policy`
      convention, so a uat apply cannot reach beacon's or product's resources.
      `bootstrap-environment.sh <env> --create-role` mints it.
      **Its trust policy cannot be hand-written.** This repository issues
      **immutable (id-qualified) OIDC subjects** — verified 2026-09-20 from
      `repos/…/actions/oidc/customization/sub`, which returns
      `use_immutable_subject: true` and
      `sub_claim_prefix = repo:pearson-dnt-coe@183024320/universal-Integration-framework@1308482856`.
      So the subject is that prefix plus `:environment:<env>`, and the
      unqualified `repo:<org>/<repo>:environment:<env>` **never matches**,
      failing as an opaque `sts:AssumeRoleWithWebIdentity` denial at the first
      deploy. The script reads the prefix from the API rather than hardcoding
      it, and grants `environment:<env>` only — no branch subject, since a role
      trusting a branch ref would let a push-triggered workflow assume it and
      bypass the environment's reviewer entirely.
      `DEPLOY-DEV.md` describes dev's trust as the unqualified form; that is
      loose wording rather than a second format, since one repository issues
      one subject shape.
- [ ] Fix `infrastructure/terraform/modules/github-oidc-deploy-role/`, which
      builds `repo:${github_org}/${github_repo}:…` with no ids — so any role
      created from it on this repository would never match. Latent trap for
      every stack, not just this one.
- [ ] `AWS_ROLE_ARN_UAT` (only `AWS_ROLE_ARN_DEV` exists), and a `uat` GitHub
      Environment created **explicitly, with a named reviewer and the
      `apicurio/v*` tag policy, before any workflow references it**.
      Referencing `environment: uat` first auto-creates it bare — no reviewer,
      no policy — which is how "restricted release authority" becomes a
      sentence in a document. The only environments today are `copilot` (no
      rules) and `dev` (branch policy for develop/main, **zero reviewers**).
      UIFPE-479.
      The same bootstrap script does this, and **refuses to create a non-dev
      environment without `--reviewer`**: a tag policy with no approver still
      lets anyone who can dispatch a workflow promote. It sets
      `prevent_self_review` so the person who dispatched cannot approve their
      own promotion, removes any branch policy left on a tag-only environment,
      sets `AWS_ROLE_ARN_<ENV>`, and **reads the protection back** — the API
      accepting a `PUT` is not evidence that protection exists. It aborts if
      the read-back shows no reviewer rule or no tag policy, on the grounds
      that a half-configured environment is worse than none.
- [ ] Extract the deploy jobs into a `workflow_call` workflow with thin dev and
      uat callers, rather than forking the file. dev keeps its push trigger;
      uat dispatches from a tag. The dev workflow is ~960 lines of hard-won
      logic and a fork would drift.
- [ ] `promote-apicurio.yml` — the tag-based deploy. Check out the *tag's*
      commit (module code travels with the tag; values are environment state),
      parse the manifest from the tag message, copy both artifacts into the
      target environment's stores **asserting the image digest and the bundle
      sha256 are unchanged**, restore the bundle and write that environment's
      own `config.json`, apply with the manifest's digests passed as variables,
      run tier 2, and cut the release tag `release/<env>/<date>.<n>` **only on
      green smoke** — a release tag whose evidence is smoke cannot precede it.
      Three of its pieces exist and are mutation-tested, none of them needing
      AWS to be verified: `scripts/read-manifest.sh` (reads a build tag's
      manifest, refusing a lightweight tag, a message describing a different
      tag, an absent field, a malformed digest, and any field at all from a
      manifest whose gate did not pass), `scripts/copy-image.sh` above, and
      `scripts/restore-bundle.sh` (extracts the released archive and refuses
      unless the tree still hashes to the manifest's `ui_bundle.sha256`,
      removing the destination when it does not, so a rejected bundle cannot be
      left where a later `aws s3 sync` would publish it). Re-hashing rather
      than checksumming the tarball is deliberate: a byte-comparison proves a
      copy happened, not that the bundle inside is the one the release
      identified.
      What is left is the workflow that sequences them — the S3 copy between
      buckets is `aws s3 cp` and needs no script of its own — and the release
      tag itself.
- [ ] `releases/<env>.yaml` — deliberately deferred, not forgotten. Blocked on
      ADR-0019 (UIFPE-480), whose schema binds Echo, Beacon and Harbor and is
      therefore not this stack's call to make unilaterally. The release tag
      alone is sufficient identity until it is ratified.
- **No stack in this repository has a working uat or prod pipeline to copy
  from.** Echo has uat and prod tfvars but no uat or prod deploy workflow;
  product has both workflows, but neither is on the default branch (`gh run
  list` answers HTTP 404 "workflow not found on the default branch") and
  neither `AWS_ROLE_ARN_UAT` nor `_PROD` exists. Apicurio would be the first,
  so the workflow shape is new work.
- [ ] **uat smoke credential** — not yet set. `uif/apicurio/uat/auth/ci`
      cannot exist until the uat stack's first apply creates it
      (`authorizer.tf`); the stack has never been applied, so the secret is
      not there to put a value into yet. Once it exists, one generated value
      goes into both halves — Secrets Manager and an environment-scoped
      `APICURIO_CI_KEY` on `uat` — so tier 2 has something to present.
      Environment-scoped rather than a second repo-level name, so the
      workflow needs no `if:` per environment and dev's key is never
      presented to uat. Commands in [`DEPLOY-UAT.md`](DEPLOY-UAT.md).
- [ ] **First uat deployment** — runbook in [`DEPLOY-UAT.md`](DEPLOY-UAT.md).
      Crosses both workflows and has a documented second pass for
      `cors_origins`, because the CloudFront domain does not exist until the
      first apply has run.

## Operate

- [ ] **Authentication — a stop-gap is live; real identity is not.** This item
      previously read "there is none, anywhere", which is no longer true: an
      API Gateway Lambda authorizer exists (`infrastructure/terraform/authorizer.tf`)
      admitting a source-IP allowlist plus a shared CI key, verified
      2026-09-20 by a green authorizer health check and by
      `test_allows_request_with_ci_key` / `test_denies_request_without_credential`.
      It remains a stop-gap rather than authentication: a shared key yields no
      principal, no per-user authorization and no audit trail of who called
      what. Entra integration is parked on a separate branch, and
      `APICURIO_AUTH_ENABLED` is still unset on the registry container itself.
      Highest-severity open gap — see `ARCHITECTURE.md`.
- [ ] Move the compliance cache and the Delivery Lab watermark store out of
      process memory. Until then `bff_desired_count` must stay at 1: two
      tasks behind the ALB would serve divergent state.
- [ ] Decide the Delivery Lab / CDC arm's fate. It migrated as-is and is
      still half-built — the `cdc-poller` source does not exist in this
      repo, so the Product tab has no data producer.
- [ ] Operational ownership registered in JSM (`../../jira/jsm-alignment.md`).
- [ ] Runbook written once deployed. `DEPLOY-DEV.md` is the first-deploy
      procedure, not an operational runbook, and promotion will need its own.

## Trace

- [ ] Work links Capability (Schema) → Epic → Story in Jira (`../../jira/`).
- Release-identity work traces to **UIFPE-456** (the parent story) with
  Half 2's blockers raised as **UIFPE-478** (second environment; carries the
  verified account topology), **UIFPE-479** (release authority / environment
  reviewer), **UIFPE-480** (ADR-0019 ratification), **UIFPE-481**
  (`s3:GetObjectVersion` for rollback) and **UIFPE-482** (tag-ruleset bypass —
  now answerable from the active ruleset above).
