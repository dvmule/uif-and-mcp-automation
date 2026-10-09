import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { FigureStrip, StripFigure } from '../FigureStrip'

describe('StripFigure', () => {
  it('shows label, value and hint', () => {
    render(<StripFigure label="Syncs healthy" value={3} hint="none failed" testid="fig" />)
    expect(screen.getByText('Syncs healthy')).toBeInTheDocument()
    expect(screen.getByText('3')).toBeInTheDocument()
    expect(screen.getByText('none failed')).toBeInTheDocument()
    expect(screen.getByTestId('fig')).toBeInTheDocument()
  })

  it('is a link to href when given, and not a link without it', () => {
    const { unmount } = render(
      <MemoryRouter>
        <StripFigure label="Syncs" value={9} href="/m/data-sync/syncs" testid="fig" />
      </MemoryRouter>,
    )
    expect(screen.getByRole('link')).toHaveAttribute('href', '/m/data-sync/syncs')
    expect(screen.getByTestId('fig').closest('a')).toHaveAttribute('href', '/m/data-sync/syncs')
    unmount()
    render(<StripFigure label="Syncs" value={9} />)
    expect(screen.queryByRole('link')).toBeNull()
  })

  it('shows "Not available here" and no value when unavailable', () => {
    render(<StripFigure label="Rows" value={42} unavailable />)
    expect(screen.getByText('Not available here')).toBeInTheDocument()
    expect(screen.queryByText('42')).toBeNull()
  })

  it('colours the value by valueTone', () => {
    const { rerender } = render(<StripFigure label="Failed" value={1} valueTone="fail" />)
    expect(screen.getByText('1')).toHaveClass('text-signal-fail')
    rerender(<StripFigure label="Failed" value={1} />)
    expect(screen.getByText('1')).toHaveClass('text-accent')
    expect(screen.getByText('1')).not.toHaveClass('text-signal-fail')
  })
})

describe('FigureStrip', () => {
  it('lays the figures on a hairline grid, four across at lg', () => {
    const { container } = render(<FigureStrip>x</FigureStrip>)
    expect(container.firstElementChild).toHaveClass('gap-px', 'bg-line', 'lg:grid-cols-4')
  })
})
