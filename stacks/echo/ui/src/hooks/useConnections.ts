import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'

export function useConnections() {
  return useQuery({
    queryKey: ['connections'],
    queryFn: api.getConnections,
    staleTime: 5 * 60_000,
  })
}
