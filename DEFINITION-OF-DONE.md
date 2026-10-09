# Definition of Done

This document says what each status on the UIFPE board means, who moves it, and what
must be true before it moves. It exists because the board is currently unreadable: on
2026-09-10 UIFPE-367 ran the full `Ready for QA → In Progress → Code Review → Ready for
QA` cycle **six times between 13:29 and 22:13** — eighteen status changes in eight hours
and forty-four minutes — then six more times on 09-11; UIFPE-184 entered `In Progress`
**seven times on 2026-09-02 alone**. Nobody can plan from that, and nobody can tell what
"Ready for QA" means today.

*(Counts corrected 2026-09-11 from `expand=changelog`. This paragraph first said "nine
times in ten hours" and "184 six times in one night" — both were estimates, and both were
wrong. The figures above are row counts.)*

Read this with [`PLANNING.md`](PLANNING.md) (what we are building) and
[`PMdiscrepancies.md`](PMdiscrepancies.md) (what the tracking system cannot currently
show). This is the third piece: what "finished" means.

**Scope.** Applies to every Story, Task, Bug and Spike in the UIFPE project. Epics and
Capabilities are containers — they are not "done", they are closed when empty (see
[§6](#6-containers-are-not-done)).

---

## 0. Read this first — "Definition of Done" means three things here

This is the single most common way to get confused in this repository, and it catches
new joiners hardest. **Three different things carry this name.** They do not compete;
they answer different questions.

| # | Where | What it actually means | Enforced by |
|---|---|---|---|
| 1 | **`Definition-of-done gate`** in `domain-ci.yml` and `stacks-ci.yml` | **Do the required files exist?** `CHECKLIST.md`, `qe/`, `src/schemas/`, and `src/Dockerfile` *or* `src/NO-IMAGE.md` | CI, blocking |
| 2 | **§5 of each `capabilities/*/guidelines.md`**, each `DOMAIN-CHECKLIST.md`, each stack `CHECKLIST.md` | **Does this artefact meet its capability standard?** e.g. "is this API contract registered before implementation" | human PR review |
| 3 | **This document** | **What do the board statuses mean, and when is a work item finished?** | human, per §1's ownership table |

Roughly: **1 is structure, 2 is craft, 3 is workflow.** If you are asking *"can I move
this ticket to Ready for QA?"* — that is this document, §3. If you are asking *"is my
API good enough?"* — that is the API capability's guidelines. If CI failed with
"missing `qe/`" — that is gate 1, and this document has nothing to say about it.

> **Naming decided by the framework owner, 2026-09-11: this file keeps the name.**
> "Definition of Done" is standard delivery vocabulary that anyone joining already
> recognises, and a recognisable name is worth more than an unambiguous one — which is
> why §0 above exists instead.
>
> **"Ways of working" is reserved and is not a synonym for this.** It names a separate,
> wider operating-structure document at closure level. **Do not merge the two, and do not
> rename this file into it** — one document answering both questions would answer neither.

---

## 1. The rule that fixes the flapping

**CI owns two transitions. Humans own the rest. They must not fight.**

Every commit carrying a Jira key fires `jira-transition.yml`, which transitions the
issue as `serv.GIPBBCICDUser`. That is why status oscillates: a human sets Ready for QA,
and the next commit drags it back to In Progress. UIFPE-367 entered `In Progress`
**twelve times** across 09-10 and 09-11.

**And a Jira Automation rule turns each of those round trips into a change of owner.** It
fires on any transition whose destination is `In Progress` — and only that destination —
then assigns the issue to whoever performed the transition, 1.3–1.7 seconds later. Proven
on UIFPE-367: transition `13:29:10.849` → reassignment `13:29:12.191`. Transitions to
Code Review, Ready for QA, QA In Progress, Done and Blocked do not touch the assignee.

> **This applies to people, not just CI.** Move a card into In Progress — on the board, in
> the issue view, anywhere — and you become its assignee, whoever owned it before, with no
> prompt. If you are picking an item up, that is what you want. If you are triaging
> someone else's, set a different status or expect to hand it back. Proven on UIFPE-36,
> 2026-08-20 `23:51:27` → `23:51:29`: a `To Do → In Progress` transition moved the issue
> off Vaibhav Yenorkar without anyone intending it.

| Transition | Owner | Trigger |
|---|---|---|
| `To Do → In Progress` | **CI** | first commit carrying the key |
| `In Progress → Code Review` | **CI** | pull request opened |
| `Code Review → Ready for QA` | **human (author)** | PR merged **and** §3 satisfied |
| `Ready for QA → QA In Progress` | **human (reviewer)** | reviewer picks it up |
| `QA In Progress → Done` | **human (reviewer)** | §4 satisfied |
| anything `→ Blocked` | **human** | §5 |

**CI must never move an issue that has already reached Ready for QA or beyond.** Until
`jira-transition.yml` enforces that, the author re-sets the status after the final
commit and the oscillation is treated as noise, not signal.

> **The contradiction this document used to carry, and how it was resolved.** The table above
> gives `To Do → In Progress` to CI, and §2 below requires a human assignee before an item is
> worked. While the automation rule assigned on *every* entry to In Progress, the first
> destroyed the second on every ticket: the moment CI started an item, a service account owned
> it.
>
> **Decided 2026-09-11 by the framework owner: condition the rule to ignore
> `serv.GIPBBCICDUser`.** The rule keeps working for people — whoever moves a card into In
> Progress owns it, which is what you want when someone picks work up — and stops firing for
> CI. **CI keeps setting status; it stops taking ownership.** Both rules above then hold
> together, and no wording in this document needs to change.
>
> The exact condition to add is specified in
> [`jira/jira-automation-rules.md`](jira/jira-automation-rules.md), under "Rules that already
> exist in the live instance", with the verification to run after the change.
>
> **Until that condition is live, the assignee is still unreliable on anything that has been
> through CI — treat the git commit author as the owner.** That is how ownership was
> established throughout the 2026-09-09/11 reconciliation. One consequence is already usable:
> because the rule fires *only* on entry to In Progress, **an item sitting at Ready for QA or
> beyond can be reassigned now and the change will stick** until a keyed commit drags it back.

## 2. Ready to start (entry criteria — before `In Progress`)

An item may not be worked until all of these hold. A sprint must not accept an item
that fails them.

- [ ] It has an **assignee who is a person**, not `serv.GIPBBCICDUser`.
- [ ] It has a **size** (T-shirt, `customfield_10044`).
- [ ] Its **acceptance criteria are written** on the issue, as statements that can be
      shown true or false — not a title restated.
- [ ] It has **one owning parent** per the single-owning-parent rule, and that parent is
      **not closed**. (UIFPE-58 sat under a Done epic until 2026-09-10.)
- [ ] Any dependency is recorded as a **Jira link**, not prose.

## 3. Ready for QA (exit criteria for the author)

The author moves it here only when **all** of the following are true.

- [ ] PR merged to `develop` **and** `validate`. Never `main`.
- [ ] All three required checks green: **author email matches a Jira user**, **every
      commit carries a Jira key and a comment**, **title and body are reviewable**.
- [ ] Tests exist for the change and run in CI — not run only on the author's machine.
- [ ] Every acceptance criterion from §2 is **individually evidenced in a comment**:
      the command run, the output, or the file and line. "Tested locally" is not evidence.
- [ ] Any contract touched (OpenAPI / AsyncAPI / JSON Schema) is **registered**, not just
      edited.
- [ ] Documentation that the change makes wrong has been corrected in the same PR.
- [ ] Anything discovered and *not* done is raised as its own issue and linked.

## 4. Done (exit criteria for the reviewer)

**There is no QE function.** Until there is, a **peer reviews — never the author**. Price
this in: a reviewer is not building while reviewing.

- [ ] A peer other than the author has checked the §3 evidence and says so in a comment,
      naming themselves.
- [ ] The change has been **observed working in the Development environment** — not
      inferred from a green build.
- [ ] **Resolution is set.** No UIFPE item closed to date carries one, so "why did this
      close" is not queryable. Done = delivered. Cancelled = will not be done, with the
      survivor named if it was superseded.
- [ ] The closing comment states what shipped, in one paragraph a reader outside the
      team can follow.

## 5. Blocked

- [ ] `Blocked` requires a comment naming **what** blocks it, **who** can unblock it, and
      **when it was raised**. A Blocked item with no named person is not blocked, it is
      abandoned.
- [ ] Blocked items are reviewed at every standup. Three consecutive days blocked
      escalates to the framework owner.

## 6. Containers are not "done"

UIF runs **AIDLC, not SDLC**: architecture and framework items are iterative containers
that accrue children continuously and never reach a finish line.

- A container is any item **with sub-tasks**. That is the test.
- **Containers never enter a sprint.** A container in a sprint carries forward forever
  and destroys velocity.
- A container closes only when every child is closed and no more are expected — as
  UIFPE-89 did on 2026-09-10, with all three children Done or Cancelled.
- The sprintable unit is always a **leaf**. Where real work sits in a sub-task, promote
  it to a Story beside the container, because **Jira will not assign a sprint to a
  sub-task**.

## 7. Sprint-level Definition of Done

A sprint is done when:

- [ ] Every committed item is Done, or explicitly carried with a reason recorded.
- [ ] The sprint goal is assessed **met or not met** — in writing, not implied by the
      burndown.
- [ ] Velocity is recorded. Sprint 2 closed with zero points and no baseline exists;
      Sprint 3 is the first chance to create one.
- [ ] Items are **not removed from a closed sprint**. Sprint 2's membership was reduced
      from 9 to 7 after it closed, which destroyed its own record.

## 8. What this document does not claim

CI enforces structural presence and the three commit gates. **It does not enforce most
of the above** — §3 and §4 are held by human review.

One live gap, found 2026-09-11: the **Entra ID check is disabled in both CI workflows**
— `domain-ci.yml:112` and `stacks-ci.yml:153`, both reading "disabled (PR #21,
entraid_remove)". Only the Keeper half still fires. So the identity mandate stated in
`CLAUDE.md` — that a domain authenticating against a different IdP fails CI — is **not
enforced today**.

**This is a tracked external dependency, not a defect.** Confirmed by the framework
owner 2026-09-11: Entra ID is **in progress and not yet received**. The check was
disabled because there is nothing yet to check against, and Echo's interim HMAC scheme
(`stacks/echo/ui/src/lib/auth.ts`) is a deliberate seam pending delivery — its own
header says so: *"When Pearson SSO lands, `login()` is replaced by a redirect to the
IdP."* Tracked on **UIFPE-289**; gates Gate 5 of **UIFPE-372**. Re-enabling the check is
a step in receiving Entra ID, not a separate remediation.

**Correction to an earlier version of this document:** it claimed stacks were ungated
because `domain-ci.yml` only triggers on `domains/**`. That is true of that workflow but
the conclusion was wrong — **`.github/workflows/stacks-ci.yml` exists**, triggers on
`stacks/**`, and carries its own `Definition-of-done gate` (line 92) and
`Entra ID + Keeper gate` (line 148). Stacks are gated. The split is deliberate per
ADR-0009 and ADR-0010.

Do not assume a rule here is mechanically checked unless a named check exists for it.

---

*Owner: framework owner. Raised as part of the programme reconciliation, 2026-09-11.
Amend by pull request; do not amend silently.*
