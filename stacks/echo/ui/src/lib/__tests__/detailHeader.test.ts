import { describe, it, expect } from 'vitest'
import {
  formatMinutesAhead,
  formatUtcAhead,
  goldHeaderFacts,
  goldPrimaryAction,
  syncHeaderFacts,
  syncPrimaryAction,
} from '../detailHeader'
import type { GoldPipelineConfig, PipelineConfig } from '../types'

describe('syncPrimaryAction', () => {
  it('follows the state: Run now, Retry now, Enable, Open run drawer', () => {
    expect(syncPrimaryAction('healthy', false)).toEqual({ label: 'Run now', kind: 'run', disabled: false })
    expect(syncPrimaryAction('degraded', false).label).toBe('Run now')
    expect(syncPrimaryAction('failing', false)).toEqual({ label: 'Retry now', kind: 'run', disabled: false })
    expect(syncPrimaryAction('disabled', false)).toEqual({ label: 'Enable', kind: 'enable', disabled: false })
    expect(syncPrimaryAction('running', true)).toEqual({ label: 'Open run drawer', kind: 'open-drawer', disabled: false })
  })

  it('holds a run while one is already in flight, but never Enable', () => {
    expect(syncPrimaryAction('healthy', true).disabled).toBe(true)
    expect(syncPrimaryAction('failing', true).disabled).toBe(true)
    expect(syncPrimaryAction('disabled', true).disabled).toBe(false)
  })
})

describe('goldPrimaryAction', () => {
  const on = { enabled: true, running: false, runLocked: false, runPending: false }
  it('Enable on a disabled entity, whatever else is true', () => {
    expect(goldPrimaryAction({ ...on, enabled: false, running: true })).toEqual({ label: 'Enable', kind: 'enable', disabled: false })
  })
  it('Run now, held while running, locked by this session, or pending', () => {
    expect(goldPrimaryAction(on)).toEqual({ label: 'Run now', kind: 'run', disabled: false })
    expect(goldPrimaryAction({ ...on, running: true })).toEqual({ label: 'Run in progress', kind: 'run', disabled: true })
    expect(goldPrimaryAction({ ...on, runLocked: true })).toEqual({ label: 'Run started', kind: 'run', disabled: true })
    expect(goldPrimaryAction({ ...on, runPending: true })).toEqual({ label: 'Run now', kind: 'run', disabled: true })
  })
})

describe('header facts', () => {
  it('formats time ahead', () => {
    expect(formatMinutesAhead(45)).toBe('45 min')
    expect(formatMinutesAhead(900)).toBe('15 h')
    const now = new Date('2026-09-29T14:00:00Z')
    expect(formatUtcAhead(new Date('2026-09-29T18:30:00Z'), now)).toBe('today 18:30 UTC')
    expect(formatUtcAhead(new Date('2026-09-30T06:30:00Z'), now)).toBe('tomorrow 06:30 UTC')
    expect(formatUtcAhead(new Date('2026-10-02T06:30:00Z'), now)).toBe('2 Oct 06:30 UTC')
  })

  it('sync: id · cloud · layers · mode · schedule · next run', () => {
    const p = {
      pipeline_id: 'eds-price-list-daily',
      extract: { table: 'EDS.PRICE_LIST', mode: 'incremental' },
      silver: { enabled: true },
      schedule: { frequency: 'daily', daily_slot: 6 },
    } as unknown as PipelineConfig
    const facts = syncHeaderFacts(p, 900)
    expect(facts.slice(0, 4)).toEqual(['eds-price-list-daily', 'AWS', 'Oracle → bronze → silver', 'incremental'])
    expect(facts[4]).toMatch(/^daily · \d\d:\d\d UTC \(.+ ET\)$/)
    expect(facts[5]).toBe('next run in 15 h')
  })

  it('sync without silver, on demand, with no next run', () => {
    const p = { pipeline_id: 'p', destination: { cloud: 'gcp' }, extract: { mode: 'full' }, schedule: {} } as unknown as PipelineConfig
    expect(syncHeaderFacts(p, null)).toEqual(['p', 'GCP', 'Oracle → bronze', 'full'])
  })

  it('gold: domain · sources · cadence · next run in UTC', () => {
    const g: GoldPipelineConfig = {
      gold_id: 'price', enabled: true, domain: 'Price',
      sources: [{ alias: 'a', silver_table: 'x.a' }, { alias: 'b', silver_table: 'x.b' }],
      schedule: { frequency: 'gold-24h', start_time: '06:30' },
    }
    const now = new Date('2026-09-29T13:31:42Z')
    expect(goldHeaderFacts(g, { showNextRun: true, now, zone: 'UTC' })).toEqual([
      'Price',
      'built from 2 silver tables',
      'daily · 06:30 UTC',
      'next run tomorrow 06:30 UTC',
    ])
    expect(goldHeaderFacts({ ...g, sources: [g.sources![0]] }, { showNextRun: false, now, zone: 'UTC' })).toEqual([
      'Price',
      'built from 1 silver table',
      'daily · 06:30 UTC',
    ])
  })
})
