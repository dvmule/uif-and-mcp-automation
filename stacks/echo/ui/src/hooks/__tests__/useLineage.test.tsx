import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { useLineage } from '../useLineage'
import { api } from '@/lib/api'

vi.mock('@/lib/api', () => ({
  api: {
    getLineage: vi.fn(),
  },
}))

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>
}

describe('useLineage', () => {
  beforeEach(() => {
    vi.mocked(api.getLineage).mockReset()
  })

  it('returns lineage data for a dataset', async () => {
    vi.mocked(api.getLineage).mockResolvedValueOnce({
      dataset: 'silver.gcp.smoke_silver.author_data',
      upstream: { sync_id: 'smoke_author', sync_name: 'smoke_author' },
      downstream_exports: [{ export_id: 'ex_partner_a', name: 'Partner A daily CSV' }],
      downstream_checks: [{ check_id: 'chk_author_null', name: 'Author null-rate' }],
    })

    const { result } = renderHook(
      () => useLineage('silver.gcp.smoke_silver.author_data'),
      { wrapper },
    )

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data?.upstream?.sync_id).toBe('smoke_author')
    expect(result.current.data?.downstream_exports).toHaveLength(1)
    expect(result.current.data?.downstream_checks).toHaveLength(1)
    expect(api.getLineage).toHaveBeenCalledWith('silver.gcp.smoke_silver.author_data')
  })

  it('does not fire when dataset is undefined', () => {
    renderHook(() => useLineage(undefined), { wrapper })
    expect(api.getLineage).not.toHaveBeenCalled()
  })
})
