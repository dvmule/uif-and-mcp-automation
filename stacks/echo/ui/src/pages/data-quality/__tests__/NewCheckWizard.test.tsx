import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { NewCheckWizard } from '../NewCheckWizard'
import { api } from '@/lib/api'

vi.mock('@/lib/api', () => ({
  api: { dq: { upsertCheck: vi.fn(), listKinds: vi.fn() } },
}))

function wrap(path = '/m/data-quality/checks/new') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <MemoryRouter initialEntries={[path]}>
      <QueryClientProvider client={qc}>
        <NewCheckWizard />
      </QueryClientProvider>
    </MemoryRouter>
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.dq.listKinds).mockResolvedValue({ kinds: [
    { kind: 'reconcile', status: 'live',        parameters: [] },
    { kind: 'null-rate', status: 'live',        parameters: [] },
    { kind: 'drift',     status: 'coming-soon', parameters: [] },
  ]})
})

describe('NewCheckWizard', () => {
  it('has the shared header with a breadcrumb back to Quality', async () => {
    wrap()
    expect(screen.getByRole('heading', { level: 1, name: 'New check' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Quality' })).toHaveAttribute('href', '/m/data-quality/checks')
    expect(screen.getByText('Step 1 of 4')).toBeInTheDocument()
  })

  it('step 1 lists every kind as a card; live ones are selectable, planned ones say Coming soon', async () => {
    wrap()
    expect(await screen.findByText('What kind of check?')).toBeInTheDocument()
    expect(await screen.findByText('2 run today; the rest are planned')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Reconcile/ })).toBeEnabled()
    expect(screen.getByRole('button', { name: /Null rate/ })).toBeEnabled()
    const drift = screen.getByRole('button', { name: /Drift/ })
    expect(drift).toBeDisabled()
    expect(drift).toHaveTextContent('Coming soon')
  })

  it('a coming-soon kind cannot be picked, so Next asks for a kind', async () => {
    wrap()
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: /Drift/ }))
    await user.click(screen.getByRole('button', { name: 'Next: Subject →' }))
    expect(await screen.findByText('Pick a kind to continue')).toBeInTheDocument()
    expect(screen.getByText('Step 1 of 4')).toBeInTheDocument()
  })

  it('advances through all four steps for null-rate', async () => {
    wrap()
    const user = userEvent.setup()
    // step 1
    await user.click(await screen.findByRole('button', { name: /Null rate/ }))
    await user.click(screen.getByRole('button', { name: /next/i }))
    // step 2 — subject
    expect(await screen.findByLabelText(/database/i)).toBeInTheDocument()
    await user.type(screen.getByLabelText(/database/i), 'silver_x')
    await user.type(screen.getByLabelText(/table/i),    't_x')
    await user.click(screen.getByRole('button', { name: /next/i }))
    // step 3 — parameters (null-rate)
    expect(await screen.findByLabelText(/column/i)).toBeInTheDocument()
    await user.type(screen.getByLabelText(/column/i), 'C')
    await user.clear(screen.getByLabelText(/threshold/i))
    await user.type(screen.getByLabelText(/threshold/i), '0.05')
    await user.click(screen.getByRole('button', { name: /next/i }))
    // step 4 — schedule; earlier steps show their answers
    expect(await screen.findByLabelText(/frequency/i)).toBeInTheDocument()
    expect(screen.getByText('silver_x.t_x')).toBeInTheDocument()
    expect(screen.getByText('C · max 5% nulls')).toBeInTheDocument()
    vi.mocked(api.dq.upsertCheck).mockResolvedValue({} as never)
    await user.click(screen.getByRole('button', { name: /create check/i }))
    expect(api.dq.upsertCheck).toHaveBeenCalledTimes(1)
    const [checkId, payload] = vi.mocked(api.dq.upsertCheck).mock.calls[0]
    expect(checkId).toMatch(/null-rate-silver_x-t_x/)
    expect(payload.kind).toBe('null-rate')
    expect(payload.parameters).toMatchObject({ column: 'C', threshold_pct: 0.05 })
  })

  it('?subject= prefills step 2 and shows it in the stepper hint', async () => {
    wrap('/m/data-quality/checks/new?subject=silver.echo_dev_silver_price.price_list')
    expect(screen.getByText('prefilled: echo_dev_silver_price.price_list')).toBeInTheDocument()
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: /Reconcile/ }))
    await user.click(screen.getByRole('button', { name: 'Next: Subject →' }))
    expect(screen.getByLabelText(/database/i)).toHaveValue('echo_dev_silver_price')
    expect(screen.getByLabelText(/table/i)).toHaveValue('price_list')
    expect(screen.getByLabelText(/layer/i)).toHaveValue('silver')
  })

  it('ignores a malformed ?subject=', () => {
    wrap('/m/data-quality/checks/new?subject=not-a-table')
    expect(screen.queryByText(/prefilled:/)).toBeNull()
  })
})
