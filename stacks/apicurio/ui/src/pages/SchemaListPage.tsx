import { useMemo, useCallback, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { FileDown, Ticket, X } from 'lucide-react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import {
  useSchemas,
  useNonCompliant,
  useSchemaUrlState,
  filterSchemas,
  SchemaToolbar,
  SchemaTable,
  RegistryOverview,
  IdentityNote,
  refreshNonCompliant,
  downloadBlob,
} from '@/features/schemas';
import type { SchemaSummary, SortField, SchemaView } from '@/features/schemas';
import type { Density } from '@/features/schemas/components/SchemaToolbar';

const DENSITY_KEY = 'schema-density';

/**
 * The remembered row density, or `compact`.
 *
 * Both the read and the value are guarded, for two different reasons that
 * `AppShell` had already learned about its own stored flag and this had not:
 *
 * A private window or blocked site data makes `getItem` THROW rather than
 * return null. Unguarded, that throw happened inside this `useState`
 * initialiser — during first render, with no error boundary anywhere in the
 * tree, so React unmounted the whole app to a blank page. In Safari private
 * browsing the read succeeds and it was the `setItem` on a density click that
 * blanked it, mid-session.
 *
 * And the value was cast to `Density` rather than checked, so any other
 * stored string — hand-edited, or left by an older build — flowed through as
 * a density. `SchemaTable` reads it as `density === 'compact' ? py-2 : py-3`,
 * so an unrecognised value silently meant cozy while the toolbar showed
 * neither density pressed, since that control compares for equality.
 * (`index.css` does carry `[data-density]` rules that would have given those
 * cells no padding at all, but nothing sets that attribute: they are matched
 * only by `SchemaList`, which is not mounted anywhere.)
 */
function readDensity(): Density {
  try {
    const stored = localStorage.getItem(DENSITY_KEY);
    return stored === 'compact' || stored === 'cozy' ? stored : 'compact';
  } catch {
    return 'compact';
  }
}

export function SchemaListPage() {
  const { state, set } = useSchemaUrlState();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const { data: schemaData, isLoading, error } = useSchemas({ limit: 500 });
  const {
    data: complianceData,
    isLoading: complianceLoading,
    isError: complianceError,
  } = useNonCompliant();

  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());

  const [density, setDensity] = useState<Density>(readDensity);

  const handleDensityChange = useCallback((d: Density) => {
    setDensity(d);
    try {
      localStorage.setItem(DENSITY_KEY, d);
    } catch {
      // Not worth surfacing: the table still works, it just won't remember.
    }
  }, []);

  /**
   * `POST /schemas/non-compliant/refresh` re-runs the metadata validation over
   * every subject. `refreshNonCompliant` has had no caller since
   * `NonCompliantSchemasPage` was deleted; the toolbar's refresh button is it.
   *
   * Invalidating `['schemas']` rather than just the compliance key is
   * deliberate — the overview stats carry `non_compliant_schemas`, and they
   * are keyed under the same prefix, so a re-check that left them alone would
   * put the glance card and the table out of step.
   */
  const refresh = useMutation({
    mutationFn: refreshNonCompliant,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['schemas'] }),
  });

  // Memoised because `?? []` is a fresh array on every render, which made it
  // a changing dependency of every memo below it.
  const items = useMemo(() => schemaData?.items ?? [], [schemaData]);

  const nonCompliantIds = useMemo(() => {
    const ids = new Set<string>();
    for (const r of complianceData?.items ?? []) ids.add(`${r.group_id}/${r.artifact_id}`);
    return ids;
  }, [complianceData]);

  // The fetch asks for 500, the BFF's own ceiling, and there is no
  // pagination — so past that this page holds a slice of the registry and
  // must not describe it as the whole.
  const truncated = typeof schemaData?.total === 'number' && items.length < schemaData.total;

  const filteredItems = useMemo(
    () => filterSchemas(items, nonCompliantIds, state),
    [items, nonCompliantIds, state],
  );

  // Clearing the selection when the filters move, during render rather than
  // in an effect. An effect runs after paint, so the selection bar showed a
  // count from the previous filter for a frame — and "Export CSV" in that
  // frame would have exported rows that were no longer on screen.
  const filterScope = [
    state.view,
    state.search,
    state.domain,
    state.classification,
    state.lifecycle,
    state.groupBy,
  ].join('\u0000');
  const [prevFilterScope, setPrevFilterScope] = useState(filterScope);
  if (prevFilterScope !== filterScope) {
    setPrevFilterScope(filterScope);
    setSelectedIds(new Set());
  }

  const selectedItems = useMemo(
    () => filteredItems.filter((i) => selectedIds.has(`${i.group_id}/${i.artifact_id}`)),
    [filteredItems, selectedIds],
  );

  /**
   * The 560px drawer is retired: a row opens the schema page. The page has the
   * facts band, all three readings of the body, the version history and the
   * diff, none of which fitted in a drawer — and the drawer's own tab state
   * lived in `?dtab=`, which the page now owns.
   */
  const handleRowClick = useCallback(
    (item: SchemaSummary) =>
      navigate(
        `/schemas/${encodeURIComponent(item.group_id)}/${encodeURIComponent(item.artifact_id)}`,
      ),
    [navigate],
  );

  const handleSort = useCallback(
    (field: SortField) => {
      if (field === state.sort) {
        set({ dir: state.dir === 'asc' ? 'desc' : 'asc' });
      } else {
        set({ sort: field, dir: 'asc' });
      }
    },
    [state.sort, state.dir, set],
  );

  const handleViewChange = useCallback((view: SchemaView) => set({ view }), [set]);

  /**
   * Everything that can narrow the list, cleared together — which is what the
   * footer's "Show all n" offers. The header search is included even though it
   * is not a facet: it narrows the list just as invisibly from down here.
   */
  const handleShowAll = useCallback(
    () =>
      set({
        view: 'all',
        search: '',
        domain: null,
        classification: null,
        lifecycle: null,
      }),
    [set],
  );

  const handleClearFacets = useCallback(
    () => set({ domain: null, classification: null, lifecycle: null }),
    [set],
  );

  const activeFacets = [
    state.domain && `domain ${state.domain}`,
    state.classification && `classification ${state.classification}`,
    state.lifecycle && `lifecycle ${state.lifecycle}`,
  ].filter((f): f is string => Boolean(f));

  // Straight to the schema page's Compliance tab, which is where the failing
  // rules and their messages are. `dtab` is the page's tab param now.
  const handleComplianceBadgeClick = useCallback(
    (item: SchemaSummary, e: React.MouseEvent) => {
      e.stopPropagation();
      navigate(
        `/schemas/${encodeURIComponent(item.group_id)}/${encodeURIComponent(item.artifact_id)}?dtab=compliance`,
      );
    },
    [navigate],
  );

  /* The list failing is a fact about the whole page: the glance cards and the
   * table are both derived from it, so an empty `items` from a failed fetch
   * would read as an all-clear registry. This branch is first for that
   * reason — do not move it below the content. */
  if (error) {
    return (
      <div className="space-y-2 pt-2">
        <p className="text-sm text-signal-fail font-mono">{(error as Error).message}</p>
        <p className="text-sm text-ink-mute">
          Check that Apicurio is reachable at the URL configured in <code>APICURIO_BASE_URL</code>.
        </p>
      </div>
    );
  }

  return (
    <div className="flex flex-col" style={{ gap: 14 }}>
      <RegistryOverview onNavigate={handleViewChange} complianceError={complianceError} />

      <SchemaToolbar
        items={items}
        state={state}
        density={density}
        isRefreshing={refresh.isPending}
        onViewChange={handleViewChange}
        onFacetChange={(key, value) => set({ [key]: value })}
        onDensityChange={handleDensityChange}
        onRefresh={() => refresh.mutate()}
        onClearAll={handleClearFacets}
      />

      {isLoading ? (
        <p className="text-sm text-ink-mute pt-2">Loading schemas from Apicurio…</p>
      ) : filteredItems.length === 0 && (state.search || activeFacets.length > 0) ? (
        <div className="py-10 text-center">
          {/* Which of the two narrowed it to nothing is the whole message:
            * blaming the query for a filter the user forgot they set sends
            * them to rewrite a search that was fine. */}
          <p className="text-sm font-medium text-ink">
            {state.search && activeFacets.length > 0
              ? `No schemas match “${state.search}” with the filters you have set.`
              : state.search
                ? `No schemas match “${state.search}”.`
                : 'No schemas match the filters you have set.'}
          </p>
          {state.search && (
            <p className="mt-1 text-xs text-ink-mute">
              Searched subject, name, domain, owner or classification. Schema bodies are not
              searched yet.
            </p>
          )}
          {/* This is the surface a search lands on, and the one where the
            * 500-subject fetch ceiling does real damage: "No schemas match"
            * is a claim about the registry, and past 500 only part of it is
            * here to match against. */}
          {truncated && (
            <p className="mt-1 text-xs text-ink-mute">
              Only the first {items.length} of {schemaData?.total} subjects are loaded, so a
              match may exist outside them.
            </p>
          )}
          {activeFacets.length > 0 && (
            <p className="mt-1 text-xs text-ink-mute">Filtering on {activeFacets.join(', ')}.</p>
          )}
          {/* Only `Clear search` here. The toolbar above now carries its own
            * `Clear filters`, and two buttons with the same label in one
            * viewport is a worse affordance than one in a known place —
            * whereas the search itself lives in the app header, where its
            * only clear is the Escape key, so this is the one visible way
            * back from a query that matched nothing. */}
          {state.search && (
            <div className="mt-3 flex items-center justify-center">
              <button
                type="button"
                onClick={() => set({ search: '' })}
                className="text-xs font-medium text-accent hover:underline"
              >
                Clear search
              </button>
            </div>
          )}
        </div>
      ) : (
        <SchemaTable
          items={filteredItems}
          allItems={items}
          nonCompliantIds={nonCompliantIds}
          complianceLoading={complianceLoading}
          complianceError={complianceError}
          // Only this segment is computed FROM the compliance list.
          // Recently-changed and All stand on the schema list alone.
          filterUnavailable={state.view === 'non-compliant' && complianceError}
          // The registry's own count, so the table can tell the difference
          // between "this is the registry" and "this is the first 500 of it".
          registryTotal={schemaData?.total}
          sort={state.sort}
          dir={state.dir}
          activeSubject={state.subject}
          groupBy={state.groupBy}
          selectedIds={selectedIds}
          showRawSubjects={state.showRawSubjects}
          density={density}
          onSort={handleSort}
          onRowClick={handleRowClick}
          onComplianceBadgeClick={handleComplianceBadgeClick}
          onSelectionChange={setSelectedIds}
          onShowAll={handleShowAll}
        />
      )}

      <IdentityNote />

      {/* Bulk action bar */}
      {selectedIds.size > 0 && (
        <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-50 bg-ink text-white rounded-xl shadow-xl px-5 py-3 flex items-center gap-4 min-w-max">
          <span className="text-sm font-medium">{selectedIds.size} selected</span>
          <button
            onClick={() => setSelectedIds(new Set())}
            className="text-white/60 hover:text-white transition-colors"
            aria-label="Clear selection"
          >
            <X size={14} />
          </button>
          <div className="w-px h-4 bg-white/20" />
          <button
            onClick={() => exportCsv(selectedItems)}
            className="flex items-center gap-1.5 text-sm text-white/90 hover:text-white transition-colors"
          >
            <FileDown size={14} />
            Export CSV
          </button>
          <button
            disabled
            title="Raise Jira tickets — not implemented (no Jira integration)"
            className="flex items-center gap-1.5 text-sm text-white/30 cursor-not-allowed"
          >
            <Ticket size={14} />
            Raise Jira
          </button>
        </div>
      )}
    </div>
  );
}

/**
 * One CSV cell: quoted, with quotes doubled, and neutralised if a spreadsheet
 * would read it as a formula.
 *
 * Excel and Sheets strip CSV quoting BEFORE deciding whether a cell is a
 * formula, so quoting alone is not a defence. Every value in this export —
 * domain, classification, lifecycle, owner CI — comes from schema metadata
 * that a publisher supplies through the publish form, so a subject
 * registered with an owner team of `=HYPERLINK(...)` or a `=cmd|…` payload
 * became a live formula in whoever's spreadsheet opened the file.
 *
 * A leading tab prefix is the usual fix and shows up as whitespace in the
 * cell. An apostrophe is what Excel itself uses to mark a literal, so that
 * is what this uses: the cell reads as the text it is.
 */
function csvCell(value: unknown): string {
  const text = String(value);
  const risky = /^[=+\-@\t\r]/.test(text);
  return `"${(risky ? `'${text}` : text).replace(/"/g, '""')}"`;
}

function exportCsv(items: SchemaSummary[]) {
  const headers = ['subject', 'group', 'domain', 'classification', 'lifecycle', 'owner_ci', 'version', 'modified_on'];
  const rows = items.map((item) =>
    [
      item.artifact_id,
      item.group_id,
      item.domain ?? '',
      item.classification ?? '',
      item.lifecycle ?? '',
      item.owner.service ?? '',
      item.latest_version ?? '',
      item.modified_on ?? '',
    ]
      .map(csvCell)
      .join(','),
  );
  const csv = [headers.join(','), ...rows].join('\n');
  // Through the shared helper rather than a second hand-rolled anchor: this
  // copy had the same two faults — a detached anchor, which does nothing in
  // Firefox, and a revoke in the same task as the click.
  downloadBlob(
    new Blob([csv], { type: 'text/csv' }),
    `schemas-${new Date().toISOString().split('T')[0]}.csv`,
  );
}
