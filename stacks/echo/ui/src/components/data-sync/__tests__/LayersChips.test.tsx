import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { LayerChip, LayersChips } from '../LayersChips'
import type { PipelineConfig } from '@/lib/types'

function pipelineFixture(overrides: Partial<PipelineConfig> = {}): PipelineConfig {
  return {
    pipeline_id: 'demo',
    version: 'latest',
    enabled: true,
    source: { secret_arn: 'arn:secret' },
    extract: { table: 'INV.DEMO', mode: 'full' },
    target: { s3_path: 's3://x/' },
    glue: { worker_type: 'G.1X', num_workers: 2, timeout_minutes: 60 },
    schedule: { frequency: 'daily' },
    ...overrides,
  }
}

describe('LayersChips', () => {
  // UIFPE-511: a sync row never claims a Gold layer. A gold table joins many
  // silver tables, so gold is its own row on the Syncs page, not a chip here.
  it('shows Bronze solid + Silver solid, and no Gold chip at all', () => {
    const p = pipelineFixture({
      silver: { enabled: true, primary_key_cols: ['id'] },
    })
    render(<LayersChips pipeline={p} />)

    expect(screen.getByText('Bronze').className).toContain('border-solid')
    expect(screen.getByText('Silver').className).toContain('border-solid')
    expect(screen.queryByText('Gold')).not.toBeInTheDocument()
  })

  it('renders Silver dashed when silver.enabled === false', () => {
    const p = pipelineFixture({
      silver: { enabled: false, primary_key_cols: [] },
    })
    render(<LayersChips pipeline={p} />)
    expect(screen.getByText('Silver').className).toContain('border-dashed')
  })

  it('renders Silver dashed when silver block absent', () => {
    const p = pipelineFixture()
    render(<LayersChips pipeline={p} />)
    expect(screen.getByText('Silver').className).toContain('border-dashed')
  })
})

describe('LayerChip', () => {
  // The gold row's single chip reuses the same pill, so it cannot drift in style.
  it('renders a solid Gold pill for a gold row', () => {
    render(<LayerChip label="Gold" enabled />)
    expect(screen.getByText('Gold').className).toContain('border-solid')
  })
})
