import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { Checks } from '../Checks'
import { api } from '@/lib/api'

vi.mock('@/lib/api', () => ({
  api: { dq: { listChecks: vi.fn(), listKinds: vi.fn(), triggerRun: vi.fn() } },
}))

function LocationProbe() {
  const loc = useLocation()
  return <div data-testid="location">{loc.pathname}{loc.search}</div>
}

function wrap(ui: React.ReactNode, path = '/m/data-quality/checks') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <MemoryRouter initialEntries={[path]}>
      <QueryClientProvider client={qc}>{ui}</QueryClientProvider>
      <LocationProbe />
    </MemoryRouter>
  )
}

const now = new Date().toISOString()

const sampleChecks = [
  {
    check_id: 'reconcile-fipm', version: 'latest', kind: 'reconcile', enabled: true,
    subject:  { layer: 'silver', database: 'silver_fipm', table: 'deal_roy_sku' },
    schedule: { frequency: 'daily' },
    parameters: {},
    metadata: { created_at: '2026-06-13T00:00:00Z', owner: 'r' },
    last_run: { ts: now, status: 'SUCCEEDED', summary: '0 orphans', run_id: 'jr_1' },
  },
  {
    check_id: 'null-rate-bar', version: 'latest', kind: 'null-rate', enabled: false,
    subject:  { layer: 'silver', database: 'silver_foo', table: 'bar' },
    schedule: { frequency: 'hourly' },
    parameters: { column: 'C', threshold_pct: 0.1 },
    metadata: { created_at: '2026-06-12T00:00:00Z', owner: 'r' },
    last_run: { ts: now, status: 'FAILED', summary: 'ratio 0.3' },
  },
]

describe('Checks page', () => {
  beforeEach(() => {
    vi.mocked(api.dq.listChecks).mockResolvedValue(sampleChecks as never)
    vi.mocked(api.dq.listKinds).mockResolvedValue({ kinds: [] })
    vi.mocked(api.dq.triggerRun).mockResolvedValue({} as never)
  })

  it('renders the Quality header and the expected column headers', async () => {
    wrap(<Checks />)
    expect(screen.getByRole('heading', { level: 1, name: 'Quality' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'New check' })).toHaveAttribute('href', '/m/data-quality/checks/new')
    await screen.findByText('reconcile-fipm')
    for (const h of ['Check', 'Kind', 'Subject', 'Schedule', 'Last run', 'Status']) {
      expect(screen.getByRole('columnheader', { name: h })).toBeInTheDocument()
    }
  })

  it('renders one row per check with layer · table subject, kind label and status', async () => {
    wrap(<Checks />)
    expect(await screen.findByText('reconcile-fipm')).toBeInTheDocument()
    expect(screen.getByText('null-rate-bar')).toBeInTheDocument()
    expect(screen.getByText('silver · silver_fipm.deal_roy_sku')).toBeInTheDocument()
    const first = screen.getByText('reconcile-fipm').closest('tr')!
    expect(within(first).getByText('Reconcile')).toBeInTheDocument()
    expect(within(first).getByText('Passed')).toBeInTheDocument()
    expect(within(first).getByText('0 orphans')).toBeInTheDocument()
    // a disabled check says Disabled whatever its last run was
    expect(within(screen.getByText('null-rate-bar').closest('tr')!).getByText('Disabled')).toBeInTheDocument()
  })

  it('shows the Overview figures in the count line, counted from the checks', async () => {
    wrap(<Checks />)
    await waitFor(() => expect(screen.getByTestId('kpi-total')).toHaveTextContent('2 checks'))
    expect(screen.getByTestId('kpi-active')).toHaveTextContent('1 active')
    expect(screen.getByTestId('kpi-pass')).toHaveTextContent('1 passed')
    expect(screen.getByTestId('kpi-fail')).toHaveTextContent('1 failed')
    expect(screen.getByTestId('kpi-warn')).toHaveTextContent('0 warning in 24 h')
    expect(screen.getByTestId('kpi-total').parentElement).toHaveTextContent(
      '2 checks · 1 active · 1 passed · 1 failed · 0 warning in 24 h',
    )
  })

  it('narrows the table by a URL-backed facet', async () => {
    wrap(<Checks />, '/m/data-quality/checks?kind=null-rate')
    expect(await screen.findByText('null-rate-bar')).toBeInTheDocument()
    expect(screen.queryByText('reconcile-fipm')).toBeNull()
    expect(screen.getByRole('button', { name: 'Filter by kind' })).toHaveTextContent('Null rate')
  })

  it('lists every known kind, all ticked with no kind in the URL, and a pick writes it', async () => {
    wrap(<Checks />)
    await screen.findByText('reconcile-fipm')
    const kind = screen.getByRole('button', { name: 'Filter by kind' })
    expect(kind).toHaveTextContent('All kinds (7)')
    await userEvent.setup().click(kind)
    await userEvent.setup().click(screen.getByRole('option', { name: /^Reconcile/ }))
    expect(screen.getByTestId('location').textContent).toBe('/m/data-quality/checks?kind=reconcile')
    expect(screen.queryByText('null-rate-bar')).toBeNull()
    await userEvent.setup().click(screen.getByRole('button', { name: /Select all/ }))
    expect(screen.getByTestId('location').textContent).toBe('/m/data-quality/checks')
  })

  it('Run now triggers the check, and the open icon goes to it', async () => {
    wrap(<Checks />)
    const row = (await screen.findByText('reconcile-fipm')).closest('tr')!
    expect(within(row).getByRole('link', { name: 'Open reconcile-fipm' })).toHaveAttribute(
      'href', '/m/data-quality/checks/reconcile-fipm',
    )
    await userEvent.setup().click(within(row).getByRole('button', { name: 'Run now' }))
    await waitFor(() => expect(api.dq.triggerRun).toHaveBeenCalledWith('reconcile-fipm'))
  })

  it('shows "no checks yet" empty state when list is empty', async () => {
    vi.mocked(api.dq.listChecks).mockResolvedValue([])
    wrap(<Checks />)
    expect(await screen.findByText(/no data quality checks/i)).toBeInTheDocument()
  })
})
