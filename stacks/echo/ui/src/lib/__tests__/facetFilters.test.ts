import { describe, it, expect } from 'vitest'
import { clearFacets, parseFacets, passesFacet, toggleFacet } from '../facetFilters'

describe('facetFilters', () => {
  it('parses comma lists per key, empty when absent, de-duplicated', () => {
    const p = new URLSearchParams('kind=a,b,a&layer=&other=x')
    expect(parseFacets(p, ['kind', 'layer', 'status'])).toEqual({ kind: ['a', 'b'], layer: [], status: [] })
  })

  it('toggles a value in and out and keeps unrelated keys', () => {
    const on = toggleFacet(new URLSearchParams('layer=silver&keep=1'), 'kind', 'reconcile')
    expect(on.get('kind')).toBe('reconcile')
    expect(on.get('keep')).toBe('1')
    const off = toggleFacet(on, 'kind', 'reconcile')
    expect(off.has('kind')).toBe(false)
    expect(off.get('layer')).toBe('silver')
  })

  it('clears only the facet keys', () => {
    const next = clearFacets(new URLSearchParams('kind=a&layer=b&layer2=c'), ['kind', 'layer'])
    expect(next.toString()).toBe('layer2=c')
  })

  it('passes when nothing is selected or the value is selected', () => {
    expect(passesFacet(undefined, 'x')).toBe(true)
    expect(passesFacet([], 'x')).toBe(true)
    expect(passesFacet(['x'], 'x')).toBe(true)
    expect(passesFacet(['y'], 'x')).toBe(false)
  })
})
