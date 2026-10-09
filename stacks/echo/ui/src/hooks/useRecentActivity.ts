import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'

export function useRecentActivity(limit = 20) {
  return useQuery({
    queryKey: ['recent-activity', limit],
    queryFn: () => api.getRecentActivity(limit),
    refetchInterval: 30_000,
    staleTime: 15_000,
  })
}
