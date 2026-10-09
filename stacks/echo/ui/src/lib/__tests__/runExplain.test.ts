import { describe, expect, it } from 'vitest'
import { explainRun } from '@/lib/runExplain'
import { goldRunRow, pipelineRunRow, type RunRow } from '@/lib/runsTable'
import type { GoldRunMetrics, PipelineConfig, RunMetrics } from '@/lib/types'

const pipeline = { pipeline_id: 'p1', extract: { table: 'X' } } as unknown as PipelineConfig

const pRow = (over: Partial<RunMetrics> = {}): RunRow =>
  pipelineRunRow(pipeline, {
    job_name: 'uif-echo-dev-bronze-ingest',
    run_id: 'jr_1',
    pipeline_id: 'p1',
    status: 'SUCCEEDED',
    started_at: '2026-09-29T11:59:00Z',
    rows_read: 100,
    rows_written: 100,
    ...over,
  })

const gRow = (over: Partial<GoldRunMetrics> = {}): RunRow =>
  goldRunRow({
    job_name: 'uif-echo-dev-gold-transform',
    run_id: 'jr_g',
    gold_id: 'price',
    status: 'SUCCEEDED',
    started_at: '2026-09-29T06:31:00Z',
    rows_read: 10,
    rows_written: 4,
    ...over,
  })

describe('explainRun', () => {
  const cases: Array<[string, RunRow, { pattern: string | null; tone: string; headline: RegExp }]> = [
    ['oracle login refused', pRow({ status: 'FAILED', error_message: 'ORA-01017: invalid username/password; logon denied' }),
      { pattern: 'ORA-01017', tone: 'fail', headline: /refused the sync's login/ }],
    ['login refused, worded without the code', pRow({ status: 'FAILED', error_message: 'invalid username/password' }),
      { pattern: 'ORA-01017', tone: 'fail', headline: /refused/ }],
    ['quality gate (failed)', pRow({ status: 'FAILED', error_message: 'Quality gate failed: min_rows' }),
      { pattern: 'quality gate', tone: 'fail', headline: /quality gate/i }],
    ['quality gate (warning)', pRow({ status: 'WARNING', error_message: 'Quality gate failed: nulls' }),
      { pattern: 'quality gate', tone: 'warn', headline: /quality gate/i }],
    ['quality gate by row count', pRow({ status: 'FAILED', rows_quality_failed: 5, error_message: 'boom' }),
      { pattern: 'quality gate', tone: 'fail', headline: /quality gate/i }],
    ['timeout', pRow({ status: 'FAILED', error_message: 'Job run timed out after 60 minutes' }),
      { pattern: 'timeout', tone: 'fail', headline: /too long/ }],
    ['glue TIMEOUT state', pRow({ status: 'TIMEOUT' as never, error_message: 'Timeout' }),
      { pattern: 'timeout', tone: 'fail', headline: /too long/ }],
    ['missing source', pRow({ status: 'FAILED', error_message: 'ORA-00942: table or view does not exist' }),
      { pattern: 'missing source', tone: 'fail', headline: /not found/ }],
    ['unrecognised failure', pRow({ status: 'FAILED', error_message: 'java.lang.OutOfMemoryError' }),
      { pattern: null, tone: 'fail', headline: /^Unrecognised error$/ }],
    ['failure with no message at all', pRow({ status: 'FAILED', error_message: null }),
      { pattern: null, tone: 'fail', headline: /^Unrecognised error$/ }],
    ['warning that matches nothing', pRow({ status: 'WARNING', error_message: 'something odd' }),
      { pattern: null, tone: 'warn', headline: /warning/i }],
    ['stopped by an operator', pRow({ status: 'STOPPED' as never }),
      { pattern: null, tone: 'warn', headline: /stopped/i }],
    ['running', pRow({ status: 'RUNNING' }),
      { pattern: 'running', tone: 'mute', headline: /still going/ }],
    ['skipped (pipeline)', pRow({ status: 'SKIPPED' }),
      { pattern: 'skipped', tone: 'mute', headline: /Skipped on purpose\.$/ }],
    ['skipped (gold)', gRow({ status: 'SKIPPED' }),
      { pattern: 'skipped', tone: 'mute', headline: /no new silver changes/ }],
    ['success that read nothing', pRow({ rows_read: 0, rows_written: 0 }),
      { pattern: 'no new rows', tone: 'ok', headline: /Nothing new to read/ }],
    ['success with rows', pRow({ rows_read: 42, rows_written: 42 }),
      { pattern: null, tone: 'ok', headline: /^Completed with 42 rows\.$/ }],
    ['success with rows but some failed quality', pRow({ rows_quality_failed: 3 }),
      { pattern: 'quality gate', tone: 'warn', headline: /some rows failed/ }],
    ['gold success that changed nothing', gRow({ rows_written: 0 }),
      { pattern: null, tone: 'ok', headline: /No rows changed/ }],
    ['gold success', gRow({ rows_written: 4 }),
      { pattern: null, tone: 'ok', headline: /^Completed with 4 rows\.$/ }],
  ]

  it.each(cases)('%s', (_name, row, want) => {
    const out = explainRun(row)
    expect(out.headline).toMatch(want.headline)
    expect(out.pattern).toBe(want.pattern)
    expect(out.tone).toBe(want.tone)
    expect(out.detail.length).toBeGreaterThan(0)
  })

  it('the unrecognised case points at the technical detail and never guesses', () => {
    expect(explainRun(pRow({ status: 'FAILED', error_message: 'weird' })).detail).toBe('See technical detail below.')
  })

  it('credentials win over the quality word when both appear', () => {
    const out = explainRun(pRow({ status: 'FAILED', error_message: 'ORA-01017 while running quality check' }))
    expect(out.pattern).toBe('ORA-01017')
  })

  it('a running run is running even if it carries an old error message', () => {
    expect(explainRun(pRow({ status: 'RUNNING', error_message: 'ORA-01017' })).pattern).toBe('running')
  })
})
