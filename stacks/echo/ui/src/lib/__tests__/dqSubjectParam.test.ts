import { describe, it, expect } from 'vitest'
import { parseDqSubjectParam } from '../dqSubjectParam'

describe('parseDqSubjectParam', () => {
  it('reads layer.database.table, the form the sync trust card builds', () => {
    expect(parseDqSubjectParam('silver.echo_dev_silver_price.price_list')).toEqual({
      layer: 'silver',
      database: 'echo_dev_silver_price',
      table: 'price_list',
    })
    expect(parseDqSubjectParam('gold.uif_echo_dev_gold_product.product')).toEqual({
      layer: 'gold',
      database: 'uif_echo_dev_gold_product',
      table: 'product',
    })
  })

  it('defaults the layer to silver when it is left out', () => {
    expect(parseDqSubjectParam('echo_dev_silver_price.price_list')).toEqual({
      layer: 'silver',
      database: 'echo_dev_silver_price',
      table: 'price_list',
    })
  })

  it.each([null, undefined, '', 'price_list', 'bronze.db.t', 'a.b.c.d', 'db.', '.t', 'db.t;drop', 'silver.db.t x'])(
    'returns null for %j',
    (raw) => {
      expect(parseDqSubjectParam(raw)).toBeNull()
    },
  )
})
