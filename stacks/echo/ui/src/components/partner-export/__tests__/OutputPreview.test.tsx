import { describe, it, expect } from 'vitest'
import { screen } from '@testing-library/react'
import { renderWithProviders } from '@/test/test-utils'
import { OutputPreview } from '../OutputPreview'
import type { MappingField } from '@/lib/types'

const mapping: MappingField[] = [
  { source_col: 'ID', output_field: 'id', type: 'int' },
  { source_col: 'NAME', output_field: 'name', type: 'string' },
]
const sample = [
  { ID: 1, NAME: 'Alpha' },
  { ID: 2, NAME: 'Beta, Inc.' },
]

describe('OutputPreview', () => {
  it('renders CSV with header + quoted comma values', () => {
    renderWithProviders(<OutputPreview format={{ kind: 'csv', csv: { delimiter: ',', quote: '"', header_row: true } }} mapping={mapping} sampleRows={sample} />)
    const pre = screen.getByTestId('output-preview-pre')
    expect(pre.textContent).toContain('id,name')
    expect(pre.textContent).toContain('1,Alpha')
    expect(pre.textContent).toContain('"Beta, Inc."')
  })

  it('renders JSON NDJSON one object per line', () => {
    renderWithProviders(<OutputPreview format={{ kind: 'json', json: { lines: true } }} mapping={mapping} sampleRows={sample} />)
    const text = screen.getByTestId('output-preview-pre').textContent ?? ''
    expect(text.split('\n').filter(Boolean)).toHaveLength(2)
    expect(text).toContain('"id":1')
    expect(text).toContain('"name":"Beta, Inc."')
  })

  it('renders Parquet as a schema dump', () => {
    renderWithProviders(<OutputPreview format={{ kind: 'parquet' }} mapping={mapping} sampleRows={sample} />)
    expect(screen.getByText(/parquet schema/i)).toBeInTheDocument()
    expect(screen.getByText(/id: int/)).toBeInTheDocument()
    expect(screen.getByText(/name: string/)).toBeInTheDocument()
  })

  it('renders an empty state when no mapping rows', () => {
    renderWithProviders(<OutputPreview format={{ kind: 'csv' }} mapping={[]} sampleRows={sample} />)
    expect(screen.getByText(/add at least one mapping/i)).toBeInTheDocument()
  })
})
