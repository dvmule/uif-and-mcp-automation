import { describe, it, expect } from 'vitest'
import {
  ACTIVITY_SPAN,
  DAY_MS,
  HOUR_MS,
  axisTicks,
  bucketLabel,
  bucketRows,
  buckets,
  compact,
  countOutcomes,
  peakBucket,
  rankByRows,
  runRows,
  runsBetween,
} from '../activity'
import type { RunRow } from '../runsTable'

const NOW = Date.UTC(2026, 8, 29, 12, 0, 0)
const ago = (h: number) => new Date(NOW - h * HOUR_MS).toISOString()

let seq = 0
function row(over: Partial<RunRow> = {}): RunRow {
  seq += 1
  return {
    key: `k${seq}`,
    runId: `r${seq}`,
    object: 'INV.A',
    objectHref: '/m/data-sync/syncs/a',
    layer: 'bronze',
    kind: 'Incremental',
    status: 'SUCCEEDED',
    startedAt: ago(1),
    durationSec: 10,
    rowsRead: 5,
    rowsWritten: 4,
    run: {} as RunRow['run'],
    pipelineId: 'a',
    ...over,
  }
}

describe('runsBetween', () => {
  it('keeps runs started in (since, until] and drops undated ones', () => {
    const rows = [
      row({ startedAt: ago(1) }),
      row({ startedAt: ago(24) }),
      row({ startedAt: ago(30) }),
      row({ startedAt: null }),
      row({ startedAt: new Date(NOW + 1000).toISOString() }),
    ]
    const got = runsBetween(rows, NOW - DAY_MS, NOW)
    expect(got).toEqual([rows[0]])
  })
})

describe('buckets', () => {
  it('makes n buckets of span and places each run in its (from, to]', () => {
    const since = NOW - DAY_MS
    const rows = [row({ startedAt: ago(0.5) }), row({ startedAt: ago(23.5) }), row({ startedAt: ago(23.5) }), row({ startedAt: ago(26) })]
    const bs = buckets(rows, since, 24, HOUR_MS)
    expect(bs).toHaveLength(24)
    expect(bs[0]).toMatchObject({ from: since, to: since + HOUR_MS })
    expect(bs[0].runs).toHaveLength(2)
    expect(bs[23].runs).toHaveLength(1)
    expect(bs.reduce((n, b) => n + b.runs.length, 0)).toBe(3)
  })

  it('ACTIVITY_SPAN is 24 hourly or 7 daily buckets', () => {
    expect(ACTIVITY_SPAN['24h']).toEqual({ n: 24, spanMs: HOUR_MS })
    expect(ACTIVITY_SPAN['7d']).toEqual({ n: 7, spanMs: DAY_MS })
  })
})

describe('rows', () => {
  it('counts rows written, else rows read, else nothing', () => {
    expect(runRows(row({ rowsWritten: 7, rowsRead: 9 }))).toBe(7)
    expect(runRows(row({ rowsWritten: null, rowsRead: 9 }))).toBe(9)
    expect(runRows(row({ rowsWritten: null, rowsRead: null }))).toBe(0)
    expect(runRows(row({ rowsWritten: 0, rowsRead: 9 }))).toBe(0)
  })

  it('bucketRows sums and peakBucket finds the fullest, or -1 when all are empty', () => {
    const bs = buckets(
      [row({ startedAt: ago(0.5), rowsWritten: 10 }), row({ startedAt: ago(0.4), rowsWritten: 5 }), row({ startedAt: ago(5.5), rowsWritten: 100 })],
      NOW - DAY_MS,
      24,
      HOUR_MS,
    )
    expect(bucketRows(bs[23])).toBe(15)
    expect(peakBucket(bs)).toEqual({ index: 18, rows: 100 })
    expect(peakBucket(buckets([], NOW - DAY_MS, 24, HOUR_MS))).toEqual({ index: -1, rows: 0 })
  })
})

describe('countOutcomes', () => {
  it('uses the Jobs status buckets: STOPPED is a warning, ERROR a failure, SKIPPED other', () => {
    const c = countOutcomes([
      row({ status: 'SUCCEEDED' }),
      row({ status: 'WARNING' }),
      row({ status: 'STOPPED' }),
      row({ status: 'FAILED' }),
      row({ status: 'ERROR' }),
      row({ status: 'RUNNING' }),
      row({ status: 'SKIPPED' }),
    ])
    expect(c).toEqual({ succeeded: 1, warning: 2, failed: 2, running: 1, other: 1, total: 7 })
  })
})

describe('labels', () => {
  it('bucketLabel says the hour for 24h and the weekday and date for 7d, in local time', () => {
    const at = new Date(2026, 8, 28, 15, 37).getTime()
    expect(bucketLabel(at, '24h')).toBe('15:00')
    expect(bucketLabel(at, '7d')).toBe('Mon 28')
    expect(bucketLabel(new Date(2026, 8, 28, 4, 5).getTime(), '24h')).toBe('04:00')
  })

  it('axisTicks gives first, quarters and last without repeats', () => {
    expect(axisTicks(24)).toEqual([0, 6, 12, 17, 23])
    expect(axisTicks(7)).toEqual([0, 2, 3, 5, 6])
    expect(axisTicks(1)).toEqual([0])
    expect(axisTicks(0)).toEqual([])
  })

  it('compact shortens thousands and millions', () => {
    expect(compact(88)).toBe('88')
    expect(compact(1300)).toBe('1.3K')
    expect(compact(431_000)).toBe('431K')
    expect(compact(1000)).toBe('1K')
    expect(compact(2_500_000)).toBe('2.5M')
    expect(compact(0)).toBe('0')
  })
})

describe('rankByRows', () => {
  it('ranks objects by rows from finished runs, with each outcome counted', () => {
    const ranked = rankByRows([
      row({ object: 'INV.A', pipelineId: 'a', rowsWritten: 10, status: 'SUCCEEDED' }),
      row({ object: 'INV.A', pipelineId: 'a', rowsWritten: 0, status: 'FAILED' }),
      row({ object: 'INV.A', pipelineId: 'a', rowsWritten: 5, status: 'WARNING' }),
      row({ object: 'INV.A', pipelineId: 'a', rowsWritten: 999, status: 'RUNNING' }),
      row({ object: 'price', pipelineId: undefined, goldId: 'price', layer: 'gold', objectHref: '/m/data-sync/gold/price', rowsWritten: 400 }),
      row({ object: 'INV.B', pipelineId: 'b', objectHref: '/m/data-sync/syncs/b', rowsWritten: 0, status: 'FAILED' }),
    ])
    expect(ranked).toEqual([
      { key: 'gold:price', label: 'price', href: '/m/data-sync/gold/price', rows: 400, succeeded: 1, warning: 0, failed: 0 },
      { key: 'a', label: 'INV.A', href: '/m/data-sync/syncs/a', rows: 15, succeeded: 1, warning: 1, failed: 1 },
      { key: 'b', label: 'INV.B', href: '/m/data-sync/syncs/b', rows: 0, succeeded: 0, warning: 0, failed: 1 },
    ])
  })

  it('keeps the top five by default and breaks ties by name', () => {
    const rows = ['F', 'E', 'D', 'C', 'B', 'A'].map((n) => row({ object: n, pipelineId: n, rowsWritten: 1 }))
    expect(rankByRows(rows).map((r) => r.label)).toEqual(['A', 'B', 'C', 'D', 'E'])
    expect(rankByRows(rows, 2)).toHaveLength(2)
  })

  it('is empty when nothing has finished', () => {
    expect(rankByRows([row({ status: 'RUNNING' })])).toEqual([])
  })
})
