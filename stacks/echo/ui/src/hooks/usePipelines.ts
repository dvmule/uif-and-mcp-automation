import { useQuery, useMutation, useQueryClient, type InfiniteData } from '@tanstack/react-query'
import { toast } from 'sonner'
import { api } from '@/lib/api'
import { apiErrorMessage } from '@/lib/apiError'
import {
  activeRunKey,
  optimisticRunningRun,
  prependRunToInfinite,
  prependRunToRunPage,
} from '@/lib/runLifecycle'
import type { PipelineConfig, RunPage } from '@/lib/types'

function applyOptimisticRun(
  qc: ReturnType<typeof useQueryClient>,
  pipelineId: string,
  runId: string,
  startedAt?: string,
  jobName = 'bronze-ingest',
  phase?: 'initial' | 'incremental',
) {
  const optimistic = optimisticRunningRun(pipelineId, runId, startedAt, jobName, phase)
  qc.setQueryData<RunPage>(['runs', pipelineId], (old) =>
    prependRunToRunPage(old, optimistic),
  )
  qc.setQueryData<InfiniteData<RunPage>>(['runs-infinite', pipelineId], (old) =>
    prependRunToInfinite(old, optimistic),
  )
  qc.setQueryData<string | null>(activeRunKey(pipelineId), runId)
}

function patchPipelineEnabled(
  qc: ReturnType<typeof useQueryClient>,
  pipelineId: string,
  enabled: boolean,
) {
  qc.setQueryData<PipelineConfig[]>(['pipelines'], (old) =>
    old?.map((p) => (p.pipeline_id === pipelineId ? { ...p, enabled } : p)),
  )
  qc.setQueryData<PipelineConfig>(['pipeline', pipelineId], (old) =>
    old ? { ...old, enabled } : old,
  )
}

export function usePipelines() {
  return useQuery({
    queryKey: ['pipelines'],
    queryFn: api.getPipelines,
    refetchInterval: 30_000,
  })
}

export function usePipeline(id: string) {
  return useQuery({
    queryKey: ['pipeline', id],
    queryFn: () => api.getPipeline(id),
    enabled: !!id,
  })
}

export function useTogglePipeline() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      api.togglePipeline(id, enabled),
    onMutate: async ({ id, enabled }) => {
      await qc.cancelQueries({ queryKey: ['pipelines'] })
      await qc.cancelQueries({ queryKey: ['pipeline', id] })
      patchPipelineEnabled(qc, id, enabled)
    },
    onSuccess: (_, { id }) => {
      void qc.invalidateQueries({ queryKey: ['pipeline', id] })
      void qc.invalidateQueries({ queryKey: ['pipelines'] })
    },
    onError: (err) => {
      void qc.invalidateQueries({ queryKey: ['pipelines'] })
      toast.error(apiErrorMessage(err, 'Failed to update sync status'))
    },
  })
}

export function useTriggerRun() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => api.triggerRun(id),
    onSuccess: async (data, id) => {
      await qc.cancelQueries({ queryKey: ['runs', id] })
      await qc.cancelQueries({ queryKey: ['runs-infinite', id] })
      applyOptimisticRun(qc, id, data.run_id, data.started_at, 'bronze-ingest', data.phase)
    },
    onError: (err) => {
      toast.error(apiErrorMessage(err, 'Failed to start run'))
    },
  })
}

export function useTriggerReconcile() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => api.triggerRun(id, { layer: 'reconcile' }),
    onSuccess: async (data, id) => {
      await qc.cancelQueries({ queryKey: ['runs', id] })
      await qc.cancelQueries({ queryKey: ['runs-infinite', id] })
      const startedAt = (data as { started_at?: string }).started_at
      applyOptimisticRun(qc, id, data.run_id, startedAt, 'silver-reconcile')
    },
    onError: (err) => {
      toast.error(apiErrorMessage(err, 'Failed to start reconcile'))
    },
  })
}

export function useUpsertPipeline() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, config }: { id: string; config: PipelineConfig }) =>
      api.upsertPipeline(id, config),
    onSuccess: (_, { id }) => {
      qc.invalidateQueries({ queryKey: ['pipeline', id] })
      qc.invalidateQueries({ queryKey: ['pipelines'] })
    },
  })
}

export function useDeletePipeline() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => api.deletePipeline(id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['pipelines'] })
    },
    onError: (err) => {
      toast.error(apiErrorMessage(err, 'Failed to remove sync'))
    },
  })
}
