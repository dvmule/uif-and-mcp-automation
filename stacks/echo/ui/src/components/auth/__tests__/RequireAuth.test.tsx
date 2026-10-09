import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { beforeEach, describe, expect, it } from 'vitest'
import { RequireAuth } from '@/components/auth/RequireAuth'
import { useAuthStore } from '@/store/authStore'

const inFuture = () => new Date(Date.now() + 3_600_000).toISOString()
const inPast = () => new Date(Date.now() - 3_600_000).toISOString()

/** Renders the location the guard redirected to, so `from` can be asserted. */
function LoginProbe() {
  const location = useLocation()
  const from = (location.state as { from?: { pathname?: string } } | null)?.from?.pathname
  return <div data-testid="login">login from={from ?? 'none'}</div>
}

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/login" element={<LoginProbe />} />
        <Route
          path="/m/data-sync/syncs"
          element={
            <RequireAuth>
              <div data-testid="protected">syncs</div>
            </RequireAuth>
          }
        />
      </Routes>
    </MemoryRouter>,
  )
}

describe('RequireAuth', () => {
  beforeEach(() => {
    useAuthStore.getState().clear()
    sessionStorage.clear()
  })

  it('redirects to /login when there is no session', () => {
    renderAt('/m/data-sync/syncs')
    expect(screen.getByTestId('login')).toBeInTheDocument()
    expect(screen.queryByTestId('protected')).not.toBeInTheDocument()
  })

  it('renders children when the session is valid', () => {
    useAuthStore.getState().setSession({ token: 't', username: 'u', expiresAt: inFuture() })
    renderAt('/m/data-sync/syncs')
    expect(screen.getByTestId('protected')).toBeInTheDocument()
    expect(screen.queryByTestId('login')).not.toBeInTheDocument()
  })

  it('redirects when the token is present but expired', () => {
    useAuthStore.getState().setSession({ token: 't', username: 'u', expiresAt: inPast() })
    renderAt('/m/data-sync/syncs')
    expect(screen.getByTestId('login')).toBeInTheDocument()
  })

  it('passes the attempted route through as `from`', () => {
    renderAt('/m/data-sync/syncs')
    expect(screen.getByTestId('login')).toHaveTextContent('from=/m/data-sync/syncs')
  })
})
