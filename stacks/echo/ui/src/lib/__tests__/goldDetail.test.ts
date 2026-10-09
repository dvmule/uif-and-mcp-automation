import { describe, it, expect } from 'vitest'
import {
  goldDetailPath,
  goldDetailState,
  goldReadersOfSilver,
  minutesUntilNextGoldRun,
  missingSourceTables,
} from '../goldDetail'
import { goldRunRow } from '../runsTable'
import type { GoldPipelineConfig, GoldRunMetrics } from '../types'

const product: GoldPipelineConfig = {
  gold_id: 'product', enabled: true, domain: 'product',
  target: { database: 'uif_echo_{env}_gold_product', table: 'product' },
  schedule: { frequency: 'gold-24h', start_time: '06:30' },
}

function run(over: Partial<GoldRunMetrics> & { started_at: string }): GoldRunMetrics {
  return {
    job_name: 'uif-echo-dev-gold-transform', run_id: `jr_${over.started_at}`, gold_id: 'product',
    status: 'SUCCEEDED', duration_sec: 100, ...over,
  }
}
const hoursAgo = (h: number) => new Date(Date.now() - h * 3600_000).toISOString()

describe('goldDetailPath', () => {
  // pipeline_id and gold_id share a namespace; /syncs/{id} must never be taught gold.
  it('is its own route, never the sync detail route', () => {
    expect(goldDetailPath(product)).toBe('/m/data-sync/gold/product')
    expect(goldDetailPath(product)).not.toContain('/syncs/')
  })
})

describe('minutesUntilNextGoldRun', () => {
  const at = (hh: number, mm: number) => new Date(Date.UTC(2026, 8, 22, hh, mm))
  it('counts to a daily start time, wrapping past midnight', () => {
    expect(minutesUntilNextGoldRun('06:30', 'gold-24h', at(6, 0))).toBe(30)
    expect(minutesUntilNextGoldRun('06:30', 'gold-24h', at(6, 30))).toBe(1440)   // slot must be strictly ahead
    expect(minutesUntilNextGoldRun('06:30', 'gold-24h', at(23, 0))).toBe(450)
  })
  it('walks the intra-day slot grid for sub-daily cadences, like slot_minutes', () => {
    expect(minutesUntilNextGoldRun('00:45', 'gold-1h', at(10, 0))).toBe(45)     // next :45
    expect(minutesUntilNextGoldRun('00:06', 'gold-30m', at(10, 10))).toBe(26)   // 10:36
  })
  it('walks the 7-day grid for the weekly rung (UIFPE-532)', () => {
    // 2026-09-23T10:00Z is a Wednesday
    const wed = new Date('2026-09-23T10:00:00Z')
    expect(minutesUntilNextGoldRun('03:00', 'gold-weekly', wed, ['TUE', 'THU'])).toBe(17 * 60)      // Thu 03:00
    expect(minutesUntilNextGoldRun('03:00', 'gold-weekly', wed, ['TUE'])).toBe((6 * 24 - 7) * 60)   // next Tue 03:00
    expect(minutesUntilNextGoldRun('06:30', 'gold-weekly', wed)).toBe((5 * 24 - 3) * 60 - 30)      // default Monday 06:30
    expect(minutesUntilNextGoldRun('03:00', 'gold-weekly', new Date('2026-09-24T03:00:00Z'), ['THU'])).toBe(7 * 1440) // strictly ahead
  })

  it('honours legacy aliases and refuses to guess an unusable start time', () => {
    expect(minutesUntilNextGoldRun('06:30', 'gold', at(6, 0))).toBe(30)
    expect(minutesUntilNextGoldRun(':30', 'gold-24h', at(6, 0))).toBeNull()
    expect(minutesUntilNextGoldRun(undefined, 'gold-24h', at(6, 0))).toBeNull()
  })
})

describe('goldDetailState', () => {
  it('is UNKNOWN with no runs - never an invented healthy', () => {
    const s = goldDetailState(product, [])
    expect(s.status).toBe('unknown')
    expect(s.lastRun).toBeNull()
    expect(s.nextRunInMin).not.toBeNull()
  })
  it('is disabled before anything else, and has no next run', () => {
    const s = goldDetailState({ ...product, enabled: false }, [run({ started_at: hoursAgo(1) })])
    expect(s.status).toBe('disabled')
    expect(s.nextRunInMin).toBeNull()
  })
  it('is running when the newest run is in flight', () => {
    expect(goldDetailState(product, [run({ started_at: hoursAgo(0), status: 'RUNNING' })]).status).toBe('running')
  })
  it('is healthy on a clean recent history, with a 30-day success rate', () => {
    const s = goldDetailState(product, [1, 2, 3].map(h => run({ started_at: hoursAgo(h) })))
    expect(s.status).toBe('healthy')
    expect(s.successRate30d).toBe(1)
  })
  it('is failing on a failed last run or three leading fails, degraded on a warning', () => {
    expect(goldDetailState(product, [run({ started_at: hoursAgo(1), status: 'FAILED' })]).status).toBe('failing')
    expect(goldDetailState(product, [run({ started_at: hoursAgo(1), status: 'WARNING' })]).status).toBe('degraded')
  })
  it('excludes runs older than 30 days from the success rate', () => {
    const s = goldDetailState(product, [
      run({ started_at: hoursAgo(1) }),
      run({ started_at: hoursAgo(24 * 40), status: 'FAILED' }),
    ])
    expect(s.successRate30d).toBe(1)
  })
  it('reads a speed trend from the last three completed runs against the three before', () => {
    const runs = [50, 50, 50, 100, 100, 100].map((d, i) => run({ started_at: hoursAgo(i + 1), duration_sec: d }))
    expect(goldDetailState(product, runs).trend).toBe('up')
    expect(goldDetailState(product, runs.slice().reverse()).trend).toBe('down')
    expect(goldDetailState(product, runs.slice(0, 4)).trend).toBe('flat')
  })
})

describe('missingSourceTables', () => {
  const sources = [
    { alias: 'p1', silver_table: 'uif_echo_{env}_silver_price.price_1' },
    { alias: 'hdr', silver_table: 'uif_echo_{env}_silver_price.price_list_header' },
    { alias: 'p', silver_table: 'uif_echo_{env}_silver_price.price' },
  ]
  const failed = (msg: string, status: GoldRunMetrics['status'] = 'FAILED') =>
    goldRunRow(run({ started_at: '2026-09-29T06:31:00Z', status, error_message: msg }))

  it('flags the source the missing-source error names, by table part, as a whole name', () => {
    expect(missingSourceTables(sources, failed('AnalysisException: Table or view not found: silver.price_1'))).toEqual([
      'uif_echo_{env}_silver_price.price_1',
    ])
  })

  it('matches the full name once {env} is resolved', () => {
    const row = failed('Table or view not found: uif_echo_dev_silver_price.price_list_header')
    expect(missingSourceTables(sources, row, 'dev')).toEqual(['uif_echo_{env}_silver_price.price_list_header'])
  })

  it('flags nothing unless the explanation is the missing-source rule', () => {
    expect(missingSourceTables(sources, failed('ORA-01017: invalid username/password price_1'))).toEqual([])
    expect(missingSourceTables(sources, failed('Table or view not found: silver.price_1', 'SUCCEEDED'))).toEqual([])
    expect(missingSourceTables(sources, null)).toEqual([])
  })

  it('flags nothing when the error names no configured source', () => {
    expect(missingSourceTables(sources, failed('Table or view not found: silver.stock_levels'))).toEqual([])
  })
})

describe('goldReadersOfSilver', () => {
  const golds: GoldPipelineConfig[] = [
    { gold_id: 'price', sources: [{ alias: 'l', silver_table: 'uif_echo_{env}_silver_price.PRICE_LIST' }] },
    { gold_id: 'product', sources: [{ alias: 'l', silver_table: 'other_db.price_list' }, { alias: 'i', silver_table: 'x.items' }] },
    { gold_id: 'stock', sources: [{ alias: 's', silver_table: 'x.stock' }] },
    { gold_id: 'bare' },
  ]

  it('matches on the full name with {env} resolved, or on the table part, ignoring case', () => {
    expect(goldReadersOfSilver(golds, 'uif_echo_dev_silver_price.price_list', 'dev')).toEqual(['price', 'product'])
  })

  it('is empty for no silver table or no golds', () => {
    expect(goldReadersOfSilver(golds, '')).toEqual([])
    expect(goldReadersOfSilver(undefined, 'x.stock')).toEqual([])
    expect(goldReadersOfSilver(golds, 'x.nothing')).toEqual([])
  })
})
