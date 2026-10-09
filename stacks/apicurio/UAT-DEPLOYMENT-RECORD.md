# uat deployment record — 2026-09-21

apicurio's uat environment went live on 2026-09-21. This document records what
is running, what is still open, and every failure the deployment hit with how
it was resolved. It exists because **prod is next and will hit most of these
again**: uat was the first environment created through the two-workflow
promotion model, so nearly every failure here is a first-run failure rather
than a uat-specific one.

The runbook is [`DEPLOY-UAT.md`](DEPLOY-UAT.md) — instructions on how to
deploy. The reusable standard this stack is the worked instance of is
[`capabilities/delivery/stack-promotion-model.md`](../../capabilities/delivery/stack-promotion-model.md). This file is history: what actually happened when those instructions
were followed for the first time. Read the "Open items" checklist before
planning prod, and the "Failures and resolutions" section when something
behaves the way one of these did.

---

## What is live

| | |
|---|---|
| Release tag | `release/uat/2026-09-21.1` |
| Promoted from | `apicurio/v0.2.3` |
| BFF image digest | `sha256:0ec3b9f29f73926114a1997abab889ce242aa0ac759a8875ede822b66d9a2892` |
| UI bundle | `523a46c5cd80b03eb774d1a206773ffec46bcc90f86b67b3745342f6e31be026` |
| Registry image | `quay.io/apicurio/apicurio-registry:3.3.2` |
| Smoke | 7 passed, 6 skipped, 2 deselected |
| CloudFront | `dbjg7v3miozyq.cloudfront.net` |
| API endpoint | `https://zsu3yrvyq1.execute-api.eu-west-1.amazonaws.com` |

Verified against the account rather than read off a log:

- `uif-apicurio-uat-registry-service` — 2/2 running
- `uif-apicurio-uat-bff-service` — 1/1 running, rollout `COMPLETED`
- API Gateway `CorsConfiguration.AllowOrigins` — `["https://dbjg7v3miozyq.cloudfront.net"]`
- Task definition `:2` — `CORS_ORIGINS=["https://dbjg7v3miozyq.cloudfront.net"]`
- A credential-less request to `/api/v1/health` — `403` from the authorizer
- uat ECR's image digest — byte-identical to dev's, same manifest media type

That last line is the promotion claim itself, and it holds across four
consecutive build tags: `v0.2.0`, `v0.2.1`, `v0.2.2` and `v0.2.3` all carry the
same BFF digest and the same UI bundle hash. The artifact moved between
environments without being rebuilt, which is the whole point of the model.

### What a green uat does not prove

[`DEPLOY-UAT.md`](DEPLOY-UAT.md) states this and it is repeated here because it
bears on prod planning: uat shares dev's VPC, both `private-css` subnets, dev's
single-AZ Secrets Manager endpoint and dev's sizing defaults. Isolation is by
resource name. A green uat exercises the application and says nothing about
prod's network or capacity shape.

---

## Open items

Ordered by what blocks prod soonest.

### Must be closed before prod

One of these five is now closed; it is left in place, ticked, because how it was
closed is the reusable part.

- [x] **UIFPE-499 — restore SAST to the `stacks-ci` gate.** **Done 2026-09-21.**
      Snyk Code recovered the same day; the SAST job on PR #431's run concluded
      success having genuinely scanned (`SAST scan completed - found
      vulnerabilities that will be evaluated against threshold 'critical'`, with
      findings listed and no 503). Reverted in `71bc13e`. The `timeout-minutes`
      work survived the revert, which was the reason for keeping the waiver in
      its own commit.

      Two things to carry forward if this is ever needed again. The revert took
      **two** commits, not one — `dbfe869` corrected the waiver's ticket
      reference and touches the same lines, so reverting `0f0de8a` alone
      conflicts. And verifying "SAST is healthy again" needs the log, not the
      conclusion: a 503 surfaces as a **warning** while the step keeps running,
      and a stack with no first-party code reports success without scanning
      anything.
- [ ] **Raise the vendor defect.** `pearson-adm-gitworks/action-security-scan@v24`
      retries an HTTP 503 **indefinitely** instead of failing or capping. That
      behaviour, not the outage, is what cost a release cycle. Nothing in this
      repo can fix it; it needs to go to the action's owners.
- [ ] **UIFPE-500 — first deployment of a new environment fails by construction.**
      Four separable fixes on that ticket: deliver the image before an apply pins
      it; set `wait_for_steady_state = true`; split the 403/5xx assertion; document
      the `--force-new-deployment` remedy. Prod will hit this.
- [ ] **UIFPE-492 — prod promotion has no cross-account resource policies.** uat
      promotes within one account, so this was never exercised. prod is a
      different account and `PromotionSourceRead` alone will not be enough.
- [ ] **UIFPE-496 — `bootstrap --verify-only` reports green on a role that cannot
      deploy.** Now known to miss four distinct mechanisms: resources the stack
      does not manage, wrongly-scoped resources, unevaluated conditions, and
      post-create actions. Every one of those cost an apply attempt on uat.

### Should be closed, not blocking

- [ ] **`stacks-ci gate` is still not a required check on `develop`.** Verified:
      protection requires exactly three contexts — `Author email matches a Jira
      user`, `Every commit carries a Jira key and a comment`, `Title and body are
      reviewable`. The gate that decides whether a build tag can be cut does not
      decide whether code can merge.
- [ ] **`.githooks/lint-commits.sh` accepts a message Jira cannot parse.** Both
      the key check and the `#comment` check `grep` the **whole message**, so
      `UIFPE-486` on line 1 and `#comment …` on line 5 pass the linter. Jira's
      Smart Commit parser needs them on the *same line*. This failure was hit
      during this work and fixed by hand, not by the guard.
- [ ] **Stale comment in `deploy-apicurio-infra.yml`.** Its header argues at
      length that `workflow_dispatch` is inert because the file is not on `main`.
      The file is now on `main` and every run of that workflow to date has been a
      dispatch run. The comment actively misleads.
- [ ] **`uif-apicurio-uat-backend.policy.json` is named narrower than its
      contents.** It holds Terraform state access *and* `WafLogDeliveryUnscopable`
      — seven `logs:` actions unrelated to the backend. Either rename it or split
      the WAF statements out.
- [ ] **Six registry smoke tests skip in uat.** They are `requires_seed` and
      deselected. uat therefore proves BFF-to-registry connectivity but not
      registry artifact behaviour.

---

## Failures and resolutions

Grouped by mechanism, because the mechanism is what repeats. Each entry gives
the symptom as it actually presented, the cause, and what was done.

### A. IAM — the deploy role could not do what the derivation predicted

Five separate rounds, each surfacing a class the previous round's verification
could not see. All five are why UIFPE-496 exists.

| Symptom | Cause | Resolution |
|---|---|---|
| `terraform init` → 403 on the state bucket | The role had no Terraform state access at all; the derivation only grants what the stack *manages*, and the state bucket is not a managed resource | Added a third policy, `uif-apicurio-uat-backend.policy.json` |
| Still 403 after that | `s3:ListBucket` had been deliberately omitted for least privilege — but S3 answers `HeadObject` on a missing key with **403, not 404**, without it, so "does state exist" was unanswerable | Added `s3:ListBucket` on the bucket, scoped to the bucket only |
| Plan failed at the very end, naming a resource the stack does not manage | `data.aws_vpc` reads two attributes; `ec2:DescribeVpcAttribute` was absent | Added it to the map's `data.aws_vpc` entry |
| `CreateSecurityGroup` denied | The action authorises against the **VPC**, not the security group | Added `…:vpc/*` to the `sg_in_vpc` scope |
| WAF logging denied despite `wafv2:PutLoggingConfiguration` being granted | The ACL is CLOUDFRONT-scope, so its log group lives in **us-east-1**, and the real gap was **caller-side** log-delivery permissions | Added 7 `logs:` log-delivery actions on `"*"` |
| `iam:PassRole` denied for the Lambda authorizer | The condition listed only `ecs-tasks.amazonaws.com` | Made `iam:PassedToService` a list including `lambda.amazonaws.com` |
| Base policy rejected at 6,324 characters | Managed policy limit is **6,144 excluding whitespace** | Collapsed 10 prefix-specific log ARNs to 4 name-scoped ones → 5,802 |
| Four denials *after* resources were created | The map covered create-time actions and was blind to the provider's **post-create reads and tags** | Added `lambda:ListVersionsByFunction`, `apigateway:TagResource`/`UntagResource`, and listener attribute actions |

**The trap inside the last row.** `elasticloadbalancing:DescribeListenerAttributes`
supports **no resource-level permissions**, so it must sit on `"*"`. Simulating
it against a specific listener ARN returns `implicitDeny` with an **empty**
`MatchedStatements` — indistinguishable from a real gap. Proof is comparative:
`ModifyListener` returns `allowed` on the identical ARN.

Two verification mistakes were made and retracted while diagnosing this: one
listener ARN was reused across six simulated actions, which made five of the
six denials meaningless; and IAM eventual consistency was blamed for what was
actually the unscopable-action behaviour above.

### B. Release chain — the tag that would not cut

| Symptom | Cause | Resolution |
|---|---|---|
| `security scan — SAST` ran 20+ minutes with no output, on two runs at once, one of them `develop` | Snyk Code's analysis API returned **HTTP 503** and the vendor action retries a 503 indefinitely | Bounded every `stacks-ci` job with `timeout-minutes`; SAST at 20 |
| No log to diagnose it from | **Job logs are unreachable until a job ends** — `gh api …/logs` answers `BlobNotFound` | The timeout above is what made the 503 legible; it was invisible before |
| `release-apicurio.yml` failed: `Timed out after 30 minutes waiting for the stacks-ci gate` | `gate` waits on everything in `needs`, so a job that never concludes **stalls** the gate rather than failing it | Waived SAST from the gate (UIFPE-499), and the workflow correctly refused to cut a tag without evidence |
| PR blocked: `No '## Verification' section` | Required PR body sections | Added the section |
| PR blocked: `No '## Summary' section` on a body that had `## What` | `.githooks/lint-pr.sh` matches `summary\|what changed\|context\|why`; bare `## What` matches none of them | Renamed the heading |

**The reasoning error worth recording.** A `stacks-ci` run was read as proof the
outage had cleared because its SAST job concluded in 5 seconds. That run scanned
only `event`, which has neither `ui/package.json` nor `src/pyproject.toml`, so it
took the `no first-party code` **skip branch** and never scanned anything. A skip
notice is not a pass. What actually settled the diagnosis as vendor-wide was
`beacon` hanging identically to apicurio.

### C. The two-pass CORS sequence

| Symptom | Cause | Resolution |
|---|---|---|
| uat's UI could not have called its API | `cors_origins` was the `http://localhost:5173` placeholder the first apply required — the variable rejects a blank value, and uat's CloudFront domain does not exist until that apply creates the distribution | Set it to the distribution's domain and re-applied from a new tag |
| A re-run would not have fixed it | `envs/uat.tfvars` is git-tracked, so its contents are **frozen into the commit a run's tag points at**. Re-running the existing run re-checks-out the pre-merge tree and plans *no* CORS change — not "CORS only", nothing | Cut `apicurio/v0.2.3` and applied from it: `1 added, 2 changed, 1 destroyed` |

**Why this one is dangerous rather than merely annoying:** smoke never exercises
a browser origin, so tier 2 would have gone green with the placeholder still in
effect — a "green uat" whose UI cannot reach its API. The general rule, from
[`DEPLOY-UAT.md`](DEPLOY-UAT.md): **changing a variable costs a re-run; changing
a file costs a new tag.**

A second trap: the placeholder's own comment specified the CloudFront domain
**alone**, no localhost. Copying dev's two-value pattern would have put a
localhost browser origin on a shared environment nobody develops against.

### D. First deployment — the ECS circuit breaker

The most expensive failure, because it presented as something else entirely.
Full detail on **UIFPE-500**; the sequence, in UTC:

| Time | Event |
|---|---|
| 17:00:39 | CORS apply creates task definition `:2` pinned to `sha256:0ec3b9f2…` |
| — | `wait_for_steady_state` unset, so the apply returns **success immediately** |
| 17:04, 17:06 | `CannotPullContainerError … not found` — uat ECR is still empty |
| **17:12:17** | **Circuit breaker trips**: rollout `FAILED`. **ECS stops retrying.** |
| 17:16:33 | promote pushes the image — four minutes too late |
| 17:17:53 | smoke runs against a dead deployment → 503 on every BFF test |

**Resolution:** `aws ecs update-service --cluster uif-apicurio-uat-cluster
--service uif-apicurio-uat-bff-service --force-new-deployment`, same task
definition. 1/1 `COMPLETED` in about three minutes, then smoke passed 7/7. No
Terraform-managed attribute changes, so no state drift.

**This is structural, not a race.** The runbook orders infra-then-promote, so on
a first deployment the infra apply *necessarily* pins a digest the promote has
not delivered yet. Following the instructions produces this outcome.

**The misdiagnosis it caused.** `test_allows_request_with_ci_key` failed with:

> The CI shared-secret header did not authorise this request (503). The secret
> container is created by Terraform with no version, so check an operator has
> actually set its value…

The credential was correct. A mismatch is **403** from the authorizer; a **503**
means the authorizer *allowed* the request and the backend was unavailable. The
message pointed diagnosis at a credential that was already right. That
`test_denies_request_without_credential` passed alongside it was already
evidence the authorizer worked.

### E. Operator-facing traps

| Symptom | Cause | Resolution |
|---|---|---|
| `Could not connect to the endpoint URL: "https://secretsmanager.eu-west-1a.amazonaws.com/"` | `eu-west-1a` is an **availability zone**, not a region | Use `--region eu-west-1`; pass it explicitly rather than relying on the environment |
| The CI key looked like it had landed at repo level | `gh secret set --env uat` prints a success line that **does not name the environment** | It was correctly env-scoped; confirmed with `gh secret list --env uat` |

**Why the scope matters.** `APICURIO_CI_KEY` exists at repository level for dev.
An environment-scoped secret on `uat` shadows it for jobs declaring
`environment: uat`. Setting uat's key at repo level instead would overwrite
dev's and send dev's value to uat — and the resulting failure is a 403
mismatch, not a missing-secret error.

---

## What this deployment cost, and what would have prevented it

Roughly ten distinct defects, in work that had already passed **five reviews,
329 unit tests and mutation testing on every guard**.

**Not one of the deployment-contact failures was found by review.** Every one was
found by running the thing against AWS. Two reviewers examined the
lightweight-tag handling specifically and cleared it as cosmetic; it was a real
defect. The IAM map passed review five times and was wrong in eight distinct
ways, each invisible until an apply hit it.

The pattern across sections A–D is one mechanism: **a step reporting success
over something that does not work.** `--verify-only` green on a role that cannot
deploy (UIFPE-496). An apply green over a service that cannot start a task
(UIFPE-500). A gate green over a scan that was never consulted (UIFPE-499). A
`simulate-principal-policy` denial that means "unscopable action", not "missing
permission". Reviews cannot see any of these, because in every case the
artifact under review is correct and the *reporting* is what lies.

The concrete lesson for prod: budget for apply-and-fix rounds rather than
assuming a reviewed plan applies cleanly, and treat every green that was not
independently verified against the account as unproven.
