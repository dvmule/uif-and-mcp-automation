import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ChartLegend } from '../ChartLegend'

describe('ChartLegend', () => {
  it('shows one swatch per item in its token colour', () => {
    render(
      <ChartLegend
        items={[
          { label: 'Succeeded', tone: 'ok' },
          { label: 'Warning', tone: 'warn' },
          { label: 'Failed', tone: 'fail' },
          { label: 'Rows', tone: 'accent' },
        ]}
      />,
    )
    expect(screen.getByText('Succeeded').querySelector('i')).toHaveClass('bg-dot-ok')
    expect(screen.getByText('Warning').querySelector('i')).toHaveClass('bg-signal-warn-line')
    expect(screen.getByText('Failed').querySelector('i')).toHaveClass('bg-dot-fail')
    expect(screen.getByText('Rows').querySelector('i')).toHaveClass('bg-accent')
  })
})
