import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import React from 'react'
import { useHomeSummary } from './useHomeSummary'

vi.mock('@/lib/api', () => ({
  api: {
    getHomeSummary: vi.fn(),
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

describe('useHomeSummary', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('returns the summary payload shape from the API', async () => {
    const payload = {
      syncs:   { ok: 5, fail: 1, in_24h: 6 },
      exports: { ok: 0, fail: 0, in_24h: 0 },
      dq:      { ok: 0, fail: 0, in_24h: 0 },
      window_hours: 24,
      generated_at: '2026-06-13T18:00:00Z',
    }
    vi.mocked(api.getHomeSummary).mockResolvedValueOnce(payload)

    const { result } = renderHook(() => useHomeSummary(), { wrapper: wrap() })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data).toEqual(payload)
    expect(api.getHomeSummary).toHaveBeenCalledTimes(1)
  })
})
