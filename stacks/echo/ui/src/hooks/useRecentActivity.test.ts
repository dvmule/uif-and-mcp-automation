import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import React from 'react'
import { useRecentActivity } from './useRecentActivity'

vi.mock('@/lib/api', () => ({
  api: {
    getRecentActivity: vi.fn(),
  },
}))

import { api } from '@/lib/api'

function wrap() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return ({ children }: { children: React.ReactNode }) =>
    React.createElement(QueryClientProvider, { client: qc }, children)
}

describe('useRecentActivity', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('passes default limit=20 and returns items', async () => {
    const items = [
      { ts: '2026-06-13T18:00:00Z', module: 'sync' as const,
        subject: 'mtl_system_items_b', status: 'SUCCEEDED',
        message: '1234 rows', link_path: '/m/data-sync/syncs/mtl_system_items_b' },
    ]
    vi.mocked(api.getRecentActivity).mockResolvedValueOnce({ items, count: 1 })

    const { result } = renderHook(() => useRecentActivity(), { wrapper: wrap() })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data?.items).toEqual(items)
    expect(api.getRecentActivity).toHaveBeenCalledWith(20)
  })

  it('forwards a custom limit', async () => {
    vi.mocked(api.getRecentActivity).mockResolvedValueOnce({ items: [], count: 0 })
    const { result } = renderHook(() => useRecentActivity(5), { wrapper: wrap() })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(api.getRecentActivity).toHaveBeenCalledWith(5)
  })
})
