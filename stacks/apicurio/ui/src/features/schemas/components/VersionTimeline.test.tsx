import { render, screen, waitFor, fireEvent, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse, delay } from 'msw';
import { setupServer } from 'msw/node';
import { VersionTimeline } from './VersionTimeline';
import type { SchemaVersionRead } from '../types/schema.types';

vi.mock('@monaco-editor/react', () => ({
  DiffEditor: ({
    original,
    modified,
    language,
  }: {
    original?: string;
    modified?: string;
    language?: string;
  }) => (
    <div
      data-testid="diff-editor"
      data-original={original}
      data-modified={modified}
      data-language={language}
    />
  ),
}));

const VERSIONS: SchemaVersionRead[] = [
  { version: '3', state: 'ENABLED', created_on: '2026-07-14T09:00:00Z', created_by: 'r.karunakaran' },
  { version: '2', state: 'ENABLED', created_on: '2026-05-02T09:00:00Z', created_by: 's.mehta' },
  { version: '1', state: 'ENABLED', created_on: '2026-03-11T09:00:00Z', created_by: 'seed' },
];

const API = 'http://localhost:8000/api/v1';

function detailFor(version: string) {
  return {
    artifact_id: 'commerce.order.placed-value',
    group_id: 'default',
    name: null,
    type: 'AVRO',
    state: 'ENABLED',
    created_on: '2026-03-11T09:00:00Z',
    modified_on: '2026-07-14T09:00:00Z',
    content: `{"version":${version}}`,
    content_version: version,
    versions: VERSIONS,
    owner: { service: null, team: null, email: null },
    domain: null,
    classification: null,
    lifecycle: null,
    pii_fields: [],
    latest_version: '3',
    compatibility: null,
  };
}

const server = setupServer(
  http.get(`${API}/schemas/default/commerce.order.placed-value`, ({ request }) => {
    const url = new URL(request.url);
    const version = url.searchParams.get('version') ?? '3';
    return HttpResponse.json(detailFor(version));
  }),
);

beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function renderTimeline(props: Partial<React.ComponentProps<typeof VersionTimeline>> = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const result = render(
    <QueryClientProvider client={qc}>
      <VersionTimeline
        groupId="default"
        artifactId="commerce.order.placed-value"
        versions={VERSIONS}
        currentVersion="3"
        {...props}
      />
    </QueryClientProvider>,
  );
  return { ...result, qc };
}

it('lists every version with who changed it and when', () => {
  renderTimeline();
  const list = within(screen.getByTestId('version-timeline-list'));
  expect(list.getByText('v3')).toBeInTheDocument();
  expect(list.getByText('v1')).toBeInTheDocument();
  expect(screen.getByText(/r\.karunakaran/)).toBeInTheDocument();
  expect(screen.getByText(/s\.mehta/)).toBeInTheDocument();
});

it('marks the current version', () => {
  renderTimeline();
  expect(screen.getByText(/current/i)).toBeInTheDocument();
});

it('offers a comparison between two versions', () => {
  renderTimeline();
  expect(screen.getByLabelText(/compare/i)).toBeInTheDocument();
});

it('does not throw and omits the date when created_on is null or malformed', () => {
  const versions: SchemaVersionRead[] = [
    { version: '2', state: 'ENABLED', created_on: null, created_by: 'r.karunakaran' },
    { version: '1', state: 'ENABLED', created_on: 'not-a-date', created_by: 'seed' },
  ];
  renderTimeline({ versions, currentVersion: '2' });
  const list = within(screen.getByTestId('version-timeline-list'));
  expect(list.getByText('v2')).toBeInTheDocument();
  expect(list.getByText('v1')).toBeInTheDocument();
});

it('shows a loading indication while the comparison is fetching', async () => {
  server.use(
    http.get(`${API}/schemas/default/commerce.order.placed-value`, async ({ request }) => {
      const url = new URL(request.url);
      const version = url.searchParams.get('version') ?? '3';
      await delay(50);
      return HttpResponse.json(detailFor(version));
    }),
  );
  renderTimeline();
  fireEvent.change(screen.getByLabelText(/compare/i), { target: { value: '1' } });
  expect(await screen.findByText(/loading/i)).toBeInTheDocument();
  await waitFor(() => expect(screen.getByTestId('diff-editor')).toBeInTheDocument());
});

it('tells the user when the comparison fetch fails', async () => {
  server.use(
    http.get(`${API}/schemas/default/commerce.order.placed-value`, () =>
      HttpResponse.json({ detail: 'boom' }, { status: 500 }),
    ),
  );
  renderTimeline();
  fireEvent.change(screen.getByLabelText(/compare/i), { target: { value: '1' } });
  expect(await screen.findByText(/could not load/i)).toBeInTheDocument();
});

it('renders the diff with the older version on the left and the current version on the right, without refetching content already provided', async () => {
  let fetchCount = 0;
  server.use(
    http.get(`${API}/schemas/default/commerce.order.placed-value`, ({ request }) => {
      fetchCount += 1;
      const url = new URL(request.url);
      const version = url.searchParams.get('version') ?? '3';
      return HttpResponse.json(detailFor(version));
    }),
  );
  renderTimeline({ currentContent: '{"version":"3"}' });
  fireEvent.change(screen.getByLabelText(/compare/i), { target: { value: '1' } });
  await waitFor(() => expect(screen.getByTestId('diff-editor')).toBeInTheDocument());
  const diffEditor = screen.getByTestId('diff-editor');
  // The older (compareTo) version's body must be the left/`original` side, and the
  // current version's body the right/`modified` side — a swap would render additions
  // as deletions with nothing else to catch it. The two bodies below are shaped
  // differently (bare number vs. quoted string) so a swap cannot pass by accident.
  expect(diffEditor).toHaveAttribute('data-original', '{"version":1}');
  expect(diffEditor).toHaveAttribute('data-modified', '{"version":"3"}');
  // Only the older (compareTo) version should be fetched; the current version's
  // content was already supplied via `currentContent`.
  expect(fetchCount).toBe(1);
});

it('resets the compare selection when the current version changes to match it', async () => {
  const { rerender, qc } = renderTimeline();
  const compareSelect = screen.getByLabelText(/compare/i) as HTMLSelectElement;

  fireEvent.change(compareSelect, { target: { value: '1' } });
  await waitFor(() => expect(screen.getByTestId('diff-editor')).toBeInTheDocument());

  // Simulate the page's own "Version" dropdown moving to the same version the user
  // had picked to compare against — currentVersion now equals the stale compareTo.
  rerender(
    <QueryClientProvider client={qc}>
      <VersionTimeline
        groupId="default"
        artifactId="commerce.order.placed-value"
        versions={VERSIONS}
        currentVersion="1"
        currentContent={'{"version":1}'}
      />
    </QueryClientProvider>,
  );

  await waitFor(() => expect(screen.getByLabelText(/compare/i)).toHaveValue(''));
  expect(screen.queryByTestId('diff-editor')).not.toBeInTheDocument();
});

it('highlights the diff in the schema type\'s own language, not always JSON', async () => {
  server.use(
    http.get(`${API}/schemas/default/commerce.order.placed-value`, ({ request }) => {
      const url = new URL(request.url);
      const version = url.searchParams.get('version') ?? '3';
      return HttpResponse.json({ ...detailFor(version), type: 'PROTOBUF' });
    }),
  );
  renderTimeline({ currentContent: 'syntax = "proto3";' });
  fireEvent.change(screen.getByLabelText(/compare/i), { target: { value: '1' } });
  await waitFor(() => expect(screen.getByTestId('diff-editor')).toBeInTheDocument());
  expect(screen.getByTestId('diff-editor')).toHaveAttribute('data-language', 'plaintext');
});

it('reads the version through useSchemaDetail\'s cache key, not a second namespace', async () => {
  server.use(
    http.get(`${API}/schemas/default/commerce.order.placed-value`, ({ request }) => {
      const url = new URL(request.url);
      const version = url.searchParams.get('version') ?? '3';
      return HttpResponse.json(detailFor(version));
    }),
  );
  const { qc } = renderTimeline({ currentContent: '{"version":"3"}' });
  fireEvent.change(screen.getByLabelText(/compare/i), { target: { value: '1' } });
  await waitFor(() => expect(screen.getByTestId('diff-editor')).toBeInTheDocument());

  const keys = qc.getQueryCache().getAll().map((q) => q.queryKey);
  // One resource, one namespace: whatever the subject page already fetched
  // through useSchemaDetail satisfies this read, and the write path's
  // invalidation cannot reach half of it.
  expect(keys).toContainEqual(['schema-detail', 'default', 'commerce.order.placed-value', '1']);
  expect(keys.filter((k) => (k as unknown[]).includes('content'))).toHaveLength(0);
});

// The BFF echoes the literal sentinel `content_version: "latest"` when the
// detail was fetched without an explicit version — which is exactly how the
// subject page fetches it. Seen live as "Compare vlatest against", and worse:
// `olderVersions` filters on `currentVersion`, so with "latest" nothing was
// filtered and the CURRENT version appeared in its own compare list, offering
// a diff of a version against itself.
//
// Resolution is by created_on, not by position: this fixture lists versions
// newest-first while the live API returns them oldest-first, so neither end of
// the array is reliably the newest.
it('resolves a non-version sentinel to the newest actual version', async () => {
  renderTimeline({ currentVersion: 'latest' });

  expect(await screen.findByText('Compare v3 against')).toBeInTheDocument();

  const select = screen.getByLabelText('Compare v3 against');
  const offered = [...select.querySelectorAll('option')]
    .map((o) => (o as HTMLOptionElement).value)
    .filter(Boolean);
  // v3 is the current version and must not be comparable with itself.
  expect(offered).toEqual(['2', '1']);

  // The "Current" marker keys on the same value, so the sentinel also meant
  // no row in the timeline was marked as the one being viewed.
  expect(screen.getAllByText('Current')).toHaveLength(1);
});

describe('diff direction', () => {
  /**
   * The compare list was named `olderVersions`, which holds only while the
   * newest version is the one being viewed. Pin v1 and it offers v2 and v3 —
   * both NEWER — and the sides were fixed: the chosen version always went to
   * `original`. So choosing v3 while viewing v1 put v3 on the left, and a
   * field added in v3 rendered as a deletion: the diff read as a rollback of
   * work that had actually happened.
   */
  it('puts the older version on the left when a newer one is chosen', async () => {
    renderTimeline({ currentVersion: '1', currentContent: '{"version":1}' });

    fireEvent.change(await screen.findByLabelText(/Compare v1 against/), {
      target: { value: '3' },
    });

    const diff = await waitFor(() => screen.getByTestId('diff-editor'));
    // v1 is older, so it is the original and v3 is the modification of it.
    expect(diff).toHaveAttribute('data-original', '{"version":1}');
    expect(diff).toHaveAttribute('data-modified', '{"version":3}');
  });

  it('still puts the older version on the left when viewing the newest', async () => {
    // The case that always worked, and must keep working: viewing v3 and
    // comparing against v1.
    renderTimeline({ currentVersion: '3', currentContent: '{"version":3}' });

    fireEvent.change(await screen.findByLabelText(/Compare v3 against/), {
      target: { value: '1' },
    });

    const diff = await waitFor(() => screen.getByTestId('diff-editor'));
    expect(diff).toHaveAttribute('data-original', '{"version":1}');
    expect(diff).toHaveAttribute('data-modified', '{"version":3}');
  });
});
