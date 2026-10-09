# Demand workflow — agent execution spec

This document is the single operational surface for an agent working UIF demand intake: the exact status names, transition names, field names, and machine-testable gate predicates, plus a paste-ready instruction block. It exists because building an agent requires literal strings — a status name that is one character off fails silently — while the two source documents are written to explain *why* the workflow has the shape it does.

Plain-language version: `demand-workflow.md` is the design and the reasoning. `jira-automation-rules.md` is the click-through spec for a Jira admin. **This file is the contract an agent is built against.** Where the three disagree, the two source documents win and this file is the one that drifted.

Rationale is deliberately not repeated here. For *why* `Redesign` is a transition rather than a status, why `HLD Link` is a URL field, or why validators are preferred over Automation, read `demand-workflow.md`.

**Prerequisite, and it is a hard one.** None of this exists in UIFPE yet — verified 2026-08-14: the project has 7 issue types (Epic, Capability, Story, Task, Bug, Sub-task, Spike) and no `Demand`; no Components are set on any issue; `UIFPE-115` is still `To Do` with every checklist item unticked. An agent has nothing to act on until the issue type, the twelve fields, and the eight statuses are provisioned. Build in this order:

1. `./provision-demand-workflow.sh` in dry-run, read the planned calls, then `--apply` against a sandbox first.
2. Build the workflow, transitions, and validators in the UI against `demand-workflow.md` §4a. Publish the draft — an unpublished draft has no effect while the board looks entirely correct.
3. Build the four Automation rules by hand (`demand-workflow.md` §4c). Automation rules have no published REST API.
4. *Then* point the agent at it.

An agent built before step 2 will appear broken for reasons that have nothing to do with the agent.

---

## 1. States and transitions — exact names

```
[*] → Demand Received
Demand Received      --Submit-->            Demand Validation
Demand Validation    --Fails validation-->  Invalid                     (terminal, reopenable)
Invalid              --Resubmit-->          Demand Validation
Demand Validation    --Met by existing-->   Met by Existing Capability  (terminal)
Demand Validation    --Design required-->   Design In Progress
Design In Progress   --Design drafted-->    Design Complete
Design Complete      --Submit for review--> Architecture Review
Architecture Review  --Redesign-->          Design In Progress          (increments Rework Count)
Architecture Review  --Approved-->          Ready for Build
Ready for Build      → Domain Epic + Stories created → delivery backlog
```

Eight statuses, nine transitions. `Met by Existing Capability` is a **metric, not a failure** — it is the state that makes the reuse target measurable rather than aspirational.

Two things are not statuses, and an agent must not treat them as such: **"is design required?"** is the branch out of `Demand Validation`, and **`Redesign`** is a transition back to `Design In Progress`.

## 2. Fields — read and write by exact name

| Field | Type | Options / constraint |
|---|---|---|
| `Business Outcome` | Paragraph | — |
| `Consuming System` | Short text | — |
| `Reuse Outcome` | Select | `Reuse` · `Partial` · `None` |
| `Satisfying Contract` | Short text | Apicurio `group:artifact` coordinate, or repo path |
| `Capability Span` | Multi-select | the thirteen capability names, exactly |
| `Invalid Reason` | Paragraph | — |
| `HLD Link` | **URL** | must match `^https://github\.com/pearson-dnt-coe/universal-Integration-framework/` |
| `Security Posture` | Paragraph | — |
| `Observability Posture` | Paragraph | — |
| `Principles Reviewed` | Checkbox | — |
| `Review Outcome` | Select | `Approved` · `Redesign` |
| `Rework Count` | Number | incremented by Automation only, never by hand or by an agent |

Built-ins that carry gates: **Reporter** (requester), **Due date** (target date), **Resolution** (paired with `Invalid Reason`), **Parent** (the Domain Epic).

Twelve custom fields is the ceiling, not a starting point. Every field is something a human must fill before a card will move, and an unfillable gate gets bypassed rather than satisfied. If a field is not named as a gate in §3, it does not belong.

## 3. Gates as machine-testable predicates

A gate is what must be true to **leave** a status, not what happens in it. Each row is a deterministic check — this is what a validator blocks on and what an agent evaluates and reports.

| Transition | Precondition (all non-empty / true) | Enforced by |
|---|---|---|
| Submit | `"Business Outcome"`, `"Consuming System"`, `duedate` | Validator |
| Fails validation | `"Invalid Reason"` | Validator |
| Met by existing | `"Reuse Outcome" = Reuse`, `"Satisfying Contract"` | Validator |
| Design required | `"Reuse Outcome"`, `"Capability Span"` | Validator |
| Design drafted | `"HLD Link"`, `"Security Posture"`, `"Observability Posture"` | Validator |
| Submit for review | `"HLD Link"` matches the GitHub prefix, `"Capability Span"` | Validator + revert fallback |
| Approved | `"Principles Reviewed" = true`, `"Review Outcome" = Approved` | Validator |

**Quote field names exactly as created** — `"HLD Link"`, never `HLD Link`. An unquoted or misspelled name matches nothing: the check silently passes, the rule reports no error, and it still looks healthy in the rule list.

**Validators block; Automation reacts.** A validator refuses the move. An Automation rule fires *after* the event, so its only gating form is revert-and-explain — comment, then transition back. Use that fallback only for gates where this instance's validator picker lacks the rule you need. UIFPE is company-managed (`simplified: false`), so validators are available; a team-managed project would have none and every row above would fall back to the reactive pattern.

## 4. Delivery-backlog invariants, past `Ready for Build`

These are the checks in `jira-automation-rules.md` §4–6, restated as predicates. They are currently unenforced — as of 2026-08-14 no automation-authored comment exists anywhere in UIFPE, and recent issues (UIFPE-153, -154, -155) carry no labels at all.

| Invariant | Detection |
|---|---|
| Delivery work parents to a **Domain Epic** | parent summary does not start with `Domain:`, **or** parent is EMPTY |
| Exactly one `capability:*` label from the closed set of thirteen | `labels not in ("capability:api", … all thirteen)` |
| A Domain Epic is complete on creation | `issuetype = Epic AND summary ~ "Domain:" AND (description IS EMPTY OR assignee IS EMPTY)` |

Apply the shared exclusion set to all three, or a sweep lights up accepted history:

```jql
project = UIFPE AND issuetype in (Story, Task, Spike, Bug)
AND statusCategory != Done AND status != Cancelled
AND labels not in ("phase:0")
AND key not in (UIFPE-36, UIFPE-37, UIFPE-38, UIFPE-39, UIFPE-40, UIFPE-41)
```

`UIFPE-36..41` are the six Sprint-0 Stories parented to Foundation Epics — the documented pre-domain exception, not a violation to correct.

**Closed sets get enumerated; open sets get branched.** The thirteen capabilities are settled in `../CLAUDE.md`, so enumerate them. Domains grow every time `git new-domain` stamps one, so never enumerate domain names — branch to parent and test the `Domain:` summary prefix. One rule then covers every current and future domain with nothing to keep in sync.

## 5. What the agent does, and what it must not

| Step | Agent's role |
|---|---|
| Demand Received completeness | **Deterministic** — report which gate fields are empty |
| **Reuse search** (Demand Validation) | **Proposes candidates; a human decides** and records `Reuse Outcome` |
| Capability Span | Proposes which of the thirteen apply, with reasoning |
| HLD diagram | Drafts Mermaid; the Solution Architect owns it |
| Architecture Review | **Human.** The agent may pre-check that each principle is *addressed*; it does not judge whether it is *satisfied* |
| Orphaned issue / missing capability label | Proposes *which* Domain Epic and *which* capability |
| `Ready for Build` → Domain Epic + Stories | **Automation, not the agent** |

**Permitted: comment, suggest, summarise. Forbidden: transition, assign, close, resolve, edit any field.** An agent that can move work through the workflow can move it wrongly, silently, at scale.

The deeper reason the gates stay in validators and JQL: an LLM may answer differently on two identical demands. Delivery cannot be gated on a non-deterministic check. The agent's value is entirely **post-detection** — the judgment-shaped work after a gate has already fired deterministically. This is the same `humanInTheLoop` posture `../capabilities/agent/guidelines.md` requires of UIF's own agents; the governance UIF writes for its agents applies to the agents UIF runs on itself.

## 6. Agent instructions — paste into Studio

```
You assist with UIF demand intake in the Jira project UIFPE only. Never act on other
projects.

THE 13 CAPABILITIES (closed set, exact names):
api, event, data, schema, semantics, knowledge-graph, mcp, a2a, agent,
infrastructure, security, observability, delivery

RULES YOU ENFORCE BY REPORTING (never by editing):
1. Single owning parent: delivery work (Story/Task/Spike/Bug) parents to exactly one
   Domain Epic titled "Domain: <Name>". Capability is ALWAYS a label
   (capability:<name>), NEVER a parent. Never offer a human two possible parents.
2. Every delivery issue carries exactly one capability:* label from the 13 above.
3. A Domain Epic has: a description naming the domain and its domains/<name>/ folder,
   an assignee (the domain's FDE), and a domain:<name> label.
4. A Demand cannot leave a status until that status's gate fields are non-empty.
   Report which are missing; do not fill them in.

DEMAND STATUSES: Demand Received, Demand Validation, Invalid,
Met by Existing Capability, Design In Progress, Design Complete,
Architecture Review, Ready for Build.

YOUR HIGHEST-VALUE TASK is the reuse search at Demand Validation: propose already-
registered contracts that could satisfy the demand, and always present them as
candidates for a human to accept or reject. Never set Reuse Outcome yourself.

PERMITTED ACTIONS: comment, suggest, summarise.
FORBIDDEN: transition, assign, close, resolve, or edit any field.

For any UIF standard not stated above, cite the repo path
(e.g. jira/demand-workflow.md §2) and DO NOT paraphrase the standard.
```

Scope the agent to UIFPE, not site-wide — a UIF agent giving UIF answers in another team's project is a support burden.

## 7. The knowledge gap that will break the reuse search

The reuse search is the highest-value step in the workflow and the one an agent cannot currently perform, because **Rovo indexes Jira and Confluence, and UIF's registered contracts live in neither.** They live in `domains/*/src/schemas/` in this repo and in Apicurio. The agent therefore starts with rich *issue* data and almost no *contract* data, and will produce plausible, confidently-wrong reuse candidates.

Three options. The first two are traps:

1. **Bulk-publish the capability guidelines to Confluence so Rovo indexes them** — creates a second copy of every standard with no build step to catch drift, against `../CLAUDE.md`'s "link, don't duplicate". The Enterprise Blueprint integration-reduction figures must not go to Confluence at all. Do not solve the knowledge-source problem this way.
2. **Rely on the base model** — authoritative-sounding invented conventions. Worse than no agent.
3. **Give it a real contract index** — the recommended path. Either an action that queries Apicurio `group:artifact` coordinates directly, or one narrow generated page that is *only* a contract inventory and not a copy of the standards. Settle this before anyone trusts a reuse suggestion.

Until option 3 exists, treat every agent reuse proposal as a starting point for a human search, not a result.

## 8. Verification

Walk one throwaway Demand through **every** transition, including `Invalid` and `Redesign`, and confirm each gate actually *refuses* an incomplete move. Then read the audit log — a misquoted field name or an unresolved smart value surfaces there as "No actions performed" while the rule list looks healthy either way.

Two failure modes specific to the revert pattern:

- **Loops.** A revert transition is itself a transition event. Leave **"Allow rule to be triggered by other rules" unchecked**, or the rule re-triggers itself.
- **Branch scope.** Inside a branch, `{{issue}}` is the *branched* issue. Use `{{triggerIssue}}` to refer to the original. Getting this backwards is the most common failure in branched rules.

One caveat when verifying via tooling rather than the UI: the Atlassian MCP JQL search **swallows JQL errors** — a query against a non-existent field or issue type returns an empty result set rather than an error. An empty result is therefore not evidence that a field exists and nothing matched. Confirm existence with the issue-type or field metadata endpoints instead.

## Related

- The design and the reasoning behind every rule here: `demand-workflow.md`
- Click-through spec for the Jira admin, and the delivery-backlog rules §4 restates: `jira-automation-rules.md`
- Provisioning script for §2's fields, the `Demand` issue type, and §1's statuses: `provision-demand-workflow.sh`
- The thirteen capability names used by `Capability Span`: `capability-model.md`
- Single-owning-parent rule that `Ready for Build` hands off to: `../capabilities/docs/capability-domain-jira-map.md`
- Domain Epic shape created at `Ready for Build`: `../.claude/skills/domain/SKILL.md`
- Human-in-the-loop policy §5 applies: `../capabilities/agent/guidelines.md`
- Admin tracking ticket: [UIFPE-115](https://pearson.atlassian.net/browse/UIFPE-115)
