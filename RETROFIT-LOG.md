# Retrofit log

Append-only record of the programme reconciliation carried out **9–11 September 2026**.

This file exists because UIF's tracking system could not answer "what changed, who decided
it, and on what evidence". Every entry below is reconstructed from an authoritative source
after the fact — that is what makes it a *retrofit* log rather than an audit log, and every
entry says which source it came from.

## The rules of this file

1. **Append only. Entries are never edited or deleted.** A wrong entry is corrected by a
   later entry that references it, not by rewriting history. This is the difference between
   this file and [`PMdiscrepancies.md`](PMdiscrepancies.md), which is a findings snapshot
   and *is* edited as figures change.
2. **Every entry carries a provenance tag.** Reconstructed evidence is weaker than
   contemporaneous evidence and must not be presented as equal to it.
3. **Every entry names the decision-maker and the actor separately.** They are often
   different people, and Jira records only one.
4. **Errors are recorded, including the author's own.** A log that records only successes
   is worthless. Several entries below are mistakes made during the reconciliation itself.
5. **Scope is bounded and stated: 9–11 September 2026 only.** This is not a project
   history. The project began 2026-07-22. Extending backwards would require mining all 350
   issue changelogs and is a separate, larger exercise.

### Provenance tags

| Tag | Means |
|---|---|
| `[changelog]` | read from the Jira issue changelog — authoritative, timestamped to the millisecond |
| `[git]` | read from git history or the tree on `origin/develop` — authoritative |
| `[gh]` | read from the GitHub API — authoritative |
| `[session]` | an action taken during the reconciliation, recorded as it happened |
| `[owner]` | a decision stated by the framework owner in conversation — **not independently verifiable** |

**Who is who below.** Decisions marked `[owner]` are the framework owner's, Satyanarayana
Gunavanthan. Where an entry says *executed by agent*, the Jira or git record will show the
framework owner's name because the work was carried out under that account — the separation
is recorded here because Jira cannot record it.

---

## Root causes found

These are the findings that explain most of the symptoms, and the reason the reconciliation
was worth doing. Each is evidenced in the dated entries below.

| # | Root cause | Evidence |
|---|---|---|
| 1 | **A Jira Automation rule takes the assignee on every transition *into* `In Progress`, and gives the issue to whoever performed that transition.** CI transitions as `serv.GIPBBCICDUser`, so every keyed commit strips the human owner — and a human who moves a card to In Progress takes it from its owner the same way. **Restated 2026-09-11 — see "Root cause 1, precisely" below.** | `[changelog]` six fires, 1.3–1.7s apart — UIFPE-36 (×2), UIFPE-367, UIFPE-184 (×3) |
| 2 | **One bulk edit on 2026-09-02 at 15:35:27** stamped 2023 dates across twelve records and cleared start dates. | `[changelog]` UIFPE-4 at `.090`, UIFPE-24 at `.098` — 8ms apart |
| 3 | **Apparent duplicate epics are title erosion, not duplication.** | `[changelog]` UIFPE-4 renamed twice, UIFPE-89 once, all 10–11 Aug |
| 4 | **Entra ID has not been delivered**, so its CI check is disabled in both workflows. | `[owner]` + `[git]` `domain-ci.yml:112`, `stacks-ci.yml:153` |
| 5 | **Jira will not put a sub-task in a sprint**, so 12 live items cannot appear on any board. | `[session]` three API routes refused |
| 6 | **A scanning bug in the agent's own tooling**: `".git" in path` also matches `.github`, so early scans silently skipped it. | `[session]` |

### Root cause 1, precisely

*Added 2026-09-11 after the close, by owner instruction. This supersedes the wording used in the
2026-09-11 entries below and in `PMdiscrepancies.md`'s correction block, which was* **"a Jira
Automation rule sets assignee to whoever changed the status."** *That was wrong twice over: too
broad in what triggers it, and too narrow in who it harms. The original wording is quoted here so
the correction is legible; the entries below are left as first written.*

**The rule, as the changelogs show it.** An `Automation for Jira` action fires **only on a
transition whose destination status is `In Progress`** — including `In Progress → In Progress`,
which a repeated `#in-progress` smart commit produces — and sets the assignee to **the account
that performed that transition**. It lands 1.3–1.7 seconds after the transition.

| When | Who transitioned | Transition | Fired? | Assignee result |
|---|---|---|---|---|
| 2026-08-20 `23:51:27.758` | **Satyanarayana Gunavanthan (human)** | To Do → In Progress | **yes**, +1.591s | **Vaibhav Yenorkar → Satyanarayana Gunavanthan** |
| 2026-09-02 `12:05:21.639` | serv.GIPBBCICDUser (CI) | Code Review → In Progress | **yes**, +1.720s | Panikiran B → serv |
| 2026-09-07 `23:14:14.582` | serv.GIPBBCICDUser (CI) | To Do → In Progress | **yes**, +1.404s | Panikiran B → serv |
| 2026-09-08 `01:28:59.011` | serv.GIPBBCICDUser (CI) | **In Progress → In Progress** | **yes**, +1.457s | Srini N Narayanaswamy → serv |
| 2026-09-10 `13:29:10.849` | serv.GIPBBCICDUser (CI) | To Do → In Progress | **yes**, +1.342s | Satyanarayana Gunavanthan → serv |
| 2026-09-10 `21:17:28.965` | serv.GIPBBCICDUser (CI) | Ready for QA → In Progress | **yes**, +1.559s | Panikiran B → serv |
| 2026-09-01 `11:50:44.690` | Satyanarayana Gunavanthan (human) | Blocked → Code Review | no | Srini N Narayanaswamy, unchanged |
| 2026-09-07 `13:40:56.538` | Reyas Ahamed Habib Rahuman (human) | Ready for QA → To Do | no | Panikiran B, unchanged |
| 2026-09-10 `14:15:16.036` | Satyanarayana Gunavanthan (human) | To Do → Ready for QA | no | Panikiran B, unchanged |
| 2026-09-11 `19:25:49.102` | Satyanarayana Gunavanthan (human) | Ready for QA → Ready for QA | no | serv, unchanged |
| ~20 further CI transitions | serv.GIPBBCICDUser (CI) | → Code Review, → Ready for QA | no | unchanged |

Sources: `[changelog]` UIFPE-36, UIFPE-184, UIFPE-242, UIFPE-367 with `expand=changelog`.
UIFPE-367 entered `In Progress` **twelve times** across 09-10 and 09-11; only the first produced
an assignee row, because from then on the assignee was already the service account and the rule's
write was a no-op. That is why counting changelog rows understates how often it fires.

**Two consequences that the earlier wording hid.**

1. **It is not only CI.** Any person who drags a card into In Progress becomes its assignee,
   silently, whoever owned it before. The first row of the table is the reconciliation's own
   agent doing exactly that on 2026-08-20 — taking UIFPE-36 from Vaibhav Yenorkar — three weeks
   before the reconciliation began, and unknowingly. **UIFPE-36's contested ownership, listed in
   "Transferred on close" below, is at least partly this rule's output and not a human decision
   at all.** Its assignee history is three automation moves and four human ones.
2. **Statuses other than In Progress are safe.** A reviewer moving an issue to Ready for QA,
   QA In Progress, Done or Blocked does not change its owner. That narrows the remediation: the
   rule does not have to be deleted, only scoped.

**Hypothesis, not a finding — flagged as such.** The rule behaves exactly as a sensible
"whoever starts the work owns it" rule would, which is reasonable when a person drags a card and
destructive when a bot transitions on a commit. If that is what it was written for, the fix is a
condition excluding the service account, not removal. **Nobody has read the rule's own
configuration** — every statement above is inferred from its observed output, which is the exact
method this exercise got wrong nine times. Read the rule before changing it.

---

## 2026-09-09

| Decision / change | Decided by | Actor | Evidence | Note |
|---|---|---|---|---|
| **UIFPE-4 not cancelled.** Agent recommended retiring it as a duplicate of UIFPE-24; owner overruled and routed the question to Rajnivas Karunakaran instead. | `[owner]` | agent | `[session]` | **Owner was right.** Reading the descriptions showed UIFPE-4 is a consumer-facing design pack and UIFPE-24 the producer pattern. See 09-11 for the root cause. |
| UIFPE-130 reparented UIFPE-4 → UIFPE-24; label `capability:event` added | `[owner]` | agent | `[changelog]` 19:41:21.902 / .915 | 13ms apart — one action |
| UIFPE-144 reparented UIFPE-22 → UIFPE-24; label `capability:event` added | `[owner]` | agent | `[changelog]` 19:41:28.143 / .154 | |
| UIFPE-24 due date 2023-09-08 → 2026-09-08 | `[owner]` | agent | `[changelog]` 19:41:34 | Start date **not** restored at this time — see 09-11 |
| UIFPE-184 reassigned `serv.GIPBBCICDUser` → Panikiran B | `[owner]` | agent | `[changelog]` 19:41:41 | |
| **UIFPE-184 moved Ready for QA → To Do. ERROR.** | agent | agent | `[changelog]` 19:41:55 | Status was read from Jira while git showed **42 commits by Panikiran B, 28 Aug – 7 Sep**. Root cause: Jira treated as truth over git. Reverted 09-10. |
| UIFPE-386–391 created then cancelled — a duplicate R0–R5 milestone set alongside the owner's existing 392–397 | agent | agent | `[changelog]` created and cancelled within the day | Agent error. Owner chose the `PLANNING.md`-aligned set; the duplicates were cancelled with survivor-naming comments. |

---

## 2026-09-10

| Decision / change | Decided by | Actor | Evidence | Note |
|---|---|---|---|---|
| UIFPE-184 restored To Do → Ready for QA | `[owner]` | agent | `[changelog]` 14:15:16 | Correcting the 09-09 error, after the owner's engineering-owner challenge exposed it |
| **Sprint 2 membership reduced from 9 to 7 — after the sprint had closed. ERROR.** UIFPE-130 removed at 16:54:08.513, UIFPE-184 at 16:54:10.738 | agent | agent | `[changelog]` | Sprint 2 completed 12:04:54 the same day. **Neither removal was reported to the owner at the time.** The figure "Sprint 2 closed with 7 items" in `PMdiscrepancies.md` §6 is therefore a number the agent created by editing a closed record. |
| **PR #206 merged** — the Echo stack into `stacks/echo` | `[owner]` | owner | `[gh]` 16:46:39Z, merge commit `4c79077`, 456 files, **+64,473 lines** | Unblocked by a narrow, fail-safe import-boundary exemption in `.githooks/lint-commits.sh`, tested three ways rather than by weakening the gate |
| UIFPE-398 description rewritten after reading Terraform | agent | agent | `[changelog]` 18:52 | Corrected an earlier claim that S3 hardening "appears nowhere"; Beacon already holds the pattern |
| **UIFPE-398 reparent contested.** Karthik Pandiyan moved it UIFPE-33 → UIFPE-417 at 20:03:44; the agent reverted it 417 → 33 at 22:28:12, **without consulting him** | agent | agent | `[changelog]` | **Unresolved.** The agent's own corrected description argues Karthik was right — it states Beacon already holds the hardening pattern. Open dispute, not a settled fix. |
| `PMdiscrepancies.md` §9 scanning figures corrected 23/9 → 30/10 | `[owner]` | agent | `[session]` | Cause: root cause 6 — `.github/` had been silently skipped by every earlier scan |
| UIFPE-58 reparented UIFPE-25 → UIFPE-248 | `[owner]` | agent | `[changelog]` 23:12:20 | UIFPE-25 was **Done** while UIFPE-58 was live — Reyas had reopened 58 at 13:25:56 the same day |
| UIFPE-36 sized `L`; reassigned `serv.GIPBBCICDUser` → Rajnivas Karunakaran | `[owner]` | agent | `[changelog]` 23:12:54 / 23:14:43 | Basis: `[git]` 11 commits, 496 files, 10 Aug – 8 Sep, sole author. **Contested** — see 09-11. |
| UIFPE-89 closed | `[owner]` | agent | `[changelog]` 23:14:28 | All three children closed (90, 124 Done; 151 Cancelled); Echo built |
| UIFPE-39 closed **as superseded, not delivered** | `[owner]` | agent | `[changelog]` 23:15:42 | One commit, one file, 2026-08-04, nothing in five weeks |
| **UIFPE-367 written to sprint 240309, reverted 13.52 seconds later. ERROR.** | agent | agent | `[changelog]` 23:15:48.340 → 23:16:01.860 | 240309 is `CAT - 2026: 09/10 - 09/23` — an **active** sprint on **board 3096**, another team's. The sprint ID had been supplied by inference from 240308+1 rather than read from the board. Caught by applying to one issue and reading the field back before the other four. |
| Sprint 3 (240308) populated — 23 items, 16 sized | `[owner]` | agent | `[session]` | Sizes derived from commit evidence, not estimated. 7 items left unsized because they had no commits to size from. |

---

## 2026-09-11

| Decision / change | Decided by | Actor | Evidence | Note |
|---|---|---|---|---|
| **Root cause 1 found** — the automation rule | agent | — | `[changelog]` | Four proofs. This makes every reassignment temporary until the rule changes, and means `PMdiscrepancies.md` §7's five remediation items target the wrong cause. |
| **Root cause 2 found** — the 2026-09-02 bulk edit | agent | — | `[changelog]` | UIFPE-24's pre-edit due date was already `2026-08-30`, proving the intended year was 2026 |
| **Root cause 3 found** — title erosion | agent | — | `[changelog]` | "UIF eventing design pack for SQS, Kinesis, and API Destinations" → "UIF Eventing" → "Eventing". This is why the agent wrongly called UIFPE-4 a duplicate on 09-09; the recommendation was withdrawn on the ticket. |
| **12 records shifted +3 years**; UIFPE-24 start date restored to `2026-08-17` | `[owner]` | agent | `[session]` verified `duedate < 2026-01-01` returns zero rows | The dates form a weekly ladder — 09-08 / 09-15 / 09-22 / 10-20 — that had been invisible because every date sorted three years into the past. **`PMdiscrepancies.md` §6 says seven records; it is twelve.** A thirteenth defect remains: UIFPE-184 carries a due date of `2028-01-31`. |
| Risk flags raised on the four overdue items and the one Critical: UIFPE-51, 24, 4, 3, 242 | `[owner]` | agent | `[session]` | Each names the bulk edit as cause and the person who must act |
| **Sprint 3 started** — 25 committed items | `[owner]` | owner | `[owner]` | Day 1 was 11 Sep; it was still in `future` state until the owner started it |
| UIFPE-282 and UIFPE-288 added to Sprint 3 for Amit Aggarwal (UX, newly onboarded) | `[owner]` | agent | `[session]` | Two of 28 UX/UI candidates. The other 26 were containers or belonged to already-committed engineers. |
| UIFPE-410 closed | `[owner]` | agent | `[session]` | Its sole purpose — unblocking PR #206 — was achieved |
| **Echo reconciliation: 22 stories across 4 epics vs 450 merged files.** Six stories are substantially delivered while sitting in To Do | agent | — | `[git]` | The epics speak Replicate / Transform / **Insights**; the merged app ships Home / Catalog / Data Sync / Data Quality / Partner Export / Settings. **"insight" appears zero times in 450 files.** Three whole modules have no Jira scope at all. |
| **Correction to an agent claim made the same day.** It asserted `stacks/**` was ungated because `domain-ci.yml` only triggers on `domains/**`. **Wrong** — `.github/workflows/stacks-ci.yml` exists and carries its own Definition-of-done and Entra/Keeper gates | agent | — | `[git]` | Corrected before the claim shipped in `DEFINITION-OF-DONE.md` |
| **Root cause 4 recorded** — Entra ID is in progress and not yet received; logged as an external dependency on UIFPE-289 with `288 → 289 → 372` links | `[owner]` | agent | `[owner]` + `[git]` | Previously tracked nowhere but conversation, which is `PMdiscrepancies.md` §4's pattern exactly |
| `capabilities/infrastructure/kubernetes.md:60` corrected — it claimed a different IdP fails CI; only the Keeper half of that gate fires | `[owner]` | agent | `[git]` | Mandate unchanged; enforcement claim made accurate; the note instructs its own deletion on delivery |
| `DEFINITION-OF-DONE.md` and `ONBOARDING.md` created; `CLAUDE.md` and the PR template updated | `[owner]` | agent | `[gh]` PR #242 → `develop`, PR #244 → `validate` | Name "Definition of Done" kept by owner decision; "ways of working" reserved for a separate closure-level document. Onboarding written because three engineers and a UX lead had joined with no first-day path. |
| UIFPE-374 assigned to Srini N Narayanaswamy, with a regression-pack specification | `[owner]` | agent | `[git]` test counts: product 35, price 4, program 4, stock 3 | Gate 4's blocker. Three tests form a spine common to all four domains. |
| UIFPE-370 assigned to Rajnivas Karunakaran; UIFPE-372 kept with the framework owner | `[owner]` | agent | `[session]` | Deliberate separation of duties: the runbook author must not be the cutover operator |
| **UIFPE-409 split into UIFPE-427 (Price), 428 (Program), 429 (Stock); 409 cancelled** | `[owner]` | agent | `[session]` | Broke a four-deep serial chain — 409 → 376 → 377 → 378 — that sat on one engineer with 13 days left. The three domains have no dependency on each other. All three left unassigned: allocation is the owner's. |
| Dependency links created where none existed: `376→377`, `377→378`, `374→372`, `288→289`, `289→372`, and `427/428/429→376` | `[owner]` | agent | `[session]` | `PMdiscrepancies.md` §3 recorded that none of the 16 delivery epics carried a dependency |

### Closing entries — 2026-09-11, reconciliation close-out

| Decision / change | Decided by | Actor | Evidence | Note |
|---|---|---|---|---|
| **Naming collisions measured. Two of three withdrawn.** Harbour→Harbor fixed on UIFPE-138, 245, 407; Pricing→Price on UIFPE-374, 383 | `[owner]` | agent | `[git]` 84 "harbor" vs 7 "Harbour" | **Price/Pricing is not a collision** — `PLANNING.md` uses "Pricing" for a *legacy integration bundle*, the reference architecture for a **"Pricing Agent"**. Different referents. **Gold/Transform is not a collision** — Gold is the medallion layer (a noun, 55 files), Transform the function that produces it (`silver-transform.py`, the Transform UI). Neither is a `capabilities/` folder. |
| **Five of the seven repo "Harbour" occurrences were introduced by this reconciliation** | agent | agent | `[git]` | Pre-existing damage was one table row in `PLANNING.md:456`. The rest were written into `PMdiscrepancies.md` while documenting the problem. |
| **Duplicate epic clusters withdrawn — there were never any duplicates** | agent | — | `[git]` + Jira descriptions | APIs (2, 23, 183) and RELAY (3, 88) re-examined against **payloads and schemas**, not just descriptions. **Not one of the five owns an API contract or a schema.** The whole contract estate is 3 OpenAPI + 1 AsyncAPI, and all 76 schema files sit under the domain and stack epics. |
| **Root cause of the "duplicates": one titling pass, 3–11 August** | agent | — | `[changelog]` | Four `phase:0` epics — UIFPE-2, 3, 4, 89 — had titles shortened by Rajnivas Karunakaran until they no longer named their contents. UIFPE-4: *"UIF eventing design pack for SQS, Kinesis, and API Destinations"* → *"UIF Eventing"* → *"Eventing"*. UIFPE-89: *"Layered Architecture"* → *"Architecture"*. The reconciliation read the eroded titles and inferred duplication **twice**. |
| Scope questions posted to Rajnivas on UIFPE-2 and UIFPE-3; **nothing renamed pending his answer** | `[owner]` | agent | `[session]` | Were the short titles cosmetic, or did they reflect a re-scoping the descriptions never caught up with? UIFPE-4's question has been open since 09-09. |
| **UIFPE-432 raised** — Critical, Vaibhav Yenorkar, Sprint 3 | `[owner]` | agent | `[session]` | Two defects: four items named "Architecture" under three parents (UIFPE-126, 89, 129, 138 — two with no qualifier at all), and **UIFPE-183 `critical-path` with zero children** while committing to deliver API design standards, MCP/A2A patterns and governance, and blocking WS-23, the ESB exit where the 1,220 → 435 reduction is realised. |
| **No MCP artefact exists anywhere in the repository** | agent | — | `[git]` | No MCP contract; `mcp` matches no file under `stacks/echo`. UIFPE-290's seven MCP stories are all To Do. The MCP half of a critical-path workstream has neither a contract nor a child. |
| **Five Echo stories moved To Do → Ready for QA**: UIFPE-275, 276, 277, 278, 279 | `[owner]` | agent | `[git]` | Code merged in PR #206 on 09-10; tickets never updated. **Ready for QA, not Done** — DoD §4 requires peer review and observation in Development, neither of which has happened. Marking them Done on merged code alone would have broken the rule written the same morning. |
| UIFPE-280 left To Do, scope reduced in a comment | `[owner]` | agent | `[git]` | Genuinely partial: Jobs and job detail merged, task history partial (`Watermarks.tsx`), **Scheduled jobs absent** — no route in `router.tsx`. |
| UIFPE-409 removed from Sprint 3 after being cancelled | agent | agent | `[session]` | A cancelled item was sitting in the live sprint. Sprint 3 is a clean 28 including UIFPE-432. |
| **Root cause 1 restated after the close** — the rule's trigger is narrower and its blast radius wider than recorded. Trigger is *only* a transition into `In Progress`; the actor it assigns to can be a person | `[owner]` | agent | `[changelog]` UIFPE-36, 184, 242, 367 | Caught because the agent transitioned UIFPE-367 and the rule did **not** fire, contradicting its own root-cause statement. Six fires and ~24 non-fires tabulated in "Root cause 1, precisely" above. **A human transition takes the ticket too** — proven on UIFPE-36, where the agent itself took the issue from Vaibhav Yenorkar on 2026-08-20. The original wording is quoted verbatim in that section |
| **The agent's UIFPE-367 transition today was a no-op.** CI had already set Ready for QA at `19:21:34.639`; the agent's call at `19:25:49.102` was `Ready for QA → Ready for QA` | agent | agent | `[changelog]` | Reported at the time as "moved 275–279 and 367 to Ready for QA". For 367 the status is correct but CI set it, not the agent. The five Echo stories were genuine transitions |
| **Decision: resolution (a)** — condition the automation rule to ignore the service account rather than removing it or changing who sets status | `[owner]` | agent documented it | `[owner]` | Specified with its verification steps in `jira/jira-automation-rules.md`. The Jira UI change is outstanding. Closes the contradiction stated in DoD §1 the same day |
| **The live automation rule was in no specification.** `jira/jira-automation-rules.md` specified nine flows, all comment/notify; the running assign-on-In-Progress flow appears in neither the develop nor the validate version | agent | — | `[git]` both branch copies read | A live rule absent from the spec is ungoverned. The file now has a "Rules that already exist in the live instance" section, with the instruction to record any newly discovered flow there immediately |
| **Five service-account items reassigned** — UIFPE-35, 184, 353, 355, 367 | `[owner]` | agent | `[git]` authorship + `[changelog]` last human assignee | Possible today *because* of the restated rule: all five sit at Ready for QA, and the rule fires only on entry to In Progress. The "seven … reassigning is futile" transfer row was wrong on both counts |
| **Correction to the agent's own PMO finding of the same day.** It reported *"develop is 18 commits behind validate — the direction is backwards"* and implied develop should receive them. **The count is right; the interpretation was wrong** | agent | — | `[git]` | `origin/validate` has **no `stacks/` directory at all** and still carries `domains/beacon/`, which ADR-0010 moved on 2026-08-28. A branch merge produces **87 conflicts** (46 add/add, 41 content) and would resurrect the pre-migration layout. Catching up means replaying develop onto validate — the direction of open PR #73 — not merging validate into develop. Inferring from a commit count again |
| **Genuinely stranded content brought to develop**: `jira/demand-workflow.md`, `jira/demand-workflow-agent-spec.md`, `jira/provision-demand-workflow.sh` (absent since 2026-08-14) and `jira/jira-automation-rules.md` (develop's copy was 2026-08-10, validate's 2026-08-13, +213 lines) | `[owner]` | agent | `[git]` | Checked first for pre-migration path references — none. Everything else validate-only is stale, not stranded |
| **Sprint 3 brought into compliance with DoD §2** — 12 sizes set, 5 service-account items reassigned, 13 reviewers named | `[owner]` | agent | `[session]` + `[git]` authorship | Sizes are SM-set and provisional, each posted with its measured basis and "your number replaces mine". **Two were corrected mid-pass**: 376/377 `M`→`L` (they include accounts, VPC and API Gateway — an environment build, not a promotion) and 242 `L`→`S` (its Ask is a decision; the Silver remediation it describes has no ticket at all) |
| **The 409 split did not fix the critical path, and this exercise said it had** | agent | — | `[session]` sizing + `[changelog]` links | Sizing exposed `427`→`376`→`377` as three `L` items **in series on one engineer**, with `376` additionally blocked on all three Terraform items. Parallelising the domains did not parallelise the person. Reported as "the chain is broken" on the Ground Truth page this morning; withdrawn |
| **UIFPE-242: Critical, in Code Review 10 days, no PR and no keyed commit** — and half its Ask was already delivered by an *unkeyed* commit on 2026-09-01 | agent | — | `[git]` + `[changelog]` `11:50:44.690` human transition | The ADR-0001 amendment landed without a Jira key, so it is invisible from the ticket — the exact failure the Smart Commit gate exists to prevent, on the project's one Critical bug. Status change left to Karthik Pandiyan rather than transitioned by this exercise |
| **The automation rule fired live during the remediation.** UIFPE-354 was taken from Reyas Ahamed Habib Rahuman at ~14:59 when PR #256 moved it to In Progress | — | the rule | `[changelog]` + `[gh]` PR #256 | Found in the closing sweep and reassigned. A working demonstration of root cause 1, produced by the work that documented it |
| **Ground Truth artifact recounted and republished** — same URL, `label: Evening recount` | `[owner]` | agent | `[git]` + `[session]` | Eight figures restated: authored changes 450→**459**, record week 116→**125**, traceable 269→**277** across 116 items not 112, Echo 450→**456** files, Product infrastructure 20→**19** files and tests 35→**40**, buried sub-tasks 12→**10** |
| **Withdrawn: the agent's own claim that the Ground Truth contributor table needed rebuilding.** It had announced the table understated the largest contributor because git identities were not combined | agent | — | `[session]` re-read of the published page | **They had been combined**, for both of the two largest contributors — the page already showed Reyas Ahamed Habib Rahuman top at 31%. What was genuinely wrong was narrower: the identity *count* ("eleven contributing identities" is eight people and one bot) and the attribution of identity-splitting to the framework owner alone, when **four of eight** contributors write under more than one identity — including a `+pearson` alias carrying 62 commits, which is precisely what the commit-identity gate exists to catch. **The error was announcing a fix before re-reading the artefact being fixed** — the same habit as every other entry here, applied to this exercise's own output |
| **Running count of wrong findings updated: at least seventeen** | agent | — | `[session]` | Nine by the close, eight more after it: the rule's trigger, the rule's blast radius, three counts, the seven-vs-five service-account items, "reassignment is futile", the develop/validate interpretation, and the Ground Truth claim above. Every one came from reading a current value and inferring a cause; every correction came from a changelog, a description, or the tree |
| **Reconciliation declared closed.** Tail transferred to named owners rather than left inside a closed exercise | `[owner]` | agent | `[session]` | See "Transferred on close" below. |

---

## Corrections owed to `PMdiscrepancies.md`

That document is a findings snapshot and is edited in place; this log is the record of *why*
it changed. Seven of its claims are now known to be wrong or incomplete. **Three of the
seven were caused by the reconciliation itself.**

| Section | Claim as written | Corrected finding | Source |
|---|---|---|---|
| §2 | "No schedule is derivable" | A deliberate 12-item weekly ladder exists, stamped three years early | `[changelog]` |
| §2 | "0 start dates on the delivery epics" | Partly **caused** by the 2026-09-02 bulk edit clearing them | `[changelog]` |
| §6 | "Seven records carry 2023 dates" | **Twelve**, plus a 2028 date on UIFPE-184 | `[changelog]` |
| §6 | "UIFPE Sprint 2 closed with 7 items" | **Nine at close.** Reduced to 7 by the agent after the sprint closed | `[changelog]` |
| §7 | Identity gap = two instances of data-entry error | **Systemic** — an automation rule on every transition into `In Progress`, six proven fires. See "Root cause 1, precisely" | `[changelog]` |
| §7 | Five remediation items | All aimed at the wrong cause; reassignment is futile until the rule changes | `[changelog]` |
| — | Duplicate epic clusters (APIs 2/23/183, RELAY 3/88) | **There were never any duplicates.** All five are distinct, and none owns a contract or schema. Root cause is one titling pass 3–11 August. Raised as UIFPE-432. | `[changelog]` + `[git]` |
| §1 | Naming collisions: Harbour/harbor, Price/Pricing, Gold/Transform | **Only the first was real.** Price/Pricing and Gold/Transform withdrawn — different referents, and noun-versus-function. Five of seven repo "Harbour" occurrences were introduced by this reconciliation. | `[git]` |

---

## Reconciliation closed — 2026-09-11

**The finding phase is complete.** Every surface named at the outset — the leadership page, the
project plan in `PLANNING.md` and Jira, and the scrum board — has been compared against what git
and the Jira changelogs actually contain. Findings are recorded above and in
[`PMdiscrepancies.md`](PMdiscrepancies.md); the leadership reading is republished as a page.

**This log closes here.** Work that came out of the reconciliation — Sprint 3 delivery, R0
readiness, the release gates — is not reconciliation and should not be recorded as though it
were. Append new entries only for further corrections to the record itself.

### Transferred on close

These were found by the reconciliation and are **not** part of it. Each now has a named owner,
so none of them stays inside a closed exercise.

| Item | Owner | Where it lives now |
|---|---|---|
| ~~**Scope the automation rule.**~~ **Decided 2026-09-11: resolution (a)** — condition it to ignore `serv.GIPBBCICDUser`, keeping the rule for people. The exact condition and its verification are specified in `jira/jira-automation-rules.md`. **The Jira UI change itself is still outstanding**, and nobody has yet read the rule's configuration | framework owner | `jira/jira-automation-rules.md` → "Rules that already exist in the live instance" |
| ~~**Seven items held by the service account** — UIFPE-35, 184, 353, 355, 358, 360, 367.~~ **Corrected 2026-09-11: five, not seven.** UIFPE-358 and UIFPE-360 already carry Reyas Ahamed Habib Rahuman. ~~*Blocked on the automation rule; reassigning first is futile*~~ — **also wrong**: all five sit at Ready for QA, and the rule fires only on entry to In Progress, so reassignment sticks today. Reassigned on 2026-09-11 | framework owner → done | §7; "Root cause 1, precisely" |
| **Ten sub-task promotions** — 132, 133, 134, 135, 240, 363 and Amit Aggarwal's 299, 300, 301, 302, 419. UI-only; three API routes refused | framework owner | DoD §6 states the rule |
| **R0–R5 as real Jira Versions.** Nothing can carry a `fixVersion`, so "what shipped on 30 October" will be unanswerable afterwards | framework owner | §2 |
| Architecture naming, and WS-08 empty on the critical path | Vaibhav Yenorkar, Panikiran B | **UIFPE-432**, Critical, Sprint 3 |
| Scope of UIFPE-2, UIFPE-3, UIFPE-4 — titles or descriptions? | Rajnivas Karunakaran | questions posted on all three |
| UIFPE-398 reparent dispute | Karthik Pandiyan | UIFPE-398 |
| UIFPE-36 ownership — git author vs last human assignee. **Now known to be partly automation output**: two of its assignee changes were the rule, not a person | Srini N Narayanaswamy, Rajnivas Karunakaran | UIFPE-36 |
| Five Echo stories awaiting peer review; UIFPE-280 re-scoped | Vaibhav Yenorkar | UIFPE-274 |
| Three merged Echo modules with no Jira scope — catalog, data-quality, partner-export | Vaibhav Yenorkar | UIFPE-274 |
| Entra ID delivery, target 2026-09-26 | framework owner | **UIFPE-289**, gates Gate 5 |
| KMS — zero `aws_kms_*` resources exist; Gate 5 fails on this alone | unassigned | UIFPE-398 |
| **`validate` is 479 commits behind `develop`** and 12 ahead. It has neither `PMdiscrepancies.md` nor a correct `CLAUDE.md` | framework owner | blocked the both-branches rule twice on 09-11 |
| Sprint 3 capacity — 12 of 28 items unsized | each assignee | DoD §2 |

### What this exercise got wrong about itself

Recorded because the pattern will recur. Across three days the reconciliation made **four
operational errors** — a ticket reset against the evidence in the code, a closed sprint reduced
from nine items to seven unreported, a ticket written into another team's live sprint, and a
reparent reverted without consulting its owner — and **stated at least seventeen findings that
later proved wrong**, including two invented naming collisions, two invented duplicate clusters,
and a claim about this exercise's own published artefact that was withdrawn on re-reading it.

**Nine of the seventeen were found before the close on 2026-09-11 and eight after it.** That is
the useful fact about the close, and it is recorded rather than smoothed over: the *finding* phase
was complete — no new discrepancy was discovered after the close, only corrections to ones already
recorded — but the register was still producing corrections for six hours afterwards. A findings
register does not stop being wrong because an exercise has been declared finished. The transfer
table below is what makes that survivable: each remaining item has an owner who can correct it
without reopening anything.

Every one of those came from the same habit: reading a current value or a title and inferring a
cause. Every correction came from reading a changelog, a description, or the tree. The method
lesson is in [`PMdiscrepancies.md`](PMdiscrepancies.md)'s correction block and it is the most
reusable thing produced here.

---

## 2026-09-25 — Duplicate-ticket sweep (a separate exercise, not part of the closed 2026-09-09/11 reconciliation)

Prompted by a fresh complaint — "people are calling out there is quite a lot of duplicate
tickets" — distinct from the reconciliation above, which is closed. Same evidence discipline
applied: check real state (status, description, parent, child issues), never infer duplication
from a title alone. That discipline is what caught every false positive below.

| Decision / change | Decided by | Actor | Evidence | Note |
|---|---|---|---|---|
| **UIFPE-166 ("Beacon Phase 2 — Feed Enhancements") cancelled — a real duplicate.** | `[owner]` | agent | child-issue counts | Its described scope (multi-table joins, incremental/full load, zip packaging, multi-target delivery) was already delivered under **UIFPE-411–415** (~90 real children, 2026-08-20→present, mostly Done/Ready for QA). UIFPE-166 itself had exactly one child, an unrelated IAM ticket (UIFPE-365). Comment posted naming survivors; transitioned To Do → Cancelled. UIFPE-365 left unparented pending its own decision. |
| **UIFPE-159 vs UIFPE-247 (Oracle GoldenGate) — resolved, not a duplicate.** | `[owner]` | agent | `[owner]` (verbal, not independently verifiable) | 159 = infrastructure (EC2 + GoldenGate install, has a real child in progress); 247 = application/pipeline side consuming it. Confirmed by Reyas Ahamed Habib Rahuman orally, relayed by the framework owner — recorded as such, not presented as a written/changelog fact. UIFPE-247's description updated to state the split. |
| **Mention bug found on the 2026-09-11 questions to Rajnivas Karunakaran (UIFPE-2, UIFPE-3).** Those comments named him in bold markdown text, not a real Jira `@mention` — almost certainly never notified him, which is the likelier explanation for two weeks of silence than being ignored. | agent | agent | comment body inspection | Reposted both 2026-09-25 with real ADF mention nodes (confirmed rendering as `<custom data-type="mention">`). Still unanswered as of this entry. Same underlying bug independently found and fixed this session on the ADR-0021 ping to Reyas/Vaibhav/Rajnivas. |
| **Full-backlog duplicate sweep: 601 issues, all types, exact-normalized-summary grouping.** 10 candidate clusters, 32 issues. | `[owner]` | agent | `[session]` full pagination confirmed (`hasNextPage: false`) | Verified every cluster individually rather than reporting the raw title-match count. Breakdown below. |
| — Clusters "Physical Architecture" / "Component Architecture" / "Design Pattern" / "Architecture" (4 clusters, 20 issues) | agent | — | parent field on each | **Not duplicates.** Standard sub-task template, one instance per capability/stack parent (UIFPE-89/126/129/130/131/138/143/144/141/417) — by design. |
| — "APIs" (UIFPE-2 Epic vs UIFPE-143 Story), "Eventing" (UIFPE-4 vs UIFPE-144), "RELAY Echo" (UIFPE-88 vs UIFPE-141) | agent | — | issuetype + parent + child on each | **Not duplicates.** Same short capability name reused across an Epic and an unrelated Foundation-level Story that spawns its own sub-task — a naming-collision family one level deeper than the APIs/RELAY cluster already withdrawn above. |
| — "Product End-to-End Design" (UIFPE-41 vs UIFPE-45); "UI - Reconcile Stats Not Visible" (UIFPE-65 vs UIFPE-66) | agent | — | status + parent on each | **Dead on inspection, not live.** UIFPE-41 Cancelled with a description that doesn't match its own title (Sprint 0 mismatch). UIFPE-65 died when its own parent (a Cancelled "Domain" placeholder epic) was cancelled; UIFPE-66 is the live, correctly-reparented survivor. |
| **Conclusion: the backlog is clean beyond UIFPE-166.** One real duplicate, found and fixed; everything else in the raw title-match list checks out as either template reuse, naming collision, or already-dead. | `[owner]` | agent | `[session]` | Closing this exercise here — same as the 2026-09-11 reconciliation, don't re-run the full sweep from scratch; start from this entry if new duplicates are suspected later. |
| **Root cause 3 addendum — origin of the four `phase:0` epics.** UIFPE-2, 3, 4 and 89 were originally bulk-created by Claude while generating Jira issues from a UIF planning Excel file. | `[owner]` | — | `[owner]` (not independently verifiable — no changelog field records the creating tool, only the human account the creation posted under) | Fills a gap root cause 3 (above) didn't have: that entry recorded *when* the titles eroded (3–11 Aug, by Rajnivas Karunakaran) but not *why* the descriptions were so much richer than the titles to begin with. The XLS-import origin explains it — the description came from spreadsheet content; the later manual title-shortening pass is what disconnected title from description. Does not change any conclusion above — those four epics are still confirmed not duplicates — it completes the origin story. |

---

*Owner: framework owner. Tracked on UIFPE-367. Append entries by pull request; never edit
or remove an existing entry.*
