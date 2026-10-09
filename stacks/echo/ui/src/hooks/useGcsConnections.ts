import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'

export function useGcsConnections() {
  return useQuery({
    queryKey: ['connections', 'gcs'],
    queryFn: api.getGcsConnections,
    staleTime: 5 * 60_000,
  })
}
