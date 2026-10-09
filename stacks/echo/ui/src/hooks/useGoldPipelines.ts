import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import type { GoldPipelineConfig } from '@/lib/types'

/** The registered gold pipelines and the Silver tables they read (UIFPE-506). */
export function useGoldPipelines() {
  return useQuery<GoldPipelineConfig[]>({
    queryKey: ['gold-pipelines'],
    queryFn: api.getGoldPipelines,
    staleTime: 60_000,
  })
}
