import { describe, expect, it } from 'vitest'
import type { ActivityItem } from '../types'
import { MAX_ATTENTION_ROWS, areaLabel, buildAttention, formatClockUtc } from '../homeAttention'

function item(over: Partial<ActivityItem>): ActivityItem {
  return {
    ts: '2026-09-29T10:00:00Z',
    module: 'sync',
    subject: 'orders',
    status: 'FAILED',
    message: 'boom',
    link_path: '/m/data-sync/syncs/orders',
    ...over,
  }
}

describe('areaLabel', () => {
  it.each([
    ['sync', 'Replicate'],
    ['export', 'Export'],
    ['dq', 'Quality'],
    ['something_else', 'something_else'],
  ])('%s -> %s', (module, label) => {
    expect(areaLabel(module)).toBe(label)
  })
})

describe('formatClockUtc', () => {
  it('reads HH:MM in UTC', () => {
    expect(formatClockUtc('2026-09-29T14:25:41Z')).toBe('14:25')
  })
  it.each([[undefined], [null], [''], ['not a date']])('gives empty for %s', (v) => {
    expect(formatClockUtc(v)).toBe('')
  })
})

describe('buildAttention', () => {
  it.each([
    ['FAILED', 'Failed', 'fail'],
    ['TIMEOUT', 'Failed', 'fail'],
    ['ERROR', 'Failed', 'fail'],
    ['STOPPED', 'Stopped', 'mute'],
  ])('lists a newest %s item as %s', (status, label, tone) => {
    const { rows, total } = buildAttention([item({ status })])
    expect(total).toBe(1)
    expect(rows[0].subject).toBe('orders')
    expect(rows[0].view.label).toBe(label)
    expect(rows[0].view.tone).toBe(tone)
  })

  it.each([['SUCCEEDED'], ['RUNNING'], ['SKIPPED'], ['NOT_IMPLEMENTED']])(
    'does not list %s',
    (status) => {
      expect(buildAttention([item({ status })]).total).toBe(0)
    },
  )

  it('keeps only the newest item per subject', () => {
    const { rows } = buildAttention([
      item({ ts: '2026-09-29T08:00:00Z', message: 'old failure' }),
      item({ ts: '2026-09-29T09:00:00Z', message: 'new failure' }),
    ])
    expect(rows).toHaveLength(1)
    expect(rows[0].message).toBe('new failure')
  })

  it('lets a newer success hide an older failure', () => {
    const { total } = buildAttention([
      item({ ts: '2026-09-29T09:00:00Z', status: 'SUCCEEDED' }),
      item({ ts: '2026-09-29T08:00:00Z', status: 'FAILED' }),
    ])
    expect(total).toBe(0)
  })

  it('treats the same subject in two areas as two objects', () => {
    const { total } = buildAttention([
      item({ module: 'sync', subject: 'orders' }),
      item({ module: 'dq', subject: 'orders' }),
    ])
    expect(total).toBe(2)
  })

  it('orders failed newest first, then stopped, then stale gold', () => {
    const { rows } = buildAttention(
      [
        item({ subject: 'a', ts: '2026-09-29T07:00:00Z' }),
        item({ subject: 'b', ts: '2026-09-29T09:00:00Z', status: 'STOPPED' }),
        item({ subject: 'c', ts: '2026-09-29T08:00:00Z' }),
      ],
      { stale_entities: ['stock'] },
    )
    expect(rows.map((r) => r.subject)).toEqual(['c', 'a', 'b', 'stock'])
  })

  it('appends one row per stale gold table, linked to Transform', () => {
    const { rows } = buildAttention([], { stale_entities: ['stock', 'price'] })
    expect(rows.map((r) => r.subject)).toEqual(['stock', 'price'])
    expect(rows[0]).toMatchObject({
      area: 'Transform',
      href: '/m/data-sync/gold',
      ts: null,
      message: 'no successful run inside its cadence',
    })
    expect(rows[0].view.label).toBe('Stale')
  })

  it('copes with no gold data', () => {
    expect(buildAttention([], null).total).toBe(0)
    expect(buildAttention([], undefined).total).toBe(0)
  })

  it('caps at ten rows and counts the rest', () => {
    const items = Array.from({ length: 12 }, (_, i) => item({ subject: `s${i}` }))
    const out = buildAttention(items)
    expect(out.rows).toHaveLength(MAX_ATTENTION_ROWS)
    expect(out.total).toBe(12)
    expect(out.more).toBe(2)
  })
})
