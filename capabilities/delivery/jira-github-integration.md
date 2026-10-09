# Jira ↔ GitHub Integration

## What this is and why it matters

When an engineer commits and pushes code, the matching Jira work item should move through its workflow on its own — nobody should have to open Jira and drag a card. This document defines how UIF connects GitHub to Jira so that the state of the code and the state of the plan stay in sync automatically.

UIF ships this in two mechanisms, delivered in order:

1. **Smart Commits (this document, phase 1).** The Atlassian GitHub app reads the Jira issue key from branch names, commit messages, and PRs, and applies workflow transitions written directly in the commit message. No code in this repository — it is configuration in Atlassian plus a commit-message convention every contributor follows.
2. **GitHub Actions workflow (phase 2, built).** `../../.github/workflows/jira-transition.yml` reads the issue key from the branch/PR and calls the Jira API to transition it, so transitions happen even when a commit message omits the smart-commit command. This is the belt-and-suspenders layer — see [Phase 2](#phase-2-github-actions-workflow) below.

Both target the same UIFPE project workflow. Phase 1 is convention-driven and put the engineer in control per commit; phase 2 enforces the baseline regardless of commit hygiene.

## Prerequisites (one-time, Jira/GitHub admin)

These are shared-system setup steps performed by a Jira site admin and a GitHub org admin. They are not done by this repository.

1. Install the **GitHub for Jira** app in the Pearson Jira site (`pearson.atlassian.net`) and connect the `pearson-dnt-coe/universal-Integration-framework` repository.
2. Confirm **Smart Commits** is enabled for the connected repository (GitHub for Jira → configuration → enable "Allow smart commits").
3. Ensure each contributor's git commit-author email matches a licensed Jira user in the site **exactly** — smart-commit commands run as that user and are silently ignored for unmatched authors. A `+alias` suffix does not match. This is a per-engineer git config, not an admin setting: see [Author matching](#author-matching--the-silent-failure-mode).

## The convention

### Reference the issue key everywhere

Every branch, commit, and PR title carries the UIFPE issue key so Jira can link and act on it.

- **Branch name:** `<type>/UIFPE-<n>-<slug>` — e.g. `feature/UIFPE-39-developer-registration-api`.
- **Commit message:** start the subject with the key — e.g. `UIFPE-39 implement contract handler`.
- **PR title:** include the key — e.g. `UIFPE-39 · Developer Registration API`.

Linking the code to the issue is automatic once the key appears. Comments and transitions each require their own smart-commit command below — plain trailing text in the commit message is not posted as a Jira comment.

### Smart-commit command syntax

```
UIFPE-<n> #comment <what changed> #<transition>
```

Two independent commands, and both matter:

- **`#comment <text>`** — posts `<text>` as a visible comment on the Jira issue's Activity tab. This is what makes the commit's intent readable to anyone watching the issue (PM, QE, reviewer) without them reading the diff. It is separate from the automatic commit link Jira shows in the issue's Development panel, which appears whenever the key is mentioned, `#comment` or not.
- **`#<transition>`** — moves the issue to the matching status. The token must match a UIFPE workflow transition name with spaces replaced by hyphens.

Put both in every commit that should move the issue forward. Multiple issues and commands can appear in one message.

Example commit message:

```
UIFPE-39 #comment Implemented the POST /developers handler and tests, opening for review #code-review
```

## UIFPE workflow → smart-commit token

The UIFPE project workflow exposes these transitions. Use the token in the smart-commit command.

| Target status | Transition name | Smart-commit token |
|---|---|---|
| To Do | To Do | `#to-do` |
| In Progress | In Progress (1) | `#in-progress` *(see caveat)* |
| Blocked | Blocked | `#blocked` |
| Code Review | Code Review | `#code-review` |
| Ready for QA | Ready for QA | `#ready-for-qa` |
| QA In Progress | QA In Progress | `#qa-in-progress` |
| Ready for UAT | Ready for UAT | `#ready-for-uat` |
| UAT In Progress | UAT In Progress | `#uat-in-progress` |
| Done | Done | `#done` |
| Cancelled | Canceled | `#canceled` |

## Recommended lifecycle mapping

This is the standard cadence for a Story or Task. Transitions are applied by the engineer in the commit message at each stage.

| Development moment | Commit command | Result |
|---|---|---|
| First commit on the feature branch | `UIFPE-<n> #comment Starting work #in-progress` | Comment logged; To Do → In Progress |
| Opening the PR (last push before review) | `UIFPE-<n> #comment PR opened, ready for review #code-review` | Comment logged; In Progress → Code Review — **the "ready for PR review" signal** |
| Merge to `main` | `UIFPE-<n> #comment Merged to main #ready-for-qa` (or `#done`) | Comment logged; Code Review → Ready for QA / Done — **the "ready for testing" hand-off to QE** |
| Work paused on an external dependency | `UIFPE-<n> #comment <reason> #blocked` | Comment logged; → Blocked |

For UIF's Development → UAT → Production promotion (`README.md`, GitHub Actions CD), the later transitions — `#qa-in-progress`, `#ready-for-uat`, `#done` — are driven from the CD workflow in phase 2 rather than by hand.

### Notifying QE and reviewers at each hand-off

A status change alone doesn't put the issue in front of a person — someone still has to be watching the board. Close that gap with a one-time **Jira Automation rule** per hand-off (Jira project settings → Automation; admin config, no code, same category as the Smart Commits app connection in Prerequisites):

| Trigger (issue transitions to) | Action |
|---|---|
| `Code Review` | Assign to / comment-mention the PR reviewer(s) — the "ready for PR review" signal becomes an actual notification. |
| `Ready for QA` | Assign to / comment-mention the domain's QE Lead — the "ready for testing" hand-off becomes an actual notification. |

This is the piece that turns "the status changed" into "the right person got told." **Required** for a complete Git ↔ Jira hand-off — configure once per project in Jira (UIFPE → Project settings → Automation); not enforced by this repository. Exact trigger/condition/action spec for each rule (plus a third rule for Bug→Story-assignee notification): `../../jira/jira-automation-rules.md`.

## Caveats

- **`In Progress (1)`:** the transition is named `In Progress (1)`, not `In Progress`. Smart-commit token matching strips whitespace, so `#in-progress` may not match because of the trailing `(1)`. Verify in a test commit; if it does not fire, ask a Jira admin to rename the transition to `In Progress`, or drive that transition from the phase-2 GitHub Actions workflow.
- **Transition must be legal from the current status:** Jira rejects a command whose transition is not available from the issue's present state. The tokens above are global transitions, so they apply from any state, but a project workflow change could restrict them.

### Author matching — the silent failure mode

Smart Commits authorise every action by matching the **commit author's email** to a licensed Jira user. On no match, Jira **discards the command silently**: no comment appears, no transition fires, and nothing is logged anywhere the engineer will see. This is the most common "nothing happened" cause, and the hardest to spot because a broken commit is indistinguishable from a correct one until you open the issue.

Jira matches the address **literally**. It does not normalise plus-addressing, so an author email of the form `first.last+alias@pearson.com` does **not** match the Jira account `first.last@pearson.com`. Case differences are fine; a `+alias` suffix is not. Because plus-addressed aliases are usually configured once in a global git config and then forgotten, this failure is persistent and affects every commit that engineer authors.

#### Every commit is authored by the engineer who wrote it

Smart Commits post the comment and apply the transition **as the commit author**. That makes the author field load-bearing rather than cosmetic: a borrowed identity attributes one engineer's work to another on the Jira issue, and a mismatched one drops the command entirely. Two rules follow, and they are not negotiable per machine:

- **One identity per engineer, never a shared or team address.** Each contributor commits under their own Pearson email, exactly as it appears on their Atlassian account. A machine configured with a colleague's address produces silently misattributed Jira activity and misleading `git blame` output — worse than no automation, because it looks correct.
- **AI-assisted commits are authored by the engineer who ran the session**, with the agent credited in a `Co-Authored-By:` trailer. Do **not** give agent-generated commits their own alias or service address. The engineer is accountable for what merges, the trailer records how it was produced, and the Jira comment lands under a real person who can answer questions about it.

On a machine more than one engineer uses, remove the fallback rather than trusting whoever set it last:

```
git config --global --unset user.email
git config --global --unset user.name
git config --global user.useConfigOnly true
```

With no global identity and `useConfigOnly` set, git **refuses to commit** until the repository has an explicit `user.email` — a hard error at commit time instead of a wrong name discovered weeks later in Jira. Each engineer then sets their own identity per clone:

```
git config user.name  "<Your Name>"
git config user.email <your-exact-Jira-account-email>
```

**Diagnose it in one command** — run this before concluding the integration is broken:

```
git log --no-merges --format='%ae' -20
```

If the address printed is not exactly the email on your Atlassian account (`id.atlassian.com` → Profile), Smart Commits cannot act on your commits.

**Fix:**

```
git config --global user.email <your-exact-Jira-account-email>
```

An Atlassian account cannot hold a second verified email, so the git config is the only fix — the alias cannot be registered on the Jira side.

**Two consequences worth knowing.** Jira processes smart-commit commands at push time only, so **already-pushed commits do not backfill** when the config is corrected — issues affected by the misconfiguration need their status and comments set by hand once. And because the transition token fails for the same reason as `#comment`, the board under-reports delivery: issues sit in `To Do` while their code is merged.

#### Enforced at three points

Both halves of the convention — *who* authored the commit and *what* the message says — are checked mechanically rather than left to review, because both fail silently in Jira.

Each rule is defined **once**, as a script under `../../.githooks/`, and called from every place that enforces it. There is no build step to catch drift between copies of a rule, so there is only one copy:

| Rule | Defined in | Blocks at commit | Blocks at push | Fails the PR |
|---|---|---|---|---|
| Author email matches a Jira user | [`lint-identity.sh`](../../.githooks/lint-identity.sh) | `pre-commit` | `pre-push` | [`commit-identity.yml`](../../.github/workflows/commit-identity.yml) |
| `UIFPE-<n>` + a descriptive `#comment` | [`lint-commits.sh`](../../.githooks/lint-commits.sh) | `commit-msg` | `pre-push` | [`smart-commit.yml`](../../.github/workflows/smart-commit.yml) |
| PR title and body are reviewable | [`lint-pr.sh`](../../.githooks/lint-pr.sh) | — | — | [`pull-request.yml`](../../.github/workflows/pull-request.yml) |

The third rule has no hook because a pull request is not a local object — there is nothing to refuse at commit or push time. It is checked on the PR and rechecked on every edit to it, so the fix never needs a new commit. Run it yourself before opening the PR: `.githooks/lint-pr.sh --title "…" --body-file <file>`.

**Install the hooks once per clone.** Git does not version `.git/hooks`, so the hooks live in `.githooks/` and are activated by pointing git at them:

```
git config core.hooksPath .githooks
```

#### The one exemption: history imported from another repository

Merge commits are exempt because their message is generated rather than written. There is one further exemption, and it has to be recorded to apply.

A `git subtree` import brings another repository's commits in with their history intact. Those commits were authored under that repository's rules, before this convention existed, and they can never carry a `UIFPE-<n>` key. Rewriting them to add one destroys the history the import was performed to preserve — which is usually the reason for importing rather than copying files.

Left unhandled, this blocks the import entirely: the gate is a required check, so a single unfixable commit stops the pull request. That is what happened to the Echo stack migration (PR #206, `UIFPE-355`) — 26 of its 56 linted commits were foreign history, and 64,086 lines of working platform sat unmergeable behind them. ADR-0015 records the move.

So each imported history records its tip in [`.githooks/import-boundaries`](../../.githooks/import-boundaries), and that commit plus its ancestors are excluded from the check:

```
bed4e0d  uif-relay-echo subtree import into stacks/echo — PR #206, UIFPE-355, ADR-0015
```

Three properties make this safe to rely on:

- **It narrows, never widens.** Only ancestors of a recorded commit are exempt. Everything authored in this repository is still linted — for PR #206 that left 30 commits checked, all passing.
- **It fails safe.** A SHA that is not present in the repository warns and is skipped, so a stale line becomes a no-op rather than a silent hole.
- **It is auditable.** Each line names the source repository and the pull request that brought it in, so the exemption can still be explained years later.

Adding a line is a deliberate act, not routine maintenance. If a commit is failing the gate and it was authored in *this* repository, the answer is to reword it — `git rebase -i` — not to record a boundary.

Do this immediately after cloning. Without it the commit-time checks do nothing and the first thing you hear about a bad commit is a red pull request.

The three points are deliberately redundant, because each catches what the one before it cannot:

- **`commit-msg` / `pre-commit`** reject the commit as it is being made. This is the only point at which nothing needs rewriting — the message is fixed while you are still writing it.
- **`pre-push`** re-checks every commit being pushed. It catches commits made before the hooks were installed, commits made with `--no-verify`, and commits pulled in from a branch that did not have them.
- **CI** re-checks the pull request. A hook only runs on a machine that has it configured, and any hook can be bypassed; a required status check cannot. This is the backstop, not the first line.

**All three checks are required status checks on `develop` and `validate`**, with no administrator exemption. Two consequences:

- **A pull request cannot be merged while any of the three is red.** Before this, they were advisory — PR #56 merged with the smart-commit check failing, which is exactly the gap being closed.
- **Direct pushes to `develop` and `validate` are refused** (`GH006 — required status checks are expected`). The checks run on `pull_request`, so a direct push has no check runs to satisfy them. Every change to these branches goes through a pull request, including a one-line documentation fix.

`main` is not covered: it does not carry these workflows, so a pull request opened directly against it is not checked. Ship the workflows there before requiring them there.

All three CI workflows run on **every** pull request with no path filter: a `capabilities/`-only change is checked exactly like a `domains/` one. Merge commits are skipped by the commit gates — GitHub generates them under its own identity and they carry no smart-commit tokens.

**Keep the issue key first on its line.** Git strips lines that begin with `#` when it cleans up a commit message, so a `#comment` at the start of a line never reaches Jira. `UIFPE-<n> #comment …` puts the token mid-line where it survives.

**`#comment` is required on every commit; a `#<transition>` token is not.** The key alone attaches the commit to a development panel most readers never expand, and plain trailing text is not posted as a comment — so without `#comment` the work is invisible to the PM and QE Lead, who do not read `git log`. Transitions, by contrast, belong at lifecycle points (first commit, PR opened, merged) rather than on every save, and Jira rejects one that isn't legal from the issue's current status. An unrecognised token produces a warning rather than a failure, since a typo'd transition is discarded silently by Jira in exactly the way a mismatched author is.

**The comment must describe the change, not mark it.** `#comment wip`, `#comment fix` and `#comment done` are rejected by a minimum of 20 characters and 4 words. That is a floor, not a quality judgement — this is the same two-tier split stated in `guidelines.md` §2: CI can verify that something was written, and the PR reviewer decides whether it describes the change. A comment that clears the floor and still says nothing (`minor updates to the file`) is a review comment, not a build failure.

Nobody should be composing these by hand. **Ask GitHub Copilot or Claude to draft the message from the staged diff** — summarising a diff in one line is what they are reliably good at — then read it and confirm it says what you actually did. The engineer stays the commit author and stays accountable for the wording (see above); the assistant is credited in a `Co-Authored-By:` trailer.

Neither gate has an allowlist, and both run at PR time rather than on push. A push-time check can only report a problem the history already carries; failing the PR is the last point at which the fix is still a rebase rather than a permanent gap in the trace.

### The pull request has to be reviewable

The commit gates make the history traceable. They do not make the change *understandable* — a branch of individually well-formed Smart Commits still arrives as a pull request titled `updates` with an empty body, and a reviewer then has nothing to read but the diff. The pull request is the only artifact in the chain that is read before the diff, and for the PM and the QE Lead it is usually the only artifact read at all.

[`pull-request.yml`](../../.github/workflows/pull-request.yml) requires four things:

| Required | Why |
|---|---|
| `UIFPE-<n>` in the title | Links the PR to the issue, and is what someone searching Jira for "what shipped for this" finds |
| A title that names the change | `UIFPE-39 · Product Data API`. A title made only of filler — `updates`, `minor fixes`, `wip` — is rejected: it tells a reviewer less than the branch name does |
| A `## Summary` section | What changed and why. The reasoning a reviewer needs *before* the diff, which the diff itself cannot supply |
| A `## Verification` section | How you know it works: the command and its result, the CI job, the environment you hit. An unticked checklist is not evidence |

Heading names are matched liberally — `Context`, `Why` or `What changed` satisfy the summary; `Test plan`, `Testing` or `Evidence` satisfy the verification. [`.github/PULL_REQUEST_TEMPLATE.md`](../../.github/PULL_REQUEST_TEMPLATE.md) already has the headings, and its guidance sits in HTML comments that are stripped before the text is measured, so an untouched template fails exactly as an empty body does.

As with the commit comment, the minimums are **floors, not quality judgements** — the same two-tier split as `guidelines.md` §2. CI verifies that a summary and a verification were written; whether the reasoning holds is the reviewer's call, and a PR that clears the floor while explaining nothing is a review comment rather than a build failure.

**The branch name is warned about, not failed on.** Convention is `feature/UIFPE-<n>-<short-description>`, and [`jira-transition.yml`](../../.github/workflows/jira-transition.yml) reads the key from the branch name, so a branch without one silently skips its transition. But renaming a branch once its PR is open means closing and reopening the PR, while the title is editable in place — so a keyless branch produces a warning and a keyless title produces a failure.

Draft the summary the same way you draft the commit message: **ask Copilot or Claude to summarise the branch diff**, then check that it says what you actually did and add the reasoning it cannot know.

*Verified against this repository's commit history and the UIFPE project, 2026-08-19: a test commit authored from a correctly-matched address posted its `#comment` and applied its `#code-review` transition as expected, confirming the app connection and the Smart Commits toggle are both correct — while 59 keyed commits carrying valid `#comment` tokens, all authored from a single plus-addressed alias, produced no comment on any of their issues.*

## Phase 2: GitHub Actions workflow

> **Engineer standard:** rely on **Smart Commits** (phase 1) for every commit that should comment on or move a UIFPE issue — `#comment` and `#transition` in the commit message. Phase 2's baseline transition is a safety net for when a commit omits it, not a replacement.
>
> **Known issue (open — Atlassian admin):** `jira-transition.yml` may return **401** from GitHub-hosted runners even with valid `JIRA_USER_EMAIL`/`JIRA_API_TOKEN` — Pearson's Atlassian instance appears to IP-allowlist API access and reject the runner's public egress IP. **Do not rely on Phase 2 until admin resolves this** (allowlist GitHub Actions runner IPs, or approve a self-hosted runner path). Smart Commits are the working mechanism today.

`../../.github/workflows/jira-transition.yml` enforces the baseline transition from Git events regardless of commit hygiene. It reads the `UIFPE-<n>` key from the branch name and drives the same UIFPE workflow. It matches on the destination **status name**, so the `In Progress (1)` transition-name caveat above does not apply to it.

**It also posts a comment on the same event** — commit SHA or PR link, the actor, and whether the transition actually applied (`Status moved to 'X'` vs `Attempted move to 'X' (no-op)`). This closes the gap Smart Commits leave when a `#comment` is forgotten: the issue's history still shows *what* pushed it, even without a hand-written comment. The comment step runs independently of the transition step (`continue-on-error` on transition, `if: always()` on the comment) — a transition failing never suppresses the comment, and a comment failing never fails the build.

Event → transition:

| GitHub event | Target status |
|---|---|
| Push to any branch except `main` | In Progress |
| PR opened as draft | In Progress |
| PR opened / reopened / marked ready for review | Code Review |
| PR merged into `main` | Ready for QA |
| PR closed without merge | (no transition) |

If the target transition is not available from the issue's current state (already there or not permitted), the step logs and exits cleanly — it never fails the build.

### Required repository secrets

Set these in the GitHub repository (Settings → Secrets and variables → Actions):

| Secret | Value |
|---|---|
| `JIRA_BASE_URL` | `https://pearson.atlassian.net` |
| `JIRA_USER_EMAIL` | Jira account email used for API auth |
| `JIRA_API_TOKEN` | API token for that account (id.atlassian.com → Security → API tokens) |

The account behind the token needs permission to transition UIFPE issues. Transitions run as that account, not the commit author — so, unlike Smart Commits, phase 2 does not depend on author-email matching.

## Governance

- **Central:** the branch/commit/PR key convention, the transition mapping, the Atlassian app connection, and the `jira-transition.yml` workflow are UIF standards — they do not vary per domain.
- **Local (FDE):** which transition to apply at which commit, within the recommended lifecycle above.

## Related

- Delivery capability overview: `./README.md`
- FDE build & deploy workflow: `../../architecture-design/operating-model/fde-build-deploy-workflow.md`
- Jira workflow states: `../../jira/jira-workflow.md`
- Confluence pointer page draft (thin wiki hub): `../../jira/confluence-pointer-draft.md`
