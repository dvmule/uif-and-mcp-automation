import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Routes, Route, useLocation } from 'react-router-dom'
import { SettingsButton } from './SettingsButton'

function LocationProbe() {
  const loc = useLocation()
  return <div data-testid="loc">{loc.pathname}</div>
}

describe('SettingsButton', () => {
  it('renders an accessible button labelled Settings', () => {
    render(
      <MemoryRouter initialEntries={['/']}>
        <SettingsButton />
      </MemoryRouter>,
    )
    expect(screen.getByRole('button', { name: /settings/i })).toBeInTheDocument()
  })

  it('navigates to /settings on click', async () => {
    const user = userEvent.setup()
    render(
      <MemoryRouter initialEntries={['/']}>
        <SettingsButton />
        <Routes>
          <Route path="*" element={<LocationProbe />} />
        </Routes>
      </MemoryRouter>,
    )
    await user.click(screen.getByRole('button', { name: /settings/i }))
    expect(screen.getByTestId('loc')).toHaveTextContent('/settings')
  })
})
