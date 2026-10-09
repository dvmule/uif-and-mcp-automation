import axios from 'axios'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { begin, end, isBusy, subscribe } from '@/lib/apiActivity'

vi.mock('@/store/envStore', () => ({
  useEnvStore: { getState: () => ({ baseUrl: 'https://api.test.invalid', env: 'dev' }) },
}))

afterEach(() => {
  while (isBusy()) end()
  vi.restoreAllMocks()
})

describe('apiActivity counter', () => {
  it('is busy while any request is in flight', () => {
    expect(isBusy()).toBe(false)
    begin()
    begin()
    expect(isBusy()).toBe(true)
    end()
    expect(isBusy()).toBe(true)
    end()
    expect(isBusy()).toBe(false)
  })

  it('never goes below zero', () => {
    end()
    end()
    begin()
    expect(isBusy()).toBe(true)
    end()
    expect(isBusy()).toBe(false)
  })

  it('notifies subscribers, and stops after unsubscribe', () => {
    const fn = vi.fn()
    const off = subscribe(fn)
    begin()
    end()
    expect(fn).toHaveBeenCalledTimes(2)
    off()
    begin()
    end()
    expect(fn).toHaveBeenCalledTimes(2)
  })
})

describe('api client feeds the activity counter', () => {
  function withAdapter(adapter: () => Promise<unknown>, seen: boolean[]) {
    const real = axios.create
    vi.spyOn(axios, 'create').mockImplementation((cfg) =>
      real({
        ...cfg,
        adapter: async () => {
          seen.push(isBusy())
          return adapter() as never
        },
      }),
    )
  }

  it('is busy during a request and idle after a success', async () => {
    const seen: boolean[] = []
    withAdapter(
      async () => ({ data: [], status: 200, statusText: 'OK', headers: {}, config: {} }),
      seen,
    )
    const { api } = await import('@/lib/api')

    await api.getPipelines()

    expect(seen).toEqual([true])
    expect(isBusy()).toBe(false)
  })

  it('is idle again after a failure, and the error still reaches the caller', async () => {
    const seen: boolean[] = []
    withAdapter(async () => {
      throw Object.assign(new Error('Server error'), { response: { status: 500 } })
    }, seen)
    const { api } = await import('@/lib/api')

    await expect(api.getPipelines()).rejects.toThrow('Server error')

    expect(seen).toEqual([true])
    expect(isBusy()).toBe(false)
  })
})
