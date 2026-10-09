# 0015. Echo Moves Into the Monorepo

- Status: Accepted (2026-09-09) — the move, as recommended; see What acceptance covers
- Date: 2026-09-09
- Author: vaibhav.yenorkar@pearson.com
- Deciders: framework owner; the team that owns `uif-relay-echo`; the owner of `uif-dal-gold`
- Scope: Whether Echo's code lives in this monorepo, who owns it once it does, and what that does to the Gold repo's shared primitives.
- Supersedes: the **premise** of ADR-0004 (two separate repositories), not its decisions CP-1 – CP-6.
- Drafted under UIFPE-355, against the migration branch `feature/UIFPE-355-echo-stack-migration` rather than as a proposal in the abstract.

---

## What acceptance covers

**Accepted: the move.** `stacks/echo/` is Echo's home, superseding ADR-0004's
premise of two repositories with two owners. ADR-0004's decisions CP-1 – CP-6
stand unchanged.

Accepted by the author (2026-09-09), who has taken every scoping decision in
UIFPE-355 and who is now also Echo's named accountable owner (D10). **The
Deciders line above lists three parties. One — the owner of `uif-dal-gold` —
still has not been consulted**, and the decision it owns is deferred rather than
answered; see D11. The team ADR-0004 describes as owning `uif-relay-echo` is
moot once that repository is archived read-only.

**Ownership is settled (2026-09-09): the author owns `stacks/echo/`.** This was
the one real decision the ADR had to make and it is no longer open — see D10 for
what the `CODEOWNERS` entry revealed, which was worse than a wrong owner.

## Context

**ADR-0004 deliberately severed this monorepo from `uif-relay-echo`.** Its scope
was narrower than it is usually quoted as being — "how the Gold module operates
independently of the platform repo after the repo split" — and its opening
sentence states the fact it was reasoning from: the platform had split into
`uif-relay-echo` (UI + Bronze/Silver, **owned by another team**) and
`uif-dal-gold`. Every decision in it follows from there being two repositories
with two owners.

`stacks/echo/` was scaffolded as a **named placeholder** and said so in all eight
of its files: that the name should have a place alongside `harbor` and `beacon`,
that this was *not* a decision to rebuild relay-echo's functionality here, and
that if the intent ever became to pull it in, "that reverses ADR-0004 and
deserves its own ADR." Its `CHECKLIST.md` made step zero this decision rather
than a Story. This is that ADR.

**Two things have changed since ADR-0004.**

First, `uif-relay-echo` **is being archived read-only** (decided 2026-09-09). The
premise ADR-0004 reasoned from — a separate, actively-developed repository owned
by another team — stops being the state of the world. An ADR whose premise has
expired should be superseded rather than quietly ignored.

Second, the migration now **exists as a reviewable diff**. UIFPE-355 imported
all 714 tracked files and 28 commits via `git subtree` (so `git blame` still
resolves to the original authors), reshaped them into Beacon's layout, and
verified the test suite at **exact parity — 16 failed / 328 passed with an
identical failure set**, so the move introduced no regressions. That work also
surfaced defects that were invisible from outside: 52 Terraform source paths that
would have failed `terraform plan`, a `Makefile` backend reference that would
have broken `make init`, and a DQ dispatcher import that cannot resolve on Glue
at all. Those are arguments for the move only in the weak sense that scrutiny
found them; they are recorded here because they are the kind of thing a decision
should be made in full knowledge of.

## What this does and does not reverse

ADR-0004 is usually cited as "the ADR that separated the repos", but its six
recorded decisions are about **how Gold runs independently**, and almost none of
them are affected by where Echo's source code sits.

| | Decision | Reversed? |
|---|---|---|
| CP-1 | Gold control plane is config-as-code | No |
| CP-2 | Gold owns its dispatcher and rules | No |
| CP-3 | Gold reuses the shared `{env}` Glue role | **Triggered — see below** |
| CP-4 | Gold shares the scripts bucket under `gold/` | **Triggered — see below** |
| CP-5 | Gold resources moved to Gold's own state | No |
| CP-6 | Gold keeps EDS naming parity and its own state key | No |
| — | *The premise: two repos, two owners* | **Yes** |

So this ADR supersedes a premise, not a design. Gold's independence — the thing
ADR-0004 actually protects — is unaffected by Echo's code changing directories.

## The one real decision this ADR has to make

Not *whether* the code moves. The archival largely settles that: code that is
read-only somewhere else is worse off than code in the monorepo, and the
alternative on the table is not "leave it where it is" but "leave it in an
archive".

The real decision is **who owns Echo once it is here**, and it needs stating
because the repository will otherwise answer it by accident.
`.github/CODEOWNERS` assigns `/stacks/` to `@uif-fde` by glob, with
`/stacks/*/{ui,ux,qe}/` overrides. The moment `stacks/echo/` exists, ownership
of a running Bronze/Silver ingestion platform transfers from the team ADR-0004
names to `@uif-fde` — **silently, by pattern match, with no one having agreed to
it.**

That is the fork: either the accountable owner is named in this ADR and
`CODEOWNERS` is made to match, or the glob decides and we discover who is on
call the first time a 3 AM reconcile deletes more than it should.

## Recommendation

**Accept the move, and name the owner explicitly.**

1. **Move**: accept `stacks/echo/` as Echo's home, superseding ADR-0004's
   premise. Its decisions CP-1 – CP-6 stand.
2. **Ownership**: name the accountable team in this ADR, then add an explicit
   `CODEOWNERS` entry for `/stacks/echo/` so the glob is not doing the deciding.
   If the answer is that ownership stays with the current team, that is fine and
   arguably preferable — but it has to be written down, because the glob says
   otherwise.
3. **Gold's shared primitives**: invoke ADR-0004's own revisit triggers rather
   than inventing a response. It anticipated this precisely:

   > | Platform tightens/renames the shared glue role | CP-3 (own role becomes simpler than coordination) |
   > | Platform deploy sync scope changes beyond `glue_jobs/` | CP-4 (move to own bucket) |

   The rename programme trips both. `echo-dev-glue-scripts` has already been
   renamed to `uif-echo-dev-artifacts` on the migration branch, and Gold ships
   its scripts to `gold/glue_jobs/` *in that bucket*; the Glue role rename is
   queued behind it. **Confirm with the Gold owner which `{env}` names Gold
   actually resolves today** — ADR-0004 was written when dev was `eds-dev-*` and
   dev is now `echo-dev-*`, so Gold may already be pointing at something that has
   moved once — then let Gold take its own role and bucket, as its own ADR
   pre-committed to doing.
4. **Sequencing**: move first, prove the plan, rename second. Doing both at once
   makes a plan unreadable, because a rename-induced replacement is
   indistinguishable from a move-induced one. See Consequences for where this has
   already been compromised.

## Consequences

**Becomes easier**

- `stacks/` holds all three named RELAY stacks with real code in two of them; the
  conflation ADR-0009 and ADR-0010 set out to close is fully closed.
- Echo inherits the monorepo's CI, the v2.0.0 naming standard (ADR-0014) and its
  `PreToolUse` hook, `CODEOWNERS`, and the ADR register — none of which
  `uif-relay-echo` had. It had no CI at all.
- The rename programme and the Echo/Gold coordination stop being cross-repo
  problems.

**Becomes harder**

- Echo would have been the **first stack to trip the `stacks-ci.yml`
  definition-of-done gate** — it demands `src/Dockerfile` once real code lands,
  and Echo ships zero images, as does Beacon. Amended under D7 so both pass via a
  declared exemption. The larger finding is that the workflow is **disabled**
  (#4), so it was never gating anything.
- Echo **arrived** with a red test suite — 16 failures plus two modules
  documented as hanging forever. Now green (D8), so this is no longer a
  consequence of the move; it is recorded because the debt was real and the
  fixing of it surfaced an undeclared dependency and cross-file environment
  pollution that had been masked for months.
- Ownership of a **live** platform moves. Bronze runs on 30-minute, hourly and
  4-hourly schedules plus twelve daily slots; the quiet window for any cutover is
  narrow.

**Accepted**

- **The empty-plan proof was compromised and then replaced with a better one.**
  The artifacts-bucket rename landed before the plan ran, so a plan at HEAD
  conflates rename intent with move safety. Resolved by planning the *pre-rename*
  tree and then the *original relay-echo tree* against the same state as a
  control — see D1. The lesson worth keeping: "empty plan" was the wrong
  criterion for a repo move, because a move legitimately changes `source` paths.
  "No unexplained difference from the original tree" is the criterion that
  actually answers the question.
- **`oracle_instant_client.zip` is unreproducible** — a licensed 127 MB
  download, never in version control. Now staged locally and verified byte-exact
  against S3. Corrected from an earlier claim in this ADR's history: the rename
  does **not** destroy it (`force_destroy` is unset and `filemd5()` fails at plan
  time, so a missing zip aborts the plan). The real hazard is the reverse — on
  apply Terraform re-uploads from the deploying machine, so a *stale* local zip
  silently overwrites the known-good S3 copy. It does still block the CI plan,
  because a clean runner has no local zips. Runbook:
  `stacks/echo/infrastructure/deploy/layers/README.md`.
- **Echo's own OIDC role is unusable.** `iam.tf` scopes
  `echo-{env}-github-actions` to `Raj-Karunakaran_pearson/enterprise-data-connect`
  — a third repository, neither the old one nor this one — so it cannot be
  assumed from anywhere. `deploy-echo-dev.yml` therefore uses the shared
  `AWS_ROLE_ARN_DEV` role, and whether that role can create Glue databases and
  issue Lake Formation grants is untested: Beacon needs neither, so its
  permissions are no evidence.
- **28 foreign commits are now interleaved** into this repository's history. That
  is the price of `git blame` continuing to work on 714 files, and it was judged
  worth paying because those messages carry real deploy forensics.
- **26 of those commits fail `smart-commit.yml`** — confirmed on PR #206, not
  predicted. They are relay-echo's own history, from a repository that had no CI
  and no Smart Commit convention: `"Initial commit: Enterprise Data Connect"`,
  `"Add root README"`, and so on. `.githooks/lint-commits.sh` runs over
  `base..head` with no skip mechanism, so the import is unavoidably in range.
  This is a genuine argument for the plain file copy that was considered and
  rejected — see decision 6. It cannot be resolved by rewording them: adding a
  UIFPE key to a commit that predates the ticket would falsify the record, and
  rewriting their SHAs would break the very blame link the import exists to
  preserve.

## Decisions taken, 2026-09-09

Recorded so acceptance is a short review rather than a re-litigation. All were
taken by the author acting as Echo's engineer; none of them settle the ownership
questions below.

| # | Decision | Basis |
|---|---|---|
| D1 | **The move is safe — verified, not inferred.** | The pre-rename tree plans to `7 add / 38 change / 7 destroy`, which is not empty. So the **original `uif-relay-echo` tree was planned against the same state as a control**, and it produces the *identical* 3 Lake Formation replacements — isolating those as pre-existing drift. What the move itself causes: in-place `source` path updates on S3 objects (same bytes, new repo path) plus 4 `aws_lambda_layer_version` replacements cascading from those re-uploads. **No bucket, table, Glue job, Lambda or EventBridge rule is touched by the relocation.** An empty plan was never achievable, so "empty" was the wrong success criterion; "no unexplained difference from the original tree" is the right one, and it passes. |
| D2 | **Land the 26 non-conforming imported commits by squash-merge.** | Archiving `uif-relay-echo` rather than deleting it was already decided, and that archive was the precondition that made a plain copy acceptable — so squashing reaches the same end state without weakening `smart-commit.yml`, which gates every PR here. **It does not clear the red check:** the lint evaluates `base..head`, so the check stays red and the merge needs a path filter or an override. Rewording the 26 is rejected — it falsifies history and breaks the `git blame` link the subtree import exists to preserve. |
| D3 | **Accept the Lake Formation narrowing.** | Three analyst grants hold `ALTER`/`DROP` and grant options that `lakeformation.tf` does not declare; the apply narrows them to config. A real permission reduction for `PCMPowerUser`, taken deliberately rather than discovered mid-window. |
| D4 | **Tier 1 renames only; stateful resources stay put.** | 47 resources renamed in dev via `name_prefix`. The 6 DynamoDB tables and the UI bucket are pinned to a new `legacy_name_prefix`, because renaming those in Terraform is destroy + create and takes the contents with it. uat and prod set no override, so the two prefixes are equal there and nothing moves. `grep legacy_name_prefix` is the remaining to-do list. |
| D5 | **The naming calls the rename depended on.** | The verb over the agent noun (`replicate-bq`, not `silver-bq-replicator`); layers scoped by consumer; the cadence vocabulary closed at five, retiring `nightly`. All applied in code. |
| D6 | **Scope exclusions.** | **GCP is out** — the standard is AWS-only, so GCS buckets, BQ projects, datasets and tables have nothing to conform to. **The 181 drafts do not migrate** — configuration stays in DynamoDB and is exported into `domains/` after the rename, so the repo records current state rather than a stale snapshot. **`staging` is removed** — it does not exist; its tfvars still held `subnet-REPLACE_WITH_STAGING_SUBNET`. |
| D7 | **The build-contract gate accepts a declared exemption.** | `stacks-ci.yml` now takes `src/Dockerfile` **or** `src/NO-IMAGE.md` stating why; Echo and Beacon both carry the declaration and it still fails closed. Whether the workflow should run at all is open — see #4. |
| D11 | **Gold is parked** (2026-09-09). | Deferred deliberately, not resolved: the coupling question stays open and unowned, and Echo's Gold rules keep dispatching nothing. Two further Gold defects were found while answering D10 and are recorded rather than fixed — the `gold_hourly` rule sends `{"frequency":"gold-hourly"}`, which `scheduler_dispatcher.handler` does not route to `_dispatch_gold` (line 263 tests only `("gold","gold-reconcile")`), so that payload falls through to the Bronze/Silver path and scans `pipeline_config` instead of the Gold table; and the same frequency is absent from the handler's own error text. Effort goes to Bronze and Silver. |
| D10 | **Echo's accountable owner is the author** (2026-09-09). | `.github/CODEOWNERS` now carries an explicit `/stacks/echo/` entry, placed **last** because GitHub applies the last matching pattern rather than the most specific one — so it also reclaims `ui/`, `ux/` and `qe/` from the role globs. Verifying it exposed the real problem: **all five `@uif-*` handles in that file 404 against the org.** GitHub ignores an unresolvable owner silently, so before this entry *no path in the repository had working CODEOWNERS coverage* — the risk was never that the glob assigned Echo to the wrong team, it was that the glob assigned it to nobody and read as though it had. The Four-in-a-Box overlay is sound; its handles are placeholders the header admits to. |
| D9 | **The move is accepted** (2026-09-09). | `stacks/echo/` is Echo's home; ADR-0004's premise is superseded, its CP-1 – CP-6 stand. Accepted by the author; the ownership question it raises is deliberately left open below. |
| D8 | **The test suite is green.** | 373 passed, 0 failed. It arrived with 16 failures and two modules documented as hanging forever. Three root causes: stale fixtures omitting `extract`, one test asserting a Glue config that cannot launch, and an **undeclared `moto` dependency** — which was also the entire cause of the "hangs". CI now gates, per suite. |

## Decision Required

**Two remain.** Ownership closed as D10 and the Gold coupling is deferred as
D11, so **nothing here now blocks a responsible merge** — both remaining items
are operational.
**Deferred — see D11. Retained because the evidence should not be lost:**

- **Confirm the Gold coupling.** *(owner of `uif-dal-gold`)* Which `{env}` role
   and bucket names Gold resolves today, and whether CP-3 and CP-4's revisit
   triggers are now invoked. **New evidence:** all five enabled Gold pipelines
   use `frequency = "gold-24h"`, which matches none of Echo's three Gold
   EventBridge rules — so those rules dispatch nothing. ADR-0004 **CP-5** says
   the platform team should have *disabled those rules and removed Gold from
   their Terraform state*. They are still here. So the question is not what to
   rename them to; it is whether CP-5's cutover was ever finished.
1. **Re-enable `Stacks CI`, or strike the "CI enforces" claim.** *(framework
   owner)* The workflow is `disabled_manually` — created 2026-08-28 (`598f7e9`),
   one run that morning at 09:00Z against `stacks/harbor` on a scratch branch,
   disabled at 09:53Z the same morning. Never run since, and never against a
   stack that ships code. Whether it was switched off *because* it failed on
   Beacon is inference, not record. What is certain: the `stacks/echo`,
   `stacks/beacon` and `stacks/harbor` CHECKLISTs all mark starred items "CI
   enforces", and **CI does not.** ADR-0010 built this workflow as an explicit
   precondition for Beacon's migration, and it has been off for almost all the
   time since. A disabled structural gate is worse than an absent one. With D7's
   amendment all four stacks pass, verified by simulation.
2. **Book the apply window.** *(Echo's owner, once named)* Measured from the live
   config: only 20 of 75 dev pipelines are enabled — 14 on `4hr`, 4 `hourly`, 1
   on daily slot 11, and 1 on `"12 hr"`, which matches no rule and has never
   dispatched. So 24 of the 33 rules currently fire into an empty set. Start
   about `:05` past an hour that is not a 4-hour multiple and is not 11:00 UTC.
   The apply is **176 replacements** and recreates all 33 EventBridge rules, so
   Bronze ingestion stops for its duration. Runbook:
   `stacks/echo/infrastructure/deploy/tier1-rename-runbook.md`.

## Not blocking acceptance, but tracked

- A single `pytest stacks/echo/qe` over all files dies with
  `INTERNALERROR MemoryError`. Per-suite is clean and is what CI runs; the root
  cause is unestablished.
- The DQ dispatcher's `dq_kinds` imports cannot resolve on Glue at all, so those
  kinds are likely hitting `except ImportError` in every real run. Pre-existing,
  surfaced by the move.
- Echo's own `echo-{env}-github-actions` OIDC role is scoped to
  `Raj-Karunakaran_pearson/enterprise-data-connect`, a third repository, and
  cannot be assumed from anywhere.
- `src/api/openapi.yaml` does not exist for 28 live routes.
