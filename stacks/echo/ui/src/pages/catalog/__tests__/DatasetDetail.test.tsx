import { describe, it, expect, vi } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { DatasetDetail } from '../DatasetDetail'
import { api } from '@/lib/api'
import type { PipelineConfig } from '@/lib/types'

vi.mock('@/lib/api', () => ({
  api: {
    getPipelines: vi.fn(),
    analyzeTable: vi.fn(),
    getLineage: vi.fn(),
    getGoldPipelines: vi.fn().mockResolvedValue([
      { gold_id: 'authors', enabled: true, sources: [{ alias: 'a', silver_table: 'smoke_silver.author_data' }] },
    ]),
  },
}))

const PIPELINE: PipelineConfig = {
  pipeline_id: 'smoke_author',
  version: 'latest',
  enabled: true,
  destination: { cloud: 'gcp' },
  source: { secret_arn: 'arn:aws:secretsmanager:eu-west-1:0:secret:src' },
  extract: { table: 'AUTHOR.AUTHOR_DATA', mode: 'incremental' },
  target: { gcs_path: 'gs://b/' },
  glue: { worker_type: 'G.1X', num_workers: 2, timeout_minutes: 60 },
  schedule: { frequency: 'daily' },
  silver: {
    enabled: true,
    bq_project: 'p',
    bq_dataset: 'smoke_silver',
    table: 'author_data',
    primary_key_cols: ['AUTHOR_ID'],
  },
}

function renderDetail(initialPath = '/m/catalog/datasets/silver/gcp/smoke_silver/author_data') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[initialPath]}>
        <Routes>
          <Route
            path="/m/catalog/datasets/:layer/:cloud/:database/:table"
            element={<DatasetDetail />}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('DatasetDetail', () => {
  it('renders 4-part dataset ref in header and switches to Lineage tab', async () => {
    vi.mocked(api.getPipelines).mockResolvedValue([PIPELINE])
    vi.mocked(api.analyzeTable).mockResolvedValue({
      table: 'AUTHOR.AUTHOR_DATA',
      row_count: 0,
      columns: [{ name: 'AUTHOR_ID', data_type: 'NUMBER', nullable: false }],
    })
    vi.mocked(api.getLineage).mockResolvedValue({
      dataset: 'silver.gcp.smoke_silver.author_data',
      upstream: { sync_id: 'smoke_author', sync_name: 'smoke_author' },
      downstream_exports: [],
      downstream_checks: [],
    })

    renderDetail()

    // Header shows the 4-part dataset reference
    await waitFor(() =>
      expect(screen.getByText('silver.gcp.smoke_silver.author_data')).toBeInTheDocument(),
    )

    // Default Overview tab visible
    expect(screen.getByRole('tab', { name: /overview/i })).toHaveAttribute(
      'aria-selected',
      'true',
    )

    // Switch to Lineage
    await userEvent.setup().click(screen.getByRole('tab', { name: /lineage/i }))
    await waitFor(() => expect(screen.getByText(/produced by/i)).toBeInTheDocument())
    expect(screen.getByText('smoke_author')).toBeInTheDocument()
  })

  it('header: Catalog · Layer back link, database/layer/cloud/format pills, fed by / read by meta and Open sync', async () => {
    vi.mocked(api.getPipelines).mockResolvedValue([PIPELINE])
    vi.mocked(api.analyzeTable).mockResolvedValue({ table: 'x', row_count: 0, columns: [] })
    vi.mocked(api.getLineage).mockResolvedValue({
      dataset: 'silver.gcp.smoke_silver.author_data', upstream: null, downstream_exports: [], downstream_checks: [],
    })

    renderDetail()
    expect(screen.getByRole('link', { name: 'Catalog · Silver' })).toHaveAttribute('href', '/m/catalog/browse?layer=silver')
    const headings = screen.getAllByRole('heading', { level: 1 })
    expect(headings).toHaveLength(1)
    const h1 = headings[0]
    expect(h1).toHaveTextContent('author_data')
    for (const pill of ['smoke_silver', 'silver', 'GCP', 'Iceberg']) {
      expect(within(h1).getByText(pill)).toBeInTheDocument()
    }
    expect(await screen.findByText(/fed by AUTHOR\.AUTHOR_DATA · read by authors/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Open sync' })).toHaveAttribute('href', '/m/data-sync/syncs/smoke_author')
  })

  it('Query tab links to BigQuery for gcp clouds', async () => {
    vi.mocked(api.getPipelines).mockResolvedValue([PIPELINE])
    vi.mocked(api.analyzeTable).mockResolvedValue({ table: 'x', row_count: 0, columns: [] })
    vi.mocked(api.getLineage).mockResolvedValue({
      dataset: 'silver.gcp.smoke_silver.author_data',
      upstream: null,
      downstream_exports: [],
      downstream_checks: [],
    })

    renderDetail()
    await waitFor(() => screen.getByRole('tab', { name: /query/i }))
    await userEvent.setup().click(screen.getByRole('tab', { name: /query/i }))
    const link = await screen.findByRole('link', { name: /open in bigquery/i })
    expect(link).toHaveAttribute('href', expect.stringContaining('console.cloud.google.com/bigquery'))
  })

  it('Query tab links to Athena for aws clouds', async () => {
    vi.mocked(api.getPipelines).mockResolvedValue([
      { ...PIPELINE, destination: undefined, target: { s3_path: 's3://b/' },
        silver: { ...PIPELINE.silver!, database: 'silver_demo', bq_dataset: undefined, bq_project: undefined } } as PipelineConfig,
    ])
    vi.mocked(api.analyzeTable).mockResolvedValue({ table: 'x', row_count: 0, columns: [] })
    vi.mocked(api.getLineage).mockResolvedValue({
      dataset: 'silver.aws.silver_demo.author_data',
      upstream: null,
      downstream_exports: [],
      downstream_checks: [],
    })

    renderDetail('/m/catalog/datasets/silver/aws/silver_demo/author_data')
    await waitFor(() => screen.getByRole('tab', { name: /query/i }))
    await userEvent.setup().click(screen.getByRole('tab', { name: /query/i }))
    const link = await screen.findByRole('link', { name: /open in athena/i })
    expect(link).toHaveAttribute('href', expect.stringContaining('athena/home'))
  })
})
