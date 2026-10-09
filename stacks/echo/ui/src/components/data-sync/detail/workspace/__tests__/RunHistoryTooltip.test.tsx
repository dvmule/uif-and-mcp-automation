import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { RunHistoryTooltip } from '@/components/data-sync/detail/workspace/RunHistoryTooltip'

describe('RunHistoryTooltip', () => {
  it('is empty when not hovered', () => {
    const { container } = render(<RunHistoryTooltip active={false} payload={[]} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('reads when, status · duration, rows read → written, then kind and watermark', () => {
    const payload = [{ payload: { label: 'Sep 29 13:32', status: 'SUCCEEDED', rows: 5120, rowsWritten: 5120, duration: 1560, kindLabel: 'Bronze', watermark: '2026-09-28T13:14:59Z' } }]
    const { container } = render(<RunHistoryTooltip active payload={payload} />)
    expect(screen.getByText('Sep 29 13:32').tagName).toBe('B')
    expect(container).toHaveTextContent('Succeeded · 26m')
    expect(container).toHaveTextContent(`${(5120).toLocaleString()} → ${(5120).toLocaleString()} rows`)
    expect(container).toHaveTextContent('Bronze · 2026-09-28T13:14:59Z')
    expect(container.firstChild).toHaveClass('rounded-[10px]', 'border-line', 'bg-paper-card', 'shadow-card')
  })
})
