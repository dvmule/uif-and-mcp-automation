import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { Login } from '@/pages/Login'
import { AuthError } from '@/lib/auth'
import { useAuthStore } from '@/store/authStore'

vi.mock('@/lib/auth', async () => {
  const actual = await vi.importActual<typeof import('@/lib/auth')>('@/lib/auth')
  return { ...actual, login: vi.fn() }
})
const { login } = await import('@/lib/auth')

const inFuture = () => new Date(Date.now() + 3_600_000).toISOString()

function renderLogin(entry: { pathname: string; state?: unknown } = { pathname: '/login' }) {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/" element={<div data-testid="home">home</div>} />
        <Route path="/m/data-sync/syncs" element={<div data-testid="syncs">syncs</div>} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('Login', () => {
  beforeEach(() => {
    useAuthStore.getState().clear()
    sessionStorage.clear()
    vi.mocked(login).mockReset()
  })

  it('renders both fields and the submit button', () => {
    renderLogin()
    expect(screen.getByLabelText('Username')).toBeInTheDocument()
    expect(screen.getByLabelText('Password')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Sign in' })).toBeInTheDocument()
  })

  it('sets autocomplete so password managers work', () => {
    renderLogin()
    expect(screen.getByLabelText('Username')).toHaveAttribute('autocomplete', 'username')
    expect(screen.getByLabelText('Password')).toHaveAttribute('autocomplete', 'current-password')
  })

  it('rejects an empty submission without calling the API', async () => {
    renderLogin()
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Enter your username and password.',
    )
    expect(login).not.toHaveBeenCalled()
  })

  it('rejects a blank password even when a username is given', async () => {
    renderLogin()
    await userEvent.type(screen.getByLabelText('Username'), 'vaibhav.yenorkar')
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(await screen.findByRole('alert')).toBeInTheDocument()
    expect(login).not.toHaveBeenCalled()
  })

  it('stores the session and lands on home after a successful sign-in', async () => {
    vi.mocked(login).mockResolvedValue({
      token: 'signed.token',
      username: 'vaibhav.yenorkar',
      expiresAt: inFuture(),
    })

    renderLogin()
    await userEvent.type(screen.getByLabelText('Username'), 'vaibhav.yenorkar')
    await userEvent.type(screen.getByLabelText('Password'), 's3cret')
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))

    await waitFor(() => expect(screen.getByTestId('home')).toBeInTheDocument())
    expect(useAuthStore.getState().isAuthenticated()).toBe(true)
    expect(useAuthStore.getState().username).toBe('vaibhav.yenorkar')
  })

  it('trims the username before sending it', async () => {
    vi.mocked(login).mockResolvedValue({ token: 't', username: 'u', expiresAt: inFuture() })
    renderLogin()
    await userEvent.type(screen.getByLabelText('Username'), '  vaibhav.yenorkar  ')
    await userEvent.type(screen.getByLabelText('Password'), 's3cret')
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    await waitFor(() => expect(login).toHaveBeenCalledWith('vaibhav.yenorkar', 's3cret'))
  })

  it("shows the server's message on a rejected credential and clears the password", async () => {
    vi.mocked(login).mockRejectedValue(new AuthError('Incorrect username or password', 401))

    renderLogin()
    await userEvent.type(screen.getByLabelText('Username'), 'vaibhav.yenorkar')
    await userEvent.type(screen.getByLabelText('Password'), 'wrong')
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Incorrect username or password',
    )
    expect(screen.getByLabelText('Password')).toHaveValue('')
    expect(useAuthStore.getState().isAuthenticated()).toBe(false)
    // Still on the login page.
    expect(screen.queryByTestId('home')).not.toBeInTheDocument()
  })

  it('marks the password field invalid and linked to the error for screen readers', async () => {
    vi.mocked(login).mockRejectedValue(new AuthError('Incorrect username or password', 401))
    renderLogin()
    await userEvent.type(screen.getByLabelText('Username'), 'u')
    await userEvent.type(screen.getByLabelText('Password'), 'p')
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))

    const pw = await screen.findByLabelText('Password')
    expect(pw).toHaveAttribute('aria-invalid', 'true')
    expect(pw).toHaveAttribute('aria-describedby', 'login-error')
  })

  it('reports a network failure', async () => {
    vi.mocked(login).mockRejectedValue(new AuthError('Could not reach the server. Check your connection and try again.'))
    renderLogin()
    await userEvent.type(screen.getByLabelText('Username'), 'u')
    await userEvent.type(screen.getByLabelText('Password'), 'p')
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/could not reach the server/i)
  })

  it('redirects away when already signed in', () => {
    useAuthStore.getState().setSession({ token: 't', username: 'u', expiresAt: inFuture() })
    renderLogin()
    expect(screen.getByTestId('home')).toBeInTheDocument()
    expect(screen.queryByLabelText('Password')).not.toBeInTheDocument()
  })

  it('returns to the route the guard bounced them from', async () => {
    vi.mocked(login).mockResolvedValue({ token: 't', username: 'u', expiresAt: inFuture() })
    renderLogin({ pathname: '/login', state: { from: { pathname: '/m/data-sync/syncs' } } })

    await userEvent.type(screen.getByLabelText('Username'), 'u')
    await userEvent.type(screen.getByLabelText('Password'), 'p')
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))

    await waitFor(() => expect(screen.getByTestId('syncs')).toBeInTheDocument())
  })
})
