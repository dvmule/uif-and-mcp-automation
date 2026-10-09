import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, waitFor, act } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { api } from '@/lib/api'
import { toast } from 'sonner'
import {
  SCHEDULE_NOT_DEPLOYED,
  scheduleErrorMessage,
  useGoldRunLock,
  CADENCE_NOT_APPLIED,
  useSetGoldSchedule,
  useToggleGoldPipeline,
  useTriggerGoldRun,
} from '../useGoldPipelineMutations'
import type { GoldPipelineConfig } from '@/lib/types'

vi.mock('@/lib/api', () => ({
  api: { toggleGoldPipeline: vi.fn(), triggerGoldRun: vi.fn(), setGoldSchedule: vi.fn() },
}))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

function makeWrapper(qc: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  }
}

const seed: GoldPipelineConfig[] = [
  { gold_id: 'product', enabled: true },
  { gold_id: 'price', enabled: true },
]

describe('useToggleGoldPipeline', () => {
  let qc: QueryClient
  beforeEach(() => {
    qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
    qc.setQueryData(['gold-pipelines'], seed)
    vi.mocked(api.toggleGoldPipeline).mockReset()
    vi.mocked(toast.error).mockReset()
  })

  it('calls the API with the explicit boolean and patches the list optimistically', async () => {
    vi.mocked(api.toggleGoldPipeline).mockResolvedValue({ gold_id: 'product', enabled: false })
    const { result } = renderHook(() => useToggleGoldPipeline(), { wrapper: makeWrapper(qc) })

    act(() => result.current.mutate({ id: 'product', enabled: false }))
    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(api.toggleGoldPipeline).toHaveBeenCalledWith('product', false)
    const list = qc.getQueryData<GoldPipelineConfig[]>(['gold-pipelines'])!
    expect(list.find(g => g.gold_id === 'product')?.enabled).toBe(false)
    expect(list.find(g => g.gold_id === 'price')?.enabled).toBe(true)
  })

  it('surfaces the server error string rather than failing silently', async () => {
    vi.mocked(api.toggleGoldPipeline).mockRejectedValue(new Error("body must include 'enabled' boolean"))
    const { result } = renderHook(() => useToggleGoldPipeline(), { wrapper: makeWrapper(qc) })

    act(() => result.current.mutate({ id: 'product', enabled: false }))
    await waitFor(() => expect(result.current.isError).toBe(true))

    expect(toast.error).toHaveBeenCalledWith("body must include 'enabled' boolean")
  })
})

describe('useTriggerGoldRun + useGoldRunLock', () => {
  let qc: QueryClient
  beforeEach(() => {
    qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
    vi.mocked(api.triggerGoldRun).mockReset()
    vi.mocked(toast.success).mockReset()
    vi.mocked(toast.error).mockReset()
  })

  it('locks the entity for the session once a run has started', async () => {
    vi.mocked(api.triggerGoldRun).mockResolvedValue({
      gold_id: 'product', kind: 'transform', run_id: 'jr_123', rebuild: false,
    })
    const wrapper = makeWrapper(qc)
    const lock = renderHook(() => useGoldRunLock('product'), { wrapper })
    expect(lock.result.current).toBeNull()

    const trigger = renderHook(() => useTriggerGoldRun(), { wrapper })
    act(() => trigger.result.current.mutate('product'))
    await waitFor(() => expect(trigger.result.current.isSuccess).toBe(true))

    expect(api.triggerGoldRun).toHaveBeenCalledWith('product')
    await waitFor(() => expect(lock.result.current).toBe('jr_123'))
    expect(toast.success).toHaveBeenCalledWith('Started transform run for product')
  })

  it('does not lock on failure, and says why', async () => {
    vi.mocked(api.triggerGoldRun).mockRejectedValue(new Error("gold pipeline 'nope' not found"))
    const wrapper = makeWrapper(qc)
    const lock = renderHook(() => useGoldRunLock('nope'), { wrapper })
    const trigger = renderHook(() => useTriggerGoldRun(), { wrapper })

    act(() => trigger.result.current.mutate('nope'))
    await waitFor(() => expect(trigger.result.current.isError).toBe(true))

    expect(lock.result.current).toBeNull()
    expect(toast.error).toHaveBeenCalledWith("gold pipeline 'nope' not found")
  })

  // UIFPE-516: once the server refuses over an in-flight run, its 409 names the
  // run and job. That text must reach the operator unedited, and no lock is set.
  it('surfaces the server 409 for an in-flight run verbatim and does not lock', async () => {
    const msg = "gold pipeline 'product' already has an in-flight run jr_abc (RUNNING) on uif-echo-dev-gold-transform"
    vi.mocked(api.triggerGoldRun).mockRejectedValue(axiosErr(409, { error: msg }))
    const wrapper = makeWrapper(qc)
    const lock = renderHook(() => useGoldRunLock('product'), { wrapper })
    const trigger = renderHook(() => useTriggerGoldRun(), { wrapper })

    act(() => trigger.result.current.mutate('product'))
    await waitFor(() => expect(trigger.result.current.isError).toBe(true))

    expect(toast.error).toHaveBeenCalledWith(msg)
    expect(lock.result.current).toBeNull()
  })
})

function axiosErr(status: number, data: unknown) {
  return Object.assign(new Error(`Request failed with status code ${status}`), {
    isAxiosError: true,
    response: { status, data },
  })
}

describe('useSetGoldSchedule (UIFPE-513, 532, 564)', () => {
  let qc: QueryClient
  beforeEach(() => {
    qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
    qc.setQueryData(['gold-pipelines'], [
      { gold_id: 'product', enabled: true, schedule: { frequency: 'gold-24h', start_time: '06:30' } },
      { gold_id: 'price', enabled: true, schedule: { frequency: 'gold-24h', start_time: '08:45' } },
    ] satisfies GoldPipelineConfig[])
    vi.mocked(api.setGoldSchedule).mockReset()
    vi.mocked(toast.error).mockReset()
    vi.mocked(toast.success).mockReset()
  })

  it('sends only the start time and patches the list optimistically, leaving cadence alone', async () => {
    vi.mocked(api.setGoldSchedule).mockResolvedValue({
      gold_id: 'product', schedule: { frequency: 'gold-24h', start_time: '07:00' },
    })
    const { result } = renderHook(() => useSetGoldSchedule(), { wrapper: makeWrapper(qc) })
    act(() => result.current.mutate({ id: 'product', change: { start_time: '07:00' } }))
    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(api.setGoldSchedule).toHaveBeenCalledWith('product', { start_time: '07:00' })   // no day set, no cadence: neither sent
    const list = qc.getQueryData<GoldPipelineConfig[]>(['gold-pipelines'])!
    expect(list.find(g => g.gold_id === 'product')?.schedule).toEqual({ frequency: 'gold-24h', start_time: '07:00' })
    expect(list.find(g => g.gold_id === 'price')?.schedule?.start_time).toBe('08:45')
  })

  it('sends the UTC day set with the time on the weekly rung and patches both optimistically', async () => {
    qc.setQueryData(['gold-pipelines'], [
      { gold_id: 'product', enabled: true, schedule: { frequency: 'gold-weekly', start_time: '06:30', weekly_days: ['MON'] } },
    ] satisfies GoldPipelineConfig[])
    vi.mocked(api.setGoldSchedule).mockResolvedValue({
      gold_id: 'product', schedule: { frequency: 'gold-weekly', start_time: '03:00', weekly_days: ['WED', 'FRI'] },
    })
    const { result } = renderHook(() => useSetGoldSchedule(), { wrapper: makeWrapper(qc) })
    act(() => result.current.mutate({ id: 'product', change: { start_time: '03:00', weekly_days: ['WED', 'FRI'] } }))
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(api.setGoldSchedule).toHaveBeenCalledWith('product', { start_time: '03:00', weekly_days: ['WED', 'FRI'] })
    const list = qc.getQueryData<GoldPipelineConfig[]>(['gold-pipelines'])!
    expect(list[0].schedule).toEqual({ frequency: 'gold-weekly', start_time: '03:00', weekly_days: ['WED', 'FRI'] })
    expect(toast.success).toHaveBeenCalledWith('product now runs on WED, FRI at 03:00 UTC')
  })

  it('sends the cadence when it moved and patches the list optimistically (UIFPE-564)', async () => {
    vi.mocked(api.setGoldSchedule).mockResolvedValue({
      gold_id: 'product', schedule: { frequency: 'gold-4h', start_time: '06:30' },
    })
    const { result } = renderHook(() => useSetGoldSchedule(), { wrapper: makeWrapper(qc) })
    act(() => result.current.mutate({ id: 'product', change: { frequency: 'gold-4h', start_time: '06:30' } }))
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(api.setGoldSchedule).toHaveBeenCalledWith('product', { frequency: 'gold-4h', start_time: '06:30' })
    const list = qc.getQueryData<GoldPipelineConfig[]>(['gold-pipelines'])!
    expect(list.find(g => g.gold_id === 'product')?.schedule)
      .toEqual({ frequency: 'gold-4h', start_time: '06:30' })
    expect(toast.success).toHaveBeenCalledWith('product now runs every 4 hours at 06:30 UTC')
  })

  it('refuses to claim success when the deployment ignored the cadence (UIFPE-564)', async () => {
    // The API half rides the latched controller while this bundle deploys on
    // merge: an older controller accepts the request, saves the time and drops
    // frequency. The echo is the only way to tell, so the hook checks it.
    vi.mocked(api.setGoldSchedule).mockResolvedValue({
      gold_id: 'product', schedule: { frequency: 'gold-24h', start_time: '06:30' },
    })
    const { result } = renderHook(() => useSetGoldSchedule(), { wrapper: makeWrapper(qc) })
    act(() => result.current.mutate({ id: 'product', change: { frequency: 'gold-4h', start_time: '06:30' } }))
    await waitFor(() => expect(result.current.isError).toBe(true))
    expect(toast.error).toHaveBeenCalledWith(CADENCE_NOT_APPLIED)
    expect(toast.success).not.toHaveBeenCalled()
  })

  it('does not check the echo when no cadence was sent', async () => {
    // A start-time-only edit must keep working on a deployment whose response
    // carries whatever cadence the row already had.
    vi.mocked(api.setGoldSchedule).mockResolvedValue({
      gold_id: 'product', schedule: { frequency: 'gold-24h', start_time: '07:00' },
    })
    const { result } = renderHook(() => useSetGoldSchedule(), { wrapper: makeWrapper(qc) })
    act(() => result.current.mutate({ id: 'product', change: { start_time: '07:00' } }))
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(toast.error).not.toHaveBeenCalled()
  })

  it('names the weekly rung and its days in the toast', async () => {
    vi.mocked(api.setGoldSchedule).mockResolvedValue({
      gold_id: 'product',
      schedule: { frequency: 'gold-weekly', start_time: '03:00', weekly_days: ['TUE'] },
    })
    const { result } = renderHook(() => useSetGoldSchedule(), { wrapper: makeWrapper(qc) })
    act(() => result.current.mutate({
      id: 'product', change: { frequency: 'gold-weekly', start_time: '03:00', weekly_days: ['TUE'] },
    }))
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(toast.success).toHaveBeenCalledWith('product now runs weekly on TUE at 03:00 UTC')
  })

  it("shows the server's own message for a rejected value", async () => {
    vi.mocked(api.setGoldSchedule).mockRejectedValue(
      axiosErr(400, { error: "body must include start_time as 'HH:MM' (UTC), got ':30'" }),
    )
    const { result } = renderHook(() => useSetGoldSchedule(), { wrapper: makeWrapper(qc) })
    act(() => result.current.mutate({ id: 'product', change: { start_time: ':30' } }))
    await waitFor(() => expect(result.current.isError).toBe(true))
    expect(toast.error).toHaveBeenCalledWith("body must include start_time as 'HH:MM' (UTC), got ':30'")
  })
})

describe('scheduleErrorMessage', () => {
  // The route needs the supervised apply; until then API Gateway 404s with a
  // bare {message}. That must read as "not deployed", not as a bug.
  it('names an undeployed route on a bare gateway 404', () => {
    expect(scheduleErrorMessage(axiosErr(404, { message: 'Not Found' }))).toBe(SCHEDULE_NOT_DEPLOYED)
  })
  it("passes through the handler's own 404 for an unknown entity", () => {
    expect(scheduleErrorMessage(axiosErr(404, { error: "gold pipeline 'nope' not found" })))
      .toBe("gold pipeline 'nope' not found")
  })
})
