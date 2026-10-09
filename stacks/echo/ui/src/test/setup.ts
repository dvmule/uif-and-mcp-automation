import { beforeEach } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { __setRuntimeConfigForTests } from '@/lib/envConfig'
import { useAuthStore } from '@/store/authStore'

// Since UIFPE-515 the API origin and environment come from /config.json at
// runtime, and the accessors in envConfig THROW when nothing has been loaded.
// That is deliberate in the browser -- the alternative, an empty base URL,
// surfaces as a CORS error or a request to the UI's own origin, a long way
// from the cause -- but it means any test rendering environment-dependent UI
// would fail on the guard rather than on its own assertion.
//
// So every test starts with a config loaded, exactly as the app does after
// main.tsx awaits it. envConfig.test.ts clears it in its own beforeEach, which
// runs after this one, so the unloaded state is still genuinely exercised
// there -- the same arrangement the auth seed below already relies on.
beforeEach(() => {
  __setRuntimeConfigForTests({ apiUrl: 'https://api.test.invalid', env: 'dev' })
})

// RequireAuth wraps every app route (see router.tsx), so any test that renders
// through the router would redirect to /login instead of the page under test.
// Default every test to a signed-in session — these suites are about routing
// and rendering, not authentication.
//
// The auth suites (RequireAuth.test.tsx, Login.test.tsx, authStore.test.ts)
// clear the store in their own beforeEach, which runs after this one, so the
// guard is still genuinely exercised there.
beforeEach(() => {
  useAuthStore.getState().setSession({
    token: 'test.token',
    username: 'test.user',
    expiresAt: new Date(Date.now() + 3_600_000).toISOString(),
  })
})
