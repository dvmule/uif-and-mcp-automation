import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'

export function useReBootstrap() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => api.reBootstrap(id),
    onSuccess: (_, id) => {
      qc.invalidateQueries({ queryKey: ['pipeline', id] })
      qc.invalidateQueries({ queryKey: ['runs', id] })
    },
  })
}
