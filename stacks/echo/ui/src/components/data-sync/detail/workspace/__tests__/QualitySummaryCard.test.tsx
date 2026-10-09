import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QualitySummaryCard } from '@/components/data-sync/detail/workspace/QualitySummaryCard'

describe('QualitySummaryCard', () => {
  it('renders one row per rule with its last-run score', () => {
    render(<QualitySummaryCard rules={[
      { name: 'PRICE_LIST_ID', context: 'not null', passed: 1, total: 1 },
      { name: 'Minimum rows', context: '≥ 1,000 expected', passed: 0, total: 1 },
    ]} />)
    expect(screen.getByText('PRICE_LIST_ID')).toBeInTheDocument()
    expect(screen.getByText('1 / 1')).toBeInTheDocument()
    expect(screen.getByText('0 / 1')).toBeInTheDocument()
    expect(screen.getByText(/≥ 1,000 expected/)).toBeInTheDocument()
  })

  it('says a rule was not evaluated when no run carried a result for it', () => {
    render(<QualitySummaryCard rules={[{ name: 'Minimum rows', context: '≥ 5 expected', passed: 0, total: 0 }]} />)
    expect(screen.getByText('not evaluated yet')).toBeInTheDocument()
    expect(screen.queryByText('0 / 0')).not.toBeInTheDocument()
  })

  it('says so when the sync has no quality rules', () => {
    render(<QualitySummaryCard rules={[]} />)
    expect(screen.getByText('No quality rules configured on this sync.')).toBeInTheDocument()
  })
})
