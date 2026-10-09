import { describe, expect, it } from 'vitest'
import { describeScope, groupRules, hearsAbout, isValidEmail, ruleStateLabel, ruleTone } from '@/lib/notifications'
import type { AlertRuleStatus } from '@/lib/types'

function rule(name: string, gold_id: string | null, condition: string, state = 'OK'): AlertRuleStatus {
  return { name, gold_id, condition, state, emails: true }
}

describe('notifications helpers (UIFPE-543)', () => {
  it('validates addresses the way the server does', () => {
    expect(isValidEmail('a@pearson.com')).toBe(true)
    expect(isValidEmail('  First.Last@pearson.com ')).toBe(true)
    for (const bad of ['', 'nope', 'a@b', 'a b@c.io', '@pearson.com', 'a@'.padEnd(260, 'x') + '.io']) {
      expect(isValidEmail(bad)).toBe(false)
    }
  })

  it('describes the operator row and a filtered row', () => {
    expect(describeScope({ gold_ids: null })).toBe('All tables and platform alerts')
    expect(describeScope({ gold_ids: [] })).toBe('All tables and platform alerts')
    expect(describeScope({ gold_ids: ['price', 'product'] })).toBe('price, product')
  })

  it('hearsAbout mirrors the notifier: no filter hears all, a filter hears its tables, disabled hears nothing', () => {
    expect(hearsAbout({ enabled: true, gold_ids: null }, 'product')).toBe(true)
    expect(hearsAbout({ enabled: true, gold_ids: ['product'] }, 'product')).toBe(true)
    expect(hearsAbout({ enabled: true, gold_ids: ['price'] }, 'product')).toBe(false)
    expect(hearsAbout({ enabled: false, gold_ids: null }, 'product')).toBe(false)
  })

  it('groups rules per entity, sorted, with the fleet last as Platform', () => {
    const groups = groupRules([
      rule('n1', 'product', 'run failed', 'ALARM'),
      rule('n2', null, 'dispatch silent'),
      rule('n3', 'marketing_copy', 'run too long'),
      rule('n4', 'product', 'freshness breached'),
    ])
    expect(groups.map(g => g.label)).toEqual(['marketing_copy', 'product', 'Platform'])
    expect(groups[1].rules.map(r => r.condition)).toEqual(['freshness breached', 'run failed'])
    expect(groups[2].key).toBe('__platform')
    expect(groupRules([rule('n', 'stock', 'x')]).map(g => g.label)).toEqual(['stock'])  // no empty Platform group
  })

  it('maps alarm state to a tone and a label', () => {
    expect([ruleTone('OK'), ruleTone('ALARM'), ruleTone('INSUFFICIENT_DATA')]).toEqual(['ok', 'alarm', 'quiet'])
    expect(ruleStateLabel('INSUFFICIENT_DATA')).toBe('No data yet')
    expect(ruleStateLabel('ALARM')).toBe('Alarm')
  })
})
