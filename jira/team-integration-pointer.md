# UIF — Git · Jira · Confluence (team pointer)

This page is for **everyone on the Four-in-a-Box** — PM, FDE, UX Lead, QE Lead. It explains how the three systems connect and where to find the real documentation.

**Confluence is a pointer, not a copy.** Capability standards, playbooks, API guidelines, and runbooks live in **GitHub** (versioned, reviewed in PRs). Jira tracks **work**. This space links you to both — it does not duplicate them. If Confluence and GitHub disagree, **GitHub wins**.

**New to the two-axis model?** Read [`jira/uif-faq.md`](uif-faq.md) — start with **§ Why two axes (T-shaped) at all?** **AI agents:** [`AGENTS.md`](../AGENTS.md) + story [`PRD.md`](../PRD.md).

**Live wiki:** [Git ↔ Jira Integration](https://pearson.atlassian.net/wiki/spaces/UIF/pages/1261241098/Git+Jira+Integration+Claude+GitHub+Jira+Confluence) · **Space:** [UIF](https://pearson.atlassian.net/wiki/spaces/UIF/overview)

---

## The three systems

| System | Your job | Source of truth |
| --- | --- | --- |
| **GitHub** | Code, standards, CI/CD | [universal-Integration-framework](https://github.com/pearson-dnt-coe/universal-Integration-framework) (branch **validate**) |
| **Jira (UIFPE)** | Stories, epics, workflow | [UIFPE project](https://pearson.atlassian.net/jira/software/projects/UIFPE) |
| **Confluence** | Quick links and cheat-sheets | [This pointer page — **not** the full docs](https://pearson.atlassian.net/wiki/spaces/UIF/pages/1261241098/Git+Jira+Integration+Claude+GitHub+Jira+Confluence) |

**Why two axes?** See GitHub [`jira/uif-faq.md` § Why two axes (T-shaped)](https://github.com/pearson-dnt-coe/universal-Integration-framework/blob/validate/jira/uif-faq.md#why-two-axes-t-shaped-at-all).

**What Confluence deliberately omits** (read these in GitHub instead):

- Capability standards (`capabilities/*/guidelines.md`)
- API / event / schema how-tos
- Infrastructure runbooks
- Full Definition of Done checklists

---

## Start here (by role)

| Role | Open first in GitHub |
| --- | --- |
| **FDE** | [README](https://github.com/pearson-dnt-coe/universal-Integration-framework/blob/validate/README.md) → [api-playbook](https://github.com/pearson-dnt-coe/universal-Integration-framework/blob/validate/delivery-lab/blueprints/api-playbook.md) → `domains/<domain>/DOMAIN-CHECKLIST.md` |
| **PM** | [jira-workflow](https://github.com/pearson-dnt-coe/universal-Integration-framework/blob/validate/jira/jira-workflow.md) → [backlog-seed](https://github.com/pearson-dnt-coe/universal-Integration-framework/blob/validate/jira/backlog-seed.md) → [PLANNING](https://github.com/pearson-dnt-coe/universal-Integration-framework/blob/validate/PLANNING.md) |
| **QE** | Domain `qe/` + [observability capability](https://github.com/pearson-dnt-coe/universal-Integration-framework/tree/validate/capabilities/observability) |
| **Everyone** | [Jira ↔ GitHub integration](https://github.com/pearson-dnt-coe/universal-Integration-framework/blob/validate/capabilities/delivery/jira-github-integration.md) |

---

## One rule (Jira + GitHub must match)

**Parent = Domain epic. Capability = label. Never two parents.**

| Question | Answer |
| --- | --- |
| Where does code go? | `domains/<domain>/src/` |
| Jira parent for delivery work? | **Domain epic** (e.g. Product — **UIFPE-51**) |
| How to tag capability? | Labels: `capability:api`, `capability:schema`, … |
| Foundation Epic as parent? | Pre-domain platform work only |

**Example:** Story under **UIFPE-51**, labels `domain:product` + `capability:api`, code in `domains/product/src/api/`.

Full map: [capability-domain-jira-map.md](https://github.com/pearson-dnt-coe/universal-Integration-framework/blob/validate/capabilities/docs/capability-domain-jira-map.md)

---

## Domain epics (UIFPE)

| Domain | Label | Repo path | Epic |
| --- | --- | --- | --- |
| Product | `domain:product` | `domains/product/` | **UIFPE-51** |
| Customer | `domain:customer` | `domains/customer/` | **UIFPE-52** |
| SKU | `domain:sku` | `domains/sku/` | **UIFPE-53** |
| Finance | `domain:finance` | `domains/finance/` | **UIFPE-54** |
| HR | `domain:hr` | `domains/hr/` | **UIFPE-55** |
| Legal | `domain:legal` | `domains/legal/` | **UIFPE-56** |
| Identity | `domain:identity` | `domains/identity/` | **UIFPE-57** |
| Orders | `domain:orders` | `domains/orders/` | **UIFPE-109** |
| Tax | `domain:tax` | `domains/tax/` | **UIFPE-110** |
| Subscriptions | `domain:subscriptions` | `domains/subscriptions/` | **UIFPE-111** |

---

## Git ↔ Jira (FDE daily loop)

### Branch and PR

```
feature/UIFPE-<n>-<short-description>
```

PR title example: `UIFPE-39 · Product Data API`

The PR itself is gated too — `.github/workflows/pull-request.yml` fails it unless the title carries the key **and** names the change, and the body has a `## Summary` (what changed and why) and a `## Verification` (how you know it works). `updates` is not a title and an empty body is not a description; the PR is the only thing the PM and QE Lead read. The template gives you the headings — fill them in, and fix the title or body **on the PR**, no new commit needed.

### Smart Commits — **every commit, not just the last one**

```
UIFPE-<n> #comment <what you did and why> #<transition>
```

The `UIFPE-<n>` key and `#comment` are required on **every** commit — `.github/workflows/smart-commit.yml` fails the PR without them. The `#<transition>` token is optional per commit; use it at the lifecycle points below.

The comment has to be a description, not a marker: `#comment wip` is rejected (minimum 20 characters and 4 words). It is read by people who will never open the diff. **Don't write it from scratch — ask GitHub Copilot or Claude to draft the message from your staged changes, then check it says what you did.** You remain the commit author; credit the assistant with a `Co-Authored-By:` trailer.

| When | Example | Result |
| --- | --- | --- |
| Start work | `UIFPE-39 #comment Starting handler #in-progress` | Comment + In Progress |
| PR ready | `UIFPE-39 #comment PR opened #code-review` | Comment + Code Review |
| Merged | `UIFPE-39 #comment Merged #ready-for-qa` | Comment + Ready for QA |
| Blocked | `UIFPE-39 #comment Waiting on access #blocked` | Comment + Blocked |

**Requirements:** GitHub for Jira app connected · Smart Commits enabled · **your commit-author email matches your own Jira account exactly.**

### The three checks are required — read this first

On **`develop`** and **`validate`**, these are required status checks with no administrator exemption:

| Check | Fails when |
| --- | --- |
| Author email matches a Jira user | A commit's author email is plus-addressed or not `@pearson.com` |
| Every commit carries a Jira key and a comment | A commit has no `UIFPE-<n>`, or no descriptive `#comment` |
| Title and body are reviewable | The PR title has no key or names nothing; the body has no `## Summary` or `## Verification` |

Two things change as a result:

- **A red check blocks the merge.** It is no longer advisory.
- **You cannot push directly to `develop` or `validate`.** The push is refused (`GH006`). Every change goes through a pull request — a one-line doc fix included.

Set up the local hooks below and none of this bites you: the same rules run on your machine, at the point where fixing them costs nothing.

### Do this once, before your first commit

```
git config core.hooksPath .githooks
git config user.name  "<Your Name>"
git config user.email <your-exact-Jira-account-email>
```

The first line installs the local hooks that **refuse a commit** that isn't a Smart Commit, and refuse a push containing one. Without it you find out from a red pull request instead, after the history already needs rewriting.

Jira runs every smart-commit action **as the commit author**, matching the address literally. If it doesn't match a licensed Jira user, Jira **discards the command without telling you** — no comment, no transition, no error. A `+alias` suffix (`first.last+something@pearson.com`) matches nothing.

Every engineer commits under their own address. Never a shared or team address, never a colleague's, never one for an agent — AI-assisted commits are authored by the engineer who ran the session, with the agent in a `Co-Authored-By:` trailer. A borrowed identity puts your work on the Jira issue under someone else's name and makes `git blame` lie.

`.github/workflows/commit-identity.yml` fails your pull request if any commit in it breaks this. Check yourself first:

```
git log --no-merges --format='%ae' -20
```

Full reference: [jira-github-integration.md](https://github.com/pearson-dnt-coe/universal-Integration-framework/blob/validate/capabilities/delivery/jira-github-integration.md)

### Phase 2 workflow (optional backup — admin-owned)

`.github/workflows/jira-transition.yml` may return **401** from GitHub runners until **Atlassian admin** fixes IP allowlist. **Do not rely on it** — Smart Commits are the working path today.

---

## Jira Automation (admin — one-time)

Status changes do **not** notify people automatically. Jira admin configures **UIFPE → Automation**:

| Transition to | Notify |
| --- | --- |
| **Code Review** | PR reviewer(s) |
| **Ready for QA** | Domain QE Lead |

---

## On every PR (GitHub CI)

| Step | What runs |
| --- | --- |
| 1 | Jira key from branch name (Smart Commits) |
| 2 | Changes under `domains/<domain>/` → Domain CI |
| 3 | Structural checks: checklist, schemas, Dockerfile, `qe/` |
| 4 | API/Helm → Entra ID + Keeper gate |

Pilot domain with code today: **domains/product/**.

---

## Still open (not blocking engineers)

| Item | Owner |
| --- | --- |
| `jira-transition.yml` **401** | Atlassian admin |
| Jira Automation notification rules | Jira project admin |
| `main` carries none of the three gate workflows, so a PR opened against it is unchecked | Repo admin |
| The ~59 commits authored from a `+pearson` alias will never backfill their Jira comments — those issues need their status and comments set by hand once | Domain FDEs |

---

## Quick links

- [UIF Confluence space](https://pearson.atlassian.net/wiki/spaces/UIF/overview)
- [GitHub repo (validate)](https://github.com/pearson-dnt-coe/universal-Integration-framework/tree/validate)
- [UIFPE Jira](https://pearson.atlassian.net/jira/software/projects/UIFPE)
- [Jira ↔ GitHub standard](https://github.com/pearson-dnt-coe/universal-Integration-framework/blob/validate/capabilities/delivery/jira-github-integration.md)

*Last updated: 2026-08-05 · Aligned to GitHub branch `validate`*
