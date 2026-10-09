import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Notifications } from '../Notifications'
import type { AlertRuleStatus, AlertSubscriber } from '@/lib/types'

const OPS: AlertSubscriber = { email: 'ops@pearson.com', enabled: true, gold_ids: null, added_by: 'terraform', added_at: '2026-09-23T17:22:54Z' }
const OWNER: AlertSubscriber = { email: 'owner@pearson.com', enabled: false, gold_ids: ['price', 'product'] }

let subscribers: { data?: { items: AlertSubscriber[]; total: number }; error?: unknown; isLoading: boolean } =
  { data: { items: [OPS, OWNER], total: 2 }, isLoading: false }
const rules: { data?: { items: AlertRuleStatus[]; total: number }; error?: unknown; isLoading: boolean } = {
  data: { items: [
    { name: 'uif-echo-dev-gold-product-run-failed', gold_id: 'product', condition: 'run failed', state: 'ALARM', emails: true },
    { name: 'uif-echo-dev-gold-dispatch-silent', gold_id: null, condition: 'dispatch silent', state: 'OK', emails: true },
  ], total: 2 }, isLoading: false,
}
const addMutate = vi.fn(), setMutate = vi.fn(), removeMutate = vi.fn()

vi.mock('@/hooks/useNotifications', () => ({
  useAlertSubscribers:      () => subscribers,
  useAlertRules:            () => rules,
  useAddAlertSubscriber:    () => ({ mutate: addMutate, isPending: false }),
  useSetAlertSubscriber:    () => ({ mutate: setMutate, isPending: false }),
  useRemoveAlertSubscriber: () => ({ mutate: removeMutate, isPending: false }),
}))
vi.mock('@/hooks/useGoldPipelines', () => ({
  useGoldPipelines: () => ({ data: [{ gold_id: 'product' }, { gold_id: 'price' }, { gold_id: 'stock' }] }),
}))

const gateway404 = Object.assign(new Error('Request failed with status code 404'), {
  isAxiosError: true, response: { status: 404, data: { message: 'Not Found' } },
})

function renderPage() {
  const qc = new QueryClient()
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><Notifications /></MemoryRouter>
    </QueryClientProvider>
  )
}

describe('settings/Notifications (UIFPE-543)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    subscribers = { data: { items: [OPS, OWNER], total: 2 }, isLoading: false }
  })

  it('renders the heading and lists every subscriber with its scope', () => {
    renderPage()
    expect(screen.getByRole('heading', { level: 1, name: /notifications/i })).toBeInTheDocument()
    expect(screen.getByRole('tooltip')).toHaveTextContent("Who is emailed when a gold table's alarms fire.")
    expect(screen.getByText('ops@pearson.com')).toBeInTheDocument()
    expect(screen.getByText(/All tables and platform alerts/)).toBeInTheDocument()
    expect(screen.getByText('owner@pearson.com')).toBeInTheDocument()
    expect(screen.getByText(/price, product/)).toBeInTheDocument()
    expect(screen.getByText('2 addresses')).toBeInTheDocument()
  })

  it('the switch pauses and resumes an address', () => {
    renderPage()
    fireEvent.click(screen.getByRole('switch', { name: /emails to owner@pearson.com/i }))
    expect(setMutate).toHaveBeenCalledWith({ email: 'owner@pearson.com', patch: { enabled: true } })
    fireEvent.click(screen.getByRole('switch', { name: /emails to ops@pearson.com/i }))
    expect(setMutate).toHaveBeenCalledWith({ email: 'ops@pearson.com', patch: { enabled: false } })
  })

  it('the bin removes the address', () => {
    renderPage()
    fireEvent.click(screen.getByRole('button', { name: /remove owner@pearson.com/i }))
    expect(removeMutate).toHaveBeenCalledWith('owner@pearson.com')
  })

  it('adding with no table ticked subscribes to everything; ticking tables narrows it', () => {
    renderPage()
    const input = screen.getByLabelText(/email address to add/i)
    fireEvent.change(input, { target: { value: 'New.Person@pearson.com' } })
    fireEvent.click(screen.getByRole('button', { name: /add/i }))
    expect(addMutate).toHaveBeenCalledWith({ email: 'new.person@pearson.com', goldIds: undefined }, expect.anything())

    fireEvent.click(screen.getByLabelText('product'))
    fireEvent.click(screen.getByLabelText('stock'))
    fireEvent.change(input, { target: { value: 'two@pearson.com' } })
    fireEvent.click(screen.getByRole('button', { name: /add/i }))
    expect(addMutate).toHaveBeenLastCalledWith({ email: 'two@pearson.com', goldIds: ['product', 'stock'] }, expect.anything())
  })

  it('refuses a malformed address before calling the API', () => {
    renderPage()
    fireEvent.change(screen.getByLabelText(/email address to add/i), { target: { value: 'nope' } })
    fireEvent.click(screen.getByRole('button', { name: /add/i }))
    expect(addMutate).not.toHaveBeenCalled()
    expect(screen.getByRole('status')).toHaveTextContent(/valid email/i)
  })

  it('shows the rules grouped by table with their live state, fleet last', () => {
    renderPage()
    // 'product' is both a rule group heading and a table checkbox label
    expect(screen.getAllByText('product').length).toBeGreaterThanOrEqual(2)
    expect(screen.getByText('run failed')).toBeInTheDocument()
    // rule states render through the shared StatusBadge vocabulary
    expect(screen.getByText('In alarm')).toBeInTheDocument()
    expect(screen.getByText('OK')).toBeInTheDocument()
    expect(screen.getByText('Platform')).toBeInTheDocument()
    expect(screen.getByText('dispatch silent')).toBeInTheDocument()
    expect(screen.getByText(/read-only, live from CloudWatch/)).toBeInTheDocument()
    // the fleet group renders after the entity groups
    const labels = screen.getAllByText(/^(product|Platform)$/).map(e => e.textContent)
    expect(labels.indexOf('Platform')).toBeGreaterThan(labels.indexOf('product'))
  })

  it('says so when the API is not deployed in this environment', () => {
    subscribers = { error: gateway404, isLoading: false }
    renderPage()
    expect(screen.getByText(/not available in this environment yet/i)).toBeInTheDocument()
    expect(screen.queryByLabelText(/email address to add/i)).toBeNull()
  })
})
