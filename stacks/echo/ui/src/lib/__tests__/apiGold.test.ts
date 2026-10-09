import axios from 'axios'
import { afterEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/store/envStore', () => ({
  useEnvStore: { getState: () => ({ baseUrl: 'https://api.test.invalid', env: 'dev' }) },
}))

/** A fake axios instance so the assertions are on the request the client
 *  BUILDS - path and body - not on a network that is not there. */
function fakeAxios() {
  const inst = {
    patch: vi.fn().mockResolvedValue({ data: {} }),
    post:  vi.fn().mockResolvedValue({ data: {} }),
    get:   vi.fn().mockResolvedValue({ data: [] }),
    interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
  }
  vi.spyOn(axios, 'create').mockReturnValue(inst as unknown as ReturnType<typeof axios.create>)
  return inst
}

// The gold mutation contracts, as read from pipeline_controller.py. The hooks
// mock `api`, so nothing else proves the client sends what the handlers expect.
describe('api gold mutations send what the handlers expect', () => {
  afterEach(() => vi.restoreAllMocks())

  it('setGoldSchedule PATCHes …/schedule with ONLY start_time (UIFPE-513)', async () => {
    const inst = fakeAxios()
    const { api } = await import('@/lib/api')
    await api.setGoldSchedule('product', { start_time: '07:00' })
    expect(inst.patch).toHaveBeenCalledWith('/gold-pipelines/product/schedule', { start_time: '07:00' })
    // toEqual ignores undefined keys, so pin the KEY SET: a plain time change must
    // carry neither weekly_days (undefined would wipe a weekly entity's days) nor
    // frequency (the handler would treat an unchanged cadence as a move)
    expect(Object.keys(inst.patch.mock.calls[0][1] as object)).toEqual(['start_time'])
  })

  it('setGoldSchedule sends weekly_days beside start_time only when given (UIFPE-532)', async () => {
    const inst = fakeAxios()
    const { api } = await import('@/lib/api')
    await api.setGoldSchedule('product', { start_time: '03:00', weekly_days: ['WED', 'FRI'] })
    expect(inst.patch).toHaveBeenCalledWith('/gold-pipelines/product/schedule',
      { start_time: '03:00', weekly_days: ['WED', 'FRI'] })
  })

  it('setGoldSchedule sends frequency only when the cadence moved (UIFPE-564)', async () => {
    const inst = fakeAxios()
    const { api } = await import('@/lib/api')
    await api.setGoldSchedule('product', { frequency: 'gold-4h', start_time: '00:15' })
    expect(inst.patch).toHaveBeenCalledWith('/gold-pipelines/product/schedule',
      { frequency: 'gold-4h', start_time: '00:15' })
    await api.setGoldSchedule('product', { start_time: '00:15' })
    expect(Object.keys(inst.patch.mock.calls[1][1] as object)).toEqual(['start_time'])
    await api.setGoldSchedule('product', { frequency: 'gold-weekly', start_time: '03:00', weekly_days: ['TUE'] })
    expect(Object.keys(inst.patch.mock.calls[2][1] as object)).toEqual(['frequency', 'start_time', 'weekly_days'])
  })

  it('toggleGoldPipeline PATCHes …/toggle with an explicit JSON boolean', async () => {
    const inst = fakeAxios()
    const { api } = await import('@/lib/api')
    await api.toggleGoldPipeline('product', false)
    // bool(body["enabled"]) server-side: the string "false" would store true.
    expect(inst.patch).toHaveBeenCalledWith('/gold-pipelines/product/toggle', { enabled: false })
    expect(typeof inst.patch.mock.calls[0][1].enabled).toBe('boolean')
  })

  it('triggerGoldRun POSTs …/run with an empty body (kind defaults to transform)', async () => {
    const inst = fakeAxios()
    const { api } = await import('@/lib/api')
    await api.triggerGoldRun('product')
    expect(inst.post).toHaveBeenCalledWith('/gold-pipelines/product/run', {})
  })
})
