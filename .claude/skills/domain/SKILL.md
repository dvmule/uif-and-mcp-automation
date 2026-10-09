---
name: domain
description: Use this skill whenever the user wants to spin up a new UIF domain from the mould (`_domain-template/`) — creating the deployable business unit's scaffold, wiring the four-in-a-box owners (FDE/UX/QE/PM), or setting up the `git new-domain` shortcut. Trigger on "create a new domain", "spin up a domain", "stamp a domain", "git new-domain", or a request to scaffold `domains/<name>/`.
---

# domain — stamp a new UIF domain from the template

A UIF domain is a deployable, ownable business unit that builds to ONE container image. Every domain is stamped from the same mould (`_domain-template/`) via `create-domain.sh`, never hand-built — that's what keeps `domains/<a>/` and `domains/<b>/` structurally identical and lets CI/CODEOWNERS/DoD checks be generic instead of per-domain.

## The one git command

If the `new-domain` alias isn't set up yet in this clone, set it once:

```bash
git config alias.new-domain '!f() { "$(git rev-parse --show-toplevel)/create-domain.sh" "$@"; }; f'
```

Then spinning up a domain — template + four-in-a-box owners — is one command:

```bash
git new-domain <name> \
  --caps <c1,c2,...|all> \
  --owner-fde @handle --owner-ux @handle --owner-qe @handle --owner-pm @handle
```

Valid `--caps` values (opt-in, always added under `src/`): `events`, `semantics`, `knowledge-graph`, `mcp`, `a2a`, `agents`. Core (`apps`, `api`, `schemas`) is always included and doesn't need to be listed. Omit `--caps` for a core-only domain.

## What running it actually does

1. **Validates the name doesn't already exist — case-insensitively.** `create-domain.sh` checks `domains/` for a case-insensitive match (not just exact) before stamping, so `Product` and `product` can't both exist even on a case-sensitive filesystem (Linux CI runners) where a naive check would miss it. If it collides, it errors and stops — it never overwrites.
2. Copies `_domain-template/` (README, `CLAUDE.md` skill, `DOMAIN-CHECKLIST.md`) as the domain root.
3. Scaffolds `src/{apps,api,schemas}` (core, always) plus only the capability folders actually requested under `--caps` — no empty stub folders for capabilities the domain doesn't use.
4. Writes a stub `src/Dockerfile` — the single build/deploy contract every domain must fill in.
5. Scaffolds the always-present top-level work folders: `qe/`, `ui/`, `ux/`, and `infrastructure/{terraform,kubernetes,helm,observability}`.
6. Prints the exact CODEOWNERS wiring for the four owners passed in, so FDE/UX/QE/PM assignment is a copy-paste, not manual editing of `.github/CODEOWNERS`.

## The matching Jira Domain Epic (GitHub scaffold ⇄ Jira owning parent)

Every domain that exists in `domains/` has a matching **Domain Epic** in Jira (project UIFPE) — the single owning parent for all delivery work that ships in that domain, per the single-owning-parent rule (`capabilities/docs/capability-domain-jira-map.md`). Capability is always a label on child issues, never a parent. Worked examples: UIFPE-51 (`Domain: Product`) through UIFPE-111 (`Domain: Subscriptions`).

A Domain Epic follows this exact shape — create it as a companion to every `git new-domain` run, not as an afterthought:
- **Issue type:** `Epic`.
- **Summary:** `Domain: <Name>` (title case, e.g. `Domain: Orders`) — never a bare `Domain` with no name.
- **Label:** `domain:<name>` (lowercase, matching the `domains/<name>/` folder).
- **Assignee:** the `--owner-fde` handle passed to `create-domain.sh` — the FDE owns the domain's delivery, so Jira ownership mirrors GitHub CODEOWNERS ownership.
- **Description**, verbatim shape: `"The <Name> domain — a deployable business unit (domains/<name>/). Owning parent for all delivery work that ships in this domain. Capability is a label on child issues, never a parent — see capabilities/docs/capability-domain-jira-map.md and the single-owning-parent rule."`

**Writing to Jira is a shared-system action — always confirm with the user before creating or editing the Domain Epic**, the same as any other Jira write (see `pm` skill's Git/Jira Safety posture). Never leave a Domain Epic with a `null` description or a blank/placeholder summary — that state is a checked deviation, not a template.

## Workflow when invoked

1. Confirm the domain name and which capabilities it needs — don't guess `--caps`; ask if it's ambiguous (a domain only carries the capability folders it actually uses, per repo convention).
2. Confirm the four owner handles (FDE/UX/QE/PM) if not given — every domain needs all four per the Four-in-a-Box model.
3. Run the git command exactly as above. If it errors on a duplicate name, report the existing domain it collided with — don't retry with a different casing to force it through.
4. After it runs, show the printed CODEOWNERS suggestion and remind the user it's a suggestion to paste into `.github/CODEOWNERS`, not something this skill edits automatically.
5. Point at `DOMAIN-CHECKLIST.md` inside the new domain as the definition-of-done the FDE now works against.
6. Propose the matching Jira Domain Epic (exact shape above) and, on confirmation, create it — so the domain has one owning parent in Jira from day one, not as a later reconciliation.

## What this skill does NOT do

- **Doesn't invent domain names or owners.** Domain naming and ownership are product/organizational decisions, not something to default silently.
- **Doesn't edit `.github/CODEOWNERS` automatically.** It prints the suggested lines; adding them is a reviewed, deliberate edit.
- **Doesn't fill in `src/Dockerfile` or any domain logic.** The scaffold is structure only — implementation is the FDE's work, guided by `capabilities/<capability>/guidelines.md`.
- **Doesn't commit or push the new domain.** Scaffolding creates untracked files on disk; staging/committing/pushing follows the same Git Safety Protocol as `fde` — only on explicit ask.
- **Doesn't create the Jira Domain Epic without confirmation.** It's a shared-system write like any other Jira action — propose the exact fields, then create only on explicit go-ahead.
