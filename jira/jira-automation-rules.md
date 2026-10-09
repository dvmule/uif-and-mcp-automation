# Jira Automation Flows — exact specs for admin setup

Jira Automation is configured in the Jira UI (**Project settings → Automation**), where a rule is now built as a **Flow** from four step types: **Trigger**, **Condition**, **Branch**, **Action**. No Atlassian API used by this repo's tooling can create automation flows, so this document is written to be clicked through by a Jira admin, not run by an agent.

This is the precise, click-through spec for every flow UIFPE needs — the hand-offs named in `../capabilities/delivery/jira-github-integration.md`, plus the invariant checks that keep the repo's own models true in Jira (single owning parent, capability-as-label, four-in-a-box ownership).

**Status: pending admin action.** Tracked as **[UIFPE-115](https://pearson.atlassian.net/browse/UIFPE-115)** with the same checklist — tick items off there as each flow is configured and verified, since neither this file nor any tool here can read Automation state.

---

## 1. Two triggers per invariant, one shared condition set

An event trigger alone cannot cover everything: it only sees issues created *after* the flow exists, arriving through the path it watches. It misses the existing backlog, and it misses issues created by the REST API, an agent, or an external tool. A scheduled JQL sweep is what actually guarantees coverage.

| Lane | Trigger | Buys | Misses alone |
|---|---|---|---|
| **Fast lane** | *Issue created* / *Issue transitioned* | Feedback within seconds, at the moment of the mistake | Backlog; API-, agent-, or tool-created issues |
| **Backbone** | *Scheduled* (JQL, daily) | Complete coverage regardless of how the issue was created | Latency — up to one sweep interval |

**Hygiene/invariant flows (§4–6) need both lanes. Hand-off notification flows (§7–9) are event-driven only** — a notification for a transition that happened last week is either noise or a duplicate, so sweeping them is wrong by design, not merely unnecessary.

Automation allows one trigger per flow, so each invariant is a **matched pair** of flows with identical conditions and actions, differing only in the trigger. The duplication is a real cost; the mitigation is that the condition set is specified once, here, and copied — not reinvented per flow. If a condition changes, change it in both members of the pair.

## 2. The shared exclusion set (state this once, apply to every hygiene flow)

A sweep across the whole project will light up accepted history unless these are excluded explicitly. Use as the scheduled-trigger JQL, and as equivalent conditions in the event-trigger twin:

```jql
project = UIFPE
AND issuetype in (Story, Task, Spike, Bug)
AND statusCategory != Done
AND status != Cancelled
AND labels not in ("phase:0")
AND key not in (UIFPE-36, UIFPE-37, UIFPE-38, UIFPE-39, UIFPE-40, UIFPE-41)
```

Why each clause earns its place:

- **`issuetype in (Story, Task, Spike, Bug)`** — restricts to delivery work. This is what keeps Capabilities (UIFPE-8..21), Foundation Epics (UIFPE-22..35), and Domain Epics themselves out of the trace gate: none of them has a Domain Epic parent, by design, and flagging them would be a false positive.
- **`statusCategory != Done AND status != Cancelled`** — never nag closed work. (UIFPE-44, the cancelled orphan Domain Epic, is excluded by this.)
- **`labels not in ("phase:0")`** — `phase:0` marks the legacy design-pack issues (UIFPE-1..7), which predate the fourteen-capability model.
- **`key not in (UIFPE-36..41)`** — the six Sprint-0 Stories parented to Foundation Epics. This is the documented pre-domain exception (see `../README.md` "How is Jira designed?"), not a violation to correct.

## 3. Closed sets get enumerated; open sets get branched

This asymmetry is deliberate and mirrors the repo's own model — build it in rather than working around it later:

- **The thirteen capabilities are a closed set** (fixed and settled in `../CLAUDE.md`). Enumerate them directly in JQL or in a condition — the list will not drift:
  `labels in ("capability:api", "capability:event", "capability:data", "capability:schema", "capability:semantics", "capability:knowledge-graph", "capability:mcp", "capability:a2a", "capability:agent", "capability:infrastructure", "capability:security", "capability:observability", "capability:delivery")`
- **Domains are an open set** that grows every time `git new-domain` stamps one. Never enumerate domain names in a flow. Instead **Branch → parent**, then test the parent's summary prefix (`Domain:`). One flow then covers every current and future domain with nothing to keep in sync.

The same rule applies to the Components prerequisite below: the domain list is whatever `ls domains/` currently returns, not a list frozen into a document.

---

## 4. Flow — delivery work must trace to a Domain Epic

Enforces the single-owning-parent rule (`../capabilities/docs/capability-domain-jira-map.md`): delivery work parents to a **Domain Epic**; capability is a label, never a parent.

| Step | Configure |
|---|---|
| **Trigger** | *Issue created* (fast lane) · **or** *Scheduled* with the §2 JQL (backbone) |
| **Condition** | The §2 exclusion set (as conditions, for the event twin) |
| **Branch** | To **parent** |
| **Condition** (in branch) | Parent **summary** does **not** start with `Domain:` |
| **Action** | *Comment on issue* (the original, not the parent): `Trace gate: this is parented to {{issue.parent.key}} ({{issue.parent.summary}}), which is not a Domain Epic. Per the single-owning-parent rule, delivery work parents to "Domain: <Name>" and carries capability as a label. See capabilities/docs/capability-domain-jira-map.md.` |

Also handle the **no parent at all** case — an issue with an empty parent never enters the branch, so it needs its own path: **Condition** `parent is EMPTY` → same comment, worded for a missing parent.

## 5. Flow — delivery work must carry a `capability:*` label

Capabilities are the horizontal axis; an issue with no capability label is untraceable to a standard.

| Step | Configure |
|---|---|
| **Trigger** | *Issue created* · **or** *Scheduled* (§2 JQL, plus `AND labels not in (<the thirteen>)`) |
| **Condition** | The §2 exclusion set |
| **Condition** | Labels do **not** match any of the thirteen `capability:*` values (§3) |
| **Action** | *Comment on issue*: `Trace gate: no capability:* label. Add exactly one of the thirteen — api, event, data, schema, semantics, knowledge-graph, mcp, a2a, agent, infrastructure, security, observability, delivery. See jira/capability-model.md.` |

## 6. Flow — a Domain Epic must be complete on creation

This is the gap that required a manual backfill: three Domain Epics existed with `null` descriptions until they were audited by hand. This flow prevents the class, rather than needing another audit.

| Step | Configure |
|---|---|
| **Trigger** | *Issue created* · **or** *Scheduled*: `project = UIFPE AND issuetype = Epic AND summary ~ "Domain:" AND (description IS EMPTY OR assignee IS EMPTY)` |
| **Condition** | Issue type = Epic **AND** summary starts with `Domain:` |
| **Condition** | Description **is empty** **OR** assignee **is empty** **OR** labels do not contain a `domain:` value |
| **Action** | *Comment on issue*: `Domain Epic incomplete. Required: a description naming the domain and its domains/<name>/ folder, an assignee (the domain's FDE), and a domain:<name> label. See .claude/skills/domain/SKILL.md for the exact shape.` |

Note the summary check also catches the bare-`Domain ` malformed case that produced UIFPE-44.

---

## 7. Flow — PR opened → record review state on the issue (event only)

**Reframed deliberately.** GitHub already notifies requested reviewers, faster and better than Jira can. The real gap is that PM and QE do not live in GitHub and cannot see review state. So this flow *records*, it does not notify.

| Step | Configure |
|---|---|
| **Trigger** | *Pull request created* (GitHub for Jira) |
| **Condition** | Project = UIFPE |
| **Action** | *Comment on issue*: `PR opened: {{pullRequest.url}} — reviewers: {{pullRequest.reviewers}}.` |

If `{{pullRequest.reviewers}}` does not resolve on this Jira/app tier, drop that half of the smart value and keep the URL — the flow still delivers its actual purpose. Verify before relying on the reviewer list; do not build a notification dependency on it.

## 8. Flow — Ready for QA → notify the domain's QE Lead (event only)

| Step | Configure |
|---|---|
| **Trigger** | *Issue transitioned* → **Ready for QA** (pick the status from the dropdown) |
| **Condition** | Project = UIFPE |
| **Branch** | To **parent** (the Domain Epic — this is where the Component lives, per §10) |
| **Action** | *Send notification* → **Component Lead** |
| **Action** | *Comment on issue*: `Ready for QA — {{issue.parent.component.lead.displayName}}, this is ready for testing.` |

The comment matters as much as the notification: it puts the hand-off in the issue's own history rather than only in someone's inbox.

**Verify the smart value resolves through the branch** before depending on it. If it does not, fall back to setting the Component on each Story directly — accepting the per-issue upkeep §10 is designed to avoid.

## 9. Flow — Bug filed against a Story → notify the Story's assignee (event only)

| Step | Configure |
|---|---|
| **Trigger** | *Issue created* |
| **Condition** | Project = UIFPE **AND** issue type = Bug |
| **Branch** | To **linked issues**, link type *relates to* / *is caused by* |
| **Condition** (in branch) | Issue type = Story |
| **Action** | *Send notification* → **Assignee** (resolves to the linked Story's assignee inside the branch) |
| **Action** | *Comment on issue* (the Story, inside the branch): `Bug {{issue.key}} filed against this Story: {{issue.summary}}.` |

Careful with smart values here: inside a branch, `{{issue}}` refers to the **branched** issue (the Story). Use `{{triggerIssue.key}}` to refer to the Bug. Getting this backwards is the most common failure in this flow.

---

## 9A. Flow — flag a possible duplicate on issue creation (event only)

Real-time companion to the 2026-09-25 backlog sweep (`../RETROFIT-LOG.md`). Catches a duplicate at
the cheapest possible moment — creation — rather than waiting for another full sweep. **Detects
only; never closes, cancels, or edits anything.** Every real duplicate found in the 2026-09-25
sweep needed a human/agent to check the description and parent, not just the title — this flow
cannot do that judgment, so it flags for review instead of asserting a duplicate.

| Step | Configure |
|---|---|
| **Trigger** | *Issue created* |
| **Condition** | Project = UIFPE |
| **Action** | *Lookup issues* — JQL: `project = UIFPE AND statusCategory != Done AND status != Cancelled AND summary ~ "\"{{triggerIssue.summary}}\"" AND key != {{triggerIssue.key}}` |
| **Branch** | For each looked-up issue |
| **Condition** (in branch) | `{{triggerIssue.issuetype.name}} != "Sub-task"` **OR** `{{issue.parent.key}} == {{triggerIssue.parent.key}}` |
| **Action** (in branch, if condition passes) | *Comment on issue* (the branched issue): `Possible duplicate: {{triggerIssue.key}} ("{{triggerIssue.summary}}") was just created with the same or a very similar summary as this issue. Check both descriptions and parents before treating this as a real duplicate — see RETROFIT-LOG.md's 2026-09-25 sweep for the shape of a false positive.` |
| **Action** (after branch, on the trigger issue, only if at least one branch matched) | *Comment on issue*: `Possible duplicate of {{lookupIssues.size}} existing open issue(s) with a matching or near-matching summary — see the comment(s) just posted on the match(es). Confirm scope before doing anything.` |

**Why the Sub-task branch condition exists, and why it is a same-parent check rather than a
blanket exclusion.** Sub-tasks are where engineers and QE actually do their day-to-day work, so
they are exactly where an accidental double-filed item is likeliest — excluding Sub-task entirely
would blind the flow to the population that needs it most. But the "Architecture / Component
Architecture / Physical Architecture / Design Pattern" sub-task template — confirmed clean in the
2026-09-25 sweep — legitimately repeats the exact same summary once per **different** parent, by
design. The distinguishing signal is the parent: a real accidental duplicate at Sub-task level is
almost always filed under the **same** parent as an existing one; the legitimate template repeats
under a **different** parent each time. The condition encodes exactly that — for a Sub-task, only
flag when the matching issue shares the same parent; for every other issue type (Story, Task, Bug,
Epic), flag on any open match regardless of parent, since no per-parent template pattern applies
there.

**Verify before relying on it**: `summary ~ "..."` is Jira's tokenized text match, not an exact
string — it will catch near-identical summaries (useful) but can also over-match on short or
generic titles (e.g. "Architecture" tokenized against "Architecture - Beacon"). Watch the first
week's audit log for over-firing; narrow to `summary = "{{triggerIssue.summary}}"` (exact match
only) if it turns out noisy.

**Status: pending admin action**, same as every other flow in this file — add to the UIFPE-115
checklist when built.

---

## 10. Prerequisite — Components as the QE-Lead carrier only

UIF's four-in-a-box ownership lives in `.github/CODEOWNERS`, not a Jira field, so Jira has nothing native to point at for "this domain's QE Lead." A Component's **Component Lead** is the one native field that carries a person per domain, which is the only reason Components are used here.

1. Create one **Component** per domain currently under `domains/` (`ls domains/` is the source of truth — this set grows every time `git new-domain` stamps a new one, so treat it as a running item, not a one-time enumeration), named to match the `domain:<name>` label.
2. Set each Component's **Component Lead** to that domain's real QE Lead account — the person named on `CODEOWNERS`' `/domains/*/qe/` line, once the placeholder handles (`@uif-qe`) are replaced with real accounts.
3. **Set the Component on the Domain Epic only** — not on every Story and Sub-task.

Step 3 is a deliberate narrowing of an earlier version of this spec, which asked for the Component on every issue. That is a recurring manual step that drifts silently, and it makes Component a *third* representation of a fact already carried twice (the `domain:<name>` label and the Domain Epic parent), against the repo's "link, don't duplicate" rule. Because **Branch** is a first-class step, flows can branch to the parent Domain Epic and read the Component there — same result, no per-issue upkeep. Component is a people-carrier, not a domain tag.

---

## 11. Steps to build a flow in the Jira UI

Do these in order. Build **one** flow end to end and verify it before building the rest — a wrong smart value fails silently, and finding that out once is cheaper than finding it out six times.

**Getting to the editor**

1. Open the UIFPE project → **Project settings** (left sidebar, bottom).
2. Select **Automation**.
3. Select **Create rule** / **Create flow** (top right). The empty canvas shows *Add a trigger*.

**Building the flow**

4. **Add a trigger.** Click *Add a trigger* and pick one:
   - *Issue created* — fast lane for §4–6.
   - *Issue transitioned* — for §8; choose the destination status **from the dropdown** (`Ready for QA`), never by typing it.
   - *Scheduled* — backbone for §4–6; tick **Run a JQL search and execute actions for each issue**, paste the §2 JQL, set the interval to daily.
   - *Pull request created* — for §7; requires the GitHub for Jira app to be connected to this project.
   Save the step.
5. **Add a condition.** Click **+** below the trigger → *Add a condition*.
   - Use **Issue fields condition** for single-field tests (project, issue type, description is empty, summary starts with).
   - Use **JQL condition** when you want the §2 exclusion set as one block rather than six separate steps — this is usually less error-prone and keeps the event twin visibly identical to the scheduled JQL.
   - Stack multiple conditions to get AND. For OR, use the condition group's *any of* option where offered, or split into two flows.
6. **Add a branch** (only where the spec calls for one). Click **+** → *Add a branch* → choose **Parent** (§4, §8) or **Linked issues** (§9, then set the link type). Steps you add *inside* the branch run against the branched issue.
7. **Add an action.** Click **+** → *Add an action*:
   - *Comment on issue* — paste the comment text from the spec verbatim, including smart values.
   - *Send notification* — choose the recipient type the spec names (Component Lead, Assignee).
   - Watch the placement: an action **inside** a branch targets the branched issue; the same action **after** the branch targets the trigger issue. §4's comment must land on the original issue, §9's on the Story.
8. **Name the flow** using the spec's section heading (e.g. *"UIFPE — trace gate: Domain Epic parent (scheduled)"*). Put the lane in the name so the matched pair (§1) is visible in the rule list and nobody edits one and forgets the other.
9. **Set the rule scope** to the UIFPE project only (rule details → scope), so it cannot fire against other projects.
10. **Turn it on** — a saved flow is not an enabled flow. Confirm it appears **Enabled** in the Automation list.

**After building**

11. Check **Audit log** (per rule, and project-wide) after the first real trigger. This is where a silently-failing smart value shows up as *No actions performed* — the flow will look healthy in the rule list either way.

## 12. Verification (do this before relying on any flow)

File one throwaway test issue per flow — a Sub-task under a real domain Story is enough — and walk it through the exact trigger and link conditions. For scheduled flows, use the trigger's **Run rule** / manual-run option rather than waiting a day.

Confirm for each: the notification arrived, the comment landed **on the intended issue**, and the audit log shows actions performed rather than zero. Automation triggers are as sensitive to exact status and issue-type names as the `In Progress (1)` transition-name mismatch documented in `../capabilities/delivery/jira-github-integration.md` — a mismatch fails silently instead of erroring.

One thing the Flows UI does improve: transition triggers pick the status from a dropdown, so that particular typo class cannot bite you here.

## 13. A UIF-scoped Rovo agent — what it can and cannot simplify

A Rovo agent built in **Atlassian Studio** (`studio.atlassian.com`), scoped to UIF only, is worth building — but it does **not** replace the flows above, and the split matters enough to state before anyone builds it.

**Flows detect. The agent drafts.** §4–6 are mechanical boolean tests: does this issue have a Domain Epic parent, a `capability:*` label, a non-empty description. JQL answers those deterministically, for free, on a schedule, with an audit log. An LLM answering the same question is slower, costs per invocation, and — decisively — *may answer differently on two identical issues*. You cannot gate delivery on a non-deterministic check. Keep enforcement in JQL.

What the agent is genuinely better at is everything that happens **after** a flow fires, which is judgment-shaped:

| Flow flags | Agent drafts (the part a human currently does by hand) |
|---|---|
| §4 — no Domain Epic parent | Reads the issue and proposes *which* Domain Epic it belongs under |
| §5 — no `capability:*` label | Proposes which of the thirteen applies, with the reasoning |
| §6 — Domain Epic incomplete | Drafts the description in the standard shape from `.claude/skills/domain/SKILL.md` |
| §9 — Bug filed | Summarises the Bug against the Story's acceptance criteria |

So chain them: the flow detects and comments; where the tier offers a **Rovo agent action** inside Automation, the flow hands the issue to the agent to draft the remedy. Verify that action exists on your tier before designing around it — if it does not, the agent stays conversational (a PM asks it in the issue view) and the flows still work unchanged.

**The real blocker is the knowledge source, not the agent.** Rovo indexes Jira and Confluence. UIF's standards deliberately do **not** live in Confluence — they live in this repo, with Confluence holding only a thin pointer page (id 1261241098). A UIF-scoped agent therefore starts with rich *issue* data and almost no *standards* data. Three options, and the first two are traps:

1. **Publish the capability guidelines to Confluence so Rovo can index them** — creates a second copy of every standard with no build step to catch drift, against `../CLAUDE.md`'s "link, don't duplicate". It also means the Enterprise Blueprint integration-reduction figures would need care: **that roadmap data does not go into Confluence.** Do not solve the knowledge-source problem by bulk-publishing the repo.
2. **Give the agent no grounding and rely on the base model** — it will produce plausible, confidently-wrong UIF conventions. Worse than no agent, because it sounds authoritative.
3. **Ground it narrowly and deliberately** — the recommended path. Put the invariants the agent needs *in the agent's own instructions* (the thirteen capability names, the single-owning-parent rule, the Domain Epic shape, the exclusion set), scope its knowledge to the UIFPE project plus the pointer page, and have it cite the repo path for anything deeper rather than paraphrasing it.

**Suggested agent configuration:**

- **Scope:** UIFPE project only. Not site-wide — a UIF agent giving UIF answers in another team's project is a support burden.
- **Instructions:** state the thirteen capabilities verbatim, the single-owning-parent rule, the Domain Epic shape, and the four-in-a-box roles. End with: *for any standard not stated here, cite the repo path and do not paraphrase it.*
- **Permitted actions:** comment, suggest, summarise. **Not** transition, assign, close, or edit fields. An agent that can move work through the workflow can move it wrongly, silently, at scale.
- **Human-in-the-loop:** every agent suggestion is a comment for a human to act on — same posture as the flows (advisory, not blocking) and the same rule the `../capabilities/agent/guidelines.md` `humanInTheLoop` policy sets for UIF's own agents. The governance UIF writes for its agents applies to the agent UIF runs on itself.

**It does not replace the Claude Code skills.** `pm`, `fde`, `architect`, `ux`, `qe`, and `domain` (`.claude/skills/`) are repo-grounded and run where the FDE works — they read the actual standards files. The Rovo agent is Jira-grounded and runs where the PM and QE Lead work. Same conventions, two surfaces, different reach. Neither is the other's replacement, and the conventions must stay identical across both or they will diverge.

## Rules that already exist in the live instance

**Everything above is a specification for flows to build. This section records a flow that
is already running and was never specified here** — found on 2026-09-11 by mining issue
changelogs, not by reading any configuration. A live rule that is not in this file is
ungoverned by definition, so record one here the moment it is discovered.

### Live flow — assign on entry to In Progress

| | |
|---|---|
| **Trigger** | *Issue transitioned* → **In Progress**. Fires on `In Progress → In Progress` too, which a repeated `#in-progress` smart commit produces |
| **Condition** | none observed |
| **Action** | *Assign issue* → **the account that performed the transition** |
| **Latency** | 1.3–1.7s after the transition, recorded as `Automation for Jira` |
| **Scope observed** | `→ Code Review`, `→ Ready for QA`, `→ QA In Progress`, `→ Done`, `→ Blocked` never touch the assignee, across ~24 events |
| **Evidence** | `[changelog]` six fires on UIFPE-36, 184, 367. Tabulated in [`../RETROFIT-LOG.md`](../RETROFIT-LOG.md) under "Root cause 1, precisely" |

**Why it is a defect in practice, not in intent.** For a person picking work up, *"whoever
starts it owns it"* is correct behaviour. The defect is that `jira-transition.yml`
authenticates as `serv.GIPBBCICDUser`, so **every keyed commit** counts as "starting it" and
hands the issue to a service account that authors no code. It has taken UIFPE-184 from
Panikiran B three times. It also fires for people: on 2026-08-20 a human `To Do → In
Progress` moved UIFPE-36 off Vaibhav Yenorkar, unintended by anyone.

### Decision — 2026-09-11, framework owner

**Condition the rule to ignore the service account. Do not delete it, and do not stop CI
setting status.** The intent is sound for humans; only the CI actor is wrong.

Add to the flow, as a **Condition** between trigger and action:

| | |
|---|---|
| **Condition type** | *User condition* — or *Advanced compare* if the Flows UI offers no user check on the trigger actor |
| **Check** | the **initiator** (`{{initiator.accountId}}`) is **not** `712020:8435500f-c113-4953-ad2e-eb12dac59cd0` (`serv.gipbbcicduser@pearson.com`) |
| **Then** | proceed to the assign action |
| **Else** | stop — leave the assignee untouched |

Prefer selecting the account from the picker over typing the id, for the reason §10 gives
about typed status names. After the change, verify it the way this file requires of every
flow: push one keyed commit to a throwaway branch against a test Sub-task, confirm the
status still moves to In Progress and the **assignee does not change**, then move the same
issue to In Progress by hand and confirm the assignee **does** become you. Check the audit
log shows *No actions performed* for the CI case.

**This closes a known contradiction elsewhere.**
[`../DEFINITION-OF-DONE.md`](../DEFINITION-OF-DONE.md) §1 gives `To Do → In Progress` to CI
while §2 requires a human assignee before work starts; until this condition exists the first
destroys the second on every ticket.

**Not yet done, and it is the only thing left:** nobody has opened the rule in
**Project settings → Automation**. The description above is inferred from the rule's output,
not read from its configuration. Confirm the trigger and action match before adding the
condition — if they do not, correct this section rather than the rule.

## Related

- Hand-off table + Smart Commit convention these flows back: `../capabilities/delivery/jira-github-integration.md`
- Comment automation driven from CI/CD events (code, not admin config): `../.github/workflows/jira-transition.yml`
- The single-owning-parent rule §4 enforces: `../capabilities/docs/capability-domain-jira-map.md`
- The Domain Epic shape §6 enforces: `../.claude/skills/domain/SKILL.md`
- The human-in-the-loop policy §13 applies to the Rovo agent: `../capabilities/agent/guidelines.md`
- §9A's real-time check is the fast lane for the backlog duplicate sweep: `../RETROFIT-LOG.md` (2026-09-25 section)
