import type { ReactNode } from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import { useAuthStore } from '@/store/authStore'

/**
 * Route guard for the interim login.
 *
 * Wraps the single root route in router.tsx, so every child — including the
 * legacy redirects — is covered in one place.
 *
 * This gates the APP, not the data: the guard runs in the browser and the API
 * is protected separately. It stops casual access and gives the SSO redirect a
 * home; it is not a security boundary on its own.
 */
export function RequireAuth({ children }: { children: ReactNode }) {
  const location = useLocation()
  // Calling the store method inside the selector keeps this reactive: the
  // guard re-renders when the session is set or cleared, and the expiry check
  // stays in one place (authStore.isAuthenticated).
  const authenticated = useAuthStore((s) => s.isAuthenticated())

  if (!authenticated) {
    // `from` lets Login send the user back to where they were headed.
    return <Navigate to="/login" replace state={{ from: location }} />
  }

  return <>{children}</>
}
