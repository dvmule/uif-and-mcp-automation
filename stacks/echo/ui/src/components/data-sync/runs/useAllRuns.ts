import { createElement, useCallback, useMemo, useState, type ReactNode } from 'react'
import { useGoldPipelines } from '@/hooks/useGoldPipelines'
import { useGoldRuns } from '@/hooks/useGoldRuns'
import { usePipelines } from '@/hooks/usePipelines'
import { CollectRuns, type Collected } from './CollectRuns'
import { goldRunRow, pipelineRunRow, type RunRow } from '@/lib/runsTable'
import type { GoldPipelineConfig, PipelineConfig } from '@/lib/types'

export interface AllRuns {
  /** Every run loaded so far, bronze/silver/reconcile and gold, unsorted. */
  rows: RunRow[]
  /** Pipelines whose runs have not arrived yet. */
  pendingPipelines: number
  /** Pipelines whose runs call failed. */
  failedPipelines: number
  goldPending: boolean
  /** The gold pipeline list is still loading. */
  goldsLoading: boolean
  goldError: boolean
  /** The pipeline list itself is loading. */
  loading: boolean
  pipelines: PipelineConfig[]
  golds: GoldPipelineConfig[]
  /** Render this once, anywhere on the page: it loads the runs. */
  collector: ReactNode
}

/** Every run of every layer as RunRows. `pipelineIds` limits which pipelines'
 *  runs are fetched (the Jobs page does when it is filtered to some objects). */
export function useAllRuns(pipelineIds?: string[] | null): AllRuns {
  const pipelinesQuery = usePipelines()
  const goldRunsQuery = useGoldRuns()
  const goldsQuery = useGoldPipelines()
  const [collected, setCollected] = useState<Record<string, Collected>>({})

  const pipelines = useMemo(() => pipelinesQuery.data ?? [], [pipelinesQuery.data])
  const golds = useMemo(() => goldsQuery.data ?? [], [goldsQuery.data])
  const idsKey = pipelineIds ? pipelineIds.join(',') : null

  const toLoad = useMemo(
    () => (idsKey === null ? pipelines : pipelines.filter((p) => idsKey.split(',').includes(p.pipeline_id))),
    [pipelines, idsKey],
  )

  const onCollect = useCallback((id: string, next: Collected) => {
    setCollected((prev) => {
      const held = prev[id]
      if (held && held.sig === next.sig && held.loading === next.loading && held.failed === next.failed) {
        return prev
      }
      return { ...prev, [id]: next }
    })
  }, [])

  const rows = useMemo(() => {
    const out: RunRow[] = []
    for (const p of toLoad) {
      for (const run of collected[p.pipeline_id]?.data?.items ?? []) out.push(pipelineRunRow(p, run))
    }
    const goldById = new Map(golds.map((g) => [g.gold_id, g]))
    for (const run of goldRunsQuery.data?.items ?? []) out.push(goldRunRow(run, goldById.get(run.gold_id)))
    return out
  }, [toLoad, collected, golds, goldRunsQuery.data])

  let pendingPipelines = 0
  let failedPipelines = 0
  for (const p of toLoad) {
    const c = collected[p.pipeline_id]
    if (!c || c.loading) pendingPipelines += 1
    else if (c.failed) failedPipelines += 1
  }

  const collector = createElement(
    'div',
    { hidden: true },
    toLoad.map((p) => createElement(CollectRuns, { key: p.pipeline_id, pipeline: p, onCollect })),
  )

  return {
    rows,
    pendingPipelines,
    failedPipelines,
    goldPending: !!goldRunsQuery.isLoading,
    goldsLoading: !!goldsQuery.isLoading,
    goldError: !!goldRunsQuery.error,
    loading: !!pipelinesQuery.isLoading,
    pipelines,
    golds,
    collector,
  }
}
