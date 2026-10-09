import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'

export function useSilverBqConnections() {
  return useQuery({
    queryKey: ['connections', 'silver-bq'],
    queryFn: api.getSilverBqConnections,
    staleTime: 5 * 60_000,
  })
}
