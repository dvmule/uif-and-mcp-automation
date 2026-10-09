import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { ServiceList } from './ServiceList';

const API = 'http://localhost:8000/api/v1';

const SERVICES = [
  { ci_id: 'CI0093421', urn: 'urn:pearson:service:product:catalog-svc', name: 'catalog-svc', domain: 'product', produces_count: 7, consumes_count: 3, app_id: 'SVC01909', app_name: 'DAC Product Data Lake' },
  { ci_id: 'CI0093422', urn: 'urn:pearson:service:learning:enrollment-svc', name: 'enrollment-svc', domain: 'learning', produces_count: 2, consumes_count: 2, app_id: 'SVC00595', app_name: 'PLA Learning Analytics- Performance XL' },
  { ci_id: 'CI0093423', urn: 'urn:pearson:service:commerce:checkout-svc', name: 'checkout-svc', domain: 'commerce', produces_count: 1, consumes_count: 2, app_id: null, app_name: null },
];

const GROUPING_SERVICES = [
  ...SERVICES,
  { ci_id: 'CI0093424', urn: 'urn:pearson:service:finance:pricing-svc', name: 'pricing-svc', domain: 'finance', produces_count: 3, consumes_count: 1, app_id: 'SVC01909', app_name: 'DAC Product Data Lake' },
];

const server = setupServer(
  http.get(`${API}/services`, () => HttpResponse.json(SERVICES)),
);

beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function renderList(initialUrl = '/services') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[initialUrl]}>
        <ServiceList />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

it('renders all services after loading', async () => {
  renderList();
  await waitFor(() => screen.getByText('catalog-svc'));
  expect(screen.getByText('catalog-svc')).toBeInTheDocument();
  expect(screen.getByText('enrollment-svc')).toBeInTheDocument();
  expect(screen.getByText('checkout-svc')).toBeInTheDocument();
});

it('renders domain badges', async () => {
  renderList();
  await waitFor(() => screen.getByText('product'));
  expect(screen.getByText('product')).toBeInTheDocument();
  expect(screen.getByText('learning')).toBeInTheDocument();
});

it('renders produces and consumes counts', async () => {
  renderList();
  await waitFor(() => screen.getByText('catalog-svc'));
  expect(screen.getByText('7')).toBeInTheDocument();
  expect(screen.getByText('3')).toBeInTheDocument();
});

it('service name links to portfolio page', async () => {
  renderList();
  await waitFor(() => screen.getByText('catalog-svc'));
  const link = screen.getByText('catalog-svc').closest('a');
  expect(link).toHaveAttribute('href', expect.stringContaining('catalog-svc'));
});

it('filters list by search query', async () => {
  renderList();
  await waitFor(() => screen.getByText('catalog-svc'));
  const input = screen.getByPlaceholderText('Search by name or domain…');
  fireEvent.change(input, { target: { value: 'enrollment' } });
  await waitFor(() => expect(screen.queryByText('catalog-svc')).not.toBeInTheDocument());
  expect(screen.getByText('enrollment-svc')).toBeInTheDocument();
});

it('shows empty state when search has no matches', async () => {
  renderList();
  await waitFor(() => screen.getByText('catalog-svc'));
  const input = screen.getByPlaceholderText('Search by name or domain…');
  fireEvent.change(input, { target: { value: 'zzznomatch' } });
  await waitFor(() => screen.getByText(/No services match/));
  expect(screen.getByText(/No services match/)).toBeInTheDocument();
});

it('shows error state when API fails', async () => {
  server.use(
    http.get(`${API}/services`, () => HttpResponse.json({ detail: 'error' }, { status: 500 })),
  );
  renderList();
  await waitFor(() => screen.getByText('Could not load services'));
  expect(screen.getByText('Could not load services')).toBeInTheDocument();
});

it('renders application name column header', async () => {
  renderList();
  await waitFor(() => screen.getByText('catalog-svc'));
  expect(screen.getByRole('columnheader', { name: 'Application' })).toBeInTheDocument();
});

it('shows app name for services with an application', async () => {
  renderList();
  await waitFor(() => screen.getByText('catalog-svc'));
  expect(screen.getByText('DAC Product Data Lake')).toBeInTheDocument();
  expect(screen.getByText('PLA Learning Analytics- Performance XL')).toBeInTheDocument();
});

it('shows dash for services without an application', async () => {
  renderList();
  await waitFor(() => screen.getByText('checkout-svc'));
  const dashes = screen.getAllByText('—');
  expect(dashes.length).toBeGreaterThan(0);
});

it('renders Application group button', async () => {
  renderList();
  await waitFor(() => screen.getByText('catalog-svc'));
  expect(screen.getByRole('button', { name: 'Application' })).toBeInTheDocument();
});

it('groups services by application name when ?groupBy=application', async () => {
  server.use(http.get(`${API}/services`, () => HttpResponse.json(GROUPING_SERVICES)));
  renderList('/services?groupBy=application');
  await waitFor(() => screen.getByText('catalog-svc'));
  expect(screen.getByRole('button', { name: /DAC Product Data Lake/ })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: /PLA Learning Analytics/ })).toBeInTheDocument();
  expect(screen.getByText('catalog-svc')).toBeInTheDocument();
  expect(screen.getByText('pricing-svc')).toBeInTheDocument();
});

it('places services with no app_name under Unassigned when grouped by application', async () => {
  server.use(http.get(`${API}/services`, () => HttpResponse.json(GROUPING_SERVICES)));
  renderList('/services?groupBy=application');
  await waitFor(() => screen.getByText('checkout-svc'));
  expect(screen.getByRole('button', { name: /Unassigned/ })).toBeInTheDocument();
  expect(screen.getByText('checkout-svc')).toBeInTheDocument();
});

it('switching to Application grouping deactivates Domain grouping', async () => {
  server.use(http.get(`${API}/services`, () => HttpResponse.json(GROUPING_SERVICES)));
  renderList('/services?groupBy=domain');
  await waitFor(() => screen.getByText('catalog-svc'));
  fireEvent.click(screen.getByRole('button', { name: 'Application' }));
  await waitFor(() => screen.getByRole('button', { name: /DAC Product Data Lake/ }));
  expect(screen.queryByRole('button', { name: /^product$/ })).not.toBeInTheDocument();
});

// ---------------------------------------------------------------------------
// 6a — the read-only list, and the CMDB's duplicate entries.
// ---------------------------------------------------------------------------

/** Two extra entries under URNs already in the list: what the real CMDB does. */
const DUPLICATED_SERVICES = [
  ...SERVICES,
  { ci_id: 'CI0093421', urn: 'urn:pearson:service:product:catalog-svc', name: 'catalog-svc', domain: 'product', produces_count: 7, consumes_count: 3, app_id: 'SVC01909', app_name: 'DAC Product Data Lake' },
  { ci_id: 'CI0093423', urn: 'urn:pearson:service:commerce:checkout-svc', name: 'checkout-svc', domain: 'commerce', produces_count: 1, consumes_count: 2, app_id: null, app_name: null },
];

describe('the CMDB returns more entries than services', () => {
  it('marks the repeats rather than hiding them, and says why', async () => {
    server.use(http.get(`${API}/services`, () => HttpResponse.json(DUPLICATED_SERVICES)));
    renderList();
    // Hiding a row would misreport what the CMDB holds. A picker de-duplicates
    // because a URN is an identity; a list must not.
    await waitFor(() => expect(screen.getAllByText('repeat')).toHaveLength(2));
    // Three occurrences of the name: two rows, plus the notice naming it.
    expect(screen.getAllByText('catalog-svc')).toHaveLength(3);
    expect(screen.getByText(/The CMDB returns/)).toHaveTextContent(
      'The CMDB returns 5 entries for 3 services',
    );
    expect(screen.getByText(/duplication is upstream/)).toBeInTheDocument();
  });

  it('names the services that arrive twice, derived from the data', async () => {
    server.use(http.get(`${API}/services`, () => HttpResponse.json(DUPLICATED_SERVICES)));
    renderList();
    await waitFor(() => expect(screen.getByText(/The CMDB returns/)).toBeInTheDocument());
    const notice = screen.getByText(/The CMDB returns/);
    expect(notice).toHaveTextContent('catalog-svc');
    expect(notice).toHaveTextContent('checkout-svc');
    // enrollment-svc arrives once, so it is not named.
    expect(notice).not.toHaveTextContent('enrollment-svc');
  });

  it('shows no notice at all when every URN is distinct', async () => {
    renderList();
    await waitFor(() => screen.getByRole('columnheader', { name: 'Service' }));
    expect(screen.queryByText(/The CMDB returns/)).not.toBeInTheDocument();
    expect(screen.queryByText('repeat')).not.toBeInTheDocument();
  });

  it('counts distinct services against total entries', async () => {
    server.use(http.get(`${API}/services`, () => HttpResponse.json(DUPLICATED_SERVICES)));
    renderList();
    await waitFor(() =>
      expect(
        screen.getByRole('status', { name: '3 services across 5 entries' }),
      ).toBeInTheDocument(),
    );
  });
});

describe('read-only by design', () => {
  it('offers no checkboxes, no row actions and no CTA', async () => {
    renderList();
    await waitFor(() => screen.getByRole('columnheader', { name: 'Service' }));
    // The registry does not create or edit services. An affordance that cannot
    // work is worse than its absence, so there are none — not disabled ones.
    expect(screen.queryAllByRole('checkbox')).toHaveLength(0);
    expect(
      screen.queryByRole('button', { name: /new|add|create|edit|delete/i }),
    ).not.toBeInTheDocument();
  });

  it('says where the records come from', async () => {
    renderList();
    expect(
      screen.getByText(/The registry does not create or edit services/),
    ).toBeInTheDocument();
  });
});

describe('the search matches name and domain only', () => {
  it('finds a service by its domain', async () => {
    renderList();
    await waitFor(() => screen.getByText('catalog-svc'));
    fireEvent.change(screen.getByPlaceholderText('Search by name or domain…'), {
      target: { value: 'learning' },
    });
    await waitFor(() => expect(screen.queryByText('catalog-svc')).not.toBeInTheDocument());
    expect(screen.getByText('enrollment-svc')).toBeInTheDocument();
  });

  // The placeholder promises name or domain. Matching the application column
  // too would return rows whose visible name has nothing to do with the query.
  it('does not match the application column', async () => {
    renderList();
    await waitFor(() => screen.getByText('catalog-svc'));
    fireEvent.change(screen.getByPlaceholderText('Search by name or domain…'), {
      target: { value: 'DAC Product Data Lake' },
    });
    await waitFor(() => expect(screen.getByText(/No services match/)).toBeInTheDocument());
    expect(screen.queryByText('catalog-svc')).not.toBeInTheDocument();
  });
});

describe('counts', () => {
  it('reads muted at zero and accented when there is something there', async () => {
    server.use(
      http.get(`${API}/services`, () =>
        HttpResponse.json([
          { ci_id: 'CI1', urn: 'urn:pearson:service:analytics:analytics-svc', name: 'analytics-svc', domain: 'analytics', produces_count: 0, consumes_count: 4, app_id: null, app_name: null },
        ]),
      ),
    );
    renderList();
    await waitFor(() => screen.getByText('analytics-svc'));
    expect(screen.getByText('0').className).toContain('text-ink-mute');
    expect(screen.getByText('4').className).toContain('text-accent');
  });
});
