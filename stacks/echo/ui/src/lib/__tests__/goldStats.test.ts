import { describe, expect, it } from 'vitest'
import {
  TREND_MAX_DAYS,
  TREND_STALE_AFTER_MINUTES,
  formatDayLabel,
  formatGeneratedAt,
  freshnessCaption,
  isTrendStale,
  sparkPoints,
  trendTotals,
} from '@/lib/goldStats'
import type { GoldLiveWindow, GoldTrendPoint } from '@/lib/goldStats'

function point(day: string, over: Partial<GoldTrendPoint> = {}): GoldTrendPoint {
  return { day, entities: 2, runs: 4, failed: 0, rows_written: 100, fresh: 2, ...over }
}

function live(over: Partial<GoldLiveWindow> = {}): GoldLiveWindow {
  return {
    as_of: '2026-09-24T12:00:00+00:00',
    window_hours: 24,
    entities: 5, enabled: 5, runs: 6, succeeded: 5, failed: 1, skipped: 0,
    rows_written: 10, fresh: 5, stale: 0, unknown: 0, stale_entities: [],
    ...over,
  }
}

describe('trendTotals', () => {
  it('sums the window it was given, not the window the file holds', () => {
    const totals = trendTotals([point('2026-09-22'), point('2026-09-23', { runs: 6, failed: 2 })])
    expect(totals.days).toBe(2)
    expect(totals.runs).toBe(10)
    expect(totals.failed).toBe(2)
    expect(totals.rowsWritten).toBe(200)
  })

  it('reports freshness as a share of entity-days', () => {
    const totals = trendTotals([point('2026-09-22', { entities: 2, fresh: 1 }),
                                point('2026-09-23', { entities: 2, fresh: 2 })])
    expect(totals.entityDays).toBe(4)
    expect(totals.fresh).toBe(3)
    expect(totals.freshShare).toBeCloseTo(0.75)
  })

  it('has no freshness share when there are no entity-days', () => {
    // Null, never 0: 0% reads as an outage, and "nothing to report" is not one.
    expect(trendTotals([]).freshShare).toBeNull()
    expect(trendTotals(undefined).freshShare).toBeNull()
    expect(trendTotals([point('2026-09-23', { entities: 0, fresh: 0 })]).freshShare).toBeNull()
  })
})

describe('sparkPoints', () => {
  it('scales against the peak from a zero baseline, not against the range', () => {
    // 800 -> 900 is an 11% rise. Scaled from zero it reads as one; scaled
    // against the series' own min-max range it would climb the full height,
    // which is the exaggeration a trend chart exists to prevent.
    expect(sparkPoints([800, 900])).toBe('0,3.1 120,0')
    expect(sparkPoints([0, 450, 900])).toBe('0,28 60,14 120,0')
  })

  it('a flat series sits on one line whatever its level', () => {
    // Peak scaling says nothing about absolute level: that is the axis
    // labels' and the totals' job, not the sparkline's.
    expect(sparkPoints([900, 900, 900])).toBe(sparkPoints([2, 2, 2]))
  })

  it('puts the newest point on the right edge', () => {
    expect(sparkPoints([1, 2]).endsWith('120,0')).toBe(true)
  })

  it('centres a single point and returns nothing for none', () => {
    expect(sparkPoints([5])).toBe('60,0')
    expect(sparkPoints([])).toBe('')
  })

  it('an all-zero series sits on the baseline rather than dividing by zero', () => {
    expect(sparkPoints([0, 0])).toBe('0,28 120,28')
  })
})

describe('freshnessCaption', () => {
  it('names the tables that are behind, because that is what to act on', () => {
    expect(freshnessCaption(live({ stale: 2, fresh: 3, stale_entities: ['price', 'stock'] })))
      .toBe('Behind: price, stock')
  })

  it('counts the rest once there are too many to name', () => {
    expect(freshnessCaption(live({
      stale: 5, stale_entities: ['a', 'b', 'c', 'd', 'e'],
    }))).toBe('Behind: a, b, c +2 more')
  })

  it('distinguishes "nothing to judge yet" from "all good"', () => {
    expect(freshnessCaption(live({ fresh: 0, unknown: 5 })))
      .toBe('No table has a last success to judge yet')
    expect(freshnessCaption(live({ fresh: 4, unknown: 1 })))
      .toBe('1 without a cadence or a last success')
    expect(freshnessCaption(live())).toBe('Every table is inside its freshness target')
  })

  it('says nothing when there is nothing loaded', () => {
    expect(freshnessCaption(undefined)).toBe('')
  })
})

describe('isTrendStale', () => {
  it('only complains once a whole night has been missed', () => {
    expect(isTrendStale(60)).toBe(false)
    expect(isTrendStale(TREND_STALE_AFTER_MINUTES)).toBe(false)
    expect(isTrendStale(TREND_STALE_AFTER_MINUTES + 1)).toBe(true)
  })

  it('an unknown age is not a claim of staleness', () => {
    expect(isTrendStale(null)).toBe(false)
    expect(isTrendStale(undefined)).toBe(false)
  })
})

describe('formatGeneratedAt', () => {
  it('renders UTC and says so, because every gold time on the page is UTC', () => {
    expect(formatGeneratedAt('2026-09-24T04:30:00+00:00')).toBe('24 Sep, 04:30 UTC')
  })

  it('converts an offset rather than printing the local wall clock', () => {
    expect(formatGeneratedAt('2026-09-24T06:30:00+02:00')).toBe('24 Sep, 04:30 UTC')
  })

  it('is empty when there is nothing to date', () => {
    expect(formatGeneratedAt(null)).toBe('')
    expect(formatGeneratedAt(undefined)).toBe('')
    expect(formatGeneratedAt('not a date')).toBe('')
  })
})

describe('formatDayLabel', () => {
  it('drops the leading zero and names the month', () => {
    expect(formatDayLabel('2026-09-03')).toBe('3 Sep')
    expect(formatDayLabel('2026-12-31')).toBe('31 Dec')
  })

  it('hands back anything it cannot read, rather than inventing a month', () => {
    expect(formatDayLabel('2026-13-01')).toBe('2026-13-01')
    expect(formatDayLabel('')).toBe('')
  })
})

describe('the window the page may ask for', () => {
  it('matches the file the nightly job writes', () => {
    // The server clamps too; this keeps the UI from labelling 30 days as 90.
    expect(TREND_MAX_DAYS).toBe(30)
  })
})
