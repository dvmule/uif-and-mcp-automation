---
name: confluence
description: Use this skill whenever the user wants to regenerate, refresh, or sync the UIF Confluence pointer page with what has changed in the repository — new PRs merged, new CI gates added, updated conventions. Trigger on "update the Confluence page", "sync docs to Confluence", "regenerate the pointer page", or "does Confluence reflect the latest repo state".
---

# confluence — keep the Confluence pointer page in sync, without silent drift

The UIF space's pointer page is deliberately thin — it links out to the repo and to Jira rather than duplicating content, per the repo's "link, don't duplicate" convention. This skill's job is to notice when the repo has moved past what the page says, draft the update, and let the user approve the publish — never to publish unattended.

## Context

- Confluence space **UIF** (id `1260880027`), overview: [UIF space](https://pearson.atlassian.net/wiki/spaces/UIF/overview), same cloudId as Jira (`f00fd716-e005-41f9-98ae-581d906c2040`).
- Pointer page: "Git ↔ Jira Integration (Claude · GitHub · Jira · Confluence)", id `1261241098`, child of the space homepage.
- **Source draft in repo:** `jira/confluence-pointer-draft.md` — start here when refreshing the page; diff it against live Confluence and the current `validate` branch state.
- **Publish wiki (preferred):** `jira/confluence-pointer-live-paste.md` — **Cmd+A paste** to Confluence; `confluence-pointer-publish-wiki.md` for edits. **Live page verified 2026-08-06**.
- **Publish HTML:** `jira/confluence-pointer-publish.html` — full page body for page id `1261241098`.
- **Partial refresh:** `jira/confluence-pointer-missing-sections.html` — only if live page regresses to Smart Commits-only.
- Content format used successfully before: HTML via `createConfluencePage`/`updateConfluencePage`, panels as `<div data-type="panel-info|warning">`, tables for structured content (Smart Commit cheat-sheet, transition tables, setup checklists).

## Workflow

1. **Fetch the current page** with `getConfluencePage` and read what it currently claims.
2. **Diff against the repo's actual current state** — recently merged PRs, new CI gates (e.g. the Entra ID + Keeper gate), new skills, changed conventions. Memory (`MEMORY.md`) and `git log`/PR history are the fastest way to find what's changed since the page's last update.
3. **Draft the updated HTML**, preserving the page's existing structure and thin-pointer style — add/update sections, don't turn it into a duplicate of the repo docs.
4. **Show the diff to the user and get explicit confirmation** before calling `updateConfluencePage` — publishing to a space visible beyond this session is a shared-system action, same bar as a Jira write or a `git push`.

## What this skill does NOT do

- **Decide what counts as "significant enough to document"** — flag candidate changes, let the user choose what goes on the page.
- **Publish without confirmation** — no exception, even for what looks like a trivial wording fix.
