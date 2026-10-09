---
name: pm
description: Use this skill whenever the user (a Product Manager / Product Owner) wants to create, parent, or audit Jira work items in the UIFPE project for the Unified Integration Fabric (UIF) — creating a Capability, Foundation Epic, Story, Task, Sub-task, Spike, or Bug; parenting a Story under the right Capability; checking that delivery work traces back to a Capability; or scaffolding an already-decided JPD Initiative against jira/jpd-alignment.md's template. Trigger this whenever the user mentions "create a story", "file a bug", "add an epic", "track this in Jira", "UIFPE", a capability name (API, Event, Data, Schema, Semantics, Knowledge Graph, MCP, A2A, Agent, Infrastructure, Security, Observability, Delivery, Architecture & Design), "scaffold this Initiative", "draft the Initiative fields", or asks whether the backlog is properly traced — even if they don't say "Jira" explicitly.
---

# pm — Jira trace for UIF Product Owners

UIF's whole point is that delivery work traces cleanly, with exactly **one owning parent per issue** — never two: **JPD Initiative → Jira Capability (label only) / Domain epic (label `domain:<name>`) → Story → Task/Sub-task** (plus Spike, Bug as siblings of Story). Capability is *always* a label, never a parent, for delivery work. This skill does the mechanical part of that trace — finding the right parent, applying the right label, creating the issue — so the PM's time goes into deciding *what* to build, not *where it lives in Jira*.

## Context to load before acting

- Site: `pearson.atlassian.net`, cloudId `f00fd716-e005-41f9-98ae-581d906c2040`, project **UIFPE** (id 15940).
- The 14 capabilities (use these names exactly, they are the `capability:<name>` label): Architecture & Design, API, Event, Data, Schema, Semantics, Knowledge Graph, MCP, A2A, Agent, Infrastructure, Security, Observability, Delivery.
- **Two kinds of parent — pick the right one, never both:**
  - **Domain epic** (label `domain:<name>`, no parent of its own — Domain epics are top-level) is the owning parent for any Story that ships code: UIFPE-51 Product, 52 Customer, 53 SKU, 54 Finance, 55 HR, 56 Legal, 57 Identity, 109 Orders, 110 Tax, 111 Subscriptions. This is the common case.
  - **Foundation Epic** (child of its Capability) is the owning parent *only* for pre-domain platform groundwork — work that isn't shipping in a specific domain yet (e.g. standing up the schema registry itself, publishing the CI template). Once that groundwork lands and a domain starts consuming it, new work parents to the Domain epic instead.
  - Either way, add the relevant `capability:<name>` label — it's how the work still traces to a Capability without being a second parent.
- Definitive model: `jira/capability-model.md` (capabilities) and `PLANNING.md` "Jira model" (parenting rule). If unsure which capability a request belongs to, most real work crosses more than one — name all of them, pick the primary one for the label, and mention the others in the description.

## Workflow

1. **Classify.** Identify which domain the work ships in (→ Domain epic) and which Capability(ies) it draws standard from (→ label). If it's pre-domain platform groundwork with no shipping domain yet, the parent is the Foundation Epic instead — say so explicitly, don't default to it.
2. **Find the parent.** For a Domain epic: `project = UIFPE AND labels = "domain:<name>" AND issuetype = Epic`. For a Foundation Epic (pre-domain groundwork only): `project = UIFPE AND labels = "capability:<name>" AND issuetype = Epic`. Never guess an issue key.
3. **Draft, don't create yet.** Compose the issue: type, summary, description, `labels: ["capability:<name>"]`, one `parent` (Domain epic for shipping work, Foundation Epic for pre-domain groundwork — never both). Show this draft to the user in chat.
4. **Confirm before writing.** Creating/editing a live Jira issue is a shared-system action — always get explicit confirmation before calling `createJiraIssue`/`editJiraIssue`, even if the user asked you to "create a story" in the same breath. A quick "here's the draft, shall I create it?" is enough; don't over-ask on routine repeats once the user has confirmed the pattern in-session.
5. **Create and report back** the issue key, so the user can link it in a commit/branch (`fde` picks up from there).

## Trace-gate audit (on request: "check the backlog", "any orphaned stories")

Two checks, not one:
1. **Orphaned:** `project = UIFPE AND issuetype = Story AND parent is EMPTY` (adjust for Task/Spike/Bug). Report any hits — a Story with no parent at all breaks the model.
2. **Misparented:** a Story that ships code but is still parented to a Foundation Epic instead of its Domain epic. There's no single JQL for this (it needs reading each Story's content to tell if it's actually pre-domain groundwork or shipping work) — when auditing, check each Foundation-Epic-parented Story against whether a domain now actually consumes it; if so, flag it for reparenting to the Domain epic.

## JPD Initiative scaffolding (mechanical only — the judgment already happened)

This fires only after the Product Owner and Engineering Owner have already decided an Idea/Opportunity is worth becoming an Initiative (`jira/jpd-alignment.md`) — never on its own initiative, and never as a way to answer "is this worth pursuing." That question stays theirs, staffing gap or not (see "What this skill does NOT do" below — unchanged by this section).

Given a decided Initiative, scaffold it against `jpd-alignment.md`'s template:

1. **Draft the four fields** — What this Initiative is / Potential Benefits / Linked Domain Epic(s) / Capability label(s) exercised — from what the Owner tells you, not invented.
2. **Check every cited Domain Epic actually exists**: `project = UIFPE AND issuetype = Epic AND key = <cited key>`. Never guess a key; if none exists yet, say so rather than filling one in.
3. **Show the draft as a diff against `jpd-alignment.md`** — same draft-then-confirm rule as creating any Jira issue (step 4 above). Never write the file directly.

## Related: how the issue you create gets updated and who finds out

Once a Story exists, developer commits carry it forward via Smart Commits: `UIFPE-<n> #comment <what changed> #<transition>` (`capabilities/delivery/jira-github-integration.md`). The `#comment` half is what posts visible progress on the issue itself — a commit with only `#<transition>` moves the status but leaves no record of *why*, which matters if you're auditing progress from Jira alone. Each hand-off (`Code Review`, `Ready for QA`) should be backed by a Jira Automation rule that notifies the reviewer/QE Lead — that's Jira admin config (project settings → Automation), not something this skill or the repo enforces; check it exists rather than assuming it does.

## What this skill does NOT do (stays with the PM)

- **Prioritization and roadmap sequencing** — the skill doesn't decide what's next.
- **Acceptance-criteria wording** — content of the Story is the PM's call; the skill only wires structure (parent/label).
- **Whether an Initiative is worth pursuing (JPD)** — WHY is never automated.

If asked to do any of these, hand the judgment back to the user rather than guessing.
