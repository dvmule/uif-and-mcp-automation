# Gold Pipeline Wizard — UX Design

This document specifies how a user sets up a gold pipeline end to end through the Transform UI: pick the gold table, register its Silver sources, map every column, schedule it, and submit for review — without filling an Excel workbook and without waiting on an engineer to write SQL for complex columns. It is the experience-side companion to [ADR-0008](../../architecture-design/decisions/0008-derivation-chains.md) (revised 2026-08-26: a complex column's contract is the user's own query, governed) and refines the wizard shipped under UIFPE-101/106/108 rather than replacing it.

**Scope and placement:** this design is platform-scope — the wizard serves every gold domain (product and program today; price and stock as they arm), so the design lives here with the other gold control-plane standards. The wizard's *code* home is `stacks/echo/transform-ui/` since UIFPE-358 phase 2 (ADR-0018; it was steward-hosted in `domains/product/ui/` per [gold-control-plane.md](gold-control-plane.md), UIFPE-122).

**Framework owner direction this design encodes (2026-08-26):** pipeline setup happens through the UI, not through templates; complex logic is captured as the user's query; the UI shows no framework vocabulary.

## Who this is for

The author is a **source-aware operator** — the same person who fills the intake mapping workbook today. They know the Silver tables, their columns, and what the filters on them mean. They do not know (and must never need to know) UIF-internal concepts: CDC, grains, CTEs, classification classes, EAV-as-a-pattern-name.

**The wizard is the live replacement for the Excel intake workbook.** Same questions, same mental model, same person — but validated as you type, with the generated SQL visible, a test run against dev data, and submit-for-review built in. If a screen asks something the workbook never asked, that is the signal it should have been defaulted or hidden.

## Vocabulary rules (hard requirement)

The UI speaks exactly two languages: **the source's own names** and **plain outcomes**.

| Shown in the UI | Never shown |
|---|---|
| Kinds by their established names: **Scalar · Conditional · Nested · Derived (SQL)** | any framework-invented kind synonyms |
| Real Silver table and column names, filters exactly as the user writes them | "EAV", "attribute-group pattern", friendly renaming layers |
| **Primary table** | "driving table" |
| Channels: **event / message / api** | "tiers", "concentric subsets" |
| Outcome sentences: "Updates automatically after approval" / "A data engineer reviews this change" | AUTO/PLAN, classify class strings |
| "When a row changes in this table, which \<entity\> does it affect?" (defaulted) | "key mapping", "CDC" |
| "The query" (shown in previews and diffs) | "CTE", "splice", "compile" |
| "One row per \<entity\>" (test-run result) | "grain" |
| "Notify consumers about backfilled values?" | "backfill mode silent/emit" |

## One journey (added 2026-08-27, UIFPE-219)

There is **one wizard**. Every way in is an entry point to the same six steps,
never a parallel flow with its own steps and its own editors:

| Entry point | What it seeds | Where it lands |
|---|---|---|
| Find an attribute → Modify | the registered column, its entity, its schedule | step 3 (Columns), everything filled in |
| Start a transformation (guided) | a blank task | step 1 (Gold table) |
| Import a mapping file | the file's entity + columns | step 3 (Columns), for review |
| Describe it (AI draft) | the drafted columns on the registered entity | step 3 (Columns), for review |
| New gold table | a new-table task (name, primary table, key to fill) | step 1 (Gold table) |
| Resume | the saved draft, wherever it stopped | where it stopped |

The method choice (guided / import / AI) is an **entry decision on the home
page**, not a wizard step — once inside, every task looks the same. The
earlier separate schema-first setup flow is retired: its one unique
capability — previewing and downloading the generated artifacts for a new
table — lives in the Review step whenever the task creates a table.

**Two rules make one journey safe rather than merely tidy.**

*Re-entry restores; it never resets.* A task in progress is saved locally on
every change, and every way back into the wizard that does not explicitly ask
for a new task — a page refresh, browser Back then Forward, a bookmark whose
attribute has since been renamed — reopens the task in progress. Starting
something new is always an explicit act (the home page's entry buttons), and
nothing is written over a saved task until the user changes something in the
new one. The alternative loses work silently, which is the one failure a
draft feature cannot have.

*A task cannot skip a step it never walked.* Entry points seed a task deep in
the flow (a modify or an import lands on Columns), so completeness is checked
for **every** step, not just the one on screen: the first unfinished step is
named, advancing is blocked, and Review refuses to submit. Without this a
seeded new-table task could reach submission with no key declared, and the
schema it generated would quietly carry an empty key.

Review and Submit present as one step ("Review & submit"): the submission
service (UIFPE-218) made submitting a real action on that step — ticket and
author collected there, the pull request opened from it — and tracking lives
on its own surface (My submissions), so a separate wizard step never earned
its place.

## The flow — six steps

### 1 · Gold table

Select or create the gold domain/table.

- Entity **cards with live status**: live / draft, last run time and result, row count. The user should know what they are touching before they touch it.
- Creating a new gold **table** in an existing domain: in-UI (name check `db.table`, already gated).
- Creating a new **domain**: not in-UI — the card explains it is a framework-owner decision and names the path. The wizard never silently expands the domain set.

### 2 · Sources

Register the Silver tables this gold table reads. **Tables only — no joins here**; join logic varies at column level and is captured per column in step 3.

**The card spec** (one card per source, in the wizard's step 2 and on the standalone Sources view alike):

- The table's **real name**, from the Silver catalog browser or the entity's known sources.
- The **primary table** marked plainly ("decides which rows exist — one changed row affects its own row").
- **How many attributes read it**, with the list one click away — this is what powers the "no attribute reads this yet — remove?" warning.
- **One plain change-impact sentence** per table — *"a change here affects …"* — auto-derived from how the columns attach; expandable detail only for genuinely odd shapes.
- **Standing**, as two distinct facts with two distinct labels — they answer different questions and must never share one pill: *no such table in Silver* (the name resolves to nothing in the catalog — a typo, or a table nobody has ingested, so the value can never arrive) and *not wired in yet* (the table exists but is not a registered runtime source for this entity, so the column ships and fills when the feed is wired in — the contract-first case).
- Freshness and row count, when live data is wired.

Nothing else. No attachment shapes, key-reach meters, per-grain breakdowns, filter chips, or footnote banners — that analysis is column-level and lives behind each column's editor, where the join actually belongs.

### 3 · Columns

One screen, list + editor (merges the earlier "list columns" / "edit rules" split — the list *is* the editor).

- Every column shows name, kind, source summary, channels, and its **outcome chip** ("Updates automatically after approval" / "A data engineer reviews this change") in the list itself, not hidden in a modal.
- **Bulk grid view for scalars** — a 60-column entity must not require 60 modal round-trips. Modal editing remains for conditional/nested/derived.
- Per-column joins: the editor **pre-fills** the obvious join (source key = primary key) and remembers how each table pair joined earlier in the task. The user touches a join only when this column genuinely joins differently — the reason joins are column-level at all.
- Live SQL preview per column for Scalar/Conditional/Nested (exists today): the query fragment forms as the user clicks.

#### The Derived (SQL) mode

For a column the three kind forms cannot express, the user supplies the query — typed, pasted, or AI-drafted into the same box.

The panel states the contract in one sentence: *"Your query must return one row per \<entity\>, including a column that identifies the \<entity\> and a column with this attribute's value."*

1. **Query box** with syntax highlight. AI assist sits beside it: describe the rule in English → a draft query appears **in the box** — what is stored, validated, and reviewed is always the query itself.
2. **Declare the contract**: which output column identifies the entity, which carries the value (defaulted to the column name). Dropdowns populated from the parsed projection.
3. **Validate** (as-you-type): parse OK · single read-only SELECT · only step-2 tables referenced (a missing table offers one-click "add to Sources", which asks that table's step-2 question) · no nondeterministic functions · declared columns present.
4. **Test run** (one click, dev data): row count · "one row per \<entity\>?" check · sample values. Shown before submit; failures explain in plain language ("2 rows returned for the same product — the value would be ambiguous").
5. **What the reviewer will see**: the exact query, wrapped and joined into the full gold query — shown as a read-only preview so the author sees their SQL in place.

Arrays and structs are built inside the query (the panel links one worked example per shape); the query's output row is always the final value.

### 4 · Schedule

- Frequency + start time (the existing eleven cadences), echoed in plain language: *"Runs daily at 06:30 UTC."*
- "Notify consumers about backfilled values?" yes/no.
- Refresh-columns selection for logic changes to existing columns, phrased as *"Recompute these columns for all rows after this change."*

### 5 · Review

A single pre-submit page — the trust anchor of the whole flow:

- **The full gold query as a diff** — exactly what will change in the deployed query, the user's derived SQL visible in place, no renderings or summaries substituted.
- The outcome summary: N columns update automatically after approval; M need a data engineer (each with its plain-language reason).
- Warnings: sources registered in step 2 that **no column reads** ("registered but unused — remove?"), columns without test runs, anything the validators flagged but did not refuse.

### 6 · Submit

- Submission creates the branch and PR automatically (per ADR-0006: the UI writes PRs, never state). The PR body embeds the plain-terms column summary, the refresh decision, and each embedded query in full — read as the change it is.
- **How it works (UIFPE-218).** The wizard builds every string deterministically (`submission.ts`) so the repository's own gates pin them: the PR title/body clear the lint floors (a test pipes the generated text through the real linter), and the commit is **authored by the submitting engineer at their exact Jira address** — never a bot — with the service credited in a trailer, exactly as an AI-assisted commit credits its agent. The service (`lambda_submit`) writes the intake envelope (plus the schema a new table registers first) to an `intake/UIFPE-<n>-<slug>` branch in one commit and opens the PR; a workflow on that branch (`intake-compile.yml`) runs the delta compiler and pushes the generated artifacts, so the compiler stays the single writer of runtime artifacts and the reviewer sees intent and generated diff in one place.
- **Credential** (decided here, parked since UIFPE-101): a fine-grained PAT scoped to the one repository (contents + pull requests) — not a GitHub App, because the identity rule above means the credential never appears as an author, and an App would add a token exchange for the same two scopes. One credential, two homes, rotated together: Secrets Manager for the service, and the Actions secret `INTAKE_COMPILE_TOKEN` for the compile workflow's push — which must not use the workflow's own `GITHUB_TOKEN`, because pushes made with it never trigger other workflows, so the PR's required checks would never run against the compile commit and the submission would sit unmergeable. The service refuses any branch outside its namespace and any file outside `domains/*/src/schemas/`, so the credential's blast radius is fenced in code as well as in scopes.
- **My submissions**: submitted → in review → approved → deployed → first run succeeded/failed, with the reviewer's comments surfaced in the UI. The author must never need GitHub to know where their change stands. Approval itself happens in the PR (reviewers are engineers); the UI mirrors it — merge state from the PR, deploy state from the workflow run carrying the merge, first-run state from the entity's next scheduled run after the merge.

## Cross-cutting rules

- **Refusal-first, inline, early.** Every check that would fail at submit fails at the field where the user typed it. Nobody reaches step 6 to learn their step-3 query reads an unregistered table.
- **Progressive disclosure.** The Derived (SQL) mode appears only when chosen; the three kind forms stay exactly as simple as they are today.
- **Drafts persist.** A half-finished task survives the browser session.
- **Honest promises.** Outcome sentences say what happens next, never the mechanism ("appears on the next scheduled run", not "compiles").

## Build state

| Piece | State |
|---|---|
| The one journey — five steps, every entry point, one wizard | **Done** (UIFPE-219). The separate schema-first setup flow and the Method step are retired; the new-table schema generation they carried lives in Review. |
| Re-entry restores, and no step can be skipped | **Done** (UIFPE-219) — both are unit-pinned, because both failed silently before they were. |
| Sources as tables only, one card per source | **Done** (UIFPE-219). Registration standing shows as its two distinct facts. |
| Vocabulary swept to source-native names + plain outcomes | **Done** (UIFPE-219) across every screen and validation message. |
| Domain list derived from the repository | **Done** (UIFPE-219) — the hand-maintained list that went stale at seven is gone. |
| Live query preview for the three kinds | Shipped earlier (UIFPE-101/106/108) |
| Derived (SQL) column contract | Shipped (UIFPE-217); **authoring panel Done (UIFPE-219)** — paste box, advisory validate-as-you-type (the browser mirror of the Python battery: read-only single SELECT, stability, sources-or-registered-aliases fence, key/value contract visibility), AI draft into the same box (the draft service, mode derivedSql), key-pair and value-column editors, and the spliced-CTE preview. The **one-click test run is Done (UIFPE-219)** — "▶ Test — one row per entity?" runs the query verbatim against dev Silver via an Athena-backed service (aliases resolved by a CTE prelude, never rewritten) and answers with the row/entity counts, the worst duplication, and a 10-row sample. Dialect honesty: the test engine is Trino, the real run is Spark — plain SQL behaves identically, a Spark-only function fails the test with a readable engine error while the submit battery remains the authority. The service URL is wired per environment after its first deploy prints grain_probe_url. |
| Entity status cards, outcome chips in the column list, scalar grid view, join pre-fill memory | **Done** (UIFPE-219). Step 1 renders one card per entity — lifecycle, columns, key from the repository at once; row count and last run arrive live from the status service (the grain-probe Lambda, mode entityStatus) and simply stay absent while unwired. Every column row carries its outcome chip ("updates automatically" / "engineer reviews", the full sentence on hover). "Bulk edit scalars" turns the scalar rows into an editable grid — the intake workbook Scalar sheet, in place — while conditional/nested/derived keep the modal. The join editor prefills the way the task ALREADY attached the same table (latest column wins), falling back to the entity-key default. |
| Pre-submit full-query diff | **Done** (UIFPE-219). Review runs the REAL delta compiler (`compile_intake_delta.py`, bundled with sqlglot into `lambda_compile_preview`) over the task's envelope and the entity's current artifact texts (shipped in the UI's own build), then renders the returned before/after as a line diff — the deployed query first, everything else (schema, mapping, manifest, schedule) collapsed beneath it. A PLAN-classified change shows the compiler's own `[PLAN]`/`[RUNBOOK]` lines instead of a diff — the SQL genuinely isn't auto-edited for those, so the honest answer is "a data engineer applies this," not a fabricated diff. A battery refusal (e.g. an unregistered source) surfaces as the compiler's own words, not a generic error. The service URL is wired per environment after its first deploy prints `compile_preview_url`; until then Review shows no diff section, same posture as the other UIFPE-219 services. |
| PR-creation service + My submissions | **Done** (UIFPE-218) — closes the long-standing UIFPE-101 residual. Submit opens the PR from the browser (deterministic plan + `lambda_submit` + `intake-compile.yml`); My submissions tracks the full ladder. The service URL is wired per environment after its first deploy prints `submit_url`; until then the wizard presents the download-and-hand-over flow. |

## Out of scope

- Template galleries and recipe pickers — ruled out by direction.
- Step-chain authoring (the Derivation Studio canvas) — retired with ADR-0008's Option B.
- Renaming or aliasing source objects — the UI shows Silver as it is.
