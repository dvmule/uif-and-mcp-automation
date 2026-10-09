import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import type { PipelineConfig } from '@/lib/types'

export function useDownstreamSyncs(silverFqn: string) {
  return useQuery({
    queryKey: ['downstream', silverFqn],
    queryFn: async (): Promise<PipelineConfig[]> => {
      if (!silverFqn) return []
      const all = await api.getPipelines()
      // PipelineConfig.extract is FLAT (table, mode, watermark_col) — not nested
      // under initial/incremental. See ui/src/lib/types.ts PipelineExtract.
      return all.filter(p => p.extract?.table === silverFqn)
    },
  })
}
