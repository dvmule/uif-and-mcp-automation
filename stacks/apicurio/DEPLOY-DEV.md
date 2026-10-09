# Apicurio — first deploy to dev

**One resource of this stack has been applied; everything else has not.** On 2026-09-14
`aws_ecr_repository.base_python` — the mirror holding the SIF base image — was created with a
targeted apply, which also created the state object at `uif-apicurio/dev/terraform.tfstate`.
A full plan against that state reports **68 to add, 0 to change, 0 to destroy**. Nothing else
in `infrastructure/terraform/` has touched an AWS account, so this is still not a redeploy
runbook — it is the ordered list of work
between the code as it stands and a working dev environment, with the facts each step needs
already surveyed. Once dev is live and stable, the operational half of this document becomes
`RUNBOOK.md` and this one is deleted.

Read it top to bottom the first time. The order is load-bearing in four places, each marked
**order matters** with the reason, because getting them wrong produces a failure that looks
like something else.

**Scope:** registry + RDS + BFF + public edge + UI, in the ESB dev account
(`283556840805`, `eu-west-1`). **Entra federation is deliberately out of scope** — that work
lives on `feature/apicurio-entra-federation-design`, whose design is recorded there as
*"Registry federates into the GIP Hub origin"*. No link is given on purpose: that ADR is
numbered 0013 on its own branch, and `develop` already has a different ADR-0013
(`0013-schema-versioning-and-consumer-migration.md`). The collision is that branch's to
resolve before it merges; this document will not hard-code either path. This deploy ships an
IP-allowlist stop-gap in Entra's place, described under Phase 1.

## Corrections since this document was approved

Twelve things turned out differently once the work started. They are listed here rather than
quietly edited into the phases below, because the plan was reviewed and approved in its
previous form and a silent rewrite would hide what changed. The phases themselves have been
corrected too; this is the index of what moved and what the evidence was.

**Five of these are errors in this document's own earlier text.** They are marked *(my
error)*. **Number 12 is a live standards breach, not just a wrong sentence** — it is the only
entry here that describes something already changed in AWS and needing reversal.

| # | What changed | Evidence |
|---|---|---|
| 1 | **`/apis/registry/v2` does exist on Apicurio 3.3.2** *(my error)*. Phase 5b said it does not, and so does `src/app/core/config.py`. The v2 compatibility API is enabled by default and answers 200; only `/apis/registry/v1` 404s. The consequence is worse than the error: the seed scripts that lack a health check run today and write silently through the deprecated surface | Probed against a live `quay.io/apicurio/apicurio-registry:3.3.2` container and the upstream `openapi.json` at tag `v3.3.2` |
| 2 | **Every `DELETE` returns 405.** 3.3.2 ships `apicurio.rest.artifact.deletion.enabled=false` and nothing overrode it, so no cleanup path existed and the seed scripts could never be re-run idempotently. `ecs.tf` now sets the three `APICURIO_REST_DELETION_*` variables, defaulting to non-prod only | Verified live: 405 stock, 204 with the variables set |
| 3 | **`terraform validate` works; the failure was one Terraform release** *(my error)*. An earlier commit on this branch claims validate can never pass on a partial backend. Terraform **1.15.0** alone validated `backend` block attributes statically; reported within hours, called an oversight by its own author, reverted in 1.15.1. 1.15.8 validates this module successfully. So validate stays in the gate, CI pins 1.15.8, and `required_version` does **not** exclude 1.15.0 — the bug affects validate alone, and blocking init/plan/apply over a broken static gate would be out of proportion | hashicorp/terraform#38021 (cause), #38466 and #38468 (revert), 1.15.1 CHANGELOG; and 1.15.8 run against this module |
| 4 | **Nine renames were eleven, and one was a silent overwrite.** Phase 0b's table missed `-registry-secrets-policy` (which named the grant, not the role holding it). And under the pairing rule *both* of `bff_task`'s inline policies resolve to the same compliant name — two `aws_iam_role_policy` resources sharing one name on one role is not an error Terraform reports; the second `PutRolePolicy` overwrites the first and the losing grant vanishes with no diff. The documents are merged into one policy per role instead | `identity-secrets.md` "Keep the role/policy pairing"; plan output |
| 5 | **A security-group conflict this document never mentioned.** `alb.tf` declared inline `ingress`/`egress` blocks on a group `bff.tf` added a separate rule resource to. Inline blocks are authoritative, so the `aws_security_group` resource revokes any rule it did not declare. The first apply works and the *next* one removes the BFF's path to the registry ALB. All 11 inline blocks across four files are now separate rule resources | AWS provider documentation on mixing the two forms; the two call sites |
| 6 | **The Secrets Manager endpoint seat is on someone else's security group.** Phase 0b called `sg-0b45c554b51638786` the endpoint's group, which it is — but it is not a dedicated one. It is `echo-uat-glue-eni`, the Glue worker group, reused. The seat is therefore an additive rule on another workload's resource, which beacon already agreed for the same reason. The endpoint also has an ENI in `private-glue-az1` **only**, not in either `private-css` subnet, so credential resolution is a cross-AZ single-AZ dependency | `describe-vpc-endpoints` and `describe-security-groups`, 2026-09-14 |
| 7 | **OIDC is not a blocker, and a plan can run from this branch** *(my error)*. Phase 3 worried the trust policy admits only `refs/heads/develop`. The `infra` job declares `environment: dev`, so the claim GitHub presents is the environment form on *any* branch and the trust policy matches it. The `dev` environment has no branch policy and no required reviewers | The role's trust policy; `deploy-apicurio-dev.yml`'s job declaration; `gh api repos/.../environments` |
| 8 | **The "Execution" decision's stated reason is wrong** *(my error)*. It says `workflow_dispatch` needs the file on `main` first, which is what GitHub documents. Eleven successful dispatches exist in this repo of two workflows that have never been on `main`. The documented rule is not what governs here. Honest limit: every one of those used `ref=develop`, so a dispatch against a *feature* ref remains unverified — it is a cheap, non-destructive test | `gh run list`, `gh workflow list --all`, `git log origin/main` |
| 9 | **Stacks CI is `disabled_manually`.** Phase 2's tier-1 gate is not "wire a step" — the whole workflow is switched off at the GitHub level, which no commit can change. `APICURIO_INFRA` is also unset, so the Terraform job has never once executed, and both existing runs of this workflow were no-ops | `gh workflow list --all`; run `34527005990` |
| 10 | **The deploy-policy section had the right conclusion and the wrong mechanism.** The `role/uif-*-dev*` wildcard is real and does cover this stack — but it lives in the managed policy `echo-dev-deploy-2`, which has no tracked copy in the repo. All three *inline* policies scope IAM to `echo-dev-*`/`uif-echo-dev-*`. Two consequences: the new policy must be **managed**, because the three inline documents total 10,130 of the 10,240-byte cap and leave 110 bytes; and a shared role holding role-CRUD over every `uif-<anything>-dev*` name in the account is a wider blast radius than the tracked files suggest, which belongs in the remediation backlog | `simulate-principal-policy` against the live role; `get-role-policy` byte counts |
| 11 | **No seed script sets a compatibility rule — at all.** That is the mechanism behind "0 of 13 live artifacts have a rule", which had been recorded as a fact without a cause. Phase 5b's compatibility-rule item is therefore not an extra; it is the missing half of seeding | `grep` across `src/scripts/`: the only match is a description string |
| 12 | **Phase 3 breaks a standard, and the standard is right** *(my error, and the most serious one here)*. `capabilities/infrastructure/README.md` line 52 states: *"A module never adds its own deploy policy. Per-module policies scoped to a module's own name prefix are what produced the confusing failure this rule exists to prevent… Ask for these documents to be extended instead."* `uif-apicurio-dev-deploy-policy` is exactly that — per-module, and scoped `uif-apicurio-dev-*` on eight of ten statements. I read that file for the policy *contents* and cited it in a commit message, and still walked past the rule at the top of it; this document cited it nowhere, which is why the reasoning reads as sound and lands outside the standard. **The capacity defence fails:** `echo-dev-deploy-1` is 3,040 bytes and `-2` is 3,638, both at 1 of 5 version slots, and the whole payload folds into the two with 2,454 bytes spare. **The defect is real and was reproduced live:** a hypothetical `uif-harbor-dev-*` resource is `implicitDeny` under my policy but `allowed` under the shared env-scoped documents — the failure the rule names, which never bites the module that introduces it. `uif-beacon-deploy-1/-2` are labelled "older… cleanup, not a prerequisite" by the same paragraph, so they are legacy, not licence; mine is the only module-scoped policy on that role postdating the rule | `capabilities/infrastructure/README.md:52-60`; live `get-policy-version` byte counts; `simulate-principal-policy` against a substituted stack name |

One thing this list does not contain: the registry image. The `quay.io` choice was already
recorded below with the real reason — Pearson's Docker Hub org policy blocks
`apicurio/apicurio-registry` outright. Worth knowing that the Docker Hub *tag API* answers 200
for 3.3.2, so "the tag exists" is not evidence the pull is permitted.

## Decisions already taken

Recorded here so a reader does not re-litigate them, and so the reasoning survives the people
who were in the room.

| Decision | Choice | Why |
|---|---|---|
| Network | `vpc-0287e62dc748ffab8`, the two `private-css` subnets | Owner instruction. Surveyed live 2026-09-14; both carry NAT egress and the corporate VGW routes |
| Auth, pending Entra | Lambda authorizer on source-IP allowlist, plus a header secret for CI | `apigw.tf` says "NO AUTHORIZER" in as many words. HTTP APIs support neither WAF nor API keys, so an authorizer is the only lock available. A secret compiled into a public SPA is not a secret, hence IP rather than secret for browsers |
| Execution | PR to `develop`, then CI with `APICURIO_INFRA` | The only route that leaves an audit trail. **Correction 8:** the reason first given here — that `workflow_dispatch` needs the file on `main` — is what GitHub documents but is not what governs this repo |
| UI delivery | This stack's own CloudFront (`frontend.tf`) | Self-contained. The Entra branch federates the UI into the GIP Team Hub's distribution instead; that coupling is not worth taking on for a first deploy |
| Registry image | `quay.io/apicurio/apicurio-registry:3.3.2` | Pearson's Docker Hub org policy rejects `apicurio/apicurio-registry` outright — `denied: image access restricted … blocked for organization pearson`. quay.io is Apicurio's other official registry. See `docker-compose.yml` |
| DB credential | RDS-managed (`manage_master_user_password = true`) | Owner decision. `random_password` persists the password in Terraform state, which the S3 backend encrypts but does not hide from anyone who can read state. RDS-managed keeps it out of state entirely and adds rotation. The cost is stated under Phase 0b — it is not free |

## Surveyed facts

Read from the account on 2026-09-14 with `aws-runas pearson-esb-dev`. Re-verify before an
apply if significant time has passed; do not substitute values.

```
VPC   vpc-0287e62dc748ffab8   gip-uif-nonprod-vpc-euw1   10.116.48.0/21
      subnet-086ed7f5de377bb91   private-css-az1   10.116.54.128/26   eu-west-1a   59 free
      subnet-0b323a493f21f4a87   private-css-az2   10.116.54.192/26   eu-west-1b   59 free
      per-AZ NAT (nat-0eba01cd27a104273 / nat-0996df748ac029748)
      VGW routes for 10/8, 172.16/12, 192.168/16 and the Pearson 159.182.* ranges
```

Two AZs is exactly the minimum for both an ALB and a DB subnet group, and ~20 ENIs fit
comfortably in two `/26`s.

**The Secrets Manager endpoint is the one that will bite.** This VPC has exactly one
interface endpoint — `vpce-0742f8ea5aa68967e`, **private DNS enabled**, ENI only in
`private-glue-az1`. Private DNS means every Secrets Manager call from anywhere in the VPC is
forced through it; the NAT path never applies. Its security group is `sg-0b45c554b51638786`
(`echo-uat-glue-eni`, owned by Echo's Terraform) and admits `:443` from exactly two sources:
itself, and Beacon's deliver SG.

`ecs.tf` uses native ECS secret injection for the registry's DB credential, which the ECS
agent resolves from the task's **own** ENI. Without a seat at that endpoint the registry task
never reaches `RUNNING`, and it presents as a Secrets Manager connection timeout — a
network-shaped symptom for a security-group-shaped cause. Beacon lost time to this in
UIFPE-241; Phase 0b mirrors its fix rather than rediscovering it.

The deploy role is `echo-dev-github-actions` (its trust policy names
`repo:pearson-dnt-coe/universal-Integration-framework` on `refs/heads/develop` and
`environment:dev`), exposed to the workflow as `secrets.AWS_ROLE_ARN_DEV`.

---

## Phase 0 · Integrate

No AWS access needed. Nothing here depends on anything outside the repo.

- [x] Rebase onto `origin/develop`. Expect conflicts in `.gitignore`,
      `domains/price/src/schemas/README.md` and `domains/product/src/events/asyncapi.yaml`.
      `architecture-design/standards/rules/naming/00-core.md` **should no longer conflict** —
      PR #281 and #282 landed the `apicurio` token and the new rows on `develop`, so this
      branch inherits them instead of fighting over them. Confirm that rather than assume it.
- [x] Lift two corrections from `feature/apicurio-entra-federation-design`, both
      Entra-independent:
      `bff.tf`'s `CORS_ORIGINS` value becomes `jsonencode(split(",", var.cors_origins))`,
      and `cors_origins` loses its `default = ""`. Bring
      `qe/tests/test_config_cors_origins.py` with them.
      **This is not cosmetic:** pydantic-settings parses a `list[str]` field's env value as
      JSON, so a bare comma-separated string raises `SettingsError` at import, killing the
      container before uvicorn binds a port. It fails on the *current* `cors_origins = ""`
      too, so the BFF would crash-loop on the first apply.
- [x] `registry_image` default → `quay.io/apicurio/apicurio-registry:3.3.2`.
- [x] `required_version` → `>= 1.10`. `backends/dev.hcl` sets `use_lockfile`, which is a
      1.10 feature; the current `>= 1.6` floor would let an older binary silently run without
      state locking.
- [x] All local gates green — **except the integration suite, which is the only part still
      open.** `qe/ci.sh` runs the rest and was verified at **12 passed, 0 skipped, 0 failed**
      with every tool present and Terraform 1.15.8: `ruff`, `pytest` (123), `npm ci`,
      `vitest` (134), `tsc -b && vite build`, `terraform fmt`, `terraform init`,
      `terraform validate`, `tflint`, `trivy config` on both the module and the image, and
      `docker build`.
- [ ] `qe/tests/integration/` against `docker compose up`. Deliberately still open: it needs a
      local Apicurio on 8080 and a local BFF, and it is the one gate CI cannot run either
      (the registry ALB is internal). Two of its tests also assert a non-empty artifact list,
      so they need the `requires_seed` marker from Phase 5 before this is meaningful against
      a fresh registry.

      **`terraform validate` is weaker than it looks**, and that caveat survives the tick: it
      does not evaluate variable validation blocks, so it reports success on configurations a
      `plan` refuses, and it batches errors and stops, so a guard that did not fire may never
      have been reached. The real `plan` — 68 to add, 0 to change, 0 to destroy — is the check
      that counts here.

## Phase 0b · Network and naming

- [x] `envs/dev.tfvars` takes the surveyed values, with the reasoning inline so the next
      reader does not "tidy" them:

```hcl
vpc_id             = "vpc-0287e62dc748ffab8"        # gip-uif-nonprod-vpc-euw1, 10.116.48.0/21
private_subnet_ids = ["subnet-086ed7f5de377bb91",   # private-css-az1  10.116.54.128/26  eu-west-1a
                      "subnet-0b323a493f21f4a87"]   # private-css-az2  10.116.54.192/26  eu-west-1b
secretsmanager_endpoint_sg_id = "sg-0b45c554b51638786"
cors_origins                  = "http://localhost:5173"   # replaced in Phase 4; see there

# connect.pearson.com. External range = the public address API Gateway sees; client range =
# the VPN adapter's own address, which only the internal ALBs ever see. See Phase 1.
api_allowed_cidrs = ["159.182.0.0/16"]
vpn_client_cidrs  = ["10.122.0.0/15"]
```

- [x] Add the endpoint seat, mirroring `aws_vpc_security_group_ingress_rule.deliver_secretsmanager_access`
      in `stacks/beacon/infrastructure/terraform/lambda.tf`: a `secretsmanager_endpoint_sg_id` variable
      (empty = no rule, so sandbox stays unaffected) and one additive
      `aws_vpc_security_group_ingress_rule` referencing the registry task SG.
      **It must carry `replace_triggered_by`** — the deploy role holds
      `ec2:AuthorizeSecurityGroupIngress` and `Revoke` but **not**
      `ec2:ModifySecurityGroupRules` (confirmed by simulation, denied at `*` as well as on
      that group), so every change to this rule has to be a replacement. An in-place update is
      refused, and that failure surfaces halfway through an apply.

      **Two corrections to what this document called that group** (**correction 6**). It is
      the endpoint's security group — and its only one — but it is not a dedicated endpoint
      group: it is `echo-uat-glue-eni`, described as the Glue ENI group, reused. So the seat is
      an additive rule on another workload's resource, which is why beacon agreed it with the
      platform side rather than just adding it. Its sole inbound 443 rule today is beacon's.

      And the endpoint has an ENI in **`private-glue-az1` only** — not in either `private-css`
      subnet. Credential resolution from a task in `private-css-az2` is therefore cross-AZ,
      and single-AZ: if that one ENI's subnet goes, no task in this stack can start.

      **The NAT route does not substitute for any of this**, which is the part worth being
      sure about before the apply. Both `private-css` subnets have an active NAT default route,
      so it is tempting to conclude the public Secrets Manager endpoint is reachable and the
      seat is optional. It is not: the interface endpoint has private DNS **enabled**, so
      `secretsmanager.eu-west-1.amazonaws.com` resolves VPC-wide to that ENI and a task is
      never handed a public address to route over NAT.
- [x] **Eleven renames**, not the seven this list first claimed and not the nine its table
      held (**correction 4**). All are pre-first-apply, so no `moved` blocks and no
      replacement risk — and doing them now is the whole point, because `00-core.md`'s "never
      rename a deployed resource" makes them expensive the day after the first apply.

| Resource | From | To |
|---|---|---|
| `aws_ecs_cluster` | `uif-apicurio-dev` | `uif-apicurio-dev-cluster` |
| `aws_ecs_service` ×2 | `…-registry`, `…-bff` | `…-registry-service`, `…-bff-service` |
| `aws_ecs_task_definition` ×2 | `…-registry`, `…-bff` | `…-registry-task-definition`, `…-bff-task-definition` |
| `aws_ecr_repository` | `uif-apicurio-dev-bff` | `uif-apicurio-dev-bff-ecr-repo` |
| `aws_s3_bucket` | `${account_id}-s3-uif-apicurio-dev-ui` | `uif-apicurio-dev-ui` |
| `aws_cloudfront_distribution` | comment `Apicurio governance UI — …` | comment `uif-apicurio-dev-ui` |
| `aws_iam_role_policy` | `…-bff-execution-policy` on the **task** role | merged, see below |
| `aws_iam_role_policy` | `…-registry-secrets-policy` **(added)** | `…-registry-execution-policy` |
| `aws_db_instance` | `uif-apicurio-dev-db` | `uif-apicurio-dev-registry-db` |
| `aws_db_subnet_group` | `uif-apicurio-dev-db-subnet-group` | `uif-apicurio-dev-registry-db-subnet-group` |
| `aws_cloudfront_origin_access_control` | `uif-apicurio-dev-ui` | unchanged — already compliant |

`-registry-secrets-policy` was missed first time round because it looked compliant: it has the
stem, the stack, the env and the `-policy` suffix. What it does not have is the *pairing*.
`identity-secrets.md` names an inline policy after the role holding it, so the policy on
`uif-apicurio-dev-registry-execution-role` is `…-registry-execution-policy`. Naming the grant
instead of the holder is exactly what lets a policy and its role drift apart unnoticed, which
is the defect the `-bff-execution-policy` row already records.

**Two inline policies on one role cannot both be renamed — they have to be merged.** Under the
pairing rule, `-bff-execution-policy` and `-bff-ssm-policy` both resolve to
`uif-apicurio-dev-bff-task-policy`, because both sit on `bff_task`. Terraform does not report
that as an error: two `aws_iam_role_policy` resources with one name on one role means the
second `PutRolePolicy` overwrites the first, and whichever grant lost is simply gone, with no
plan diff to show it. `iam.tf` now builds one document — the ECS Exec statement plus a
`dynamic` SSM statement gated on `cdc_tables_ssm_parameter` — and attaches one policy.

This is a gap in the rule, not just in this stack: the pairing form cannot express two inline
policies on one role. One-policy-per-role is the resolution here and should be written into
`identity-secrets.md` rather than left as local knowledge.

The installed naming guard enforces every row above **except the CloudFront comment** —
`comment` is not in its `NAME_ATTRS`, so that one is the only rename you have to remember. All
31 judgeable names in the plan were run through the guard: none denied.


- [x] Delete `aws_appautoscaling_target` and `aws_appautoscaling_policy`. The deploy role has
      no `application-autoscaling` grant, and with `bff_max_count = 1` they do nothing. Two
      reasons to delete rather than request a permission.

### Hand the DB credential to RDS

`rds.tf` currently generates the password with `random_password` and stores it in a secret
this stack owns. Terraform persists every generated value in state, so the password is in
state whatever the variable is called — the old variable description in the pre-migration
module claimed otherwise and was simply wrong. RDS-managed credentials remove it from state
and add rotation.

- [x] `rds.tf`: set `manage_master_user_password = true`, drop the `password` argument
      (setting both is an error), and delete `random_password.db`,
      `aws_secretsmanager_secret.db` and `aws_secretsmanager_secret_version.db`. Keep
      `username = "apicurio"`.
- [x] `ecs.tf`: point both injected secrets at the RDS-owned secret. The managed secret's
      JSON carries `username` and `password` keys, so the `:key::` syntax is unchanged and
      this is a one-line-per-entry edit:

```hcl
secrets = [
  { name = "APICURIO_DATASOURCE_USERNAME",
    valueFrom = "${aws_db_instance.this.master_user_secret[0].secret_arn}:username::" },
  { name = "APICURIO_DATASOURCE_PASSWORD",
    valueFrom = "${aws_db_instance.this.master_user_secret[0].secret_arn}:password::" },
]
```

- [x] `iam.tf`: the execution role's `secretsmanager:GetSecretValue` resource becomes
      `aws_db_instance.this.master_user_secret[0].secret_arn`. Still one secret, still
      least-privilege, different owner.
- [x] **The endpoint seat is still required.** ECS still fetches a Secrets Manager secret at
      task start, so nothing about Phase 0b's endpoint rule changes — only which secret is
      fetched.

**Three consequences, none of them blockers, all worth knowing before the first apply.**

*Rotation can break a running task.* Apicurio reads the datasource password once, at
container start. RDS rotation changes the password in the database; existing pooled
connections keep working, but new ones authenticate with a credential the container no longer
holds. The failure therefore appears later as a task that cannot reconnect, not at rotation
time. Mitigation is a forced new deployment after a rotation; detection is the container
health check failing on DB reconnect. `random_password` never rotated, so this risk is new
and belongs in `RUNBOOK.md`.

*The secret's name is AWS's, not ours.* RDS names it `rds!db-<resource-id>`, which cannot be
set and does not follow `identity-secrets.md`'s `uif/{stack}/{env}/{system-type}/{system}`
form. That is an AWS-owned name in the same class as the `$default` API Gateway stage and the
account's `default` event bus, and the naming standard should record it as such — filed as a
follow-up, not smuggled into this deploy.

*Encryption key.* The managed secret uses the Secrets Manager default KMS key unless a
customer-managed key is named, and the key cannot be changed once RDS is managing the
credential. Default is correct for dev. If the ECS task fails to fetch the secret with a KMS
error rather than a timeout, this is where to look — a timeout is the endpoint seat, a denial
is KMS.

## Phase 1 · Auth stop-gap

The public API Gateway proxies `ANY /{proxy+}` straight to a BFF with no authentication,
including `POST /api/v1/schemas/{group}/{artifact}`. This is gap 1 in `ARCHITECTURE.md` and
the same finding that was confirmed exploitable on the pre-migration SIT deployment. It is
not acceptable to deploy open, and Entra is out of scope, so:

- [x] `authorizer.tf`: an `aws_apigatewayv2_authorizer` of type `REQUEST`
      (`uif-apicurio-dev-bff-authorizer`) plus a small Python function
      (`uif-apicurio-dev-authorizer`) that allows when
      `requestContext.http.sourceIp` falls inside `api_allowed_cidrs`, **or** when the
      `x-uif-ci-key` header matches a secret in Secrets Manager. `authorization_type = CUSTOM`
      on both routes. Plan goes 61 → 68 resources.
- [x] Two credentials paths on purpose: browsers authenticate by network position, because
      anything the SPA carries is public; CI authenticates by secret, because GitHub-hosted
      runner IPs move. Neither is a substitute for user identity — this is a lock on the
      door, not a sign-in.
- [ ] **Set the CI key by hand, once, in TWO places.** Generate one value and put it in
      both: `aws secretsmanager put-secret-value --secret-id uif/apicurio/dev/auth/ci
      --secret-string "$(openssl rand -hex 32)"`, which is what the authorizer compares
      against, and `gh secret set APICURIO_CI_KEY`, which is what the smoke job presents.
      Terraform creates the container and deliberately no `aws_secretsmanager_secret_version`,
      so no value is ever generated into state or into a plan. Until the Secrets Manager value
      is set, the header path denies everything and the source-IP path still works — the right
      failure order, but tier 2 cannot run. *Why two copies rather than one read:* the smoke
      job used to fetch the value with `secretsmanager:GetSecretValue`, which the deploy role
      is denied on purpose — `CiSecretContainerNoValueAccess` grants the container, never the
      value, and `uif-apicurio-dev-phase3-deploy-policy.sh` asserts that denial as a boundary.
      Holding CI's copy in an Actions secret keeps that boundary intact; the price is drift
      between the two, which the smoke job reports as a 403 in so many words.
- [ ] Delete this file's worth of code in one commit when the Entra authorizer lands.

**Three implementation details that are load-bearing, not incidental.**

*Caching is off, and it has to be.* `authorizer_result_ttl_in_seconds = 0`. A simple-response
decision is cached against the **identity sources only** — not against the source IP — so any
TTL above zero means one allowlisted request caches an `allow` that is then served to *any*
address until it expires. The control would be bypassed by design.

*The CI header is not an identity source.* If it were listed as one, API Gateway would return
401 to any request lacking it **without invoking the function at all** — so every browser
request would be rejected before its IP was ever examined. `identity_sources = []` is what
makes the two paths coexist.

*Failure is closed, everywhere.* A malformed or absent source IP denies rather than raising,
because an exception becomes a 500 and a security control must fail shut, not confusing. The
header is compared with `hmac.compare_digest`. And if the secret has no value set yet, the
source-IP path still works while the header path denies — verified across 21 handler cases,
including an unset secret, a throttled Secrets Manager, an IPv6 source against an IPv4
allowlist, and a header value that is a correct prefix of the secret.

### The two Pearson ranges do different jobs

`connect.pearson.com` (the current VPN) has an **external range `159.182.0.0/16`** and a
**client range `10.122.0.0/15`**. They are not interchangeable, and putting the wrong one in
the wrong place produces config that reads as a working rule and enforces nothing.

```hcl
api_allowed_cidrs = ["159.182.0.0/16"]   # the PUBLIC address API Gateway sees
```

`10.122.0.0/15` is the address on the VPN client's own adapter. API Gateway never sees it —
traffic reaches the public edge already translated to the external range — so listing it in
`api_allowed_cidrs` would be dead config. Every `159.182.*` VGW route in this VPC
(`159.182.76.0/23`, `159.182.120.0/24`, `159.182.13.10/32` and eight more) falls inside the
/16, which corroborates it.

**Be honest about what a /16 buys.** This is a perimeter control: anyone on Pearson's network
reaches the write endpoints, including `POST /api/v1/schemas/{group}/{artifact}`. It closes
the internet-facing hole that made gap 1 exploitable, and it is not identity. That is the
trade being made until Entra lands, and it is the reason this stop-gap is dev-only.

### Where `10.122.0.0/15` does belong (optional, recommended)

Both ALBs currently admit only `data.aws_vpc.this.cidr_block` (`10.116.48.0/21`), so a
VPN-connected laptop cannot reach the registry directly and every check in Phase 5 has to go
through ECS Exec. Since the client range routes back through the VGW
(`10.0.0.0/8 → vgw-0cb5ded80beb3388e` is already in both `private-css` route tables), one
optional variable makes the internal ALBs reachable from the VPN:

- [x] Add `vpn_client_cidrs` (`default = []`, so it is off unless set) and widen both ALB
      security groups to `concat([data.aws_vpc.this.cidr_block], var.vpn_client_cidrs)`.
      Set it to `["10.122.0.0/15"]` in `envs/dev.tfvars`.

This is worth having for a stack nobody has ever operated: it turns "exec into a task to
curl an endpoint" into "curl the endpoint". It widens the internal ALBs to the VPN client
range, which is a deliberate dev-only choice — leave the variable empty in any environment
where that is not wanted, and note that the registry itself still has
`APICURIO_AUTH_ENABLED` unset, so in-VPC reachability is unauthenticated reachability.

## Phase 2 · CD wiring

`deploy-apicurio-dev.yml` provisions infrastructure only. The BFF image and the UI bundle
have no CD at all today.

- [x] A `bff-image` job: build `src/Dockerfile`, tag with the commit sha, push to ECR, and
      pass `-var bff_image_tag=$GITHUB_SHA` to Terraform. Tagging by sha rather than
      `latest` is what makes a deploy identifiable after the fact.
- [x] A `ui` job, gated independently of `APICURIO_INFRA` so shipping a UI change never
      means applying Terraform unattended: build the bundle, write
      `config.json` from the `bff_api_url` output, sync to the bucket,
      invalidate `/index.html` and `/config.json`. The Entra branch
      has a working version of this job to adapt — re-point it at this stack's own bucket
      and distribution rather than the hub's.
### Wire the regression gate — QE tier 1

**Done** — `stacks/apicurio/qe/ci.sh` plus the `stacks-ci.yml` wiring that calls it. Recorded
here because two things about it differ from what this section originally said.

The gate is a **script the stack owns**, not a list of steps in the workflow. Two callers
share one definition — CI, and an FDE about to push — for the same reason
`.githooks/lint-commits.sh` exists for the commit convention. The root `CLAUDE.md`'s actual
requirement is not an editor; it is that a contributor "can run the same checks CI enforces
before you push", which is only true if there is one thing to run. `stacks-ci.yml` now probes
for `src/pyproject.toml`, `ui/package.json` and `infrastructure/terraform`, installs only the
toolchains it finds, and runs `qe/ci.sh` if the stack has one — so this file's own convention
holds, that adding a stack needs no workflow edit. Beacon, echo and harbor have no such
script and stay green, saying so in a notice rather than silently.

- [x] Gates, all blocking: `ruff`, `pytest` (123 unit), `npm ci`, `vitest` (134), `tsc -b`,
      `vite build`, `terraform fmt`, `terraform init`, `terraform validate`, `tflint`,
      `trivy config`, `docker build`. That is **257 unit tests**, not the 253 this document
      said — the four new `test_config_cors_origins.py` cases are Phase 0's.
- [x] `terraform validate` **is** in the gate. An earlier commit on this branch says it can
      never pass here; that was wrong (**correction 3**). CI pins 1.15.8, on which it passes;
      the script skips it loudly on exactly 1.15.0, citing the issue, so a laptop with the bad
      release gets an explanation rather than a mystery.
- [x] A missing tool is a **failure** in CI. Only a local run may pass
      `--allow-missing-tools`, and the summary then names every gate that did not run and
      states that the result is not evidence they would pass. A scanner that reports success
      without running is worse than no scanner, because it looks like coverage.
- [x] `tsc -b` and `vite build` are separate gates: esbuild strips types without checking
      them, so a green `vite build` proves nothing about types.
- [x] Leave the integration suite **out** of this job. It needs a reachable registry, and
      the registry ALB is internal — a GitHub-hosted runner cannot see it. Trying to run it
      here produces a skip that looks like a pass. Tier 2 below covers what CI *can* reach.
- [x] **Stacks CI enabled** (**correction 9**). It was `disabled_manually` at the GitHub level,
      which no commit can change — `gh workflow enable stacks-ci.yml`, now `active`.

      Checked before flipping it, because this workflow runs on **every** pull request touching
      `stacks/**` and a failure would be someone else's: the structural gate passes for all
      four stacks today. `beacon` and `echo` declare `src/NO-IMAGE.md`, `harbor` is
      scaffold-only, `apicurio` ships an image and is the only stack with a `qe/ci.sh`. Nobody
      else's pull request goes red because this was switched on.

      Also found by checking: the workflow installed python, node, terraform and tflint but
      **never trivy**, so the gate would have hit its own `missing_tool` path and failed on the
      first run — red for a workflow bug rather than for the code. Fixed in the same breath,
      and `ruff` pinned while there, since ruff adds rules in minor releases and an unpinned
      install would eventually fail a pull request that changed nothing.
- [ ] `Domain CI` and `Standards Review` are still `disabled_manually`. Left alone deliberately
      — they gate `domains/**` and `architecture-design/**`, so switching them on is not this
      deploy's call to make. But note what it means for this branch: **the naming-guard CI check
      it relies on may not be running either.** Worth confirming before trusting a green tick.
- [ ] Set `APICURIO_INFRA`, or dispatch with an input. It is unset, so `resolve` falls to
      `skip` and the Terraform job has **never executed** — both existing runs of this
      workflow were no-ops. Nothing has yet proven the role assumption for this stack
      end to end.
- [ ] Decide on `ruff format`. It reports 32 files non-compliant; the lint gate is green
      without it. That is a separate formatting commit, not something to fold in here.

### Two corrections to how the workflow runs Terraform

- [x] **Apply the reviewed plan, do not re-plan.** The workflow writes `-out=tf.plan` and
      then ignores it:

```yaml
- name: Plan
  run: terraform plan -input=false -var-file=envs/dev.tfvars -out=tf.plan
- name: Apply
  run: terraform apply -input=false -auto-approve -var-file=envs/dev.tfvars   # plans again
```

      What gets applied is therefore not what anyone reviewed. Change the apply step to
      `terraform apply -input=false tf.plan`. Drop `-auto-approve`: applying a saved plan does
      not prompt, so the flag adds nothing and reads as if it were doing the approving.

      **Plan and apply must stay in the same job.** Never upload `tf.plan` as a CI artifact —
      `grep` finds nothing in it because the file is compressed, but `terraform show -json`
      prints the lot. Same runner, same filesystem, nothing persisted.

      The original reason given here was that the plan holds generated secrets, and that is
      now **not true of this stack**: RDS owns the DB password, and the authorizer's CI secret
      is a container Terraform creates with no version, so no value is generated anywhere.
      The rule stands regardless — a plan carries every resolved input, including the VPC and
      subnet ids, the account id and the full IAM documents, and it is a reconnaissance
      artifact even with no credential in it. It is also the wrong thing to keep for a
      different reason: a plan is only valid against the state it was made from, so an
      artifact that outlives its job invites someone to apply a stale one.

- [x] **Add the static-analysis gate** the root `CLAUDE.md` already claims CI enforces:
      `tflint`, plus `trivy config .` or `checkov -d .`, on every path to apply. The infra
      backlog's G16 records that neither runs today. Failing the build on findings is the
      point; report-only is what let ruff accumulate 94 findings that blocked nothing.

## Phase 3 · Deploy-role permissions

All seven of the role's policies were read on 2026-09-14. Seven gaps, every one verified by simulation rather than
suspected. This is self-service: `PCMPowerUser` is `allowed` for `iam:CreatePolicy`,
`iam:CreatePolicyVersion` and `iam:AttachRolePolicy`, and the role has 4 of 10 managed-policy
slots used.

| # | Gap | Effect |
|---|---|---|
| 1 | **No `rds:` action anywhere** | `aws_db_instance`, `aws_db_subnet_group` denied |
| 2 | `ecs` cluster/service actions scoped to `cluster/uif-beacon-*` | apicurio cluster and both services denied |
| 3 | `ecr` scoped to `repository/uif-beacon-*`, **and no push actions exist at all** | repo denied; image push impossible |
| 4 | S3 create/put scoped to `echo-dev-*`, `uif-echo-dev-*`, `uif-beacon-*` | `uif-apicurio-dev-ui` denied |
| 5 | `secretsmanager:CreateSecret` scoped to `secret:uif-*-dev*` — **hyphens** | Phase 1's authorizer secret at `uif/apicurio/dev/...` denied |
| 6 | No `application-autoscaling` | resolved by deletion in Phase 0b |
| 7 | `cloudfront:CreateDistribution` allowed, **`CreateDistributionWithTags` not** | `default_tags` is set, so the provider calls the WithTags variant. The allowed action is the wrong one — found only by simulating against the real call |

Gap 5 is the inverse of the trap `identity-secrets.md` warns about: the standard mandates
slashes, the policy wildcard assumes hyphens, and nothing fails until `CreateSecret` runs. It
survived the move to RDS-managed credentials — RDS creates the database secret itself, so the
deploy role never needs `CreateSecret` for it, but Phase 1's authorizer secret is still
created by Terraform under `uif/apicurio/dev/`.

**No grant is needed on the RDS-owned secret.** It is read by the ECS *execution* role, which
this stack's own Terraform creates and scopes, and the deploy role's existing
`iam:PutRolePolicy` on `role/uif-*-dev*` already covers writing that policy. Do not widen the
deploy policy to `secret:rds!db-*` — nothing in the pipeline reads that secret.

Already sufficient, needing no change — every line of this was confirmed with
`iam simulate-principal-policy` against the live role, not read off the policy documents:
`elasticloadbalancing:*`, `apigateway:*` (covers the HTTP API, VPC link and authorizer),
`cloudfront:*`, `logs:` create/retention/tag on both target log groups, `lambda:*` on
`function:*dev*` (covers the authorizer), EC2 security-group create/authorize/revoke,
`ecs:CreateCluster`/`RegisterTaskDefinition`/`TagResource`, Secrets Manager
`CreateSecret`/`DescribeSecret`/`TagResource` on hyphen-form names, IAM role CRUD on
`role/uif-*-dev*`, and state-bucket access including the `.tflock` object.

**Three things the simulation corrected about that list** (**correction 10**):

*The `role/uif-*-dev*` wildcard is real, but not where this document implied.* All three
**inline** policies scope IAM to `echo-dev-*`/`uif-echo-dev-*` only. The wildcard lives in the
**managed** policy `echo-dev-deploy-2`, which has no tracked copy in the repo — so the
conclusion here was right and the mechanism was guesswork. Worth its own backlog item: a
shared CI role holding role-CRUD and `PassRole` over every `uif-<anything>-dev*` name in the
account is a wider blast radius than the tracked files suggest.

*`PassRole` is conditioned on `iam:PassedToService`, and only some services are listed.*
`ecs-tasks.amazonaws.com` and `lambda.amazonaws.com` are allowed, which is all this stack
passes. `rds.amazonaws.com` and `apigateway.amazonaws.com` are **denied** — so the day someone
adds RDS enhanced monitoring or a REST API needing a CloudWatch role, it fails at apply, not
at plan. Nothing to do now; worth knowing why.

*The new policy must be managed, not inline.* The three inline documents total **10,130 bytes
of the 10,240-byte cap** — 110 bytes of headroom. A fourth inline policy is not an option
whatever its size. (`echo-dev-github-actions-inline` and `echo-dev-github-actions-policy` are
near-duplicates; deduplicating them would free about 3.5 KB if inline is ever needed again.)

There is **no explicit `Deny`** anywhere across the seven documents, and the role carries no
permissions boundary — so the whole gap is closeable by adding a policy. Note the limit of
this method: `simulate-principal-policy` evaluates identity policies only, so SCPs and
resource-based policies (an S3 bucket policy, an ECR repository policy) remain unverified by
this route.

- [x] **Done, 2026-09-14.** `uif-apicurio-dev-deploy-policy` created (v1) and attached to
      `echo-dev-github-actions`, which now carries 5 of 20 managed-policy slots (the AWS default is 10; this account is raised to 20). All nine
      required actions verified `allowed` **against the attached role**, not against the
      document; both boundaries this policy owns verified still denied. Three pre-existing
      grants reported and not blocking — see the last table in this file.

      Re-runnable, and worth re-running: it is the standing check that nothing has widened.
      The paths below are repo-root-relative; the script itself resolves the
      policy document from `git rev-parse --show-toplevel`, so it runs from any directory.
      `PCMPowerUser` is `allowed` for every call it makes — self-service, no platform ticket.

```bash
eval $(aws-runas -e dev)     # or: /Users/raj9karu/.aws/aws-runas pearson-esb-dev bash <the script>

stacks/apicurio/infrastructure/deploy/uif-apicurio-dev-phase3-deploy-policy.sh --dry-run
stacks/apicurio/infrastructure/deploy/uif-apicurio-dev-phase3-deploy-policy.sh
```

A script rather than three pasted commands, for reasons that are not
convenience:

- **It refuses to run against the wrong account.** `echo-dev-github-actions` exists in more
  than one Pearson account, and attaching an apicurio-scoped policy to the wrong one is
  *silent* — it grants nothing that account uses, so nothing breaks and nothing tells you.
- **It is idempotent.** Re-running compares the live default version against the tracked
  document, creates a new version only if they differ, and prunes the oldest non-default
  version first, because five is the hard limit and the sixth edit would otherwise fail.
- **It verifies in both directions.** Nine actions the apply needs must come back `allowed`,
  and three that were meant to stay denied must still be denied: `GetSecretValue` on the CI
  authorizer secret, `ModifySecurityGroupRules` on the shared Echo group, and `iam:DeleteRole`
  on the deploy role itself. A paste checks the first set; only the second set tells you the
  policy is no wider than intended.
- **The two failure classes get opposite advice**, because they are opposite problems. An
  action that should be allowed and is not usually means a resource name moved — re-plan and
  compare, do not widen. An action that should be denied and is not means the grant is too
  wide, or something already on the shared role provides it, and that is a finding about the
  role rather than something to accept here.
- **`--rollback` detaches then deletes**, in that order, because a policy with attachments
  cannot be deleted, and it clears non-default versions first for the same reason. Safe at any
  point: the document is purely additive with no `Deny`, so detaching only un-grants the
  apicurio-scoped actions and cannot affect Echo, Beacon or the gold pipeline.

Verified by stubbing the AWS CLI and exercising every branch — happy path, wrong account,
a needed action denied, a denied action allowed, and rollback — so each assertion was seen
failing before it was trusted. Exit codes: 0 clean, 1 on any check failure, 2 on a bad
argument.

**On the name:** the precedent in this account is `uif-beacon-deploy-1` / `echo-dev-deploy-1`,
which carry no `env` token and predate the current convention. Those are legacy by design;
`identity-secrets.md` governs *new* policies, so this one takes
`uif-{stack}-{env}-{service}-policy`. The deviation from precedent is deliberate.

The document is tracked at
[`capabilities/infrastructure/uif-apicurio-dev-deploy.policy.json`](../../capabilities/infrastructure/uif-apicurio-dev-deploy.policy.json),
alongside the copies of the role's existing policies. **3,195 bytes minified**, against the
6,144-byte managed-policy limit. Ten statements:

| Sid | Actions | Resource |
|---|---|---|
| `Rds` | 11 | `db:uif-apicurio-dev-*`, `subgrp:uif-apicurio-dev-*` |
| `Ecs` | 6 | `cluster/uif-apicurio-dev-*`, `service/uif-apicurio-dev-*/*` |
| `Ecr` | 19 | `repository/uif-apicurio-dev-*` |
| `EcrAuthUnscopable` | 1 | `*` |
| `UiBucket` | 23 | `s3:::uif-apicurio-dev-*` |
| `UiBucketObjects` | 3 | `s3:::uif-apicurio-dev-*/*` |
| `CloudFrontCreateWithTagsUnscopable` | 1 | `*` |
| `CiSecretContainerNoValueAccess` | 6 | `secret:uif/apicurio/dev/auth/*` |
| `ModifyOwnSecurityGroupRules` | 1 | `security-group/*`, conditioned on `aws:ResourceTag/Project = uif-apicurio` |
| `ModifySgRulesRuleResource` | 1 | `security-group-rule/*` |

Three actions have to be `*` because AWS supports no resource-level permission for them:
`ecr:GetAuthorizationToken`, `cloudfront:CreateDistributionWithTags`, and the
`security-group-rule/*` companion is already as narrow as that resource type allows.

**Four things about this document that are not obvious from reading it.**

*`cloudfront:CreateDistributionWithTags` is a gap neither this checklist nor the first pass at
the policy had.* The role is allowed `cloudfront:CreateDistribution` — which is false comfort.
`default_tags` is set on the provider, so Terraform calls the **`WithTags`** variant, and that
one is denied. This would have failed the apply at the CloudFront distribution, with a denial
naming an action the policy appears to grant.

*`ModifySecurityGroupRules` needs two statements, not one.* The action authorizes against both
the security group **and** the security-group rule, so a tag-conditioned `security-group/*`
statement on its own matches nothing. The companion must be scoped to
`security-group-rule/*` — **not** `*`, which would match the security-group ARN too and
silently grant the action on the shared Echo group, defeating the tag gate entirely. That
mistake was made and caught during verification, which is the only reason it is written down.

*The tag condition is what keeps `replace_triggered_by` load-bearing.* `ModifySecurityGroupRules`
stays denied on `sg-0b45c554b51638786` — verified both when tagged `Project=echo` and when
untagged — so the endpoint-seat rule must still be replaced rather than updated, exactly as
Phase 0b says. The grant covers only groups this stack tags itself. Note it is not exercised by
the first apply at all: 68 creates means every rule goes through `Authorize…`, and this
statement earns its place on the *second* apply.

*No grant is needed on the RDS-owned secret, confirmed rather than assumed.*
`rds:DescribeDBInstances` returns `master_user_secret`, so Terraform never calls Secrets
Manager for it. `DescribeSecret`, `GetSecretValue` and `ListSecretVersionIds` on `secret:rds!db-*`
are all denied, correctly. The only reader is the ECS execution role, a different principal.

Likewise `CiSecretContainerNoValueAccess` grants container lifecycle and **no**
`GetSecretValue` or `PutSecretValue`, matching `authorizer.tf`'s design: Terraform creates the
container, an operator sets the value, and only the function's own role reads it.

**Verified end to end:** a sweep of roughly 110 actions across all twelve services the stack
touches, against the real resource names on this branch, returned zero non-`allowed` results
with this document supplied via `--policy-input-list` — i.e. proven before anything is
attached. The limits of that method, stated because they matter: `simulate-principal-policy`
evaluates identity policies only, so SCPs and resource-based policies are not covered, and the
Secrets Manager VPC endpoint may carry an endpoint policy worth a separate look.


- [ ] Confirm the plan's resource names match those wildcards before applying. Every
      statement is scoped to `uif-apicurio-dev-*`, so a name that skips the convention is
      denied at apply — which is the intent, not a bug to work around by widening the policy.
- [ ] Watch item, not yet verified: `aws_db_instance` sets `storage_encrypted = true` with
      the default `aws/rds` key. AWS-managed keys normally need no explicit `kms:` grant for
      the caller. If the apply fails on KMS, that is the cause.

## Phase 4 · Apply

**Order matters, three times.**

- [ ] Set `APICURIO_INFRA=plan`, push to `develop`, and read the plan. Nothing applies.
- [ ] `terraform apply -target=aws_ecr_repository.bff` **first**, then build and push the
      image, then the full apply. *Why:* Terraform creates the ECR repository empty and the
      BFF service in the same apply, so the service has no image to pull and sits failing
      while the apply reports success. Targeted applies are a bootstrap escape hatch; this is
      what it is for.
- [ ] Full apply with `cors_origins = "http://localhost:5173"`. Read `ui_url`. Set
      `cors_origins` to `https://<distribution>.cloudfront.net,http://localhost:5173` and
      apply again. *Why:* the CloudFront domain does not exist until after the first apply,
      and `cors_origins` is now a required variable with no default, so it cannot be left
      blank to be filled in later.
- [ ] Publish the UI **last**, so `config.json` can be written from the `bff_api_url`
      output, then sync and invalidate. *Why:* the bundle no longer carries the origin, so
      the build itself is environment-independent — but a publish before the API Gateway
      exists has no origin to write, and a UI served with no `config.json` fails at boot
      with an explicit error rather than looking like a CORS failure.

## Phase 5 · Verify

Nothing below is satisfied by a green apply.

- [ ] `aws ecs describe-services` — both services at `runningCount` = `desiredCount`. If the
      registry task cycles, read its stopped-task reason before anything else: a Secrets
      Manager timeout here means the Phase 0b endpoint seat is missing or wrong.
- [ ] Registry reachable: `/apis/registry/v3/system/info` returns 200. With
      `vpn_client_cidrs` set, `curl http://<registry-alb-dns>/apis/registry/v3/system/info`
      works from a VPN-connected machine. Without it, the ALBs admit only the VPC CIDR and
      this has to go through ECS Exec (`enable_execute_command` is already on for the BFF).
- [ ] `GET /api/v1/health` reports `"state": "up"`, not `"degraded"` (lower-case — the
      response serialises a `StrEnum`). `degraded` means the BFF cannot reach the registry,
      and the response is still HTTP 200 — so the ALB health check passes either way and this
      has to be read, not inferred.
- [ ] The authorizer denies a request from outside `api_allowed_cidrs`, and allows one from
      inside. A 403 from the wrong cause looks identical to a 403 from the right one, so test
      both directions.
- [ ] The UI loads over CloudFront and lists schemas. An empty list with a browser CORS error
      means the bundle was built before the API Gateway existed (Phase 4).
### QE tier 2 — automated smoke, from where CI can reach

The existing integration suite **cannot pass against a fresh deploy as written**, and the
reason is worth understanding before anyone runs it and concludes the deploy is broken:

```python
# qe/tests/integration/test_bff_connectivity.py
assert len(body) > 0, "Schema list is empty — Apicurio has no artifacts"
```

A brand-new registry *is* empty. The assertion is correct about a populated environment and
wrong about this one.

- [ ] Split the seeded-data assumption out: mark `test_returns_non_empty_list` and
      `test_items_have_required_fields` with a `requires_seed` marker, registered in
      `pytest.ini`, so an empty registry passes tier 2 and those two run in tier 3.
- [ ] Add a `smoke` job to `deploy-apicurio-dev.yml`, gated separately from `APICURIO_INFRA`,
      running against the **public API Gateway** with `BFF_URL=<bff_api_url>` and
      `RUN_INTEGRATION_TESTS=1`. That path is reachable from a GitHub-hosted runner; the
      registry ALB is not.
- [ ] The smoke job needs the authorizer's CI secret in its header, not an allowlisted IP —
      runner addresses move. This is the second credential path Phase 1 exists to provide,
      and the smoke job is its first real consumer.
- [ ] Three new tests, covering what this deploy introduces and nothing currently touches:
      the authorizer allows an allowlisted source and denies another; a pre-flight `OPTIONS`
      from the real CloudFront origin returns the expected `Access-Control-Allow-Origin`;
      and `/api/v1/health` reports `"up"`, which only passes if the RDS-managed secret was
      injected and the registry came up.

Tier 3 — the full regression run — needs a populated registry and is Phase 5b.
- [ ] Set the `APICURIO_REGISTRY_URL` repo variable. **Know what this does:** it is the
      moment `contract-governance.yml`'s compatibility-check and register-and-sync jobs stop
      being no-ops on every PR in the repo. Deliberate, not incidental.
- [ ] Unset `APICURIO_INFRA`. Left on `apply`, every push touching `stacks/apicurio/**`
      applies Terraform with nobody reading the plan.

## Phase 5b · QE tier 3 — full regression

Immediately after dev is up, not before: every item here is easier to write against a
registry that exists, and blocking the deploy on them delays the thing that makes them
testable. Budget about half a day.

- [ ] **Port the seed scripts to v3.** This item was written on a wrong premise
      (**correction 1**) and the real picture is worse. `/apis/registry/v2` *does* answer on
      3.3.2 — the v2 compatibility API is enabled by default — so the v2 path default is not
      the blocker. Two things actually are:

      **The health check.** Five scripts probe `{base}/health`, which 404s on 3.3.2 because
      readiness moved to the management port, and they `sys.exit` before doing anything. Those
      five fail loudly and harmlessly.

      **The other five have no health check, so they run.** They write through the deprecated
      v2 surface right now, and `seed_catalog_svc.py` and `inspect_apicurio.py` hardcode the
      v2 path with no env override, so they cannot even be redirected. `inspect_apicurio.py`
      is the one to fix first: it reads `properties` where v3 has `labels`, so it reports "no
      metadata" for every artifact — a diagnostic tool that lies about whether seeding worked.

      **Do not fix the path defaults alone.** Flipping `APICURIO_API_PATH` to v3 without the
      request-shape fixes turns today's loud failure into a 404 on
      `ifExists=RETURN_OR_UPDATE` (the v3 enum is `FIND_OR_CREATE_VERSION`, and JAX-RS answers
      404, not 400, which reads as "endpoint missing") plus `KeyError: 'id'` tracebacks where
      v3 returns `artifactId`. That is strictly harder to diagnose than the current state.

      The shape changes, all verified against a live 3.3.2 container: artifact id and type
      move from `X-Registry-*` headers into a JSON body, content nests under
      `firstVersion.content.content`, `PUT …/artifacts/{a}/meta` becomes
      `PUT …/artifacts/{a}`, `labels` is a map rather than a list, and `properties` is
      **accepted with a 204 and discarded** — silently, so the ADR-0001 derived index never
      lands and the BFF's `properties_mismatch` then fires on every artifact.

      Also: `src/scripts/apicurio-clean.py` is a byte-identical duplicate of
      `cleanup_apicurio.py` whose own usage text names the other file. Delete it rather than
      port both. `test_refs.py` is a v2 one-off whose question v3 answers in its schema —
      delete that too. `src/scripts/` has no test coverage at all, which is exactly the
      asymmetry that let the v2→v3 port catch the client and miss the scripts.

      Deletion must be enabled for any of this to be re-runnable — see
      `registry_deletion_enabled`, already wired in Phase 0b. **Without this item there is no
      working way to populate dev.**
- [ ] Seed dev, then run the full integration suite — both files, including the
      `requires_seed` tests — with `REGISTRY_URL` pointed at the registry ALB. This needs
      `vpn_client_cidrs` set (Phase 1) or an in-VPC runner; it is not reachable from CI.
      Passing this closes `ARCHITECTURE.md` gap 11, where the v3 client port is asserted
      against `respx` mocks and a local container but never a deployed registry.
- [ ] Parameterise Playwright's `baseURL`, currently hard-coded to `http://localhost:5173`
      in `ui/playwright.config.ts`, and run the 12 e2e specs against the CloudFront URL.
- [ ] Decide what the compatibility-rule regression looks like. The SIT registry had **0 of
      13 artifacts with a compatibility rule**, which is the single thing
      `capabilities/api/guidelines.md` cites to justify Apicurio — "an incompatible push is
      rejected by the registry itself, not caught only by a human reviewer". Registering a
      rule and proving an incompatible push is rejected is the test that makes that claim
      true rather than documented.
- [ ] Record the real test counts in `ARCHITECTURE.md` and `CHECKLIST.md`. Both currently
      assert `pytest` 119 / `vitest` 134 with nothing recomputing them — the same staleness
      class as the naming package's counts, which needed a test to stop drifting.

## Phase 6 · Record

- [ ] `ARCHITECTURE.md` §4: gap 10 (nothing deployed) closes. Gap 1 becomes "IP-allowlist
      stop-gap; Entra pending". Gap 11 closes if the integration suite ran against dev.
- [ ] `CHECKLIST.md` Ship section: tick "Deployed to dev" and link this file.
- [ ] Write `RUNBOOK.md` and delete this document.
- [ ] **QE Lead sign-off.** Four-in-a-Box names the role and `CHECKLIST.md` has no QE gate
      for a deploy, so this is the step that would otherwise be nobody's. What is being
      signed off is specific: tier 1 blocking on PRs, tier 2 green against dev, tier 3
      scheduled with an owner — not "tests pass".
- [ ] Register operational ownership in JSM (`../../jira/jsm-alignment.md`).
- [ ] Trace the work under UIFPE-306 (Infrastructure & Deployment).

## Rollback

Nothing exists yet, which makes the first apply unusually safe to undo and unusually easy to
undo carelessly.

- **Run `terraform plan -destroy` and read every resource it lists before running a
  destroy.** Locals and `for_each` pull in implicit dependents, so a targeted destroy can
  remove more than the resource you named. Never `-auto-approve` a destroy.
- `frontend_force_destroy = true` and `skip_final_snapshot` on non-prod mean a destroy of
  this stack takes the UI bucket and the database with it. That is correct for dev and is
  exactly what makes the command dangerous the day dev holds something people rely on.
- **The one residue is outside this stack:** the ingress rule Terraform writes into Echo's
  Secrets Manager endpoint security group. Terraform owns it, so it reverses with everything
  else — but verify it is gone rather than assume, because an orphaned rule on someone
  else's security group is invisible from here.
- Keep the plan output and a `terraform show -json` of the first apply as evidence of what
  was created. Keep them out of the repository and out of CI artifacts.
- Partial-apply recovery: if an apply fails midway, re-run `plan` and read it rather than
  re-running `apply`. Terraform's state will show what landed, and the usual cause here is a
  missing deploy-role permission (Phase 3) rather than anything needing a state edit.

## One decision that got made

**The CloudFront distribution used to ship with no WAF; it no longer does (closed 2026-09-15,
`waf.tf`).** It was raised here rather than buried in a `trivy:ignore` comment because it was a
real judgement call, not a fix everything else here was: an internal ALB is internal, a public
bundle does not need a customer-managed key, but a genuinely internet-facing distribution is a
different class of exposure and "it is internal" never applied to it.

What actually shipped: a CLOUDFRONT-scope `aws_wafv2_web_acl` (must live in `us-east-1`
regardless of the origin's own region — `providers.tf`'s `aws.use1` alias) with a default-block
action and one allow rule keyed to an `aws_wafv2_ip_set` built from `var.api_allowed_cidrs` --
the same range the API Gateway authorizer already requires, reused rather than duplicated so
the two controls cannot drift against each other. `frontend.tf`'s distribution resource gained
`web_acl_id`. It bills per month and per request, same as the cost noted when this was still
open, and it needed its own IAM grant on the CI role (`UiWaf` in
`uif-apicurio-dev-deploy.policy.json`) since `wafv2:*` had never been touched by this stack
before.

Confirm both static assets and the write-path share the one control: the WAF handles static
asset delivery, the authorizer handles the API. A network position that fails one will fail
both, but they are still two separate resources answering the same question, not one shared
mechanism.

**The web ACL also carries Pearson's account-wide "WAF & IP Sanctions" compliance program's
per-web-ACL ruleset** (owner: PCM/SRE/Security; due 2026-09-30) — confirmed live in this
account (`PCMSanctionsRuleGroupCloudFront`, Firewall-Manager-seeded) before adding anything,
not assumed from a reference implementation. Sanctions rule group always first and enforcing;
the existing CIDR allow-list next (exempts the Pearson range from what follows, never from
sanctions); a per-IP rate limit in count mode (no real traffic baseline for this stack yet);
`AWSManagedRulesKnownBadInputsRuleSet` in block mode (the one group the program requires
unconditionally); `AWSManagedRulesAmazonIpReputationList` / `CommonRuleSet` / `SQLiRuleSet` in
count mode (their compliant default absent a SOC clearance to run them in block, which this
stack has not sought). Logs to a new `aws-waf-logs-`-prefixed CloudWatch log group, `authorization`
and `cookie` headers redacted.

Two things the program also requires that are **not** implemented here, on purpose, and
recorded rather than silently dropped:

- **Shield Advanced.** A paid AWS Organization-level subscription, not a per-resource Terraform
  change — enabling it is an account/org decision, not something this stack's module can decide
  for itself.
- **Chronicle log shipping.** The program's required destination for WAF logs. CloudWatch here
  is a real, working destination, not the final one — getting logs into Chronicle needs a
  cross-account log destination this stack has no visibility into setting up.

Two smaller ones in the same class, both recorded rather than decided:

- **ECR tags are mutable.** What runs is already reproducible — CI pins the image to the commit
  sha — so what mutability still permits is `latest` moving under a local apply. Closing it
  means dropping the `latest` push from the workflow and leaving `bff_image_tag` without a
  usable default. That is a deploy change and I did not make it mid-flight.
- **`terraform validate` is skipped on Terraform 1.15.0 only.** CI pins 1.15.8 so CI always
  runs it. If you would rather the gate hard-fail on that release instead of skipping loudly,
  it is a two-line change.

## What this deploy does not fix

Stated plainly so nobody reads a working dev URL as a finished service. All are current
entries in `ARCHITECTURE.md` §4.

| Gap | Still true after this deploy |
|---|---|
| 2 · CMDB is a mock | Every ownership answer the UI gives is fixture data, and compliance results inherit the fiction |
| 3 · `cdc-poller` absent | The Delivery Lab tab has no data producer; nothing writes to it |
| 4 · ECS, not the mandated EKS + Helm + Argo CD | Unresolved platform decision, and it gates production rather than dev |
| 6 · Metadata validator is Avro-only | Every artifact in the registry today is JSON, so the nine governance rules cannot validate a single one by its intended path |
| 8 · Compliance cache and watermarks in process memory | Pins `bff_desired_count` to 1; two tasks would serve divergent state |
| ~~12 · `ruff` report-only~~ | **Fixed.** 0 findings, and blocking via `qe/ci.sh` — once Stacks CI is enabled |
| — · No user authentication | The stop-gap is network position, not identity. Nobody is authenticated; they are merely reachable |
| — · Task security-group egress is `0.0.0.0/0` | Kept on purpose for the first apply: narrowing to 443 + 5432 drops UDP/53 and the image pull then fails by name. Worth tightening against a deployment that exists to test it |
| — · **The shared deploy role can delete itself, and any `uif-*-dev*` role** | Confirmed live when Phase 3's verification flagged it. **Granted four times over**, not once: `echo-dev-deploy-2` plus all three inline policies (`uif-gold-deploy`, `echo-dev-github-actions-inline`, `echo-dev-github-actions-policy`). Deleting Beacon's roles is granted by `echo-dev-deploy-2` and `uif-beacon-deploy-2`. That multiplicity is the finding's real shape — narrowing any single document removes nothing, so a remediation ticket scoped to one policy would close having achieved zero. **Not introduced by this deploy:** `uif-apicurio-dev-deploy-policy` contains no `iam:` action and no `Deny`, so it can neither cause nor revoke it. A CI role reachable from a pull request that can delete its own role, rewrite its own inline policies, and delete another stack's roles belongs in the infra remediation backlog against the shared role |
| — · `identity-secrets.md` cannot express two inline policies on one role | The pairing rule collides. Resolved locally as one-policy-per-role; the standard still needs the sentence |
| — · Domain CI and Standards Review are also `disabled_manually` | Found while checking Stacks CI. The naming-guard CI check this branch relies on may not be running either |
| — · `ruff format` reports 32 files non-compliant | Lint is clean and blocking; formatting is a separate decision and a separate commit |
| ~~CloudFront has no WAF~~ | **Fixed.** `web_acl_id` now restricts the distribution to `var.api_allowed_cidrs`, the same range the API Gateway authorizer requires, and carries Pearson's WAF & IP Sanctions ruleset. Raised above as a decision that got made, not only as a code comment |
| — · No Shield Advanced | Pearson's compliance program requires it on internet-facing CloudFront/ALB/EIP/Route53. Account/org-level paid subscription, not a per-resource Terraform change — raised above, not decided here |
| — · WAF logs go to CloudWatch, not Chronicle | The program's required destination is Chronicle; CloudWatch is a real, working interim destination. Needs a cross-account log destination this stack has no visibility into setting up — raised above, not decided here |
| ~~`src/Dockerfile` ran uvicorn as root~~ | **Fixed.** `appuser` at uid 10001, `USER` after the installs so the process cannot rewrite its own code. Verified from `/proc/1/status` under the real CMD, not from the instruction being present. `capabilities/infrastructure/docker.md` already required this |
| ~~`ui/Dockerfile` appears unbuilt~~ | **Deleted.** Confirmed unreferenced by any build: the workflow ships the UI as `npm run build` + `s3 sync`, `docker-compose.yml` says to run it natively, and `git log -S` shows it arrived in the bulk migration and was never wired up. It also had no `.dockerignore`, so `COPY . .` would have baked 466 MB of `node_modules` into a layer |
| ~~`docker.md` asks for four more things this image does not do~~ | **Two of the four fixed.** The image is now **multi-stage** (the `build-base`/`python3-dev`/`libffi-dev` toolchain stays in the builder and is absent from runtime — verified: no `gcc`/`cc`/`make`/`ld`) and **`@sha256`-pinned** via `ARG BASE_IMAGE`. Still not done, both deliberate: **distroless** — SIF publishes no distroless image at all, so it is not an available option, and it would remove the `curl` the ECS task definition depends on; **image-level `HEALTHCHECK`** — defined once in the task definition, where an image-level copy would be inert in the deployed path and a second place for a value that has already been wrong once |
| ~~`src/Dockerfile` base carried 205 HIGH/CRITICAL CVEs~~ | **Fixed.** `python:3.12-slim` → digest-pinned `python:3.12-alpine`: **205 → 0** HIGH/CRITICAL, 584 MB → 126 MB. 203 of the 205 were Debian OS packages (143 in `linux-libc-dev`) and only 7 were fixable, so the base had to change rather than be patched. The 7 HIGH the Alpine base itself carried (all `libuuid`) are closed by `apk upgrade`, a real package upgrade — no `.trivyignore`, no raised threshold |
| ~~`qe/ci.sh` never ran `trivy image`~~ | **Fixed.** It ran `trivy config` twice — Dockerfile and terraform as *text* — and never opened the built image, which is how 205 CVEs sat behind a gate reporting 0 misconfigurations. There is now a blocking `trivy image --scanners vuln --severity HIGH,CRITICAL --exit-code 1` gate, scanning the tag `docker build` produces. `docker.md:86` required this all along |

## Phase 7 · Move the base image to SIF — DONE (2026-09-15)

**Status: complete.** Both IAM prerequisites below are resolved (verified live via
`aws iam simulate-principal-policy`, not assumed from the policy document), the mirror was
pushed 2026-09-14 and confirmed still live via `aws ecr describe-images` on 2026-09-15, and
`src/Dockerfile`'s `ARG BASE_IMAGE` default now points at it. Built and run-tested directly
against the mirror before that default changed: non-root (`uid=10001`), `/docs` returns 200,
and `trivy image --severity HIGH,CRITICAL` on the built image reports 0 findings across every
layer — the same result the public-image proving run got, now reproduced against the real SIF
mirror rather than assumed to carry over.

The commands below are left as the runbook that was actually followed (with one correction —
see the digest table), for whoever mirrors the next tag bump.

### Why, and what is already done

`src/Dockerfile` has been rewritten onto Alpine/musl and now scans **0 HIGH/CRITICAL**, down
from **205** (1 CRITICAL, 204 HIGH) on `python:3.12-slim`. 203 of those 205 were Debian OS
packages, 143 in `linux-libc-dev` alone, and only 7 were fixable at all — the base could not be
patched out of it, which is why this was a base change and not a bump.

What remains is *which registry* the Alpine base comes from. The proving run deliberately used
the **public** `python:3.12-alpine`, digest-pinned, because the SIF image cannot be reached from
here (see blockers). The hard part — musl wheels, `apk` dependencies, busybox `adduser -S` —
is already proven. This phase is a registry swap, and the Dockerfile is built so that it is
*only* a build-arg change:

```bash
docker build --build-arg \
  BASE_IMAGE=283556840805.dkr.ecr.eu-west-1.amazonaws.com/uif-apicurio-dev-base-python-ecr-repo:3.14-alpine3.23@sha256:<digest-after-push> \
  -t uif-apicurio-dev-bff src/
```

**Python 3.14 is satisfiable — checked, not assumed.** The SIF Python image is `3.14`, this
stack proved against `3.12`, and `pyproject.toml` declares `requires-python = ">=3.12"`. Every
native dependency publishes a `cp314` `musllinux` `x86_64` wheel on PyPI today:
`pydantic-core` 2.49.0, `uvloop` 0.22.1, `httptools` 0.8.0, `watchfiles` 1.2.0. So the 3.14 move
should **not** mean compiling Rust in the builder — on 3.12 nothing third-party compiled from
source at all. Re-check at the versions the lockfile actually resolves on the day; the builder
stage keeps `build-base python3-dev libffi-dev` precisely so a missing wheel degrades to a slow
build instead of a failed one.

### The SIF catalogue, read off the portal on 2026-09-14 (VPN)

Pulled from `https://sif.pearson-pso.io/library/python` while connected, so these are observed
values rather than the reference table's. **Three of them contradict the Pearson tag reference
this stack was built from, so check the portal yourself rather than trusting either document.**

- **"All SIF images are Alpine/musl. No Debian, no slim, no distroless" is false.** The Python
  library carries **56 tags: 34 Alpine, 15 `slim`, 3 `bookworm`, 3 `bullseye`, 1
  `amazonlinux2023`.** 21 are Debian-based. Alpine is still the right choice here, but it is a
  choice, not the only option the registry offers — someone reading the reference would believe
  they had no decision to make.
- **`nginx` is in the SIF library**, alongside `rhel`, `ubuntu`, `tomcat`, `dotnet`,
  `amazoncorretto` and `openjdk`. The reference says SIF publishes no nginx.
- **`3.14-alpine3.24` exists**, so the reference's `3.14-alpine3.23` is no longer the newest.

**SIF's own Twistlock scan corroborates the base-image decision from a second scanner.** Every
Alpine tag reports `{critical: 0, high: 0, medium: 0, low: 0}`; the Debian ones do not —
`3.11-bullseye` reports 5 critical / 21 high / 588 total, `3.10-slim-bullseye` 2 critical /
2 high / 121 total, `3.12-slim-trixie` 0 critical / 3 high / 60 total. That is the same shape as
the local `trivy` measurement (203 CVEs from the Debian base, 0 on Alpine), found independently.
Worth knowing how that data reads in the payload: `packages` is always an empty list, so a zero
looks like it might be an unscanned image. It is not — 33 of 103 architecture records carry
non-zero counts, which proves the distribution is real and the package list is merely trimmed
before it reaches the browser.

**SIF's Python image is a rebuild of `public.ecr.aws/docker/library/python`** — the payload
names that as the `distro` for every tag. So this migration buys curated provenance, a private
registry and Twistlock scanning; it does **not** change the base's upstream lineage, which is
the same Docker official image the public pin already uses. Useful to know before anyone
attributes the 205→0 to SIF: the CVE drop came from Alpine-vs-Debian, not from SIF.

**Recommended tag: `3.12-alpine3.24`**, not `3.14-*`. The musl rewrite is proven on 3.12 with
every native wheel installed prebuilt and nothing compiled from source; moving to 3.14 in the
same change would conflate a registry migration with a runtime bump, and a missing `cp314`
wheel would then present as a SIF problem. Do 3.14 afterwards, on its own. Note there is no
`3.12-alpine3.23` in the catalogue at all, so `alpine3.24` is the newest Alpine for 3.12.

Digests observed on 2026-09-14, all pushed by SIF on 2026-09-13. Mirroring with
`--platform linux/amd64` pushes that single architecture's manifest, so the **amd64** digest
below is the one to expect in our ECR — verify it against the `docker push` output rather than
pasting it in blind:

| Tag | amd64 digest | Size |
|---|---|---|
| `3.12-alpine3.24` | `sha256:7c4996751ff4c50ff2c5e5e222188ced2208a45ea54dea9d058e1279615948dc` | 22.9 MB |
| `3.13-alpine3.24` | `sha256:b7c8f89761d9883c6dea5add78567ad70bbf937452b73f33cc87bb1f364a3dd2` | 21.8 MB |
| `3.14-alpine3.24` | `sha256:18d78afbc34191d437ba21794a603f577b7bce999e3ccc4a15f7cbcef4490835` | 22.6 MB |
| `3.14-alpine3.23` | `sha256:2cd7ba4e01d8f034d05614a3641908011f89746ce2378eea3140821ae635bfc5` | 22.5 MB |

**Correction, confirmed by actually mirroring `3.12-alpine3.24`:** the digest that landed in our
ECR is `sha256:14f65fb17e64d24fedd69a30d41c19f17d4bbcfb3a23b99663858e65c44616f6`, **not** the
`sha256:7c499675...` in the table above. This is exactly the risk this section already warned
about — "verify it against the `docker push` output rather than pasting it in blind" — and the
portal digest turned out to be the wrong one to paste. `src/Dockerfile`'s `ARG BASE_IMAGE` is
pinned to the real, push-produced digest.

SIF publishes **arm64 too** — the portal lists both for every tag — so the `--platform` pinning
matters for the usual reason: an Apple Silicon host pulls arm64 by default and Fargate runs
amd64.

**What VPN alone did not unblock.** The registry answered `401` and `aws sts get-caller-identity`
reported no credentials configured at all, so the pull below still could not be attempted. VPN
gets you the catalogue; the pull needs credentials *and* the grant in blocker 1.

### Blockers — both resolved

1. **~~SIF must grant `283556840805` pull access.~~ Turned out not to be needed.** This blocker
   assumed a resource policy on the SIF ECR repository was required for cross-account pull.
   The actual mirroring run (`docker login` + `docker pull` from account `283556840805` against
   SIF's registry) succeeded with no such grant — verified empirically, not by anyone at SIF
   adding a policy. Moot in practice anyway: mirroring is a one-time pull, and every build after
   it reads from our own account's ECR, never SIF's again.
2. **The deploy role could not pull from ECR at all — any repository. Fixed 2026-09-15.**
   `ecr:GetDownloadUrlForLayer` is now granted on `uif-apicurio-dev-deploy-policy`'s `Ecr`
   statement, applied live (policy version confirmed via
   `aws iam simulate-principal-policy` — was `implicitDeny`, now `allowed`). This was the real
   blocker: every push action was already present (`BatchCheckLayerAvailability`,
   `InitiateLayerUpload`, `UploadLayerPart`, `CompleteLayerUpload`, `PutImage`) but the pull-side
   action needed by `docker build FROM` was missing, invisible until something actually pulled.
   `capabilities/infrastructure/uif-apicurio-dev-deploy.policy.json` reflects the grant; keep it
   in sync with whatever's actually attached live if either drifts.

Also note it is **cross-region**: SIF publishes in `eu-west-2`, this stack deploys to
`eu-west-1`. That is a reason the mirror exists rather than an obstacle, but it does mean the
two `docker login` calls in step 3 target different registries.

### Repository name — and the tension in it

Use **`uif-apicurio-dev-base-python-ecr-repo`**. It is not a free choice; it is the
intersection of two constraints, and the skill's suggested `apicurio-base/python` satisfies
neither:

- The deploy role's ECR statement is scoped to
  `arn:aws:ecr:eu-west-1:283556840805:repository/uif-apicurio-dev-*`. `apicurio-base/python`
  does not match that prefix, so CI would get `AccessDenied` pulling the base — *after* a
  successful `docker login`, which reads like a registry fault rather than an IAM one.
- This repo's naming standard (`architecture-design/standards/rules/naming/compute-etl.md`)
  requires the `uif-` stem and an `-ecr-repo` suffix. Verified against the installed guard
  rather than inferred from the existing `uif-apicurio-dev-bff-ecr-repo`:
  `check_name("uif-apicurio-dev-base-python-ecr-repo", "aws_ecr_repository")` returns `None`
  (compliant), while `check_name("uif-apicurio-dev-base-ecr-repo/python", ...)` is **rejected**
  — a nested `namespace/image` name cannot end in `-ecr-repo`, so the flat name is forced.

**The tension, stated rather than hidden:** a base-image mirror is not logically
environment-specific — the same bytes serve dev, stage and prod — yet both the IAM scope and the
naming standard's mandatory `{env}` slot push toward a per-environment repository. **Chosen:
a per-env mirror**, because it needs no IAM or standards change and is therefore the only option
that can be executed today. **What it costs:** each environment stores and pays for its own copy
of an identical image, and a base bump is repeated per environment — three pushes, three chances
to have mirrored different digests. **The alternative** is one shared `uif-base-*` repository
with the deploy role's scope widened to reach outside `uif-apicurio-dev-*`; that is strictly
cleaner and needs both an IAM decision and a naming-standard amendment for a resource that has
no environment. Worth revisiting when a second stack needs the same base — not worth blocking
this on.

### The commands

```bash
# 1. Pull the SIF base. --platform linux/amd64 is MANDATORY: Actions runners are
#    amd64 and an Apple Silicon Mac pulls arm64 by default. An arm64 image pushed
#    to ECR fails at Fargate task start, not at build time.
aws ecr get-login-password --region eu-west-2 \
  | docker login --username AWS --password-stdin 322587279590.dkr.ecr.eu-west-2.amazonaws.com
docker pull --platform linux/amd64 \
  322587279590.dkr.ecr.eu-west-2.amazonaws.com/python:3.14-alpine3.23

# 2. Create the mirror repository in OUR account and region (283556840805 / eu-west-1).
aws ecr create-repository \
  --repository-name uif-apicurio-dev-base-python-ecr-repo \
  --region eu-west-1

# 3. Tag and push. Note the SECOND login: step 1 authenticated against SIF in
#    eu-west-2, and this is a different account AND a different region.
aws ecr get-login-password --region eu-west-1 \
  | docker login --username AWS --password-stdin 283556840805.dkr.ecr.eu-west-1.amazonaws.com
docker tag 322587279590.dkr.ecr.eu-west-2.amazonaws.com/python:3.14-alpine3.23 \
  283556840805.dkr.ecr.eu-west-1.amazonaws.com/uif-apicurio-dev-base-python-ecr-repo:3.14-alpine3.23
docker push \
  283556840805.dkr.ecr.eu-west-1.amazonaws.com/uif-apicurio-dev-base-python-ecr-repo:3.14-alpine3.23

# 4. Read back the digest the push produced and pin the Dockerfile's ARG default to it.
#    docker.md:85 requires an @sha256 pin, and the digest is registry-specific --
#    the public image's digest is NOT valid for this reference.
aws ecr describe-images --region eu-west-1 \
  --repository-name uif-apicurio-dev-base-python-ecr-repo \
  --image-ids imageTag=3.14-alpine3.23 \
  --query 'imageDetails[0].imageDigest' --output text
```

**Done**: `ARG BASE_IMAGE` in `src/Dockerfile` now defaults to the mirrored reference, pinned to
the real push digest (not the portal one — see the correction above). Built and run directly
against it before changing the default: non-root (`uid=10001`), `/docs` returns 200, and
`trivy image --severity HIGH,CRITICAL` reports 0 findings across every layer — the SIF base
carries the same clean result the public-image proving run got. Re-run `qe/ci.sh` in CI to
confirm the same result there; the `trivy image` gate scans whatever the ARG resolves to, so it
needs no further change to pick this up.

### What needs no change

**`.github/workflows/deploy-apicurio-dev.yml` requires no edit** — verified by reading it, not
assumed. Its `Build and push` step runs
`aws ecr get-login-password | docker login ... "${uri%%/*}"` at line 248, *before* `docker build`
at line 263, and `${uri%%/*}` is the registry host `283556840805.dkr.ecr.eu-west-1.amazonaws.com`
— the same host the mirror lives on. So the pull is already authenticated by the login the push
needed. No `registries:` argument is required on `amazon-ecr-login` either, precisely because the
base ends up in our own account. The only change the workflow needs is the IAM action in
blocker 2, which lives in the policy, not the workflow.
