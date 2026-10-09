import axios from 'axios'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useAuthStore } from '@/store/authStore'

vi.mock('@/store/envStore', () => ({
  useEnvStore: { getState: () => ({ baseUrl: 'https://api.test.invalid', env: 'dev' }) },
}))

const inFuture = () => new Date(Date.now() + 3_600_000).toISOString()

/** The last instance axios.create() handed back, so its config/interceptors can be read. */
function captureAxios() {
  const real = axios.create
  const instances: ReturnType<typeof axios.create>[] = []
  vi.spyOn(axios, 'create').mockImplementation((cfg) => {
    const inst = real(cfg)
    instances.push(inst)
    return inst
  })
  return instances
}

describe('api client auth wiring', () => {
  beforeEach(() => {
    useAuthStore.getState().clear()
  })
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('attaches the bearer token when a session exists', async () => {
    useAuthStore.getState().setSession({ token: 'signed.token', username: 'u', expiresAt: inFuture() })
    const instances = captureAxios()
    const { api } = await import('@/lib/api')

    await api.getPipelines().catch(() => {})

    expect(instances).toHaveLength(1)
    expect(instances[0].defaults.headers.Authorization).toBe('Bearer signed.token')
  })

  it('sends no Authorization header when there is no session', async () => {
    const instances = captureAxios()
    const { api } = await import('@/lib/api')

    await api.getPipelines().catch(() => {})

    expect(instances[0].defaults.headers.Authorization).toBeUndefined()
  })

  it('a 401 clears the session — RequireAuth then redirects', async () => {
    useAuthStore.getState().setSession({ token: 't', username: 'u', expiresAt: inFuture() })
    const instances = captureAxios()
    const { api } = await import('@/lib/api')

    // Reject through the instance's adapter so the response interceptor runs.
    instances.length = 0
    const real = axios.create
    vi.spyOn(axios, 'create').mockImplementation((cfg) => {
      const inst = real({
        ...cfg,
        adapter: async () => {
          throw Object.assign(new Error('Unauthorized'), { response: { status: 401 } })
        },
      })
      instances.push(inst)
      return inst
    })

    await expect(api.getPipelines()).rejects.toBeTruthy()
    expect(useAuthStore.getState().token).toBeNull()
    expect(useAuthStore.getState().isAuthenticated()).toBe(false)
  })

  it('a non-401 failure leaves the session intact', async () => {
    useAuthStore.getState().setSession({ token: 't', username: 'u', expiresAt: inFuture() })
    const real = axios.create
    vi.spyOn(axios, 'create').mockImplementation((cfg) =>
      real({
        ...cfg,
        adapter: async () => {
          throw Object.assign(new Error('Server error'), { response: { status: 500 } })
        },
      }),
    )
    const { api } = await import('@/lib/api')

    await expect(api.getPipelines()).rejects.toBeTruthy()
    expect(useAuthStore.getState().token).toBe('t')
    expect(useAuthStore.getState().isAuthenticated()).toBe(true)
  })
})
