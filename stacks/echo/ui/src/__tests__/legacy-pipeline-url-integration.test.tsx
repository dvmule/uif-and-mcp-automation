import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { RouterProvider, createMemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

vi.mock('@/hooks/usePipelines', () => ({
  usePipeline: () => ({
    data: {
      pipeline_id: 'sync_demo',
      version: 'latest',
      enabled: true,
      source: { secret_arn: 'arn:secret' },
      extract: { table: 'INV.DEMO', mode: 'full' },
      target: { s3_path: 's3://x/' },
      glue: { worker_type: 'G.1X', num_workers: 2, timeout_minutes: 60 },
      schedule: { frequency: 'daily' },
    },
    isLoading: false,
  }),
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
vi.mock('@/hooks/useDqChecks',           () => ({ useDqChecks:           () => ({ data: [], isLoading: false }) }))
vi.mock('@/hooks/usePartnerExports',     () => ({ usePartnerExports:     () => ({ data: [], isLoading: false }) }))
vi.mock('@/hooks/useDownstreamSyncs',    () => ({ useDownstreamSyncs:    () => ({ data: [] }) }))
// SyncDetail's Dependencies card counts the gold tables that read its silver table (UIFPE-682).
vi.mock('@/hooks/useGoldPipelines',      () => ({ useGoldPipelines:      () => ({ data: [], isLoading: false }) }))

import { routes } from '@/router'

describe('legacy /pipelines/:id → /m/data-sync/syncs/:id integration', () => {
  beforeEach(() => {
    if (!window.matchMedia) {
      window.matchMedia = vi.fn().mockReturnValue({
        matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn(),
      }) as unknown as typeof window.matchMedia
    }
  })

  it('navigating to /pipelines/sync_demo lands on SyncDetail and rewrites the URL', async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const router = createMemoryRouter(routes, { initialEntries: ['/pipelines/sync_demo'] })

    render(
      <QueryClientProvider client={qc}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    )

    await waitFor(() => {
      // Hero title reads the table name from the fixture — proves we landed on the
      // new SyncDetail (the old tabs UI was removed in the Mission Control redesign).
      expect(screen.getByRole('heading', { name: /INV\.DEMO/ })).toBeInTheDocument()
    })

    // URL has been rewritten by the RedirectLegacyPipelineDetail wrapper
    expect(router.state.location.pathname).toBe('/m/data-sync/syncs/sync_demo')
  })
})
