import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { CheckDetail } from '../CheckDetail'
import { api } from '@/lib/api'

vi.mock('@/lib/api', () => ({
  api: { dq: { getCheck: vi.fn(), listCheckRuns: vi.fn(), triggerRun: vi.fn() }, getPipelines: vi.fn() },
}))

const check = {
  check_id: 'reconcile-x', version: 'latest', kind: 'reconcile', enabled: true,
  subject:  { layer: 'silver', database: 'silver_d', table: 't' },
  schedule: { frequency: 'daily' },
  parameters: { pk_cols: ['ID'], max_deletes_per_run: 5000, dry_run: true,
                 oracle_secret_arn: 'arn:aws:secretsmanager:eu-west-1:0:secret:x',
                 jdbc_url: 'jdbc:oracle:thin:@//h:1521/s' },
  metadata: { created_at: '2026-06-13T00:00:00Z', owner: 'raj@pearson.com' },
  last_run: null,
}

function wrap(checkId: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <MemoryRouter initialEntries={[`/m/data-quality/checks/${checkId}`]}>
      <QueryClientProvider client={qc}>
        <Routes>
          <Route path="/m/data-quality/checks/:checkId" element={<CheckDetail />} />
        </Routes>
      </QueryClientProvider>
    </MemoryRouter>
  )
}

describe('CheckDetail', () => {
  beforeEach(() => {
    vi.mocked(api.dq.getCheck).mockResolvedValue(check as never)
    vi.mocked(api.dq.listCheckRuns).mockResolvedValue({ check_id: 'reconcile-x', runs: [] })
    vi.mocked(api.getPipelines).mockResolvedValue([
      { pipeline_id: 'sync-t', enabled: true, extract: { table: 'SRC.T', mode: 'full' },
        silver: { enabled: true, database: 'silver_d', table: 't', primary_key_cols: [] } } as never,
    ])
  })

  it('shows the Quality back link, kind and layer pills, and the table · schedule meta line', async () => {
    wrap('reconcile-x')
    const headings = await screen.findAllByRole('heading', { level: 1 })
    expect(screen.getByRole('link', { name: 'Quality' })).toHaveAttribute('href', '/m/data-quality/checks')
    expect(headings).toHaveLength(1)
    const h1 = headings[0]
    expect(h1).toHaveTextContent('reconcile-x')
    expect(within(h1).getByText('Reconcile')).toBeInTheDocument()
    expect(within(h1).getByText('silver')).toBeInTheDocument()
    expect(screen.getByText(/silver_d\.t · daily · never ran/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Run now' })).toBeInTheDocument()
  })

  it('Linked sync tab links the sync whose silver table is the subject', async () => {
    wrap('reconcile-x')
    const user = userEvent.setup()
    await user.click(await screen.findByRole('tab', { name: /linked sync/i }))
    expect(await screen.findByRole('link', { name: 'SRC.T' })).toHaveAttribute('href', '/m/data-sync/syncs/sync-t')
  })

  it('Linked sync tab says so when no sync writes the table', async () => {
    vi.mocked(api.getPipelines).mockResolvedValue([])
    wrap('reconcile-x')
    const user = userEvent.setup()
    await user.click(await screen.findByRole('tab', { name: /linked sync/i }))
    expect(await screen.findByText('No sync writes this table.')).toBeInTheDocument()
  })

  it('renders tab list with Overview, Runs, Parameters, Linked sync', async () => {
    wrap('reconcile-x')
    expect(await screen.findByRole('tab', { name: /overview/i })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: /runs/i })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: /parameters/i })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: /linked sync/i })).toBeInTheDocument()
  })

  it('renders the check id in the heading', async () => {
    wrap('reconcile-x')
    expect(await screen.findByRole('heading', { name: /reconcile-x/i })).toBeInTheDocument()
  })

  it('Parameters tab shows kind-specific keys', async () => {
    wrap('reconcile-x')
    const user = userEvent.setup()
    await user.click(await screen.findByRole('tab', { name: /parameters/i }))
    expect(await screen.findByText(/pk_cols/i)).toBeInTheDocument()
    expect(screen.getByText(/dry_run/i)).toBeInTheDocument()
  })
})
