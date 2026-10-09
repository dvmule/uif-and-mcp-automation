import { useEffect } from 'react'
import { useRuns } from '@/hooks/useRuns'
import type { PipelineConfig, RunPage } from '@/lib/types'

export interface Collected {
  sig: string
  data: RunPage | undefined
  loading: boolean
  failed: boolean
}

/** A cheap fingerprint of a runs page, so the parent only re-renders when a run
 *  appears or changes, not on every render of the query. */
function fingerprint(data: RunPage | undefined): string {
  const items = data?.items ?? []
  return `${items.length}:${items
    .map((r) => `${r.run_id}|${r.status}|${r.duration_sec ?? ''}|${r.rows_written ?? ''}|${r.completed_at ?? ''}`)
    .join(',')}`
}

/** One hidden component per pipeline: GET /pipelines/{id}/runs is per pipeline
 *  and a hook cannot loop over them. There is no fleet-level runs endpoint yet. */
export function CollectRuns({
  pipeline,
  onCollect,
}: {
  pipeline: PipelineConfig
  onCollect: (id: string, next: Collected) => void
}) {
  const { data, isLoading, isError } = useRuns(pipeline.pipeline_id)
  const sig = fingerprint(data)
  useEffect(() => {
    onCollect(pipeline.pipeline_id, { sig, data, loading: !!isLoading, failed: !!isError })
    // `data` is covered by `sig`; listing it would re-fire on every render of a mocked hook
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sig, isLoading, isError, pipeline.pipeline_id, onCollect])
  return null
}
