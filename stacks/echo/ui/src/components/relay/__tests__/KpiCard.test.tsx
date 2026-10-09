import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { KpiCard, KpiRow, SuccessRing } from '../KpiCard'

describe('KpiCard', () => {
  it('renders the label, value and hint on the purple edge by default', () => {
    render(<KpiCard label="Runs · 30 days" value={17} hint="88% of finished runs succeeded" testid="kpi-runs" />)
    const card = screen.getByTestId('kpi-runs')
    expect(card).toHaveTextContent('Runs · 30 days')
    expect(card).toHaveTextContent('17')
    expect(card).toHaveTextContent('88% of finished runs succeeded')
    expect(card).toHaveClass('border-t-[3px]', 'border-t-pearson-purple', 'rounded-card', 'shadow-card')
  })

  it.each([
    ['turq', 'border-t-turq'],
    ['fuchsia', 'border-t-fuchsia-brand'],
    ['ok', 'border-t-signal-ok'],
  ] as const)('the %s edge uses %s', (edge, cls) => {
    render(<KpiCard label="x" value="1" edge={edge} testid="k" />)
    expect(screen.getByTestId('k')).toHaveClass(cls)
  })

  it('places the ring when given one', () => {
    render(<KpiCard label="Runs" value={3} ring={<SuccessRing ok={2} bad={1} />} testid="k" />)
    const ring = screen.getByTestId('success-ring')
    expect(ring).toHaveAttribute('aria-hidden', 'true')
    expect(ring.querySelectorAll('circle')).toHaveLength(2)
  })

  it('shows an em dash and the caller hint when unavailable, and drops the ring', () => {
    render(
      <KpiCard
        label="Rows written"
        value="1,320"
        hint="not available in this environment yet"
        ring={<SuccessRing ok={1} bad={0} />}
        unavailable
        testid="k"
      />,
    )
    const card = screen.getByTestId('k')
    expect(card).toHaveTextContent('—')
    expect(card).not.toHaveTextContent('1,320')
    expect(card).toHaveTextContent('not available in this environment yet')
    expect(screen.queryByTestId('success-ring')).toBeNull()
  })

  it('accepts a node as the value', () => {
    render(<KpiCard label="Last verified" value={<b>Passed</b>} testid="k" />)
    expect(screen.getByText('Passed').tagName).toBe('B')
  })
})

describe('SuccessRing', () => {
  it('draws the ok share over the fail circle', () => {
    const { container } = render(<SuccessRing ok={3} bad={1} size={40} />)
    const [fail, ok] = Array.from(container.querySelectorAll('circle'))
    expect(fail).toHaveAttribute('stroke', 'var(--color-dot-fail)')
    expect(ok).toHaveAttribute('stroke', 'var(--color-dot-ok)')
    const c = 2 * Math.PI * 16
    expect(ok).toHaveAttribute('stroke-dasharray', `${0.75 * c} ${c}`)
  })
})

describe('KpiRow', () => {
  it('lays the cards out in a responsive grid', () => {
    const { container } = render(<KpiRow><span>a</span></KpiRow>)
    expect(container.firstChild).toHaveClass('grid', 'sm:grid-cols-2', 'xl:grid-cols-4')
  })
})
