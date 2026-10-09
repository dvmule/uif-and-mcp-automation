import { describe, it, expect, vi } from 'vitest'
import type { ReactNode } from 'react'
import { renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useActiveRunId } from '@/hooks/useRuns'
import { activeRunKey } from '@/lib/runLifecycle'

vi.mock('@/lib/api', () => ({ api: {} }))

function setup(seed?: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  if (seed) qc.setQueryData<string | null>(activeRunKey('p1'), seed)
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  )
  return { qc, ...renderHook(() => useActiveRunId('p1'), { wrapper }) }
}

describe('useActiveRunId', () => {
  it('resolves the query to null (not undefined) when nothing is cached, and returns undefined', async () => {
    const { qc, result } = setup()
    await waitFor(() => expect(qc.getQueryState(activeRunKey('p1'))?.status).toBe('success'))
    expect(qc.getQueryData(activeRunKey('p1'))).toBeNull()
    expect(result.current).toBeUndefined()
  })

  it('returns the run id a trigger stored', async () => {
    const { result } = setup('jr_42')
    await waitFor(() => expect(result.current).toBe('jr_42'))
  })
})
