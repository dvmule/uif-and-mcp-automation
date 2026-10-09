import { useEffect } from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { PublishPage } from './PublishPage';

const API = 'http://localhost:8000/api/v1';

const SERVICES = [
  { ci_id: 'CI0093423', urn: 'urn:pearson:service:commerce:checkout-svc', name: 'checkout-svc', domain: 'commerce', produces_count: 1, consumes_count: 2, app_id: null, app_name: null },
];

const server = setupServer(http.get(`${API}/services`, () => HttpResponse.json(SERVICES)));
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

// Written from an effect, not during render: assigning a module-scope
// variable while rendering is a side effect in render, and effects flush
// inside `act` so the reads below still see it.
let routed = { pathname: '', search: '' };

function LocationProbe() {
  const current = useLocation();
  useEffect(() => {
    routed = { pathname: current.pathname, search: current.search };
  }, [current]);
  return null;
}

function renderPage() {
  routed = { pathname: '', search: '' };
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/schemas/publish']}>
        <PublishPage />
        <LocationProbe />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

// Fills every field the form requires to submit, using a group that is NOT
// the default -- this is the exact case the hardcoded-'default' bug that
// `submittedGroup` replaced would have gotten wrong.
async function fillAndSubmit(groupId: string) {
  await waitFor(() => expect(screen.getByLabelText(/owning service/i)).toBeInTheDocument());
  fireEvent.change(screen.getByLabelText(/subject/i), { target: { value: 'commerce.order.placed-value' } });
  fireEvent.change(screen.getByLabelText(/group/i), { target: { value: groupId } });
  fireEvent.change(screen.getByLabelText(/schema body/i), { target: { value: '{"type":"record","name":"X","fields":[]}' } });
  fireEvent.change(screen.getByLabelText(/owning service/i), { target: { value: 'urn:pearson:service:commerce:checkout-svc' } });
  fireEvent.click(screen.getByLabelText(/confidential/i));
  fireEvent.click(screen.getByRole('button', { name: /publish/i }));
}

// The success panel is gone. Registration navigates to the new subject's
// schema page, which is where its compliance is shown — the thing publishing
// through the registry exists to produce. The group still has to come from
// what was submitted, not a hardcoded 'default', or the URL names a subject
// that does not exist in that group.
it('navigates to the new subject under the group that was submitted', async () => {
  server.use(
    http.post(`${API}/schemas/commerce/commerce.order.placed-value`, () =>
      HttpResponse.json({ topic: 'commerce.order.placed-value', registered: true, schema_type: 'AVRO', error: null }),
    ),
  );
  renderPage();
  await fillAndSubmit('commerce');
  await waitFor(() =>
    expect(routed.pathname).toBe('/schemas/commerce/commerce.order.placed-value'),
  );
});

// HTTP 200 is not success. The endpoint answers 200 with registered:false.
it('stays on the form and leads with the registry error on a soft failure', async () => {
  server.use(
    http.post(`${API}/schemas/commerce/commerce.order.placed-value`, () =>
      HttpResponse.json({
        topic: 'commerce.order.placed-value',
        registered: false,
        schema_type: null,
        error: 'Unknown service URN: urn:pearson:service:commerce:checkout-svc',
      }),
    ),
  );
  renderPage();
  await fillAndSubmit('commerce');
  await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument());
  expect(screen.getByRole('alert')).toHaveTextContent(/was not registered/i);
  expect(screen.getByText(/Unknown service URN/)).toBeInTheDocument();
  // And it must NOT have navigated away from a failure.
  expect(routed.pathname).toBe('/schemas/publish');
});

it('announces a transport-level failure as an alert', async () => {
  server.use(
    http.post(`${API}/schemas/commerce/commerce.order.placed-value`, () => HttpResponse.error()),
  );
  renderPage();
  await fillAndSubmit('commerce');
  await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument());
  expect(screen.getByRole('alert')).toHaveTextContent(/the request itself failed/i);
  expect(routed.pathname).toBe('/schemas/publish');
});

it('says what registering here is for', async () => {
  renderPage();
  expect(
    screen.getByText(/Registering here is what makes a schema compliant/i),
  ).toBeInTheDocument();
  expect(
    screen.getByText(/pushed straight to Apicurio skips both and lands non-compliant/i),
  ).toBeInTheDocument();
});
