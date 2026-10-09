import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { GoldNotifyCard } from '../gold/GoldNotifyCard'
import type { AlertSubscriber } from '@/lib/types'

let subscribers: { data?: { items: AlertSubscriber[]; total: number }; error?: unknown; isLoading: boolean } =
  { data: { items: [], total: 0 }, isLoading: false }
const addMutate = vi.fn(), setMutate = vi.fn(), removeMutate = vi.fn()

vi.mock('@/hooks/useNotifications', () => ({
  useAlertSubscribers:      () => subscribers,
  useAddAlertSubscriber:    () => ({ mutate: addMutate, isPending: false }),
  useSetAlertSubscriber:    () => ({ mutate: setMutate, isPending: false }),
  useRemoveAlertSubscriber: () => ({ mutate: removeMutate, isPending: false }),
}))

const gateway404 = Object.assign(new Error('Request failed with status code 404'), {
  isAxiosError: true, response: { status: 404, data: { message: 'Not Found' } },
})

function renderCard(goldId = 'product') {
  const qc = new QueryClient()
  return render(<QueryClientProvider client={qc}><GoldNotifyCard goldId={goldId} /></QueryClientProvider>)
}

function withRows(...items: AlertSubscriber[]) {
  subscribers = { data: { items, total: items.length }, isLoading: false }
}

describe('GoldNotifyCard (UIFPE-543 self-service)', () => {
  beforeEach(() => { vi.clearAllMocks(); withRows() })

  it('subscribes the typed address to THIS table only', () => {
    renderCard('price')
    fireEvent.change(screen.getByLabelText(/your email address/i), { target: { value: 'Me@Pearson.com' } })
    fireEvent.click(screen.getByRole('button', { name: /notify me/i }))
    expect(addMutate).toHaveBeenCalledWith({ email: 'me@pearson.com', goldIds: ['price'] }, expect.anything())
  })

  it('refuses a malformed address before calling the API', () => {
    renderCard()
    fireEvent.change(screen.getByLabelText(/your email address/i), { target: { value: 'me@' } })
    fireEvent.click(screen.getByRole('button', { name: /notify me/i }))
    expect(addMutate).not.toHaveBeenCalled()
    expect(screen.getByRole('status')).toHaveTextContent(/valid email/i)
  })

  it('counts who hears about this table: filtered rows for it and the all-tables row, never a disabled one', () => {
    withRows(
      { email: 'ops@pearson.com', enabled: true, gold_ids: null },
      { email: 'me@pearson.com', enabled: true, gold_ids: ['product'] },
      { email: 'pricing@pearson.com', enabled: true, gold_ids: ['price'] },
      { email: 'paused@pearson.com', enabled: false, gold_ids: ['product'] },
    )
    renderCard('product')
    expect(screen.getByText(/emailed about this table \(2\)/i)).toBeInTheDocument()
    expect(screen.getByText('ops@pearson.com')).toBeInTheDocument()
    expect(screen.getByText('me@pearson.com')).toBeInTheDocument()
    expect(screen.queryByText('pricing@pearson.com')).toBeNull()
    expect(screen.queryByText('paused@pearson.com')).toBeNull()
  })

  it('an address already on this table sees "Stop notifying me"; its last table removes the row', () => {
    withRows({ email: 'me@pearson.com', enabled: true, gold_ids: ['product'] })
    renderCard('product')
    fireEvent.change(screen.getByLabelText(/your email address/i), { target: { value: 'me@pearson.com' } })
    fireEvent.click(screen.getByRole('button', { name: /stop notifying me/i }))
    expect(removeMutate).toHaveBeenCalledWith('me@pearson.com', expect.anything())
    expect(setMutate).not.toHaveBeenCalled()
  })

  it('with other tables left, only this table is removed from the set', () => {
    withRows({ email: 'me@pearson.com', enabled: true, gold_ids: ['price', 'product'] })
    renderCard('product')
    fireEvent.change(screen.getByLabelText(/your email address/i), { target: { value: 'me@pearson.com' } })
    fireEvent.click(screen.getByRole('button', { name: /stop notifying me/i }))
    expect(setMutate).toHaveBeenCalledWith({ email: 'me@pearson.com', patch: { remove_gold_ids: ['product'] } }, expect.anything())
    expect(removeMutate).not.toHaveBeenCalled()
  })

  it('an all-tables row is managed from Settings, not narrowed here', () => {
    withRows({ email: 'ops@pearson.com', enabled: true, gold_ids: null })
    renderCard('product')
    fireEvent.change(screen.getByLabelText(/your email address/i), { target: { value: 'ops@pearson.com' } })
    fireEvent.click(screen.getByRole('button', { name: /stop notifying me/i }))
    expect(removeMutate).not.toHaveBeenCalled()
    expect(setMutate).not.toHaveBeenCalled()
    expect(screen.getByRole('status')).toHaveTextContent(/Settings/)
  })

  it('says so when the API is not deployed in this environment', () => {
    subscribers = { error: gateway404, isLoading: false }
    renderCard()
    expect(screen.getByText(/not available in this environment yet/i)).toBeInTheDocument()
    expect(screen.queryByLabelText(/your email address/i)).toBeNull()
  })
})
