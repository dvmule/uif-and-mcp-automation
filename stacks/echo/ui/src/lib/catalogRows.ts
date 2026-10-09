// Catalog › Browse row model: who feeds each dataset and how fresh it is, from data already loaded. Pure.
import { goldFreshness, goldTableFqn } from './syncRow'
import type { CatalogDataset, GoldPipelineConfig, PipelineConfig, RunMetrics } from './types'
import { freshness, type FreshnessLevel } from './utils'

export interface CatalogRow {
  dataset: CatalogDataset
  href: string
  domain: string
  /** The sync (bronze/silver) or gold entity that writes the table. */
  fedBy: { label: string; href: string } | null
  /** Null when there is nothing to judge by: the page shows a dash. */
  freshness: FreshnessLevel | null
  /** True when the feeding sync has runs but no ingest run to judge by. */
  neverRan: boolean
}

export function datasetHref(d: Pick<CatalogDataset, 'layer' | 'cloud' | 'database' | 'table'>): string {
  return `/m/catalog/datasets/${d.layer}/${d.cloud}/${d.database}/${d.table}`
}

/** The gold entity whose target (with `{env}` resolved) is this dataset. */
export function goldForDataset(
  d: Pick<CatalogDataset, 'database' | 'table'>,
  golds: GoldPipelineConfig[] | undefined,
  env: string,
): GoldPipelineConfig | undefined {
  const fqn = `${d.database}.${d.table}`
  return golds?.find((g) => goldTableFqn(g, env) === fqn)
}

export interface CatalogSources {
  pipelines?: PipelineConfig[]
  golds?: GoldPipelineConfig[]
  /** A sync's display last run, where its runs are already in the cache; absent = not loaded. */
  lastRunByPipeline?: Map<string, RunMetrics | undefined>
  env: string
}

export function catalogRow(d: CatalogDataset, src: CatalogSources): CatalogRow {
  const base = { dataset: d, href: datasetHref(d) }
  if (d.layer === 'gold') {
    const g = goldForDataset(d, src.golds, src.env)
    return {
      ...base,
      domain: g?.domain ?? g?.metadata?.domain ?? 'Other',
      fedBy: g ? { label: g.gold_id, href: `/m/data-sync/gold/${encodeURIComponent(g.gold_id)}` } : null,
      freshness: g ? goldFreshness(g) : null,
      neverRan: false,
    }
  }
  const p = d.sync_id ? src.pipelines?.find((x) => x.pipeline_id === d.sync_id) : undefined
  const runsLoaded = !!p && !!src.lastRunByPipeline?.has(p.pipeline_id)
  const run = p ? src.lastRunByPipeline?.get(p.pipeline_id) : undefined
  return {
    ...base,
    domain: p?.metadata?.domain ?? 'Other',
    fedBy: d.sync_id
      ? { label: p?.extract?.table || d.sync_id, href: `/m/data-sync/syncs/${encodeURIComponent(d.sync_id)}` }
      : null,
    // The Syncs-list rule, only once that sync's runs are known.
    freshness: p && runsLoaded ? freshness(p, run?.completed_at) : null,
    neverRan: runsLoaded && !run,
  }
}
