import { describe, it, expect } from 'vitest'
import {
  derivePipelineListStatus,
  pickDisplayLastRun,
  pipelineStatus,
} from '@/lib/filterMatchers'
import type { PipelineConfig, RunMetrics } from '@/lib/types'

const basePipeline = (overrides: Partial<PipelineConfig> = {}): PipelineConfig => ({
  pipeline_id: 'p1',
  version: 'latest',
  enabled: true,
  extract: { table: 'APPS.X', mode: 'incremental' },
  target: { s3_path: 's3://b/x/', compression: 'snappy' },
  silver: { enabled: true, database: 'db', table: 't', s3_path: 's3://s/x/', primary_key_cols: ['ID'] },
  ...overrides,
} as PipelineConfig)

const run = (partial: Partial<RunMetrics> & Pick<RunMetrics, 'job_name' | 'status' | 'started_at'>): RunMetrics => ({
  run_id: 'jr',
  pipeline_id: 'p1',
  ...partial,
})

describe('derivePipelineListStatus', () => {
  it('shows WARNING when latest bronze is quality-gate warning despite newer reconcile FAILED', () => {
    const p = basePipeline()
    const runs = [
      run({ job_name: 'echo-dev-silver-reconcile', status: 'FAILED', started_at: '2026-07-22T11:01:23Z' }),
      run({ job_name: 'echo-dev-bronze-ingest', status: 'WARNING', started_at: '2026-07-22T10:31:40Z' }),
    ]
    expect(derivePipelineListStatus(p, runs)).toBe('WARNING')
  })

  it('shows SUCCEEDED when bronze/silver ok despite newer reconcile SKIPPED (dry-run)', () => {
    const p = basePipeline()
    const runs = [
      run({ job_name: 'echo-dev-silver-reconcile', status: 'SKIPPED', started_at: '2026-07-22T08:30:49Z' }),
      run({ job_name: 'echo-dev-bronze-ingest', status: 'SUCCEEDED', started_at: '2026-07-22T08:01:12Z' }),
      run({ job_name: 'echo-dev-silver-transform', status: 'SUCCEEDED', started_at: '2026-07-21T18:43:52Z' }),
    ]
    expect(derivePipelineListStatus(p, runs)).toBe('SUCCEEDED')
  })

  it('returns SKIPPED when no ingest runs exist', () => {
    const p = basePipeline()
    expect(derivePipelineListStatus(p, [])).toBe('SKIPPED')
  })

  it('shows RUNNING for optimistic trigger row before Glue returns job_name', () => {
    const p = basePipeline()
    const runs = [
      run({ job_name: '', status: 'RUNNING', started_at: '2026-07-24T17:00:00Z' }),
    ]
    expect(derivePipelineListStatus(p, runs)).toBe('RUNNING')
  })

  it('shows RUNNING for optimistic bronze-ingest placeholder', () => {
    const p = basePipeline()
    const runs = [
      run({ job_name: 'bronze-ingest', status: 'RUNNING', started_at: '2026-07-24T17:00:00Z' }),
    ]
    expect(derivePipelineListStatus(p, runs)).toBe('RUNNING')
  })

  it('does not show RUNNING for in-flight reconcile-only activity', () => {
    const p = basePipeline()
    const runs = [
      run({ job_name: 'echo-dev-silver-reconcile', status: 'RUNNING', started_at: '2026-07-24T17:00:00Z' }),
      run({ job_name: 'echo-dev-bronze-ingest', status: 'SUCCEEDED', started_at: '2026-07-22T08:01:12Z' }),
    ]
    expect(derivePipelineListStatus(p, runs)).toBe('SUCCEEDED')
  })
})

describe('pickDisplayLastRun', () => {
  it('prefers latest bronze/silver over reconcile for Last Run column', () => {
    const runs = [
      run({ job_name: 'echo-dev-silver-reconcile', status: 'SKIPPED', started_at: '2026-07-22T08:30:49Z' }),
      run({ job_name: 'echo-dev-bronze-ingest', status: 'SUCCEEDED', started_at: '2026-07-22T08:01:12Z' }),
    ]
    expect(pickDisplayLastRun(runs)?.job_name).toBe('echo-dev-bronze-ingest')
  })

  it('prefers latest bronze/silver over replicator rows', () => {
    const runs = [
      run({ job_name: 'echo-dev-silver-bq-replicator', status: 'SUCCEEDED', started_at: '2026-08-06T19:45:14Z' }),
      run({ job_name: 'echo-dev-gcs-replicator', status: 'SUCCEEDED', started_at: '2026-08-06T18:33:42Z' }),
      run({ job_name: 'echo-dev-silver-transform', status: 'SUCCEEDED', started_at: '2026-08-06T18:32:54Z' }),
      run({ job_name: 'echo-dev-bronze-ingest', status: 'SUCCEEDED', started_at: '2026-08-06T18:14:56Z' }),
    ]
    expect(pickDisplayLastRun(runs)?.job_name).toBe('echo-dev-silver-transform')
  })
})

describe('pipelineStatus', () => {
  it('uses allRuns when provided', () => {
    const p = basePipeline()
    const runs = [
      run({ job_name: 'echo-dev-silver-reconcile', status: 'FAILED', started_at: '2026-07-22T11:00:00Z' }),
      run({ job_name: 'echo-dev-bronze-ingest', status: 'WARNING', started_at: '2026-07-22T10:00:00Z' }),
    ]
    expect(pipelineStatus(p, runs[0], runs)).toBe('WARNING')
  })
})
