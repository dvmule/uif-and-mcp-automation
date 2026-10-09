import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { AuthError, login } from '@/lib/auth'

// Since UIFPE-515 the config is fetched at runtime, so these are accessor
// functions rather than module constants.
vi.mock('@/lib/envConfig', () => ({
  getApiBaseUrl: () => 'https://api.test.invalid',
  getEnv: () => 'dev',
  showSandbox: () => true,
  getEnvLabel: () => 'DEV',
}))

const jsonResponse = (status: number, body: unknown) =>
  ({ ok: status >= 200 && status < 300, status, json: async () => body }) as Response

describe('login', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
  })
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('POSTs the credential as JSON to /auth/login', async () => {
    vi.mocked(fetch).mockResolvedValue(
      jsonResponse(200, { token: 't', username: 'alice', expiresAt: '2026-01-01T00:00:00+00:00' }),
    )

    await login('alice', 's3cret')

    const [url, init] = vi.mocked(fetch).mock.calls[0] as [string, RequestInit]
    expect(url).toBe('https://api.test.invalid/auth/login')
    expect(init.method).toBe('POST')
    expect(JSON.parse(init.body as string)).toEqual({ username: 'alice', password: 's3cret' })
  })

  it('never sends cookies — bearer token only', async () => {
    vi.mocked(fetch).mockResolvedValue(
      jsonResponse(200, { token: 't', username: 'alice', expiresAt: '2026-01-01T00:00:00+00:00' }),
    )
    await login('alice', 's3cret')
    const [, init] = vi.mocked(fetch).mock.calls[0] as [string, RequestInit]
    expect(init.credentials).toBe('omit')
  })

  it('returns the session on success', async () => {
    vi.mocked(fetch).mockResolvedValue(
      jsonResponse(200, {
        token: 'signed.token',
        username: 'alice',
        expiresAt: '2026-01-01T00:00:00+00:00',
      }),
    )
    await expect(login('alice', 's3cret')).resolves.toEqual({
      token: 'signed.token',
      username: 'alice',
      expiresAt: '2026-01-01T00:00:00+00:00',
    })
  })

  it("surfaces the server's 401 message verbatim", async () => {
    vi.mocked(fetch).mockResolvedValue(
      jsonResponse(401, { error: 'Incorrect username or password' }),
    )
    await expect(login('alice', 'wrong')).rejects.toMatchObject({
      name: 'AuthError',
      status: 401,
      message: 'Incorrect username or password',
    })
  })

  it('falls back to a generic message when a 401 carries no body', async () => {
    vi.mocked(fetch).mockResolvedValue({
      ok: false,
      status: 401,
      json: async () => {
        throw new Error('not json')
      },
    } as unknown as Response)
    await expect(login('alice', 'wrong')).rejects.toMatchObject({
      status: 401,
      message: 'Incorrect username or password',
    })
  })

  it('reports a network failure without a status', async () => {
    vi.mocked(fetch).mockRejectedValue(new TypeError('Failed to fetch'))
    const err = await login('alice', 's3cret').catch((e) => e)
    expect(err).toBeInstanceOf(AuthError)
    expect(err.status).toBeUndefined()
    expect(err.message).toMatch(/could not reach the server/i)
  })

  it('rejects a 200 that is missing session fields', async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse(200, { token: 't' }))
    await expect(login('alice', 's3cret')).rejects.toThrow(/incomplete session/i)
  })

  it('surfaces a 500 as a non-credential failure', async () => {
    vi.mocked(fetch).mockResolvedValue(
      jsonResponse(500, { error: 'Login is not configured' }),
    )
    await expect(login('alice', 's3cret')).rejects.toMatchObject({
      status: 500,
      message: 'Login is not configured',
    })
  })
})

describe('login without a configured API base URL', () => {
  it('throws before making a request', async () => {
    vi.resetModules()
    vi.doMock('@/lib/envConfig', () => ({ getApiBaseUrl: () => '' }))
    const fetchSpy = vi.fn()
    vi.stubGlobal('fetch', fetchSpy)

    const { login: unconfigured } = await import('@/lib/auth')
    await expect(unconfigured('alice', 's3cret')).rejects.toThrow(/API base URL is not configured/)
    expect(fetchSpy).not.toHaveBeenCalled()

    vi.unstubAllGlobals()
    vi.doUnmock('@/lib/envConfig')
    vi.resetModules()
  })
})
