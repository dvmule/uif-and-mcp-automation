import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Sources } from '../Sources'

const DEMO = 'arn:aws:secretsmanager:eu-west-1:0:secret:oracle-demo-AbC123'
const OTHER = 'arn:aws:secretsmanager:eu-west-1:0:secret:oracle-other-XyZ789'

vi.mock('@/hooks/usePipelines', () => ({
  usePipelines: () => ({
    data: [
      { pipeline_id: 'a', enabled: true, source: { secret_arn: DEMO }, extract: { table: 'INV.A', mode: 'full' } },
      { pipeline_id: 'b', enabled: true, source: { secret_arn: DEMO }, extract: { table: 'INV.B', mode: 'full' } },
      { pipeline_id: 'c', enabled: true, source: { secret_arn: OTHER }, extract: { table: 'INV.C', mode: 'full' } },
    ],
    isLoading: false,
  }),
}))

function r(n: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>{n}</MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('Sources', () => {
  beforeEach(() => {
    if (!window.matchMedia) {
      window.matchMedia = vi.fn().mockReturnValue({
        matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn(),
      }) as unknown as typeof window.matchMedia
    }
  })

  it('uses the Sources header and points at Settings › Connections', () => {
    r(<Sources />)
    expect(screen.getByRole('heading', { level: 1, name: 'Sources' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /manage in settings › connections/i })).toHaveAttribute('href', '/settings/connections')
    expect(screen.getByRole('tooltip')).toHaveTextContent('Which Oracle secret feeds which syncs.')
  })

  it('counts sources and syncs under the table', () => {
    r(<Sources />)
    expect(screen.getByTestId('sources-total').parentElement).toHaveTextContent('2 sources · 3 syncs')
  })

  it('Find narrows the sources by a table name', async () => {
    const user = userEvent.setup()
    r(<Sources />)
    await user.type(screen.getByRole('searchbox', { name: 'Search source or table' }), 'inv.c')
    expect(screen.getByText('oracle-other')).toBeInTheDocument()
    expect(screen.queryByText('oracle-demo')).toBeNull()
    expect(screen.getByTestId('sources-total').parentElement).toHaveTextContent('1 source · 1 sync · filtered from 2')
  })

  it('groups syncs by source secret with the ARN tail and the full ARN on hover', () => {
    r(<Sources />)
    expect(screen.getByText('oracle-demo')).toBeInTheDocument()
    expect(screen.getByText('oracle-other')).toBeInTheDocument()
    expect(screen.getByText('…secret:oracle-demo-AbC123')).toHaveAttribute('title', DEMO)
  })

  it('shows the sync count and links each table to its sync', () => {
    r(<Sources />)
    const demoRow = screen.getByText('oracle-demo').closest('tr')!
    expect(within(demoRow).getByText('2')).toBeInTheDocument()
    expect(within(demoRow).getByRole('link', { name: 'INV.A' })).toHaveAttribute('href', '/m/data-sync/syncs/a')
    expect(within(demoRow).getByRole('link', { name: 'INV.B' })).toHaveAttribute('href', '/m/data-sync/syncs/b')
    const otherRow = screen.getByText('oracle-other').closest('tr')!
    expect(within(otherRow).getByText('1')).toBeInTheDocument()
  })
})
