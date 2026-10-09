import { describe, expect, it } from 'vitest'
import { buildUpcoming, formatNextAt, nextGoldRun, nextPipelineRun, objectSchedule } from '@/lib/upcoming'
import type { GoldPipelineConfig, PipelineConfig } from '@/lib/types'

const iso = (d: Date | null) => d?.toISOString() ?? null

const pipe = (over: Record<string, unknown> = {}): PipelineConfig =>
  ({
    pipeline_id: 'p1',
    enabled: true,
    extract: { table: 'T1' },
    schedule: { frequency: 'hourly' },
    ...over,
  }) as unknown as PipelineConfig

const gold = (over: Record<string, unknown> = {}): GoldPipelineConfig =>
  ({
    gold_id: 'price',
    enabled: true,
    schedule: { frequency: 'gold-24h', start_time: '06:30' },
    ...over,
  }) as unknown as GoldPipelineConfig

describe('nextPipelineRun', () => {
  it('hourly: the next whole hour', () => {
    expect(iso(nextPipelineRun(pipe(), new Date('2026-09-29T13:55:20Z')))).toBe('2026-09-29T14:00:00.000Z')
  })
  it('hourly: exactly on the hour still means the next one', () => {
    expect(iso(nextPipelineRun(pipe(), new Date('2026-09-29T14:00:00Z')))).toBe('2026-09-29T15:00:00.000Z')
  })
  it('hourly: rolls over midnight', () => {
    expect(iso(nextPipelineRun(pipe(), new Date('2026-09-29T23:30:00Z')))).toBe('2026-09-30T00:00:00.000Z')
  })
  it.each([
    ['slot 0 not yet reached today', 0, '2026-09-29T04:00:00Z', '2026-09-29T05:30:00.000Z'],
    ['slot 0 already passed: tomorrow', 0, '2026-09-29T06:00:00Z', '2026-09-30T05:30:00.000Z'],
    ['slot 6 is 08:30', 6, '2026-09-29T06:00:00Z', '2026-09-29T08:30:00.000Z'],
    ['across midnight and a month end', 0, '2026-09-30T23:50:00Z', '2026-10-01T05:30:00.000Z'],
  ])('daily, %s', (_n, slot, now, expected) => {
    const p = pipe({ schedule: { frequency: 'daily', daily_slot: slot } })
    expect(iso(nextPipelineRun(p, new Date(now)))).toBe(expected)
  })
  it('daily with no slot is slot 0', () => {
    const p = pipe({ schedule: { frequency: 'daily' } })
    expect(iso(nextPipelineRun(p, new Date('2026-09-29T00:00:00Z')))).toBe('2026-09-29T05:30:00.000Z')
  })
  it.each(['weekly', 'reconcile', undefined])('%s has no projection', (frequency) => {
    expect(nextPipelineRun(pipe({ schedule: { frequency } }), new Date('2026-09-29T00:00:00Z'))).toBeNull()
  })
})

describe('nextGoldRun', () => {
  it('24h: the start time today, else tomorrow', () => {
    expect(iso(nextGoldRun(gold(), new Date('2026-09-29T05:00:00Z')))).toBe('2026-09-29T06:30:00.000Z')
    expect(iso(nextGoldRun(gold(), new Date('2026-09-29T06:30:30Z')))).toBe('2026-09-30T06:30:00.000Z')
  })
  it('an interval cadence steps from the start time', () => {
    const g = gold({ schedule: { frequency: 'gold-6h', start_time: '06:30' } })
    expect(iso(nextGoldRun(g, new Date('2026-09-29T13:00:00Z')))).toBe('2026-09-29T18:30:00.000Z')
    expect(iso(nextGoldRun(g, new Date('2026-09-29T00:10:00Z')))).toBe('2026-09-29T00:30:00.000Z')
  })
  it('weekly: the next chosen weekday at the start time (29 Sep 2026 is a Tuesday)', () => {
    const g = gold({ schedule: { frequency: 'gold-weekly', start_time: '06:30', weekly_days: ['MON'] } })
    expect(new Date('2026-09-29T10:00:00Z').getUTCDay()).toBe(2)
    expect(iso(nextGoldRun(g, new Date('2026-09-29T10:00:00Z')))).toBe('2026-10-05T06:30:00.000Z')
  })
  it('weekly with no days defaults to Monday', () => {
    const g = gold({ schedule: { frequency: 'gold-weekly', start_time: '06:30' } })
    expect(iso(nextGoldRun(g, new Date('2026-09-29T10:00:00Z')))).toBe('2026-10-05T06:30:00.000Z')
  })
  it('a cadence the runtime does not know has no projection', () => {
    const g = gold({ schedule: { frequency: 'gold-fortnightly', start_time: '06:30' } })
    expect(nextGoldRun(g, new Date())).toBeNull()
  })
  it('no usable start time has no projection', () => {
    expect(nextGoldRun(gold({ schedule: { frequency: 'gold-24h' } }), new Date())).toBeNull()
    expect(nextGoldRun(gold({ schedule: { frequency: 'gold-24h', start_time: '25:99' } }), new Date())).toBeNull()
  })
  it('a row with no frequency uses the default cadence (daily)', () => {
    const g = gold({ schedule: { start_time: '06:30' } })
    expect(iso(nextGoldRun(g, new Date('2026-09-29T05:00:00Z')))).toBe('2026-09-29T06:30:00.000Z')
  })
})

describe('buildUpcoming', () => {
  const now = new Date('2026-09-29T13:55:00Z')
  const pipelines = [
    pipe({ pipeline_id: 'hourly', extract: { table: 'OM.STOCK_POSITION' } }),
    pipe({ pipeline_id: 'daily6', extract: { table: 'FIPM.X' }, schedule: { frequency: 'daily', daily_slot: 6 }, silver: { enabled: true } }),
    pipe({ pipeline_id: 'off', extract: { table: 'SIS.STUDENT' }, enabled: false, schedule: { frequency: 'daily', daily_slot: 0 } }),
    pipe({ pipeline_id: 'rec', schedule: { frequency: 'reconcile' } }),
    pipe({ pipeline_id: 'wk', schedule: { frequency: 'weekly' } }),
  ]
  const golds = [
    gold({ gold_id: 'product' }),
    gold({ gold_id: 'customer', schedule: { frequency: 'gold-weekly', start_time: '06:30', weekly_days: ['MON'] } }),
    gold({ gold_id: 'stock', enabled: false }),
  ]
  const lastRuns = new Map([
    ['hourly', { at: '2026-09-29T13:55:00Z', status: 'RUNNING' }],
    ['gold:product', { at: '2026-09-29T06:31:00Z', status: 'SUCCEEDED' }],
  ])

  it('24 h: soonest first, projected tables only, reconcile and weekly pipelines left out', () => {
    const rows = buildUpcoming(pipelines, golds, lastRuns, now, 24)
    expect(rows.map((r) => r.key)).toEqual([
      'hourly', // 14:00
      'off', // tomorrow 05:30, disabled but still listed
      'gold:product', // tomorrow 06:30
      'gold:stock', // tomorrow 06:30, disabled
      'daily6', // tomorrow 08:30
    ])
  })

  it('7 d: the weekly gold table comes into range, last', () => {
    const rows = buildUpcoming(pipelines, golds, lastRuns, now, 168)
    expect(rows.map((r) => r.key).at(-1)).toBe('gold:customer')
    expect(rows.find((r) => r.key === 'gold:customer')?.cadence).toBe('weekly · Mon (UTC)')
  })

  it('the horizon edge is inclusive: a run exactly 24 h away is listed', () => {
    const at0530 = new Date('2026-09-29T05:30:00Z')
    const p = pipe({ schedule: { frequency: 'daily', daily_slot: 0 } })
    // 05:30 has just "passed" at 05:30:00, so the next is tomorrow 05:30, exactly 24 h on
    expect(buildUpcoming([p], [], new Map(), at0530, 24)).toHaveLength(1)
    expect(buildUpcoming([p], [], new Map(), at0530, 24)[0].nextAt?.toISOString()).toBe('2026-09-30T05:30:00.000Z')
  })

  it('flags disabled tables and says why', () => {
    const rows = buildUpcoming(pipelines, golds, lastRuns, now, 24)
    expect(rows.find((r) => r.key === 'off')).toMatchObject({
      disabled: true, note: 'skipped while disabled', lastRunAt: null, lastStatus: null,
    })
    expect(rows.find((r) => r.key === 'hourly')).toMatchObject({
      disabled: false, note: undefined, lastStatus: 'RUNNING',
    })
  })

  it('layer, link and cadence', () => {
    const rows = buildUpcoming(pipelines, golds, lastRuns, now, 24)
    expect(rows.find((r) => r.key === 'hourly')).toMatchObject({
      object: 'OM.STOCK_POSITION', layer: 'Bronze', cadence: 'hourly', href: '/m/data-sync/syncs/hourly',
    })
    expect(rows.find((r) => r.key === 'daily6')?.layer).toBe('Bronze + Silver')
    expect(rows.find((r) => r.key === 'gold:product')).toMatchObject({
      object: 'product', layer: 'Gold', cadence: 'daily', href: '/m/data-sync/gold/product',
      lastRunAt: '2026-09-29T06:31:00Z', lastStatus: 'SUCCEEDED',
    })
  })

  it('a table with no projection is left out rather than shown with a blank time', () => {
    expect(buildUpcoming([pipelines[3], pipelines[4]], [], new Map(), now, 168)).toEqual([])
  })
})

describe('objectSchedule', () => {
  const now = new Date('2026-09-29T13:55:00Z')
  const pipelines = [
    pipe({ pipeline_id: 'hourly' }),
    pipe({ pipeline_id: 'off', enabled: false, schedule: { frequency: 'daily', daily_slot: 0 } }),
  ]
  const golds = [gold({ gold_id: 'product' }), gold({ gold_id: 'stock', enabled: false })]

  it('a sync: its cadence and next run', () => {
    expect(objectSchedule('hourly', pipelines, golds, now)).toEqual({
      cadence: 'hourly', disabled: false, nextAt: new Date('2026-09-29T14:00:00Z'),
    })
  })
  it('a gold table, keyed gold:{id}', () => {
    const s = objectSchedule('gold:product', pipelines, golds, now)
    expect(s?.disabled).toBe(false)
    expect(iso(s?.nextAt ?? null)).toBe('2026-09-30T06:30:00.000Z')
    expect(s?.cadence).toBeTruthy()
  })
  it('disabled: flagged, with no next run', () => {
    expect(objectSchedule('off', pipelines, golds, now)).toMatchObject({ disabled: true, nextAt: null })
    expect(objectSchedule('gold:stock', pipelines, golds, now)).toMatchObject({ disabled: true, nextAt: null })
  })
  it('an object whose config is not loaded: null', () => {
    expect(objectSchedule('gone', pipelines, golds, now)).toBeNull()
    expect(objectSchedule('gold:gone', pipelines, golds, now)).toBeNull()
  })
})

describe('formatNextAt', () => {
  const now = new Date('2026-09-29T13:55:00Z')
  it('same UTC day: clock only, with a zone-local clock and minutes to go', () => {
    const out = formatNextAt(new Date('2026-09-29T14:30:00Z'), 'UTC', now)
    expect(out.utc).toBe('14:30 UTC')
    expect(out.local).toBe('14:30 UTC')
    expect(out.relative).toBe('in 35 min')
  })
  it('tomorrow is named, and the local clock follows the zone', () => {
    const out = formatNextAt(new Date('2026-09-30T05:30:00Z'), 'Asia/Kolkata', now)
    expect(out.utc).toBe('Tomorrow 05:30 UTC')
    expect(out.local).toMatch(/^11:00/)
    expect(out.relative).toBe('in 16 h')
  })
  it('later days name the weekday; far days count in days', () => {
    const out = formatNextAt(new Date('2026-10-05T06:30:00Z'), 'UTC', now)
    expect(out.utc).toBe('Mon 06:30 UTC')
    expect(out.relative).toBe('in 6 d')
  })
  it('an unknown zone falls back to the UTC text instead of throwing', () => {
    expect(formatNextAt(new Date('2026-09-29T14:30:00Z'), 'Not/AZone', now).local).toBe('14:30 UTC')
  })
})
