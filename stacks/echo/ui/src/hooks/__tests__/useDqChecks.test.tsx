import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'

import { useDqChecks } from '../useDqChecks'
import { api } from '@/lib/api'
import type { DqCheckConfig } from '@/lib/types'

vi.mock('@/lib/api', () => ({
  api: {
    dq: {
      listChecks:    vi.fn(),
      getCheck:      vi.fn(),
      upsertCheck:   vi.fn(),
      deleteCheck:   vi.fn(),
      triggerRun:    vi.fn(),
      listCheckRuns: vi.fn(),
      listKinds:     vi.fn(),
    },
  },
}))

function wrap({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>
}

describe('useDqChecks', () => {
  beforeEach(() => vi.clearAllMocks())

  it('returns the list of checks from api.dq.listChecks', async () => {
    const mockChecks: DqCheckConfig[] = [
      { check_id: 'null-rate-x', version: 'latest', kind: 'null-rate', enabled: true,
        subject: { layer: 'silver', database: 'd', table: 't' },
        schedule: { frequency: 'daily' },
        parameters: { column: 'C', threshold_pct: 0.1 },
        metadata: { created_at: '2026-06-13T00:00:00Z', owner: 'r' } },
    ]
    vi.mocked(api.dq.listChecks).mockResolvedValue(mockChecks)
    const { result } = renderHook(() => useDqChecks(), { wrapper: wrap })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data).toEqual(mockChecks)
  })

  it('passes subject filter through to api', async () => {
    vi.mocked(api.dq.listChecks).mockResolvedValue([])
    renderHook(() => useDqChecks({ subject: 'silver.d.t' }), { wrapper: wrap })
    await waitFor(() => expect(api.dq.listChecks).toHaveBeenCalledWith({ subject: 'silver.d.t' }))
  })
})
