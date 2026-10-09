import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { Database } from 'lucide-react'
import { SettingsTile } from '../SettingsTile'

function renderTile(props: Parameters<typeof SettingsTile>[0]) {
  return render(
    <MemoryRouter>
      <SettingsTile {...props} />
    </MemoryRouter>
  )
}

describe('SettingsTile', () => {
  it('enabled tile renders a router Link to `to`', () => {
    renderTile({
      icon: Database,
      label: 'Connections',
      description: 'Sources, destinations, credentials',
      to: '/settings/connections',
    })
    const link = screen.getByRole('link', { name: /connections/i })
    expect(link).toHaveAttribute('href', '/settings/connections')
    expect(screen.getByText(/sources, destinations, credentials/i)).toBeInTheDocument()
  })

  it('disabled tile renders no link and shows the "later" badge', () => {
    renderTile({
      icon: Database,
      label: 'Users',
      description: 'Identity & access',
      to: '/settings/users',
      disabled: true,
    })
    expect(screen.queryByRole('link')).toBeNull()
    expect(screen.getByText(/later/i)).toBeInTheDocument()
    expect(screen.getByText('Users')).toBeInTheDocument()
  })

  it('disabled tile has aria-disabled="true"', () => {
    renderTile({
      icon: Database,
      label: 'Audit',
      description: 'Log of platform changes',
      to: '/settings/audit',
      disabled: true,
    })
    expect(screen.getByTestId('settings-tile-audit')).toHaveAttribute('aria-disabled', 'true')
  })

  it('custom badge text overrides the default "later" label', () => {
    renderTile({
      icon: Database,
      label: 'API tokens',
      description: 'Programmatic access',
      to: '/settings/api-tokens',
      disabled: true,
      badge: 'beta',
    })
    expect(screen.getByText('beta')).toBeInTheDocument()
    expect(screen.queryByText(/later/i)).toBeNull()
  })
})
