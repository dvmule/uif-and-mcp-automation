import { describe, it, expect } from 'vitest'
import {
  clockAndAgo,
  failedKpiHint,
  goldBookmarkWord,
  goldLastSuccessFact,
  hoursAgo,
  keysSelectedFact,
  lastSuccessFact,
  latestReconcileCheck,
  newestSucceeded,
  reconcileHint,
  relativeDayWord,
  rowsReadWrittenFact,
  rowsWrittenFact,
  runsKpiHint,
  windowRunCounts,
} from '../trust'
import { goldRunRow, pipelineRunRow } from '../runsTable'
import type { DqCheckConfig, GoldRunMetrics, PipelineConfig, RunMetrics } from '../types'

const NOW = new Date('2026-09-29T17:50:00Z')
const pipeline = { pipeline_id: 'p-1', extract: { table: 'EDS.PRICE_LIST' } } as unknown as PipelineConfig

function run(over: Partial<RunMetrics>): RunMetrics {
  return {
    job_name: 'uif-echo-dev-bronze-ingest-eds', pipeline_id: 'p-1', run_id: 'r', status: 'SUCCEEDED',
    started_at: '2026-09-29T15:50:00Z', rows_read: 10, rows_written: 10, ...over,
  }
}
function gold(over: Partial<GoldRunMetrics>): GoldRunMetrics {
  return {
    job_name: 'uif-echo-dev-gold-transform', run_id: 'g', gold_id: 'price', status: 'SUCCEEDED',
    started_at: '2026-09-29T06:31:00Z', rows_read: 50, rows_written: 0, affected_keys: 0, ...over,
  }
}
function check(over: Partial<DqCheckConfig>): DqCheckConfig {
  return {
    check_id: 'reconcile-db-t', version: 'latest', kind: 'reconcile', enabled: true,
    subject: { layer: 'silver', database: 'db', table: 't' }, schedule: { frequency: 'daily' },
    parameters: {}, metadata: { created_at: '', owner: '' }, ...over,
  }
}

describe('time words', () => {
  it('hoursAgo counts minutes under an hour and hours after', () => {
    expect(hoursAgo('2026-09-29T17:05:00Z', NOW)).toBe('45 min ago')
    expect(hoursAgo('2026-09-29T15:50:00Z', NOW)).toBe('2 h ago')
    expect(hoursAgo('2026-09-28T10:50:00Z', NOW)).toBe('31 h ago')
    expect(hoursAgo(null, NOW)).toBe('—')
  })

  it('relativeDayWord goes by UTC calendar day', () => {
    expect(relativeDayWord('2026-09-29T00:10:00Z', NOW)).toBe('today')
    expect(relativeDayWord('2026-09-28T23:59:00Z', NOW)).toBe('yesterday')
    expect(relativeDayWord('2026-09-26T08:00:00Z', NOW)).toBe('3 days ago')
  })

  it('clockAndAgo', () => {
    expect(clockAndAgo('2026-09-29T15:50:00Z', NOW)).toBe('15:50 UTC · 2 h ago')
  })
})

describe('last run facts', () => {
  it('rows read → written is what the run recorded, even a failure that read nothing', () => {
    const f = rowsReadWrittenFact(pipelineRunRow(pipeline, run({ status: 'FAILED', rows_read: 0, rows_written: 0 })))
    expect(f).toEqual({ value: '0 → 0', hint: 'last run, 15:50 UTC · failed' })
  })

  it('shows a dash for a count the run did not record, and "no runs yet" with no run', () => {
    expect(rowsReadWrittenFact(pipelineRunRow(pipeline, run({ rows_read: null, rows_written: null }))).value).toBe('— → —')
    expect(rowsReadWrittenFact(null)).toEqual({ value: '—', hint: 'no runs yet' })
  })

  it('gold rows written and keys selected', () => {
    const row = goldRunRow(gold({ status: 'FAILED', rows_written: 0, affected_keys: 12 }))
    expect(rowsWrittenFact(row)).toEqual({ value: '0', hint: 'last run, 06:31 UTC · failed' })
    expect(keysSelectedFact(row)).toEqual({ value: '12', hint: 'affected keys in the last run' })
    expect(keysSelectedFact(null).value).toBe('—')
  })
})

describe('last successful run', () => {
  const rows = [
    pipelineRunRow(pipeline, run({ run_id: 'fail', status: 'FAILED' })),
    pipelineRunRow(pipeline, run({ run_id: 'old', started_at: '2026-09-27T08:30:00Z', rows_written: 900 })),
    pipelineRunRow(pipeline, run({ run_id: 'ok', started_at: '2026-09-28T08:47:00Z', rows_written: 5120 })),
  ]

  it('picks the newest SUCCEEDED run whatever the order', () => {
    expect(newestSucceeded(rows)?.runId).toBe('ok')
  })

  it('reads as a day word with rows, clock and hours ago', () => {
    expect(lastSuccessFact(rows, NOW)).toEqual({ value: 'yesterday', hint: '5,120 rows · 08:47 UTC · 33 h ago' })
  })

  it('says none yet when nothing succeeded', () => {
    expect(lastSuccessFact([rows[0]], NOW).value).toBe('none yet')
  })

  it('gold: bookmark advanced only when rows were written, else no changes', () => {
    expect(goldLastSuccessFact([goldRunRow(gold({ rows_written: 1206 }))], NOW).hint).toBe(
      '1,206 rows · 06:31 UTC · bookmark advanced',
    )
    expect(goldLastSuccessFact([goldRunRow(gold({ rows_written: 0 }))], NOW).hint).toBe('06:31 UTC · no changes')
    expect(goldLastSuccessFact([goldRunRow(gold({ status: 'FAILED' }))], NOW).value).toBe('none yet')
  })

  it('goldBookmarkWord follows the newest success', () => {
    expect(goldBookmarkWord([goldRunRow(gold({ rows_written: 3 }))])).toBe('bookmark advanced')
    expect(goldBookmarkWord([goldRunRow(gold({ rows_written: 0 }))])).toBe('no changes')
    expect(goldBookmarkWord([goldRunRow(gold({ status: 'FAILED' }))])).toBeNull()
  })
})

describe('window figures', () => {
  const rows = [
    pipelineRunRow(pipeline, run({ run_id: 'a', status: 'FAILED', started_at: '2026-09-29T13:14:00Z', error_message: 'ORA-01017: invalid username/password' })),
    pipelineRunRow(pipeline, run({ run_id: 'b', status: 'WARNING', started_at: '2026-09-28T09:00:00Z' })),
    pipelineRunRow(pipeline, run({ run_id: 'c', started_at: '2026-09-28T08:47:00Z' })),
    pipelineRunRow(pipeline, run({ run_id: 'd', started_at: '2026-09-27T08:47:00Z' })),
    pipelineRunRow(pipeline, run({ run_id: 'e', status: 'SKIPPED' })),
  ]

  it('counts succeeded against failed or warned; skipped is neither', () => {
    expect(windowRunCounts(rows)).toEqual({ total: 5, ok: 2, bad: 2 })
  })

  it('runs hint: the share of finished runs and the last success day', () => {
    expect(runsKpiHint(windowRunCounts(rows), rows, NOW)).toBe('50% of finished runs succeeded · last success yesterday')
    expect(runsKpiHint({ total: 0, ok: 0, bad: 0 }, [], NOW)).toBe(
      'no finished runs in this range · no successful run recorded',
    )
  })

  it('failed hint names the newest failure by clock and pattern', () => {
    expect(failedKpiHint(rows)).toBe('latest: 13:14 UTC · ORA-01017')
    expect(failedKpiHint([rows[1]])).toBe('latest: 09:00 UTC · Finished with a warning.')
    expect(failedKpiHint(rows.slice(2))).toBe('nothing failed')
  })
})

describe('reconcile check', () => {
  it('takes the newest reconcile check that has run, ignoring other kinds and never-run checks', () => {
    const picked = latestReconcileCheck([
      check({ check_id: 'never', last_run: null }),
      check({ check_id: 'nulls', kind: 'null-rate', last_run: { ts: '2026-09-29T09:00:00Z', status: 'FAILED' } }),
      check({ check_id: 'older', last_run: { ts: '2026-09-27T08:55:00Z', status: 'FAILED' } }),
      check({ check_id: 'newer', last_run: { ts: '2026-09-28T08:55:00Z', status: 'SUCCEEDED', summary: 'variance 0' } }),
    ])
    expect(picked?.check_id).toBe('newer')
    expect(reconcileHint(picked!)).toBe('reconcile · 28 Sep 2026, 08:55 UTC · variance 0')
  })

  it('leaves the summary out when the check wrote none, and is null with no reconcile check', () => {
    expect(reconcileHint(check({ last_run: { ts: '2026-09-28T08:55:00Z', status: 'SUCCEEDED' } }))).toBe(
      'reconcile · 28 Sep 2026, 08:55 UTC',
    )
    expect(latestReconcileCheck([])).toBeNull()
  })
})
