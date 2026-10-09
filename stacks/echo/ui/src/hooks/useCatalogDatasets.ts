import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { getEnv } from '@/lib/envConfig'
import type { CatalogDataset, GoldPipelineConfig, PipelineConfig } from '@/lib/types'

type Layer = 'bronze' | 'silver' | 'gold'

function deriveCloud(p: PipelineConfig): 'aws' | 'gcp' {
  return (p.destination?.cloud ?? 'aws') as 'aws' | 'gcp'
}

function pipelineToBronzeDataset(p: PipelineConfig): CatalogDataset | null {
  if (!p.enabled) return null
  const tbl = p.extract?.table
  if (!tbl || !tbl.includes('.')) return null
  const [schema, name] = tbl.split('.', 2)
  const database = schema.toLowerCase()
  const table = name.toLowerCase()
  const cloud = deriveCloud(p)
  return {
    dataset: `bronze.${cloud}.${database}.${table}`,
    layer: 'bronze',
    cloud,
    database,
    table,
    sync_id: p.pipeline_id,
  }
}

function pipelineToSilverDataset(p: PipelineConfig): CatalogDataset | null {
  if (!p.enabled || !p.silver?.enabled) return null
  const database = (p.silver.database || p.silver.bq_dataset || '').toLowerCase()
  const table = (p.silver.table || '').toLowerCase()
  if (!database || !table) return null
  const cloud = deriveCloud(p)
  return {
    dataset: `silver.${cloud}.${database}.${table}`,
    layer: 'silver',
    cloud,
    database,
    table,
    sync_id: p.pipeline_id,
  }
}

/** Gold target names are stored with an unresolved `{env}` placeholder, so
 *  they are resolved here: the catalog names real databases, and the Query tab
 *  builds `SELECT * FROM {database}.{table}` — a placeholder there is invalid
 *  SQL and an unusable Athena link. */
function goldToDataset(g: GoldPipelineConfig, env: string): CatalogDataset | null {
  if (g.enabled !== true) return null
  const database = (g.target?.database || '').replace('{env}', env).toLowerCase()
  const table = (g.target?.table || '').toLowerCase()
  if (!database || !table) return null
  return {
    dataset: `gold.aws.${database}.${table}`,
    layer: 'gold',
    // Gold is AWS-only today; the lineage endpoint asserts the same.
    cloud: 'aws',
    database,
    table,
    // Deliberately null: a gold entity is not a sync, and the detail page's
    // "Produced by sync" link goes to /m/data-sync/syncs/{id}, which 404s on a
    // gold_id (the dead-link trap UIFPE-502 and UIFPE-504 designed around).
    sync_id: null,
  }
}

/**
 * Derive Catalog datasets from the live pipeline list using the 4-part
 * grammar from resolved decision #3 (<layer>.<cloud>.<database>.<table>).
 *
 * Gold comes from the gold pipeline configs rather than the sync list.
 */
export function useCatalogDatasets(layer: Layer) {
  return useQuery<CatalogDataset[]>({
    queryKey: ['catalog-datasets', layer],
    queryFn: async () => {
      if (layer === 'gold') {
        const golds = await api.getGoldPipelines()
        return golds
          .map(g => goldToDataset(g, getEnv()))
          .filter((d): d is CatalogDataset => d !== null)
          .sort((a, b) => a.dataset.localeCompare(b.dataset))
      }
      const pipelines = await api.getPipelines()
      const project =
        layer === 'silver' ? pipelineToSilverDataset : pipelineToBronzeDataset
      return pipelines
        .map(project)
        .filter((d): d is CatalogDataset => d !== null)
        .sort((a, b) => a.dataset.localeCompare(b.dataset))
    },
    staleTime: 30_000,
  })
}
