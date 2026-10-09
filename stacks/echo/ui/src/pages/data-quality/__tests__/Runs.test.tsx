import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { Runs } from '../Runs'
import { api } from '@/lib/api'

vi.mock('@/lib/api', () => ({
  api: { dq: { listChecks: vi.fn(), listCheckRuns: vi.fn() } },
}))

const now = new Date().toISOString()

beforeEach(() => {
  vi.mocked(api.dq.listChecks).mockResolvedValue([
    { check_id: 'a', version: 'latest', kind: 'reconcile', enabled: true,
      subject: { layer: 'silver', database: 'd', table: 't' },
      schedule: { frequency: 'daily' }, parameters: {},
      metadata: { created_at: now, owner: 'r' },
      last_run: null } as never,
  ])
  vi.mocked(api.dq.listCheckRuns).mockResolvedValue({
    check_id: 'a',
    runs: [
      { run_id: 'jr_1', status: 'SUCCEEDED', summary: '0 orphans', started_at: now, check_id: 'a' },
      { run_id: 'jr_0', status: 'FAILED',    summary: 'cap exceeded', started_at: now, check_id: 'a' },
    ],
  })
})

function wrap() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<MemoryRouter><QueryClientProvider client={qc}><Runs /></QueryClientProvider></MemoryRouter>)
}

describe('DQ Runs', () => {
  it('renders one row per run across all checks', async () => {
    wrap()
    expect(await screen.findByText('jr_1')).toBeInTheDocument()
    expect(screen.getByText('jr_0')).toBeInTheDocument()
    expect(screen.getByText(/0 orphans/)).toBeInTheDocument()
  })

  it('uses the Quality header and the Started · Check · Kind · Status · Summary columns', async () => {
    wrap()
    expect(screen.getByRole('heading', { level: 1, name: 'Quality' })).toBeInTheDocument()
    await screen.findByText('jr_1')
    for (const h of ['Started', 'Check', 'Kind', 'Status', 'Summary']) {
      expect(screen.getByRole('columnheader', { name: h })).toBeInTheDocument()
    }
    expect(screen.getByText('Passed')).toBeInTheDocument()
    expect(screen.getByText('Failed')).toBeInTheDocument()
    expect(screen.getAllByText('Reconcile')).toHaveLength(2)
  })
})
