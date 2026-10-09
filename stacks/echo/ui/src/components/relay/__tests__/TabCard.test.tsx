import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { TabCard } from '../TabCard'

const TABS = [
  { value: 'details', label: 'Details', content: <p>the details</p> },
  { value: 'runs', label: 'Runs', count: 17, content: <p>the runs</p> },
]

describe('TabCard', () => {
  it('renders a named tablist with the first tab selected', () => {
    render(<TabCard tabs={TABS} label="Sync sections" />)
    expect(screen.getByRole('tablist', { name: 'Sync sections' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'Details' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tab', { name: /Runs/ })).toHaveAttribute('aria-selected', 'false')
    expect(screen.getByText('the details')).toBeInTheDocument()
    expect(screen.queryByText('the runs')).toBeNull()
  })

  it('shows the count bubble on a tab that has one', () => {
    render(<TabCard tabs={TABS} label="x" />)
    expect(screen.getByRole('tab', { name: /Runs/ })).toHaveTextContent('Runs17')
    expect(screen.getByText('17')).toHaveClass('rounded-full', 'bg-accent-soft', 'text-accent')
  })

  it('switches tabs on click and from the keyboard', async () => {
    const user = userEvent.setup()
    render(<TabCard tabs={TABS} label="x" />)
    await user.click(screen.getByRole('tab', { name: /Runs/ }))
    expect(screen.getByRole('tab', { name: /Runs/ })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByText('the runs')).toBeInTheDocument()
    expect(screen.queryByText('the details')).toBeNull()
    await user.keyboard('{ArrowLeft}')
    expect(screen.getByRole('tab', { name: 'Details' })).toHaveAttribute('aria-selected', 'true')
  })

  it('honours defaultValue', () => {
    render(<TabCard tabs={TABS} label="x" defaultValue="runs" />)
    expect(screen.getByText('the runs')).toBeInTheDocument()
  })
})
