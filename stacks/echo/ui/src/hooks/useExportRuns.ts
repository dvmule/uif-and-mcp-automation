import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'

export function useExportRuns(exportId: string | undefined, nextToken?: string) {
  return useQuery({
    queryKey: ['export-runs', exportId, nextToken],
    queryFn: () => api.partnerExport.listExportRuns(exportId!, nextToken),
    enabled: !!exportId,
    refetchInterval: 30_000,
  })
}

export function useAllExportRuns(nextToken?: string) {
  return useQuery({
    queryKey: ['all-export-runs', nextToken],
    queryFn: () => api.partnerExport.listRuns(nextToken),
    refetchInterval: 30_000,
  })
}
