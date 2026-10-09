import { describe, expect, it } from 'vitest'
import { silverTableFqn } from '@/lib/cloud'
import { latestLayerRun, pipelineRowSummary } from '@/lib/runRowCounts'
import type { RunMetrics } from '@/lib/types'

describe('runRowCounts', () => {
  it('pairs bronze source with silver target', () => {
    const runs: RunMetrics[] = [
      {
        job_name: 'echo-dev-silver-bq-native',
        run_id: 's1',
        pipeline_id: 'p1',
        status: 'SUCCEEDED',
        started_at: '2026-07-17T10:00:00Z',
        rows_read: 100,
        rows_written: 95,
      },
      {
        job_name: 'echo-dev-bronze-ingest',
        run_id: 'b1',
        pipeline_id: 'p1',
        status: 'SUCCEEDED',
        started_at: '2026-07-17T09:00:00Z',
        rows_read: 1_000,
        rows_written: 1_000,
      },
    ]
    expect(latestLayerRun(runs, 'bronze')?.run_id).toBe('b1')
    expect(latestLayerRun(runs, 'silver')?.run_id).toBe('s1')
    expect(pipelineRowSummary(runs)).toEqual({ source: 1_000, target: 95 })
  })

  it('does not treat silver-bq-replicator as a silver ingest run', () => {
    const runs: RunMetrics[] = [
      {
        job_name: 'echo-dev-silver-bq-replicator',
        run_id: 'rep1',
        pipeline_id: 'p1',
        status: 'SUCCEEDED',
        started_at: '2026-08-06T19:45:14Z',
        rows_written: 0,
      },
      {
        job_name: 'echo-dev-silver-transform',
        run_id: 's1',
        pipeline_id: 'p1',
        status: 'SUCCEEDED',
        started_at: '2026-08-06T18:32:54Z',
        rows_read: 100,
        rows_written: 95,
      },
    ]
    expect(latestLayerRun(runs, 'silver')?.run_id).toBe('s1')
  })
})

describe('silverTableFqn', () => {
  it('uses bq_project.bq_dataset.table for GCP bq_native', () => {
    const fqn = silverTableFqn({
      pipeline_id: 'p1',
      version: 'latest',
      enabled: true,
      source: { secret_arn: 'x' },
      extract: { table: 'FPI_REP_DEA_FAB3_V', mode: 'incremental' },
      target: {},
      glue: { worker_type: 'G.1X', num_workers: 2, timeout_minutes: 60 },
      schedule: { frequency: 'daily' },
      destination: { cloud: 'gcp' },
      silver: {
        enabled: true,
        bq_project: 'my-proj',
        bq_dataset: 'silver_ds',
        table: 'FPI_REP_DEA_FAB3_V',
        primary_key_cols: ['ID'],
      },
    })
    expect(fqn).toBe('my-proj.silver_ds.FPI_REP_DEA_FAB3_V')
  })
})
