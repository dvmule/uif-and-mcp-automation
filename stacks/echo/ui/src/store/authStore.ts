import { create } from 'zustand'
import { createJSONStorage, persist } from 'zustand/middleware'
import type { Session } from '@/lib/auth'

/**
 * Session state for the interim login (see lib/auth.ts for the protocol).
 *
 * Persisted to sessionStorage, not localStorage: the session dies with the tab,
 * which is the right default for a temporary shared-credential gate. Survives
 * a page reload, so an F5 mid-task does not sign you out.
 *
 * This store is issuer-agnostic — it holds an opaque token. It carries over to
 * Pearson SSO unchanged.
 */
export interface AuthState {
  token: string | null
  username: string | null
  /** ISO 8601 from the server. */
  expiresAt: string | null

  setSession: (session: Session) => void
  clear: () => void
  /** Token present AND not expired. Read this, never `token` alone. */
  isAuthenticated: () => boolean
}

/** jsdom and browsers both provide sessionStorage; this keeps a bare Node import safe. */
const storage = createJSONStorage<Pick<AuthState, 'token' | 'username' | 'expiresAt'>>(() =>
  typeof sessionStorage !== 'undefined'
    ? sessionStorage
    : { getItem: () => null, setItem: () => {}, removeItem: () => {} },
)

export const useAuthStore = create<AuthState>()(
  persist(
    (set, get) => ({
      token: null,
      username: null,
      expiresAt: null,

      setSession: ({ token, username, expiresAt }) => set({ token, username, expiresAt }),

      clear: () => set({ token: null, username: null, expiresAt: null }),

      isAuthenticated: () => {
        const { token, expiresAt } = get()
        if (!token || !expiresAt) return false
        const expiry = Date.parse(expiresAt)
        // An unparseable expiry is a corrupt or hand-edited session: fail closed.
        if (Number.isNaN(expiry)) return false
        return expiry > Date.now()
      },
    }),
    {
      name: 'echo-auth',
      storage,
      // Persist data only — never the actions.
      partialize: ({ token, username, expiresAt }) => ({ token, username, expiresAt }),
    },
  ),
)
