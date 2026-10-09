import { describe, it, expect } from 'vitest'
import {
  checkStatusKey,
  checkStatusView,
  checkSubjectLabel,
  computeDqStats,
  failedHint,
  filterChecks,
  linkedSync,
} from '../dqStats'
import type { DqCheckConfig, PipelineConfig } from '../types'

const NOW = new Date('2026-09-30T12:00:00Z')
const HOUR = 3_600_000
const ago = (h: number) => new Date(NOW.getTime() - h * HOUR).toISOString()

function check(over: Partial<DqCheckConfig> & { check_id: string }): DqCheckConfig {
  return {
    version: 'latest',
    kind: 'reconcile',
    enabled: true,
    subject: { layer: 'silver', database: 'db', table: over.check_id },
    schedule: { frequency: 'daily' },
    parameters: {},
    metadata: { created_at: '2026-09-01T00:00:00Z', owner: 'r' },
    last_run: null,
    ...over,
  }
}

describe('computeDqStats', () => {
  const checks = [
    check({ check_id: 'a', last_run: { ts: ago(1), status: 'SUCCEEDED' } }),
    check({ check_id: 'b', last_run: { ts: ago(2), status: 'FAILED' } }),
    check({ check_id: 'c', last_run: { ts: ago(3), status: 'SKIPPED' } }),
    check({ check_id: 'd', enabled: false, last_run: { ts: ago(30), status: 'FAILED' } }),
    check({ check_id: 'e', enabled: false }),
  ]

  it('counts active and disabled over every check', () => {
    const s = computeDqStats(checks, NOW)
    expect(s).toMatchObject({ total: 5, active: 3, disabled: 2 })
  })

  it('tallies last runs inside 24 h only, skipped as warning', () => {
    const s = computeDqStats(checks, NOW)
    expect(s).toMatchObject({ passed24h: 1, failed24h: 1, warning24h: 1 })
    expect(s.failedSubjects).toEqual(['db.b'])
  })

  it('ignores a missing or unreadable timestamp', () => {
    const s = computeDqStats([check({ check_id: 'x', last_run: { ts: 'nope', status: 'FAILED' } })], NOW)
    expect(s.failed24h).toBe(0)
  })

  it('names up to two failed subjects in the hint', () => {
    expect(failedHint({ failedSubjects: [] })).toBe('runs')
    expect(failedHint({ failedSubjects: ['d.a'] })).toBe('d.a')
    expect(failedHint({ failedSubjects: ['d.a', 'd.b', 'd.c', 'd.d'] })).toBe('d.a, d.b +2 more')
  })
})

describe('check rows', () => {
  it('status key and view: disabled wins, then the last run, then never ran', () => {
    const off = check({ check_id: 'a', enabled: false, last_run: { ts: ago(1), status: 'SUCCEEDED' } })
    expect(checkStatusKey(off)).toBe('DISABLED')
    expect(checkStatusView(off)?.label).toBe('Disabled')
    const passed = check({ check_id: 'b', last_run: { ts: ago(1), status: 'SUCCEEDED' } })
    expect(checkStatusKey(passed)).toBe('SUCCEEDED')
    expect(checkStatusView(passed)?.label).toBe('Passed')
    const never = check({ check_id: 'c' })
    expect(checkStatusKey(never)).toBe('NONE')
    expect(checkStatusView(never)).toBeNull()
  })

  it('linked sync is the pipeline whose silver table is the subject, case-insensitive', () => {
    const p = (id: string, database: string, table: string, enabled = true) =>
      ({ pipeline_id: id, silver: { enabled, database, table, primary_key_cols: [] } }) as unknown as PipelineConfig
    const pipelines = [p('one', 'db', 'other'), p('two', 'DB', 'Price'), p('off', 'db', 'price', false)]
    const subject = (layer: 'silver' | 'gold') => ({ subject: { layer, database: 'db', table: 'price' } })
    expect(linkedSync(pipelines, subject('silver'))?.pipeline_id).toBe('two')
    expect(linkedSync(pipelines, subject('gold'))).toBeUndefined()
    expect(linkedSync(undefined, subject('silver'))).toBeUndefined()
    expect(linkedSync([p('x', 'db', 'nope')], subject('silver'))).toBeUndefined()
  })

  it('subject label is layer · database.table', () => {
    expect(checkSubjectLabel(check({ check_id: 't' }))).toBe('silver · db.t')
  })

  it('filters on search (id or table) and the kind, status and layer facets', () => {
    const rows = [
      check({ check_id: 'rec-a', subject: { layer: 'silver', database: 'db', table: 'price' } }),
      check({ check_id: 'nr-b', kind: 'null-rate', subject: { layer: 'gold', database: 'g', table: 'product' } }),
      check({ check_id: 'rec-c', enabled: false }),
    ]
    const ids = (r: DqCheckConfig[]) => r.map((c) => c.check_id)
    expect(ids(filterChecks(rows, 'PRICE', {}))).toEqual(['rec-a'])
    expect(ids(filterChecks(rows, 'nr-', {}))).toEqual(['nr-b'])
    expect(ids(filterChecks(rows, '', { kind: ['reconcile'] }))).toEqual(['rec-a', 'rec-c'])
    expect(ids(filterChecks(rows, '', { status: ['DISABLED'] }))).toEqual(['rec-c'])
    expect(ids(filterChecks(rows, '', { layer: ['gold'] }))).toEqual(['nr-b'])
    expect(ids(filterChecks(rows, '', { kind: [], status: [], layer: [] }))).toHaveLength(3)
  })
})
