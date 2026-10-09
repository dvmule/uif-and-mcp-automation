import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import type { Partner } from '@/lib/types'

export function usePartners() {
  return useQuery({
    queryKey: ['partners'],
    queryFn: api.partnerExport.listPartners,
    staleTime: 5 * 60_000,
  })
}

export function usePartner(id: string | undefined) {
  return useQuery({
    queryKey: ['partner', id],
    queryFn: () => api.partnerExport.getPartner(id!),
    enabled: !!id,
  })
}

export function useCreatePartner() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: Omit<Partner, 'partner_id'> & { partner_id?: string }) =>
      api.partnerExport.createPartner(body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['partners'] })
    },
  })
}
