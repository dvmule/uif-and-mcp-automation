import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Watermarks } from '../Watermarks'

const resetMutate = vi.fn()

vi.mock('@/hooks/usePipelines', () => ({
  usePipelines: () => ({
    data: [
      { pipeline_id: 'a', enabled: true, schedule: { frequency: 'daily' }, extract: { table: 'INV.A', mode: 'incremental', watermark_col: 'LAST_UPDATE_DATE', watermark_value: '2026-06-12T00:00:00' } },
      { pipeline_id: 'b', enabled: true, schedule: { frequency: 'daily' }, extract: { table: 'INV.B', mode: 'full' } },
    ],
    isLoading: false,
  }),
}))
vi.mock('@/hooks/useResetWatermark', () => ({
  useResetWatermark: () => ({ mutate: resetMutate, isPending: false }),
}))

function r(n: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>{n}</MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('Watermarks', () => {
  beforeEach(() => {
    resetMutate.mockClear()
    if (!window.matchMedia) {
      window.matchMedia = vi.fn().mockReturnValue({
        matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn(),
      }) as unknown as typeof window.matchMedia
    }
  })

  it('uses the Watermarks header and columns', () => {
    r(<Watermarks />)
    expect(screen.getByRole('heading', { level: 1, name: 'Watermarks' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'About Watermarks' })).toBeInTheDocument()
    expect(screen.getByRole('tooltip')).toHaveTextContent('Where each incremental sync is up to.')
    expect(screen.getByText('Where each incremental sync is up to.')).toBeInTheDocument()
    for (const h of ['Sync', 'Column', 'Current value', 'Advanced']) {
      expect(screen.getByRole('columnheader', { name: h })).toBeInTheDocument()
    }
  })

  it('counts syncs, incremental, behind and full load under the table', () => {
    r(<Watermarks />)
    expect(screen.getByTestId('watermarks-total').parentElement).toHaveTextContent(
      '2 syncs · 1 incremental · 1 behind · 1 full load',
    )
  })

  it('gives every row an open link to its sync', () => {
    r(<Watermarks />)
    expect(screen.getByRole('link', { name: 'Open INV.A' })).toHaveAttribute('href', '/m/data-sync/syncs/a')
    expect(screen.getByRole('link', { name: 'Open INV.B' })).toHaveAttribute('href', '/m/data-sync/syncs/b')
  })

  it('keeps Reset… a visible button, not a hover action', () => {
    r(<Watermarks />)
    const reset = within(screen.getByText('INV.A').closest('tr')!).getByRole('button', { name: 'Reset…' })
    expect(reset.closest('[class*="group-hover"]')).toBeNull()
  })

  it('Find narrows the rows by sync name, id or column', async () => {
    const user = userEvent.setup()
    r(<Watermarks />)
    await user.type(screen.getByRole('searchbox', { name: 'Search sync or column' }), 'last_update')
    expect(screen.getByText('INV.A')).toBeInTheDocument()
    expect(screen.queryByText('INV.B')).toBeNull()
    expect(screen.getByTestId('watermarks-total').parentElement).toHaveTextContent('1 sync · 1 incremental · 1 behind · 0 full load · filtered from 2')
  })

  it('shows watermark column + value and flags a watermark past the stale rule as Behind', () => {
    r(<Watermarks />)
    const row = screen.getByText('INV.A').closest('tr')!
    expect(within(row).getByText('LAST_UPDATE_DATE')).toBeInTheDocument()
    expect(within(row).getByText(/2026-06-12T00:00:00/)).toBeInTheDocument()
    expect(within(row).getByText('Behind')).toBeInTheDocument()
  })

  it('shows a full-load sync with a full load badge and no Reset', () => {
    r(<Watermarks />)
    const row = screen.getByText('INV.B').closest('tr')!
    expect(within(row).getByText('full load')).toBeInTheDocument()
    expect(within(row).queryByRole('button', { name: /reset/i })).toBeNull()
  })

  it('Reset… opens the confirm dialog and resets that pipeline', async () => {
    const user = userEvent.setup()
    r(<Watermarks />)
    await user.click(within(screen.getByText('INV.A').closest('tr')!).getByRole('button', { name: 'Reset…' }))
    await user.click(await screen.findByRole('button', { name: 'Reset watermark' }))
    expect(resetMutate).toHaveBeenCalledWith('a')
  })
})
