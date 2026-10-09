import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { Home, Plus, Send } from 'lucide-react'
import { LabelledRail } from './LabelledRail'
import type { RailConfig } from './railConfig'

const config: RailConfig = {
  heading: 'Test',
  items: [
    { to: '/new', label: 'Make one', icon: Plus, quick: true },
    { to: '/', label: 'Home', icon: Home, end: true },
    { divider: true },
    { to: '/m/beacon/index.html#/m/beacon', label: 'Elsewhere', icon: Send, external: true },
  ],
}

function renderRail(collapsed: boolean, onToggle = vi.fn(), path = '/') {
  render(
    <MemoryRouter initialEntries={[path]}>
      <LabelledRail config={config} collapsed={collapsed} onToggle={onToggle} />
    </MemoryRouter>,
  )
  return onToggle
}

describe('LabelledRail', () => {
  it('expanded: shows the heading, labels and no tooltips', () => {
    renderRail(false)
    const nav = screen.getByRole('navigation', { name: 'Test menu' })
    expect(nav.className).toContain('w-[212px]')
    expect(screen.getByText('Test')).not.toHaveClass('sr-only')
    expect(screen.getByRole('link', { name: 'Home' })).toHaveTextContent('Home')
    expect(screen.getByRole('link', { name: 'Home' })).not.toHaveAttribute('aria-label')
    expect(nav.querySelectorAll('[aria-hidden="true"].pointer-events-none')).toHaveLength(0)
    expect(screen.getByRole('button', { name: 'Collapse menu' })).toHaveTextContent('Collapse')
  })

  it('collapsed: icon-only links keep a name and get a tooltip', () => {
    renderRail(true)
    const nav = screen.getByRole('navigation', { name: 'Test menu' })
    expect(nav.className).toContain('w-[64px]')
    expect(screen.getByText('Test')).toHaveClass('sr-only')
    const home = screen.getByRole('link', { name: 'Home' })
    expect(home).toHaveAttribute('aria-label', 'Home')
    expect(home.className).toContain('group')
    // the tooltip is decorative text inside the link, shown on hover and focus
    const tip = home.querySelector('span[aria-hidden="true"]')
    expect(tip).toHaveTextContent('Home')
    expect(tip?.className).toContain('group-hover:opacity-100')
    expect(tip?.className).toContain('group-focus-visible:opacity-100')
    expect(screen.getByRole('button', { name: 'Expand menu' })).toHaveAttribute('aria-expanded', 'false')
  })

  it('renders an external item as a plain anchor with a trailing icon', () => {
    renderRail(false)
    const link = screen.getByRole('link', { name: 'Elsewhere' })
    expect(link.tagName).toBe('A')
    expect(link).toHaveAttribute('href', '/m/beacon/index.html#/m/beacon')
    expect(link.querySelector('svg.lucide-external-link')).not.toBeNull()
    expect(link).not.toHaveAttribute('aria-current')
  })

  it('collapsed external item has no trailing icon but a label', () => {
    renderRail(true)
    const link = screen.getByRole('link', { name: 'Elsewhere' })
    expect(link.querySelector('svg.lucide-external-link')).toBeNull()
  })

  it('renders the quick item as a pill that is never marked current', () => {
    renderRail(false, vi.fn(), '/new')
    const quick = screen.getByRole('link', { name: 'Make one' })
    expect(quick.className).toContain('rounded-full')
    expect(quick.className).toContain('bg-pearson-purple')
    expect(quick).not.toHaveAttribute('aria-current')
  })

  it('collapsed quick item is icon-only with a name', () => {
    renderRail(true)
    const quick = screen.getByRole('link', { name: 'Make one' })
    expect(quick).toHaveAttribute('aria-label', 'Make one')
    expect(quick.className).toContain('mx-2.5')
  })

  it('renders a divider', () => {
    renderRail(false)
    expect(screen.getByRole('separator')).toBeInTheDocument()
  })

  it('marks Home current with end matching, styled as active', () => {
    renderRail(false, vi.fn(), '/')
    const home = screen.getByRole('link', { name: 'Home' })
    expect(home).toHaveAttribute('aria-current', 'page')
    expect(home.className).toContain('border-accent')
  })

  it('does not mark Home current on another page', () => {
    renderRail(false, vi.fn(), '/elsewhere')
    expect(screen.getByRole('link', { name: 'Home' })).not.toHaveAttribute('aria-current')
  })

  it('shows the environment under the collapse control when expanded only', () => {
    renderRail(false)
    expect(screen.getByTestId('rail-version')).toHaveTextContent(/^dev/)
  })

  it('hides the version line when collapsed', () => {
    renderRail(true)
    expect(screen.queryByTestId('rail-version')).toBeNull()
  })

  it('calls onToggle when the collapse button is pressed', async () => {
    const user = userEvent.setup()
    const onToggle = renderRail(false)
    await user.click(screen.getByRole('button', { name: 'Collapse menu' }))
    expect(onToggle).toHaveBeenCalledTimes(1)
  })
})
