import { describe, it, expect, vi } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { useDownstreamSyncs } from '@/hooks/useDownstreamSyncs'
import { api } from '@/lib/api'

vi.mock('@/lib/api', () => ({
  api: { getPipelines: vi.fn() },
}))

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>
}

describe('useDownstreamSyncs', () => {
  it('returns pipelines whose extract.table matches the silver FQN', async () => {
    // Real PipelineConfig.extract is FLAT — { table, mode, watermark_col }.
    // Mocks use `as any` so we only assert the filter logic.
    vi.mocked(api.getPipelines).mockResolvedValue([
      { pipeline_id: 'a', extract: { table: 'items.mtl_system_items_b', mode: 'incremental' } },
      { pipeline_id: 'b', extract: { table: 'other.table', mode: 'incremental' } },
      { pipeline_id: 'c', extract: { table: 'items.mtl_system_items_b', mode: 'incremental' } },
    ] as any)
    const { result } = renderHook(() => useDownstreamSyncs('items.mtl_system_items_b'), { wrapper })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data?.map(p => p.pipeline_id)).toEqual(['a', 'c'])
  })

  it('returns empty array when silverFqn is empty', async () => {
    const { result } = renderHook(() => useDownstreamSyncs(''), { wrapper })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data).toEqual([])
  })
})
