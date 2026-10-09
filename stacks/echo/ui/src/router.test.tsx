import { describe, it, expect, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { createMemoryRouter, RouterProvider } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { routes } from './router'
import { openBeaconPanel } from '@/lib/echoPaths'

// UIFPE-484: the redirect leaves the SPA; jsdom cannot navigate, so the
// page-leave helper is mocked and asserted instead.
vi.mock('@/lib/echoPaths', async (importOriginal) => {
  const mod = await importOriginal<typeof import('@/lib/echoPaths')>()
  return { ...mod, openBeaconPanel: vi.fn() }
})

vi.mock('@/hooks/useHomeSummary', () => ({
  useHomeSummary: () => ({
    data: {
      syncs:   { ok: 0, fail: 0, in_24h: 0 },
      exports: { ok: 0, fail: 0, in_24h: 0 },
      dq:      { ok: 0, fail: 0, in_24h: 0 },
      window_hours: 24,
      generated_at: '2026-06-13T00:00:00Z',
    },
    isLoading: false,
    isError: false,
  }),
}))
vi.mock('@/hooks/useRecentActivity', () => ({
  useRecentActivity: () => ({
    data: { items: [], count: 0 },
    isLoading: false,
    isError: false,
  }),
}))
vi.mock('@/hooks/usePipelines', () => ({
  usePipeline: () => ({ data: null, isLoading: false }),
  usePipelines: () => ({ data: [], isLoading: false }),
  useTogglePipeline: () => ({ mutate: vi.fn(), isPending: false }),
  useTriggerRun:    () => ({ mutate: vi.fn(), isPending: false }),
  useDeletePipeline:() => ({ mutate: vi.fn(), isPending: false }),
  useUpsertPipeline:() => ({ mutateAsync: vi.fn(), isPending: false }),
}))
vi.mock('@/hooks/useGoldPipelines', () => ({ useGoldPipelines: () => ({ data: [], isLoading: false, refetch: vi.fn() }) }))
vi.mock('@/hooks/useGoldRuns', () => ({ useGoldRuns: () => ({ data: { items: [], truncated: false }, isLoading: false }) }))
vi.mock('@/hooks/useGoldStats', () => ({ useGoldStats: () => ({ data: undefined, isLoading: true }) }))
vi.mock('@/hooks/useRuns', () => ({
  useRuns: () => ({ data: { items: [] } }),
  useRunsInfinite: () => ({ data: { pages: [{ items: [], next_token: null }] }, fetchNextPage: vi.fn(), hasNextPage: false }),
}))
vi.mock('@/hooks/useConnections',        () => ({ useConnections:        () => ({ data: [], isLoading: false }) }))
vi.mock('@/hooks/useGcsConnections',     () => ({ useGcsConnections:     () => ({ data: [], isLoading: false }) }))
vi.mock('@/hooks/useSilverBqConnections',() => ({ useSilverBqConnections:() => ({ data: [], isLoading: false }) }))
vi.mock('@/hooks/useCatalogDatasets',    () => ({ useCatalogDatasets:    () => ({ data: [],   isLoading: false }) }))
vi.mock('@/hooks/useDatasetSchema',      () => ({ useDatasetSchema:      () => ({ data: null, isLoading: false }) }))
vi.mock('@/hooks/useLineage',            () => ({ useLineage:            () => ({ data: null, isLoading: false }) }))
vi.mock('@/lib/api', () => ({
  api: {
    getAlertSubscribers: vi.fn().mockResolvedValue({ items: [], total: 0 }),
    getAlertRules: vi.fn().mockResolvedValue({ items: [], total: 0 }),
  },
}))

function renderAt(path: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const r = createMemoryRouter(routes, { initialEntries: [path] })
  render(<QueryClientProvider client={qc}><RouterProvider router={r} /></QueryClientProvider>)
  return r
}

describe('router', () => {
  it('renders Home at /', async () => {
    renderAt('/')
    await waitFor(() => {
      expect(screen.getByRole('heading', { level: 1, name: /^Welcome/ })).toBeInTheDocument()
    })
  })

  it('renders Insights Overview at /m/data-sync (UIFPE-681)', async () => {
    renderAt('/m/data-sync')
    await waitFor(() => {
      expect(screen.getByRole('heading', { level: 1, name: 'Insights' })).toBeInTheDocument()
    })
  })

  it('renders Insights Jobs at /m/data-sync/runs (UIFPE-681)', async () => {
    renderAt('/m/data-sync/runs')
    await waitFor(() => {
      expect(screen.getByRole('heading', { level: 1, name: 'Jobs' })).toBeInTheDocument()
    })
  })

  it('renders Insights Coming up at /m/data-sync/upcoming (UIFPE-681)', async () => {
    renderAt('/m/data-sync/upcoming')
    await waitFor(() => {
      expect(screen.getByRole('heading', { level: 1, name: 'Coming up' })).toBeInTheDocument()
    })
  })

  it('renders the Transform landing at /m/data-sync/gold (UIFPE-679)', async () => {
    renderAt('/m/data-sync/gold')
    await waitFor(() => {
      expect(screen.getByRole('heading', { level: 1, name: 'Transform' })).toBeInTheDocument()
    })
  })

  it('sends /m/partner-export to the Beacon panel (UIFPE-484)', async () => {
    renderAt('/m/partner-export')
    await waitFor(() => {
      expect(vi.mocked(openBeaconPanel)).toHaveBeenCalled()
    })
  })

  it('redirects /m/data-quality to /m/data-quality/checks', async () => {
    const r = renderAt('/m/data-quality')
    await waitFor(() => {
      expect(r.state.location.pathname).toBe('/m/data-quality/checks')
    })
  })

  it('renders Catalog Browse at /m/catalog (after redirect)', async () => {
    renderAt('/m/catalog')
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /^Catalog$/ })).toBeInTheDocument()
    })
  })

  it('renders Settings Index at /settings', async () => {
    renderAt('/settings')
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /^Settings$/ })).toBeInTheDocument()
    })
  })

  it('redirects /dashboard to /', async () => {
    const r = renderAt('/dashboard')
    await waitFor(() => {
      expect(r.state.location.pathname).toBe('/')
    })
  })

  it('redirects /pipelines to /m/data-sync/syncs', async () => {
    const r = renderAt('/pipelines')
    await waitFor(() => {
      expect(r.state.location.pathname).toBe('/m/data-sync/syncs')
    })
  })

  it('redirects /pipelines/new to /m/data-sync/syncs/new', async () => {
    const r = renderAt('/pipelines/new')
    await waitFor(() => {
      expect(r.state.location.pathname).toBe('/m/data-sync/syncs/new')
    })
  })

  it('redirects /pipelines/:id to /m/data-sync/syncs/:id', async () => {
    const r = renderAt('/pipelines/abc-123')
    await waitFor(() => {
      expect(r.state.location.pathname).toBe('/m/data-sync/syncs/abc-123')
    })
  })

  it('redirects /reconciliation to /m/data-quality/checks', async () => {
    const r = renderAt('/reconciliation')
    await waitFor(() => {
      expect(r.state.location.pathname).toBe('/m/data-quality/checks')
    })
  })

  it('redirects /connections to /settings/connections', async () => {
    const r = renderAt('/connections')
    await waitFor(() => {
      expect(r.state.location.pathname).toBe('/settings/connections')
    })
  })

  it('redirects /alerts to /settings/notifications', async () => {
    const r = renderAt('/alerts')
    await waitFor(() => {
      expect(r.state.location.pathname).toBe('/settings/notifications')
    })
  })

  it('redirects /catalog to /m/catalog/browse', async () => {
    const r = renderAt('/catalog')
    await waitFor(() => {
      expect(r.state.location.pathname).toBe('/m/catalog/browse')
    })
  })
})
