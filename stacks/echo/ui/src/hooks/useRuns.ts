import {
  useQuery,
  useInfiniteQuery,
  useQueries,
  useQueryClient,
  type InfiniteData,
  type QueryClient,
} from '@tanstack/react-query'
import { useEffect, useMemo } from 'react'
import { api } from '@/lib/api'
import {
  activeRunKey,
  isInFlightStatus,
  mergeRunIntoInfinite,
  mergeRunIntoRunPage,
  resolveInFlightRunId,
} from '@/lib/runLifecycle'
import type { RunMetrics, RunPage } from '@/lib/types'

/** Reactive subscription to the active-run id written by useTriggerRun. */
export function useActiveRunId(pipelineId: string): string | undefined {
  const qc = useQueryClient()
  // React Query rejects undefined query data, so an empty cache resolves to null.
  const { data } = useQuery<string | null>({
    queryKey: activeRunKey(pipelineId),
    queryFn: () => qc.getQueryData<string | null>(activeRunKey(pipelineId)) ?? null,
    enabled: !!pipelineId,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  })
  return data ?? undefined
}

function finalizePolledRun(qc: QueryClient, pipelineId: string, polledRun: RunMetrics) {
  qc.setQueryData(['runs', pipelineId], (old: RunPage | undefined) =>
    mergeRunIntoRunPage(old, polledRun),
  )
  qc.setQueryData<InfiniteData<RunPage>>(['runs-infinite', pipelineId], (old) =>
    mergeRunIntoInfinite(old, polledRun),
  )
  if (!isInFlightStatus(polledRun.status)) {
    qc.removeQueries({ queryKey: activeRunKey(pipelineId) })
    void qc.invalidateQueries({ queryKey: ['runs', pipelineId] })
    void qc.invalidateQueries({ queryKey: ['runs-infinite', pipelineId] })
    void qc.invalidateQueries({ queryKey: ['pipelines'] })
  }
}

export function useRuns(pipelineId: string) {
  return useQuery({
    queryKey: ['runs', pipelineId],
    queryFn: () => api.getRuns(pipelineId),
    enabled: !!pipelineId,
    refetchInterval: 30_000,
  })
}

export function useRunsInfinite(pipelineId: string) {
  return useInfiniteQuery({
    queryKey: ['runs-infinite', pipelineId],
    queryFn: ({ pageParam }) => api.getRuns(pipelineId, pageParam as string | undefined),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => last.next_token ?? undefined,
    enabled: !!pipelineId,
    refetchInterval: (query) => {
      const pages = query.state.data?.pages
      const latest = pages?.[0]?.items?.[0]
      return latest && isInFlightStatus(latest.status) ? 30_000 : false
    },
  })
}

/**
 * Polls GET /pipelines/{id}/runs/{runId} (Glue-backed while in flight) and merges
 * status into both runs caches until the job reaches a terminal state.
 */
export function useActiveRunPoll(pipelineId: string, latestRun?: RunMetrics) {
  const qc = useQueryClient()
  const activeRunId = useActiveRunId(pipelineId)
  const runId = resolveInFlightRunId(activeRunId, latestRun)

  const { data: polledRun } = useQuery({
    queryKey: ['run', pipelineId, runId],
    queryFn: () => api.getRun(pipelineId, runId!),
    enabled: !!pipelineId && !!runId,
    refetchInterval: (query) => {
      const s = query.state.data?.status
      return s && isInFlightStatus(s) ? 30_000 : false
    },
  })

  useEffect(() => {
    if (!pipelineId || !polledRun) return
    finalizePolledRun(qc, pipelineId, polledRun)
  }, [polledRun, pipelineId, qc])
}

/**
 * List page: poll Glue for in-flight runs (manual trigger or after page refresh).
 * Complements useLastRunByPipeline (metrics + Glue in-flight from list_runs).
 */
export function useSyncsActiveRunPolls(pipelineIds: string[]) {
  const qc = useQueryClient()
  const stableIds = useMemo(
    () => [...new Set(pipelineIds.filter(Boolean))].sort(),
    [pipelineIds],
  )

  const activeWatches = useQueries({
    queries: stableIds.map((id) => ({
      queryKey: activeRunKey(id),
      queryFn: () => qc.getQueryData<string | null>(activeRunKey(id)) ?? null,
      enabled: !!id,
      staleTime: Infinity,
      refetchOnWindowFocus: false,
      refetchOnReconnect: false,
    })),
  })

  const runsWatches = useQueries({
    queries: stableIds.map((id) => ({
      queryKey: ['runs', id] as const,
      queryFn: () => api.getRuns(id),
      enabled: !!id,
      staleTime: 25_000,
    })),
  })

  const pairs = useMemo(
    () =>
      stableIds
        .map((pipelineId, i) => {
          const runId = resolveInFlightRunId(
            activeWatches[i]?.data ?? undefined,
            runsWatches[i]?.data?.items?.[0],
          )
          return runId ? { pipelineId, runId } : null
        })
        .filter((p): p is { pipelineId: string; runId: string } => p !== null),
    [stableIds, activeWatches, runsWatches],
  )

  const polls = useQueries({
    queries: pairs.map(({ pipelineId, runId }) => ({
      queryKey: ['run', pipelineId, runId],
      queryFn: () => api.getRun(pipelineId, runId),
      enabled: !!runId,
      refetchInterval: (query: { state: { data?: RunMetrics } }) => {
        const s = query.state.data?.status
        return s && isInFlightStatus(s) ? 30_000 : false
      },
    })),
  })

  useEffect(() => {
    pairs.forEach(({ pipelineId }, i) => {
      const polledRun = polls[i]?.data
      if (!polledRun) return
      finalizePolledRun(qc, pipelineId, polledRun)
    })
  }, [pairs, polls, qc])
}

export function useRun(pipelineId: string, runId: string) {
  return useQuery({
    queryKey: ['run', pipelineId, runId],
    queryFn: () => api.getRun(pipelineId, runId),
    enabled: !!pipelineId && !!runId,
  })
}
