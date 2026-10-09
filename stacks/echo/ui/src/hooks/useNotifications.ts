import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'

/** UIFPE-543: who is emailed when a gold alarm changes state, and the alarms
 *  themselves. Both routes ride the echo controller; until it is deployed in
 *  an environment they answer a bare 404, which the pages read with
 *  isUndeployedRouteError - so no retries on error. */
export const SUBSCRIBERS_KEY = ['alert-subscribers'] as const
export const RULES_KEY = ['alert-rules'] as const

export function useAlertSubscribers() {
  return useQuery({ queryKey: SUBSCRIBERS_KEY, queryFn: api.getAlertSubscribers, retry: false })
}

export function useAlertRules() {
  return useQuery({ queryKey: RULES_KEY, queryFn: api.getAlertRules, retry: false, refetchInterval: 60_000 })
}

function useInvalidateSubscribers() {
  const qc = useQueryClient()
  return () => qc.invalidateQueries({ queryKey: SUBSCRIBERS_KEY })
}

export function useAddAlertSubscriber() {
  const invalidate = useInvalidateSubscribers()
  return useMutation({
    mutationFn: ({ email, goldIds }: { email: string; goldIds?: string[] }) => api.addAlertSubscriber(email, goldIds),
    onSuccess: invalidate,
  })
}

export function useSetAlertSubscriber() {
  const invalidate = useInvalidateSubscribers()
  return useMutation({
    mutationFn: ({ email, patch }: { email: string; patch: Parameters<typeof api.setAlertSubscriber>[1] }) =>
      api.setAlertSubscriber(email, patch),
    onSuccess: invalidate,
  })
}

export function useRemoveAlertSubscriber() {
  const invalidate = useInvalidateSubscribers()
  return useMutation({ mutationFn: (email: string) => api.removeAlertSubscriber(email), onSuccess: invalidate })
}
