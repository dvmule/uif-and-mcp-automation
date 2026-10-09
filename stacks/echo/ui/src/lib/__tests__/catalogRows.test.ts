import { describe, it, expect } from 'vitest'
import { catalogRow, datasetHref, goldForDataset } from '../catalogRows'
import type { CatalogDataset, GoldPipelineConfig, PipelineConfig, RunMetrics } from '../types'

const hoursAgo = (h: number) => new Date(Date.now() - h * 3_600_000).toISOString()

const silver: CatalogDataset = {
  dataset: 'silver.aws.db.price', layer: 'silver', cloud: 'aws', database: 'db', table: 'price', sync_id: 'eds-price',
}
const gold: CatalogDataset = {
  dataset: 'gold.aws.uif_echo_dev_gold_product.product', layer: 'gold', cloud: 'aws',
  database: 'uif_echo_dev_gold_product', table: 'product', sync_id: null,
}
const pipeline = {
  pipeline_id: 'eds-price', enabled: true, schedule: { frequency: 'daily' },
  extract: { table: 'EDS.PRICE_LIST', mode: 'full' }, metadata: { domain: 'Price' },
} as PipelineConfig
const goldCfg: GoldPipelineConfig = {
  gold_id: 'product', enabled: true, domain: 'Product',
  target: { database: 'uif_echo_{env}_gold_product', table: 'product' },
  schedule: { frequency: 'gold-24h' }, metadata: { last_run_at: hoursAgo(1) },
}
const run = (completed: string) => ({ job_name: 'bronze', run_id: 'r', pipeline_id: 'eds-price', status: 'SUCCEEDED', started_at: completed, completed_at: completed }) as RunMetrics

describe('catalogRow', () => {
  it('links the dataset detail route', () => {
    expect(datasetHref(silver)).toBe('/m/catalog/datasets/silver/aws/db/price')
  })

  it('a silver row is fed by its sync, with the sync domain', () => {
    const r = catalogRow(silver, { pipelines: [pipeline], env: 'dev' })
    expect(r.fedBy).toEqual({ label: 'EDS.PRICE_LIST', href: '/m/data-sync/syncs/eds-price' })
    expect(r.domain).toBe('Price')
  })

  it('silver freshness uses the Syncs rule only once the runs are known', () => {
    expect(catalogRow(silver, { pipelines: [pipeline], env: 'dev' }).freshness).toBeNull()
    const fresh = new Map([['eds-price', run(hoursAgo(1))]])
    expect(catalogRow(silver, { pipelines: [pipeline], lastRunByPipeline: fresh, env: 'dev' }).freshness).toBe('fresh')
    const stale = new Map([['eds-price', run(hoursAgo(30))]])
    expect(catalogRow(silver, { pipelines: [pipeline], lastRunByPipeline: stale, env: 'dev' }).freshness).toBe('stale')
    const none = new Map<string, RunMetrics | undefined>([['eds-price', undefined]])
    const never = catalogRow(silver, { pipelines: [pipeline], lastRunByPipeline: none, env: 'dev' })
    expect(never).toMatchObject({ freshness: 'critical', neverRan: true })
  })

  it('a gold row is fed by the gold entity whose resolved target matches, with gold freshness', () => {
    expect(goldForDataset(gold, [goldCfg], 'dev')?.gold_id).toBe('product')
    expect(goldForDataset(gold, [goldCfg], 'uat')).toBeUndefined()
    const r = catalogRow(gold, { golds: [goldCfg], env: 'dev' })
    expect(r.fedBy).toEqual({ label: 'product', href: '/m/data-sync/gold/product' })
    expect(r.domain).toBe('Product')
    expect(r.freshness).toBe('fresh')
  })

  it('unknown feeders give no link, Other domain and no freshness', () => {
    const r = catalogRow({ ...silver, sync_id: null }, { env: 'dev' })
    expect(r).toMatchObject({ fedBy: null, domain: 'Other', freshness: null })
    expect(catalogRow(gold, { golds: [], env: 'dev' })).toMatchObject({ fedBy: null, freshness: null })
  })
})
