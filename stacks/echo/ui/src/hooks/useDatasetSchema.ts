import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import type { AnalyzeResult, PipelineConfig } from '@/lib/types'

/**
 * Fetch the schema for a Catalog dataset.
 *
 * v1: locate the upstream sync via usePipelines, then POST /analyze on its
 * Oracle source. The analyze payload includes the column list which we reuse
 * as the schema view.
 *
 * Gold returns empty (no upstream Oracle table — Gold is materialized from
 * Silvers). A future plan wires Glue catalog lookup for Gold.
 */
function findSync(
  layer: 'bronze' | 'silver' | 'gold',
  database: string,
  table: string,
  pipelines: PipelineConfig[],
): PipelineConfig | undefined {
  const dbLc = database.toLowerCase()
  const tblLc = table.toLowerCase()
  if (layer === 'silver') {
    return pipelines.find(p => {
      const cand = (p.silver?.database || p.silver?.bq_dataset || '').toLowerCase()
      return p.silver?.enabled && cand === dbLc && (p.silver.table || '').toLowerCase() === tblLc
    })
  }
  if (layer === 'bronze') {
    return pipelines.find(p => {
      const ext = p.extract?.table || ''
      if (!ext.includes('.')) return false
      const [schema, name] = ext.split('.', 2)
      return schema.toLowerCase() === dbLc && name.toLowerCase() === tblLc
    })
  }
  return undefined
}

export function useDatasetSchema(
  layer: 'bronze' | 'silver' | 'gold',
  database: string,
  table: string,
) {
  return useQuery<AnalyzeResult>({
    queryKey: ['dataset-schema', layer, database, table],
    queryFn: async () => {
      if (layer === 'gold') {
        return { table: `${database}.${table}`, row_count: 0, columns: [] }
      }
      const pipelines = await api.getPipelines()
      const sync = findSync(layer, database, table, pipelines)
      if (!sync) {
        return { table: `${database}.${table}`, row_count: 0, columns: [] }
      }
      return api.analyzeTable(sync.extract.table, sync.source.secret_arn)
    },
    staleTime: 60_000,
  })
}
