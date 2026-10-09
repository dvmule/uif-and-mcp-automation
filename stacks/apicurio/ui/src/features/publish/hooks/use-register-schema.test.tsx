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

/**
 * Registration re-runs the BFF's compliance scan, so every test here needs the
 * refresh endpoint answered. It is a default handler rather than a per-test
 * `server.use`: the hook calls it on every successful registration, and an
 * unhandled request would be swallowed by the hook's own catch and prove
 * nothing either way.
 */
let revalidations = 0;

const server = setupServer(
  http.post(`${API}/schemas/non-compliant/refresh`, () => {
    revalidations += 1;
    return HttpResponse.json({ items: [], last_validated: new Date().toISOString(), total_checked: 13 });
  }),
);
beforeAll(() => server.listen());
beforeEach(() => {
  revalidations = 0;
});
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

function renderWithClient(qc: QueryClient) {
  return renderHook(() => useRegisterSchema(), {
    wrapper: ({ children }: { children: React.ReactNode }) => (
      <QueryClientProvider client={qc}>{children}</QueryClientProvider>
    ),
  });
}

const DETAIL_KEY = ['schema-detail', 'default', 'commerce.order.placed-value', undefined];

it('invalidates the list and the detail cache when something actually registered', async () => {
  server.use(
    http.post(`${API}/schemas/default/commerce.order.placed-value`, () =>
      HttpResponse.json({ topic: 'commerce.order.placed-value', registered: true, schema_type: 'AVRO', error: null }),
    ),
  );
  const qc = new QueryClient({ defaultOptions: { mutations: { retry: false }, queries: { retry: false } } });
  qc.setQueryData(['schemas'], { items: [] });
  qc.setQueryData(DETAIL_KEY, { content: 'stale' });

  const { result } = renderWithClient(qc);
  result.current.mutate({ groupId: 'default', artifactId: 'commerce.order.placed-value', body: BODY });
  await waitFor(() => expect(result.current.isSuccess).toBe(true));

  expect(qc.getQueryState(['schemas'])?.isInvalidated).toBe(true);
  // A new version changes the detail of the subject just published, and
  // `useSchemaDetail` — the timeline's diff included — sits under its own key.
  expect(qc.getQueryState(DETAIL_KEY)?.isInvalidated).toBe(true);
});

it('invalidates nothing when the 200 carries registered:false', async () => {
  server.use(
    http.post(`${API}/schemas/default/commerce.order.placed-value`, () =>
      HttpResponse.json({ topic: 'commerce.order.placed-value', registered: false, schema_type: null, error: 'Unknown service URN: urn:bogus' }),
    ),
  );
  const qc = new QueryClient({ defaultOptions: { mutations: { retry: false }, queries: { retry: false } } });
  qc.setQueryData(['schemas'], { items: [] });
  qc.setQueryData(DETAIL_KEY, { content: 'stale' });

  const { result } = renderWithClient(qc);
  result.current.mutate({ groupId: 'default', artifactId: 'commerce.order.placed-value', body: BODY });
  await waitFor(() => expect(result.current.isSuccess).toBe(true));

  expect(qc.getQueryState(['schemas'])?.isInvalidated).toBe(false);
  expect(qc.getQueryState(DETAIL_KEY)?.isInvalidated).toBe(false);
});

/**
 * The compliance verdict lives in a cache on the BFF, and
 * `GET /schemas/non-compliant` serves it. Re-fetching the query would return
 * the verdict from before the registration -- so the schema page a publish
 * navigates to would report the compliance of the version that was just
 * replaced. Clearing that cache is a POST, and this hook is what makes it.
 */
it('re-runs the compliance scan on the BFF before invalidating it', async () => {
  server.use(
    http.post(`${API}/schemas/default/commerce.order.placed-value`, () =>
      HttpResponse.json({ topic: 'commerce.order.placed-value', registered: true, schema_type: 'AVRO', error: null }),
    ),
  );
  const qc = new QueryClient({ defaultOptions: { mutations: { retry: false }, queries: { retry: false } } });
  qc.setQueryData(['schemas', 'non-compliant'], { items: [], last_validated: null, total_checked: 0 });

  const { result } = renderWithClient(qc);
  result.current.mutate({ groupId: 'default', artifactId: 'commerce.order.placed-value', body: BODY });
  await waitFor(() => expect(result.current.isSuccess).toBe(true));

  expect(revalidations).toBe(1);
  expect(qc.getQueryState(['schemas', 'non-compliant'])?.isInvalidated).toBe(true);
});

// Nothing registered, so nothing about compliance changed. A full re-scan of
// every subject is not free, and running one here would also flag the
// registration as having had an effect.
it('does not re-scan when the 200 carries registered:false', async () => {
  server.use(
    http.post(`${API}/schemas/default/commerce.order.placed-value`, () =>
      HttpResponse.json({ topic: 'commerce.order.placed-value', registered: false, schema_type: null, error: 'Unknown service URN: urn:bogus' }),
    ),
  );
  const { result } = renderHook(() => useRegisterSchema(), { wrapper });
  result.current.mutate({ groupId: 'default', artifactId: 'commerce.order.placed-value', body: BODY });
  await waitFor(() => expect(result.current.isSuccess).toBe(true));

  expect(revalidations).toBe(0);
});

/**
 * A failed re-scan leaves the verdict as stale as it already was. It must not
 * turn a registration that succeeded into a failure -- the version is on the
 * registry either way, and the list toolbar's refresh is still the way out.
 */
it('still reports success, and still invalidates, when the re-scan fails', async () => {
  server.use(
    http.post(`${API}/schemas/default/commerce.order.placed-value`, () =>
      HttpResponse.json({ topic: 'commerce.order.placed-value', registered: true, schema_type: 'AVRO', error: null }),
    ),
    http.post(`${API}/schemas/non-compliant/refresh`, () =>
      HttpResponse.json({ detail: 'Apicurio unreachable' }, { status: 503 }),
    ),
  );
  const qc = new QueryClient({ defaultOptions: { mutations: { retry: false }, queries: { retry: false } } });
  qc.setQueryData(['schemas'], { items: [] });

  const { result } = renderWithClient(qc);
  result.current.mutate({ groupId: 'default', artifactId: 'commerce.order.placed-value', body: BODY });
  await waitFor(() => expect(result.current.isSuccess).toBe(true));

  expect(result.current.isError).toBe(false);
  expect(result.current.data?.registered).toBe(true);
  expect(qc.getQueryState(['schemas'])?.isInvalidated).toBe(true);
});
