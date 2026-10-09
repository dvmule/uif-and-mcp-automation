import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'

export function useHomeSummary() {
  return useQuery({
    queryKey: ['home-summary'],
    queryFn: api.getHomeSummary,
    refetchInterval: 30_000,
    staleTime: 15_000,
  })
}
