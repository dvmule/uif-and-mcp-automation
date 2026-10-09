# Copy inventory — Schema Registry UI

**Status:** awaiting sign-off · **Raised:** 2026-09-22 (UIFPE-507)
**Updated:** 2026-09-23 — the schema-update flow (Update schema → publish a new
version) added strings; they are marked `(new)` and are all invented copy.
**Scope:** every user-facing string in `stacks/apicurio/ui/src`.

## Why this exists

The brief for this redesign said: *"Ask me before inventing copy. All strings
in the designs are final."* A great deal of copy has since been written that
was **not** in the designs — failure states, empty states, placeholder chrome,
and caveats about data the registry cannot vouch for. The designs cover the
happy path; almost everything below covers what happens when it does not hold.

Nothing here has been changed pending this review. Mark up the **Decision**
column — `ok`, or replacement text — and the changes follow from that.

## How provenance was established, and what it cannot tell you

Neither `UX-REDESIGN-SPEC.md` nor `UX-REDESIGN-PLAN.md` records any UI string,
so **there is no written record of which copy came from the design handoff.**
That is worth fixing separately; it means a reviewer cannot tell design copy
from invented copy by reading the repo.

What can be established is *when* each string entered the codebase. The
**First seen** column is the earliest commit whose diff changes the number of
occurrences of that string under `src/`, found with `git log -S`. Two things
follow, and neither is a claim about the designs:

- A bare hash is a commit from the current working session.
- `(pre)` is a commit from before it.
- `(new)` is a string added by the schema-update flow (Update schema → publish a
  new version) and not yet in any commit when this was written. Every `(new)`
  string is invented copy by definition: updating a schema from the UI is not a
  flow the designs cover, so there was nothing to take copy from.

A string that merely moved between files nets no occurrence change, so a
wholesale file rewrite does not re-date the strings that survived it.

## The strings that make a claim — these need a decision

The rest of this document is labels. These fifteen assert something, and are
where a wrong string does damage rather than just reading oddly.

### 1. The header identity chip — asserts a feature that does not exist

| Where | String |
|---|---|
| `AppShell.tsx` | `Not signed in` |
| `AppShell.tsx` | `Sign-in pending` |
| `AppShell.tsx` | `Sign-in is not available yet` (tooltip) |
| `AppShell.tsx` | `Notifications — not available yet` (tooltip) |

You asked for "Avatar, name, role" and a notification bell as placeholders, so
the chip itself is authorised. The wording is not, and `Sign-in pending`
states that a sign-in flow is coming — which nothing in this stack is
building. There is no auth in the BFF and no ticket for one.

**Recommendation:** keep `Not signed in`, which is true, and drop
`Sign-in pending` rather than replace it. The tooltips are accurate and can
stay.

### 2. The registry table footer — derived numbers with connective prose

| Where | String |
|---|---|
| `SchemaTable.tsx` | `{n} of {m} domains shown · {grouped by …, }sorted by {…}` |
| `SchemaTable.tsx` | `Show all {n}` |
| `SchemaTable.tsx` | `{n} of {m} subjects loaded` |

All three figures are computed, never hardcoded — the prototype's "3 of 8" was
a literal. `Show all {n}` clears whatever is narrowing the list; it does not
fetch more.

**Recommendation:** ok as written. Flagged because the prose around the
numbers was mine.

### 3. Failure states — none of these were in the designs

| Where | String |
|---|---|
| `SchemaDetail.tsx` | `Could not load services` |
| `SchemaDetail.tsx` | `Who produces and consumes this schema is unknown, not none. Nothing here says it is safe to change.` |
| `SchemaTable.tsx` | `Could not load the compliance list` |
| `SchemaTable.tsx` | `This view is built from that list, so an empty table here means unknown, not none.` |
| `SchemaListPage.tsx` | `Only the first {n} of {m} subjects are loaded, so a match may exist outside them.` |
| `ErrorBoundary.tsx` | `This page stopped working` / `The registry stopped working` |
| `ErrorBoundary.tsx` | `Nothing was changed. The registry itself is unaffected — …` |

The headings are not new. `Could not load services` is the **same string**
the services list has carried since the stack migration (`37e9b53`), and
`Could not load schema` and `Could not load portfolio` predate this session
too — so the failed-load heading style is established copy, not my invention.
The explanatory second line of each is mine, and each exists because the state
it describes was previously rendered as a fact:
"No services produce this schema" on a failed CMDB query, "No schemas match"
on a failed compliance query, and a blank white page on a render error.

**Recommendation:** ok, but the phrasing is yours to set — these are the
sentences a reader sees at the moment they most need to trust the tool.

### 4. The publish form's JSON gate — rewritten, not invented

| Where | String |
|---|---|
| `SchemaPublishForm.tsx` | `Schema body is not valid JSON. It would register with no metadata block, which is what makes a schema non-compliant.` |

This replaced `Schema body is not valid JSON. The registry cannot parse it and
will reject it.` The second half of that was false: the registry accepts the
body and silently writes no metadata block. The replacement borrows its
phrasing from the type-override warning already on the same form.

**Recommendation:** ok — the previous string was incorrect, so reverting it is
not an option. The wording is open.

### 5. The schema-update flow — a surface the designs do not cover

The designs cover publishing a schema, not updating one. Every string in this
flow is therefore invented, and four of them make a claim rather than label a
control.

| Where | String |
|---|---|
| `SchemaEditPage.tsx` | `The body and the governance values below are what the registry holds now. Both are rewritten on publish — the metadata block is built from this form, not merged into what is already there — so leave anything you do not mean to change alone.` |
| `SchemaPublishForm.tsx` | `A change to the body or to governance` (checklist line) |
| `SchemaPublishForm.tsx` | `This needs a change to the body or to governance.` (why Publish is refused) |
| `SchemaPublishForm.tsx` | `The subject and the group are what identify the artifact, so a new version cannot change them. Publish under a different subject to register a separate schema.` |
| `SchemaEditPage.tsx` | `This subject carries no compatibility rule, so the registry will not check this version against v{n}. A removed or renamed field registers without complaint and breaks its consumers at runtime — check the Services tab for who reads this schema before you publish.` |

All five are true of the implementation and each is load-bearing:

- The metadata block is **rebuilt** from the form by `inject_metadata`, which
  assigns `body["metadata"]` outright rather than merging. A reader who assumes
  otherwise will erase a classification by editing a field.
- An unchanged submit is refused because `ifExists=FIND_OR_CREATE_VERSION`
  finds the version the registry already holds and still answers
  `registered: true`. Allowing it would report a success that published
  nothing.
- Subject and group are the artifact's identity, so the form fixes them.
- The compatibility warning follows from a fact the subject page already
  states as `Compatibility · not set`, and that **0 of the registry's subjects
  have a rule**. Apicurio rejects an incompatible version only when the
  subject carries one, so on this registry nothing does reject it. This is the
  strongest claim on the page and the one most worth a reviewer's attention:
  it says a breaking change will be accepted. It is shown only when
  `compatibility` is null, so it disappears by itself once rules are set.

What a reviewer should decide is the wording, not the facts. The alternative to
the long intro paragraph is saying nothing about the rewrite, which leaves the
erasure above as a trap.

## Full inventory

Grouped by the surface a reader sees. `(pre)` marks a string introduced before
the current session.

### App shell — nav, header

`src/components/layout/AppShell.tsx`

| String | First seen | Decision |
|---|---|---|
| `Not signed in` | aeefab4 |  |
| `Notifications are not available yet` | aeefab4 |  |
| `Notifications — not available yet` | aeefab4 |  |
| `Sign-in is not available yet` | aeefab4 |  |
| `Sign-in pending` | aeefab4 |  |
| `© 2026 Pearson. All Rights Reserved. — v0.1.0` | b7cd84f |  |

### Error boundary

`src/components/layout/ErrorBoundary.tsx`

| String | First seen | Decision |
|---|---|---|
| `Back to the registry` | ce7f2de (pre) |  |
| `Reload the page` | ab8a0fa |  |
| `The registry stopped working` | ab8a0fa |  |
| `This page stopped working` | ab8a0fa |  |

### Glance cards

`src/features/schemas/components/RegistryOverview.tsx`

| String | First seen | Decision |
|---|---|---|
| `Couldn’t load` (written `Couldn&rsquo;t` in source) | bfdd662 (pre) |  |

### Header search

`src/features/schemas/components/RegistrySearch.tsx`

| String | First seen | Decision |
|---|---|---|
| `Search schemas` | d8db36e (pre) |  |
| `Search subject, domain, owner…` | 37e9b53 (pre) |  |

### Header sync indicator

`src/features/schemas/components/SyncIndicator.tsx`

| String | First seen | Decision |
|---|---|---|
| `Syncing…` | 37e9b53 (pre) |  |

### Identity note

`src/features/schemas/components/IdentityNote.tsx`

| String | First seen | Decision |
|---|---|---|
| `Owned by me` | 37a31d2 |  |

### Publish form

`src/features/publish/components/SchemaPublishForm.tsx`

| String | First seen | Decision |
|---|---|---|
| `A 200 is not a success` | d9c5151 |  |
| `A data classification` | d9c5151 |  |
| `A schema body that parses as JSON` | d9c5151 |  |
| `A subject` | d9c5151 |  |
| `An owning service the CMDB can resolve` | d9c5151 |  |
| `A change to the body or to governance` | (new) |  |
| `Before you publish` | d9c5151 |  |
| `Before you publish a new version` | (new) |  |
| `Publish new version` | (new) |  |
| `The subject and the group are what identify the artifact, so a new version cannot change them. Publish under a different subject to register a separate schema.` | (new) |  |
| `This needs {the missing gates}.` | (new) |  |
| `based on v{n}` | (new) |  |
| `Choose a service…` | bc10707 (pre) |  |
| `Data classification` | 37e9b53 (pre) |  |
| `Detect automatically` | bc10707 (pre) |  |
| `Field name, then Enter` | bc10707 (pre) |  |
| `Loading services…` | 37e9b53 (pre) |  |
| `Publish schema` | bc10707 (pre) |  |
| `Publishing…` | bc10707 (pre) |  |
| `Rarely changed.` | d9c5151 |  |
| `Remove ${f}` | bc10707 (pre) |  |
| `Schema body` | 37e9b53 (pre) |  |
| `Schema body is not valid JSON. It would register with no metadata block, which is what makes a schema non-compliant.` | dd5ce7f |  |
| `Service list unavailable` | 26bd653 (pre) |  |
| `Subjects in this registry look like domain.entity.event-value. Yours does not, which is allowed but unconventional.` | bc10707 (pre) |  |
| `The contract carries no default, so the form has to ask.` | d9c5151 |  |
| `Type detection can be overridden below.` | d9c5151 |  |
| `Upload a file` | bc10707 (pre) |  |
| `Valid JSON` | d9c5151 |  |
| `What gets written` | d9c5151 |  |
| `commerce.order.placed-value` | 37e9b53 (pre) |  |

### Publish page

`src/pages/PublishPage.tsx`

| String | First seen | Decision |
|---|---|---|
| `Back to the registry` | ce7f2de (pre) |  |
| `Publish a schema` | c484e78 (pre) |  |
| `The request itself failed` | ce7f2de (pre) |  |

### Schema edit page

`src/pages/SchemaEditPage.tsx`

| String | First seen | Decision |
|---|---|---|
| `Back to the schema` | (new) |  |
| `Could not load schema` | (new) |  |
| `Loading schema…` | (new) |  |
| `Publish a new version` | (new) |  |
| `This subject carries no compatibility rule, so the registry will not check this version against v{n}. A removed or renamed field registers without complaint and breaks its consumers at runtime — check the Services tab for who reads this schema before you publish.` | (new) |  |
| `The body and the governance values below are what the registry holds now. Both are rewritten on publish — the metadata block is built from this form, not merged into what is already there — so leave anything you do not mean to change alone.` | (new) |  |
| `The request itself failed` | (new) |  |
| `Unknown error` | (new) |  |

Three of these are deliberately the same string as the publish page's or the
subject page's — `Could not load schema`, `Loading schema…`, `The request
itself failed`, `Unknown error`. Same failure, same words; a reviewer changing
one should change all of them.

### Publish result

`src/features/publish/components/PublishResult.tsx`

| String | First seen | Decision |
|---|---|---|
| `The registry gave no reason.` | ce7f2de (pre) |  |

### Registry list page

`src/pages/SchemaListPage.tsx`

| String | First seen | Decision |
|---|---|---|
| `APICURIO_BASE_URL` | 37e9b53 (pre) |  |
| `Check that Apicurio is reachable at the URL configured in` | 37e9b53 (pre) |  |
| `Clear search` | 37e9b53 (pre) |  |
| `Clear selection` | 37e9b53 (pre) |  |
| `Export CSV` | 37e9b53 (pre) |  |
| `Loading schemas from Apicurio…` | 37e9b53 (pre) |  |
| `No schemas match the filters you have set.` | b572091 (pre) |  |
| `Raise Jira` | 37e9b53 (pre) |  |
| `Raise Jira tickets — not implemented (no Jira integration)` | 37e9b53 (pre) |  |

### Registry table

`src/features/schemas/components/SchemaTable.tsx`

| String | First seen | Decision |
|---|---|---|
| `Could not load the compliance list` | 6ab1e43 |  |
| `No schemas match the current filters.` | 37e9b53 (pre) |  |
| `Select ${item.artifact_id}` | 37e9b53 (pre) |  |
| `Select all` | 37e9b53 (pre) |  |

### Registry toolbar

`src/features/schemas/components/SchemaToolbar.tsx`

| String | First seen | Decision |
|---|---|---|
| `Clear filters` | b572091 (pre) |  |
| `Compact rows` | 37a31d2 |  |
| `Cozy rows` | 37a31d2 |  |
| `Filter by state` | 37a31d2 |  |
| `Publish a schema` | c484e78 (pre) |  |
| `Re-check compliance` | 37a31d2 |  |
| `Re-check every subject against the metadata rules` | 37a31d2 |  |

### Service dependency graph

`src/features/services/components/ServiceDependencyGraph.tsx`

| String | First seen | Decision |
|---|---|---|
| `No service dependencies to display.` | 37e9b53 (pre) |  |
| `Reset view` | 37e9b53 (pre) |  |

### Service portfolio

`src/features/services/components/ServicePortfolio.tsx`

| String | First seen | Decision |
|---|---|---|
| `All services` | b5e6024 |  |
| `Application:` | 37e9b53 (pre) |  |
| `Consume schemas this service produces` | b5e6024 |  |
| `Could not load portfolio` | 37e9b53 (pre) |  |
| `Derived from shared schemas, not declared anywhere.` | b5e6024 |  |
| `Downstream — depends on me` | 37e9b53 (pre) |  |
| `Loading portfolio…` | 37e9b53 (pre) |  |
| `No service dependencies detected.` | 37e9b53 (pre) |  |
| `Nothing to edit here` | b5e6024 |  |
| `Produce schemas this service consumes` | b5e6024 |  |
| `Services that consume schemas this service produces` | 37e9b53 (pre) |  |
| `Services that produce schemas this service consumes` | 37e9b53 (pre) |  |
| `Shared schemas` | 37e9b53 (pre) |  |
| `Unknown error` | 37e9b53 (pre) |  |
| `Upstream — I depend on` | 37e9b53 (pre) |  |

### Services list

`src/features/services/components/ServiceList.tsx`

| String | First seen | Decision |
|---|---|---|
| `${filteredDistinct} services across ${filtered.length} entries` | b5e6024 |  |
| `Clear filters` | b572091 (pre) |  |
| `Clear search` | 37e9b53 (pre) |  |
| `Could not load services` | 37e9b53 (pre) |  |
| `Loading services…` | 37e9b53 (pre) |  |
| `No services found.` | 37e9b53 (pre) |  |
| `No services match the current filters.` | 37e9b53 (pre) |  |
| `Search by name or domain…` | 37e9b53 (pre) |  |
| `Search services` | b5e6024 |  |

### Shared labels (URL state)

`src/features/schemas/hooks/use-schema-url-state.ts`

| String | First seen | Decision |
|---|---|---|
| `Last modified` | 37e9b53 (pre) |  |
| `Non-compliant` | 37e9b53 (pre) |  |
| `Recently changed` | 37e9b53 (pre) |  |

### Subject page

`src/features/schemas/components/SchemaDetail.tsx`

| String | First seen | Decision |
|---|---|---|
| `All compliance checks passed.` | 37e9b53 (pre) |  |
| `Back to the registry` | ce7f2de (pre) |  |
| `Checking compliance…` | a646d80 |  |
| `Clear the find query` | a646d80 |  |
| `Compare this version against an earlier one` | a646d80 |  |
| `Compare versions` | a646d80 |  |
| `Copy a link to this version` | b31437d |  |
| `Copy the schema body` | a646d80 |  |
| `Could not load schema` | 37e9b53 (pre) |  |
| `Could not load services` | 37e9b53 (pre) |  |
| `Download ${downloadName}` | b31437d |  |
| `Download this version` | b31437d |  |
| `Expand to fullscreen` | 37e9b53 (pre) |  |
| `Find a field, key or value…` | a646d80 |  |
| `Find in this schema` | a646d80 |  |
| `How to read the schema` | a646d80 |  |
| `Loading schema…` | 37e9b53 (pre) |  |
| `Loading…` | 37e9b53 (pre) |  |
| `Matches appear here as you type.` | a646d80 |  |
| `Next match` | a646d80 |  |
| `No compatibility rule` | a646d80 |  |
| `No matches` | a646d80 |  |
| `Not implemented — the registry API exposes no endpoint for writing a compatibility rule yet` | a646d80 |  |
| `Not wired up yet: rules are set in Apicurio directly.` | a646d80 |  |
| `Previous match` | a646d80 |  |
| `Set a rule` | a646d80 |  |
| `There is only one version, so there is nothing to compare against` | a646d80 |  |
| `Unknown error` | 37e9b53 (pre) |  |
| `Update schema` | (new) |  |

### Subject page — Code view

`src/features/schemas/components/SchemaViewer.tsx`

| String | First seen | Decision |
|---|---|---|
| `Close fullscreen` | 37e9b53 (pre) |  |
| `Close fullscreen (Escape)` | a646d80 |  |
| `Loading editor…` | 37e9b53 (pre) |  |
| `Schema fullscreen view` | 37e9b53 (pre) |  |

### Subject page — Fields view

`src/features/schemas/components/SchemaFieldsView.tsx`

| String | First seen | Decision |
|---|---|---|
| `Schema fields` | a646d80 |  |
| `This body is not JSON, so it has no fields to tabulate. Read it in Code view.` | a646d80 |  |
| `This body parses, but declares no` | a646d80 |  |

### Subject page — Graph view

`src/features/schemas/components/SchemaGraphView.tsx`

| String | First seen | Decision |
|---|---|---|
| `Copy the schema body` | a646d80 |  |
| `Download the graph as a PNG` | e713d82 |  |
| `Download the graph as a PNG — the last attempt failed` | e713d82 |  |
| `Fit the graph to the view` | a646d80 |  |
| `Fit to view` | a646d80 |  |
| `Graph bounds could not be measured` | e18ce5e |  |
| `This body has no nested structure to draw. Read it in Code view.` | a646d80 |  |
| `This body is not JSON, so it cannot be drawn as a graph. Read it in Code view.` | a646d80 |  |
| `Zoom in` | a646d80 |  |
| `Zoom out` | a646d80 |  |

### Subject page — History

`src/features/schemas/components/VersionTimeline.tsx`

| String | First seen | Decision |
|---|---|---|
| `Choose a version…` | 88754e7 (pre) |  |
| `Loading comparison…` | 88754e7 (pre) |  |
| `One version so far, so there is nothing to compare against yet.` | a646d80 |  |

### src/services/api-client.ts

`src/services/api-client.ts`

| String | First seen | Decision |
|---|---|---|
| `Content-Type` | 37e9b53 (pre) |  |

## Deliberately not listed

- **Test assertions.** Tests quote product copy but are not copy; changing a
  string means changing its test, and `npm test` finds every one.
- **Code comments.** Not user-facing.
- **`console.error` text.** `Unhandled render error` in the boundary goes to
  the console, which is the only sink this app has.
- **Data values.** Domain names, classifications, lifecycles, subject names
  and CMDB fields are registry data, not copy — `active`, `internal` and
  `orphan` are rendered verbatim from the API.
- **`index.html`'s `<title>`.** Still `Pearson Schema Registry`, deliberately
  not shortened when the nav's product name became `Registry`.
