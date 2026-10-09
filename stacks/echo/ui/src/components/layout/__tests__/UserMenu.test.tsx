import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it } from 'vitest'
import { UserMenu } from '@/components/layout/UserMenu'
import { useAuthStore } from '@/store/authStore'

const inFuture = () => new Date(Date.now() + 3_600_000).toISOString()

function renderMenu() {
  return render(
    <MemoryRouter initialEntries={['/']}>
      <Routes>
        <Route path="/" element={<UserMenu />} />
        <Route path="/login" element={<div data-testid="login">login</div>} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('UserMenu', () => {
  beforeEach(() => {
    useAuthStore.getState().setSession({
      token: 't',
      username: 'vaibhav.yenorkar',
      expiresAt: inFuture(),
    })
  })

  it('shows the signed-in username', () => {
    renderMenu()
    expect(screen.getByRole('button', { name: /vaibhav\.yenorkar/ })).toBeInTheDocument()
  })

  it('renders nothing when there is no session', () => {
    useAuthStore.getState().clear()
    const { container } = renderMenu()
    expect(container).toBeEmptyDOMElement()
  })

  it('signing out clears the session and navigates to /login', async () => {
    renderMenu()
    await userEvent.click(screen.getByRole('button', { name: /vaibhav\.yenorkar/ }))
    await userEvent.click(await screen.findByText('Sign out'))

    expect(await screen.findByTestId('login')).toBeInTheDocument()
    expect(useAuthStore.getState().isAuthenticated()).toBe(false)
    expect(useAuthStore.getState().token).toBeNull()
  })
})
