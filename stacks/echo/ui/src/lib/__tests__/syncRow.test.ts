import { describe, it, expect } from 'vitest'
import {
  formatGoldScheduleLabel,
  goldCadenceLabel,
  goldCadenceMinutes,
  parseStartTime,
  goldCatalogPath,
  goldFrequencyHours,
  formatRunTime,
  goldFreshness,
  goldMatchesFilters,
  goldRow,
  goldRunStatus,
  goldStatus,
  goldTableFqn,
  latestGoldRunByEntity,
  pipelineRow,
  rowLabel,
  rowSearchText,
} from '../syncRow'
import type { SyncFilterState } from '../filters'
import type { GoldPipelineConfig, GoldRunMetrics, PipelineConfig } from '../types'

const product: GoldPipelineConfig = {
  gold_id: 'product',
  enabled: true,
  domain: 'product',
  target: { database: 'uif_echo_{env}_gold_product', table: 'product' },
  schedule: { frequency: 'gold-24h', start_time: '06:30' },
  metadata: { last_run_at: new Date(Date.now() - 2 * 3600_000).toISOString() },
}

const NO_FILTERS: SyncFilterState = { domain: [], source: [], status: [], freshness: [], tag: [] }

describe('goldTableFqn / goldCatalogPath', () => {
  it('resolves {env} and lowercases', () => {
    expect(goldTableFqn(product, 'dev')).toBe('uif_echo_dev_gold_product.product')
    expect(goldCatalogPath(product, 'dev')).toBe('/m/catalog/datasets/gold/aws/uif_echo_dev_gold_product/product')
  })

  it('passes an already-resolved database through unchanged', () => {
    const g = { ...product, target: { database: 'uif_echo_dev_gold_product', table: 'Product' } }
    expect(goldTableFqn(g, 'uat')).toBe('uif_echo_dev_gold_product.product')
  })

  it('is null when the config cannot name its table', () => {
    expect(goldTableFqn({ gold_id: 'x', target: { database: 'db' } }, 'dev')).toBeNull()
    expect(goldCatalogPath({ gold_id: 'x' }, 'dev')).toBeNull()
  })

  // /m/data-sync/syncs/{id} resolves a pipeline_id and 404s on a gold id — the
  // trap UIFPE-502, 504 and the catalog work each had to design around.
  it('never links into the sync detail route', () => {
    expect(goldCatalogPath(product, 'dev')).not.toContain('/m/data-sync/syncs/')
  })
})

describe('goldFrequencyHours', () => {
  it('reads the gold cadence ladder', () => {
    expect(goldFrequencyHours('gold-24h')).toBe(24)
    expect(goldFrequencyHours('gold-1h')).toBe(1)
    expect(goldFrequencyHours('gold-30m')).toBe(0.5)
  })
  it('treats anything else as a day', () => {
    expect(goldFrequencyHours('daily')).toBe(24)
    expect(goldFrequencyHours(undefined)).toBe(24)
    expect(goldFrequencyHours('gold-')).toBe(24)
  })
  // Un-resynced configs still carry the pre-UIFPE-149 names.
  it('honours the legacy aliases gold_frequencies.py still accepts', () => {
    expect(goldCadenceMinutes('gold')).toBe(1440)
    expect(goldCadenceMinutes('gold-hourly')).toBe(60)
    expect(goldCadenceMinutes('gold-60m')).toBe(60)
    expect(goldCadenceMinutes('weekly')).toBeNull()
  })

  it('knows the weekly rung (UIFPE-532): 7 days, labelled weekly', () => {
    expect(goldCadenceMinutes('gold-weekly')).toBe(7 * 1440)
    expect(goldFrequencyHours('gold-weekly')).toBe(168)
    expect(goldCadenceLabel('gold-weekly')).toBe('weekly')
  })
})

describe('goldCadenceLabel', () => {
  it("speaks the replicate layer's vocabulary", () => {
    expect(goldCadenceLabel('gold-24h')).toBe('daily')
    expect(goldCadenceLabel('gold')).toBe('daily')
    expect(goldCadenceLabel('gold-1h')).toBe('hourly')
    expect(goldCadenceLabel('gold-6h')).toBe('every 6 hours')
    expect(goldCadenceLabel('gold-30m')).toBe('every 30 minutes')
  })
  it('shows an unknown token as-is rather than guessing', () => {
    expect(goldCadenceLabel('gold-fortnightly')).toBe('gold-fortnightly')
    expect(goldCadenceLabel(undefined)).toBe('—')
  })
})

describe('parseStartTime', () => {
  it('is as strict as the Python twin', () => {
    expect(parseStartTime('06:30')).toBe(390)
    expect(parseStartTime('6:5')).toBe(365)
    expect(parseStartTime(':30')).toBeNull()
    expect(parseStartTime('24:00')).toBeNull()
    expect(parseStartTime('6e1:00')).toBeNull()
    expect(parseStartTime(undefined)).toBeNull()
  })
})

describe('goldFreshness', () => {
  it('is fresh within one cadence', () => {
    expect(goldFreshness(product)).toBe('fresh')
  })
  it('is stale within two cadences and critical beyond', () => {
    const hours = (h: number) => ({ last_run_at: new Date(Date.now() - h * 3600_000).toISOString() })
    expect(goldFreshness({ ...product, metadata: hours(30) })).toBe('stale')
    expect(goldFreshness({ ...product, metadata: hours(60) })).toBe('critical')
  })
  it('is critical with no run and unknown when disabled', () => {
    expect(goldFreshness({ ...product, metadata: {} })).toBe('critical')
    expect(goldFreshness({ ...product, enabled: false })).toBe('unknown')
  })
})

describe('goldStatus', () => {
  // The config says WHEN it last ran, not HOW it ended; /gold-runs is not live.
  it('is UNKNOWN (undefined) for an enabled entity, never an invented SUCCEEDED', () => {
    expect(goldStatus(product)).toBeUndefined()
  })
  it('is DISABLED when not enabled', () => {
    expect(goldStatus({ ...product, enabled: false })).toBe('DISABLED')
    expect(goldStatus({ gold_id: 'x' })).toBe('DISABLED')
  })
})

describe('formatGoldScheduleLabel', () => {
  // Same shape as formatDailyScheduleLabel on a sync row, so the Schedule
  // column reads as one column, not two dialects.
  // 2026-09-23 is a Wednesday; Chicago is on CDT (UTC-5) then, CST (UTC-6) in January.
  const sep = new Date('2026-09-23T12:00:00Z')

  it('renders like a sync row: cadence word, then the viewer clock and UTC', () => {
    expect(formatGoldScheduleLabel(product.schedule, 'UTC')).toBe('daily · 06:30 UTC')
    expect(formatGoldScheduleLabel({ frequency: 'gold-1h', start_time: '00:45' }, 'UTC')).toBe('hourly · 00:45 UTC')
    expect(formatGoldScheduleLabel(product.schedule, 'America/Chicago', sep)).toBe('daily · 1:30 AM CDT · 06:30 UTC')
  })

  it('names the weekly run days as the viewer sees them, not as UTC stores them', () => {
    const weekly = { frequency: 'gold-weekly', weekly_days: ['WED', 'FRI'], start_time: '03:00' }
    expect(formatGoldScheduleLabel(weekly, 'UTC')).toBe('weekly on Wed, Fri · 03:00 UTC')
    // Wednesday 03:00 UTC is Tuesday 22:00 in Chicago: the day flips with the time
    expect(formatGoldScheduleLabel(weekly, 'America/Chicago', sep)).toBe('weekly on Tue, Thu · 10:00 PM CDT · 03:00 UTC')
    // no day set stored yet: the runtime default, Monday
    expect(formatGoldScheduleLabel({ frequency: 'gold-weekly', start_time: '06:30' }, 'UTC')).toBe('weekly on Mon · 06:30 UTC')
  })
  it('degrades to the cadence alone without a usable start time', () => {
    expect(formatGoldScheduleLabel({ frequency: 'gold-1h' })).toBe('hourly')
    expect(formatGoldScheduleLabel({ frequency: 'gold-24h', start_time: ':30' })).toBe('daily')
    expect(formatGoldScheduleLabel(undefined)).toBe('—')
  })
})

describe('goldMatchesFilters', () => {
  it('passes with no filters and matches its own domain', () => {
    expect(goldMatchesFilters(product, NO_FILTERS)).toBe(true)
    expect(goldMatchesFilters(product, { ...NO_FILTERS, domain: ['product'] })).toBe(true)
    expect(goldMatchesFilters(product, { ...NO_FILTERS, domain: ['price'] })).toBe(false)
  })
  it('is excluded by a status filter unless that status is DISABLED and it is', () => {
    expect(goldMatchesFilters(product, { ...NO_FILTERS, status: ['SUCCEEDED'] })).toBe(false)
    expect(goldMatchesFilters({ ...product, enabled: false }, { ...NO_FILTERS, status: ['DISABLED'] })).toBe(true)
  })
  it('answers freshness, and is excluded by any tag filter', () => {
    expect(goldMatchesFilters(product, { ...NO_FILTERS, freshness: ['fresh'] })).toBe(true)
    expect(goldMatchesFilters(product, { ...NO_FILTERS, freshness: ['critical'] })).toBe(false)
    expect(goldMatchesFilters(product, { ...NO_FILTERS, tag: ['anything'] })).toBe(false)
  })
})

describe('goldMatchesFilters with a real run status', () => {
  it('matches the status the caller derived from the latest run', () => {
    expect(goldMatchesFilters(product, { ...NO_FILTERS, status: ['FAILED'] }, 'FAILED')).toBe(true)
    expect(goldMatchesFilters(product, { ...NO_FILTERS, status: ['FAILED'] }, 'SUCCEEDED')).toBe(false)
  })
  it('an entity with no known status is still excluded by any status filter', () => {
    expect(goldMatchesFilters(product, { ...NO_FILTERS, status: ['FAILED'] }, undefined)).toBe(false)
  })
  it('falls back to the domain in metadata when the row has no top-level domain', () => {
    const g = { ...product, domain: undefined, metadata: { ...product.metadata, domain: 'price' } }
    expect(goldMatchesFilters(g, { ...NO_FILTERS, domain: ['price'] })).toBe(true)
  })
})

function goldRunAt(gold_id: string, started_at: string, status = 'SUCCEEDED'): GoldRunMetrics {
  return { gold_id, run_id: `${gold_id}-${started_at}`, job_name: 'gold-transform', status, started_at } as GoldRunMetrics
}

describe('latestGoldRunByEntity', () => {
  it('is empty for no runs', () => {
    expect(latestGoldRunByEntity([]).size).toBe(0)
  })
  it('keeps the newest run per gold_id whatever the order of the page', () => {
    const items = [
      goldRunAt('product', '2026-09-28T06:00:00Z', 'FAILED'),
      goldRunAt('price', '2026-09-28T05:00:00Z'),
      goldRunAt('product', '2026-09-29T06:00:00Z'),
      goldRunAt('product', '2026-09-27T06:00:00Z'),
    ]
    const latest = latestGoldRunByEntity(items)
    expect([...latest.keys()].sort()).toEqual(['price', 'product'])
    expect(latest.get('product')?.started_at).toBe('2026-09-29T06:00:00Z')
    expect(latest.get('product')?.status).toBe('SUCCEEDED')
  })
  it('compares instants, not strings, across offsets', () => {
    const items = [
      goldRunAt('product', '2026-09-29T08:00:00+02:00', 'FAILED'),   // 06:00 UTC
      goldRunAt('product', '2026-09-29T05:00:00Z'),
    ]
    expect(latestGoldRunByEntity(items).get('product')?.status).toBe('FAILED')
  })
  it('on a tie the first in the list wins; an unparsable date loses to a parsable one', () => {
    const tie = latestGoldRunByEntity([
      goldRunAt('a', '2026-09-29T06:00:00Z', 'FAILED'),
      goldRunAt('a', '2026-09-29T06:00:00Z', 'SUCCEEDED'),
    ])
    expect(tie.get('a')?.status).toBe('FAILED')
    const bad = latestGoldRunByEntity([goldRunAt('a', 'not-a-date', 'FAILED'), goldRunAt('a', '2026-09-29T06:00:00Z')])
    expect(bad.get('a')?.status).toBe('SUCCEEDED')
  })
})

describe('goldRunStatus', () => {
  it('is DISABLED whatever the run says when the entity is off', () => {
    expect(goldRunStatus({ ...product, enabled: false }, goldRunAt('product', '2026-09-29T06:00:00Z'))).toBe('DISABLED')
  })
  it('is undefined (Unknown) for an enabled entity with no run', () => {
    expect(goldRunStatus(product, undefined)).toBeUndefined()
  })
  it('maps the latest run through the shared status normaliser', () => {
    expect(goldRunStatus(product, goldRunAt('product', '2026-09-29T06:00:00Z', 'SUCCEEDED'))).toBe('SUCCEEDED')
    expect(goldRunStatus(product, goldRunAt('product', '2026-09-29T06:00:00Z', 'TIMEOUT'))).toBe('FAILED')
    expect(goldRunStatus(product, goldRunAt('product', '2026-09-29T06:00:00Z', 'STARTING'))).toBe('RUNNING')
  })
})

describe('formatRunTime', () => {
  const now = new Date('2026-09-29T12:00:00Z')
  it('is the UTC clock time for a run today', () => {
    expect(formatRunTime('2026-09-29T06:31:12Z', now)).toBe('06:31 UTC')
  })
  it('adds the day for an earlier one', () => {
    expect(formatRunTime('2026-09-28T06:32:00Z', now)).toBe('28 Sep, 06:32 UTC')
  })
  it('is a dash for nothing or nonsense', () => {
    expect(formatRunTime(undefined, now)).toBe('—')
    expect(formatRunTime('garbage', now)).toBe('—')
  })
})

describe('row labels', () => {
  const p = {
    pipeline_id: 'inv_one',
    extract: { table: 'INV.ONE' },
  } as unknown as PipelineConfig

  it('labels and searches a pipeline by table and id', () => {
    expect(rowLabel(pipelineRow(p), 'dev')).toBe('INV.ONE')
    expect(rowSearchText(pipelineRow(p), 'dev')).toBe('inv.one inv_one')
  })
  it('labels and searches a gold row by resolved fqn and gold_id', () => {
    expect(rowLabel(goldRow(product), 'dev')).toBe('uif_echo_dev_gold_product.product')
    expect(rowSearchText(goldRow(product), 'dev')).toContain('product')
    expect(goldRow(product).id).toBe('gold:product')
  })
})
