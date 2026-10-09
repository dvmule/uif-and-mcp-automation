import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'

export function useFormatTemplates() {
  return useQuery({
    queryKey: ['format-templates'],
    queryFn: api.partnerExport.listFormats,
    staleTime: 5 * 60_000,
  })
}
