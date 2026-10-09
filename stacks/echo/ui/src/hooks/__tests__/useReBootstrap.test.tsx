import { describe, it, expect, vi } from 'vitest'
import { renderHook, waitFor, act } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { useReBootstrap } from '@/hooks/useReBootstrap'
import { api } from '@/lib/api'

vi.mock('@/lib/api', () => ({
  api: { reBootstrap: vi.fn(() => Promise.resolve()) },
}))

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>
}

describe('useReBootstrap', () => {
  it('calls api.reBootstrap', async () => {
    const { result } = renderHook(() => useReBootstrap(), { wrapper })
    await act(async () => { result.current.mutate('p1') })
    await waitFor(() => expect(vi.mocked(api.reBootstrap)).toHaveBeenCalledWith('p1'))
  })
})
