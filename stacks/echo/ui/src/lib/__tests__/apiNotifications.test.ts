import axios from 'axios'
import { afterEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/store/envStore', () => ({
  useEnvStore: { getState: () => ({ baseUrl: 'https://api.test.invalid', env: 'dev' }) },
}))

function fakeAxios() {
  const inst = {
    get:    vi.fn().mockResolvedValue({ data: { items: [], total: 0 } }),
    post:   vi.fn().mockResolvedValue({ data: {} }),
    patch:  vi.fn().mockResolvedValue({ data: {} }),
    delete: vi.fn().mockResolvedValue({ data: {} }),
    interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
  }
  vi.spyOn(axios, 'create').mockReturnValue(inst as unknown as ReturnType<typeof axios.create>)
  return inst
}

// The notifications contract, as read from pipeline_controller.py (UIFPE-543).
describe('api notifications send what the controller expects', () => {
  afterEach(() => vi.restoreAllMocks())

  it('getAlertSubscribers GETs the collection', async () => {
    const inst = fakeAxios()
    const { api } = await import('@/lib/api')
    await api.getAlertSubscribers()
    expect(inst.get).toHaveBeenCalledWith('/notifications/subscribers')
  })

  it('addAlertSubscriber sends gold_ids only when tables were chosen', async () => {
    const inst = fakeAxios()
    const { api } = await import('@/lib/api')
    await api.addAlertSubscriber('a@pearson.com', ['product', 'price'])
    expect(inst.post).toHaveBeenCalledWith('/notifications/subscribers', { email: 'a@pearson.com', gold_ids: ['product', 'price'] })
    await api.addAlertSubscriber('ops@pearson.com')
    // an absent key means "everything"; an empty list would be refused server-side
    expect(Object.keys(inst.post.mock.calls[1][1] as object)).toEqual(['email'])
    await api.addAlertSubscriber('ops2@pearson.com', [])
    expect(Object.keys(inst.post.mock.calls[2][1] as object)).toEqual(['email'])
  })

  it('setAlertSubscriber PATCHes the URL-encoded address with the patch as-is', async () => {
    const inst = fakeAxios()
    const { api } = await import('@/lib/api')
    await api.setAlertSubscriber('a@pearson.com', { enabled: false })
    expect(inst.patch).toHaveBeenCalledWith('/notifications/subscribers/a%40pearson.com', { enabled: false })
    await api.setAlertSubscriber('a@pearson.com', { remove_gold_ids: ['product'] })
    expect(inst.patch).toHaveBeenLastCalledWith('/notifications/subscribers/a%40pearson.com', { remove_gold_ids: ['product'] })
    await api.setAlertSubscriber('a@pearson.com', { gold_ids: null })
    expect(inst.patch).toHaveBeenLastCalledWith('/notifications/subscribers/a%40pearson.com', { gold_ids: null })
  })

  it('removeAlertSubscriber DELETEs the URL-encoded address', async () => {
    const inst = fakeAxios()
    const { api } = await import('@/lib/api')
    await api.removeAlertSubscriber('first.last+tag@pearson.com')
    expect(inst.delete).toHaveBeenCalledWith('/notifications/subscribers/first.last%2Btag%40pearson.com')
  })

  it('getAlertRules GETs the rules', async () => {
    const inst = fakeAxios()
    const { api } = await import('@/lib/api')
    await api.getAlertRules()
    expect(inst.get).toHaveBeenCalledWith('/notifications/rules')
  })

  it('the alert-config decoy is gone from the client', async () => {
    fakeAxios()
    const { api } = await import('@/lib/api')
    expect('getAlertConfig' in api).toBe(false)
    expect('upsertAlertConfig' in api).toBe(false)
  })
})
