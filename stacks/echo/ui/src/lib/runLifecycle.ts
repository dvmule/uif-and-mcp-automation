import type { InfiniteData } from '@tanstack/react-query'
import type { RunMetrics, RunPage, RunStatus } from './types'

/** Glue JobRunState values that mean the job is still in flight. */
const IN_FLIGHT = new Set(['RUNNING', 'STARTING', 'STOPPING', 'WAITING'])

export function isInFlightStatus(status: string | null | undefined): boolean {
  return !!status && IN_FLIGHT.has(status.toUpperCase())
}

/** True when a manual/scheduled run is still in flight for this pipeline. */
export function isPipelineRunInFlight(
  lastRun: RunMetrics | undefined,
  activeRunId: string | undefined,
): boolean {
  if (activeRunId) return true
  return isInFlightStatus(lastRun?.status)
}

/** Map Glue job-run states to UI RunStatus. */
export function normalizeRunStatus(status: string | null | undefined): RunStatus {
  const s = (status ?? '').toUpperCase()
  if (IN_FLIGHT.has(s)) return 'RUNNING'
  if (s === 'SUCCEEDED') return 'SUCCEEDED'
  if (s === 'SKIPPED') return 'SKIPPED'
  if (s === 'WARNING') return 'WARNING'
  if (s === 'FAILED' || s === 'ERROR' || s === 'TIMEOUT') return 'FAILED'
  return 'FAILED'
}

/** Map replication Lambda `replication_status` to UI RunStatus. */
export function normalizeReplicationStatus(
  replicationStatus: string | null | undefined,
): RunStatus | null {
  const s = (replicationStatus ?? '').toLowerCase()
  if (!s) return null
  if (s === 'replicated') return 'SUCCEEDED'
  if (s === 'skipped') return 'SKIPPED'
  if (s === 'partial' || s === 'bq_failed') return 'WARNING'
  if (s === 'failed') return 'FAILED'
  return 'FAILED'
}

function isQualityGateFailure(run: Record<string, unknown>): boolean {
  const err = run.error_message
  return typeof err === 'string' && err.includes('Quality gate failed')
}

function isEmptyBronzeFailure(run: Record<string, unknown>): boolean {
  const err = run.error_message
  return typeof err === 'string' && (
    err.includes('Not found: Uris') ||
    err.includes('Empty Bronze:') ||
    err.includes('empty_bronze')
  )
}

function normalizeRunPhase(
  run: Record<string, unknown>,
): 'initial' | 'incremental' | null {
  const resolved = (run.phase ?? run.load_phase) as string | undefined
  if (resolved === 'initial' || resolved === 'incremental') return resolved
  return null
}

export function normalizeRunMetrics(run: Record<string, unknown>): RunMetrics {
  const raw = run.status as string | undefined
  let status = normalizeRunStatus(raw)
  if (!raw?.trim()) {
    const fromReplication = normalizeReplicationStatus(
      run.replication_status as string | undefined,
    )
    if (fromReplication) status = fromReplication
  }
  if (status === 'FAILED' && isQualityGateFailure(run)) status = 'WARNING'
  if (status === 'FAILED' && isEmptyBronzeFailure(run)) status = 'WARNING'
  const rowsRead =
    (run.rows_read as number | undefined) ??
    (run.rows_extracted as number | undefined) ??
    null
  const rowsWritten = (run.rows_written as number | undefined) ?? null
  return {
    ...(run as unknown as RunMetrics),
    status,
    rows_read: rowsRead,
    rows_written: rowsWritten,
    rows_extracted: rowsRead,
    new_watermark:
      (run.watermark_end as string | undefined) ??
      (run.new_watermark as string | undefined) ??
      null,
    phase: normalizeRunPhase(run),
  }
}

export const activeRunKey = (pipelineId: string) => ['active-run', pipelineId] as const

/** Prefer optimistic trigger id; else latest run from list/metrics cache. */
export function resolveInFlightRunId(
  activeRunId: string | undefined,
  ...candidates: (RunMetrics | undefined)[]
): string | undefined {
  if (activeRunId) return activeRunId
  for (const run of candidates) {
    if (run?.run_id && isInFlightStatus(run.status)) return run.run_id
  }
  return undefined
}

export function optimisticRunningRun(
  pipelineId: string,
  runId: string,
  startedAt?: string,
  jobName = 'bronze-ingest',
  phase?: 'initial' | 'incremental',
): RunMetrics {
  return {
    job_name: jobName,
    run_id: runId,
    pipeline_id: pipelineId,
    status: 'RUNNING',
    started_at: startedAt ?? new Date().toISOString(),
    ...(phase ? { phase } : {}),
  }
}

export function prependRunToInfinite(
  old: InfiniteData<RunPage> | undefined,
  run: RunMetrics,
): InfiniteData<RunPage> {
  if (!old?.pages?.length) {
    return { pages: [{ items: [run], next_token: null }], pageParams: [undefined] }
  }
  const pages = old.pages.map((page, i) => {
    if (i !== 0) return page
    const items = [run, ...page.items.filter((r) => r.run_id !== run.run_id)]
    return { ...page, items }
  })
  return { ...old, pages }
}

export function patchRunInInfinite(
  old: InfiniteData<RunPage> | undefined,
  run: RunMetrics,
): InfiniteData<RunPage> | undefined {
  if (!old?.pages?.length) return old
  const pages = old.pages.map((page) => ({
    ...page,
    items: page.items.map((r) => (r.run_id === run.run_id ? { ...r, ...run } : r)),
  }))
  return { ...old, pages }
}

/** List page cache (`['runs', pipelineId]`) — prepend optimistic / polled run. */
export function prependRunToRunPage(
  old: RunPage | undefined,
  run: RunMetrics,
): RunPage {
  const items = old?.items ?? []
  return {
    items: [run, ...items.filter((r) => r.run_id !== run.run_id)],
    next_token: old?.next_token ?? null,
  }
}

export function patchRunInRunPage(
  old: RunPage | undefined,
  run: RunMetrics,
): RunPage | undefined {
  if (!old?.items?.length) return old
  return {
    ...old,
    items: old.items.map((r) => (r.run_id === run.run_id ? { ...r, ...run } : r)),
  }
}

export function mergeRunIntoRunPage(
  old: RunPage | undefined,
  run: RunMetrics,
): RunPage {
  if (!old?.items?.some((r) => r.run_id === run.run_id)) {
    return prependRunToRunPage(old, run)
  }
  return patchRunInRunPage(old, run) ?? old
}

export function mergeRunIntoInfinite(
  old: InfiniteData<RunPage> | undefined,
  run: RunMetrics,
): InfiniteData<RunPage> {
  const exists = old?.pages?.some((p) => p.items.some((r) => r.run_id === run.run_id))
  if (!exists) return prependRunToInfinite(old, run)
  return patchRunInInfinite(old, run) ?? prependRunToInfinite(old, run)
}
