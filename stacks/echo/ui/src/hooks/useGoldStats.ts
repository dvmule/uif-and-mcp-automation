import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { TREND_DEFAULT_DAYS } from '@/lib/goldStats'

/** UIFPE-544: the gold fleet Overview.
 *
 *  One call, not one per entity: the fan-out this page already suffers from on
 *  the silver side is the thing this route exists to avoid. `retry: false`
 *  because until the controller is applied in an environment the route answers
 *  a bare 404, which the card reads with isUndeployedRouteError. */
export const GOLD_STATS_KEY = ['gold-stats'] as const

export function useGoldStats(days: number = TREND_DEFAULT_DAYS) {
  return useQuery({
    queryKey: [...GOLD_STATS_KEY, days],
    queryFn: () => api.getGoldStats(days),
    retry: false,
    // The live half moves with every run; a minute is the same cadence the
    // alarm rules card refreshes at.
    refetchInterval: 60_000,
  })
}
