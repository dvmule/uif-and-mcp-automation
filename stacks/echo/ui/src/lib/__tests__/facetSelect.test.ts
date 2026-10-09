import { describe, it, expect } from 'vitest'
import { FRESHNESS_OPTIONS, PIPELINE_STATUS_OPTIONS, facetItems, facetShown, facetUrlValues } from '../facetSelect'
import { setFacet } from '../facetFilters'

const ITEMS = facetItems(['b', 'a', 'a', null, undefined, 'c'])

describe('facetItems', () => {
  it('lists the distinct loaded values, sorted, with the row count as meta', () => {
    expect(ITEMS).toEqual([
      { key: 'a', label: 'a', meta: 2 },
      { key: 'b', label: 'b', meta: 1 },
      { key: 'c', label: 'c', meta: 1 },
    ])
  })

  it('keeps the vocabulary order, zero counts included, then adds loaded and URL values it lacks', () => {
    const items = facetItems(['FAILED', 'FAILED', 'ODD'], {
      vocab: [{ key: 'SUCCEEDED', label: 'Succeeded' }, { key: 'FAILED', label: 'Failed' }],
      selected: ['ZZZ'],
      label: (k) => k.toLowerCase(),
    })
    expect(items.map((i) => [i.key, i.label, i.meta])).toEqual([
      ['SUCCEEDED', 'Succeeded', 0],
      ['FAILED', 'Failed', 2],
      ['ODD', 'odd', 1],
      ['ZZZ', 'zzz', 0],
    ])
  })

  it('names the sync and gold status and freshness vocabularies in sentence case', () => {
    expect(PIPELINE_STATUS_OPTIONS.map((o) => o.label)).toEqual([
      'Running', 'Succeeded', 'Warning', 'Failed', 'Skipped', 'Disabled',
    ])
    expect(FRESHNESS_OPTIONS.map((o) => o.label)).toEqual(['Fresh', 'Stale', 'Overdue', 'Unknown'])
  })
})

describe('facetShown / facetUrlValues', () => {
  it('shows every option ticked when the URL holds none, else the URL values', () => {
    expect(facetShown(ITEMS, [])).toEqual(['a', 'b', 'c'])
    expect(facetShown(ITEMS, ['b'])).toEqual(['b'])
  })

  it('picking every option, or none, drops the key', () => {
    expect(facetUrlValues(ITEMS, ['a'], ['a', 'b', 'c'])).toEqual([])
    expect(facetUrlValues(ITEMS, ['a'], [])).toEqual([])
    expect(facetUrlValues(ITEMS, [], [])).toEqual([])
  })

  it('from the all state, a click on one option keeps only that option', () => {
    expect(facetUrlValues(ITEMS, [], ['a', 'c'])).toEqual(['b'])
  })

  it('otherwise writes the subset as picked', () => {
    expect(facetUrlValues(ITEMS, ['b'], ['b', 'a'])).toEqual(['b', 'a'])
    expect(facetUrlValues(ITEMS, ['a', 'b'], ['a'])).toEqual(['a'])
  })
})

describe('setFacet', () => {
  it('writes the full list under one key and keeps the others; an empty list removes the key', () => {
    const on = setFacet(new URLSearchParams('layer=silver&keep=1'), 'kind', ['a', 'b'])
    expect(on.toString()).toBe('layer=silver&keep=1&kind=a%2Cb')
    expect(setFacet(on, 'kind', []).toString()).toBe('layer=silver&keep=1')
  })
})
