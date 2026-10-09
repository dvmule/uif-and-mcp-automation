import { describe, it, expect, vi } from 'vitest'
import { renderHook, waitFor, act } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { useResetWatermark } from '@/hooks/useResetWatermark'
import { api } from '@/lib/api'

vi.mock('@/lib/api', () => ({
  api: { resetWatermark: vi.fn(() => Promise.resolve()) },
}))

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>
}

describe('useResetWatermark', () => {
  it('calls api.resetWatermark with the pipeline id', async () => {
    const { result } = renderHook(() => useResetWatermark(), { wrapper })
    await act(async () => { result.current.mutate('p1') })
    await waitFor(() => expect(vi.mocked(api.resetWatermark)).toHaveBeenCalledWith('p1'))
  })
})
