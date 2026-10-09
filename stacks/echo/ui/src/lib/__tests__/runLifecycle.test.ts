import { describe, it, expect } from 'vitest'
import {
  isInFlightStatus,
  normalizeRunStatus,
  normalizeReplicationStatus,
  normalizeRunMetrics,
  prependRunToInfinite,
  optimisticRunningRun,
  resolveInFlightRunId,
  isPipelineRunInFlight,
} from '@/lib/runLifecycle'

describe('runLifecycle', () => {
  it('treats Glue in-flight states as running', () => {
    expect(isInFlightStatus('RUNNING')).toBe(true)
    expect(isInFlightStatus('STARTING')).toBe(true)
    expect(isInFlightStatus('SUCCEEDED')).toBe(false)
  })

  it('normalizes Glue terminal states to UI RunStatus', () => {
    expect(normalizeRunStatus('STARTING')).toBe('RUNNING')
    expect(normalizeRunStatus('SUCCEEDED')).toBe('SUCCEEDED')
    expect(normalizeRunStatus('WARNING')).toBe('WARNING')
    expect(normalizeRunStatus('ERROR')).toBe('FAILED')
  })

  it('maps Bronze load_phase into phase when phase is absent', () => {
    const run = normalizeRunMetrics({
      run_id: 'jr_bronze',
      status: 'SUCCEEDED',
      load_phase: 'initial',
    })
    expect(run.phase).toBe('initial')
  })

  it('prefers explicit phase over load_phase', () => {
    const run = normalizeRunMetrics({
      run_id: 'jr_silver',
      status: 'SUCCEEDED',
      phase: 'incremental',
      load_phase: 'initial',
    })
    expect(run.phase).toBe('incremental')
  })

  it('maps rows_read and rows_written from DynamoDB metrics', () => {
    const run = normalizeRunMetrics({
      run_id: 'jr_1',
      status: 'SUCCEEDED',
      rows_read: 1000,
      rows_written: 990,
    })
    expect(run.rows_read).toBe(1000)
    expect(run.rows_written).toBe(990)
    expect(run.rows_extracted).toBe(1000)
  })

  it('maps legacy quality-gate FAILED metrics to WARNING', () => {
    const run = normalizeRunMetrics({
      run_id: 'jr_1',
      status: 'FAILED',
      error_message: 'Quality gate failed: got 0 rows, expected >= 1',
    })
    expect(run.status).toBe('WARNING')
  })

  it('maps empty Bronze Silver GCS 404 FAILED metrics to WARNING', () => {
    const run = normalizeRunMetrics({
      run_id: 'jr_2',
      status: 'FAILED',
      error_message: 'google.api_core.exceptions.NotFound: 404 Not found: Uris gs://enterprise-ipm-bronze-dev/foo/*',
    })
    expect(run.status).toBe('WARNING')
  })

  it('prepends optimistic run to infinite runs cache', () => {
    const run = optimisticRunningRun('p1', 'jr_abc')
    const next = prependRunToInfinite(undefined, run)
    expect(next.pages[0].items[0].run_id).toBe('jr_abc')
    expect(next.pages[0].items[0].status).toBe('RUNNING')
  })

  it('resolveInFlightRunId prefers active trigger over list cache', () => {
    const latest = optimisticRunningRun('p1', 'jr_list')
    expect(resolveInFlightRunId('jr_active', latest)).toBe('jr_active')
    expect(resolveInFlightRunId(undefined, latest)).toBe('jr_list')
    expect(resolveInFlightRunId(undefined, { ...latest, status: 'SUCCEEDED' })).toBeUndefined()
  })

  it('isPipelineRunInFlight is true for active run id or running status', () => {
    const run = optimisticRunningRun('p1', 'jr_1')
    expect(isPipelineRunInFlight(run, undefined)).toBe(true)
    expect(isPipelineRunInFlight(undefined, 'jr_1')).toBe(true)
    expect(isPipelineRunInFlight({ ...run, status: 'SUCCEEDED' }, undefined)).toBe(false)
  })
})

describe('normalizeRunMetrics replication', () => {
  it('maps replication_status when Glue status is absent', () => {
    const run = normalizeRunMetrics({
      job_name: 'echo-dev-gcs-replicator',
      run_id: 'r1',
      pipeline_id: 'p1',
      started_at: '2026-08-06T18:33:42Z',
      replication_status: 'replicated',
    })
    expect(run.status).toBe('SUCCEEDED')
  })

  it('maps failed replication_status', () => {
    const run = normalizeRunMetrics({
      job_name: 'echo-dev-silver-bq-replicator',
      run_id: 'r2',
      pipeline_id: 'p1',
      started_at: '2026-08-06T18:45:32Z',
      replication_status: 'failed',
      error_message: 'metadata_rewrite: no metadata',
    })
    expect(run.status).toBe('FAILED')
  })

  it('prefers explicit Glue status over replication_status', () => {
    const run = normalizeRunMetrics({
      job_name: 'echo-dev-bronze-ingest',
      run_id: 'r3',
      pipeline_id: 'p1',
      started_at: '2026-08-06T18:14:56Z',
      status: 'SUCCEEDED',
      replication_status: 'failed',
    })
    expect(run.status).toBe('SUCCEEDED')
  })
})

describe('normalizeReplicationStatus', () => {
  it('maps known replication outcomes', () => {
    expect(normalizeReplicationStatus('replicated')).toBe('SUCCEEDED')
    expect(normalizeReplicationStatus('bq_failed')).toBe('WARNING')
    expect(normalizeReplicationStatus('partial')).toBe('WARNING')
    expect(normalizeReplicationStatus('skipped')).toBe('SKIPPED')
    expect(normalizeReplicationStatus('failed')).toBe('FAILED')
    expect(normalizeReplicationStatus(null)).toBeNull()
  })
})
