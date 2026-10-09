import { describe, it, expect, vi } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, useLocation, useRoutes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { routes } from '@/router'

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
vi.mock('@/hooks/useRuns', () => ({
  useRuns: () => ({ data: { items: [] } }),
  useRunsInfinite: () => ({ data: { pages: [{ items: [], next_token: null }] }, fetchNextPage: vi.fn(), hasNextPage: false }),
  useSyncsActiveRunPolls: () => {},
}))
vi.mock('@/hooks/useGoldPipelines', () => ({ useGoldPipelines: () => ({ data: [] }) }))
vi.mock('@/hooks/useGoldPipelineMutations', () => ({
  useTriggerGoldRun:  () => ({ mutate: vi.fn(), isPending: false, variables: undefined }),
  useSetGoldSchedule: () => ({ mutate: vi.fn(), isPending: false }),
}))
vi.mock('@/hooks/useConnections',        () => ({ useConnections:        () => ({ data: [], isLoading: false }) }))
vi.mock('@/hooks/useGcsConnections',     () => ({ useGcsConnections:     () => ({ data: [], isLoading: false }) }))
vi.mock('@/hooks/useSilverBqConnections',() => ({ useSilverBqConnections:() => ({ data: [], isLoading: false }) }))
vi.mock('@/lib/api', () => ({
  api: {
    getAlertSubscribers: vi.fn().mockResolvedValue({ items: [], total: 0 }),
    getAlertRules: vi.fn().mockResolvedValue({ items: [], total: 0 }),
    partnerExport: {
      listExports: vi.fn().mockResolvedValue([]),
      listPartners: vi.fn().mockResolvedValue([]),
      listFormats:  vi.fn().mockResolvedValue([]),
      listRuns:     vi.fn().mockResolvedValue({ items: [], next_token: null }),
    },
    dq: {
      listChecks: vi.fn().mockResolvedValue([]),
      listKinds:  vi.fn().mockResolvedValue({ kinds: [] }),
    },
  },
}))

// A component router (not createMemoryRouter): the data router builds a fetch
// Request on every navigation, which this jsdom + Node combination rejects.
function AppRoutes() {
  return useRoutes(routes)
}
function LocationProbe() {
  return <div data-testid="pathname">{useLocation().pathname}</div>
}

function renderAt(path: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <AppRoutes />
        <LocationProbe />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

const pathname = () => screen.getByTestId('pathname').textContent

describe('chrome integration', () => {
  it('clicking the Replicate rail link from Home lands on the Syncs list', async () => {
    const user = userEvent.setup()
    renderAt('/')
    expect(screen.getByRole('heading', { level: 1, name: /^Welcome/ })).toBeInTheDocument()
    await user.click(screen.getByRole('link', { name: 'Replicate' }))
    await waitFor(() => {
      expect(pathname()).toBe('/m/data-sync/syncs')
    })
    await waitFor(() => {
      expect(screen.getByRole('heading', { level: 1, name: 'Replicate' })).toBeInTheDocument()
    })
    expect(screen.getByRole('link', { name: 'Replicate' })).toHaveAttribute('aria-current', 'page')
    expect(screen.getByRole('navigation', { name: 'Section tabs' })).toBeInTheDocument()
  })

  it('the Export rail link leaves the SPA for the Beacon panel (UIFPE-484)', () => {
    renderAt('/')
    // A plain <a>, not a NavLink: the Beacon panel is a sibling static app,
    // so following it is a full page load jsdom cannot perform — assert the
    // target instead of clicking.
    const link = screen.getByRole('link', { name: 'Export · Beacon' })
    expect(link.getAttribute('href')).toBe('/m/beacon/index.html#/m/beacon')
  })

  it('clicking the settings cog navigates to /settings and shows the Settings rail', async () => {
    const user = userEvent.setup()
    renderAt('/')
    await user.click(screen.getByRole('button', { name: /^settings$/i }))
    await waitFor(() => {
      expect(pathname()).toBe('/settings')
    })
    const rail = screen.getByRole('navigation', { name: 'Settings menu' })
    expect(within(rail).getByRole('link', { name: 'Connections' })).toBeInTheDocument()
    expect(within(rail).getByRole('link', { name: 'Back to console' })).toBeInTheDocument()
  })
})
