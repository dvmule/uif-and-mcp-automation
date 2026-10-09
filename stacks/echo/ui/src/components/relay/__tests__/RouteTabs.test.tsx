import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { RouteTabs } from '../RouteTabs'

const tabs = [
  { to: '/m/x', label: 'Overview', end: true },
  { to: '/m/x/runs', label: 'Runs' },
]

describe('RouteTabs', () => {
  it('marks only the tab for the current route as current', () => {
    render(
      <MemoryRouter initialEntries={['/m/x/runs']}>
        <RouteTabs tabs={tabs} label="Sections" />
      </MemoryRouter>,
    )
    expect(screen.getByRole('navigation', { name: 'Sections' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Runs' })).toHaveAttribute('aria-current', 'page')
    expect(screen.getByRole('link', { name: 'Overview' })).not.toHaveAttribute('aria-current')
  })

  it('uses end so a parent tab is not current on a child route', () => {
    render(
      <MemoryRouter initialEntries={['/m/x']}>
        <RouteTabs tabs={tabs} />
      </MemoryRouter>,
    )
    expect(screen.getByRole('link', { name: 'Overview' })).toHaveAttribute('aria-current', 'page')
    expect(screen.getByRole('link', { name: 'Runs' })).not.toHaveAttribute('aria-current')
  })
})
