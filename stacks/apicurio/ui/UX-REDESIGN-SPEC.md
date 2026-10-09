# Schema Registry UX redesign — design spec

**Status:** superseded in part. Its intent shipped; four of its structural
decisions did not. See *What shipped* below before using this document.
**Date:** 2026-09-21 · **Superseded:** 2026-09-22 (UIFPE-507)
**Scope:** `stacks/apicurio/ui` — the registry dashboard. No BFF changes in Stage 1.

## What shipped

The high-fidelity design handoff of 2026-09-22 (`design_handoff_schema_registry`,
options 2a / 3a / 4a / 4b / 5a / 6a / 6b) implements this spec's intent — find a
schema, read it, publish one, see what a service owns — and departs from it in
four places. **Where this document and `src/` disagree, `src/` is right.**

| This spec says | What shipped | Why |
|---|---|---|
| "The `overview` view becomes Home and becomes the default", with a four-tab bar `Home · Browse all · Non-compliant · Recently changed` | No Home surface and no tab bar. One list, with `All · Non-compliant · Recently changed` as a segmented partition inside the toolbar, and `all` as the default | Two front doors to one result set meant the table — the thing people came for — was always one click away. `?view=overview` still parses and resolves to `all`, so Home's links keep working |
| The schema detail lives in a 560px right-hand drawer with an `Overview` tab | A page at `/schemas/:group/:id`. `OverviewTab`'s `<dl>` is the always-visible facts band, so `Overview` is retired and `History` takes its place | The drawer had no room for the facts band, three readings of the body, the version history or the diff — and publish-success and the service portfolio already linked to the page, so one schema had two surfaces |
| Monaco showing the raw body is the schema view | Three views over one parse: `Fields` (a field table plus the metadata block), `Code` (Monaco with a Pearson token theme), `Graph` (React Flow + dagre). One search field drives all three; Monaco's find widget gives up ⌘F to it | "Visually see the schema" was the need this spec recorded as unmet; raw text with a second find box did not meet it |
| Registration reports success in a result panel on the publish page | Success navigates to the new subject's schema page. The panel remains for the soft failure (HTTP 200 with `registered: false`), leading with the registry's own error | Compliance is what publishing through the registry produces, and the schema page is where it is shown |

Everything in *Section 5 — what we are not building* still holds, and the
honesty constraints it records are enforced with tests rather than convention.
`pending-review` is gone and stays gone.

## What this is

The registry dashboard today has one shape: a filtered governance table. This
spec redesigns it around what a build user actually arrives wanting to do —
find a schema, see what they own, read its structure and history, and publish a
new one. It is the "two front doors" option: a task-shaped landing page, with
the existing table kept intact as the power surface behind it.

Written for whoever builds it, including an AI agent. Every claim about current
behaviour below was checked against the code, and the file references are exact.

## Why the current design does not serve these needs

| Need | What exists | Gap |
|---|---|---|
| See what I've registered | Nothing. No `current_user`, oidc, jwt or entra reference anywhere in `src/app` or `ui/src` | There is no "me" in this app |
| Understand a schema's lifecycle | `lifecycle` (`active\|deprecated`) as a filter chip; `versions[]` listed in a drawer | Present as data, absent as an experience |
| Visually see the schema | Monaco showing raw JSON/YAML + a version dropdown (`SchemaViewer.tsx`) | Raw text only; no field view, no diff |
| Publish a schema | Nothing. The one registration form lived inside a page titled "Simulate Deploy" and was deleted on 2026-09-21 | Missing; recoverable from git |
| Keyword search | Client-side substring over `artifact_id`, `domain`, `owner.service` against one `?limit=500` fetch | Cannot find a field name, description, or anything in the body |

Two structural faults compound this:

- **`pending-review` is a dead tab.** `filterSchemas` in
  `src/features/schemas/hooks/use-schema-filter.ts` returns `false` for it
  unconditionally, so it can never show a row. The tab bar advertises a review
  workflow that does not exist.
- **Every need is forced through one surface.** Each need above is either a
  person question ("what did I register") or a task ("publish it", "see it",
  "trace its life"). All five currently route through a governance table. The
  table is not the problem — it is well covered by tests — but it is the wrong
  front door.

## Section 1 — Information architecture

**The `overview` view becomes Home and becomes the default.** `/schemas`
currently defaults to `view=all`. `use-schema-url-state.ts` already models
`view` in the URL, so changing the default costs no routing work and existing
deep links keep resolving.

Rejected alternative: a separate `/` route. It would split URL-state handling
across two places for no benefit when the rail has two items.

**Tab set after the change:**

```
Home · Browse all · Non-compliant · Recently changed
```

`pending-review` is removed (see Section 5).

### Home composition, top to bottom

1. **Search — the primary element.** One wide input. Submitting navigates to
   `view=all&search=<q>` so results render in the existing table. The home
   deliberately has no result list of its own: there is one result surface, which
   is what keeps "two front doors" from becoming two places to hunt. Placeholder
   states real scope: `Search subject, domain, owner…`.

2. **Publish a schema** — primary action, routing to `/schemas/publish`
   (Section 2).

3. **Your schemas** — a panel in a `coming soon` state that says *why*: it
   arrives with sign-in. It must not render an empty table or a zero count;
   either would assert that the user owns nothing, which is a different and false
   statement. Deferred to the parked Entra work by owner decision, 2026-09-21.

4. **Needs attention** — three compact lists, every one backed by data that
   exists today:
   - Non-compliant — `useNonCompliant()`
   - Changed this week — `modified_on` within 7 days, already computed as
     `RECENTLY_CHANGED_MS` in `use-schema-filter.ts`
   - Deprecated — `lifecycle === 'deprecated'`

   Rows link to the subject; each lane header links to its filtered table view.

5. **Registry at a glance** — the existing Total and Non-compliant tiles, plus
   **`schemas_with_compatibility_rules`**, which `GET /schemas/overview` already
   returns (`src/app/schemas/overview.py`) and the UI currently discards. That
   figure is 0 of 13 in the live registry, which makes it the most consequential
   number the page can show: until it is 13, no compatibility rule is enforced
   anywhere.

The three request-rate bar charts are **dropped** (owner decision,
2026-09-21) — they are registry-ops telemetry, not build-user information.

## Section 2 — The publish flow

**Publishing through the BFF is the only path that produces a compliant
schema.** `POST /schemas/{group_id}/{artifact_id}`
(`src/app/api/v1/schemas.py:88`) calls `inject_metadata()`
(`src/app/services/metadata_injector.py:13`), which writes the `metadata` block
into the schema body, then `build_apicurio_properties()` mirrors the same values
onto Apicurio properties.

This matters because `metadata_validator.py` reads compliance **from the body**
(`metadata.owner.service`, `metadata.domain`, `metadata.dataClassification`,
`metadata.lifecycle`, `metadata.piiFields`) and separately checks that Apicurio
properties agree with it (`properties_mismatch`). Registering straight against
Apicurio skips both steps, so the artifact fails `metadata_block_missing` the
moment it lands. Publish is therefore not a convenience — it is the compliance
mechanism, and it is the most likely explanation for non-compliant artifacts in
the live registry. The page should say so.

### Form fields

Driven by `DirectSchemaRegisterRequest` (`src/app/schemas/service.py:77`):

| Input | Control | Notes |
|---|---|---|
| `artifact_id`, `group_id` | Text | Subject name. Live convention is `<domain>.<entity>.<event>-value`; validate the shape and warn, never block |
| `service_urn` | Service picker | Required. `resolve_urn` fails registration on an unknown URN, so never free text. `GET /services` backs the picker |
| `content` | File drop + Monaco | Monaco already highlights every one of these types in `SchemaViewer.tsx` |
| `type_override` | Select, optional | BFF auto-detects via `detect_schema_type`; override only when detection is wrong |
| `data_classification` | Radio, required | The contract has no default, so the form must ask |
| `pii_fields` | Tag input | Enter/comma to commit, as the deleted `SchemaAttachForm` did |
| `lifecycle` | Select, default `active` | |

### Two behaviours that matter more than the layout

1. **HTTP 200 is not success.** The response is
   `SchemaRegistrationResult { topic, registered, schema_type, error }` and
   registration fails *softly* with `registered: false` plus an `error` string.
   The result panel keys on `registered`, and when it is false the `error` is the
   headline. Treating 200 as done would report success on failure.

2. **There is no dry-run endpoint**, so pre-validation cannot be honest
   server-side. Two checks the form can still do client-side, both of which catch
   failures that otherwise land silently:
   - unparseable JSON
   - a non-JSON type (`PROTOBUF`, `XSD`, `GRAPHQL`), where `inject_metadata`
     returns the content untouched — meaning **no metadata block is written and
     the schema is non-compliant on arrival.** Warn explicitly in the form.

**Reuse:** `git show HEAD:stacks/apicurio/ui/src/features/onboarding/components/SchemaAttachForm.tsx`
already implements the file reader, type list, classification list and PII tag
input. Restore it as the basis; drop the `OnboardPanel` manifest wrapper and the
"Simulate Deploy" framing, which hid a registration form behind a title that
described something else.

On success, navigate to the new subject's detail page.

## Section 3 — Search

**Stage 1 — client-side, widened.** Keep the existing filter but add the fields
it currently ignores: `name` (present on every `SchemaSummary` and unused),
`owner.team`, `classification`, `lifecycle`. The empty state names the fields
searched rather than only reporting no results. No fuzzy matching and no
ranking: substring matching is predictable, and predictable beats clever at this
size.

**Stage 2 — a `?q=` param on `GET /schemas`.** This is what unlocks field-name
and body search. `list_schemas` (`src/app/api/v1/schemas.py:38`) takes only
`limit` and `offset` today. The trigger is concrete rather than aesthetic: the
UI fetches with `limit=500`, so client-side search silently stops being complete
at 501 artifacts. There are 13.

## Section 4 — Lifecycle

**Build:**

- A **version timeline** on the subject page from `versions[]` — `version`,
  `state`, `created_on` and `created_by` are all real and already returned by
  `GET /schemas/{group}/{artifact}`. This is the largest honest win available.
- A **version diff**. `@monaco-editor/react` exports `DiffEditor`, so comparing
  two versions is nearly free. **No backend dependency:**
  `GET /schemas/{group}/{artifact}` accepts `?version=`
  (`src/app/api/v1/schemas.py:120`) and `getSchemaDetail(groupId, artifactId, version?)`
  in `schemas.service.ts` already forwards it. Verified 2026-09-21.
- A **Deprecated lane** on Home — real data.

**Do not build:**

- The five-state lifecycle board (proposed → active → deprecated → retiring →
  retired). `lifecycle` has two values, `retirement_date` is an unpopulated
  stub, and there is no draft state because registration is immediate and
  synchronous. Building it now produces a second permanently-empty
  `pending-review`. Revisit when the backend carries the states.
- Any UI for `successor_artifact_id` or `retirement_date` while they are always
  `null`. If surfaced at all, they read `not set`.

## Section 5 — Removals and test impact

| Change | Files | Test impact |
|---|---|---|
| Remove `pending-review` | `use-schema-url-state.ts` (`SchemaView` union), `SchemaTabs.tsx` (`TABS`), `use-schema-filter.ts` (`filterSchemas`) | 4 assertions in `use-schema-filter.test.ts` |
| Drop the three request-rate charts | `RegistryOverview.tsx` | — |
| Drop the `recharts` dependency | `package.json`, and the `optimizeDeps: { include: ['recharts'] }` line in `vite.config.ts` | `RegistryOverview.tsx` is recharts' only consumer, confirmed by grep |
| Default view `all` → `overview` | `use-schema-url-state.ts` | `use-schema-url-state.test.ts:21` asserts `'all'` |

**New surfaces:** a Home component (replacing `RegistryOverview`'s role), a
`/schemas/publish` route and page, the restored attach form, and the
search-submit wiring from Home into `view=all`.

## Open dependencies

1. **Identity.** "Your schemas" cannot be built until the parked Entra work
   lands. Until then it is a `coming soon` panel, by owner decision.
2. **Server-side search.** Stage 2 needs a BFF change; not in this scope.

Resolved during planning: older-version content needs no API change — see
Section 4.

## Out of scope

- BFF changes of any kind in Stage 1.
- The `Services` section and `ServiceDependencyGraph`.
- `useRefreshNonCompliant` / `refreshNonCompliant`, which currently have no
  caller after `NonCompliantSchemasPage` was deleted. They wrap a live endpoint
  (`POST /schemas/non-compliant/refresh`) and are a candidate for the
  Non-compliant lane's refresh control, but wiring them is not part of this
  spec.
