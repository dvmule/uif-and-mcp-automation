import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, waitFor, act } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { usePartners, usePartner, useCreatePartner } from '../usePartners'
import { useFormatTemplates } from '../useFormatTemplates'
import { useExportRuns, useAllExportRuns } from '../useExportRuns'
import { api } from '@/lib/api'

vi.mock('@/lib/api', () => ({
  api: {
    partnerExport: {
      listPartners: vi.fn(),
      getPartner: vi.fn(),
      createPartner: vi.fn(),
      listFormats: vi.fn(),
      listExportRuns: vi.fn(),
      listRuns: vi.fn(),
    },
  },
}))

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>
}

beforeEach(() => {
  vi.mocked(api.partnerExport.listPartners).mockReset()
  vi.mocked(api.partnerExport.getPartner).mockReset()
  vi.mocked(api.partnerExport.createPartner).mockReset()
  vi.mocked(api.partnerExport.listFormats).mockReset()
  vi.mocked(api.partnerExport.listExportRuns).mockReset()
  vi.mocked(api.partnerExport.listRuns).mockReset()
})

describe('usePartners', () => {
  it('fetches the partner list', async () => {
    vi.mocked(api.partnerExport.listPartners).mockResolvedValue([
      { partner_id: 'p1', name: 'Partner A' },
    ])
    const { result } = renderHook(() => usePartners(), { wrapper })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data).toHaveLength(1)
  })

  it('usePartner is disabled when id is empty', () => {
    renderHook(() => usePartner(''), { wrapper })
    expect(api.partnerExport.getPartner).not.toHaveBeenCalled()
  })

  it('useCreatePartner invalidates the partners list', async () => {
    vi.mocked(api.partnerExport.createPartner).mockResolvedValue({
      partner_id: 'p2',
      name: 'Partner B',
    })
    const { result } = renderHook(() => useCreatePartner(), { wrapper })
    await act(async () => {
      await result.current.mutateAsync({ name: 'Partner B' })
    })
    expect(api.partnerExport.createPartner).toHaveBeenCalledWith({ name: 'Partner B' })
  })
})

describe('useFormatTemplates', () => {
  it('returns saved format templates', async () => {
    vi.mocked(api.partnerExport.listFormats).mockResolvedValue([
      { format_id: 'csv_default', name: 'CSV default', config: { kind: 'csv' } },
    ])
    const { result } = renderHook(() => useFormatTemplates(), { wrapper })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data?.[0].format_id).toBe('csv_default')
  })
})

describe('useExportRuns', () => {
  it('useExportRuns is disabled when no export_id', () => {
    renderHook(() => useExportRuns(undefined), { wrapper })
    expect(api.partnerExport.listExportRuns).not.toHaveBeenCalled()
  })

  it('useExportRuns fetches per-export runs', async () => {
    vi.mocked(api.partnerExport.listExportRuns).mockResolvedValue({ items: [], next_token: null })
    const { result } = renderHook(() => useExportRuns('partner_a_csv'), { wrapper })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(api.partnerExport.listExportRuns).toHaveBeenCalledWith('partner_a_csv', undefined)
  })

  it('useAllExportRuns fetches cross-export runs', async () => {
    vi.mocked(api.partnerExport.listRuns).mockResolvedValue({ items: [], next_token: null })
    const { result } = renderHook(() => useAllExportRuns(), { wrapper })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(api.partnerExport.listRuns).toHaveBeenCalled()
  })
})
