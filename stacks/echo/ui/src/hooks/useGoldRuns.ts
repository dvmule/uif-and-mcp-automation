import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { isUndeployedRouteError } from '@/lib/apiError'
import type { GoldRunPage } from '@/lib/types'

/** Gold run history for every entity in one request (UIFPE-502). */
export function useGoldRuns(goldId?: string, limit?: number) {
  return useQuery<GoldRunPage>({
    queryKey: ['gold-runs', goldId ?? 'all', limit ?? 'default'],
    queryFn: () => api.getGoldRuns(goldId, limit),
    staleTime: 30_000,
    // A bare gateway 404 means the route is not deployed here; retrying it
    // only delays the honest placeholder.
    retry: (count, err) => !isUndeployedRouteError(err) && count < 2,
  })
}
