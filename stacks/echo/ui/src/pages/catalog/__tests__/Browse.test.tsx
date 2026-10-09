import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Browse } from '../Browse'
import { api } from '@/lib/api'
import type { GoldPipelineConfig, PipelineConfig } from '@/lib/types'

vi.mock('@/lib/api', () => ({ api: { getPipelines: vi.fn(), getGoldPipelines: vi.fn(), getRuns: vi.fn() } }))

function renderBrowseAt(initialPath = '/m/catalog/browse', qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })) {
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[initialPath]}>
        <Browse />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

const FAKE: PipelineConfig[] = [
  {
    pipeline_id: 'smoke_author',
    version: 'latest',
    enabled: true,
    destination: { cloud: 'gcp' },
    source: { secret_arn: 'arn:x' },
    extract: { table: 'AUTHOR.AUTHOR_DATA', mode: 'incremental' },
    target: { gcs_path: 'gs://b/' },
    glue: { worker_type: 'G.1X', num_workers: 2, timeout_minutes: 60 },
    schedule: { frequency: 'daily' },
    metadata: { domain: 'Product' },
    silver: {
      enabled: true,
      bq_dataset: 'smoke_silver',
      bq_project: 'p',
      table: 'author_data',
      primary_key_cols: ['AUTHOR_ID'],
    },
  },
]

const GOLD: GoldPipelineConfig[] = [
  { gold_id: 'product', enabled: true, domain: 'Product', target: { database: 'uif_echo_{env}_gold_product', table: 'product' } },
]

describe('Browse', () => {
  beforeEach(() => {
    vi.mocked(api.getPipelines).mockResolvedValue(FAKE)
    vi.mocked(api.getGoldPipelines).mockResolvedValue(GOLD)
    vi.mocked(api.getRuns).mockClear()
  })

  it('defaults to silver when ?layer= is absent', async () => {
    renderBrowseAt('/m/catalog/browse')
    await waitFor(() => expect(screen.getByText('author_data')).toBeInTheDocument())
    // Silver button is the active one
    const silver = screen.getByRole('button', { name: /silver/i })
    expect(silver).toHaveAttribute('aria-pressed', 'true')
  })

  it('respects ?layer=bronze from URL', async () => {
    renderBrowseAt('/m/catalog/browse?layer=bronze')
    await waitFor(() => {
      const bronze = screen.getByRole('button', { name: /bronze/i })
      expect(bronze).toHaveAttribute('aria-pressed', 'true')
    })
    // Bronze row shows the Oracle schema as database
    await waitFor(() => expect(screen.getByText('author')).toBeInTheDocument())
  })

  it('updates ?layer= when LayerSelector clicks', async () => {
    const user = userEvent.setup()
    renderBrowseAt('/m/catalog/browse')
    await waitFor(() => screen.getByText('author_data'))
    await user.click(screen.getByRole('button', { name: /bronze/i }))
    // After click, bronze is active and the URL would have ?layer=bronze
    // (we can't easily read it via MemoryRouter, so just assert the UI state)
    await waitFor(() => {
      const bronze = screen.getByRole('button', { name: /bronze/i })
      expect(bronze).toHaveAttribute('aria-pressed', 'true')
    })
  })

  it('shows the Catalog header and a row with domain, cloud and the sync that feeds it', async () => {
    renderBrowseAt('/m/catalog/browse')
    expect(screen.getByRole('heading', { level: 1, name: 'Catalog' })).toBeInTheDocument()
    const row = (await screen.findByText('author_data')).closest('tr')!
    expect(within(row).getByRole('link', { name: 'author_data' })).toHaveAttribute(
      'href', '/m/catalog/datasets/silver/gcp/smoke_silver/author_data',
    )
    expect(within(row).getByText('Product')).toBeInTheDocument()
    expect(within(row).getByText('GCP')).toBeInTheDocument()
    expect(within(row).getByRole('link', { name: 'AUTHOR.AUTHOR_DATA' })).toHaveAttribute('href', '/m/data-sync/syncs/smoke_author')
    expect(within(row).getByRole('link', { name: 'Open author_data' })).toHaveAttribute(
      'href', '/m/catalog/datasets/silver/gcp/smoke_silver/author_data',
    )
    expect(screen.getByTestId('catalog-total').parentElement).toHaveTextContent('1 table in silver')
    // no runs in the cache: freshness is unknown, and the page never fetches them
    await waitFor(() => expect(within(row).getAllByText('—').length).toBeGreaterThan(0))
    expect(api.getRuns).not.toHaveBeenCalled()
  })

  it('reads freshness from runs already in the shared cache', async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    qc.setQueryData(['runs', 'smoke_author'], {
      items: [{ job_name: 'echo-dev-bronze-ingest', run_id: 'r1', pipeline_id: 'smoke_author', status: 'SUCCEEDED',
                started_at: new Date().toISOString(), completed_at: new Date().toISOString() }],
    })
    renderBrowseAt('/m/catalog/browse', qc)
    const row = (await screen.findByText('author_data')).closest('tr')!
    expect(await within(row).findByText('Fresh')).toBeInTheDocument()
    expect(api.getRuns).not.toHaveBeenCalled()
  })

  it('a gold row is fed by its gold entity', async () => {
    renderBrowseAt('/m/catalog/browse?layer=gold')
    await waitFor(() => expect(screen.getAllByRole('link', { name: 'product' })).toHaveLength(2))
    const hrefs = screen.getAllByRole('link', { name: 'product' }).map(a => a.getAttribute('href'))
    expect(hrefs).toContain('/m/data-sync/gold/product')
    expect(hrefs.some(h => h?.startsWith('/m/catalog/datasets/gold/aws/'))).toBe(true)
  })

  it('filters by a URL-backed domain facet', async () => {
    renderBrowseAt('/m/catalog/browse?domain=Finance')
    await waitFor(() => expect(screen.getByText('No datasets match these filters')).toBeInTheDocument())
    expect(screen.getByRole('button', { name: 'Filter by domain' })).toHaveTextContent('Finance')
  })

  it('shows every domain and cloud with none in the URL, and a cloud pick narrows the rows', async () => {
    const user = userEvent.setup()
    renderBrowseAt('/m/catalog/browse')
    await screen.findByText('author_data')
    expect(screen.getByRole('searchbox', { name: 'Search table' })).toHaveAttribute('placeholder', 'Table')
    expect(screen.getByRole('button', { name: 'Filter by domain' })).toHaveTextContent('Product')
    const cloud = screen.getByRole('button', { name: 'Filter by cloud' })
    expect(cloud).toHaveTextContent('All clouds (2)')
    await user.click(cloud)
    await user.click(screen.getByRole('option', { name: /^AWS/ }))
    expect(cloud).toHaveTextContent('AWS')
    expect(screen.getByText('No datasets match these filters')).toBeInTheDocument()
  })
})
