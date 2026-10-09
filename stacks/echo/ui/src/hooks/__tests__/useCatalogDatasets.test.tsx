import { describe, it, expect, vi } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { useCatalogDatasets } from '../useCatalogDatasets'
import { api } from '@/lib/api'
import type { GoldPipelineConfig, PipelineConfig } from '@/lib/types'

vi.mock('@/lib/api', () => ({
  api: { getPipelines: vi.fn(), getGoldPipelines: vi.fn() },
}))

// The env comes from /config.json at runtime (UIFPE-515); pin it so the {env}
// resolution below is asserted against a known value.
vi.mock('@/lib/envConfig', () => ({ getEnv: () => 'dev' }))

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>
}

const FAKE: PipelineConfig[] = [
  {
    pipeline_id: 'smoke_author',
    version: 'latest',
    enabled: true,
    destination: { cloud: 'gcp' },
    source: { secret_arn: 'arn:aws:secretsmanager:eu-west-1:0:secret:x' },
    extract: { table: 'AUTHOR.AUTHOR_DATA', mode: 'incremental' },
    target: { gcs_path: 'gs://b/smoke/author_data/' },
    glue: { worker_type: 'G.1X', num_workers: 2, timeout_minutes: 60 },
    schedule: { frequency: 'daily' },
    silver: {
      enabled: true,
      bq_dataset: 'smoke_silver',
      bq_project: 'p',
      table: 'author_data',
      primary_key_cols: ['AUTHOR_ID'],
    },
  },
  {
    pipeline_id: 'mtl_items',
    version: 'latest',
    enabled: true,
    source: { secret_arn: 'arn:aws:secretsmanager:eu-west-1:0:secret:y' },
    extract: { table: 'INV.MTL_SYSTEM_ITEMS_B', mode: 'incremental' },
    target: { s3_path: 's3://bronze/inv/mtl_system_items_b/' },
    glue: { worker_type: 'G.2X', num_workers: 4, timeout_minutes: 120 },
    schedule: { frequency: 'hourly' },
    silver: {
      enabled: true,
      database: 'uif_echo_dev_silver_mdm',
      table: 'mtl_system_items_b',
      s3_path: 's3://silver/...',
      primary_key_cols: ['INVENTORY_ITEM_ID', 'ORGANIZATION_ID'],
    },
  },
]

const FAKE_GOLD: GoldPipelineConfig[] = [
  {
    gold_id: 'product',
    enabled: true,
    target: { database: 'uif_echo_{env}_gold_product', table: 'product' },
  },
  {
    gold_id: 'marketing_copy',
    enabled: true,
    target: { database: 'uif_echo_{env}_gold_product', table: 'marketing_copy' },
  },
  {
    gold_id: 'stock',
    enabled: false,
    target: { database: 'uif_echo_{env}_gold_stock', table: 'stock' },
  },
]

describe('useCatalogDatasets', () => {
  it('returns 4-part silver datasets including cloud segment', async () => {
    vi.mocked(api.getPipelines).mockResolvedValueOnce(FAKE)

    const { result } = renderHook(() => useCatalogDatasets('silver'), { wrapper })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    const datasets = result.current.data ?? []
    expect(datasets).toHaveLength(2)
    expect(datasets[0].layer).toBe('silver')
    expect(datasets.map(d => d.dataset)).toContain('silver.gcp.smoke_silver.author_data')
    expect(datasets.map(d => d.dataset)).toContain('silver.aws.uif_echo_dev_silver_mdm.mtl_system_items_b')
    // Cloud is propagated to row
    const gcp = datasets.find(d => d.dataset === 'silver.gcp.smoke_silver.author_data')!
    expect(gcp.cloud).toBe('gcp')
    const aws = datasets.find(d => d.dataset === 'silver.aws.uif_echo_dev_silver_mdm.mtl_system_items_b')!
    expect(aws.cloud).toBe('aws')
  })

  it('returns 4-part bronze datasets', async () => {
    vi.mocked(api.getPipelines).mockResolvedValueOnce(FAKE)

    const { result } = renderHook(() => useCatalogDatasets('bronze'), { wrapper })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    const datasets = result.current.data ?? []
    expect(datasets).toHaveLength(2)
    expect(datasets.every(d => d.layer === 'bronze')).toBe(true)
    expect(datasets.map(d => d.dataset)).toContain('bronze.gcp.author.author_data')
    expect(datasets.map(d => d.dataset)).toContain('bronze.aws.inv.mtl_system_items_b')
  })

  // Gold used to return [] unconditionally — that was a v1 limitation, not a
  // requirement, and the assertion below replaces the test that pinned it.
  it('derives gold datasets and resolves the {env} placeholder', async () => {
    vi.mocked(api.getGoldPipelines).mockResolvedValueOnce(FAKE_GOLD)

    const { result } = renderHook(() => useCatalogDatasets('gold'), { wrapper })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    const datasets = result.current.data ?? []
    // two entities share the product database and differ by table
    expect(datasets.map(d => d.dataset)).toEqual([
      'gold.aws.uif_echo_dev_gold_product.marketing_copy',
      'gold.aws.uif_echo_dev_gold_product.product',
    ])
    // the stored name carries {env}; a placeholder here would be an unusable
    // database name and invalid SQL in the Query tab
    expect(datasets.every(d => !d.database.includes('{env}'))).toBe(true)
    expect(datasets.every(d => d.layer === 'gold' && d.cloud === 'aws')).toBe(true)
  })

  it('skips a disabled gold entity, as bronze and silver skip disabled syncs', async () => {
    vi.mocked(api.getGoldPipelines).mockResolvedValueOnce(FAKE_GOLD)

    const { result } = renderHook(() => useCatalogDatasets('gold'), { wrapper })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    // `stock` is enabled:false in the fixture
    expect((result.current.data ?? []).map(d => d.table)).not.toContain('stock')
  })

  it('leaves a gold dataset with no sync_id, so no dead "Produced by sync" link', async () => {
    vi.mocked(api.getGoldPipelines).mockResolvedValueOnce(FAKE_GOLD)

    const { result } = renderHook(() => useCatalogDatasets('gold'), { wrapper })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    // DatasetDetail renders that link to /m/data-sync/syncs/{sync_id}, which
    // 404s on a gold_id — the row must not carry one.
    expect((result.current.data ?? []).every(d => !d.sync_id)).toBe(true)
  })
})
