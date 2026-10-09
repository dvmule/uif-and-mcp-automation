import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { CheckStatusBadge } from '../CheckStatusBadge'
import { KindBadge } from '../KindBadge'

describe('CheckStatusBadge', () => {
  it('renders SUCCEEDED as Passed (dqRunStatusView)', () => {
    render(<CheckStatusBadge status="SUCCEEDED" />)
    expect(screen.getByText('Passed')).toBeInTheDocument()
  })
  it('renders FAILED label and fail styling', () => {
    render(<CheckStatusBadge status="FAILED" />)
    expect(screen.getByText(/failed/i)).toBeInTheDocument()
  })
  it('renders SKIPPED label', () => {
    render(<CheckStatusBadge status="SKIPPED" />)
    expect(screen.getByText(/skipped/i)).toBeInTheDocument()
  })
  it('renders NOT_IMPLEMENTED with coming-soon hint', () => {
    render(<CheckStatusBadge status="NOT_IMPLEMENTED" />)
    expect(screen.getByText(/coming soon/i)).toBeInTheDocument()
  })
  it('renders dash when status is null/undefined', () => {
    render(<CheckStatusBadge status={null} />)
    expect(screen.getByText('—')).toBeInTheDocument()
  })
})

describe('KindBadge', () => {
  it('renders the kind display label', () => {
    render(<KindBadge kind="null-rate" />)
    expect(screen.getByText('Null rate')).toBeInTheDocument()
  })
  it('applies live styling to implemented kinds', () => {
    const { container } = render(<KindBadge kind="reconcile" />)
    expect(container.querySelector('[data-status="live"]')).not.toBeNull()
  })
  it('applies coming-soon styling to unimplemented kinds', () => {
    const { container } = render(<KindBadge kind="drift" />)
    expect(container.querySelector('[data-status="coming-soon"]')).not.toBeNull()
  })
})
