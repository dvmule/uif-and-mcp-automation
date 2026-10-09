import { beforeEach, describe, expect, it } from 'vitest'
import { useAuthStore } from '@/store/authStore'

const inFuture = (mins = 60) => new Date(Date.now() + mins * 60_000).toISOString()
const inPast = (mins = 60) => new Date(Date.now() - mins * 60_000).toISOString()

const session = (expiresAt: string) => ({
  token: 'signed.token',
  username: 'vaibhav.yenorkar',
  expiresAt,
})

describe('authStore', () => {
  beforeEach(() => {
    useAuthStore.getState().clear()
    sessionStorage.clear()
  })

  it('starts unauthenticated', () => {
    expect(useAuthStore.getState().isAuthenticated()).toBe(false)
    expect(useAuthStore.getState().token).toBeNull()
  })

  it('setSession stores the session and authenticates', () => {
    useAuthStore.getState().setSession(session(inFuture()))
    const s = useAuthStore.getState()
    expect(s.isAuthenticated()).toBe(true)
    expect(s.username).toBe('vaibhav.yenorkar')
    expect(s.token).toBe('signed.token')
  })

  it('clear removes every field', () => {
    useAuthStore.getState().setSession(session(inFuture()))
    useAuthStore.getState().clear()
    const s = useAuthStore.getState()
    expect(s.isAuthenticated()).toBe(false)
    expect(s.token).toBeNull()
    expect(s.username).toBeNull()
    expect(s.expiresAt).toBeNull()
  })

  it('an expired session is not authenticated even though the token is present', () => {
    useAuthStore.getState().setSession(session(inPast()))
    const s = useAuthStore.getState()
    expect(s.token).toBe('signed.token')
    expect(s.isAuthenticated()).toBe(false)
  })

  it('fails closed on an unparseable expiry', () => {
    useAuthStore.setState({ token: 't', username: 'u', expiresAt: 'not-a-date' })
    expect(useAuthStore.getState().isAuthenticated()).toBe(false)
  })

  it('fails closed when a token exists but expiresAt does not', () => {
    useAuthStore.setState({ token: 't', username: 'u', expiresAt: null })
    expect(useAuthStore.getState().isAuthenticated()).toBe(false)
  })

  it('persists to sessionStorage, not localStorage', () => {
    useAuthStore.getState().setSession(session(inFuture()))
    const raw = sessionStorage.getItem('echo-auth')
    expect(raw).toBeTruthy()
    expect(localStorage.getItem('echo-auth')).toBeNull()
    expect(JSON.parse(raw as string).state.token).toBe('signed.token')
  })

  it('persists data only — never the actions', () => {
    useAuthStore.getState().setSession(session(inFuture()))
    const persisted = JSON.parse(sessionStorage.getItem('echo-auth') as string).state
    expect(Object.keys(persisted).sort()).toEqual(['expiresAt', 'token', 'username'])
  })

  it('clear also empties the persisted copy', () => {
    useAuthStore.getState().setSession(session(inFuture()))
    useAuthStore.getState().clear()
    const persisted = JSON.parse(sessionStorage.getItem('echo-auth') as string).state
    expect(persisted.token).toBeNull()
  })
})
