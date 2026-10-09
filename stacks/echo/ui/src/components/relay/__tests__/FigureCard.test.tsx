import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { Figure, FigureRow } from '../FigureCard'

describe('Figure', () => {
  it('shows title, sub, value and foot, with the test id and tip on the root', () => {
    render(
      <Figure icon="✓" tone="ok" title="Succeeded" sub="24 hours" value={5} foot="3 replicate · 2 gold" testid="fig" tip="hover" />,
    )
    const fig = screen.getByTestId('fig')
    expect(fig).toHaveTextContent('Succeeded')
    expect(fig).toHaveTextContent('24 hours')
    expect(fig).toHaveTextContent('3 replicate · 2 gold')
    expect(fig).toHaveAttribute('title', 'hover')
    expect(screen.getByText('5')).toHaveClass('text-[32px]', 'text-accent')
  })

  it('puts the icon in a disc coloured by tone, hidden from assistive tech', () => {
    const tones = { ok: 'bg-dot-ok', warn: 'bg-signal-warn-line', fail: 'bg-dot-fail', live: 'bg-signal-live', accent: 'bg-accent' } as const
    for (const [tone, cls] of Object.entries(tones)) {
      const { unmount } = render(<Figure icon="!" tone={tone as keyof typeof tones} title="T" sub="s" value={1} />)
      const disc = screen.getByText('!')
      expect(disc).toHaveClass(cls)
      expect(disc).toHaveAttribute('aria-hidden', 'true')
      unmount()
    }
  })

  it('shows a skeleton while the value is undefined, and no foot when none is given', () => {
    render(<Figure icon="✓" tone="ok" title="Succeeded" sub="24 hours" value={undefined} testid="fig" />)
    const fig = screen.getByTestId('fig')
    expect(fig.querySelector('[data-slot="skeleton"]')).not.toBeNull()
    expect(fig.children).toHaveLength(3)
  })

  it('shows a zero, not a skeleton', () => {
    render(<Figure icon="✓" tone="ok" title="Succeeded" sub="24 hours" value={0} testid="fig" />)
    expect(screen.getByText('0')).toBeInTheDocument()
    expect(screen.getByTestId('fig').querySelector('[data-slot="skeleton"]')).toBeNull()
  })
})

describe('FigureRow', () => {
  it('lays figures two across, then five at xl', () => {
    const { container } = render(<FigureRow>x</FigureRow>)
    expect(container.firstElementChild).toHaveClass('grid', 'sm:grid-cols-2', 'xl:grid-cols-5')
  })
})
