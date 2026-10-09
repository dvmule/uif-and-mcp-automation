# Onboarding — your first week on UIF

Welcome. This is the first-day path. It exists because until 2026-09-11 there wasn't one:
people found the rules by opening `CLAUDE.md` and hoping, or by tripping a CI gate.

Read this top to bottom once. It should take under an hour. Everything else in the
repository is reference you come back to — this is the only document written to be read
in order.

**Who this is for:** engineers, UX, QE, product — anyone contributing. AI coding agents
read [`AGENTS.md`](AGENTS.md) instead, then the same documents linked below.

---

## Day 1 — what this thing is, in four documents

Read these four, in this order. Do not skip to the code.

| # | Document | What it tells you | Time |
|---|---|---|---|
| 1 | [`README.md`](README.md) | What UIF is and why it exists. The 66% integration-reduction target is real, not aspirational | 15 min |
| 2 | [`CLAUDE.md`](CLAUDE.md) | The rules of the repository. The **thirteen capabilities**, the domain/stack split, and five non-negotiables | 20 min |
| 3 | [`DEFINITION-OF-DONE.md`](DEFINITION-OF-DONE.md) | **What "finished" means.** Read §0 first — it explains why three different things here share that name | 10 min |
| 4 | [`PLANNING.md`](PLANNING.md) | The programme plan — workstreams, releases, the 30 October date | skim, then return |

### The five things people get wrong first

1. **Domains are vertical, capabilities are horizontal, stacks are neither.**
   `domains/<domain>/` is a deployable business unit. `capabilities/<capability>/` is a
   standard with **no code in it, ever**. `stacks/<stack>/` — `echo`, `beacon`, `harbor`
   — are RELAY application units and are **not domains** (ADR-0009, ADR-0010). Putting
   code in `capabilities/` is the single most common structural mistake.

2. **There is no root `src/`.** All implementation code lives under `domains/` or
   `stacks/`.

3. **"Definition of Done" means three things.** A CI structural gate, each capability's
   §5 craft checklist, and the workflow rules. [§0 of
   `DEFINITION-OF-DONE.md`](DEFINITION-OF-DONE.md) disambiguates them. Get this wrong
   and you will argue past someone for ten minutes.

4. **Schema Registry first.** Contracts are registered *before* implementation, not
   after. CI checks this for some paths; reviewers check it for the rest.

5. **Never push to `main`.** Every change ships to **`develop` and `validate`**. Never
   force-push, on any branch.

---

## Day 2 — get set up and make one small change

### Tools you need to run what CI runs

CI will fail you for things you can catch in thirty seconds locally.

```
ruff            # Python lint
pytest          # Python tests
eslint          # TypeScript lint
vitest          # TypeScript tests
tflint          # Terraform lint
checkov         # Terraform security
hadolint        # Dockerfile lint
pre-commit      # runs the above, mirrors the CI gates — install it
```

VS Code is the recommended default; no editor is prohibited. The requirement isn't the
editor, it's that yours can run those checks before you push.

### Your first commit — the part that surprises everyone

**Every commit must carry a Jira key and a comment.** This is a required CI check, not a
convention:

```
UIFPE-123 #comment Add the price-list include-list to the gold pipeline config
```

Three gates run on every pull request. All three will block you:

| Gate | What it wants |
|---|---|
| **Every commit carries a Jira key and a comment** | the `UIFPE-n #comment …` form above, on *every* commit |
| **Author email matches a Jira user** | your commit author email = your Jira account email. **No `+alias` addresses** — a plus-addressed email has failed this for 62 commits historically |
| **Title and body are reviewable** | PR title `UIFPE-<n> · <what this delivers>`, and a body with `## Summary` and `## Verification` |

Fix your git identity now, before your first commit:

```bash
git config user.email "your.name@pearson.com"
```

### Then open a pull request

The [PR template](.github/PULL_REQUEST_TEMPLATE.md) prompts you for everything.
`## Verification` means the command you ran and its output — **"tested locally" is not
evidence** and a reviewer will send it back.

---

## Day 3 — how work is tracked

Jira project **UIFPE**, board **24532**. Read [`jira/uif-faq.md`](jira/uif-faq.md).

**The hierarchy:** Capability → Epic → Story / Task / Bug / Spike → Sub-task. Story,
Task, Bug and Spike are *siblings at the same level*, not a sequence.

**Things that will confuse you, all true:**

- **A sub-task cannot be put in a sprint.** It inherits its parent's sprint. If your work
  is in a sub-task and you can't find it on the board, that's why.
- **Architecture and framework items are containers, not deliverables.** UIF runs
  **AIDLC, not SDLC** — they accrue children continuously and never "finish". The test is
  whether an item has sub-tasks. Containers never go in a sprint.
- **CI moves your ticket.** A commit transitions it to In Progress; opening a PR moves it
  to Code Review. You own the move to Ready for QA. See
  [`DEFINITION-OF-DONE.md` §1](DEFINITION-OF-DONE.md).
- **Moving a card to In Progress makes you its assignee** — whoever owned it before. An
  automation rule does this, silently, 1–2 seconds later, and only for that one status. If
  you are browsing someone else's item, don't set In Progress. This is also why the
  assignee on anything that has been through CI is usually the service account rather than
  a person: **when you need the real owner, use `git log` on the files, not the Jira
  assignee.**
- **There is no QE function.** A peer reviews your work — never the author. Expect to
  spend real time reviewing, and expect that to be visible in your capacity.

---

## Day 4–5 — read your own area, then ask

Find your domain or stack and read its `README.md` and `CHECKLIST.md` (stacks) or
`DOMAIN-CHECKLIST.md` (domains). Then read the `capabilities/<capability>/guidelines.md`
for whatever you're about to touch — its §5 is the craft bar your PR will be held to.

**Where the real state of things is written down:**

- [`PMdiscrepancies.md`](PMdiscrepancies.md) — the honest internal defect list: what the
  programme has built versus what the tracking system can show. Read it. It will save
  you from assuming Jira is accurate.
- [`architecture-design/decisions/`](architecture-design/decisions/) — the ADRs. If you
  are about to argue for a design, check whether it was already decided.

**Ask early.** `CLAUDE.md` ends with *"a clarifying question costs less than a silent
wrong turn."* That is meant literally. In particular, ask before you:

- create any new top-level folder or category
- add a fourteenth capability, or a new domain
- resolve an apparent contradiction between two pieces of existing guidance

---

## Things that are true today and might not look deliberate

Current as of **2026-09-11**. If you spot one of these, it is known — not a discovery.

- **Entra ID is not yet available.** The CI identity check is disabled in both workflows
  pending delivery, so `CLAUDE.md`'s claim that a different IdP fails CI is not true
  today. Echo runs a deliberate interim auth seam. Tracked on **UIFPE-289**.
- **`domain-cd.yml` names stages that don't exist** — `-dev`, `-qa`, `-stage`,
  `-production`. The real chain is **Development → UAT → Production**. Tracked on
  **UIFPE-378**.
- **Price, Program and Stock have no Terraform at all.** Tracked on **UIFPE-409**.
- **No Jira Versions exist**, so nothing carries a `fixVersion` and releases can't be
  tracked in Jira yet.
- **Observability is deferred** pending an SRE who hasn't joined.

---

## Your first week, checked off

- [ ] Read the four Day 1 documents
- [ ] `git config user.email` set to your Pearson address, no alias
- [ ] `pre-commit` installed and passing locally
- [ ] Read `DEFINITION-OF-DONE.md`, including §0
- [ ] Found your Jira board and your first assigned item
- [ ] Read your domain's or stack's checklist, and the guidelines for one capability
- [ ] Opened one small pull request, green on all three gates, reviewed by a peer
- [ ] Asked at least one question rather than guessing

---

*Owner: framework owner. Written 2026-09-11 because three engineers and a UX lead joined
without a first-day path. Amend by pull request — if something here misled you, fixing it
is a contribution.*
