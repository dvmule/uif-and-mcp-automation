import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'

export function useDqCheckDetail(checkId: string) {
  return useQuery({
    queryKey: ['dq', 'check', checkId],
    queryFn:  () => api.dq.getCheck(checkId),
    enabled:  !!checkId,
  })
}

export function useDqCheckRuns(checkId: string) {
  return useQuery({
    queryKey: ['dq', 'check', checkId, 'runs'],
    queryFn:  () => api.dq.listCheckRuns(checkId).then(r => r.runs),
    enabled:  !!checkId,
    refetchInterval: 15_000,
  })
}
