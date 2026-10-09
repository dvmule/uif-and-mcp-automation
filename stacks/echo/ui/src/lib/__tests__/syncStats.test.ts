import { describe, it, expect } from 'vitest'
import { computeSyncStats } from '../syncStats'
import type { PipelineConfig, RunMetrics } from '../types'

const hoursAgo = (h: number) => new Date(Date.now() - h * 3600_000).toISOString()

function pipe(id: string, over: Partial<PipelineConfig> = {}): PipelineConfig {
  return {
    pipeline_id: id, version: 'latest', enabled: true,
    source: { secret_arn: 'arn' }, extract: { table: id.toUpperCase(), mode: 'full' },
    target: { s3_path: 's3://x/' }, glue: { worker_type: 'G.1X', num_workers: 2, timeout_minutes: 60 },
    schedule: { frequency: 'daily' },
    ...over,
  } as PipelineConfig
}

function run(status: string, completedHoursAgo: number | null = 1): RunMetrics {
  return {
    job_name: 'bronze-ingest', run_id: `r-${status}`, pipeline_id: 'x',
    status: status as RunMetrics['status'],
    started_at: hoursAgo(completedHoursAgo ?? 0),
    completed_at: completedHoursAgo == null ? null : hoursAgo(completedHoursAgo),
  }
}

interface Case {
  name: string
  pipelines: PipelineConfig[]
  last: Record<string, RunMetrics | undefined>
  /** Pipelines whose runs have loaded (an empty list is still loaded). */
  loaded: string[]
  expected: Partial<ReturnType<typeof computeSyncStats>>
}

const CASES: Case[] = [
  {
    name: 'empty input is all zeros',
    pipelines: [], last: {}, loaded: [],
    expected: { total: 0, active: 0, disabled: 0, failing: 0, behind: 0, running: 0, pending: 0 },
  },
  {
    name: 'a healthy pipeline counts only as active',
    pipelines: [pipe('a')], last: { a: run('SUCCEEDED', 1) }, loaded: ['a'],
    expected: { total: 1, active: 1, failing: 0, behind: 0, running: 0 },
  },
  {
    name: 'a failed latest run is failing',
    pipelines: [pipe('a')], last: { a: run('FAILED', 1) }, loaded: ['a'],
    expected: { failing: 1, behind: 0 },
  },
  {
    name: 'a daily pipeline last run 30h ago is behind (stale)',
    pipelines: [pipe('a')], last: { a: run('SUCCEEDED', 30) }, loaded: ['a'],
    expected: { behind: 1, failing: 0 },
  },
  {
    name: 'a daily pipeline last run 60h ago is behind (critical)',
    pipelines: [pipe('a')], last: { a: run('SUCCEEDED', 60) }, loaded: ['a'],
    expected: { behind: 1 },
  },
  {
    name: 'an enabled pipeline that never ran, with runs loaded, is behind',
    pipelines: [pipe('a')], last: {}, loaded: ['a'],
    expected: { behind: 1, failing: 0 },
  },
  {
    name: 'a disabled pipeline is never behind or failing, even with a failed old run',
    pipelines: [pipe('a', { enabled: false })], last: { a: run('FAILED', 90) }, loaded: ['a'],
    expected: { active: 0, disabled: 1, failing: 0, behind: 0 },
  },
  {
    name: 'an in-flight run counts as running',
    pipelines: [pipe('a')], last: { a: run('RUNNING', null) }, loaded: ['a'],
    expected: { running: 1, failing: 0 },
  },
  {
    // Same rule as the Freshness column: it reads the last run's completed_at, which a
    // run in flight does not have yet, so the tile and the column agree.
    name: 'an in-flight run with no completed_at also reads as behind, like its Freshness badge',
    pipelines: [pipe('a')], last: { a: run('RUNNING', null) }, loaded: ['a'],
    expected: { running: 1, behind: 1 },
  },
  {
    name: 'a Glue STARTING state counts as running',
    pipelines: [pipe('a')], last: { a: run('STARTING', null) }, loaded: ['a'],
    expected: { running: 1 },
  },
  {
    name: 'runs not loaded yet: pending, and neither behind nor failing',
    pipelines: [pipe('a')], last: {}, loaded: [],
    expected: { active: 1, pending: 1, behind: 0, failing: 0, running: 0 },
  },
  {
    name: 'a mix adds up per tile',
    pipelines: [pipe('a'), pipe('b'), pipe('c', { enabled: false }), pipe('d'), pipe('e')],
    last: { a: run('SUCCEEDED', 1), b: run('FAILED', 2), d: run('RUNNING', null), e: run('SUCCEEDED', 40) },
    loaded: ['a', 'b', 'c', 'd', 'e'],
    expected: { total: 5, active: 4, disabled: 1, failing: 1, behind: 2, running: 1, pending: 0 },
  },
]

describe('computeSyncStats', () => {
  it.each(CASES)('$name', ({ pipelines, last, loaded, expected }) => {
    const lastMap = new Map(Object.entries(last))
    const runsMap = new Map<string, RunMetrics[]>(loaded.map(id => [id, []]))
    expect(computeSyncStats(pipelines, lastMap, runsMap)).toMatchObject(expected)
  })
})
