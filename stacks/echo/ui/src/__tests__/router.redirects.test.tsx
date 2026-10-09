import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { RouterProvider, createMemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

// Mocks must be set up before the router import.
vi.mock('@/hooks/usePipelines', () => ({
  usePipeline: () => ({ data: null, isLoading: false }),
  usePipelines: () => ({ data: [], isLoading: false }),
  useTogglePipeline: () => ({ mutate: vi.fn(), isPending: false }),
  useTriggerRun:    () => ({ mutate: vi.fn(), isPending: false }),
  useDeletePipeline:() => ({ mutate: vi.fn(), isPending: false }),
  useUpsertPipeline:() => ({ mutateAsync: vi.fn(), isPending: false }),
}))
vi.mock('@/hooks/useRuns', () => ({
  // Every remaining export of @/hooks/useRuns, so the mock is
  // COMPLETE. vitest rejects a partial module mock, and each
  // missing one surfaced only as the next render-time throw --
  // three rounds of that before the list was filled from the
  // module's own exports rather than from the error messages.
  useActiveRunId: () => undefined,
  useRun: () => ({ data: undefined, isLoading: false, isError: false }),
  // Both are called for their side effect only -- SyncDetail.tsx:53 and
  // Syncs.tsx:96 assign neither return value, and useActiveRunPoll returns
  // nothing at all -- so a no-op is a faithful mock, not a stub that hides a
  // shape. Missing from this mock until UIFPE-515: vitest rejects a partial
  // module mock, so the component threw
  // `No "useActiveRunPoll" export is defined on the "@/hooks/useRuns" mock`
  // at render and the assertion never ran. Nothing caught it because this
  // stack ran no vitest at all before qe/ci.sh gained the gate.
  useActiveRunPoll: () => {},
  useSyncsActiveRunPolls: () => {},
  useRuns: () => ({ data: { items: [] } }),
  useRunsInfinite: () => ({ data: { pages: [{ items: [], next_token: null }] }, fetchNextPage: vi.fn(), hasNextPage: false }),
}))
vi.mock('@/hooks/useConnections',        () => ({ useConnections:        () => ({ data: [], isLoading: false }) }))
vi.mock('@/hooks/useGcsConnections',     () => ({ useGcsConnections:     () => ({ data: [], isLoading: false }) }))
vi.mock('@/hooks/useSilverBqConnections',() => ({ useSilverBqConnections:() => ({ data: [], isLoading: false }) }))
vi.mock('@/hooks/useHomeSummary',        () => ({ useHomeSummary:        () => ({ data: { syncs: {ok:0,fail:0,in_24h:0}, exports: {ok:0,fail:0,in_24h:0}, dq: {ok:0,fail:0,in_24h:0}, window_hours: 24, generated_at: '' }, isLoading: false, isError: false }) }))
vi.mock('@/hooks/useRecentActivity',     () => ({ useRecentActivity:     () => ({ data: { items: [], count: 0 }, isLoading: false, isError: false }) }))
// SyncDetail reads the gold configs for its Dependencies card (UIFPE-682).
vi.mock('@/hooks/useGoldPipelines',      () => ({ useGoldPipelines:      () => ({ data: [], isLoading: false }) }))

import { routes } from '@/router'

function renderAt(initialPath: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const router = createMemoryRouter(routes, { initialEntries: [initialPath] })
  return render(
    <QueryClientProvider client={qc}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
}

describe('legacy redirects', () => {
  beforeEach(() => {
    if (!window.matchMedia) {
      window.matchMedia = vi.fn().mockReturnValue({
        matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn(),
      }) as unknown as typeof window.matchMedia
    }
  })

  it('/pipelines redirects to /m/data-sync/syncs', async () => {
    renderAt('/pipelines')
    await waitFor(() => {
      // The Syncs page is titled "Replicate" since UIFPE-679.
      expect(screen.getByRole('heading', { level: 1, name: /^Replicate$/ })).toBeInTheDocument()
    })
  })

  it('/pipelines/new redirects to /m/data-sync/syncs/new', async () => {
    renderAt('/pipelines/new')
    await waitFor(() => {
      // The wizard is titled "New replication" since UIFPE-683.
      expect(screen.getByRole('heading', { level: 1, name: /^New replication$/ })).toBeInTheDocument()
    })
  })

  it('/pipelines/:id redirects to /m/data-sync/syncs/:syncId', async () => {
    renderAt('/pipelines/some_id')
    await waitFor(() => {
      expect(screen.getByText(/Sync not found\./)).toBeInTheDocument()
    })
  })
})
