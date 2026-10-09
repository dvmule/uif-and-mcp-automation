# PM discrepancies

This file records the gap between what the UIF programme has actually built and what its tracking
system can show. It exists because status questions about UIF cannot currently be answered from
Jira — not because the work is going badly, but because the plan and the delivery work were built as
two separate structures that were never joined. Anyone asked "are we on track?" today has to
reason from a document rather than query the system of record.

This is an internal engineering and programme-management document. It is not a status report and it
is not for a leadership audience — it lists defects and the order in which to fix them.

**Scope.** Every figure below was read from the 350 live records in the `UIFPE` Jira project on
**2026-09-10**: issue types, parent links, labels, dates, assignees, estimates, sprints, releases and
dependency links. Two figures are quoted rather than recomputed and are flagged where they appear.
Each section carries the JQL to re-derive it, so nothing here has to be taken on trust.

**Relationship to other documents.** [`PLANNING.md`](PLANNING.md) is the programme plan and remains
the source of truth for workstream scope, the `WS-01`…`WS-37` grid, releases and dependencies. This
file does not restate any of it; it records where Jira and that plan disagree. The leadership-facing
reading of the same data is published separately as a page, not held in the repository.
[`RETROFIT-LOG.md`](RETROFIT-LOG.md) is the append-only record of what was changed during the
reconciliation and why; [`DEFINITION-OF-DONE.md`](DEFINITION-OF-DONE.md) is what "finished" means.

> ## ⚠️ Seven claims below are now known to be wrong — corrected 2026-09-11
>
> On 2026-09-11 the Jira **issue changelogs** were mined for the first time. They are authoritative
> and timestamped to the millisecond, and they overturned seven findings in this document. The
> figures here were read from *current field values*, which cannot show how a value got there.
> **Three of the seven errors were caused by the reconciliation itself.**
>
> Each correction is evidenced entry-by-entry in [`RETROFIT-LOG.md`](RETROFIT-LOG.md). Summary:
>
> | Section | As written below | Corrected |
> |---|---|---|
> | §2 | "No schedule is derivable" | A deliberate **12-item weekly ladder exists**, stamped three years early by one bulk edit on 2026-09-02 at 15:35:27. Corrected in Jira 2026-09-11. |
> | §2 | "0 start dates on the delivery epics" | Partly **caused** by that same bulk edit clearing them. UIFPE-24's `2026-08-17` was destroyed and has been restored. |
> | §6 | "Seven records carry 2023 dates" | **Twelve** — plus a thirteenth defect, a `2028-01-31` due date on UIFPE-184. |
> | §6 | "UIFPE Sprint 2 closed with 7 items" | **Nine at close.** It was reduced to seven by this reconciliation *after* the sprint closed — so the figure quoted below is one this exercise created. |
> | §7 | Identity gap = two instances of data-entry error | **Systemic.** A Jira Automation rule fires on every transition **into `In Progress`** — and only into `In Progress` — and assigns the issue to whoever performed it, 1.3–1.7s later. CI transitions as the service account, so every keyed commit strips the human owner; **and a person who moves a card to In Progress takes it from its owner the same way.** Six fires and ~24 non-fires are tabulated in [`RETROFIT-LOG.md`](RETROFIT-LOG.md) under "Root cause 1, precisely" — which also corrects this row's own first wording, *"sets assignee to whoever changed the status"*, as too broad in trigger and too narrow in harm. |
> | §7 | The five remediation items | **All aimed at the wrong cause.** Reassigning people is futile until that rule changes. |
> | §1 | Duplicate epic clusters | **There were never any duplicates.** All five epics were re-examined on 2026-09-11 against the payloads and schemas they own, not just their descriptions — and **not one of the five owns an API contract or a schema**. The entire contract estate is 3 OpenAPI documents and 1 AsyncAPI; all 76 schema files sit under the domain and stack epics. Root cause: one titling pass across 3–11 August shortened four `phase:0` epics (UIFPE-2, 3, 4, 89) until they no longer named their contents. Raised as **UIFPE-432**. |
> | §1 | Naming collisions — Harbour/harbor, Price/Pricing, Gold/Transform | **Only the first was real**, and five of its seven repository occurrences were introduced *by this document*. **Price/Pricing withdrawn**: `PLANNING.md` uses "Pricing" for a legacy integration bundle and the reference architecture for a "Pricing Agent" — different referents, both legitimate. **Gold/Transform withdrawn**: Gold is the medallion layer, Transform the function that produces it; neither is a `capabilities/` folder. Fixed: UIFPE-138, 245, 407, 374, 383. |
>
> **Reconciliation closed 2026-09-11.** Its finding phase is complete and its remaining items are
> transferred to named owners — see the "Transferred on close" table in
> [`RETROFIT-LOG.md`](RETROFIT-LOG.md). This document remains the live findings register and is
> still edited as figures change; that log is append-only and records why.
>
> **The method lesson, recorded because it will recur:** current field values describe a state;
> changelogs describe a history. Every claim in this document about *why* something is the way it
> is was inferred from the former, and seven of those inferences were wrong. Mine the changelog
> before attributing a cause.

## What the project actually contains

| Layer | Count | Holds work? |
|---|---|---|
| Capability (level 2) | 21 | No — 6 of these are not capabilities at all |
| Epic (level 1) | 79 | 34 do; **45 hold nothing** |
| Story · Task · Bug · Spike (level 0) | 201 | Yes |
| Sub-task (level -1) | 49 | Yes, across only 20 parent stories |
| **Work items total** | **250** | 109 done, 141 open |

```
project = UIFPE ORDER BY key ASC
```

The parent-child linking is **correct**: zero level-0 items are unparented, and zero skip the epic
level. The single hierarchy violation is `UIFPE-146`, a Sub-task attached directly to a Capability,
and it is already Cancelled. The defect is not tagging — it is that 45 epics were created as
classification containers that can never hold work.

## 1. Six populations of epic, only one of which is a work breakdown

The 79 epics were created for six different purposes:

| Family | Epics | Items held | Empty |
|---|---|---|---|
| **Delivery** — Echo, Beacon, Apicurio, platform | 16 | **141 (70%)** | 1 |
| Capability Foundation — one per capability | 14 | 25 | 5 |
| Domain — one per domain | 14 | 25 | 9 |
| WS workstream — the plan layer | 17 | **1** | 16 |
| phase-0 architecture — original scaffolding | 8 | 6 | 3 |
| DS tracks — page mirror, retired 2026-09-09 | 10 | 0 | 10 |

Four of the six families are **classification schemes**, not work breakdowns. Capability, domain and
workstream are orthogonal attributes of the same work, and Jira permits one parent per issue — so
work lands in the delivery epic where the engineer is operating and the other containers stay
permanently empty. They are not awaiting decomposition; they cannot be filled without duplicating
work.

[`jira/jira-workflow.md`](jira/jira-workflow.md) already states the rule for one of these
attributes — a delivery issue has exactly one owning parent, its Domain epic, and *capability is
always a label, never a parent*. The same rule appears in [`README.md`](README.md) and
[`jira/backlog-seed.md`](jira/backlog-seed.md). Domain and workstream are the same kind of thing as
capability — attributes of work — and were modelled as epics anyway.

**Concentration.** The top 5 epics hold 51% of all level-0 items; the top 10 hold 71%.
`UIFPE-166` alone holds 38.

## 2. No schedule can be derived from the data

A schedule requires scope, size, sequence, capacity and dates. Coverage across the 250 work items:

| Ingredient | Present |
|---|---|
| Size estimate (`Story Points` or `Story point estimate`) | **2 of 250** |
| Ever assigned to a sprint | **19 of 250** |
| `Team` field set | **0 of 250** |
| Release / `fixVersion` anywhere in the project | **0** |
| Dependency links on the 16 delivery epics | **0** |
| Written description on the 16 delivery epics | **4 of 16** |
| Start date on the 16 delivery epics | **0** |
| Due date on the 16 delivery epics | **1**, and it reads `2023-09-22` |

```
project = UIFPE AND issuetype not in (Capability, Epic) AND ("Story Points" is not EMPTY OR "Story point estimate" is not EMPTY)
project = UIFPE AND issuetype not in (Capability, Epic) AND Sprint is not EMPTY
project = UIFPE AND fixVersion is not EMPTY
```

**Consequence.** Every date currently quoted for UIF was reasoned backwards from a target rather
than rolled up from work. That is the only thing this data supports; it is not a reporting failure by
whoever produced those dates.

**An empirical basis does exist, but not in Jira — see section 8.** The figures previously quoted
for it (Product 15 days, Program 13 days, "roughly a fortnight per domain") came from the
delivery-sequence board's own record. They have now been checked against commit history and **both
are wrong**: Program took 9 days and Product has taken 35 and is still open. Do not use the
fortnight figure; use section 8.

## 3. The dependency graph models only empty containers

A correct, well-formed dependency graph exists across 12 nodes: `UIFPE-182` (WS-11 schema registry)
blocks WS-08, WS-09, WS-10 and WS-15; `UIFPE-177` (WS-03) blocks WS-09; WS-05/06/07 block WS-24
(ETL exit); WS-10 blocks WS-22 (MFT exit).

**Every node in it holds zero work items.** None of the 16 delivery epics — the 141 items that can
actually slip — carries a single dependency link. The critical path is modelled across containers
that cannot move.

`UIFPE-189` (WS-23 ESB exit) has no inbound blocker recorded at all, although `CLAUDE.md` states
that ESB, ETL and SSDP exit at the same gate.

## 4. No external commitment is tracked

```
project = UIFPE AND issuetype in (Capability, Epic) AND issueLinkType is not EMPTY
```

22 containers carry links. **All 22 point inside `UIFPE`. There are zero cross-project links.**

UIF depends on at least nine commitments owned outside the programme, every one of which exists
only as prose in `PLANNING.md` or on the programme page:

1. Platinum programme consuming Product, Pricing, Stock, Program and Event data
2. Data team — Order domain data model and contract definition
3. Data team — Customer domain data model and contract definition
4. IBM QE resource onboarding (expected 30 Sep 2026)
5. IBM SRE resource onboarding — gates all observability work
6. Autobahn event and data streaming substrate (WS-03)
7. MuleSoft Anypoint and API Portal (WS-04)
8. New Relic and OpenTelemetry (WS-02)
9. The four decommissioning hand-offs — MFT, ESB, ETL, SSDP (WS-22 … WS-25)

None can be aged, escalated or reported on. If a counterparty slips, nothing in Jira changes state.
There is no Platinum project visible on this Jira site, so a genuine cross-project link is not
available — these have to be held as `UIFPE`-side records.

**The four exit workstreams are the largest of these and hold no work.** They are where the
1,220 → 435 integration reduction is realised. *Reduction figures are from the Pearson Enterprise
Blueprint, not from this repository.*

## 5. Delivery is concentrated on one person

| Person | Open items | Clusters spanned |
|---|---|---|
| Satyanarayana Gunavanthan | **60 of 141 (43%)** | **all 5** |
| Vaibhav Yenorkar | 22 | 2 |
| Reyas Ahamed Habib Rahuman | 13 | 3 |
| Karthik Pandiyan | 12 | 3 |
| Srini N Narayanaswamy | 11 | 2 |
| Amit Aggarwal | 8 | 1 |
| Abdul Jailani | 6 | 1 |
| Venkata Routhu | 6 | 2 |
| `serv.GIPBBCICDUser` | 3 | 3 |

Work clusters naturally into three squads, but only two can be staffed today:

| Cluster | Open | Team | Viable? |
|---|---|---|---|
| Echo — Transform, Replicate, UI, Platform API | 35 | Reyas, Vaibhav, Amit | Yes |
| Beacon — partner exports | 19 | Karthik, Abdul | Yes — only cluster with proven throughput |
| Apicurio — schema registry | 27 | Satya alone | **No** |
| Foundation work | 46 | unowned | **No** |
| Domain builds — Price, Stock | 12 | Reyas, Srini | Partial |

**Apicurio is both the critical path and the least staffed.** It is the schema registry that
`UIFPE-182` uses to block four workstreams, all 27 items are open and assigned to one person who
also carries work in every other cluster. `UIFPE-182`'s only child is assigned to an engineer in the
Beacon cluster.

**46 items belong to no squad** — Design Foundation 9, Cloud Platform Foundation 7, Event Foundation
6, Delivery Foundation 5, Schema Registry Foundation 4, plus `UIFPE-3` (6) and `UIFPE-88` (1). This
is how the concentration formed: unowned work drifts to whoever will take it.

## 6. Data defects

| Defect | Detail |
|---|---|
| **2023 dates** | Verified on `UIFPE-3`, `UIFPE-4`, `UIFPE-84`, `UIFPE-96`, `UIFPE-129`, `UIFPE-238`, `UIFPE-241`. `UIFPE-96` is In Progress with 21 children. A year typo repeated across records, making them permanently overdue. `UIFPE-24` was corrected on 2026-09-09. |
| **Service account holds live work** | `serv.GIPBBCICDUser` owns 3 open items and epic `UIFPE-35`. One, `UIFPE-353` (Price Gold), sits in the 30 October scope. It authors no commits — see section 7 before acting on the account itself. |
| **Sprint not closed** | "UIFPE Sprint 2" ended `2026-09-07` and still reports as active. |
| **No bug lineage** | All 21 Bugs are parented to epics as siblings of stories, so no defect traces to the work that caused it. |
| **Critical defect awaiting review** | `UIFPE-242` — Silver layer does not meet the Medallion architecture standard — is in Code Review, not blocked. Reviewers are available. |
| **Undescribed containers** | 12 of the 16 epics holding 70% of the work have an empty description field. |
| **Category errors** | `UIFPE-77` (a POC), `UIFPE-94`, `UIFPE-95` (a backlog bucket), `UIFPE-145`, `UIFPE-244`, `UIFPE-245` are typed Capability but are not capabilities. |

```
project = UIFPE AND (duedate < 2024-01-01 OR "Start date" < 2024-01-01)
```

**Provenance of the category errors — cloning.** `UIFPE-94` clones `UIFPE-77`; `UIFPE-95` clones
`UIFPE-94`; `UIFPE-245` clones `UIFPE-145`; and `UIFPE-244` ("RELAY - Echo") clones `UIFPE-13`
("Semantics") — which is where its incorrect `capability:semantics` label came from. Cloning a
Capability to create a new one propagates both the type error and the labels. This is a process
defect and will recur until the practice stops.

## 7. The identity gates disagree with each other

Commits are policed by **two independent layers with different definitions of a valid identity**, and
a commit can satisfy one while failing the other silently.

**Layer 1 — enterprise rulesets.** Two rulesets, `allowable_authors` (id 3748714) and
`allowable_committers` (id 3490601), both `enforcement: active`, both inherited from
`Enterprise:pearson` rather than defined in this repository. They apply to `~ALL` refs, excluding only
`refs/heads/copilot/**`, and match author and committer email against an approved-domain pattern
(`pearson.com`, `pearsondev.com`, `peroot.com`, `pearsoned.com`, `pearsoncanada.com`, `ged.com`,
`mondly.com`, plus GitHub, GitLab and Snyk bot addresses).

**Layer 2 — repository status checks.** Author email matches a Jira user · Every commit carries a
Jira key and a comment · Title and body are reviewable.

Layer 1 validates the **domain**. Layer 2 validates **resolution to a Jira user**. These are not the
same test, and the gap between them is live:

> `reyasahamed.habibrahman+pearson@pearson.com` authored **62 of the 541 commits on `develop`**.
> The enterprise pattern accepts it, because the pattern anchors on the domain and ignores the local
> part. Jira does not resolve plus-addressed email, so every one of those 62 Smart Commits was
> silently discarded — the commits landed, the Jira links never formed, and no gate reported a
> failure.

`VALIDATE_TODO.md` records this as 13 commits and leaves the cause open. The cause is the layering
above, and the count is **62**, not 13.

**Protection is identical across all three shared branches.** `develop`, `validate` and `main` carry
the same three status checks, the same two enterprise rulesets, `enforce_admins: true`, force-push
and deletion blocked, `strict: false` — and **0 required approving reviews**. A change can reach any
of the three with checks green and no human review. There is no `validate`-specific ruleset;
`validate` is not governed more strictly than `develop` despite its name.

**The service account is not a git problem.** `serv.GIPBBCICDUser` has authored **zero commits in
the entire history** — it is not a git identity in this repository. It is a Jira assignee on
`UIFPE-215`, `UIFPE-241`, `UIFPE-353` and epic `UIFPE-35`, and `jira-transition.yml` authenticates
to Jira with `JIRA_USER_EMAIL` / `JIRA_API_TOKEN` secrets that plausibly belong to it. The two roles
must be handled separately: reassign the four records to humans, and leave the automation identity
alone — disabling it would break Smart Commit transitions with no obvious cause.

### Second instance: the Smart Commit gate versus imported history

The same failure mode blocks the largest single piece of work in the repository.

**`PR #206` (`UIFPE-355`) carries the complete Echo stack — 455 files, +64,086 lines, 25 Terraform
files — and cannot merge.** It is MERGEABLE but BLOCKED, failing exactly one check: *"Every commit
carries a Jira key and a comment"*.

**28 of its 59 commits fail, and all 28 are imported history.** The branch brought `uif-relay-echo`
in via subtree with history preserved. The 31 commits authored *for* the migration all pass, carrying
`UIFPE-355 #comment …`. The failures are the source repository's own commits — "Initial commit:
Enterprise Data Connect (UIF Relay Echo)", "Add root README", "Fix silver bq_native merge performance
and same-day watermark gap". They predate UIF's conventions and were never subject to them.

**They cannot be made to pass.** Adding Jira keys to another repository's history means rewriting it,
which destroys the history the import existed to preserve. The author recorded the problem at the
time — one commit reads *"Record the commit-lint consequence in ADR-0015 as decision …"* — and
ADR-0015 on that branch documents the move.

So the gate is not wrong about its rule; it is being applied to a case the rule was never written
for. Same shape as the plus-addressed email above: the check is enforced literally, the outcome is
silent in one case and blocking in the other, and in neither does the message explain the real cause.
Raised as **`UIFPE-410`, Critical**.

**Remediation for this section**

1. Tighten "Author email matches a Jira user" to reject plus-addressed local parts explicitly
   instead of relying on the domain pattern to catch them. The enterprise rulesets are inherited and
   cannot be changed here, so the fix has to sit in the repository layer.
2. **Decide how the Smart Commit gate treats imported history**, and write the decision down so the
   next subtree import does not hit the same wall. Options: scope the check to commits authored after
   an import boundary, exempt merge and subtree commits, squash-merge so only the pull-request title
   is linted, or permit a recorded one-off override. This is a change to a required check and needs
   framework-owner sign-off. It is currently holding 64,086 lines of working platform out of
   `develop`.
3. Decide the 62 historical commits: accept the traceability gap, or re-author them — which rewrites
   shared history, changes hashes and needs the author's and team's sign-off. Open since
   `VALIDATE_TODO.md` was written.
4. Reassign the four service-account records, `UIFPE-353` first since it sits in the October scope.
5. Confirm whether **0 required approving reviews** on `validate` and `main` is intended.

## 8. The evidence base is in git, not Jira

Sections 2 and 3 establish that no schedule can be derived from Jira. That is true, and it is not the
whole picture: **the commit history holds the dates, the throughput and the real ownership that Jira
is missing.** 542 commits on `develop` reference 94 distinct Jira issues, giving 90 of the 250 work
items a real activity window.

Use this as the estimating and reporting basis until Jira carries dates of its own.

### Real delivery windows, measured from commits

| Epic | Commits | Items | First → last | Days | Jira status |
|---|---|---|---|---|---|
| `UIFPE-96` Transform framework | 82 | 19 | 09 Aug → 09 Sep | 31 | In Progress |
| `UIFPE-166` Beacon Phase 2 | 73 | 24 | 20 Aug → 09 Sep | 20 | In Progress |
| `UIFPE-51` Domain: Product | 42 | 12 | 06 Aug → 10 Sep | **35** | In Progress |
| `UIFPE-184` WS-09 events | 42 | 1 | 28 Aug → 07 Sep | 10 | Ready for QA |
| `UIFPE-112` Beacon Phase 1 | 42 | 8 | 20 Aug → 03 Sep | 14 | Done |
| `UIFPE-248` Silver→Gold runtime | 18 | 8 | 11 Aug → 07 Sep | 27 | In Progress |
| `UIFPE-116` Domain: Program | 15 | 5 | 11 Aug → 20 Aug | **9** | Done |
| `UIFPE-234` Domain: Price | 7 | 2 | 06 Sep → 10 Sep | 4 | In Progress |

### Corrected domain build times

**Program: 9 days** (11–20 Aug), 15 commits, one engineer. **Product: 35 days** (6 Aug – 10 Sep),
42 commits, still open. The two are not comparable — Product was built while the framework it depends
on was also being built. Program is the only clean measurement of a domain build against an existing
framework, and it is 9 days with one engineer.

### Price and Stock have started

Earlier readings of this project — including the leadership page — stated that Price and Stock had no
build scheduled. **That is false.** Price has 7 commits from 6 September and was active on
10 September; Stock has 2 commits from 5 September. Jira shows 2 items and 1 item because the work is
not being decomposed, not because it is not happening. Both are **100% authored by one engineer**.

### The delivery bottleneck is not the person Jira points at

Section 5 reports that one person holds 43% of open Jira work. Commit history shows a different
constraint: **Reyas Ahamed Habib Rahuman has authored 201 of 542 commits — 37% of everything** (139
under his correct address plus the 62 plus-addressed ones from section 7). He is the sole contributor
to Program, Price and Stock, and co-leads Product and the Transform framework.

Both concentrations are real and they are different. One holds the assignments; the other ships the
code. **For the October date, the git concentration is the binding one**, and any squad plan drawn
from Jira assignment data alone will not relieve it.

### What reconciled cleanly

- **Status lag is 3 issues**, not the widespread drift that might be assumed: `UIFPE-184`,
  `UIFPE-259`, `UIFPE-222`. `UIFPE-184`'s lag was introduced by the 2026-09-09 eventing
  reconciliation itself, not by the team, and has been reverted.
- **Throughput is accelerating.** Weekly commits: 38, 22, 92, 48, 69, 103, **106**, 64 (partial
  week). The last full week was the highest in the programme's history.
- **The section 7 blast radius is narrower than the commit count implies.** Only **5 issues** had
  their entire history authored with the plus-addressed email — `UIFPE-60`, `61`, `117`, `118`, `119`,
  all already Done. The 62-commit figure is accurate; most of those commits had correctly-addressed
  siblings, so the issue-level loss is 5, not 62.

### Gaps this exposes

- **160 of 250 work items have never had a commit**, and **37 of 109 Done items have none**.
  Legitimate for design, decision and documentation work; worth a spot check for anything that
  claims shipped code.
- **`UIFPE-184`'s 42 commits all reference the epic key directly**, never a child story. The work is
  therefore invisible at story level and cannot be sprinted or burned down. Whether other epics
  share this pattern has not been checked.
- **201 of 542 commits carry no Jira key at all** — these are GitHub merge commits, which
  legitimately have none. Not a defect, recorded to prevent it being counted as one.

### Method and limits

Derived from `git log origin/develop` with Jira keys extracted from commit subjects, joined against
a Jira snapshot taken on 2026-09-10. Limits: it measures **commit dates, not effort**; a first commit
is not a start date, since design and analysis precede it; merge commits inflate per-issue counts
where a branch was merged more than once; and work done on branches never merged to `develop` is
invisible. Treat the windows as *observed activity spans*, which is still considerably better than
the nothing Jira offers.

## 9. Architecture components with no work behind them

**There is no system-level architecture diagram in this repository.** What exists is 11 scattered
mermaid blocks and 3 eventing sequence PNGs, weighted heavily toward Gold and eventing. The
authoritative set — six figures covering the Enterprise Blueprint, the UIF logical architecture, the
RELAY logical design, eventing end to end, the physical AWS view with the security rail, and the
GoldenGate CDC backbone — is held outside the repository and was supplied on 2026-09-10. **Anyone
reasoning about scope from this repository alone will infer the architecture and get it wrong**; that
is how the go-live readiness gap in section 8 went unnoticed for so long.

Mapping those figures against Jira gives the coverage below.

| Architecture layer | Jira work items |
|---|---|
| Data Abstraction — medallion, PySpark, Bronze/Silver/Gold | **~50** — strongest area |
| Self-service: Echo | 60 |
| Self-service: Beacon (export) | 51 |
| Eventing — EventBridge routing, archive and replay | ~45 |
| API & MCP layer | ~10 |
| Systems of Record → GoldenGate CDC | 3 |
| RELAY replicate — Micro / Realtime | 6 / **0** |
| Semantic layer, Knowledge Graph, Digital Twin | **1** |
| **Self-service: Harbour (import)** | **1** |
| **SQS, Kinesis, Autobahn publishing** | **0 each** |
| **Single front door — MuleSoft Omni / MCP / API Gateway, API and MCP Catalog** | **0** |
| **Agentic layer — CrewAI, Agentforce, LangChain, IBM AI Control Plane** | **0** |
| **Data governance — Collibra, quality, lineage, metadata catalog, discovery** | **0** |

### The sharpest gaps

**Harbour is half of RELAY and was unplanned.** Beacon exports, Harbour imports. Fig 5 specifies
Harbour in full — Transformation, AWS Transfer Family, Mapping Templates, Step Functions, and four
inbound connectors. Jira held `UIFPE-245` (an empty Capability) and one architecture story, against
Beacon's 51 items and Echo's 60. **An earlier reading in this file's history called self-service
"well covered" — that was true of Echo and Beacon and wrong about import.**

**SQS is specified everywhere and provisioned nowhere.** It appears in three of the six figures,
including its own `Queue Management` box in the RELAY logical design, and across five ADRs
(0001, 0007, 0014, 0015, 0017). Zero work items, while the 30 October release claims Event readiness
and SQS is the AWS-native delivery path.

**The security rail is a chosen toolchain with nothing scheduled.** Fig 5 names Snyk, AWS WAF,
Armor Code, Keeper Security, CEDAR, Auth0 and Azure AD. **Five of those had zero work items.** And
`UIFPE-33 Security Foundation`, the Foundation Epic for one of the thirteen capabilities, held
**zero children**: all 12 security items sat as stories under Apicurio and Echo epics, so security
existed only where a stack happened to need it.

**Corrected 2026-09-11 — the scanning figures in this section were computed wrongly.** The scan that
produced them excluded directories matching `".git"`, and `".git"` is a substring of `".github"`, so
it silently skipped the entire `.github/` tree — which is precisely where scanning is configured. The
counts here were 23 references across 9 files; corrected, they are **30 across 10**. Two things
follow, and they pull in opposite directions.

**More exists than was reported.** `.github/dependabot.yml` is configured and this section missed it
entirely. So dependency scanning is not absent.

**But its coverage is far narrower than "Dependabot is configured" suggests.** It watches exactly two
ecosystems: `github-actions` at the repository root, and `pip` at
`/domains/product/src/events/src` — **one Python path in the whole monorepo.** There is no `npm`
entry at all, against roughly 190 frontend files across Echo, Beacon's UI and Product's UI; and no
`pip` entry for any domain other than that single Product events path.

**And the CI scan steps are placeholders, not scanning.** `domain-ci.yml` and `stacks-ci.yml` each
carry a step named `Build / test / scan`, and each is preceded by a comment saying it is a
*"Placeholder for the domain's real build/test/scan"*. Nothing is scanned there today.

So the original conclusion stands — **`UIFPE-403` (Snyk across the domains) is still needed in full**
— but for a sharper reason than was first given: it is not that no scanning exists, it is that what
exists covers one path and one ecosystem, and the CI hooks meant to carry the rest are empty. The
other statement in this section, that `UIFPE-326` (ruff and Trivy) is scoped to the Apicurio stack
alone, is unaffected and remains true.

**Two other figures in this section drifted for a second, unrelated reason** — `develop` moved during
the session as other people's work merged. Glue is **818 references across 92 files** (was 772/83, of
which 33 in 8 `.github` files); SQS **167/25**; S3 hardening **10/3**; self-service **41/17**. None
changes a conclusion.

**S3 hardening had 10 references in 3 Terraform files and no work items** — for the layer every
domain in the October release stores its data in.

**PGP encryption is an explicit pipeline stage with no work.** Fig 3 shows two Step Functions —
"Scan, PGP [De]cryption, Unzip" and "PGP [En]cryption, zip". Partner file exchange requires it.

**`WS-17` has no Jira epic at all** — the analytics consolidation wave, recorded in `PLANNING.md` as
the largest single wave by integration count before Q6.

### A divergence between the diagrams and the code

The figures name **PySpark, Step Functions, Lambda, ECS Fargate and EKS** as the compute runtime.
**AWS Glue appears in none of them** — yet this repository carries 818 Glue references across 92
files, and `UIFPE-209` explicitly retired PySpark-on-Fargate *in favour of* Glue per ADR-0009. The
diagrams are behind the implementation. This is not a work gap; it is a reference document that will
mislead the next person to plan from it.

### What was created in response

Thirty-two items on 2026-09-10, all queryable:

* **`labels = "r0-readiness"`** — 16 go-live readiness items (section 8's gap) plus 9 more covering
  S3 hardening, SQS provisioning and the security toolchain. Homed on `UIFPE-33`, `UIFPE-32`,
  `UIFPE-34`, `UIFPE-24` and the four domain epics. Thirteen are deliberately **unassigned** so the
  resourcing gap stays visible rather than inflating one person's load.
* **`labels = "roadmap:release-milestones"`** — `UIFPE-392`…`397`, the R0–R5 records, each with real
  start and due dates and each marked as a record that will never hold children.
* **`UIFPE-407`** — Harbour, targeted at R1.

**Deliberately not created:** Gateway and Catalog, Kinesis, Autobahn publishing, the agentic layer,
Collibra and data governance. All are R1 or later and belong as scoped epics rather than thin
stories. They are recorded here so they surface as decisions rather than surprises.

## 10. What is actually provisioned — read from Terraform, not Jira

**Sections 1–9 were built from Jira and from keyword scans. This section is built from Terraform and
deploy history, and it corrects several of them.** The pattern that keeps repeating in this file is
the same one recorded in section 8: *absence from Jira is not absence from the platform*. Section 9
said SQS "has zero work items" and S3 hardening "appears nowhere" — both understated what is built,
because both were counted from Jira and from `grep`, not from infrastructure code.

### Infrastructure by owner

| | Terraform resources | App code | Schemas | QE tests | Ever deployed |
|---|---|---|---|---|---|
| `domains/product` | **121** in 16 files | 128 | 19 | 40 | **never** |
| `stacks/beacon` | **68** in 15 files | 85 | 1 | 36 | **yes — 4 Dev runs** |
| `stacks/apicurio` | 19 in 8 files | 0 | 0 | 0 | never |
| `domains/price` | **0** | 5 | 7 | 5 | impossible |
| `domains/program` | **0** | 5 | 7 | 5 | impossible |
| `domains/stock` | **0** | 4 | 7 | 4 | impossible |
| `stacks/echo` | **0** | **0** | **0** | **0** | nothing exists |
| `stacks/harbor` | **0** | **0** | 0 | 0 | nothing exists |
| the other nine domains | 0 | 0 | 0–1 | 0 | not started |

### Infrastructure as code did happen — for two units

`domains/product` carries **121 Terraform resources**: 3 `aws_glue_job`, **5 `aws_sqs_queue` with 4
queue policies**, 3 `aws_kinesis_stream`, 12 `aws_cloudwatch_event_rule` with 12 targets, 8 Lambda
functions, 7 DynamoDB tables and 3 CloudWatch alarms. **Glue, SQS, Kinesis and EventBridge are all
running in Development.** Section 9's claim that SQS and Kinesis had nothing behind them was wrong at
the infrastructure level; what is true is that they exist for Product only.

`stacks/beacon` carries 68 resources and **already holds the S3 hardening pattern** — 2
`aws_s3_bucket_public_access_block`, 1 `aws_s3_bucket_versioning`, 1
`aws_s3_bucket_server_side_encryption_configuration`, 1 `aws_s3_bucket_lifecycle_configuration` and
2 `aws_lakeformation_permissions`.

### Three of the four go-live domains have no infrastructure code at all

**Price, Program and Stock have schemas, application code and QE tests — and zero Terraform.** They
cannot be deployed to any environment, because nothing provisions them. Raised as **`UIFPE-409`,
Critical**, and it displaces the promotion path as the top blocker for 30 October: a promotion path
has nothing to promote until this exists.

### CD is not in place — four deploy runs exist in total

Every one is **`Deploy beacon — Dev`**, all on 2026-09-10. Product has `deploy-product-uat.yml` and
`deploy-product-prod.yml` and **neither has ever executed**. So continuous delivery is proven for one
stack, in one environment, and nowhere else.

The cause is recorded on `UIFPE-378`: **`domain-cd.yml` implements a promotion path nobody uses.** It
declares `<domain>-dev`, `-qa`, `-stage` and `-production` and contains **zero references to UAT**,
while the real chain is **Development → UAT → Production**. Because the shared workflow could not
express the real path, Product's UAT and Production workflows were hand-rolled and the other three
domains were never done. `CLAUDE.md` line 30 documents the unused four-stage version and is wrong on
both counts.

### Application monitoring is not automated

Three `aws_cloudwatch_metric_alarm` in Product, 4 log groups in Beacon, 1 log metric filter. **No
`aws_sns_topic` anywhere in the repository**, so the alarms that do exist have nowhere to send.
No dashboards. `UIFPE-379`, `380` and `381` cover this; an SNS topic is the prerequisite none of them
currently name.

### Absent from every Terraform file in the repository

| Service | Consequence |
|---|---|
| **`aws_kms_*` — zero** | Every bucket, queue and table across Product, Beacon and Apicurio uses AWS-managed keys. No customer-managed encryption anywhere. |
| **`aws_wafv2_*` — zero** | Four domains publish APIs at go-live with no web application firewall. |
| **`aws_transfer_*` — zero** | Harbour's Transfer Family endpoint does not exist. |
| **`aws_sns_topic` — zero** | Alarms cannot notify anyone. |
| `aws_sfn_state_machine` — **1** | Fig 3 specifies **seven** Step Functions for RELAY. One exists. |

**`aws_s3_bucket` in `domains/product` — zero.** Product declares 5 `aws_s3_object` but no bucket, so
**the Bronze, Silver and Gold buckets appear to be provisioned outside this repository.** Until that
is established, no S3 control can be enforced from here. It is the first question on `UIFPE-398`.

### Echo is built, and blocked by a CI gate it cannot pass

**Corrected 2026-09-10.** An earlier draft of this section said Echo had zero files and was "the
most-planned component and the least-built one". That was wrong, and it is the clearest example in
this file of the error the section opens by warning about: `stacks/echo/` on `develop`, `validate`
and `main` does hold only eight placeholder READMEs — but **the work exists on an open pull
request.**

**`PR #206` (`UIFPE-355`, opened 2026-09-09) carries a complete Echo stack: 455 files, +64,086
lines.**

| Content | Files |
|---|---|
| Frontend | 192 |
| Tests | 142 |
| Python | 41 |
| **Terraform** | **25** |
| Schemas | 7 |
| Deploy scripts | 7 |
| Docs | 12 |

The Terraform is production-shaped, not scaffold: `apigateway.tf`, `cloudfront.tf`, `cloudwatch.tf`,
`data_quality.tf`, `dynamodb.tf`, `glue.tf`, `iam.tf`, `lakeformation.tf`, `lambda.tf`, `s3.tf`,
`scheduler.tf`, `vpc.tf`, `bootstrap/main.tf`, plus providers, locals, outputs and variables.

**The blocker is one required check.** `PR #206` is MERGEABLE but BLOCKED, failing only *"Every
commit carries a Jira key and a comment"* — see section 7, where this is recorded as the second
instance of a gate whose rule does not fit the case it polices. Raised as **`UIFPE-410`, Critical**.

**Two things the PR does not fix.** Its `s3.tf` has zero references to `kms`,
`server_side_encryption`, `public_access_block`, `versioning` or `lifecycle`, and declares no
`aws_s3_bucket` — so `UIFPE-398` stands in full and the KMS gap spans Echo too. And `UIFPE-244`
"RELAY - Echo" still carries the incorrect `capability:semantics` label inherited from its clone
source.

**It also settles the environment question independently.** The PR's Terraform backends are
`dev.hcl`, `dev-uif.hcl`, `uat.hcl` and `prod.hcl` with matching tfvars, and one of its commits reads
*"Remove the staging environment - it does not exist"*. That is third-party evidence for
**Development → UAT → Production**, against the four-stage chain still documented in 51 files.

### The delivery order this implies

Each gate is blocked by the one above it:

1. **Provision** — `UIFPE-409` Terraform for three domains; `UIFPE-376`/`377` UAT and Production
   environments; establish medallion bucket ownership.
2. **Deliver** — `UIFPE-378` correct `domain-cd.yml` to Development → UAT → Production, then extend
   to four domains; run Product's untested UAT deploy once.
3. **Prove** — `UIFPE-374`/`375` regression pack and CI automation; `UIFPE-385` Stock decomposition;
   `UIFPE-382`–`385` UAT sign-off.
4. **Operate** — SNS topic, then `UIFPE-379`/`380`/`381`; then KMS, Snyk and WAF
   (`UIFPE-398`/`403`/`404`).

## Workstream mapping — what is correct

Two findings here contradict earlier assumptions and should not be "fixed":

- **`WS-18` legitimately spans two domains** — Product and Price — per `PLANNING.md`. `WS-19`
  legitimately spans Orders and Subscriptions. Both correctly carry the same label on two epics. A
  workstream rollup must sum across multiple domain epics rather than assume one-to-one.
- **The Domain epic layer is the one fully-modelled part of the project.** All 12 labelled domain
  epics map correctly to their `PLANNING.md` workstream; `UIFPE-116` (Program) is deliberately
  exempt with `program:building-block`; `UIFPE-150` (Income) is cancelled. Nothing to fix.

`WS-12`, `WS-13`, `WS-16` and `WS-26`…`WS-30` exist **only as labels on Domain epics** — there is no
epic named `WS-12`. The workstream numbering is therefore split across two epic families, which any
rollup has to account for.

## Remediation, in order of effect

Each item depends only on those above it.

1. **Agree the container rule.** Every level-0 item's parent is a Domain epic, a **Stack epic**, or a
   Capability Foundation epic. Capability, workstream, domain, stack and roadmap phase are labels,
   never parents. WS epics are plan records and are expected to hold zero children.

   *This amends a documented standard and needs framework-owner sign-off.*
   `jira/jira-workflow.md` and `README.md` currently define **two** owning-parent kinds — Domain
   epic for work that ships in a domain, Foundation Epic for pre-domain platform groundwork. Neither
   admits a stack. But `CLAUDE.md` states that `echo`, `beacon` and `harbor` are deployable stacks
   and *never* domains, and they are not among the thirteen capabilities — so 15 of the 16 delivery
   epics, holding 140 work items, have no legitimate owning parent under the rule as written. Adding
   a third kind plus a `stack:` label closes that gap; the alternative is to treat stack work as
   Foundation groundwork indefinitely, which misstates what it is.
2. **Unload the concentration.** Move the 27 Apicurio items off a single owner to engineers already
   working adjacent Foundation items. This relieves the bottleneck and staffs the critical path in
   one action. Highest value available.
3. **Define releases.** *Partly done 2026-09-10.* `R0`…`R5` now exist as milestone records
   `UIFPE-392`…`397`, labelled `roadmap:release-milestones`, carrying the quarter dates from
   `PLANNING.md` — so the release model is visible and draws on a timeline. **Still outstanding:**
   the same six as real Jira **Versions**, so `fixVersion` can be stamped and a release burndown
   drawn. Versions cannot be created through the issue API and need a project admin. Until they
   exist, "what ships on 30 October" is answerable by the `r0-readiness` label but not by release.
4. **Label the 16 delivery epics** with the workstream they serve. A one-off pass over 16 records
   makes the plan layer reportable without moving any work item. Four are unresolved — `UIFPE-96`,
   `246`, `247`, `248` — because no workstream in the `WS` grid owns the Gold layer. That may be a
   genuine gap in the grid rather than a mislabelling; 38 items depend on the answer.
5. **Assign the 46 Foundation items** to a named squad, or the concentration in item 2 reforms.
6. **Record the nine external commitments** as tracked records with a counterparty and a date.
7. **Clear the data defects** in section 6, starting with `UIFPE-242` and `UIFPE-353` — both inside
   the October scope and both movable today.
8. **Estimate the October scope only.** Enough to test the date honestly; not all 250 items.

## Open questions

1. Do Domain epics and Stack epics both act as containers? Product already holds 20 items, so the
   domain layer is half-committed to that role while nine domain epics hold nothing. Splitting the
   difference is what produced the current state.
2. Which workstream owns the Gold layer? Blocks item 4 above for 38 work items.
3. Should the WS epics be re-dated? **No** — they cover full programme scope (`UIFPE-176` runs to
   2027-04-30, `UIFPE-183` to 2028-01-31). The near-term board dates are a slice of the same work,
   not the same object. Recorded here because the opposite has been proposed before.
4. *Recorded in Jira 2026-09-10 on `UIFPE-397`, still unfixed in the repository.*
   `CLAUDE.md` states the reduction target lands at "Release 3 Go Live". The release numbering has
   since shifted to `R0`…`R5` with Release 0 inserted at Q1, so that reference needs amending.
