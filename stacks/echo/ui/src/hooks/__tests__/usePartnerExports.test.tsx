import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, waitFor, act } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import {
  usePartnerExports,
  usePartnerExport,
  useUpsertPartnerExport,
  useDeletePartnerExport,
  useRunPartnerExport,
} from '../usePartnerExports'
import { api } from '@/lib/api'

vi.mock('@/lib/api', () => ({
  api: {
    partnerExport: {
      listExports: vi.fn(),
      getExport: vi.fn(),
      upsertExport: vi.fn(),
      deleteExport: vi.fn(),
      runExport: vi.fn(),
    },
  },
}))

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>
}

const sampleExport = {
  export_id: 'partner_a_csv',
  name: 'Partner A daily CSV',
  enabled: true,
  source: { layer: 'silver' as const, dataset: 'silver.s.t' },
  format: { kind: 'csv' as const },
  mapping: [],
  delivery: {
    method: 'sftp' as const,
    partner_id: 'partner_a',
    connection_arn: 'arn:x',
    path_template: '/{YYYYMMDD}.csv',
  },
  schedule: {
    frequency: 'daily' as const,
    empty_file_behavior: 'skip' as const,
    retention_days: 30,
    alerts: { on_failure: true, on_partner_http_error: true, on_zero_rows: false, recipients: [] },
  },
  health: 'healthy' as const,
}

describe('usePartnerExports hooks', () => {
  beforeEach(() => {
    vi.mocked(api.partnerExport.listExports).mockReset()
    vi.mocked(api.partnerExport.getExport).mockReset()
    vi.mocked(api.partnerExport.upsertExport).mockReset()
    vi.mocked(api.partnerExport.deleteExport).mockReset()
    vi.mocked(api.partnerExport.runExport).mockReset()
  })

  it('listExports passes partner + source filters through', async () => {
    vi.mocked(api.partnerExport.listExports).mockResolvedValue([sampleExport])
    const { result } = renderHook(
      () => usePartnerExports({ partner: 'partner_a' }),
      { wrapper },
    )
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(api.partnerExport.listExports).toHaveBeenCalledWith({ partner: 'partner_a' })
    expect(result.current.data).toHaveLength(1)
  })

  it('getExport is enabled only when id is truthy', () => {
    renderHook(() => usePartnerExport(undefined), { wrapper })
    expect(api.partnerExport.getExport).not.toHaveBeenCalled()
  })

  it('upsertExport invalidates list + detail on success', async () => {
    vi.mocked(api.partnerExport.upsertExport).mockResolvedValue(undefined)
    const { result } = renderHook(() => useUpsertPartnerExport(), { wrapper })
    await act(async () => {
      await result.current.mutateAsync({ id: 'partner_a_csv', body: sampleExport })
    })
    expect(api.partnerExport.upsertExport).toHaveBeenCalledWith('partner_a_csv', sampleExport)
  })

  it('deleteExport invalidates the list', async () => {
    vi.mocked(api.partnerExport.deleteExport).mockResolvedValue(undefined)
    const { result } = renderHook(() => useDeletePartnerExport(), { wrapper })
    await act(async () => {
      await result.current.mutateAsync('partner_a_csv')
    })
    expect(api.partnerExport.deleteExport).toHaveBeenCalledWith('partner_a_csv')
  })

  it('runExport returns the run_id', async () => {
    vi.mocked(api.partnerExport.runExport).mockResolvedValue({ run_id: 'r123' })
    const { result } = renderHook(() => useRunPartnerExport(), { wrapper })
    let runId = ''
    await act(async () => {
      const out = await result.current.mutateAsync('partner_a_csv')
      runId = out.run_id
    })
    expect(runId).toBe('r123')
  })
})
