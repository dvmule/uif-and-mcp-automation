import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { StatusBadge } from '../StatusBadge'
import { runStatusView } from '@/lib/statusVocabulary'

describe('StatusBadge', () => {
  it('shows the label, the tone classes and the raw value as the tooltip', () => {
    render(<StatusBadge view={runStatusView('SUCCEEDED')} />)
    const badge = screen.getByText('Succeeded')
    expect(badge).toHaveAttribute('title', 'SUCCEEDED')
    expect(badge.className).toContain('text-signal-ok')
    expect(badge.querySelector('svg')).not.toBeNull()
  })

  it('lets the caller override the tooltip', () => {
    render(<StatusBadge view={runStatusView('FAILED')} title="Custom" />)
    expect(screen.getByText('Failed')).toHaveAttribute('title', 'Custom')
  })

  it('uses the pulsing dot instead of an icon for a running status', () => {
    render(<StatusBadge view={runStatusView('RUNNING')} />)
    const badge = screen.getByText('Running')
    expect(badge.querySelector('svg')).toBeNull()
    expect(badge.querySelector('.pulse-ring')).not.toBeNull()
  })

  it('hides the icon when showIcon is false', () => {
    render(<StatusBadge view={runStatusView('WARNING')} showIcon={false} />)
    expect(screen.getByText('Warning').querySelector('svg')).toBeNull()
  })
})
