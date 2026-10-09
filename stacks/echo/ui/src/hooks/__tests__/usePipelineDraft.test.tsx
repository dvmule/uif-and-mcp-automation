import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, waitFor, act } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { usePipelineDraft } from '@/hooks/usePipelineDraft'

vi.mock('@/lib/feature-flags', () => ({
  FEATURE_FLAGS: { draftPersistenceEnabled: false },
}))

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>
}

describe('usePipelineDraft', () => {
  beforeEach(() => { localStorage.clear() })

  it('persists draft to localStorage when backend disabled', async () => {
    const { result } = renderHook(() => usePipelineDraft('p1'), { wrapper })
    await act(async () => { result.current.save({ pipeline_id: 'p1', enabled: true } as any) })
    expect(localStorage.getItem('pipeline-draft-p1')).toContain('"pipeline_id":"p1"')
  })

  it('loads the existing draft on init', async () => {
    localStorage.setItem('pipeline-draft-p2', JSON.stringify({ pipeline_id: 'p2', enabled: true }))
    const { result } = renderHook(() => usePipelineDraft('p2'), { wrapper })
    await waitFor(() => expect(result.current.draft?.pipeline_id).toBe('p2'))
  })

  it('clears the draft', async () => {
    localStorage.setItem('pipeline-draft-p3', JSON.stringify({ pipeline_id: 'p3' }))
    const { result } = renderHook(() => usePipelineDraft('p3'), { wrapper })
    await act(async () => { result.current.discard() })
    expect(localStorage.getItem('pipeline-draft-p3')).toBeNull()
  })
})
