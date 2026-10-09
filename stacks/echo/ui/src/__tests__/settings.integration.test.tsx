import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createMemoryRouter, RouterProvider } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

// Mock all the hooks pages need so they render without a live API.
vi.mock('@/hooks/useConnections', () => ({
  useConnections: () => ({ data: [], isLoading: false }),
}))
vi.mock('@/hooks/useGcsConnections', () => ({
  useGcsConnections: () => ({ data: [], isLoading: false }),
}))
vi.mock('@/hooks/useSilverBqConnections', () => ({
  useSilverBqConnections: () => ({ data: [], isLoading: false }),
}))
vi.mock('@/hooks/usePipelines', () => ({
  usePipelines: () => ({ data: [], isLoading: false }),
  usePipeline: () => ({ data: null, isLoading: false }),
  useTogglePipeline: () => ({ mutate: vi.fn(), isPending: false }),
  useTriggerRun: () => ({ mutate: vi.fn(), isPending: false }),
  useDeletePipeline: () => ({ mutate: vi.fn(), isPending: false }),
  useUpsertPipeline: () => ({ mutateAsync: vi.fn(), isPending: false }),
}))
vi.mock('@/hooks/useRuns', () => ({
  useRuns: () => ({ data: { items: [] } }),
  useRunsInfinite: () => ({ data: { pages: [{ items: [], next_token: null }] }, fetchNextPage: vi.fn(), hasNextPage: false }),
}))
vi.mock('@/hooks/useHomeSummary', () => ({
  useHomeSummary: () => ({ data: { syncs: {ok:0,fail:0,in_24h:0}, exports: {ok:0,fail:0,in_24h:0}, dq: {ok:0,fail:0,in_24h:0}, window_hours: 24, generated_at: '' }, isLoading: false, isError: false }),
}))
vi.mock('@/hooks/useRecentActivity', () => ({
  useRecentActivity: () => ({ data: { items: [], count: 0 }, isLoading: false, isError: false }),
}))
vi.mock('@/lib/api', () => ({
  api: {
    getAlertConfig: vi.fn().mockResolvedValue({ rules: [], slack_webhook_url: '', email_recipients: [] }),
    upsertAlertConfig: vi.fn(),
  },
}))

import { routes } from '@/router'

function setupAt(path: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const r = createMemoryRouter(routes, { initialEntries: [path] })
  render(
    <QueryClientProvider client={qc}>
      <RouterProvider router={r} />
    </QueryClientProvider>
  )
  return r
}

describe('Settings integration', () => {
  beforeEach(() => {
    if (!window.matchMedia) {
      window.matchMedia = vi.fn().mockReturnValue({
        matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn(),
      }) as unknown as typeof window.matchMedia
    }
  })

  it('clicking the Connections tile navigates to /settings/connections', async () => {
    const user = userEvent.setup()
    const r = setupAt('/settings')
    expect(await screen.findByRole('heading', { level: 1, name: /^settings$/i })).toBeInTheDocument()
    // Use the tile testid to avoid ambiguity with the SettingsSidebar NavLink
    await user.click(screen.getByTestId('settings-tile-connections'))
    await screen.findByRole('heading', { level: 1, name: /^connections$/i })
    expect(r.state.location.pathname).toBe('/settings/connections')
  })

  // UIFPE-684: the Users / API tokens / Audit tiles had no page and were removed.
  it('shows only the three live section tiles', async () => {
    setupAt('/settings')
    await screen.findByTestId('settings-tile-connections')
    expect(screen.getByTestId('settings-tile-notifications')).toBeInTheDocument()
    expect(screen.getByTestId('settings-tile-environments')).toBeInTheDocument()
    expect(screen.queryByTestId('settings-tile-users')).toBeNull()
  })
})
