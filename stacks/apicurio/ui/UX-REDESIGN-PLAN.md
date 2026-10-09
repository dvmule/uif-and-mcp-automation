# Schema Registry UX Redesign Implementation Plan

> **The code is the authority, not this plan's snippets.** Several task snippets
> below were superseded during implementation and are known to be wrong where
> they differ from what shipped. The clearest example is the publish form's
> `canSubmit` in Task 11 below: it gates submission on `!jsonError` while the
> form offers PROTOBUF, GRAPHQL and XSD, which the BFF accepts and which are not
> JSON — so as written it makes three of seven schema types unpublishable. That
> was fixed in review (UIFPE-507) by applying the JSON parse check only when the
> effective type is JSON-based. Read the plan for intent and sequence; read
> `src/` for behaviour.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the registry dashboard's single governance-table front door with a task-shaped Home, a working publish flow, widened search, and a real version history.

> **Superseded 2026-09-22 (UIFPE-507).** The architecture line below describes
> a Home surface and a tab bar that no longer exist — the high-fidelity design
> handoff replaced both with one list and a segmented partition. Read
> `UX-REDESIGN-SPEC.md`'s *What shipped* table for the four divergences, and
> `src/` for behaviour. This plan is kept for its task sequence and its record
> of what was tried, not as a description of the app.

**Architecture:** ~~The `overview` URL view becomes Home and the default, composed from small focused components. The existing table stays untouched as the browse surface and receives Home's search via `view=all&search=<q>`, so there is exactly one result surface.~~ (Superseded — see the note above.) Publish is a new route posting to the existing BFF registration endpoint. No BFF changes.

**Tech Stack:** React 18, TypeScript, Vite 5, Tailwind 3, TanStack Query 5, react-router-dom 7, Monaco (`@monaco-editor/react`), Vitest 2 + Testing Library + msw 2.

**Spec:** `stacks/apicurio/ui/UX-REDESIGN-SPEC.md`

## Global Constraints

- **Working directory for every command is `stacks/apicurio/ui`.**
- **No BFF changes.** Every endpoint this plan calls already exists.
- **Layering, one direction only:** `service` → TanStack Query hook → component. Never `fetch`/`axios` in a component. Never server state in Zustand. (`stacks/apicurio/CLAUDE.md`)
- **Never a default export.** Named exports only. (`stacks/apicurio/CLAUDE.md`)
- **Tests are colocated** as `*.test.tsx` / `*.test.ts` next to the unit. Vitest only collects `src/**/*.{test,spec}.{ts,tsx}` (`vite.config.ts`).
- **The msw API base in tests is exactly `http://localhost:8000/api/v1`** — it must match `public/config.json`, which the app fetches at boot.
- **Tailwind tokens already in use:** `ink`, `ink-soft`, `ink-mute`, `paper`, `paper-line`, `accent`. Use these, not raw hex.
- **Brand, for any new visual work:** Plus Jakarta Sans; Pearson Purple `#0D004D`, Amethyst `#512EAB`, Light Purple `#C1BFFF`, Mist Purple `#EDECF6`. (root `CLAUDE.md`)
- **Commit subject format is `UIFPE-507 #comment <message>`** — the issue key first, per `capabilities/delivery/jira-github-integration.md`. A commit-msg hook rejects any message without a `UIFPE-<n>` key.
- **Every commit message ends with a `Co-Authored-By:` trailer naming the model
  that actually wrote the commit** — use the exact trailer your own harness
  instruction gives you. Do NOT copy a model name out of this plan: this
  document was authored by a different model than the one implementing any
  given task, and the trailer is an attribution of authorship.
- **Full suite command:** `npx vitest run`. Baseline before Task 1 is **136 tests / 11 files passing**.
- **Typecheck command:** `npx tsc -b --noEmit`. Must be clean before every commit.
- **`npm run lint` is broken and out of scope** — `eslint` is not installed locally and there is no `eslint.config.*`. Do not try to fix it here; do not let it block a commit.

## File Structure

| File | Responsibility |
|---|---|
`src/features/schemas/hooks/use-schema-url-state.ts` | modify — drop `pending-review` from `SchemaView`, default `view` to `overview`
`src/features/schemas/components/SchemaTabs.tsx` | modify — 4 tabs, relabelled
`src/features/schemas/hooks/use-schema-filter.ts` | modify — drop the dead case, widen search fields
`src/features/schemas/components/RegistryOverview.tsx` | modify — becomes the "Registry at a glance" tiles only; charts and stub data removed
`src/features/schemas/components/RegistryHome.tsx` | **create** — Home composition
`src/features/schemas/components/HomeSearch.tsx` | **create** — the search hero and its submit wiring
`src/features/schemas/components/YourSchemasPanel.tsx` | **create** — the deferred-identity panel
`src/features/schemas/components/NeedsAttention.tsx` | **create** — the three attention lanes
`src/features/schemas/components/VersionTimeline.tsx` | **create** — version history + diff
`src/features/publish/types/publish.types.ts` | **create** — registration request/result types
`src/features/publish/services/publish.service.ts` | **create** — `registerSchema`
`src/features/publish/hooks/use-register-schema.ts` | **create** — the mutation hook
`src/features/publish/components/SchemaPublishForm.tsx` | **create** — the form (restored from git)
`src/features/publish/components/PublishResult.tsx` | **create** — result panel keyed on `registered`
`src/features/publish/index.ts` | **create** — feature barrel
`src/pages/PublishPage.tsx` | **create** — the `/schemas/publish` page
`src/App.tsx` | modify — add the publish route
`package.json`, `vite.config.ts` | modify — drop `recharts`

---

### Task 1: Remove the dead `pending-review` tab

`filterSchemas` returns `false` for this view unconditionally, so it can never show a row.

**Files:**
- Modify: `src/features/schemas/hooks/use-schema-url-state.ts:5-10`
- Modify: `src/features/schemas/components/SchemaTabs.tsx:5-11`
- Modify: `src/features/schemas/hooks/use-schema-filter.ts`
- Test: `src/features/schemas/components/SchemaTabs.test.tsx` (create)
- Test: `src/features/schemas/hooks/use-schema-filter.test.ts` (modify)

**Interfaces:**
- Consumes: nothing.
- Produces: `SchemaView = 'overview' | 'all' | 'non-compliant' | 'recently-changed'`. Every later task uses this narrowed union.

- [ ] **Step 1: Write the failing test**

Create `src/features/schemas/components/SchemaTabs.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react';
import { SchemaTabs } from './SchemaTabs';
import type { SchemaView } from '../hooks/use-schema-url-state';

const COUNTS: Record<SchemaView, number> = {
  overview: 0,
  all: 13,
  'non-compliant': 2,
  'recently-changed': 4,
};

it('renders exactly the four live tabs', () => {
  render(<SchemaTabs activeView="overview" counts={COUNTS} onSelect={() => {}} />);
  expect(screen.getAllByRole('button')).toHaveLength(4);
});

it('does not render a pending review tab', () => {
  render(<SchemaTabs activeView="overview" counts={COUNTS} onSelect={() => {}} />);
  expect(screen.queryByText(/pending review/i)).not.toBeInTheDocument();
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npx vitest run src/features/schemas/components/SchemaTabs.test.tsx`
Expected: FAIL — 5 buttons found, not 4, and the pending review text is present. A TypeScript error on the `COUNTS` object literal (missing `'pending-review'` key) is also expected and is part of the same failure.

- [ ] **Step 3: Narrow the `SchemaView` union**

In `src/features/schemas/hooks/use-schema-url-state.ts`, replace the union:

```ts
export type SchemaView =
  | 'overview'
  | 'all'
  | 'non-compliant'
  | 'recently-changed';
```

- [ ] **Step 4: Drop the tab**

In `src/features/schemas/components/SchemaTabs.tsx`, replace `TABS`:

```ts
const TABS: { value: SchemaView; label: string }[] = [
  { value: 'overview', label: 'Overview' },
  { value: 'all', label: 'All' },
  { value: 'non-compliant', label: 'Non-compliant' },
  { value: 'recently-changed', label: 'Recently changed' },
];
```

- [ ] **Step 5: Drop the dead filter case**

In `src/features/schemas/hooks/use-schema-filter.ts`, delete these two lines from the `switch` inside `filterSchemas`:

```ts
        case 'pending-review':
          return false;
```

- [ ] **Step 6: Remove the stale assertions**

Run `grep -n "pending-review" src/features/schemas/hooks/use-schema-filter.test.ts` — there are 4 matches. Delete the `it(...)` block containing each. If a block also asserts other behaviour, keep that behaviour and delete only the `pending-review` assertion.

- [ ] **Step 7: Verify**

Run: `npx tsc -b --noEmit && npx vitest run`
Expected: typecheck clean; `SchemaTabs.test.tsx` passes; no file references `pending-review`. Confirm with `grep -rn "pending-review" src/` returning nothing.

- [ ] **Step 8: Commit**

```bash
git add src/features/schemas/hooks/use-schema-url-state.ts \
        src/features/schemas/components/SchemaTabs.tsx \
        src/features/schemas/components/SchemaTabs.test.tsx \
        src/features/schemas/hooks/use-schema-filter.ts \
        src/features/schemas/hooks/use-schema-filter.test.ts
git commit -m "$(cat <<'EOF'
UIFPE-507 #comment Remove the pending-review tab, which could never show a row

filterSchemas returned false for this view unconditionally, so the tab bar
advertised a review workflow that does not exist.

Co-Authored-By: <the exact trailer your harness instruction gives you>
EOF
)"
```

---

### Task 2: Make Home the default view

**Files:**
- Modify: `src/features/schemas/hooks/use-schema-url-state.ts` (`DEFAULTS.view`)
- Modify: `src/features/schemas/components/SchemaTabs.tsx` (labels)
- Test: `src/features/schemas/hooks/use-schema-url-state.test.ts:21`

**Interfaces:**
- Consumes: `SchemaView` from Task 1.
- Produces: landing on `/schemas` with no params now yields `state.view === 'overview'`.

- [ ] **Step 1: Update the failing assertion**

In `src/features/schemas/hooks/use-schema-url-state.test.ts`, line 21 currently reads `expect(result.current.state.view).toBe('all');`. Change it to:

```ts
    expect(result.current.state.view).toBe('overview');
```

- [ ] **Step 2: Add a test that the label changed**

Append to `src/features/schemas/components/SchemaTabs.test.tsx`:

```tsx
it('labels the default view Home and the table Browse all', () => {
  render(<SchemaTabs activeView="overview" counts={COUNTS} onSelect={() => {}} />);
  expect(screen.getByText('Home')).toBeInTheDocument();
  expect(screen.getByText('Browse all')).toBeInTheDocument();
});
```

- [ ] **Step 3: Run both to verify they fail**

Run: `npx vitest run src/features/schemas/hooks/use-schema-url-state.test.ts src/features/schemas/components/SchemaTabs.test.tsx`
Expected: FAIL — view is `'all'`, and neither `Home` nor `Browse all` is found.

- [ ] **Step 4: Flip the default**

In `use-schema-url-state.ts`, inside `DEFAULTS`, change the first entry:

```ts
const DEFAULTS: SchemaUrlState = {
  view: 'overview',
```

- [ ] **Step 5: Relabel the tabs**

In `SchemaTabs.tsx`:

```ts
const TABS: { value: SchemaView; label: string }[] = [
  { value: 'overview', label: 'Home' },
  { value: 'all', label: 'Browse all' },
  { value: 'non-compliant', label: 'Non-compliant' },
  { value: 'recently-changed', label: 'Recently changed' },
];
```

- [ ] **Step 6: Verify**

Run: `npx tsc -b --noEmit && npx vitest run`
Expected: all pass.

Note: because `set()` deletes any param equal to its default, `view=overview` no longer appears in the URL and `view=all` now does. That is correct and existing deep links still resolve.

- [ ] **Step 7: Commit**

```bash
git add src/features/schemas/hooks/use-schema-url-state.ts \
        src/features/schemas/hooks/use-schema-url-state.test.ts \
        src/features/schemas/components/SchemaTabs.tsx \
        src/features/schemas/components/SchemaTabs.test.tsx
git commit -m "$(cat <<'EOF'
UIFPE-507 #comment Land on Home instead of the governance table

Co-Authored-By: <the exact trailer your harness instruction gives you>
EOF
)"
```

---

### Task 3: Delete the fabricated metrics charts and the recharts dependency

`RegistryOverview.tsx` renders three bar charts from a hard-coded `STUB_METRICS` constant carrying a `TODO(W3): wire real metrics from Prometheus`. The landing page currently shows invented numbers as though they were real.

**Files:**
- Modify: `src/features/schemas/components/RegistryOverview.tsx`
- Modify: `package.json`
- Modify: `vite.config.ts`
- Test: `src/features/schemas/components/RegistryOverview.test.tsx` (create)

**Interfaces:**
- Consumes: `useOverviewStats()` returning `OverviewStats { total_schemas, schemas_with_compatibility_rules, non_compliant_schemas }`.
- Produces: `RegistryOverview` renders stat tiles only, no charts.

- [ ] **Step 1: Write the failing test**

Create `src/features/schemas/components/RegistryOverview.test.tsx`:

```tsx
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { RegistryOverview } from './RegistryOverview';

const API = 'http://localhost:8000/api/v1';

const STATS = {
  total_schemas: 13,
  schemas_with_compatibility_rules: 0,
  non_compliant_schemas: 2,
};

const server = setupServer(
  http.get(`${API}/schemas/overview`, () => HttpResponse.json(STATS)),
);

beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function renderOverview() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <RegistryOverview onNavigate={() => {}} />
    </QueryClientProvider>,
  );
}

it('shows the real total from the API', async () => {
  renderOverview();
  await waitFor(() => expect(screen.getByText('13')).toBeInTheDocument());
});

it('renders no fabricated request-rate metrics', async () => {
  renderOverview();
  await waitFor(() => expect(screen.getByText('13')).toBeInTheDocument());
  expect(screen.queryByText(/read requests/i)).not.toBeInTheDocument();
  expect(screen.queryByText(/create requests/i)).not.toBeInTheDocument();
  expect(screen.queryByText(/delete requests/i)).not.toBeInTheDocument();
  expect(screen.queryByText('525')).not.toBeInTheDocument();
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npx vitest run src/features/schemas/components/RegistryOverview.test.tsx`
Expected: FAIL — "Read requests" and "525" are present.

If the component's current props differ from `onNavigate`, read the top of `RegistryOverview.tsx` and match the real prop name in the test before proceeding.

- [ ] **Step 3: Strip the charts**

In `src/features/schemas/components/RegistryOverview.tsx`, delete:
- the whole `recharts` import block (`ResponsiveContainer`, `BarChart`, `Bar`, `XAxis`, `YAxis`, `Tooltip`, `CartesianGrid`)
- `type TimeRange`, `interface DataPoint`, `const STUB_METRICS`, `const RANGE_LABELS`, and the `TODO(W3)` comment
- the `useState` for the time range if it is only used by the charts, and the `PackageOpen` icon import if it was only the charts' empty state
- the entire "Schema Registry metrics" JSX section

Keep the `StatCard` component and the stat-tile row.

- [ ] **Step 4: Run the test to verify it passes**

Run: `npx vitest run src/features/schemas/components/RegistryOverview.test.tsx`
Expected: PASS.

- [ ] **Step 5: Confirm recharts has no other consumer, then drop it**

Run: `grep -rn "recharts" src/`
Expected: no matches.

In `package.json`, delete the `"recharts": "^3.8.1",` line from `dependencies`.

In `vite.config.ts`, delete:

```ts
  optimizeDeps: {
    include: ['recharts'],
  },
```

- [ ] **Step 6: Reinstall and verify nothing broke**

Run: `npm install && npx tsc -b --noEmit && npx vitest run`
Expected: install succeeds, typecheck clean, whole suite passes.

**Use the repo's toolchain, not a newer npm.** If `npm install` rewrites `package-lock.json` in a way CI rejects, check `npm -v` against CI's version before committing the lockfile.

- [ ] **Step 7: Commit**

```bash
git add src/features/schemas/components/RegistryOverview.tsx \
        src/features/schemas/components/RegistryOverview.test.tsx \
        package.json package-lock.json vite.config.ts
git commit -m "$(cat <<'EOF'
UIFPE-507 #comment Remove the invented request-rate charts from the landing page

The three bar charts read from a hard-coded STUB_METRICS constant carrying a
TODO to wire real Prometheus metrics, so the page showed fabricated numbers as
real ones. RegistryOverview was recharts' only consumer, so the dependency goes
with them.

Co-Authored-By: <the exact trailer your harness instruction gives you>
EOF
)"
```

---

### Task 4: Surface the compatibility-rule coverage stat

`GET /schemas/overview` already returns `schemas_with_compatibility_rules`; the UI discards it. It is 0 of 13 live, meaning no compatibility rule is enforced anywhere.

**Files:**
- Modify: `src/features/schemas/components/RegistryOverview.tsx`
- Test: `src/features/schemas/components/RegistryOverview.test.tsx`

**Interfaces:**
- Consumes: `OverviewStats` from Task 3.
- Produces: no new exports.

- [ ] **Step 1: Write the failing test**

Append to `RegistryOverview.test.tsx`:

```tsx
it('shows compatibility-rule coverage as a fraction of the total', async () => {
  renderOverview();
  await waitFor(() => expect(screen.getByText(/compatibility rules/i)).toBeInTheDocument());
  expect(screen.getByText('0 of 13')).toBeInTheDocument();
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npx vitest run src/features/schemas/components/RegistryOverview.test.tsx -t compatibility`
Expected: FAIL — no such text.

- [ ] **Step 3: Add the tile**

In `RegistryOverview.tsx`, add a third tile to the stat row. Place it after the non-compliant tile:

```tsx
<div className="rounded border border-paper-line bg-white p-4">
  <p className="text-xs text-ink-mute">Compatibility rules</p>
  <p className="mt-2 text-2xl font-semibold text-ink tabular-nums">
    {stats ? `${stats.schemas_with_compatibility_rules} of ${stats.total_schemas}` : '—'}
  </p>
  <p className="mt-1 text-xs text-ink-mute">
    {stats && stats.schemas_with_compatibility_rules === 0
      ? 'No schema is protected against a breaking change yet.'
      : 'Schemas with a compatibility rule set.'}
  </p>
</div>
```

Bind `stats` to whatever `useOverviewStats()`'s `data` is already destructured as in this file; reuse that name rather than adding a second call.

- [ ] **Step 4: Run to verify it passes**

Run: `npx vitest run src/features/schemas/components/RegistryOverview.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/features/schemas/components/RegistryOverview.tsx \
        src/features/schemas/components/RegistryOverview.test.tsx
git commit -m "$(cat <<'EOF'
UIFPE-507 #comment Show compatibility-rule coverage, which the API already returned

Co-Authored-By: <the exact trailer your harness instruction gives you>
EOF
)"
```

---

### Task 5: Widen keyword search

Search currently matches `artifact_id`, `domain` and `owner.service`, ignoring `name`, `owner.team`, `classification` and `lifecycle` — all present on `SchemaSummary`.

**Files:**
- Modify: `src/features/schemas/hooks/use-schema-filter.ts`
- Test: `src/features/schemas/hooks/use-schema-filter.test.ts`

**Interfaces:**
- Consumes: `SchemaSummary` from `../types/schema.types`.
- Produces: `filterSchemas` unchanged in signature; matches 7 fields.

- [ ] **Step 1: Write the failing tests**

Append to `src/features/schemas/hooks/use-schema-filter.test.ts`. Build the fixture with the same helper the existing tests use — read the top of the file and reuse it; if it builds `SchemaSummary` objects inline, follow that shape exactly.

```ts
describe('search field coverage', () => {
  const items = [
    makeSchema({ artifact_id: 'commerce.order.placed-value', name: 'OrderPlaced', domain: 'commerce', classification: 'confidential', lifecycle: 'active', owner: { service: 'CI0093423', team: 'TM-COMMERCE-PLATFORM', email: null } }),
    makeSchema({ artifact_id: 'learning.learner-value', name: 'Learner', domain: 'learning', classification: 'internal', lifecycle: 'deprecated', owner: { service: 'CI0093422', team: 'TM-LEARNING-PLATFORM', email: null } }),
  ];
  const base = { ...DEFAULT_STATE, view: 'all' as const };

  it('matches the record name', () => {
    expect(filterSchemas(items, new Set(), { ...base, search: 'OrderPlaced' })).toHaveLength(1);
  });

  it('matches the owning team', () => {
    expect(filterSchemas(items, new Set(), { ...base, search: 'TM-LEARNING' })).toHaveLength(1);
  });

  it('matches the classification', () => {
    expect(filterSchemas(items, new Set(), { ...base, search: 'confidential' })).toHaveLength(1);
  });

  it('matches the lifecycle', () => {
    expect(filterSchemas(items, new Set(), { ...base, search: 'deprecated' })).toHaveLength(1);
  });

  it('is case insensitive', () => {
    expect(filterSchemas(items, new Set(), { ...base, search: 'orderplaced' })).toHaveLength(1);
  });
});
```

Replace `makeSchema` and `DEFAULT_STATE` with the real helper and default-state names from the existing file.

- [ ] **Step 2: Run to verify they fail**

Run: `npx vitest run src/features/schemas/hooks/use-schema-filter.test.ts -t "search field coverage"`
Expected: FAIL on name, team, classification and lifecycle — each returns 0 matches.

- [ ] **Step 3: Widen the matcher**

In `use-schema-filter.ts`, replace the `if (state.search)` block:

```ts
  // Free-text search. Substring, not fuzzy: predictable beats clever at this size.
  if (state.search) {
    const q = state.search.toLowerCase();
    const hit = (v: string | null | undefined) => (v ?? '').toLowerCase().includes(q);
    result = result.filter(
      (item) =>
        hit(item.artifact_id) ||
        hit(item.name) ||
        hit(item.domain) ||
        hit(item.classification) ||
        hit(item.lifecycle) ||
        hit(item.owner.service) ||
        hit(item.owner.team),
    );
  }
```

- [ ] **Step 4: Run to verify they pass**

Run: `npx vitest run src/features/schemas/hooks/use-schema-filter.test.ts`
Expected: PASS, including all pre-existing tests in the file.

- [ ] **Step 5: Update the placeholder to match reality**

In `src/features/schemas/components/SchemaToolbar.tsx`, change the input's placeholder to:

```tsx
placeholder="Search subject, name, domain, team, classification…"
```

- [ ] **Step 6: Verify and commit**

Run: `npx tsc -b --noEmit && npx vitest run`

```bash
git add src/features/schemas/hooks/use-schema-filter.ts \
        src/features/schemas/hooks/use-schema-filter.test.ts \
        src/features/schemas/components/SchemaToolbar.tsx
git commit -m "$(cat <<'EOF'
UIFPE-507 #comment Search the fields the registry actually carries

name, owner.team, classification and lifecycle were all on SchemaSummary and
none of them were searchable.

Co-Authored-By: <the exact trailer your harness instruction gives you>
EOF
)"
```

---

### Task 6: An empty state that names what was searched

**Files:**
- Modify: `src/features/schemas/components/SchemaList.tsx`
- Test: `src/features/schemas/components/SchemaList.test.tsx` (create if absent; otherwise append)

**Interfaces:**
- Consumes: `filterSchemas` from Task 5.
- Produces: no new exports.

- [ ] **Step 1: Write the failing test**

```tsx
it('names the searched fields when nothing matches', async () => {
  renderList('/schemas?view=all&search=zzzzznope');
  await waitFor(() =>
    expect(screen.getByText(/no schemas match/i)).toBeInTheDocument(),
  );
  expect(screen.getByText(/subject, name, domain, team or classification/i)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: /clear search/i })).toBeInTheDocument();
});
```

Reuse the existing `renderList` helper in this file if there is one; otherwise copy the msw + `MemoryRouter` + `QueryClientProvider` harness from `src/features/services/components/ServiceList.test.tsx` and point its handler at `${API}/schemas`.

- [ ] **Step 2: Run to verify it fails**

Run: `npx vitest run src/features/schemas/components/SchemaList.test.tsx -t "names the searched fields"`
Expected: FAIL — no such copy exists.

- [ ] **Step 3: Add the empty state**

In `SchemaList.tsx`, where the filtered list renders, add ahead of the table:

```tsx
{filtered.length === 0 && state.search && (
  <div className="py-10 text-center">
    <p className="text-sm font-medium text-ink">
      No schemas match “{state.search}”.
    </p>
    <p className="mt-1 text-xs text-ink-mute">
      Searched subject, name, domain, team or classification. Schema bodies are
      not searched yet.
    </p>
    <button
      type="button"
      onClick={() => set({ search: '' })}
      className="mt-3 text-xs font-medium text-accent hover:underline"
    >
      Clear search
    </button>
  </div>
)}
```

Bind `filtered`, `state` and `set` to the names already in scope in this component.

- [ ] **Step 4: Run to verify it passes, then commit**

Run: `npx tsc -b --noEmit && npx vitest run`

```bash
git add src/features/schemas/components/SchemaList.tsx \
        src/features/schemas/components/SchemaList.test.tsx
git commit -m "$(cat <<'EOF'
UIFPE-507 #comment Say what was searched when a search finds nothing

Co-Authored-By: <the exact trailer your harness instruction gives you>
EOF
)"
```

---

### Task 7: The `YourSchemasPanel` deferred-identity panel

The app has no identity — no `current_user`, oidc, jwt or entra reference exists in the BFF or UI. This panel must say the feature is pending sign-in and must **not** render an empty list or a zero, either of which asserts the user owns nothing.

**Files:**
- Create: `src/features/schemas/components/YourSchemasPanel.tsx`
- Test: `src/features/schemas/components/YourSchemasPanel.test.tsx`

**Interfaces:**
- Consumes: nothing.
- Produces: `export function YourSchemasPanel(): JSX.Element` — no props.

- [ ] **Step 1: Write the failing test**

```tsx
import { render, screen } from '@testing-library/react';
import { YourSchemasPanel } from './YourSchemasPanel';

it('explains that the panel needs sign-in rather than showing a count', () => {
  render(<YourSchemasPanel />);
  expect(screen.getByText(/your schemas/i)).toBeInTheDocument();
  expect(screen.getByText(/coming soon/i)).toBeInTheDocument();
  expect(screen.getByText(/sign-in/i)).toBeInTheDocument();
  expect(screen.queryByText('0')).not.toBeInTheDocument();
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `npx vitest run src/features/schemas/components/YourSchemasPanel.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Write the component**

```tsx
import { UserCircle } from 'lucide-react';

export function YourSchemasPanel() {
  return (
    <section className="rounded border border-dashed border-paper-line bg-paper p-5">
      <div className="flex items-start gap-3">
        <UserCircle size={20} className="mt-0.5 shrink-0 text-ink-mute" strokeWidth={1.75} />
        <div>
          <div className="flex items-center gap-2">
            <h2 className="text-sm font-semibold text-ink">Your schemas</h2>
            <span className="rounded bg-paper-line px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-ink-mute">
              Coming soon
            </span>
          </div>
          <p className="mt-1.5 max-w-prose text-xs text-ink-mute">
            Once sign-in is available, this shows the schemas your team owns and
            the ones waiting on you. Until then, filter by owner from{' '}
            <span className="font-medium text-ink-soft">Browse all</span>.
          </p>
        </div>
      </div>
    </section>
  );
}
```

- [ ] **Step 4: Run to verify it passes, then commit**

Run: `npx vitest run src/features/schemas/components/YourSchemasPanel.test.tsx`

```bash
git add src/features/schemas/components/YourSchemasPanel.tsx \
        src/features/schemas/components/YourSchemasPanel.test.tsx
git commit -m "$(cat <<'EOF'
UIFPE-507 #comment Add the Your schemas panel, pending the Entra work

States that it needs sign-in rather than rendering an empty list, which would
assert the user owns nothing.

Co-Authored-By: <the exact trailer your harness instruction gives you>
EOF
)"
```

---

### Task 8: The `NeedsAttention` lanes

**Files:**
- Create: `src/features/schemas/components/NeedsAttention.tsx`
- Test: `src/features/schemas/components/NeedsAttention.test.tsx`

**Interfaces:**
- Consumes: `SchemaSummary` (`../types/schema.types`), `useNonCompliant()` (`../hooks/use-non-compliant`).
- Produces: `export function NeedsAttention({ items, onNavigate }: { items: SchemaSummary[]; onNavigate: (view: SchemaView) => void }): JSX.Element`

- [ ] **Step 1: Write the failing test**

```tsx
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { NeedsAttention } from './NeedsAttention';
import type { SchemaSummary } from '../types/schema.types';

const API = 'http://localhost:8000/api/v1';
const DAY = 24 * 60 * 60 * 1000;

function makeSummary(over: Partial<SchemaSummary>): SchemaSummary {
  return {
    artifact_id: 'commerce.order.placed-value',
    group_id: 'default',
    name: 'OrderPlaced',
    type: 'AVRO',
    state: 'ENABLED',
    created_on: null,
    modified_on: null,
    latest_version: '3',
    owner: { service: 'CI0093423', team: 'TM-COMMERCE-PLATFORM', email: null },
    domain: 'commerce',
    classification: 'confidential',
    lifecycle: 'active',
    pii_fields: [],
    ...over,
  };
}

const ITEMS = [
  makeSummary({ artifact_id: 'commerce.order.placed-value', modified_on: new Date(Date.now() - 2 * DAY).toISOString() }),
  makeSummary({ artifact_id: 'learning.learner-value', lifecycle: 'deprecated', modified_on: new Date(Date.now() - 90 * DAY).toISOString() }),
];

const server = setupServer(
  http.get(`${API}/schemas/non-compliant`, () =>
    HttpResponse.json({ items: [], last_validated: null, total_checked: 13 }),
  ),
);

beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function renderLanes() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <NeedsAttention items={ITEMS} onNavigate={() => {}} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

it('lists a schema changed in the last seven days', async () => {
  renderLanes();
  await waitFor(() => expect(screen.getByText(/changed this week/i)).toBeInTheDocument());
  expect(screen.getByText('commerce.order.placed-value')).toBeInTheDocument();
});

it('lists deprecated schemas', async () => {
  renderLanes();
  await waitFor(() => expect(screen.getByText(/deprecated/i)).toBeInTheDocument());
  expect(screen.getByText('learning.learner-value')).toBeInTheDocument();
});
```

Note the deliberate typo guard: write the closing tag as `</QueryClientProvider>`. Fix it if you copied it wrong.

- [ ] **Step 2: Run to verify it fails**

Run: `npx vitest run src/features/schemas/components/NeedsAttention.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Write the component**

```tsx
import { Link } from 'react-router-dom';
import { AlertTriangle, Clock, Archive } from 'lucide-react';
import { useNonCompliant } from '../hooks/use-non-compliant';
import type { SchemaSummary } from '../types/schema.types';
import type { SchemaView } from '../hooks/use-schema-url-state';

const RECENTLY_CHANGED_MS = 7 * 24 * 60 * 60 * 1000;

interface LaneProps {
  icon: React.ReactNode;
  title: string;
  subjects: string[];
  emptyCopy: string;
  viewAll?: { label: string; view: SchemaView };
  onNavigate: (view: SchemaView) => void;
}

function Lane({ icon, title, subjects, emptyCopy, viewAll, onNavigate }: LaneProps) {
  return (
    <section className="rounded border border-paper-line bg-white p-4">
      <div className="flex items-center gap-2">
        {icon}
        <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-mute">{title}</h3>
        <span className="ml-auto text-xs tabular-nums text-ink-mute">{subjects.length}</span>
      </div>
      {subjects.length === 0 ? (
        <p className="mt-3 text-xs text-ink-mute">{emptyCopy}</p>
      ) : (
        <ul className="mt-3 space-y-1.5">
          {subjects.slice(0, 5).map((s) => (
            <li key={s}>
              <Link
                to={`/schemas?view=all&search=${encodeURIComponent(s)}`}
                className="block truncate font-mono text-xs text-ink hover:text-accent hover:underline"
              >
                {s}
              </Link>
            </li>
          ))}
        </ul>
      )}
      {viewAll && subjects.length > 5 && (
        <button
          type="button"
          onClick={() => onNavigate(viewAll.view)}
          className="mt-3 text-xs font-medium text-accent hover:underline"
        >
          {viewAll.label}
        </button>
      )}
    </section>
  );
}

export function NeedsAttention({
  items,
  onNavigate,
}: {
  items: SchemaSummary[];
  onNavigate: (view: SchemaView) => void;
}) {
  const { data: nonCompliant } = useNonCompliant();

  const failing = (nonCompliant?.items ?? []).map((r) => r.artifact_id);

  const changed = items
    .filter((i) => i.modified_on && Date.now() - new Date(i.modified_on).getTime() < RECENTLY_CHANGED_MS)
    .map((i) => i.artifact_id);

  const deprecated = items.filter((i) => i.lifecycle === 'deprecated').map((i) => i.artifact_id);

  return (
    <div className="grid gap-3 md:grid-cols-3">
      <Lane
        icon={<AlertTriangle size={14} className="text-ink-mute" strokeWidth={2} />}
        title="Non-compliant"
        subjects={failing}
        emptyCopy="Every schema passes its metadata rules."
        viewAll={{ label: 'See all non-compliant', view: 'non-compliant' }}
        onNavigate={onNavigate}
      />
      <Lane
        icon={<Clock size={14} className="text-ink-mute" strokeWidth={2} />}
        title="Changed this week"
        subjects={changed}
        emptyCopy="No schema changed in the last seven days."
        viewAll={{ label: 'See all recent changes', view: 'recently-changed' }}
        onNavigate={onNavigate}
      />
      <Lane
        icon={<Archive size={14} className="text-ink-mute" strokeWidth={2} />}
        title="Deprecated"
        subjects={deprecated}
        emptyCopy="No schema is marked deprecated."
        onNavigate={onNavigate}
      />
    </div>
  );
}
```

- [ ] **Step 4: Run to verify it passes, then commit**

Run: `npx tsc -b --noEmit && npx vitest run src/features/schemas/components/NeedsAttention.test.tsx`

```bash
git add src/features/schemas/components/NeedsAttention.tsx \
        src/features/schemas/components/NeedsAttention.test.tsx
git commit -m "$(cat <<'EOF'
UIFPE-507 #comment Add the Needs attention lanes, all from data that exists

Co-Authored-By: <the exact trailer your harness instruction gives you>
EOF
)"
```

---

### Task 9: `HomeSearch` and the `RegistryHome` composition

**Files:**
- Create: `src/features/schemas/components/HomeSearch.tsx`
- Create: `src/features/schemas/components/RegistryHome.tsx`
- Create: `src/features/schemas/components/HomeSearch.test.tsx`
- Modify: `src/pages/SchemaListPage.tsx` — render `RegistryHome` for `view === 'overview'`
- Modify: `src/features/schemas/index.ts` — export `RegistryHome`

**Interfaces:**
- Consumes: `RegistryOverview` (Tasks 3–4), `YourSchemasPanel` (Task 7), `NeedsAttention` (Task 8).
- Produces:
  - `export function HomeSearch({ onSubmit }: { onSubmit: (q: string) => void }): JSX.Element`
  - `export function RegistryHome({ items, onNavigate, onSearch }: { items: SchemaSummary[]; onNavigate: (view: SchemaView) => void; onSearch: (q: string) => void }): JSX.Element`

- [ ] **Step 1: Write the failing test**

```tsx
import { render, screen, fireEvent } from '@testing-library/react';
import { HomeSearch } from './HomeSearch';

it('submits the typed query', () => {
  const onSubmit = vi.fn();
  render(<HomeSearch onSubmit={onSubmit} />);
  fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'order.placed' } });
  fireEvent.submit(screen.getByRole('search'));
  expect(onSubmit).toHaveBeenCalledWith('order.placed');
});

it('does not submit an empty query', () => {
  const onSubmit = vi.fn();
  render(<HomeSearch onSubmit={onSubmit} />);
  fireEvent.submit(screen.getByRole('search'));
  expect(onSubmit).not.toHaveBeenCalled();
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `npx vitest run src/features/schemas/components/HomeSearch.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Write `HomeSearch`**

```tsx
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { Search, FilePlus } from 'lucide-react';

export function HomeSearch({ onSubmit }: { onSubmit: (q: string) => void }) {
  const [q, setQ] = useState('');

  return (
    <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
      <form
        role="search"
        className="relative flex-1"
        onSubmit={(e) => {
          e.preventDefault();
          const trimmed = q.trim();
          if (trimmed) onSubmit(trimmed);
        }}
      >
        <Search
          size={16}
          strokeWidth={2}
          className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-ink-mute"
        />
        <input
          id="home-search"
          type="search"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search subject, name, domain, team, classification…"
          className="h-11 w-full rounded border border-paper-line bg-white pl-9 pr-3 text-sm text-ink placeholder:text-ink-mute focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent/40"
        />
      </form>
      <Link
        to="/schemas/publish"
        className="inline-flex h-11 shrink-0 items-center justify-center gap-2 rounded bg-ink px-4 text-sm font-semibold text-white hover:bg-accent"
      >
        <FilePlus size={16} strokeWidth={2} />
        Publish a schema
      </Link>
    </div>
  );
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `npx vitest run src/features/schemas/components/HomeSearch.test.tsx`
Expected: PASS.

- [ ] **Step 5: Write `RegistryHome`**

```tsx
import { HomeSearch } from './HomeSearch';
import { YourSchemasPanel } from './YourSchemasPanel';
import { NeedsAttention } from './NeedsAttention';
import { RegistryOverview } from './RegistryOverview';
import type { SchemaSummary } from '../types/schema.types';
import type { SchemaView } from '../hooks/use-schema-url-state';

export function RegistryHome({
  items,
  onNavigate,
  onSearch,
}: {
  items: SchemaSummary[];
  onNavigate: (view: SchemaView) => void;
  onSearch: (q: string) => void;
}) {
  return (
    <div className="space-y-6">
      <HomeSearch onSubmit={onSearch} />
      <YourSchemasPanel />
      <div>
        <h2 className="mb-3 text-sm font-semibold text-ink">Needs attention</h2>
        <NeedsAttention items={items} onNavigate={onNavigate} />
      </div>
      <div>
        <h2 className="mb-3 text-sm font-semibold text-ink">Registry at a glance</h2>
        <RegistryOverview onNavigate={onNavigate} />
      </div>
    </div>
  );
}
```

Match `RegistryOverview`'s real prop name from Task 3 rather than assuming `onNavigate`.

- [ ] **Step 6: Wire it into the page**

In `src/pages/SchemaListPage.tsx`, find where `view === 'overview'` currently renders `RegistryOverview` and render `RegistryHome` instead:

```tsx
{state.view === 'overview' ? (
  <RegistryHome
    items={items}
    onNavigate={(view) => set({ view })}
    onSearch={(q) => set({ view: 'all', search: q })}
  />
) : (
  /* leave the existing table branch exactly as it is */
)}
```

Bind `items` to the already-fetched schema list in that component. Add `RegistryHome` to the import from `@/features/schemas`, and export it from `src/features/schemas/index.ts`:

```ts
export { RegistryHome } from './components/RegistryHome';
```

- [ ] **Step 7: Verify in the browser**

Run: `npm run dev`, open `http://localhost:5173/schemas`.
Expected: Home renders with the search hero, Publish button, Your schemas panel, three attention lanes and three stat tiles. Typing a query and pressing Enter navigates to the table with `view=all&search=<q>` in the URL and the results filtered.

- [ ] **Step 8: Verify and commit**

Run: `npx tsc -b --noEmit && npx vitest run`

```bash
git add src/features/schemas/components/HomeSearch.tsx \
        src/features/schemas/components/HomeSearch.test.tsx \
        src/features/schemas/components/RegistryHome.tsx \
        src/features/schemas/index.ts \
        src/pages/SchemaListPage.tsx
git commit -m "$(cat <<'EOF'
UIFPE-507 #comment Compose the task-shaped Home and feed its search into the table

Home has no result list of its own; submitting navigates to view=all&search=,
so there stays exactly one result surface.

Co-Authored-By: <the exact trailer your harness instruction gives you>
EOF
)"
```

---

### Task 10: The publish service layer

**Files:**
- Create: `src/features/publish/types/publish.types.ts`
- Create: `src/features/publish/services/publish.service.ts`
- Create: `src/features/publish/hooks/use-register-schema.ts`
- Create: `src/features/publish/hooks/use-register-schema.test.tsx`

**Interfaces:**
- Consumes: `apiClient` from `@/services/api-client`.
- Produces:
  - `DirectSchemaRegisterRequest { service_urn: string; content: string; type_override?: string | null; data_classification: 'public'|'internal'|'confidential'|'restricted'; pii_fields?: string[]; lifecycle?: 'active'|'deprecated' }`
  - `SchemaRegistrationResult { topic: string; registered: boolean; schema_type: string | null; error: string | null }`
  - `registerSchema(groupId: string, artifactId: string, body: DirectSchemaRegisterRequest): Promise<SchemaRegistrationResult>`
  - `useRegisterSchema()` — a TanStack mutation taking `{ groupId, artifactId, body }`

- [ ] **Step 1: Write the types**

`src/features/publish/types/publish.types.ts`:

```ts
/** Mirrors DirectSchemaRegisterRequest in stacks/apicurio/src/app/schemas/service.py */
export interface DirectSchemaRegisterRequest {
  service_urn: string;
  content: string;
  type_override?: string | null;
  data_classification: 'public' | 'internal' | 'confidential' | 'restricted';
  pii_fields?: string[];
  lifecycle?: 'active' | 'deprecated';
}

/**
 * Mirrors SchemaRegistrationResult. Note `registered` — the endpoint returns
 * HTTP 200 with registered:false and an error string on failure, so a 200 is
 * not success.
 */
export interface SchemaRegistrationResult {
  topic: string;
  registered: boolean;
  schema_type: string | null;
  error: string | null;
}
```

- [ ] **Step 2: Write the failing hook test**

`src/features/publish/hooks/use-register-schema.test.tsx`:

```tsx
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { useRegisterSchema } from './use-register-schema';
import type { DirectSchemaRegisterRequest } from '../types/publish.types';

const API = 'http://localhost:8000/api/v1';

const BODY: DirectSchemaRegisterRequest = {
  service_urn: 'urn:pearson:service:commerce:checkout-svc',
  content: '{"type":"record","name":"OrderPlaced","fields":[]}',
  data_classification: 'confidential',
  pii_fields: ['customerEmail'],
  lifecycle: 'active',
};

const server = setupServer();
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function wrapper({ children }: { children: React.ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

it('posts to the subject path and returns the result', async () => {
  server.use(
    http.post(`${API}/schemas/default/commerce.order.placed-value`, () =>
      HttpResponse.json({ topic: 'commerce.order.placed-value', registered: true, schema_type: 'AVRO', error: null }),
    ),
  );
  const { result } = renderHook(() => useRegisterSchema(), { wrapper });
  result.current.mutate({ groupId: 'default', artifactId: 'commerce.order.placed-value', body: BODY });
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  expect(result.current.data?.registered).toBe(true);
});

it('surfaces a soft failure that arrives as HTTP 200', async () => {
  server.use(
    http.post(`${API}/schemas/default/commerce.order.placed-value`, () =>
      HttpResponse.json({ topic: 'commerce.order.placed-value', registered: false, schema_type: null, error: 'Unknown service URN: urn:bogus' }),
    ),
  );
  const { result } = renderHook(() => useRegisterSchema(), { wrapper });
  result.current.mutate({ groupId: 'default', artifactId: 'commerce.order.placed-value', body: BODY });
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  expect(result.current.data?.registered).toBe(false);
  expect(result.current.data?.error).toMatch(/unknown service urn/i);
});
```

- [ ] **Step 3: Run to verify it fails**

Run: `npx vitest run src/features/publish/hooks/use-register-schema.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 4: Write the service**

`src/features/publish/services/publish.service.ts`:

```ts
import { apiClient } from '@/services/api-client';
import type { DirectSchemaRegisterRequest, SchemaRegistrationResult } from '../types/publish.types';

export async function registerSchema(
  groupId: string,
  artifactId: string,
  body: DirectSchemaRegisterRequest,
): Promise<SchemaRegistrationResult> {
  const { data } = await apiClient.post<SchemaRegistrationResult>(
    `/schemas/${encodeURIComponent(groupId)}/${encodeURIComponent(artifactId)}`,
    body,
  );
  return data;
}
```

- [ ] **Step 5: Write the hook**

`src/features/publish/hooks/use-register-schema.ts`:

```ts
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { registerSchema } from '../services/publish.service';
import type { DirectSchemaRegisterRequest, SchemaRegistrationResult } from '../types/publish.types';

interface RegisterArgs {
  groupId: string;
  artifactId: string;
  body: DirectSchemaRegisterRequest;
}

export function useRegisterSchema() {
  const queryClient = useQueryClient();
  return useMutation<SchemaRegistrationResult, Error, RegisterArgs>({
    mutationFn: ({ groupId, artifactId, body }) => registerSchema(groupId, artifactId, body),
    onSuccess: (result) => {
      // Only invalidate when something actually landed: the endpoint returns
      // 200 with registered:false on failure.
      if (result.registered) {
        queryClient.invalidateQueries({ queryKey: ['schemas'] });
      }
    },
  });
}
```

- [ ] **Step 6: Run to verify it passes, then commit**

Run: `npx tsc -b --noEmit && npx vitest run src/features/publish/`

```bash
git add src/features/publish/
git commit -m "$(cat <<'EOF'
UIFPE-507 #comment Add the publish service layer, keyed on registered not HTTP 200

The registration endpoint returns 200 with registered:false and an error
string on failure, so the cache is only invalidated when something landed.

Co-Authored-By: <the exact trailer your harness instruction gives you>
EOF
)"
```

---

### Task 11: The publish form

**Files:**
- Create: `src/features/publish/components/SchemaPublishForm.tsx`
- Create: `src/features/publish/components/SchemaPublishForm.test.tsx`

Recover the file reader, type list, classification list and PII tag input from git rather than rewriting:

```bash
git show HEAD:stacks/apicurio/ui/src/features/onboarding/components/SchemaAttachForm.tsx
```

**Interfaces:**
- Consumes: `DirectSchemaRegisterRequest` (Task 10), `useServiceList` from `@/features/services`.
- Produces: `export function SchemaPublishForm({ onSubmit, isSubmitting }: { onSubmit: (args: { groupId: string; artifactId: string; body: DirectSchemaRegisterRequest }) => void; isSubmitting: boolean }): JSX.Element`

- [ ] **Step 1: Write the failing tests**

```tsx
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { SchemaPublishForm } from './SchemaPublishForm';

const API = 'http://localhost:8000/api/v1';

const SERVICES = [
  { ci_id: 'CI0093423', urn: 'urn:pearson:service:commerce:checkout-svc', name: 'checkout-svc', domain: 'commerce', produces_count: 1, consumes_count: 2, app_id: null, app_name: null },
];

const server = setupServer(http.get(`${API}/services`, () => HttpResponse.json(SERVICES)));
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function renderForm(onSubmit = vi.fn()) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <SchemaPublishForm onSubmit={onSubmit} isSubmitting={false} />
    </QueryClientProvider>,
  );
  return onSubmit;
}

it('warns when the body is not parseable JSON', async () => {
  renderForm();
  fireEvent.change(screen.getByLabelText(/schema body/i), { target: { value: '{not json' } });
  await waitFor(() => expect(screen.getByText(/not valid json/i)).toBeInTheDocument());
});

it('warns that a non-JSON type gets no metadata block', async () => {
  renderForm();
  fireEvent.change(screen.getByLabelText(/schema type/i), { target: { value: 'PROTOBUF' } });
  await waitFor(() =>
    expect(screen.getByText(/no metadata block/i)).toBeInTheDocument(),
  );
});

it('blocks submission until a service is chosen', async () => {
  const onSubmit = renderForm();
  fireEvent.change(screen.getByLabelText(/subject/i), { target: { value: 'commerce.order.placed-value' } });
  fireEvent.change(screen.getByLabelText(/schema body/i), { target: { value: '{"type":"record","name":"X","fields":[]}' } });
  fireEvent.click(screen.getByRole('button', { name: /publish/i }));
  expect(onSubmit).not.toHaveBeenCalled();
});

it('submits the full request once every required field is set', async () => {
  const onSubmit = renderForm();
  await waitFor(() => expect(screen.getByLabelText(/owning service/i)).toBeInTheDocument());
  fireEvent.change(screen.getByLabelText(/subject/i), { target: { value: 'commerce.order.placed-value' } });
  fireEvent.change(screen.getByLabelText(/schema body/i), { target: { value: '{"type":"record","name":"X","fields":[]}' } });
  fireEvent.change(screen.getByLabelText(/owning service/i), { target: { value: 'urn:pearson:service:commerce:checkout-svc' } });
  fireEvent.click(screen.getByLabelText(/confidential/i));
  fireEvent.click(screen.getByRole('button', { name: /publish/i }));
  await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
  expect(onSubmit).toHaveBeenCalledWith({
    groupId: 'default',
    artifactId: 'commerce.order.placed-value',
    body: expect.objectContaining({
      service_urn: 'urn:pearson:service:commerce:checkout-svc',
      data_classification: 'confidential',
      lifecycle: 'active',
    }),
  });
});
```

- [ ] **Step 2: Run to verify they fail**

Run: `npx vitest run src/features/publish/components/SchemaPublishForm.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Write the form**

Use a plain `<textarea>` for the body, not Monaco — Monaco does not render in jsdom and would make this component untestable. Monaco stays where it already is, in `SchemaViewer`.

```tsx
import { useMemo, useRef, useState } from 'react';
import { Upload, X } from 'lucide-react';
import { useServiceList } from '@/features/services';
import type { DirectSchemaRegisterRequest } from '../types/publish.types';

const SCHEMA_TYPES = ['', 'AVRO', 'JSON', 'OPENAPI', 'ASYNCAPI', 'PROTOBUF', 'GRAPHQL', 'XSD'] as const;
const JSON_BASED = new Set(['AVRO', 'JSON', 'OPENAPI', 'ASYNCAPI']);
const CLASSIFICATIONS = ['public', 'internal', 'confidential', 'restricted'] as const;
const SUBJECT_SHAPE = /^[a-z0-9]+(\.[a-z0-9-]+)+-(value|key)$/;

interface Props {
  onSubmit: (args: { groupId: string; artifactId: string; body: DirectSchemaRegisterRequest }) => void;
  isSubmitting: boolean;
}

export function SchemaPublishForm({ onSubmit, isSubmitting }: Props) {
  const { data: services } = useServiceList();
  const fileRef = useRef<HTMLInputElement>(null);

  const [artifactId, setArtifactId] = useState('');
  const [groupId, setGroupId] = useState('default');
  const [content, setContent] = useState('');
  const [serviceUrn, setServiceUrn] = useState('');
  const [typeOverride, setTypeOverride] = useState('');
  const [classification, setClassification] = useState<(typeof CLASSIFICATIONS)[number] | ''>('');
  const [lifecycle, setLifecycle] = useState<'active' | 'deprecated'>('active');
  const [piiFields, setPiiFields] = useState<string[]>([]);

  const jsonError = useMemo(() => {
    if (!content.trim()) return null;
    try {
      JSON.parse(content);
      return null;
    } catch {
      return 'Schema body is not valid JSON. The registry cannot parse it and will reject it.';
    }
  }, [content]);

  const nonJsonWarning =
    typeOverride && !JSON_BASED.has(typeOverride)
      ? `${typeOverride} bodies get no metadata block written, so the schema will be non-compliant the moment it registers. Register it, then fix compliance manually.`
      : null;

  const subjectWarning =
    artifactId && !SUBJECT_SHAPE.test(artifactId)
      ? 'Subjects in this registry look like domain.entity.event-value. Yours does not, which is allowed but unconventional.'
      : null;

  const canSubmit =
    !!artifactId.trim() && !!content.trim() && !!serviceUrn && !!classification && !jsonError && !isSubmitting;

  function handleFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (ev) => setContent((ev.target?.result as string) ?? '');
    reader.readAsText(file);
    e.target.value = '';
  }

  function commitPii(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key !== 'Enter' && e.key !== ',') return;
    e.preventDefault();
    const raw = e.currentTarget.value.trim();
    if (raw && !piiFields.includes(raw)) setPiiFields([...piiFields, raw]);
    e.currentTarget.value = '';
  }

  return (
    <form
      className="space-y-5"
      onSubmit={(e) => {
        e.preventDefault();
        if (!canSubmit || !classification) return;
        onSubmit({
          groupId: groupId.trim() || 'default',
          artifactId: artifactId.trim(),
          body: {
            service_urn: serviceUrn,
            content,
            type_override: typeOverride || null,
            data_classification: classification,
            pii_fields: piiFields,
            lifecycle,
          },
        });
      }}
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <div>
          <label htmlFor="pub-subject" className="block text-xs font-medium text-ink">Subject</label>
          <input
            id="pub-subject"
            value={artifactId}
            onChange={(e) => setArtifactId(e.target.value)}
            placeholder="commerce.order.placed-value"
            className="mt-1 h-9 w-full rounded border border-paper-line px-2 font-mono text-xs"
          />
          {subjectWarning && <p className="mt-1 text-xs text-ink-mute">{subjectWarning}</p>}
        </div>
        <div>
          <label htmlFor="pub-group" className="block text-xs font-medium text-ink">Group</label>
          <input
            id="pub-group"
            value={groupId}
            onChange={(e) => setGroupId(e.target.value)}
            className="mt-1 h-9 w-full rounded border border-paper-line px-2 font-mono text-xs"
          />
        </div>
      </div>

      <div>
        <label htmlFor="pub-service" className="block text-xs font-medium text-ink">Owning service</label>
        <select
          id="pub-service"
          value={serviceUrn}
          onChange={(e) => setServiceUrn(e.target.value)}
          className="mt-1 h-9 w-full rounded border border-paper-line px-2 text-xs"
        >
          <option value="">Choose a service…</option>
          {(services ?? []).map((s) => (
            <option key={s.urn} value={s.urn}>{s.name} — {s.domain}</option>
          ))}
        </select>
        <p className="mt-1 text-xs text-ink-mute">
          Required. Registration fails outright on a URN the CMDB cannot resolve.
        </p>
      </div>

      <div>
        <div className="flex items-center justify-between">
          <label htmlFor="pub-body" className="block text-xs font-medium text-ink">Schema body</label>
          <button
            type="button"
            onClick={() => fileRef.current?.click()}
            className="inline-flex items-center gap-1 text-xs font-medium text-accent hover:underline"
          >
            <Upload size={12} strokeWidth={2} /> Upload a file
          </button>
          <input ref={fileRef} type="file" onChange={handleFile} className="hidden" />
        </div>
        <textarea
          id="pub-body"
          value={content}
          onChange={(e) => setContent(e.target.value)}
          rows={12}
          spellCheck={false}
          className="mt-1 w-full rounded border border-paper-line p-2 font-mono text-xs"
        />
        {jsonError && <p className="mt-1 text-xs font-medium text-red-700">{jsonError}</p>}
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <div>
          <label htmlFor="pub-type" className="block text-xs font-medium text-ink">Schema type</label>
          <select
            id="pub-type"
            value={typeOverride}
            onChange={(e) => setTypeOverride(e.target.value)}
            className="mt-1 h-9 w-full rounded border border-paper-line px-2 text-xs"
          >
            {SCHEMA_TYPES.map((t) => (
              <option key={t || 'auto'} value={t}>{t || 'Detect automatically'}</option>
            ))}
          </select>
          {nonJsonWarning && <p className="mt-1 text-xs font-medium text-amber-700">{nonJsonWarning}</p>}
        </div>
        <div>
          <label htmlFor="pub-lifecycle" className="block text-xs font-medium text-ink">Lifecycle</label>
          <select
            id="pub-lifecycle"
            value={lifecycle}
            onChange={(e) => setLifecycle(e.target.value as 'active' | 'deprecated')}
            className="mt-1 h-9 w-full rounded border border-paper-line px-2 text-xs"
          >
            <option value="active">active</option>
            <option value="deprecated">deprecated</option>
          </select>
        </div>
      </div>

      <fieldset>
        <legend className="text-xs font-medium text-ink">Data classification</legend>
        <div className="mt-1.5 flex flex-wrap gap-4">
          {CLASSIFICATIONS.map((c) => (
            <label key={c} htmlFor={`pub-class-${c}`} className="flex items-center gap-1.5 text-xs text-ink">
              <input
                id={`pub-class-${c}`}
                type="radio"
                name="classification"
                value={c}
                checked={classification === c}
                onChange={() => setClassification(c)}
              />
              {c}
            </label>
          ))}
        </div>
      </fieldset>

      <div>
        <label htmlFor="pub-pii" className="block text-xs font-medium text-ink">PII fields</label>
        <input
          id="pub-pii"
          onKeyDown={commitPii}
          placeholder="Field name, then Enter"
          className="mt-1 h-9 w-full rounded border border-paper-line px-2 font-mono text-xs"
        />
        {piiFields.length > 0 && (
          <ul className="mt-2 flex flex-wrap gap-1.5">
            {piiFields.map((f) => (
              <li key={f} className="inline-flex items-center gap-1 rounded bg-paper-line px-1.5 py-0.5 font-mono text-[11px]">
                {f}
                <button type="button" aria-label={`Remove ${f}`} onClick={() => setPiiFields(piiFields.filter((x) => x !== f))}>
                  <X size={10} strokeWidth={2.5} />
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      <button
        type="submit"
        disabled={!canSubmit}
        className="h-10 rounded bg-ink px-5 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-40 hover:bg-accent"
      >
        {isSubmitting ? 'Publishing…' : 'Publish schema'}
      </button>
    </form>
  );
}
```

- [ ] **Step 4: Run to verify they pass**

Run: `npx vitest run src/features/publish/components/SchemaPublishForm.test.tsx`
Expected: PASS, all 4.

- [ ] **Step 5: Commit**

```bash
git add src/features/publish/components/SchemaPublishForm.tsx \
        src/features/publish/components/SchemaPublishForm.test.tsx
git commit -m "$(cat <<'EOF'
UIFPE-507 #comment Add the publish form, restored from the deleted attach form

Warns on unparseable JSON and on non-JSON types, where inject_metadata writes
no metadata block and the schema lands non-compliant.

Co-Authored-By: <the exact trailer your harness instruction gives you>
EOF
)"
```

---

### Task 12: The publish page, its result panel, and the route

**Files:**
- Create: `src/features/publish/components/PublishResult.tsx`
- Create: `src/features/publish/components/PublishResult.test.tsx`
- Create: `src/features/publish/index.ts`
- Create: `src/pages/PublishPage.tsx`
- Modify: `src/App.tsx`

**Interfaces:**
- Consumes: `SchemaRegistrationResult` (Task 10), `SchemaPublishForm` (Task 11), `useRegisterSchema` (Task 10).
- Produces: `export function PublishResult({ result, groupId }: { result: SchemaRegistrationResult; groupId: string }): JSX.Element`, `export function PublishPage(): JSX.Element`, route `/schemas/publish`.

- [ ] **Step 1: Write the failing test**

`src/features/publish/components/PublishResult.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { PublishResult } from './PublishResult';

it('reports a soft failure as a failure, not a success', () => {
  render(
    <MemoryRouter>
      <PublishResult groupId="default" result={{ topic: 'commerce.order.placed-value', registered: false, schema_type: null, error: 'Unknown service URN: urn:bogus' }} />
    </MemoryRouter>,
  );
  expect(screen.getByText(/not registered/i)).toBeInTheDocument();
  expect(screen.getByText(/unknown service urn/i)).toBeInTheDocument();
  expect(screen.queryByText(/registered successfully/i)).not.toBeInTheDocument();
});

it('links to the new subject on success', () => {
  render(
    <MemoryRouter>
      <PublishResult groupId="default" result={{ topic: 'commerce.order.placed-value', registered: true, schema_type: 'AVRO', error: null }} />
    </MemoryRouter>,
  );
  expect(screen.getByText(/registered successfully/i)).toBeInTheDocument();
  expect(screen.getByRole('link', { name: /view schema/i })).toHaveAttribute(
    'href',
    '/schemas/default/commerce.order.placed-value',
  );
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `npx vitest run src/features/publish/components/PublishResult.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Write `PublishResult`**

```tsx
import { Link } from 'react-router-dom';
import { CheckCircle2, XCircle } from 'lucide-react';
import type { SchemaRegistrationResult } from '../types/publish.types';

export function PublishResult({ result, groupId }: { result: SchemaRegistrationResult; groupId: string }) {
  if (!result.registered) {
    return (
      <div className="rounded border border-red-300 bg-red-50 p-4">
        <p className="flex items-center gap-2 text-sm font-semibold text-red-800">
          <XCircle size={16} strokeWidth={2} />
          {result.topic} was not registered
        </p>
        <p className="mt-1.5 font-mono text-xs text-red-900">
          {result.error ?? 'The registry gave no reason.'}
        </p>
      </div>
    );
  }

  return (
    <div className="rounded border border-emerald-300 bg-emerald-50 p-4">
      <p className="flex items-center gap-2 text-sm font-semibold text-emerald-900">
        <CheckCircle2 size={16} strokeWidth={2} />
        {result.topic} registered successfully
      </p>
      <p className="mt-1.5 text-xs text-emerald-900">
        Detected as {result.schema_type ?? 'an unknown type'}. Its metadata block was
        written into the body, so it starts compliant.
      </p>
      <Link
        to={`/schemas/${encodeURIComponent(groupId)}/${encodeURIComponent(result.topic)}`}
        className="mt-3 inline-block text-xs font-semibold text-emerald-900 underline"
      >
        View schema
      </Link>
    </div>
  );
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `npx vitest run src/features/publish/components/PublishResult.test.tsx`
Expected: PASS.

- [ ] **Step 5: Write the barrel and the page**

`src/features/publish/index.ts`:

```ts
export { SchemaPublishForm } from './components/SchemaPublishForm';
export { PublishResult } from './components/PublishResult';
export { useRegisterSchema } from './hooks/use-register-schema';
export { registerSchema } from './services/publish.service';
export type { DirectSchemaRegisterRequest, SchemaRegistrationResult } from './types/publish.types';
```

`src/pages/PublishPage.tsx`:

```tsx
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { ArrowLeft } from 'lucide-react';
import { SchemaPublishForm, PublishResult, useRegisterSchema } from '@/features/publish';

export function PublishPage() {
  const register = useRegisterSchema();
  // The result carries only `topic`, so remember the group we submitted under.
  const [submittedGroup, setSubmittedGroup] = useState('default');

  return (
    <div className="max-w-3xl space-y-6">
      <div>
        <Link to="/schemas" className="inline-flex items-center gap-1 text-xs font-medium text-ink-mute hover:text-ink">
          <ArrowLeft size={12} strokeWidth={2} /> Back to the registry
        </Link>
        <h1 className="mt-2 text-xl font-semibold tracking-tight text-ink">Publish a schema</h1>
        <p className="mt-1 max-w-prose text-sm text-ink-mute">
          Registering here is what makes a schema compliant: the registry writes the
          Pearson metadata block into the body and mirrors it onto the artifact's
          properties. A schema pushed straight to Apicurio skips both and lands
          non-compliant.
        </p>
      </div>

      {register.data && <PublishResult result={register.data} groupId={submittedGroup} />}

      {register.isError && (
        <div className="rounded border border-red-300 bg-red-50 p-4">
          <p className="text-sm font-semibold text-red-800">The request itself failed</p>
          <p className="mt-1 font-mono text-xs text-red-900">{register.error.message}</p>
        </div>
      )}

      <SchemaPublishForm
        onSubmit={(args) => {
          setSubmittedGroup(args.groupId);
          register.mutate(args);
        }}
        isSubmitting={register.isPending}
      />
    </div>
  );
}
```

- [ ] **Step 6: Add the route**

In `src/App.tsx`, add the import and the route. The publish route **must come before** `/schemas/:groupId/:id`, or `publish` is captured as a `groupId`:

```tsx
import { PublishPage } from '@/pages/PublishPage';
```

```tsx
        <Route path="/schemas" element={<SchemaListPage />} />
        <Route path="/schemas/publish" element={<PublishPage />} />
        <Route path="/schemas/:groupId/:id" element={<SchemaDetailPage />} />
```

- [ ] **Step 7: Verify in the browser**

Run: `npm run dev`, open `http://localhost:5173/schemas/publish`.
Expected: the form renders and the owning-service dropdown populates. Submitting with a bogus subject should surface the registry's own error in a red panel rather than reporting success. (With no BFF running locally, expect the "request itself failed" panel — that is the correct behaviour for a dead API.)

- [ ] **Step 8: Verify and commit**

Run: `npx tsc -b --noEmit && npx vitest run`

```bash
git add src/features/publish/ src/pages/PublishPage.tsx src/App.tsx
git commit -m "$(cat <<'EOF'
UIFPE-507 #comment Add the publish page and route

The route is ordered before /schemas/:groupId/:id so "publish" is not captured
as a group id.

Co-Authored-By: <the exact trailer your harness instruction gives you>
EOF
)"
```

---

### Task 13: Version timeline and diff on the subject page

`GET /schemas/{group}/{artifact}` accepts `?version=` and `getSchemaDetail(groupId, artifactId, version?)` already forwards it, so this needs no BFF change.

**Files:**
- Create: `src/features/schemas/components/VersionTimeline.tsx`
- Create: `src/features/schemas/components/VersionTimeline.test.tsx`
- Modify: `src/features/schemas/components/SchemaDetail.tsx`
- Modify: `src/features/schemas/index.ts`

**Interfaces:**
- Consumes: `SchemaVersionRead { version, state, created_on, created_by }`, `getSchemaDetail` from `../services/schemas.service`.
- Produces: `export function VersionTimeline({ groupId, artifactId, versions, currentVersion }: { groupId: string; artifactId: string; versions: SchemaVersionRead[]; currentVersion: string }): JSX.Element`

- [ ] **Step 1: Write the failing test**

Monaco does not render in jsdom, so assert on the timeline and the compare control, not on the diff editor's contents.

```tsx
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { VersionTimeline } from './VersionTimeline';
import type { SchemaVersionRead } from '../types/schema.types';

vi.mock('@monaco-editor/react', () => ({
  DiffEditor: () => <div data-testid="diff-editor" />,
}));

const VERSIONS: SchemaVersionRead[] = [
  { version: '3', state: 'ENABLED', created_on: '2026-07-14T09:00:00Z', created_by: 'r.karunakaran' },
  { version: '2', state: 'ENABLED', created_on: '2026-05-02T09:00:00Z', created_by: 's.mehta' },
  { version: '1', state: 'ENABLED', created_on: '2026-03-11T09:00:00Z', created_by: 'seed' },
];

function renderTimeline() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <VersionTimeline groupId="default" artifactId="commerce.order.placed-value" versions={VERSIONS} currentVersion="3" />
    </QueryClientProvider>,
  );
}

it('lists every version with who changed it and when', () => {
  renderTimeline();
  expect(screen.getByText('v3')).toBeInTheDocument();
  expect(screen.getByText('v1')).toBeInTheDocument();
  expect(screen.getByText(/r\.karunakaran/)).toBeInTheDocument();
  expect(screen.getByText(/s\.mehta/)).toBeInTheDocument();
});

it('marks the current version', () => {
  renderTimeline();
  expect(screen.getByText(/current/i)).toBeInTheDocument();
});

it('offers a comparison between two versions', () => {
  renderTimeline();
  expect(screen.getByLabelText(/compare/i)).toBeInTheDocument();
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `npx vitest run src/features/schemas/components/VersionTimeline.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Write the component**

```tsx
import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { DiffEditor } from '@monaco-editor/react';
import { getSchemaDetail } from '../services/schemas.service';
import type { SchemaVersionRead } from '../types/schema.types';

interface Props {
  groupId: string;
  artifactId: string;
  versions: SchemaVersionRead[];
  currentVersion: string;
}

function useVersionContent(groupId: string, artifactId: string, version: string | null) {
  return useQuery({
    queryKey: ['schemas', groupId, artifactId, 'content', version],
    queryFn: () => getSchemaDetail(groupId, artifactId, version ?? undefined),
    enabled: !!version,
  });
}

export function VersionTimeline({ groupId, artifactId, versions, currentVersion }: Props) {
  const [compareTo, setCompareTo] = useState<string>('');

  const left = useVersionContent(groupId, artifactId, compareTo || null);
  const right = useVersionContent(groupId, artifactId, compareTo ? currentVersion : null);

  const olderVersions = versions.filter((v) => v.version !== currentVersion);

  return (
    <div className="space-y-4">
      <ol className="divide-y divide-paper-line">
        {versions.map((v) => (
          <li key={v.version} className="flex items-baseline gap-3 py-2.5">
            <span className="w-8 shrink-0 font-mono text-xs font-semibold text-accent">v{v.version}</span>
            <div className="min-w-0 flex-1">
              <p className="text-xs text-ink">
                {v.created_by ?? 'unknown'}
                {v.created_on && (
                  <span className="text-ink-mute"> · {new Date(v.created_on).toISOString().slice(0, 10)}</span>
                )}
              </p>
              <p className="text-[11px] text-ink-mute">{v.state}</p>
            </div>
            {v.version === currentVersion && (
              <span className="shrink-0 rounded bg-paper-line px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-ink-mute">
                Current
              </span>
            )}
          </li>
        ))}
      </ol>

      {olderVersions.length > 0 && (
        <div>
          <label htmlFor="version-compare" className="block text-xs font-medium text-ink">
            Compare v{currentVersion} against
          </label>
          <select
            id="version-compare"
            value={compareTo}
            onChange={(e) => setCompareTo(e.target.value)}
            className="mt-1 h-8 rounded border border-paper-line px-2 text-xs"
          >
            <option value="">Choose a version…</option>
            {olderVersions.map((v) => (
              <option key={v.version} value={v.version}>v{v.version}</option>
            ))}
          </select>
        </div>
      )}

      {compareTo && left.data && right.data && (
        <div className="h-80 overflow-hidden rounded border border-paper-line">
          <DiffEditor
            original={left.data.content}
            modified={right.data.content}
            language="json"
            options={{ readOnly: true, renderSideBySide: true, minimap: { enabled: false }, scrollBeyondLastLine: false }}
          />
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `npx vitest run src/features/schemas/components/VersionTimeline.test.tsx`
Expected: PASS, all 3.

- [ ] **Step 5: Mount it on the subject page**

Export it from `src/features/schemas/index.ts`:

```ts
export { VersionTimeline } from './components/VersionTimeline';
```

In `src/features/schemas/components/SchemaDetail.tsx`, render it where version history currently appears, passing the detail object's own fields:

```tsx
<VersionTimeline
  groupId={detail.group_id}
  artifactId={detail.artifact_id}
  versions={detail.versions}
  currentVersion={detail.content_version}
/>
```

Bind `detail` to the name already in scope. Run `npx vitest run src/features/schemas/components/SchemaDetail.test.tsx` afterwards — if the existing 5 tests now fail on Monaco, add the same `vi.mock('@monaco-editor/react', …)` used in Step 1 to that file.

- [ ] **Step 6: Verify the full suite and commit**

Run: `npx tsc -b --noEmit && npx vitest run`
Expected: everything passes.

```bash
git add src/features/schemas/components/VersionTimeline.tsx \
        src/features/schemas/components/VersionTimeline.test.tsx \
        src/features/schemas/components/SchemaDetail.tsx \
        src/features/schemas/components/SchemaDetail.test.tsx \
        src/features/schemas/index.ts
git commit -m "$(cat <<'EOF'
UIFPE-507 #comment Add a version timeline and a version-to-version diff

GET /schemas/{group}/{artifact} already accepts ?version=, so comparing two
versions needed no API change.

Co-Authored-By: <the exact trailer your harness instruction gives you>
EOF
)"
```

---

## Final verification

- [ ] `npx tsc -b --noEmit` — clean
- [ ] `npx vitest run` — all pass; expect roughly 155–165 tests (baseline 136, minus 4 `pending-review` assertions, plus ~26 new)
- [ ] `grep -rn "pending-review\|recharts\|STUB_METRICS" src/` — no matches
- [ ] `npm run dev` and walk the whole path: land on Home → search → results in the table → Publish a schema → submit → result panel → open a subject → version timeline → compare two versions

## Deferred, with reasons

| Deferred | Why | Unblocks when |
|---|---|---|
| "Your schemas" real content | No identity anywhere in the BFF or UI | The parked Entra work lands |
| Server-side `?q=` search | Needs a BFF change; out of this scope | Artifact count nears the 500 client fetch cap, or field/body search is wanted |
| Lifecycle board (proposed → … → retired) | `lifecycle` has 2 values, `retirement_date` is an unpopulated stub, registration is synchronous so there is no draft state | The backend carries the states |
| Structural field view per schema type | Needs a renderer per type (Avro/JSON Schema/OpenAPI/AsyncAPI/Protobuf/XSD) | A follow-on plan |
| Wiring `useRefreshNonCompliant` | Has no caller since `NonCompliantSchemasPage` was deleted; a candidate for the Non-compliant lane's refresh control | A follow-on plan |
