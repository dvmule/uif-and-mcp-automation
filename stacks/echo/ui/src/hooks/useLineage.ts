import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import type { DatasetLineage } from '@/lib/types'

/**
 * Fetch cross-module lineage for a Catalog dataset.
 * `datasetRef` is the canonical 4-part "<layer>.<cloud>.<database>.<table>" string.
 */
export function useLineage(datasetRef: string | undefined) {
  return useQuery<DatasetLineage>({
    queryKey: ['lineage', datasetRef],
    queryFn: () => api.getLineage(datasetRef as string),
    enabled: !!datasetRef,
    staleTime: 30_000,
  })
}
