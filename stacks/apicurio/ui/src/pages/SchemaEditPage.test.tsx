import { useEffect } from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { http, HttpResponse, delay } from 'msw';
import { setupServer } from 'msw/node';
import { SchemaEditPage } from './SchemaEditPage';

const API = 'http://localhost:8000/api/v1';
const SUBJECT = 'commerce.order.placed-value';

const SERVICES = [
  { ci_id: 'CI0093423', urn: 'urn:pearson:service:commerce:checkout-svc', name: 'checkout-svc', domain: 'commerce', produces_count: 1, consumes_count: 2, app_id: null, app_name: null },
  { ci_id: 'CI0093999', urn: 'urn:pearson:service:finance:ledger-svc', name: 'ledger-svc', domain: 'finance', produces_count: 0, consumes_count: 1, app_id: null, app_name: null },
];

const V2_BODY = JSON.stringify(
  {
    type: 'record',
    name: 'OrderPlaced',
    fields: [{ name: 'order_id', type: 'string' }],
    metadata: {
      owner: { service: 'CI0093423', team: 'TM-COMMERCE', email: 'commerce@pearson.com' },
      domain: 'commerce',
      dataClassification: 'confidential',
      piiFields: ['email'],
      lifecycle: 'active',
    },
  },
  null,
  2,
);

function detail(overrides: Record<string, unknown> = {}) {
  return {
    artifact_id: SUBJECT,
    group_id: 'commerce',
    name: 'OrderPlaced',
    type: 'AVRO',
    state: 'ENABLED',
    created_on: '2026-09-01T00:00:00Z',
    modified_on: '2026-09-10T00:00:00Z',
    latest_version: '2',
    owner: { service: 'CI0093423', team: 'TM-COMMERCE', email: 'commerce@pearson.com' },
    domain: 'commerce',
    classification: 'confidential',
    lifecycle: 'active',
    pii_fields: ['email'],
    content: V2_BODY,
    content_version: '2',
    versions: [
      { version: '1', state: 'ENABLED', created_on: '2026-09-01T00:00:00Z', created_by: 'a' },
      { version: '2', state: 'ENABLED', created_on: '2026-09-10T00:00:00Z', created_by: 'a' },
    ],
    compatibility: null,
    ...overrides,
  };
}

const server = setupServer(
  http.get(`${API}/services`, () => HttpResponse.json(SERVICES)),
  http.get(`${API}/schemas/commerce/${SUBJECT}`, () => HttpResponse.json(detail())),
  http.get(`${API}/schemas/commerce/${SUBJECT}/services`, () =>
    HttpResponse.json({
      artifact_id: SUBJECT,
      group_id: 'commerce',
      producers: [{ ci_id: 'CI0093423', urn: 'urn:pearson:service:commerce:checkout-svc', name: 'checkout-svc', domain: 'commerce' }],
      consumers: [],
    }),
  ),
);
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

let routed = { pathname: '', search: '' };

function LocationProbe() {
  const current = useLocation();
  useEffect(() => {
    routed = { pathname: current.pathname, search: current.search };
  }, [current]);
  return null;
}

function renderPage(url = `/schemas/commerce/${SUBJECT}/edit`, seed?: (qc: QueryClient) => void) {
  routed = { pathname: '', search: '' };
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  seed?.(qc);
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[url]}>
        <Routes>
          <Route path="/schemas/:groupId/:id/edit" element={<SchemaEditPage />} />
        </Routes>
        <LocationProbe />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/** The body arrives only after the schema query resolves. */
const bodyField = async () => {
  await waitFor(() => expect(screen.getByLabelText(/schema body/i)).toBeInTheDocument());
  return screen.getByLabelText(/schema body/i);
};

it('opens on the registry\'s current body, not an empty form', async () => {
  renderPage();
  expect(await bodyField()).toHaveValue(V2_BODY);
  expect(screen.getByText('based on v2')).toBeInTheDocument();
});

/**
 * The metadata block is rebuilt from the form on every publish rather than
 * merged, so a governance value the form did not carry over would be silently
 * erased from the schema by an unrelated edit.
 */
it('carries the existing governance values over so an edit cannot erase them', async () => {
  renderPage();
  await bodyField();
  await waitFor(() =>
    expect(screen.getByRole('option', { name: /checkout-svc/ })).toBeInTheDocument(),
  );
  expect(screen.getByLabelText(/owning service/i)).toHaveValue('urn:pearson:service:commerce:checkout-svc');
  expect(screen.getByLabelText(/confidential/i)).toBeChecked();
  expect(screen.getByLabelText(/lifecycle/i)).toHaveValue('active');
  expect(screen.getByText('email')).toBeInTheDocument();
});

it('posts the edit to the subject it is editing and returns to the schema', async () => {
  const posted: unknown[] = [];
  server.use(
    http.post(`${API}/schemas/commerce/${SUBJECT}`, async ({ request }) => {
      posted.push(await request.json());
      return HttpResponse.json({ topic: SUBJECT, registered: true, schema_type: 'AVRO', error: null });
    }),
  );
  renderPage();
  const body = await bodyField();
  fireEvent.change(body, { target: { value: '{"type":"record","name":"OrderPlaced","fields":[]}' } });
  fireEvent.click(screen.getByRole('button', { name: /publish new version/i }));

  await waitFor(() => expect(routed.pathname).toBe(`/schemas/commerce/${SUBJECT}`));
  expect(posted).toHaveLength(1);
  expect(posted[0]).toMatchObject({
    service_urn: 'urn:pearson:service:commerce:checkout-svc',
    data_classification: 'confidential',
    pii_fields: ['email'],
    lifecycle: 'active',
  });
});

// HTTP 200 is not success here either -- and on this page a soft failure means
// the schema still holds the version it had, which is worth not navigating away
// from.
it('stays on the form and leads with the registry error on a soft failure', async () => {
  server.use(
    http.post(`${API}/schemas/commerce/${SUBJECT}`, () =>
      HttpResponse.json({
        topic: SUBJECT,
        registered: false,
        schema_type: 'AVRO',
        error: 'INVALID_COMPATIBILITY: removing field order_id breaks BACKWARD',
      }),
    ),
  );
  renderPage();
  const body = await bodyField();
  fireEvent.change(body, { target: { value: '{"type":"record","name":"OrderPlaced","fields":[]}' } });
  fireEvent.click(screen.getByRole('button', { name: /publish new version/i }));

  await waitFor(() => expect(screen.getByText(/INVALID_COMPATIBILITY/)).toBeInTheDocument());
  expect(routed.pathname).toBe(`/schemas/commerce/${SUBJECT}/edit`);
});

/**
 * `?version=` pins which version the edit starts from, so Update on a page
 * showing v1 edits v1 rather than silently jumping to the head.
 */
it('starts from the pinned version when the URL names one', async () => {
  const V1_BODY = '{\n  "type": "record",\n  "name": "OrderPlaced",\n  "fields": []\n}';
  server.use(
    http.get(`${API}/schemas/commerce/${SUBJECT}`, ({ request }) => {
      const version = new URL(request.url).searchParams.get('version');
      return HttpResponse.json(
        version === '1' ? detail({ content: V1_BODY, content_version: '1' }) : detail(),
      );
    }),
  );
  renderPage(`/schemas/commerce/${SUBJECT}/edit?version=1`);
  expect(await bodyField()).toHaveValue(V1_BODY);
  expect(screen.getByText('based on v1')).toBeInTheDocument();
});

/**
 * A URN is the only thing the register endpoint accepts, and it fails outright
 * on one the CMDB cannot resolve. When neither the produces edge nor the
 * service list can turn this schema's CI id into a URN, the picker stays unset
 * and the checklist blocks -- which is the point: guessing would submit a URN
 * that is going to be rejected.
 */
it('leaves the service unset, and blocks, when no URN can be resolved for it', async () => {
  server.use(
    http.get(`${API}/schemas/commerce/${SUBJECT}/services`, () =>
      HttpResponse.json({ artifact_id: SUBJECT, group_id: 'commerce', producers: [], consumers: [] }),
    ),
    http.get(`${API}/schemas/commerce/${SUBJECT}`, () =>
      HttpResponse.json(detail({ owner: { service: 'CI-GONE', team: null, email: null } })),
    ),
  );
  renderPage();
  const body = await bodyField();
  await waitFor(() =>
    expect(screen.getByRole('option', { name: /checkout-svc/ })).toBeInTheDocument(),
  );
  expect(screen.getByLabelText(/owning service/i)).toHaveValue('');

  fireEvent.change(body, { target: { value: '{"type":"record","name":"OrderPlaced","fields":[]}' } });
  expect(screen.getByRole('button', { name: /publish new version/i })).toBeDisabled();
  expect(screen.getByText(/an owning service/i, { selector: 'p' })).toBeInTheDocument();
});

it('reports a schema it could not load rather than offering an empty form', async () => {
  server.use(
    http.get(`${API}/schemas/commerce/${SUBJECT}`, () =>
      HttpResponse.json({ detail: 'Artifact not found' }, { status: 404 }),
    ),
  );
  renderPage();
  await waitFor(() => expect(screen.getByText(/could not load schema/i)).toBeInTheDocument());
  expect(screen.queryByLabelText(/schema body/i)).not.toBeInTheDocument();
});

/**
 * Arriving from the schema page, not by a cold load.
 *
 * The schema is already in the query cache then, while the produces edge that
 * names the owning service is not — and a query that is enabled but has not
 * yet begun fetching is pending AND idle, so `isLoading` reads false for it.
 * Gated on `isLoading`, the form mounted into that gap, seeded itself with an
 * owning service of `''`, and was then unmounted and rebuilt when the edge
 * landed. Anything typed in between was gone.
 *
 * So: no form until the prefill is final, and the URN is right the first time
 * the form is seen.
 */
it('does not mount the form until every source of the prefill has settled', async () => {
  server.use(
    http.get(`${API}/schemas/commerce/${SUBJECT}/services`, async () => {
      await delay(40);
      return HttpResponse.json({
        artifact_id: SUBJECT,
        group_id: 'commerce',
        producers: [{ ci_id: 'CI0093423', urn: 'urn:pearson:service:commerce:checkout-svc', name: 'checkout-svc', domain: 'commerce' }],
        consumers: [],
      });
    }),
  );

  renderPage(`/schemas/commerce/${SUBJECT}/edit`, (qc) => {
    // What the schema page leaves behind: its detail query, under the key the
    // edit page reads too.
    qc.setQueryData(['schema-detail', 'commerce', SUBJECT, undefined], detail());
  });

  // The body is cached and could be rendered right now. It must not be.
  expect(screen.queryByLabelText(/schema body/i)).not.toBeInTheDocument();
  expect(screen.getByText(/loading schema/i)).toBeInTheDocument();

  await waitFor(() => expect(screen.getByLabelText(/schema body/i)).toBeInTheDocument());
  await waitFor(() =>
    expect(screen.getByRole('option', { name: /checkout-svc/ })).toBeInTheDocument(),
  );
  // Right the first time the form is seen, rather than after a remount.
  expect(screen.getByLabelText(/owning service/i)).toHaveValue('urn:pearson:service:commerce:checkout-svc');
});

// The other half of the same defect: once the form is up, a query settling
// later must not take it away and rebuild it.
it('keeps what has been typed when a later fetch settles', async () => {
  renderPage();
  const body = await bodyField();
  const edited = '{"type":"record","name":"OrderPlaced","fields":[{"name":"id","type":"string"}]}';
  fireEvent.change(body, { target: { value: edited } });

  await waitFor(() =>
    expect(screen.getByRole('option', { name: /checkout-svc/ })).toBeInTheDocument(),
  );
  // Same element, same value -- not a fresh form seeded back to the registry's
  // body.
  expect(screen.getByLabelText(/schema body/i)).toHaveValue(edited);
});

/**
 * The compatibility warning, which belongs to this page and not to the publish
 * page: a first registration has no earlier version to be incompatible with,
 * a new version does. Apicurio refuses an incompatible version only when the
 * subject carries a rule, and no subject in this registry carries one — so the
 * check that would catch a removed field is not running, and the page has to
 * say so.
 */
it('warns that nothing will check this version when the subject has no rule', async () => {
  renderPage();
  await bodyField();
  expect(screen.getByText(/carries no compatibility rule/i)).toBeInTheDocument();
  // Names the version it is not being checked against, not just "the previous
  // one".
  expect(screen.getByText(/against v2/)).toBeInTheDocument();
});

it('does not warn when the subject does carry a rule', async () => {
  server.use(
    http.get(`${API}/schemas/commerce/${SUBJECT}`, () =>
      HttpResponse.json(detail({ compatibility: 'BACKWARD' })),
    ),
  );
  renderPage();
  await bodyField();
  expect(screen.queryByText(/carries no compatibility rule/i)).not.toBeInTheDocument();
});
