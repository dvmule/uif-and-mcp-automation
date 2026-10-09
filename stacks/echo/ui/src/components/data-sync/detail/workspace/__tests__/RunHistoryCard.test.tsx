import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { RunHistoryCard } from '@/components/data-sync/detail/workspace/RunHistoryCard'
import type { RunLike } from '@/lib/types'

const runs: RunLike[] = []

describe('RunHistoryCard', () => {
  it('renders the legend with all 4 statuses and the click hint', () => {
    render(<RunHistoryCard runs={runs} onBarClick={vi.fn()} />)
    expect(screen.getByText('Succeeded')).toBeInTheDocument()
    expect(screen.getByText('Failed')).toBeInTheDocument()
    expect(screen.getByText('Skipped')).toBeInTheDocument()
    expect(screen.getByText('Running')).toBeInTheDocument()
    expect(screen.getByText('Click a bar to open that run')).toBeInTheDocument()
  })

  it('switches the active window on toggle click', () => {
    render(<RunHistoryCard runs={runs} onBarClick={vi.fn()} />)
    const toggle = screen.getByRole('button', { name: '7 days' })
    expect(toggle).toHaveAttribute('aria-pressed', 'false')
    fireEvent.click(toggle)
    expect(toggle).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: '30 days' })).toHaveAttribute('aria-pressed', 'false')
  })

  it('names the windows in words, 30 days by default', () => {
    render(<RunHistoryCard runs={runs} onBarClick={vi.fn()} />)
    expect(screen.getAllByRole('button').map((b) => b.textContent)).toEqual(['24 hours', '7 days', '30 days', '90 days'])
    expect(screen.getByRole('button', { name: '30 days' })).toHaveAttribute('aria-pressed', 'true')
  })

  it('reports the chosen window and follows a window set by the page', () => {
    const onWindowChange = vi.fn()
    const { rerender } = render(<RunHistoryCard runs={runs} onBarClick={vi.fn()} window="30d" onWindowChange={onWindowChange} />)
    fireEvent.click(screen.getByRole('button', { name: '24 hours' }))
    expect(onWindowChange).toHaveBeenCalledWith('24h')
    rerender(<RunHistoryCard runs={runs} onBarClick={vi.fn()} window="90d" onWindowChange={onWindowChange} />)
    expect(screen.getByRole('button', { name: '90 days' })).toHaveAttribute('aria-pressed', 'true')
  })

  it('shows the placeholder instead of the chart when given one', () => {
    render(<RunHistoryCard runs={runs} onBarClick={vi.fn()} placeholder={<p>not here</p>} />)
    expect(screen.getByText('not here')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '7 days' })).not.toBeInTheDocument()
  })
})
