import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { SettingsIndex } from '../Index'

function renderIndex() {
  return render(
    <MemoryRouter>
      <SettingsIndex />
    </MemoryRouter>
  )
}

describe('SettingsIndex', () => {
  it('renders the three live section tiles as links', () => {
    renderIndex()
    expect(screen.getByRole('link', { name: /connections/i })).toHaveAttribute('href', '/settings/connections')
    expect(screen.getByRole('link', { name: /notifications/i })).toHaveAttribute('href', '/settings/notifications')
    expect(screen.getByRole('link', { name: /environments/i })).toHaveAttribute('href', '/settings/environments')
    expect(screen.getAllByRole('link')).toHaveLength(3)
  })

  it('no longer shows the dead Users, API tokens and Audit tiles', () => {
    renderIndex()
    expect(screen.queryByText('Users')).toBeNull()
    expect(screen.queryByText('API tokens')).toBeNull()
    expect(screen.queryByText('Audit')).toBeNull()
    expect(screen.queryByText(/later/i)).toBeNull()
  })

  it('shows a page heading', () => {
    renderIndex()
    expect(screen.getByRole('heading', { level: 1, name: /settings/i })).toBeInTheDocument()
  })
})
