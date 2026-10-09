import { isPipelineRunInFlight, normalizeRunStatus } from './runLifecycle'
import { freshness } from './utils'
import type { PipelineConfig, RunMetrics } from './types'

export interface SyncStats {
  total: number
  active: number
  disabled: number
  failing: number
  behind: number
  running: number
  /** Pipelines whose runs have not arrived yet: failing and behind cannot be judged for them. */
  pending: number
}

/** The Replicate stat strip, computed from data the page already holds.
 *  A pipeline with no entry in `runsByPipeline` has not loaded its runs yet:
 *  it counts as pending, never as behind or failing. A disabled pipeline is
 *  never failing or behind - its row says Disabled. */
export function computeSyncStats(
  pipelines: PipelineConfig[],
  lastRunByPipeline: Map<string, RunMetrics | undefined>,
  runsByPipeline: Map<string, RunMetrics[]>,
): SyncStats {
  const stats: SyncStats = {
    total: pipelines.length, active: 0, disabled: 0, failing: 0, behind: 0, running: 0, pending: 0,
  }
  for (const p of pipelines) {
    if (p.enabled) stats.active += 1
    else stats.disabled += 1

    const loaded = runsByPipeline.has(p.pipeline_id)
    if (!loaded) stats.pending += 1
    const lastRun = lastRunByPipeline.get(p.pipeline_id)

    if (loaded && isPipelineRunInFlight(lastRun, undefined)) stats.running += 1
    if (!p.enabled || !loaded) continue

    if (lastRun && normalizeRunStatus(lastRun.status) === 'FAILED') stats.failing += 1
    const level = freshness(p, lastRun?.completed_at)
    if (level === 'stale' || level === 'critical') stats.behind += 1
  }
  return stats
}
