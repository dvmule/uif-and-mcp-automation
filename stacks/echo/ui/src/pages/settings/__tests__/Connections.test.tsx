import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Connections } from '../Connections'

vi.mock('@/hooks/useConnections', () => ({
  useConnections: () => ({
    data: [{ arn: 'arn:aws:secretsmanager:us-east-1:1:secret:ora-1', name: 'oracle-prod', description: 'PRD' }],
    isLoading: false,
  }),
}))
vi.mock('@/hooks/useGcsConnections', () => ({
  useGcsConnections: () => ({
    data: [{ arn: 'arn:aws:secretsmanager:us-east-1:1:secret:gcs-1', name: 'gcs-landing' }],
    isLoading: false,
  }),
}))
vi.mock('@/hooks/useSilverBqConnections', () => ({
  useSilverBqConnections: () => ({ data: [], isLoading: false }),
}))
vi.mock('@/hooks/usePipelines', () => ({
  usePipelines: () => ({ data: [] }),
}))

function renderConn(path = '/') {
  const qc = new QueryClient()
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}><Connections /></MemoryRouter>
    </QueryClientProvider>
  )
}

const row = (name: string) => screen.getByText(name).closest('tr')!

describe('settings/Connections', () => {
  beforeEach(() => vi.clearAllMocks())

  it('renders the page heading and its description behind the info button', () => {
    renderConn()
    expect(screen.getByRole('heading', { level: 1, name: /connections/i })).toBeInTheDocument()
    expect(screen.getByRole('tooltip')).toHaveTextContent('Secrets the platform can read from. Adding one is a CLI step today.')
  })

  it('lists the Oracle connection from the mocked hook', () => {
    renderConn()
    expect(screen.getByText('oracle-prod')).toBeInTheDocument()
  })

  it('offers a Type filter with every type shown by default', () => {
    renderConn()
    expect(screen.getByRole('button', { name: 'Filter by type' })).toHaveTextContent('All types (3)')
    expect(screen.getByText('gcs-landing')).toBeInTheDocument()
  })

  it('shows the ARN tail, a Copy ARN button and how many syncs use the secret', () => {
    renderConn()
    expect(screen.getByText('…secret:ora-1')).toHaveAttribute('title', 'arn:aws:secretsmanager:us-east-1:1:secret:ora-1')
    expect(within(row('oracle-prod')).getByRole('button', { name: /copy arn/i })).toBeInTheDocument()
    expect(within(row('oracle-prod')).getByText('0 syncs')).toBeInTheDocument()
    expect(within(row('oracle-prod')).getByText('Oracle')).toBeInTheDocument()
  })

  it('counts connections by type under the table', () => {
    renderConn()
    expect(screen.getByTestId('connections-total').parentElement).toHaveTextContent(
      '2 connections · 1 Oracle · 1 GCS · 0 Silver → BigQuery · 0 in use',
    )
  })

  it('?type=gcs narrows to the GCS rows', () => {
    renderConn('/?type=gcs')
    expect(screen.getByText('gcs-landing')).toBeInTheDocument()
    expect(screen.queryByText('oracle-prod')).toBeNull()
    expect(screen.getByRole('button', { name: 'Filter by type' })).toHaveTextContent('GCS')
    expect(screen.getByTestId('connections-total').parentElement).toHaveTextContent(
      '1 connection · 0 Oracle · 1 GCS · 0 Silver → BigQuery · 0 in use · filtered from 2',
    )
  })

  it('"How to add a connection" opens the CLI snippet', async () => {
    renderConn()
    await userEvent.setup().click(screen.getByRole('button', { name: 'How to add a connection' }))
    expect(await screen.findByText(/register_secret\.py --name/)).toBeInTheDocument()
  })
})
