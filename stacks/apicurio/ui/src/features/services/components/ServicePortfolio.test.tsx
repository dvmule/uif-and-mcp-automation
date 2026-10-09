import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { ServicePortfolio } from './ServicePortfolio';

const URN = 'urn:pearson:service:product:catalog-svc';
const API = 'http://localhost:8000/api/v1';

const PORTFOLIO = {
  ci_id: 'CI0093421',
  urn: URN,
  name: 'catalog-svc',
  domain: 'product',
  produces: [
    {
      artifact_id: 'product.product.created-value',
      group_id: 'default',
      name: null,
      type: 'AVRO',
      state: 'ENABLED',
      created_on: null,
      modified_on: null,
      latest_version: '1',
      owner: { service: 'CI0093421', team: null, email: null },
      domain: 'product',
      classification: 'internal',
      lifecycle: 'active',
      pii_fields: [],
    },
  ],
  consumes: [
    {
      artifact_id: 'content.asset.published-value',
      group_id: 'default',
      name: null,
      type: 'AVRO',
      state: 'ENABLED',
      created_on: null,
      modified_on: null,
      latest_version: '1',
      owner: { service: 'CI0099999', team: null, email: null },
      domain: 'content',
      classification: 'internal',
      lifecycle: 'active',
      pii_fields: [],
    },
  ],
  dependencies: {
    downstream: [
      { ci_id: 'CI0093422', urn: 'urn:pearson:service:learning:enrollment-svc', name: 'enrollment-svc', domain: 'learning', shared_schema_count: 1 },
    ],
    upstream: [
      { ci_id: 'CI0093430', urn: 'urn:pearson:service:content:content-svc', name: 'content-svc', domain: 'content', shared_schema_count: 1 },
    ],
  },
  app_id: 'SVC01909',
  app_name: 'DAC Product Data Lake',
};

const server = setupServer(
  http.get(`${API}/services/${encodeURIComponent(URN)}/schema-portfolio`, () =>
    HttpResponse.json(PORTFOLIO),
  ),
);

beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function renderPortfolio() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <ServicePortfolio urn={URN} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

it('renders service name after loading', async () => {
  renderPortfolio();
  // Twice by design: the page title, and the rail's focal node, which mirrors
  // the one the dependency graph draws.
  await waitFor(() => expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('catalog-svc'));
  expect(screen.getAllByText('catalog-svc')).toHaveLength(2);
});

it('says in the title that the whole page is read-only', async () => {
  renderPortfolio();
  await waitFor(() => expect(screen.getByText('read-only')).toBeInTheDocument());
  expect(screen.getByText('Nothing to edit here')).toBeInTheDocument();
  expect(
    screen.getByText(/the registry will pick it up on the next sync/i),
  ).toBeInTheDocument();
  // Read-only means no affordances at all, not disabled ones.
  expect(screen.queryByRole('button', { name: /edit|delete|create|save/i })).not.toBeInTheDocument();
});

it('says the dependents are inferred, not declared', async () => {
  renderPortfolio();
  await waitFor(() =>
    expect(screen.getByText('Derived from shared schemas, not declared anywhere.')).toBeInTheDocument(),
  );
});

it('renders Produces tab with schema rows', async () => {
  renderPortfolio();
  await waitFor(() => screen.getByText('product.product.created-value'));
  expect(screen.getByText('product.product.created-value')).toBeInTheDocument();
});

it('switching to Consumes tab shows consume schemas', async () => {
  renderPortfolio();
  // `Consumes` is both a tab label and a facts-band label, so go by role.
  await waitFor(() => screen.getByRole('button', { name: /^Consumes/ }));
  fireEvent.click(screen.getByRole('button', { name: /^Consumes/ }));
  await waitFor(() => screen.getByText('content.asset.published-value'));
  expect(screen.getByText('content.asset.published-value')).toBeInTheDocument();
});

it('tab count badges reflect portfolio size', async () => {
  renderPortfolio();
  await waitFor(() => screen.getByRole('heading', { level: 1 }));
  // Produces, Consumes and Dependents tabs each show count badge
  const ones = screen.getAllByText('1');
  expect(ones.length).toBeGreaterThanOrEqual(2);
});

it('renders Dependents tab button', async () => {
  renderPortfolio();
  // `Dependents` is the tab label and the rail's own heading — the rail
  // orients, the tab lists. Go by role to mean the tab.
  await waitFor(() => expect(screen.getByRole('button', { name: /^Dependents/ })).toBeInTheDocument());
});

it('switching to Dependents tab shows downstream and upstream sections', async () => {
  renderPortfolio();
  await waitFor(() => screen.getByRole('button', { name: /^Dependents/ }));
  fireEvent.click(screen.getByRole('button', { name: /^Dependents/ }));
  await waitFor(() => screen.getByText('enrollment-svc'));
  expect(screen.getByText('enrollment-svc')).toBeInTheDocument();
  expect(screen.getByText('content-svc')).toBeInTheDocument();
});

it('dependent service name links to service portfolio page', async () => {
  renderPortfolio();
  await waitFor(() => screen.getByRole('button', { name: /^Dependents/ }));
  fireEvent.click(screen.getByRole('button', { name: /^Dependents/ }));
  await waitFor(() => screen.getByText('enrollment-svc'));
  const link = screen.getByText('enrollment-svc').closest('a');
  expect(link).toHaveAttribute('href', expect.stringContaining('enrollment-svc'));
});

it('shows error state on 404', async () => {
  server.use(
    http.get(`${API}/services/${encodeURIComponent(URN)}/schema-portfolio`, () =>
      HttpResponse.json({ detail: 'Unknown service URN' }, { status: 404 }),
    ),
  );
  renderPortfolio();
  await waitFor(() => screen.getByText('Could not load portfolio'));
  expect(screen.getByText('Could not load portfolio')).toBeInTheDocument();
});

it('shows application name in portfolio header', async () => {
  renderPortfolio();
  await waitFor(() => screen.getByRole('heading', { level: 1 }));
  expect(screen.getByText('DAC Product Data Lake')).toBeInTheDocument();
});

it('shows application id next to app name', async () => {
  renderPortfolio();
  await waitFor(() => screen.getByRole('heading', { level: 1 }));
  expect(screen.getByText('(SVC01909)')).toBeInTheDocument();
});

describe('a service that is both upstream and downstream', () => {
  /**
   * An ordinary bidirectional relationship: it consumes a schema this service
   * produces AND produces one this service consumes, so it appears in both
   * dependency lists.
   *
   * Nothing in the BFF prevents that shape, and nothing in the current CMDB
   * data produces it — I checked all 14 live portfolios, and none has an
   * overlap. So this is the invariant made explicit rather than a live bug:
   * the count summed the two lists, and the graph emitted a node per entry
   * with `id: dep.ci_id`, which React Flow silently collapses when two nodes
   * share an id.
   */
  const BOTH = {
    ci_id: 'CI0093431',
    urn: 'urn:pearson:service:price:pricing-svc',
    name: 'pricing-svc',
    domain: 'price',
    shared_schema_count: 2,
  };

  beforeEach(() => {
    server.use(
      http.get(`${API}/services/${encodeURIComponent(URN)}/schema-portfolio`, () =>
        HttpResponse.json({
          ...PORTFOLIO,
          dependencies: {
            upstream: [...PORTFOLIO.dependencies.upstream, BOTH],
            downstream: [...PORTFOLIO.dependencies.downstream, BOTH],
          },
        }),
      ),
    );
  });

  it('counts it once as a dependent, not once per direction', async () => {
    renderPortfolio();
    // enrollment-svc, content-svc, pricing-svc — three services, four entries.
    const tab = await screen.findByRole('button', { name: /dependents/i });
    expect(tab).toHaveTextContent('3');
    expect(tab).not.toHaveTextContent('4');
  });

  it('lists it in both directions, because it really is in both', async () => {
    // Deduplicating the COUNT must not hide the relationship: the panel still
    // has to show that the traffic goes both ways.
    renderPortfolio();
    await waitFor(() => screen.getByRole('button', { name: /^Dependents/ }));
    fireEvent.click(screen.getByRole('button', { name: /^Dependents/ }));
    await waitFor(() => screen.getByText('enrollment-svc'));
    // Once in each section — deduplicating the COUNT must not hide the
    // relationship, which really does run both ways.
    expect(screen.getAllByText('pricing-svc')).toHaveLength(2);
  });
});
