import { useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useQueries } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { parseFilters, isEmptyFilters, type SyncFilterState } from '@/lib/filters'
import { matchesAll, pickDisplayLastRun } from '@/lib/filterMatchers'
import type { PipelineConfig, RunMetrics } from '@/lib/types'

export interface PipelineRunsSnapshot {
  displayLastRun: RunMetrics | undefined
  runs: RunMetrics[]
  /** The runs call has answered (success or error). False while it is in flight. */
  loaded: boolean
}

// Fetch runs for every pipeline in parallel. Shares the `['runs', pipelineId]`
// cache key with per-row queries — zero extra calls when deduped by TanStack Query.
export function useRunsByPipeline(
  pipelines: PipelineConfig[],
): Map<string, PipelineRunsSnapshot> {
  const queries = useQueries({
    queries: pipelines.map(p => ({
      queryKey: ['runs', p.pipeline_id] as const,
      queryFn: () => api.getRuns(p.pipeline_id),
      enabled: !!p.pipeline_id,
      refetchInterval: 30_000,
      staleTime: 25_000,
    })),
  })

  return useMemo(() => {
    const m = new Map<string, PipelineRunsSnapshot>()
    pipelines.forEach((p, i) => {
      const runs = queries[i]?.data?.items ?? []
      m.set(p.pipeline_id, {
        runs,
        displayLastRun: pickDisplayLastRun(runs),
        loaded: queries[i] ? !queries[i].isPending : false,
      })
    })
    return m
  }, [pipelines, queries])
}

/** @deprecated Use useRunsByPipeline — kept for callers that only need display last run. */
export function useLastRunByPipeline(
  pipelines: PipelineConfig[],
): Map<string, RunMetrics | undefined> {
  const byPipeline = useRunsByPipeline(pipelines)
  return useMemo(() => {
    const m = new Map<string, RunMetrics | undefined>()
    pipelines.forEach(p => {
      m.set(p.pipeline_id, byPipeline.get(p.pipeline_id)?.displayLastRun)
    })
    return m
  }, [pipelines, byPipeline])
}

/** Single source of truth for the URL-filtered pipeline list.
 *  `runsByPipeline` holds an entry only for pipelines whose runs call has
 *  answered, so a missing entry means "still loading" (a loaded pipeline with
 *  no runs has an empty list). */
export function useFilteredPipelines(pipelines: PipelineConfig[]): {
  filtered: PipelineConfig[]
  lastRunByPipeline: Map<string, RunMetrics | undefined>
  runsByPipeline: Map<string, RunMetrics[]>
  state: SyncFilterState
} {
  const [searchParams] = useSearchParams()
  const state = useMemo(() => parseFilters(searchParams), [searchParams])
  const runsSnapshot = useRunsByPipeline(pipelines)

  const lastRunByPipeline = useMemo(() => {
    const m = new Map<string, RunMetrics | undefined>()
    pipelines.forEach(p => {
      m.set(p.pipeline_id, runsSnapshot.get(p.pipeline_id)?.displayLastRun)
    })
    return m
  }, [pipelines, runsSnapshot])

  const runsByPipeline = useMemo(() => {
    const m = new Map<string, RunMetrics[]>()
    pipelines.forEach(p => {
      const snap = runsSnapshot.get(p.pipeline_id)
      if (snap?.loaded) m.set(p.pipeline_id, snap.runs)
    })
    return m
  }, [pipelines, runsSnapshot])

  const filtered = useMemo(() => {
    if (isEmptyFilters(state)) return pipelines
    return pipelines.filter(p =>
      matchesAll(
        p,
        lastRunByPipeline.get(p.pipeline_id),
        state,
        runsByPipeline.get(p.pipeline_id),
      ),
    )
  }, [pipelines, lastRunByPipeline, runsByPipeline, state])

  return { filtered, lastRunByPipeline, runsByPipeline, state }
}
