import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import type { DqCheckConfig } from '@/lib/types'

export function useDqChecks(opts?: { subject?: string }) {
  return useQuery({
    queryKey: ['dq', 'checks', opts?.subject ?? 'all'],
    queryFn:  () => api.dq.listChecks(opts?.subject ? { subject: opts.subject } : undefined),
    refetchInterval: 30_000,
  })
}

export function useUpsertDqCheck() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ checkId, body }: { checkId: string; body: DqCheckConfig }) =>
      api.dq.upsertCheck(checkId, body),
    onSuccess: (_, { checkId }) => {
      qc.invalidateQueries({ queryKey: ['dq', 'checks'] })
      qc.invalidateQueries({ queryKey: ['dq', 'check', checkId] })
    },
  })
}

export function useDeleteDqCheck() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (checkId: string) => api.dq.deleteCheck(checkId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['dq', 'checks'] }),
  })
}

export function useTriggerDqCheck() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (checkId: string) => api.dq.triggerRun(checkId),
    onSuccess: (_, checkId) => {
      setTimeout(() => qc.invalidateQueries({ queryKey: ['dq', 'check', checkId, 'runs'] }), 3000)
    },
  })
}

export function useDqKinds() {
  return useQuery({
    queryKey: ['dq', 'kinds'],
    queryFn:  () => api.dq.listKinds().then(r => r.kinds),
    staleTime: 5 * 60_000,
  })
}
