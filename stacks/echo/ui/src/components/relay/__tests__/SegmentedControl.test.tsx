import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, within } from '@testing-library/react'
import { SegmentedControl } from '../SegmentedControl'

const options = [
  { value: '24h', label: '24 hours' },
  { value: '7d', label: '7 days' },
  { value: 'all', label: 'All loaded' },
] as const

describe('SegmentedControl', () => {
  it('is a labelled group of buttons with the current one pressed', () => {
    render(<SegmentedControl label="Time range" options={options} value="7d" onChange={() => {}} />)
    const group = screen.getByRole('group', { name: 'Time range' })
    const buttons = within(group).getAllByRole('button')
    expect(buttons.map((b) => b.textContent)).toEqual(['24 hours', '7 days', 'All loaded'])
    expect(buttons.map((b) => b.getAttribute('aria-pressed'))).toEqual(['false', 'true', 'false'])
    expect(buttons.every((b) => b.getAttribute('type') === 'button')).toBe(true)
  })

  it('paints the pressed option in purple and the rest muted', () => {
    render(<SegmentedControl label="Time range" options={options} value="24h" onChange={() => {}} />)
    expect(screen.getByRole('button', { name: '24 hours' })).toHaveClass('bg-pearson-purple', 'text-white', 'shadow-card')
    expect(screen.getByRole('button', { name: '7 days' })).toHaveClass('text-ink-mute', 'hover:text-ink')
    expect(screen.getByRole('button', { name: '7 days' })).not.toHaveClass('bg-pearson-purple')
    expect(screen.getByRole('group')).toHaveClass('rounded-lg', 'border-line', 'bg-paper-card', 'shadow-card')
  })

  it('calls onChange with the picked value', () => {
    const onChange = vi.fn()
    render(<SegmentedControl label="Time range" options={options} value="24h" onChange={onChange} />)
    fireEvent.click(screen.getByRole('button', { name: 'All loaded' }))
    expect(onChange).toHaveBeenCalledWith('all')
  })

  it('size sm matches the 38px toolbar controls with smaller text', () => {
    render(
      <SegmentedControl
        label="View"
        size="sm"
        options={[{ value: 'runs', label: 'Runs' }, { value: 'objects', label: 'Objects' }]}
        value="runs"
        onChange={() => {}}
      />,
    )
    expect(screen.getByRole('group', { name: 'View' })).toHaveClass('h-[38px]')
    expect(screen.getByRole('button', { name: 'Runs' })).toHaveClass('px-2', 'py-0.5', 'text-label')
    expect(screen.getByRole('button', { name: 'Runs' })).not.toHaveClass('text-meta')
  })
})
