import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import type { PartnerExport } from '@/lib/types'

export interface ListExportsFilters {
  partner?: string
  source?: string
}

export function usePartnerExports(filters: ListExportsFilters = {}) {
  return useQuery({
    queryKey: ['partner-exports', filters],
    queryFn: () => api.partnerExport.listExports(filters),
    refetchInterval: 30_000,
  })
}

export function usePartnerExport(id: string | undefined) {
  return useQuery({
    queryKey: ['partner-export', id],
    queryFn: () => api.partnerExport.getExport(id!),
    enabled: !!id,
  })
}

export function useUpsertPartnerExport() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: PartnerExport }) =>
      api.partnerExport.upsertExport(id, body),
    onSuccess: (_, { id }) => {
      qc.invalidateQueries({ queryKey: ['partner-exports'] })
      qc.invalidateQueries({ queryKey: ['partner-export', id] })
    },
  })
}

export function useDeletePartnerExport() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => api.partnerExport.deleteExport(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['partner-exports'] })
    },
  })
}

export function useRunPartnerExport() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => api.partnerExport.runExport(id),
    onSuccess: (_, id) => {
      setTimeout(() => qc.invalidateQueries({ queryKey: ['export-runs', id] }), 3000)
    },
  })
}
