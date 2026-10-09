import { describe, it, expect } from 'vitest'
import { watermarkRow, watermarkRows } from '../watermarkRows'
import type { PipelineConfig } from '../types'

const hoursAgo = (h: number) => new Date(Date.now() - h * 3_600_000).toISOString()

function pipe(id: string, extract: Partial<PipelineConfig['extract']>, over: Partial<PipelineConfig> = {}): PipelineConfig {
  return {
    pipeline_id: id,
    enabled: true,
    schedule: { frequency: 'daily' },
    extract: { table: id.toUpperCase(), mode: 'incremental', ...extract },
    ...over,
  } as PipelineConfig
}

describe('watermarkRow', () => {
  it('reads column and value from the extract block', () => {
    const r = watermarkRow(pipe('a', { watermark_col: 'LUD', watermark_value: hoursAgo(1) }))
    expect(r).toMatchObject({ incremental: true, column: 'LUD', behind: false })
    expect(r.advancedAt).toBe(r.value)
  })

  it('is Behind past one cadence (the Syncs freshness rule)', () => {
    expect(watermarkRow(pipe('a', { watermark_value: hoursAgo(30) })).behind).toBe(true)
    expect(watermarkRow(pipe('a', { watermark_value: hoursAgo(30) }, { schedule: { frequency: 'weekly' } })).behind).toBe(false)
  })

  it('never flags a disabled sync, a missing value or a value that is not a time', () => {
    expect(watermarkRow(pipe('a', { watermark_value: hoursAgo(99) }, { enabled: false })).behind).toBe(false)
    expect(watermarkRow(pipe('a', { watermark_value: null })).behind).toBe(false)
    const numeric = watermarkRow(pipe('a', { watermark_value: '12345' }))
    expect(numeric).toMatchObject({ value: '12345', behind: false })
  })

  it('a full load has no column, value or flag', () => {
    const r = watermarkRow(pipe('f', { mode: 'full', watermark_col: 'X', watermark_value: hoursAgo(99) }))
    expect(r).toMatchObject({ incremental: false, column: null, value: null, advancedAt: null, behind: false })
  })
})

describe('watermarkRows', () => {
  it('lists incremental syncs first, each group by table', () => {
    const rows = watermarkRows([pipe('z', { mode: 'full' }), pipe('b', {}), pipe('a', { mode: 'full' }), pipe('c', {})])
    expect(rows.map((r) => r.pipelineId)).toEqual(['b', 'c', 'a', 'z'])
  })
})
