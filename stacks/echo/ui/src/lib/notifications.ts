import type { AlertRuleStatus, AlertSubscriber } from './types'

/** UIFPE-543: pure helpers behind the Notifications page and the per-table
 *  card. The server normalises and validates too; these keep the page honest
 *  before a request is made and give the two surfaces one vocabulary. */

const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/

export function isValidEmail(value: string): boolean {
  const v = value.trim()
  return v.length > 0 && v.length <= 254 && EMAIL.test(v)
}

/** "All tables and platform alerts" for the operator row, else the tables. */
export function describeScope(row: Pick<AlertSubscriber, 'gold_ids'>): string {
  if (row.gold_ids === null || row.gold_ids.length === 0) return 'All tables and platform alerts'
  return row.gold_ids.join(', ')
}

export function hearsAbout(row: Pick<AlertSubscriber, 'gold_ids' | 'enabled'>, goldId: string): boolean {
  return row.enabled && (row.gold_ids === null || row.gold_ids.includes(goldId))
}

export type RuleTone = 'ok' | 'alarm' | 'quiet'

export function ruleTone(state: string): RuleTone {
  if (state === 'ALARM') return 'alarm'
  if (state === 'OK') return 'ok'
  return 'quiet'
}

export function ruleStateLabel(state: string): string {
  if (state === 'ALARM') return 'Alarm'
  if (state === 'OK') return 'OK'
  if (state === 'INSUFFICIENT_DATA') return 'No data yet'
  return state
}

/** Rules grouped for display: one group per entity (sorted), then the fleet
 *  ("Platform") group last. */
export function groupRules(rules: AlertRuleStatus[]): Array<{ key: string; label: string; rules: AlertRuleStatus[] }> {
  const byEntity = new Map<string, AlertRuleStatus[]>()
  const fleet: AlertRuleStatus[] = []
  for (const r of rules) {
    if (r.gold_id) byEntity.set(r.gold_id, [...(byEntity.get(r.gold_id) ?? []), r])
    else fleet.push(r)
  }
  const groups = [...byEntity.keys()].sort().map(k => ({
    key: k, label: k, rules: [...byEntity.get(k)!].sort((a, b) => a.condition.localeCompare(b.condition)),
  }))
  if (fleet.length) groups.push({ key: '__platform', label: 'Platform', rules: [...fleet].sort((a, b) => a.condition.localeCompare(b.condition)) })
  return groups
}
