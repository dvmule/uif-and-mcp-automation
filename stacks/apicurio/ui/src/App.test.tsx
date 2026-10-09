import { render, screen, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { App } from './App';

// One page made to throw on render, so the boundary is exercised against the
// real tree rather than a stub of it. `/services` is the least entangled
// route — it has no URL state of its own.
vi.mock('@/pages/ServiceListPage', () => ({
  ServiceListPage: () => {
    throw new Error('data.map is not a function');
  },
}));

const API = 'http://localhost:8000/api/v1';

// PublishPage's form fetches the service list; mocked so the route renders
// without a network error muddying the assertion.
const server = setupServer(http.get(`${API}/services`, () => HttpResponse.json([])));
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function renderAppAt(initialUrl: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[initialUrl]}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

// Pins that /schemas/publish has a route at all, and that it resolves to
// PublishPage rather than the /schemas catch-all. It is not a route-ordering
// test: /schemas/:groupId/:id has three segments to this URL's two, so the two
// cannot collide, and react-router v7 ranks by specificity, not by declaration
// order. What would break this test is the publish route being deleted,
// renamed, or nested somewhere it no longer matches.
it('resolves /schemas/publish to the publish page, not the schema-detail page', async () => {
  renderAppAt('/schemas/publish');

  expect(await screen.findByRole('heading', { name: /publish a schema/i })).toBeInTheDocument();

  // Schema-detail-page-only content must not appear.
  expect(screen.queryByText(/could not load schema/i)).not.toBeInTheDocument();
  expect(screen.queryByText(/loading schema…/i)).not.toBeInTheDocument();
  // Nor the /schemas home dashboard, which is where the catch-all would have
  // sent us if the publish route were missing or mis-ordered.
  expect(screen.queryByRole('heading', { name: /schema registry home/i })).not.toBeInTheDocument();
});

describe('a page that throws', () => {
  /**
   * There was no error boundary anywhere in this tree. A render-time throw
   * unmounted the entire app to a blank white page — no message, no nav, no
   * URL to report — and it had already happened twice in development: a
   * fixture whose `data.map` was not a function, and an unguarded
   * `localStorage.getItem` in a `useState` initialiser.
   */
  let consoleError: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    consoleError = vi.spyOn(console, 'error').mockImplementation(() => {});
    server.use(
      http.get(`${API}/schemas/overview`, () =>
        HttpResponse.json({
          total_schemas: 13,
          schemas_with_compatibility_rules: 0,
          non_compliant_schemas: 0,
        }),
      ),
      http.get(`${API}/schemas`, () =>
        HttpResponse.json({ items: [], total: 0, limit: 500, offset: 0 }),
      ),
    );
  });

  afterEach(() => consoleError.mockRestore());

  it('reports the failure and keeps the app navigable', async () => {
    renderAppAt('/services');

    expect(await screen.findByRole('alert')).toBeInTheDocument();
    expect(screen.getByText('This page stopped working')).toBeInTheDocument();
    expect(screen.getByText('data.map is not a function')).toBeInTheDocument();

    // The shell is still there — which is the whole point of the boundary
    // sitting inside it. A blank page leaves the reader with no way out.
    expect(screen.getByRole('navigation', { name: 'Sections' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Schemas/ })).toBeInTheDocument();
  });

  it('clears when you navigate away, rather than following you around', async () => {
    // A boundary does not reset itself. Without the `key={pathname}` the
    // error state outlived the route that caused it, so every subsequent page
    // rendered as broken too.
    renderAppAt('/services');
    expect(await screen.findByRole('alert')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('link', { name: /Schemas/ }));

    // The registry page renders — its glance card, since an empty list shows
    // no table — and the error is gone with the route that caused it.
    expect(await screen.findByText('subjects registered')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(screen.queryByText('This page stopped working')).not.toBeInTheDocument();
  });
});
