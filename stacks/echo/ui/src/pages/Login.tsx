import { useEffect, useState } from 'react'
import { Navigate, useLocation, useNavigate } from 'react-router-dom'
import { AlertCircle, Loader2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { login } from '@/lib/auth'
import { sameOriginReturnPath } from '@/lib/returnPath'
import { AppHeader } from '@/components/layout/AppHeader'
import { useAuthStore } from '@/store/authStore'

/**
 * Interim sign-in page. Replaced by an SSO redirect — at which point only
 * lib/auth.ts and this file go; the store, guard and API interceptor stay.
 *
 * Plain useState rather than react-hook-form: two fields and one rule (both
 * required). The resolver, schema and register plumbing would not earn their
 * keep here, and the codebase only uses useForm on one page.
 */
export function Login() {
  const navigate = useNavigate()
  const location = useLocation()
  const setSession = useAuthStore((s) => s.setSession)
  const authenticated = useAuthStore((s) => s.isAuthenticated())

  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  // Where RequireAuth was sending them before it bounced them here.
  const from = (location.state as { from?: { pathname?: string } } | null)?.from?.pathname ?? '/'
  // UIFPE-487: a gated sibling app (the Beacon panel) sends ?return=<path> —
  // outside the SPA, so finishing there is a full page load, not a navigate.
  const externalReturn = sameOriginReturnPath(location.search)

  // Already signed in (bookmarked /login, a second tab, or a sibling app
  // bouncing back) — don't show the form.
  useEffect(() => {
    if (authenticated && externalReturn) window.location.replace(externalReturn)
  }, [authenticated, externalReturn])
  if (authenticated) return externalReturn ? null : <Navigate to={from} replace />

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (submitting) return

    if (!username.trim() || !password) {
      setError('Enter your username and password.')
      return
    }

    setError(null)
    setSubmitting(true)
    try {
      setSession(await login(username.trim(), password))
      if (externalReturn) {
        window.location.replace(externalReturn)
        return
      }
      navigate(from, { replace: true })
    } catch (err) {
      // The server returns one identical message for unknown user,
      // unprovisioned account and wrong password. Show it as-is.
      setError(err instanceof Error ? err.message : 'Sign in failed')
      setPassword('')
      setSubmitting(false)
    }
  }

  return (
    <div className="flex flex-col h-screen overflow-hidden bg-paper">
      <AppHeader minimal />

      <div className="flex flex-1 flex-col items-center justify-center gap-[18px] p-10 overflow-auto">
        <form
          onSubmit={onSubmit}
          noValidate
          className="w-[400px] rounded-xl bg-paper-card p-7"
          aria-labelledby="login-heading"
        >
          <h1 id="login-heading" className="text-[16px] font-medium text-ink">
            Sign in
          </h1>
          <p className="mt-1 mb-[22px] text-[13px] leading-normal text-ink-mute">
            Use the credentials shared by Global Integration Platform team.
          </p>

          <Label htmlFor="username" className="mb-1.5 text-[13px] font-medium text-ink">
            Username
          </Label>
          <Input
            id="username"
            name="username"
            type="text"
            autoComplete="username"
            autoFocus
            required
            disabled={submitting}
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            className="mb-4 h-9"
          />

          <Label htmlFor="password" className="mb-1.5 text-[13px] font-medium text-ink">
            Password
          </Label>
          <Input
            id="password"
            name="password"
            type="password"
            autoComplete="current-password"
            required
            disabled={submitting}
            aria-invalid={error ? true : undefined}
            aria-describedby={error ? 'login-error' : undefined}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className={error ? 'mb-2 h-9' : 'mb-[22px] h-9'}
          />

          {error && (
            <p
              id="login-error"
              role="alert"
              className="mb-5 flex items-center gap-1.5 text-[13px] text-destructive"
            >
              <AlertCircle size={15} strokeWidth={2} className="shrink-0" aria-hidden="true" />
              {error}
            </p>
          )}

          <Button
            type="submit"
            disabled={submitting}
            className="w-full h-[38px] bg-pearson-purple text-white hover:bg-accent disabled:opacity-70"
          >
            {submitting ? (
              <>
                <Loader2 size={15} strokeWidth={2.5} className="animate-spin" aria-hidden="true" />
                Signing in…
              </>
            ) : (
              'Sign in'
            )}
          </Button>
        </form>

        <p className="w-[400px] text-center text-[12px] leading-normal text-ink-mute">
          Temporary access control. Single sign-on replaces this.
        </p>
      </div>
    </div>
  )
}
