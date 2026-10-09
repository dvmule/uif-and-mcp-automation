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

function renderOverview(props: { complianceError?: boolean } = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <RegistryOverview onNavigate={() => {}} {...props} />
    </QueryClientProvider>,
  );
}

it('shows the real total from the API', async () => {
  renderOverview();
  await waitFor(() => expect(screen.getByText('13')).toBeInTheDocument());
  expect(screen.getByText('subjects registered')).toBeInTheDocument();
});

it('renders no fabricated request-rate metrics', async () => {
  renderOverview();
  await waitFor(() => expect(screen.getByText('13')).toBeInTheDocument());
  expect(screen.queryByText(/read requests/i)).not.toBeInTheDocument();
  expect(screen.queryByText(/create requests/i)).not.toBeInTheDocument();
  expect(screen.queryByText(/delete requests/i)).not.toBeInTheDocument();
  expect(screen.queryByText('525')).not.toBeInTheDocument();
});

it('states compatibility-rule coverage as a bare ratio', async () => {
  // The number the page exists to show. It is 0, and it is not softened: no
  // reassuring caption, no percentage, no rounding away from zero.
  renderOverview();
  await waitFor(() => expect(screen.getByText('0 of 13')).toBeInTheDocument());
  expect(screen.getByText('have a compatibility rule set')).toBeInTheDocument();
});

it('does not make the compatibility card a drill-down to nowhere', async () => {
  // There is no "has a compatibility rule" filter to navigate to. The card
  // used to route to the unfiltered list, dressing an arbitrary navigation as
  // a drill-down; only the two cards with a real destination are buttons.
  renderOverview();
  await waitFor(() => expect(screen.getByText('0 of 13')).toBeInTheDocument());
  expect(screen.getAllByRole('button')).toHaveLength(2);
});

it("says it couldn't load rather than captioning a failed fetch as a fact", async () => {
  server.use(
    http.get(`${API}/schemas/overview`, () => HttpResponse.json({ detail: 'boom' }, { status: 500 })),
  );
  renderOverview();
  await waitFor(() => expect(screen.getAllByText(/couldn’t load/i).length).toBe(3));
  expect(screen.queryByText('13')).not.toBeInTheDocument();
  expect(screen.queryByText('0 of 13')).not.toBeInTheDocument();
});

it('will not report a non-compliant count while compliance is unknown', async () => {
  // The stats endpoint answered, so `non_compliant_schemas` is a real number —
  // but the compliance query failed, so the table beside this card renders
  // every row as `unknown`. "2 failing a metadata rule" above that table
  // asserts a check that did not complete.
  renderOverview({ complianceError: true });
  await waitFor(() => expect(screen.getByText('13')).toBeInTheDocument());
  expect(screen.queryByText('2')).not.toBeInTheDocument();
  expect(screen.getByText(/couldn’t load/i)).toBeInTheDocument();
  // The other two figures came from a query that did succeed, and still show.
  expect(screen.getByText('0 of 13')).toBeInTheDocument();
});
