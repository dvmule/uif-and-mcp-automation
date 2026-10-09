import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactElement } from 'react'
import { Navigate, RouterProvider, createMemoryRouter } from 'react-router-dom'
import { routes } from '@/router'

vi.mock('@/hooks/useHomeSummary', () => ({
  useHomeSummary: () => ({
    data: { syncs: { ok: 0, fail: 0, in_24h: 0 }, exports: { ok: 0, fail: 0, in_24h: 0 },
            dq: { ok: 0, fail: 0, in_24h: 0 }, window_hours: 24, generated_at: '' },
    isLoading: false, isError: false,
  }),
}))
vi.mock('@/hooks/useRecentActivity', () => ({
  useRecentActivity: () => ({ data: { items: [], count: 0 }, isLoading: false, isError: false }),
}))
vi.mock('@/hooks/usePipelines', () => ({
  usePipeline:       () => ({ data: null, isLoading: false }),
  usePipelines:      () => ({ data: [], isLoading: false }),
  useTogglePipeline: () => ({ mutate: vi.fn(), isPending: false }),
  useTriggerRun:     () => ({ mutate: vi.fn(), isPending: false }),
  useDeletePipeline: () => ({ mutate: vi.fn(), isPending: false }),
  useUpsertPipeline: () => ({ mutateAsync: vi.fn(), isPending: false }),
}))
vi.mock('@/lib/api', () => ({
  api: {
    dq: {
      listChecks: vi.fn().mockResolvedValue([]),
      getCheck:   vi.fn().mockResolvedValue(null),
      listKinds:  vi.fn().mockResolvedValue({ kinds: [] }),
      listCheckRuns: vi.fn().mockResolvedValue({ check_id: '', runs: [] }),
    },
    getAlertSubscribers: vi.fn().mockResolvedValue({ items: [], total: 0 }),
    getAlertRules: vi.fn().mockResolvedValue({ items: [], total: 0 }),
  },
}))

function wrapAt(path: string) {
  const router = createMemoryRouter(routes, { initialEntries: [path] })
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}><RouterProvider router={router} /></QueryClientProvider>)
}

describe('DQ module routing', () => {
  it('redirects /m/data-quality to /m/data-quality/checks', async () => {
    wrapAt('/m/data-quality')
    expect(await screen.findByText(/no data quality checks/i)).toBeInTheDocument()
  })

  // UIFPE-684: Overview folded into the Checks count line; asserted on the route table like kinds below.
  it('redirects /m/data-quality/overview to Checks', () => {
    const shell = routes.find(r => r.path === '/')
    const overview = shell?.children?.find(r => r.path === 'm/data-quality/overview')
    const el = overview?.element as ReactElement<{ to: string; replace?: boolean }> | undefined
    expect(el?.type).toBe(Navigate)
    expect(el?.props).toMatchObject({ to: '/m/data-quality/checks', replace: true })
  })

  it('renders the Quality checks page with its count line at /m/data-quality/checks', async () => {
    wrapAt('/m/data-quality/checks')
    expect(await screen.findByRole('heading', { level: 1, name: 'Quality' })).toBeInTheDocument()
    expect(screen.getByTestId('kpi-active')).toHaveTextContent(/active/)
  })

  it('renders NewCheckWizard at /m/data-quality/checks/new', async () => {
    wrapAt('/m/data-quality/checks/new')
    expect(await screen.findByRole('heading', { level: 1, name: 'New check' })).toBeInTheDocument()
  })

  // UIFPE-683: the Kinds page folded into the New check wizard's first step. Asserted on the
  // route table, not by rendering, so it runs on every Node version (data-router redirects do not locally).
  it('redirects /m/data-quality/kinds to the New check wizard', () => {
    const shell = routes.find(r => r.path === '/')
    const kinds = shell?.children?.find(r => r.path === 'm/data-quality/kinds')
    const el = kinds?.element as ReactElement<{ to: string; replace?: boolean }> | undefined
    expect(el?.type).toBe(Navigate)
    expect(el?.props).toMatchObject({ to: '/m/data-quality/checks/new', replace: true })
  })
})
