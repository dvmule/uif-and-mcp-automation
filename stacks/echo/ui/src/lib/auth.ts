/**
 * Authentication protocol — the SSO seam.
 *
 * This is the ONLY file that knows *how* a token is obtained. Everything
 * downstream (authStore, the Bearer header in api.ts, RequireAuth, UserMenu)
 * works with an opaque token and does not care where it came from.
 *
 * When Pearson SSO lands, `login()` is replaced by a redirect to the IdP and
 * `completeRedirect()` reads the token back out of the callback. Nothing else
 * in the UI changes.
 *
 * Interim scheme: POST /auth/login checks the credential server-side against
 * Secrets Manager and returns an HMAC-signed token. The credential is never
 * shipped in this bundle — a shared password harvested from JS gets tried
 * against other systems, which is the risk that matters, not app access.
 */
import { getApiBaseUrl } from '@/lib/envConfig'

export interface Session {
  token: string
  username: string
  /** ISO 8601, from the server — never computed client-side. */
  expiresAt: string
}

/** A failed sign-in. `status` is absent when the request never reached the API. */
export class AuthError extends Error {
  readonly status?: number

  constructor(message: string, status?: number) {
    super(message)
    this.name = 'AuthError'
    this.status = status
  }
}

const GENERIC = 'Incorrect username or password'

/**
 * Exchange a username and password for a session.
 *
 * Deliberately uses `fetch` rather than the axios client from api.ts: that
 * client attaches the Bearer header and redirects to /login on 401, which
 * would be circular here — a rejected sign-in would bounce the user to the
 * page they are already on and swallow the error.
 */
export async function login(username: string, password: string): Promise<Session> {
  // getApiBaseUrl() throws if the runtime config was never loaded, which is
  // the honest failure for a UI served without config.json.
  //
  // The empty-string check below cannot be reached from a real config.json --
  // parseConfig refuses a blank apiUrl at load time -- so it is defence in
  // depth for a seeded config (the test seam) and for any future relaxation of
  // the loader. Keeping it costs one comparison and means login never fires a
  // request it cannot address.
  const baseUrl = getApiBaseUrl()
  if (!baseUrl) {
    throw new AuthError(
      'API base URL is not configured for this environment. config.json is ' +
        'served but its apiUrl is empty.',
    )
  }

  let res: Response
  try {
    res = await fetch(`${baseUrl}/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      // Bearer token, not cookies — never send credentials cross-origin.
      credentials: 'omit',
      body: JSON.stringify({ username, password }),
    })
  } catch {
    throw new AuthError('Could not reach the server. Check your connection and try again.')
  }

  let body: unknown = null
  try {
    body = await res.json()
  } catch {
    // Non-JSON body (gateway error page, empty 502). Fall through to status handling.
  }

  const payload = (body ?? {}) as Partial<Session> & { error?: string }

  if (!res.ok) {
    // 401 keeps the server's wording, which is deliberately identical for
    // unknown user, unprovisioned account and wrong password — do not try to
    // be more specific here, that would reintroduce username enumeration.
    throw new AuthError(payload.error || (res.status === 401 ? GENERIC : 'Sign in failed'), res.status)
  }

  if (!payload.token || !payload.username || !payload.expiresAt) {
    throw new AuthError('Sign in failed: the server returned an incomplete session.', res.status)
  }

  return { token: payload.token, username: payload.username, expiresAt: payload.expiresAt }
}

/**
 * End the session.
 *
 * The interim token is stateless, so there is nothing to revoke server-side —
 * clearing local state is the whole operation (see authStore.clear). This
 * stays here as the seam: with SSO it becomes a redirect to the IdP's
 * end-session endpoint, and callers do not change.
 */
export async function logout(): Promise<void> {
  /* no-op under the interim scheme */
}
