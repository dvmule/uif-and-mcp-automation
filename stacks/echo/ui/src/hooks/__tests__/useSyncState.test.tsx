import { describe, it, expect } from 'vitest'
import { renderHook } from '@testing-library/react'
import { useSyncState } from '@/hooks/useSyncState'
import type { PipelineConfig, RunMetrics } from '@/lib/types'

const basePipeline = {
  pipeline_id: 'p1', enabled: true,
  schedule: { frequency: 'hourly' },
  extract: { initial: {}, incremental: { mode: 'incremental' } },
} as unknown as PipelineConfig

function run(
  status: RunMetrics['status'],
  started: string,
  dur = 180,
  rows = 2_400_000,
  jobName = 'echo-dev-bronze-ingest',
): RunMetrics {
  return {
    run_id: `r-${started}`,
    job_name: jobName,
    pipeline_id: 'p1',
    status,
    started_at: started,
    duration_sec: dur,
    rows_extracted: rows,
  } as RunMetrics
}

describe('useSyncState', () => {
  it('returns healthy when last run succeeded and success rate >= 80%', () => {
    const runs = [run('SUCCEEDED', '2026-06-17T13:00:00Z')]
    const { result } = renderHook(() => useSyncState(basePipeline, runs))
    expect(result.current.status).toBe('healthy')
    expect(result.current.successRate30d).toBeGreaterThanOrEqual(0.8)
  })

  it('returns degraded (not failing) when last run is a quality-gate warning', () => {
    const runs = [run('WARNING', '2026-06-17T13:00:00Z', 42, 0)]
    const { result } = renderHook(() => useSyncState(basePipeline, runs))
    expect(result.current.status).toBe('degraded')
  })

  it('returns failing when last run failed', () => {
    const runs = [run('FAILED', '2026-06-17T13:00:00Z')]
    const { result } = renderHook(() => useSyncState(basePipeline, runs))
    expect(result.current.status).toBe('failing')
  })

  it('stays healthy when newest rows are replicator failures but ingest succeeded', () => {
    const runs = [
      run('FAILED', '2026-08-06T19:45:14Z', 4, 0, 'echo-dev-silver-bq-replicator'),
      run('SUCCEEDED', '2026-08-06T18:32:54Z', 713, 6_865_921, 'echo-dev-silver-transform'),
      run('SUCCEEDED', '2026-08-06T18:14:56Z', 1106, 6_865_921, 'echo-dev-bronze-ingest'),
    ]
    const { result } = renderHook(() => useSyncState(basePipeline, runs))
    expect(result.current.status).toBe('healthy')
    expect(result.current.lastRun?.job_name).toBe('echo-dev-silver-transform')
  })

  it('returns degraded (not failing) when last succeeded by accident after 3 prior fails', () => {
    // last=SUCCEEDED suppresses 'failing' (which requires lastFailed OR 3 leading fails).
    // But 3 fails in last 10 still trips the degraded threshold.
    const runs = [
      run('SUCCEEDED', '2026-06-17T13:30:00Z'),
      run('FAILED', '2026-06-17T13:00:00Z'),
      run('FAILED', '2026-06-17T12:30:00Z'),
      run('FAILED', '2026-06-17T12:00:00Z'),
    ]
    const { result } = renderHook(() => useSyncState(basePipeline, runs))
    expect(result.current.status).toBe('degraded')
  })

  it('returns degraded when last OK but >=2 fails in last 10', () => {
    const runs = [
      run('SUCCEEDED', '2026-06-17T13:00:00Z'),
      run('FAILED',    '2026-06-17T12:30:00Z'),
      run('FAILED',    '2026-06-17T12:00:00Z'),
      ...Array.from({ length: 7 }, (_, i) => run('SUCCEEDED', `2026-06-17T0${i}:00:00Z`)),
    ]
    const { result } = renderHook(() => useSyncState(basePipeline, runs))
    expect(result.current.status).toBe('degraded')
  })

  it('returns disabled when pipeline.enabled is false', () => {
    const { result } = renderHook(() => useSyncState({ ...basePipeline, enabled: false }, []))
    expect(result.current.status).toBe('disabled')
  })

  it('returns running when latest run status is RUNNING', () => {
    const { result } = renderHook(() => useSyncState(basePipeline, [run('RUNNING', '2026-06-17T13:00:00Z')]))
    expect(result.current.status).toBe('running')
  })

  it('returns running when latest run status is STARTING (Glue)', () => {
    const { result } = renderHook(() => useSyncState(basePipeline, [run('STARTING' as RunMetrics['status'], '2026-06-17T13:00:00Z')]))
    expect(result.current.status).toBe('running')
  })

  it('marks stale=true when last_run_at older than 3x schedule interval', () => {
    const old = new Date(Date.now() - 1000 * 60 * 60 * 4).toISOString() // 4h ago, freq hourly (60min) → 4h > 3h → stale
    const { result } = renderHook(() => useSyncState(basePipeline, [run('SUCCEEDED', old)]))
    expect(result.current.stale).toBe(true)
  })

  it('handles real frequency strings (hourly/daily/weekly)', () => {
    const old = new Date(Date.now() - 1000 * 60 * 60 * 4).toISOString() // 4h ago
    // hourly: 4h ago is > 3 × 1h = 3h → stale
    const hourly = renderHook(() => useSyncState({ ...basePipeline, schedule: { frequency: 'hourly' } } as any, [run('SUCCEEDED', old)]))
    expect(hourly.result.current.stale).toBe(true)
    // daily: 4h ago is < 3 × 24h = 72h → not stale
    const daily = renderHook(() => useSyncState({ ...basePipeline, schedule: { frequency: 'daily' } } as any, [run('SUCCEEDED', old)]))
    expect(daily.result.current.stale).toBe(false)
  })

  it('treats reconcile frequency as on-demand (never stale, no nextRunInMin)', () => {
    const old = new Date(Date.now() - 1000 * 60 * 60 * 24 * 30).toISOString() // 30 days ago
    const { result } = renderHook(() => useSyncState({ ...basePipeline, schedule: { frequency: 'reconcile' } } as any, [run('SUCCEEDED', old)]))
    expect(result.current.stale).toBe(false)
    expect(result.current.nextRunInMin).toBeNull()
  })
})
