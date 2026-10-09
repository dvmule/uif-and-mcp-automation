import type { PipelineConfig, PipelineStatus, RunMetrics } from './types'
import { freshness, parseDateMs } from './utils'
import type { SyncFilterState } from './filters'
import { isBronzeJob, isIngestJob, isReconcileJob, isReplicatorJob, isSilverJob } from './runKindLabel'
import { isInFlightStatus } from './runLifecycle'

function byStartedDesc(a: RunMetrics, b: RunMetrics): number {
  return (parseDateMs(b.started_at) ?? 0) - (parseDateMs(a.started_at) ?? 0)
}

function latestRun(runs: RunMetrics[], pred: (r: RunMetrics) => boolean): RunMetrics | undefined {
  return runs.filter(pred).sort(byStartedDesc)[0]
}

/** Row status from latest Bronze/Silver only — reconcile jobs are auxiliary. */
export function derivePipelineListStatus(
  p: PipelineConfig,
  runs: RunMetrics[] | undefined,
): PipelineStatus {
  if (!p.enabled) return 'DISABLED'
  if (!runs?.length) return 'SKIPPED'

  // Optimistic trigger rows may lack job_name until Glue responds; still in-flight.
  if (runs.some(r => isInFlightStatus(r.status) && isIngestJob(r.job_name ?? ''))) {
    return 'RUNNING'
  }

  const ingestRuns = runs.filter(
    r => isBronzeJob(r.job_name ?? '') || isSilverJob(r.job_name ?? ''),
  )
  if (ingestRuns.some(r => isInFlightStatus(r.status))) return 'RUNNING'

  const bronze = latestRun(runs, r => isBronzeJob(r.job_name ?? ''))
  const silver = latestRun(runs, r => isSilverJob(r.job_name ?? ''))

  if (!bronze && !silver) return 'SKIPPED'

  if (bronze?.status === 'WARNING') return 'WARNING'
  if (bronze?.status === 'FAILED') return 'FAILED'

  if (bronze?.status === 'SUCCEEDED') {
    if (p.silver?.enabled) {
      if (silver?.status === 'FAILED') return 'FAILED'
      if (silver?.status === 'WARNING') return 'WARNING'
    }
    return 'SUCCEEDED'
  }

  if (silver) return (silver.status ?? 'SKIPPED') as PipelineStatus
  return 'SKIPPED'
}

/** Last Run column: latest Bronze/Silver ingest, not replicate/reconcile ticks. */
export function pickDisplayLastRun(runs: RunMetrics[] | undefined): RunMetrics | undefined {
  if (!runs?.length) return undefined
  const ingest = runs.filter(r => isIngestJob(r.job_name ?? ''))
  const pool = ingest.length
    ? ingest
    : runs.filter(r => !isReplicatorJob(r.job_name ?? '') && !isReconcileJob(r.job_name ?? ''))
  return pool.length ? [...pool].sort(byStartedDesc)[0] : undefined
}

// Compute the row-level Status the same way Syncs renders it. Keeping this
// in one place ensures the filter dropdown matches what the user sees in
// the Status column.
export function pipelineStatus(
  p: PipelineConfig,
  lastRun: RunMetrics | undefined,
  allRuns?: RunMetrics[],
): PipelineStatus {
  if (!p.enabled) return 'DISABLED'
  if (allRuns?.length) return derivePipelineListStatus(p, allRuns)
  return (lastRun?.status ?? 'SKIPPED') as PipelineStatus
}

function passes<T>(selected: T[], value: T): boolean {
  // Empty selection means "no filter" → every row passes.
  return selected.length === 0 || selected.includes(value)
}

export function matchesAll(
  p: PipelineConfig,
  lastRun: RunMetrics | undefined,
  state: SyncFilterState,
  allRuns?: RunMetrics[],
): boolean {
  if (!passes(state.domain,    p.metadata?.domain ?? 'Other'))             return false
  if (!passes(state.source,    p.metadata?.source_system ?? '—'))          return false
  if (!passes(state.status,    pipelineStatus(p, lastRun, allRuns)))       return false
  if (!passes(state.freshness, freshness(p, lastRun?.completed_at)))       return false

  // Tags: row passes if ANY of its tags matches ANY selected value (OR).
  if (state.tag.length > 0) {
    const rowTags = p.metadata?.tags ?? []
    if (!rowTags.some(t => state.tag.includes(t))) return false
  }
  return true
}
