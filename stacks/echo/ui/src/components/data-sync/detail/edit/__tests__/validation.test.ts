import { describe, it, expect } from 'vitest'
import { validateDraft } from '@/components/data-sync/detail/edit/validation'

describe('validateDraft', () => {
  it('returns ERR for missing Silver PK when Silver enabled', () => {
    const v = validateDraft({ silver: { enabled: true, database: 'd', table: 't', primary_key_cols: [] } } as any)
    expect(v.errors).toContainEqual(expect.objectContaining({ section: 'silver', message: expect.stringMatching(/primary key/i) }))
  })

  it('returns WARN when incremental predicates exceed 20', () => {
    const preds = Array.from({ length: 24 }, (_, i) => `p${i}`)
    const v = validateDraft({ extract: { jdbc_predicates: preds }, silver: { enabled: false } } as any)
    expect(v.warnings).toContainEqual(expect.objectContaining({ section: 'extraction', message: expect.stringMatching(/exceeds recommended cap/i) }))
  })

  it('returns empty arrays for a healthy draft', () => {
    const v = validateDraft({
      extract: { jdbc_predicates: ['p1'] },
      silver: { enabled: true, database: 'd', table: 't', primary_key_cols: ['id'] },
    } as any)
    expect(v.errors).toEqual([])
    expect(v.warnings).toEqual([])
  })
})
