---
name: pmdiscrepancies
description: Use this skill whenever the user wants to update PMdiscrepancies.md or jira/jpd-alignment.md, check whether a specific claim in either is still accurate, verify an architecture-vs-build or Jira-vs-reality gap, or reconcile either document against current state. Trigger on "update PMdiscrepancies", "is this claim still accurate", "re-check §9", "re-check §10", "verify the gap analysis", "check the architecture components section", "audit PMdiscrepancies", "has the PM/SM been onboarded yet", "check the BAA section", "re-check jpd-alignment", or any request to refresh either document.
---

# pmdiscrepancies — evidence-checked updates to PMdiscrepancies.md and jpd-alignment.md

`PMdiscrepancies.md` exists because Jira, the architecture blueprints, and the repository drift apart, and someone has to keep saying so with receipts. Updating any part of it — not only §9/§10, but the Jira-hygiene findings in §1–7, the workstream mapping, the remediation list, and the open questions — is a repeatable verification procedure, not a writing task: match a claim to a real source, classify it, show the diff. §8 ("The evidence base is in git, not Jira") is where this discipline comes from in the document itself; `evidence-hierarchy-git-over-jira.md` and `working-conventions.md` carry the same rule forward for everything else in this repo.

`jira/jpd-alignment.md` carries the same discipline in miniature: two explicit, event-triggered markers rather than numbered sections — *"rewrite once a PM/SM is onboarded"* (ownership line) and *"re-check when the BAA conflict is reconciled"* (the Business Assurance Agent section, which currently states two sources disagree on what BAA is). Neither fires on a schedule; both fire when someone asks. This skill's workflow applies unchanged — match the claim, check the real source, classify, diff — the only difference from `PMdiscrepancies.md` is that there's one claim to check per marker, not a whole section.

## Scope — the whole document, not just the architecture sections

Every numbered section is a candidate for re-check, and they're not all the same kind of claim:

| Sections | What they claim | Primary evidence |
|---|---|---|
| §1–2 (epic populations, schedule) | Jira data quality and structure | Jira itself — `searchJiraIssuesUsingJql`, re-derive counts |
| §3–4 (dependency graph, external commitments) | What Jira links actually show vs. imply | Jira issue links, re-checked, not assumed from a summary |
| §5–6 (delivery concentration, data defects) | Who really did the work, what's broken in the data | `git log --author`, commit history — not the Jira assignee (automation reassigns it, see `rule-delivery-not-enforcement.md`) |
| §7 (identity gates) | Whether CI gates agree with each other | The actual gate configs / workflow files, run against real commit history |
| §9 (architecture components) | What's built vs. what six external diagrams show | `grep`/`find` the repo, ADRs, Terraform |
| §10 (what's provisioned) | Real infra vs. Jira coverage | Terraform files directly, `gh run list` for deploy history |
| Workstream mapping, Remediation, Open questions | Programme-level framing | Cross-check against current Jira epics and this repo's own `jira/` docs |
| `jpd-alignment.md` ownership line | Whether a PM/Scrum Master is onboarded yet | Ask the user directly — staffing isn't derivable from git or Jira; this is the one claim type where the source is a person, not a system |
| `jpd-alignment.md` BAA section | Whether the Project-Plan-vs-Operating-Structure conflict on what BAA is has been reconciled | Re-read both sources (Confluence page 1479999498; the UIF Operating Structure reference) — reclassify only if they now agree |

## Workflow

1. **Read the exact claim before touching it.** Quote it verbatim from the current file — don't paraphrase from memory of a prior read; the document and the repo may both have changed since.

2. **Match the claim to its real source, per the evidence hierarchy — git and Terraform outrank Jira:**
   - Code/infrastructure existence → `grep`/`find` the repo, read the Terraform files directly, check real resource state. Never infer from a Jira ticket's title or status.
   - Deploy/CI history → `gh run list` / `gh run view`, not a cached count from a prior pass.
   - Jira coverage or structure → `searchJiraIssuesUsingJql`, re-derive the count yourself.
   - "Who did this" → `git log --oneline --author=<email>` on the actual files, not the Jira assignee field (a status-transition automation rule silently reassigns it — `rule-delivery-not-enforcement.md`).
   - A referenced Jira ticket (e.g. "tracked as UIFPE-209") → re-check its *current* status with `getJiraIssue`, don't assume it's still open just because the doc says so.

3. **Classify the result into exactly one of three buckets, and say which, with the evidence:**
   - **Confirmed** — still true; cite the same source type that originally established it.
   - **Stale** — was true, isn't anymore; name what changed (a merged PR, a new Terraform resource, a deploy run, a closed ticket) and when.
   - **Deliberately deferred** — not built on purpose, backed by an actual recorded decision (e.g. "R1 or later," a named epic) — not silence mistaken for a decision.

   If the evidence doesn't land cleanly in one of the three, say so and flag it for a human call rather than forcing a bucket.

4. **One item first, on any bulk re-check.** Verify one claim completely, show the before/after wording, and get confirmation on the *pattern* of the fix before repeating it across the rest of a section. A single pass that silently "fixes" forty rows the same way is exactly how one wrong assumption propagates forty times — the same lesson the DS-* epic retrofit (`ds-epics-retrofit.md`) already paid for once.

5. **Draft the edit as a diff; never write the file directly without it being shown first.** `PMdiscrepancies.md` is a real, shared repository document, committed with a Jira key like any other change here (`capabilities/delivery/jira-github-integration.md`) — same draft-then-confirm rule as every other shared-system write in this repo.

6. **A new finding gets flagged separately, never folded into an existing section as if it were already known.** If checking one claim surfaces a different, previously-undocumented gap, record it as its own item, labelled as new, with its own evidence — don't quietly expand the scope of the section being corrected.

## Worked example (a real one, from this repository's own history)

**Claim checked:** §9 originally implied Echo's stack had only 8 placeholder READMEs and no real implementation.

1. *Read the exact claim* — quoted the specific line rather than the general gist.
2. *Matched to evidence* — checked git directly: `gh pr view 206` / `git log` showed PR #206 (UIFPE-355) merged 2026-09-10, adding the real Echo tree.
3. *Classified* — **Stale**, not wrong-when-written: the claim was accurate at the time the analysis was dated, but a real merge afterward changed the ground truth. Named the exact PR and date rather than just saying "this seems out of date."
4. *Drafted the correction* as a one-line addition — "as of PR #206 (2026-09-10), this is no longer accurate; the tree now has N real files" — rather than deleting the original finding, which would have erased the historical record of what was true when the analysis ran.

The same pattern applied to §10's "four deploy runs total, all Beacon" claim: `gh run list` directly, on the day of the re-check, showed dozens of successful runs across Echo, Apicurio, and Beacon in the prior two days — reclassified from a static fact to "stale — corrected as of <date>," with the real number shown alongside the old one, not replacing it silently.

## What this skill does NOT do (stays a human judgment call)

- **Whether a gap is "deferred on purpose" vs. actually overlooked.** The skill surfaces the evidence either way; deciding which reading is correct when it's ambiguous needs context the source alone doesn't carry.
- **Whether an open architectural question should now be resolved.** Items like mono-repo vs. poly-repo (§ Open questions) get flagged as still open — the skill never closes one on its own initiative.
- **Deciding remediation.** `PMdiscrepancies.md` documents gaps; it does not decide what to do about them. That's a separate ADR, epic, or explicit owner decision — this skill stops at "here is what's actually true today."
