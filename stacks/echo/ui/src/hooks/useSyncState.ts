import { isInFlightStatus } from '@/lib/runLifecycle'
import { isIngestJob } from '@/lib/runKindLabel'
import { minutesUntilNextDailySlot, resolveDailySlot } from '@/lib/dailySchedule'
import type { Frequency, PipelineConfig, RunMetrics } from '@/lib/types'
export type SyncStatus = 'healthy' | 'degraded' | 'failing' | 'running' | 'disabled'

export interface SyncState {
  status: SyncStatus
  stale: boolean
  lastRun: RunMetrics | null
  successRate30d: number
  trend: 'up' | 'down' | 'flat'
  nextRunInMin: number | null
}

// Keyed by the real `Frequency` union from lib/types. `reconcile` is on-demand
// (no fixed cadence), so it maps to null and is treated as never-stale.
const FREQ_MINUTES: Record<Frequency, number | null> = {
  hourly: 60,
  daily: 1440,
  weekly: 10080,
  reconcile: null,
}

export function useSyncState(pipeline: PipelineConfig | undefined, runs: RunMetrics[]): SyncState {
  if (!pipeline) {
    return { status: 'disabled', stale: false, lastRun: null, successRate30d: 0, trend: 'flat', nextRunInMin: null }
  }
  const ingestOnly = filterIngestRuns(runs)

  if (!pipeline.enabled) {
    return {
      status: 'disabled',
      stale: false,
      lastRun: latestIngestRun(runs),
      successRate30d: rateOf(ingestOnly),
      trend: 'flat',
      nextRunInMin: null,
    }
  }

  const lastRun = latestIngestRun(runs)
  if (lastRun && isInFlightStatus(lastRun.status)) {
    return {
      status: 'running',
      stale: false,
      lastRun,
      successRate30d: rateOf(ingestOnly),
      trend: 'flat',
      nextRunInMin: null,
    }
  }

  const consecutiveFails = countLeadingFails(ingestOnly)
  const failsInLast10 = ingestOnly.slice(0, 10).filter(r => r.status === 'FAILED').length
  const lastFailed = lastRun?.status === 'FAILED'

  let status: SyncStatus = 'healthy'
  if (lastFailed || consecutiveFails >= 3) status = 'failing'
  else if (lastRun?.status === 'WARNING' || failsInLast10 >= 2) status = 'degraded'

  const stale = isStale(pipeline, lastRun)
  const successRate30d = rateOf(ingestOnly)
  const trend = trendOf(ingestOnly)
  const nextRunInMin = nextRunIn(pipeline, lastRun)

  return { status, stale, lastRun, successRate30d, trend, nextRunInMin }
}

function filterIngestRuns(runs: RunMetrics[]): RunMetrics[] {
  return runs.filter(r => isIngestJob(r.job_name ?? ''))
}

function latestIngestRun(runs: RunMetrics[]): RunMetrics | null {
  const ingest = filterIngestRuns(runs)
  if (!ingest.length) return null
  return [...ingest].sort(
    (a, b) => (b.started_at ?? '').localeCompare(a.started_at ?? ''),
  )[0]
}

function countLeadingFails(runs: RunMetrics[]): number {
  let n = 0
  for (const r of runs) { if (r.status === 'FAILED') n++; else break }
  return n
}

function rateOf(runs: RunMetrics[]): number {
  if (runs.length === 0) return 0
  const recent = runs.slice(0, 30)
  return recent.filter(r => r.status === 'SUCCEEDED').length / recent.length
}

function isStale(p: PipelineConfig, last: RunMetrics | null): boolean {
  if (!last?.started_at) return false
  const freq = p.schedule?.frequency
  const freqMin = freq ? FREQ_MINUTES[freq] : undefined
  // null = on-demand (reconcile) → can't be stale without a cadence.
  // undefined = unknown frequency → also treat as not-stale.
  if (freqMin == null) return false
  const ageMin = (Date.now() - new Date(last.started_at).getTime()) / 60000
  return ageMin > freqMin * 3
}

function trendOf(runs: RunMetrics[]): 'up' | 'down' | 'flat' {
  if (runs.length < 4) return 'flat'
  const recentDur = avg(runs.slice(0, 5).map(r => r.duration_sec ?? 0))
  const olderDur  = avg(runs.slice(5, 15).map(r => r.duration_sec ?? 0))
  if (olderDur === 0) return 'flat'
  const delta = (recentDur - olderDur) / olderDur
  if (delta < -0.1) return 'up'      // faster
  if (delta >  0.1) return 'down'    // slower
  return 'flat'
}
function avg(xs: number[]): number { return xs.length === 0 ? 0 : xs.reduce((a,b) => a+b, 0) / xs.length }

function nextRunIn(p: PipelineConfig, last: RunMetrics | null): number | null {
  const freq = p.schedule?.frequency
  if (freq === 'daily') {
    return minutesUntilNextDailySlot(resolveDailySlot(p.schedule))
  }
  const freqMin = freq ? FREQ_MINUTES[freq] : undefined
  // null (reconcile = on-demand) or undefined (unknown) → no projected next run.
  if (freqMin == null || !last?.started_at) return null
  const ageMin = (Date.now() - new Date(last.started_at).getTime()) / 60000
  return Math.max(0, Math.round(freqMin - ageMin))
}
