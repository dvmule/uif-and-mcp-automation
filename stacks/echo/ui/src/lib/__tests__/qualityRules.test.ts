import { describe, it, expect } from 'vitest'
import { qualityRules } from '../qualityRules'

const QUALITY = { not_null_cols: ['PRICE_LIST_ID', 'LIST_HEADER_ID'], min_rows_expected: 1000 }

describe('qualityRules', () => {
  it('builds one rule per not-null column and one for minimum rows, from the config', () => {
    const rules = qualityRules(QUALITY, null)
    expect(rules.map((r) => [r.name, r.context])).toEqual([
      ['PRICE_LIST_ID', 'not null'],
      ['LIST_HEADER_ID', 'not null'],
      ['Minimum rows', '≥ 1,000 expected'],
    ])
  })

  it('scores each rule from the last run’s quality_result', () => {
    const rules = qualityRules(QUALITY, {
      not_null_checks: {
        PRICE_LIST_ID: { passed: true, null_count: 0 },
        list_header_id: { passed: false, null_count: 3 },
      },
      min_rows: { passed: true, actual: 5120, expected: 1000 },
    })
    expect(rules.map((r) => `${r.passed}/${r.total}`)).toEqual(['1/1', '0/1', '1/1'])
  })

  it('marks every rule not evaluated when the last run has no quality_result', () => {
    for (const result of [null, undefined]) {
      expect(qualityRules(QUALITY, result).every((r) => r.total === 0)).toBe(true)
    }
  })

  it('marks one rule not evaluated when the result is silent on it', () => {
    const rules = qualityRules(QUALITY, { min_rows: { passed: false, actual: 2, expected: 1000 } })
    expect(rules.map((r) => `${r.passed}/${r.total}`)).toEqual(['0/0', '0/0', '0/1'])
  })

  it('has no rules when the sync configures none', () => {
    expect(qualityRules(undefined, null)).toEqual([])
    expect(qualityRules({}, { min_rows: { passed: true, actual: 1, expected: 1 } })).toEqual([])
  })
})
