import { useEffect } from 'react';
import { render, screen, waitFor, fireEvent, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { SchemaDetail } from './SchemaDetail';

vi.mock('@monaco-editor/react', () => ({
  default: ({ value }: { value?: string }) => <pre data-testid="monaco-editor">{value}</pre>,
  DiffEditor: () => <div data-testid="diff-editor" />,
}));

// React Flow measures its container, which jsdom gives no size, so the real
// canvas renders nothing useful and logs about it. The graph's structure is
// covered directly against `buildSchemaGraph` in schema-body.test.ts; what
// matters here is that the page mounts the right view.
vi.mock('@xyflow/react', () => ({
  ReactFlow: ({ children }: { children?: React.ReactNode }) => (
    <div data-testid="react-flow">{children}</div>
  ),
  Handle: () => null,
  Panel: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
  Position: { Left: 'left', Right: 'right' },
  useReactFlow: () => ({ zoomIn: () => {}, zoomOut: () => {}, fitView: () => {} }),
}));

const BODY = JSON.stringify(
  {
    type: 'record',
    name: 'Product',
    namespace: 'com.pearson.commerce',
    fields: [
      { name: 'product_id', type: 'string' },
      { name: 'buyer_email', type: 'string' },
      { name: 'placed_at', type: { type: 'long', logicalType: 'timestamp-millis' } },
    ],
    metadata: {
      owner: { service: 'CI0093421', team: 'TM-COMMERCE' },
      domain: 'commerce',
      dataClassification: 'internal',
      piiFields: ['buyer_email'],
      lifecycle: 'active',
    },
  },
  null,
  2,
);

const DETAIL = {
  artifact_id: 'commerce.product-value',
  group_id: 'default',
  name: null,
  type: 'AVRO',
  state: 'ENABLED',
  created_on: '2024-01-01T00:00:00Z',
  modified_on: '2024-03-01T00:00:00Z',
  content: BODY,
  content_version: '2',
  versions: [
    { version: '1', state: 'ENABLED', created_on: '2024-01-01T00:00:00Z', created_by: null },
    { version: '2', state: 'ENABLED', created_on: '2024-03-01T00:00:00Z', created_by: null },
  ],
  // `team` is null on the artifact's own properties even though the body's
  // metadata block above names one — the facts band must not paper over that.
  owner: { service: 'CI0093421', team: null, email: null },
  domain: 'commerce',
  classification: 'internal',
  lifecycle: 'active',
  pii_fields: [],
  latest_version: '2',
  compatibility: null,
};

const API = 'http://localhost:8000/api/v1';

const server = setupServer(
  http.get(`${API}/schemas/default/commerce.product-value`, () => HttpResponse.json(DETAIL)),
  http.get(`${API}/schemas/non-compliant`, () =>
    HttpResponse.json({ items: [], last_validated: null, total_checked: 1 }),
  ),
  http.get(`${API}/schemas/default/commerce.product-value/services`, () =>
    HttpResponse.json({
      artifact_id: 'commerce.product-value',
      group_id: 'default',
      producers: [],
      consumers: [],
    }),
  ),
);

beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

// The version is URL state now, so the tests need to see the URL.
// Written from an effect, not during render — see the note in
// `SchemaListPage.test.tsx`.
let routed = { pathname: '', search: '' };

function LocationProbe() {
  const current = useLocation();
  useEffect(() => {
    routed = { pathname: current.pathname, search: current.search };
  }, [current]);
  return null;
}

const currentSearch = () => routed.search;

function renderDetail(url = '/schemas/default/commerce.product-value') {
  routed = { pathname: '', search: '' };
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[url]}>
        <SchemaDetail groupId="default" artifactId="commerce.product-value" />
        <LocationProbe />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('page chrome', () => {
  it('renders the subject with its suffix split into a badge', async () => {
    renderDetail();
    // The `-value` suffix is Apicurio's, not part of the subject anyone names,
    // so the heading carries the base and the suffix sits beside it.
    await waitFor(() =>
      expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('commerce.product'),
    );
    expect(screen.getByText('value')).toBeInTheDocument();
  });

  it('renders schema content in the code view by default', async () => {
    renderDetail();
    await waitFor(() => expect(screen.getByTestId('monaco-editor')).toBeInTheDocument());
    expect(screen.getByTestId('monaco-editor')).toHaveTextContent('"name": "Product"');
  });

  it('the facts band shows the artifact properties, not the body metadata', async () => {
    renderDetail();
    await waitFor(() => expect(screen.getByText('Owner CI')).toBeInTheDocument());
    expect(screen.getByText('CI0093421')).toBeInTheDocument();
    // The body's metadata block names TM-COMMERCE. The artifact's own
    // properties do not, and this band reads the properties. The em-dash is
    // the honest answer; synthesising the value from the body would claim
    // Apicurio holds something it does not.
    const ownerTeam = screen.getByText('Owner team').closest('div')!;
    expect(within(ownerTeam).getByText('—')).toBeInTheDocument();
    expect(within(ownerTeam).queryByText('TM-COMMERCE')).not.toBeInTheDocument();
  });

  it('states the compatibility rule as not set', async () => {
    // 0 of 12 subjects have one. This is the per-schema face of that number.
    renderDetail();
    await waitFor(() => expect(screen.getByText('Compatibility')).toBeInTheDocument());
    const cell = screen.getByText('Compatibility').closest('div')!;
    expect(within(cell).getByText('not set')).toBeInTheDocument();
  });

  it('version dropdown contains all versions', async () => {
    renderDetail();
    const versionSelect = await screen.findByLabelText('Version');
    const options = within(versionSelect).getAllByRole('option');
    expect(options).toHaveLength(2);
    expect(options[0]).toHaveValue('1');
    expect(options[1]).toHaveValue('2');
  });

  it('selecting a version triggers a new fetch', async () => {
    const v1Body = JSON.stringify({ type: 'record', name: 'ProductV1', fields: [] }, null, 2);
    server.use(
      http.get(`${API}/schemas/default/commerce.product-value`, ({ request }) => {
        const url = new URL(request.url);
        if (url.searchParams.get('version') === '1') {
          return HttpResponse.json({ ...DETAIL, content: v1Body, content_version: '1' });
        }
        return HttpResponse.json(DETAIL);
      }),
    );

    renderDetail();
    const versionSelect = await screen.findByLabelText('Version');
    fireEvent.change(versionSelect, { target: { value: '1' } });
    await waitFor(() =>
      expect(screen.getByTestId('monaco-editor')).toHaveTextContent('ProductV1'),
    );
  });

  // Every other fixture here sets `content_version` to a concrete version, which
  // is why nothing caught this: the real BFF echoes the literal sentinel
  // "latest" when the detail is fetched without an explicit version — which is
  // how this component fetches it. "latest" matches no <option>, so the browser
  // fell back to showing the FIRST option (v1) while the editor displayed v2's
  // content: the selector named a version other than the one on screen.
  it('selects the newest version when the API reports content_version as "latest"', async () => {
    server.use(
      http.get(`${API}/schemas/default/commerce.product-value`, () =>
        HttpResponse.json({ ...DETAIL, content_version: 'latest' }),
      ),
    );
    renderDetail();
    const select = await screen.findByLabelText('Version');
    await waitFor(() => expect((select as HTMLSelectElement).value).toBe('2'));
  });

  it('disables Compare versions when there is only one version', async () => {
    server.use(
      http.get(`${API}/schemas/default/commerce.product-value`, () =>
        HttpResponse.json({
          ...DETAIL,
          content_version: '1',
          versions: [DETAIL.versions[0]],
        }),
      ),
    );
    renderDetail();
    const compare = await screen.findByRole('button', { name: /compare versions/i });
    expect(compare).toBeDisabled();
  });
});

describe('the retired Overview tab', () => {
  it('offers Schema, Compliance, Services and History — not Overview', async () => {
    renderDetail();
    await waitFor(() => expect(screen.getByTestId('monaco-editor')).toBeInTheDocument());
    const tabs = screen.getAllByRole('tab').filter((t) => !['Fields', 'Code', 'Graph'].includes(t.textContent ?? ''));
    expect(tabs.map((t) => t.textContent)).toEqual([
      'Schema',
      'Compliance',
      'Services',
      'History',
    ]);
  });

  it('resolves a stale ?dtab=overview link onto Schema', async () => {
    // The drawer's Overview tab is gone — its `<dl>` is the facts band now —
    // but `?dtab=overview` links were shareable, so the value still parses.
    renderDetail('/schemas/default/commerce.product-value?dtab=overview');
    await waitFor(() => expect(screen.getByTestId('monaco-editor')).toBeInTheDocument());
    expect(screen.getByRole('tab', { name: 'Schema' })).toHaveAttribute('aria-selected', 'true');
  });
});

describe('field view', () => {
  it('tabulates the record fields with their types and PII marks', async () => {
    renderDetail('/schemas/default/commerce.product-value?schemaView=fields');
    await waitFor(() => expect(screen.getByText('product_id')).toBeInTheDocument());

    expect(screen.getByText('Product')).toBeInTheDocument();
    expect(screen.getByText('record · com.pearson.commerce')).toBeInTheDocument();
    // A logical type changes what the value means, so it is not collapsed.
    expect(screen.getByText('long (timestamp-millis)')).toBeInTheDocument();
    // Exactly one field is in the metadata block's piiFields.
    expect(screen.getAllByText('PII')).toHaveLength(1);
  });

  it('shows the metadata block compliance is read from', async () => {
    renderDetail('/schemas/default/commerce.product-value?schemaView=fields');
    await waitFor(() => expect(screen.getByText('metadata')).toBeInTheDocument());
    expect(
      screen.getByText('written by the registry · compliance is read from this block'),
    ).toBeInTheDocument();
    expect(screen.getByText('owner.service')).toBeInTheDocument();
    // This is the value the facts band shows as `—`; both are correct.
    expect(screen.getByText('TM-COMMERCE')).toBeInTheDocument();
  });

  it('derives the match count from the matches rather than hard-coding it', async () => {
    renderDetail('/schemas/default/commerce.product-value?schemaView=fields');
    const find = await screen.findByLabelText('Find in this schema');

    fireEvent.change(find, { target: { value: 'email' } });
    // `buyer_email` (field name), `owner.email`? no — owner has no email here.
    // So: the field name, and the `piiFields` value `buyer_email`. Two.
    await waitFor(() =>
      expect(screen.getByText('2 matches in fields and metadata')).toBeInTheDocument(),
    );

    // `internal` hits exactly one thing: the dataClassification value. If the
    // count were a literal it would still read 2 here.
    fireEvent.change(find, { target: { value: 'internal' } });
    await waitFor(() =>
      expect(screen.getByText('1 match in fields and metadata')).toBeInTheDocument(),
    );
  });

  it('reports one match in the singular', async () => {
    renderDetail('/schemas/default/commerce.product-value?schemaView=fields');
    const find = await screen.findByLabelText('Find in this schema');
    fireEvent.change(find, { target: { value: 'placed_at' } });
    await waitFor(() =>
      expect(screen.getByText('1 match in fields and metadata')).toBeInTheDocument(),
    );
  });
});

describe('code view find', () => {
  it('reports n of m and steps through the matches', async () => {
    renderDetail();
    const find = await screen.findByLabelText('Find in this schema');
    fireEvent.change(find, { target: { value: 'name' } });
    await waitFor(() => expect(screen.getByText(/ of /)).toBeInTheDocument());
    const label = screen.getByText(/^\d+ of \d+$/).textContent!;
    expect(label).toMatch(/^1 of \d+$/);

    fireEvent.click(screen.getByLabelText('Next match'));
    await waitFor(() => expect(screen.getByText(/^2 of \d+$/)).toBeInTheDocument());
  });

  it('lists the matches by line number', async () => {
    renderDetail();
    const find = await screen.findByLabelText('Find in this schema');
    fireEvent.change(find, { target: { value: 'buyer_email' } });
    // Two lines carry it: the field declaration and the metadata piiFields
    // entry — which is the point of listing them by line rather than counting.
    await waitFor(() => expect(screen.getAllByText(/^L\d+$/)).toHaveLength(2));
  });

  it('says so when nothing matches, rather than showing an empty rail', async () => {
    renderDetail();
    const find = await screen.findByLabelText('Find in this schema');
    fireEvent.change(find, { target: { value: 'zzzznope' } });
    await waitFor(() =>
      expect(screen.getByText(/Nothing in this body matches/)).toBeInTheDocument(),
    );
    expect(screen.getByText('No matches')).toBeInTheDocument();
  });
});

describe('graph view', () => {
  it('mounts the graph and reports matched nodes', async () => {
    renderDetail('/schemas/default/commerce.product-value?schemaView=graph');
    await waitFor(() => expect(screen.getByTestId('react-flow')).toBeInTheDocument());
    const find = screen.getByLabelText('Find in this schema');
    fireEvent.change(find, { target: { value: 'buyer_email' } });
    // Three: the `fields[]` container (its row lists the field), the
    // `buyer_email` field node itself, and `metadata` (its piiFields row).
    // Exercised directly in schema-body.test.ts — here it just has to reach
    // the label.
    // Reported twice by design: once by the shared find field, once by the
    // graph's own floating search pill.
    await waitFor(() => expect(screen.getAllByText('3 nodes')).toHaveLength(2));
  });
});

describe('compliance', () => {
  it('renders the failing rules and counts them on the tab', async () => {
    server.use(
      http.get(`${API}/schemas/non-compliant`, () =>
        HttpResponse.json({
          items: [
            {
              artifact_id: 'commerce.product-value',
              group_id: 'default',
              passed: false,
              failures: [
                { rule: 'metadata_block_missing', message: 'No metadata block in the body.' },
              ],
              validated_at: '2026-01-01T00:00:00Z',
            },
          ],
          last_validated: '2026-01-01T00:00:00Z',
          total_checked: 1,
        }),
      ),
    );
    renderDetail('/schemas/default/commerce.product-value?dtab=compliance');
    await waitFor(() => expect(screen.getByText('metadata_block_missing')).toBeInTheDocument());
    const tab = screen.getByRole('tab', { name: /compliance/i });
    expect(within(tab).getByText('1')).toBeInTheDocument();
  });

  // The single most repeated claim on this page. An empty compliance response
  // from a FAILED query is not a pass.
  it('reads unknown, not compliant, when the compliance query fails', async () => {
    server.use(http.get(`${API}/schemas/non-compliant`, () => HttpResponse.error()));
    renderDetail('/schemas/default/commerce.product-value?dtab=compliance');
    await waitFor(() => expect(screen.getAllByText('unknown').length).toBeGreaterThan(0));
    expect(screen.queryByText('compliant')).not.toBeInTheDocument();
    expect(
      screen.getByText(/could not be loaded, so nothing is known/i),
    ).toBeInTheDocument();
  });
});

describe('history', () => {
  it('says there is nothing to compare when there is one version', async () => {
    server.use(
      http.get(`${API}/schemas/default/commerce.product-value`, () =>
        HttpResponse.json({
          ...DETAIL,
          content_version: '1',
          versions: [DETAIL.versions[0]],
        }),
      ),
    );
    renderDetail('/schemas/default/commerce.product-value?dtab=history');
    await waitFor(() =>
      expect(
        screen.getByText('One version so far, so there is nothing to compare against yet.'),
      ).toBeInTheDocument(),
    );
    // Rather than a select whose only option is its own placeholder.
    expect(screen.queryByLabelText(/Compare v/)).not.toBeInTheDocument();
  });

  it('offers the earlier versions to diff against when there are several', async () => {
    renderDetail('/schemas/default/commerce.product-value?dtab=history');
    await waitFor(() => expect(screen.getByLabelText('Compare v2 against')).toBeInTheDocument());
    expect(
      screen.queryByText('One version so far, so there is nothing to compare against yet.'),
    ).not.toBeInTheDocument();
  });
});

describe('the compatibility gap', () => {
  it('leads the rail, and its action is disabled rather than inert', async () => {
    renderDetail();
    await waitFor(() => expect(screen.getByText('No compatibility rule')).toBeInTheDocument());
    expect(
      screen.getByText(/a breaking change would register silently/i),
    ).toBeInTheDocument();
    // There is no BFF endpoint for writing a rule. A button that silently did
    // nothing is what the redesign exists to remove.
    expect(screen.getByRole('button', { name: 'Set a rule' })).toBeDisabled();
    expect(screen.getByText(/rules are set in Apicurio directly/i)).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Deep links and downloads.
// ---------------------------------------------------------------------------

describe('deep linking', () => {
  // The version used to be component state, so the address bar named a subject
  // while the page showed a version — and a link you sent someone opened at
  // whatever was latest for them, not what you were looking at.
  it('pins the chosen version in the URL', async () => {
    renderDetail();
    const select = await screen.findByLabelText('Version');
    fireEvent.change(select, { target: { value: '1' } });
    await waitFor(() => expect(currentSearch()).toContain('version=1'));
  });

  it('loads the pinned version from the URL', async () => {
    const v1Body = JSON.stringify({ type: 'record', name: 'ProductV1', fields: [] }, null, 2);
    server.use(
      http.get(`${API}/schemas/default/commerce.product-value`, ({ request }) => {
        const url = new URL(request.url);
        if (url.searchParams.get('version') === '1') {
          return HttpResponse.json({ ...DETAIL, content: v1Body, content_version: '1' });
        }
        return HttpResponse.json(DETAIL);
      }),
    );
    renderDetail('/schemas/default/commerce.product-value?version=1');
    await waitFor(() =>
      expect(screen.getByTestId('monaco-editor')).toHaveTextContent('ProductV1'),
    );
    expect((screen.getByLabelText('Version') as HTMLSelectElement).value).toBe('1');
  });

  it('leaves no version in the URL until one is chosen, so links do not go stale', async () => {
    renderDetail();
    await waitFor(() => expect(screen.getByTestId('monaco-editor')).toBeInTheDocument());
    expect(currentSearch()).not.toContain('version=');
  });

  it('copies a link carrying the version, the tab and the view', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });

    renderDetail('/schemas/default/commerce.product-value?dtab=schema&schemaView=fields&version=2');
    const button = await screen.findByRole('button', { name: /copy a link to this version/i });
    fireEvent.click(button);

    await waitFor(() => expect(writeText).toHaveBeenCalledTimes(1));
    const link = writeText.mock.calls[0][0] as string;
    expect(link).toContain('/schemas/default/commerce.product-value');
    expect(link).toContain('version=2');
    expect(link).toContain('schemaView=fields');
    // Absolute, so it can be pasted somewhere other than the address bar.
    expect(link.startsWith('http')).toBe(true);
  });
});

describe('downloading a version', () => {
  it('names the file for the schema type and the version on screen', async () => {
    renderDetail();
    const button = await screen.findByRole('button', { name: /download this version/i });
    // AVRO, v2 — not `.json`, and not the subject alone.
    expect(button).toHaveAttribute('title', 'Download commerce.product-value.v2.avsc');
  });

  it('writes the body to a blob and clicks a download link', async () => {
    // Fake timers only here: the revoke is deferred, and the rest of this
    // file waits on real queries.
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const createObjectURL = vi.fn().mockReturnValue('blob:schema');
    const revokeObjectURL = vi.fn();
    Object.assign(URL, { createObjectURL, revokeObjectURL });
    // Captured AT the click: the helper removes the anchor again straight
    // afterwards, so inspecting it later says nothing.
    let connectedAtClick: boolean | null = null;
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, 'click')
      .mockImplementation(function (this: HTMLAnchorElement) {
        connectedAtClick = this.isConnected;
      });

    renderDetail();
    const button = await screen.findByRole('button', { name: /download this version/i });
    fireEvent.click(button);

    expect(createObjectURL).toHaveBeenCalledTimes(1);
    // jsdom's Blob exposes no text() — type and byte length are what it can
    // answer, and together they pin the media type and the exact body.
    const blob = createObjectURL.mock.calls[0][0] as Blob;
    expect(blob.type).toBe('application/json');
    expect(blob.size).toBe(new TextEncoder().encode(BODY).length);
    expect(click).toHaveBeenCalledTimes(1);

    // The anchor is IN the document when it is clicked. Detached, it works in
    // Chromium and does nothing in Firefox — and the e2e suite is Chromium
    // only, so it could not have caught that.
    expect(connectedAtClick).toBe(true);

    // And the URL is NOT released in the same task as the click. It used to
    // be revoked on the next line, before a browser is guaranteed to have
    // begun fetching the blob.
    expect(revokeObjectURL).not.toHaveBeenCalled();
    // Released eventually, so it is not leaked either.
    vi.advanceTimersByTime(10_000);
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:schema');

    click.mockRestore();
    vi.useRealTimers();
  });

  it('downloads the pinned version, not the latest', async () => {
    const v1Body = JSON.stringify({ type: 'record', name: 'ProductV1', fields: [] }, null, 2);
    server.use(
      http.get(`${API}/schemas/default/commerce.product-value`, ({ request }) => {
        const url = new URL(request.url);
        if (url.searchParams.get('version') === '1') {
          return HttpResponse.json({ ...DETAIL, content: v1Body, content_version: '1' });
        }
        return HttpResponse.json(DETAIL);
      }),
    );
    renderDetail('/schemas/default/commerce.product-value?version=1');
    const button = await screen.findByRole('button', { name: /download this version/i });
    await waitFor(() =>
      expect(button).toHaveAttribute('title', 'Download commerce.product-value.v1.avsc'),
    );
  });
});

describe('services tab', () => {
  /**
   * "No services produce this schema" reads as "nothing breaks if you change
   * it". That is the most consequential sentence on the page, and it was
   * printed whenever the CMDB lookup FAILED: `data` is undefined on error as
   * well as on empty, and `?? []` collapsed the two.
   */
  it('does not report a failed lookup as no producers and no consumers', async () => {
    server.use(
      http.get(`${API}/schemas/default/commerce.product-value/services`, () =>
        HttpResponse.json({ error: 'cmdb_unavailable' }, { status: 502 }),
      ),
    );
    renderDetail('/schemas/default/commerce.product-value?dtab=services');

    expect(await screen.findByText('Could not load services')).toBeInTheDocument();
    expect(screen.queryByText('No services produce this schema.')).not.toBeInTheDocument();
    expect(screen.queryByText('No services consume this schema.')).not.toBeInTheDocument();
  });

  it('still says none when the lookup succeeds and there are none', async () => {
    // The empty state is correct and must survive: the fix has to separate
    // "none" from "unknown", not replace one with the other.
    renderDetail('/schemas/default/commerce.product-value?dtab=services');
    expect(await screen.findByText('No services produce this schema.')).toBeInTheDocument();
    expect(screen.getByText('No services consume this schema.')).toBeInTheDocument();
    expect(screen.queryByText('Could not load services')).not.toBeInTheDocument();
  });
});

describe('finding in a minified body', () => {
  /**
   * The registry stores what it was sent. Bodies published through this app
   * are re-serialised with indent=2 by the BFF, which is why every artifact
   * in the live registry is pretty-printed — but the seed script's own
   * `json.dumps` has no indent, and anything registered straight against
   * Apicurio keeps the shape it arrived in.
   *
   * The editor reformats such a body for display. The page's match rail, its
   * "n of m" count and its stepper were all computed from the RAW string, so
   * they described a document nobody was looking at: measured on this body,
   * raw reports one match on line 1 where the rendered text has two, on
   * separate lines.
   */
  const MINIFIED = JSON.stringify({
    type: 'record',
    name: 'Product',
    fields: [
      { name: 'buyer_email', type: 'string' },
      { name: 'buyer_email_verified', type: 'boolean' },
    ],
  });

  beforeEach(() => {
    server.use(
      http.get(`${API}/schemas/default/commerce.product-value`, () =>
        HttpResponse.json({ ...DETAIL, content: MINIFIED }),
      ),
    );
  });

  it('counts the matches the rendered body has, not the stored one', async () => {
    renderDetail('/schemas/default/commerce.product-value?schemaView=code');
    const find = await screen.findByLabelText('Find in this schema');
    fireEvent.change(find, { target: { value: 'buyer_email' } });

    // Two, because the editor shows two lines. The raw one-line body reported
    // one.
    await waitFor(() => expect(screen.getByText('1 of 2')).toBeInTheDocument());
  });

  it('lists the matches on the lines the editor actually shows', async () => {
    renderDetail('/schemas/default/commerce.product-value?schemaView=code');
    const find = await screen.findByLabelText('Find in this schema');
    fireEvent.change(find, { target: { value: 'buyer_email' } });

    const lines = await waitFor(() => {
      const found = screen.getAllByText(/^L\d+$/);
      expect(found).toHaveLength(2);
      return found;
    });
    // Not both on L1, which is where a minified body puts everything.
    const numbers = lines.map((el) => Number(el.textContent!.slice(1)));
    expect(new Set(numbers).size).toBe(2);
    expect(numbers.every((n) => n > 1)).toBe(true);
  });
});

/**
 * The way in to editing a schema. The registry publishes a new version through
 * the same endpoint a fresh subject goes through, so what the detail page owes
 * is a link that carries the version the reader is looking at.
 */
describe('updating the schema', () => {
  it('links to the edit page for this subject', async () => {
    renderDetail();
    const update = await screen.findByRole('link', { name: /update schema/i });
    expect(update).toHaveAttribute('href', '/schemas/default/commerce.product-value/edit');
  });

  // Update on a page showing v1 has to edit v1. Without the pin it would open
  // the head version while the page it was clicked from showed an older one.
  it('carries the pinned version through to the edit page', async () => {
    renderDetail('/schemas/default/commerce.product-value?version=1');
    const update = await screen.findByRole('link', { name: /update schema/i });
    expect(update).toHaveAttribute(
      'href',
      '/schemas/default/commerce.product-value/edit?version=1',
    );
  });
});

/**
 * A gold attribute mapping is a different body from a schema: no `fields`, no
 * `properties`, no metadata block. `parseSchemaBody` finds nothing in one, so
 * the Mapping tab is the only reading of it that means anything — and it is
 * offered to nothing else. Design spec §3.2.
 */
const MAPPING_BODY = JSON.stringify({
  $schema: '../../gold-attribute-mapping.schema.json',
  domain: 'commerce',
  table: 'gold_commerce.product',
  drivingTable: 'MTL_SYSTEM_ITEMS_B',
  entityKey: [{ goldColumn: 'inventoryItemId', drivingColumn: 'INVENTORY_ITEM_ID' }],
  columns: [{
    goldColumnName: 'itemStatus', businessDescription: 'Lifecycle status.',
    kind: 'scalar', dataType: 'string',
    source: { table: 'MTL_SYSTEM_ITEMS_B', column: 'INVENTORY_ITEM_STATUS_CODE', filter: null },
    transform: null, channels: { event: true, message: true, api: true },
    nullable: true, notes: '',
  }],
}, null, 2);

const MAPPING_DETAIL = { ...DETAIL, type: 'JSON', content: MAPPING_BODY };

function serveMapping() {
  server.use(
    http.get(`${API}/schemas/default/commerce.product-value`, () =>
      HttpResponse.json(MAPPING_DETAIL),
    ),
  );
}

describe('the mapping tab (UIFPE-598)', () => {
  it('is absent for an Avro body', async () => {
    renderDetail();
    await waitFor(() => expect(screen.getByRole('heading', { level: 1 })).toBeInTheDocument());
    expect(screen.queryByRole('tab', { name: 'Mapping' })).not.toBeInTheDocument();
  });

  it('is absent for a JSON Schema body', async () => {
    // Type JSON is not enough — the body shape decides.
    server.use(
      http.get(`${API}/schemas/default/commerce.product-value`, () =>
        HttpResponse.json({
          ...DETAIL, type: 'JSON',
          content: JSON.stringify({
            $schema: 'https://json-schema.org/draft/2020-12/schema',
            type: 'object', properties: { id: { type: 'string' } },
          }),
        }),
      ),
    );
    renderDetail();
    await waitFor(() => expect(screen.getByRole('heading', { level: 1 })).toBeInTheDocument());
    expect(screen.queryByRole('tab', { name: 'Mapping' })).not.toBeInTheDocument();
  });

  it('appears for a mapping body', async () => {
    serveMapping();
    renderDetail();
    await waitFor(() =>
      expect(screen.getByRole('tab', { name: 'Mapping' })).toBeInTheDocument(),
    );
  });

  it('lands on the Mapping tab for a mapping body', async () => {
    // It is the only reading of a mapping that means anything, so landing on
    // Code and making the reader find it would waste the tab.
    serveMapping();
    renderDetail();
    await waitFor(() =>
      expect(screen.getByRole('tab', { name: 'Mapping' })).toHaveAttribute('aria-selected', 'true'),
    );
    expect(screen.getByText('gold_commerce.product')).toBeInTheDocument();
  });

  it('honours an explicit ?dtab=schema on a mapping', async () => {
    // Landing on Mapping is a default, not a lock — a deep link still wins.
    serveMapping();
    renderDetail('/schemas/default/commerce.product-value?dtab=schema');
    await waitFor(() =>
      expect(screen.getByRole('tab', { name: 'Schema' })).toHaveAttribute('aria-selected', 'true'),
    );
  });

  it('lets a CLICK reach the Schema tab on a mapping', async () => {
    // `set({ detailTab: 'schema' })` DELETES the param, because `schema` is
    // the URL default — so the URL alone cannot tell "the reader chose Schema"
    // from "the reader chose nothing". Without a record of the click the
    // Schema tab is unreachable on a mapping, and design spec §3.2 keeps Code
    // there for exactly this reader.
    serveMapping();
    renderDetail();
    await waitFor(() =>
      expect(screen.getByRole('tab', { name: 'Mapping' })).toHaveAttribute('aria-selected', 'true'),
    );
    fireEvent.click(screen.getByRole('tab', { name: 'Schema' }));
    await waitFor(() =>
      expect(screen.getByRole('tab', { name: 'Schema' })).toHaveAttribute('aria-selected', 'true'),
    );
    expect(screen.getByTestId('monaco-editor')).toBeInTheDocument();
  });

  it('switches sheet and writes mkind to the URL', async () => {
    serveMapping();
    renderDetail();
    await waitFor(() => expect(screen.getByRole('tab', { name: /Scalar/ })).toBeInTheDocument());
    fireEvent.click(screen.getByRole('tab', { name: /Nested/ }));
    await waitFor(() => expect(currentSearch()).toContain('mkind=nested'));
  });
});

describe('the Schema tab on a mapping body', () => {
  it('offers Code only — Fields and Graph would render nothing useful', async () => {
    // Fields finds no fields or properties in a mapping; Graph draws its
    // columns as anonymous objects.
    serveMapping();
    renderDetail('/schemas/default/commerce.product-value?dtab=schema');
    await waitFor(() => expect(screen.getByRole('tab', { name: 'Schema' })).toBeInTheDocument());
    expect(screen.getByRole('tab', { name: /Code/ })).toBeInTheDocument();
    expect(screen.queryByRole('tab', { name: /Fields/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('tab', { name: /Graph/ })).not.toBeInTheDocument();
  });

  it('answers a stale ?schemaView=fields link with an explanation, not a blank table', async () => {
    serveMapping();
    renderDetail('/schemas/default/commerce.product-value?dtab=schema&schemaView=fields');
    await waitFor(() => expect(screen.getByRole('tab', { name: 'Schema' })).toBeInTheDocument());
    // Not a bare /mapping/i: the Mapping TAB also matches that, so the loose
    // regex would pass on a page that never rendered the explanation.
    expect(screen.getByText(/gold attribute mapping, not a schema/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Mapping tab/ })).toBeInTheDocument();
  });

  it('still offers all three views for an Avro body', async () => {
    renderDetail('/schemas/default/commerce.product-value?dtab=schema');
    await waitFor(() => expect(screen.getByRole('tab', { name: /Fields/ })).toBeInTheDocument());
    expect(screen.getByRole('tab', { name: /Code/ })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /Graph/ })).toBeInTheDocument();
  });
});
