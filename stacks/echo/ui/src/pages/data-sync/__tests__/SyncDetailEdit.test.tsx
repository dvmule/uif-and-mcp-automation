import { describe, it, expect, vi } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { SyncDetailEdit } from '@/pages/data-sync/SyncDetailEdit'

// Stable references — without these, the mocked hooks return fresh objects each
// render which makes `useEditDraft`'s effect re-fire and triggers an
// infinite-loop in React 19 + jsdom.
const PIPELINE = {
  pipeline_id: 'p1',
  enabled: true,
  extract: { jdbc_predicates: [] },
  silver: { enabled: false },
}
const PIPELINE_RESULT = { data: PIPELINE }
const UPSERT_RESULT = { mutateAsync: vi.fn(), isPending: false }
const DRAFT_RESULT = { draft: null, save: vi.fn(), discard: vi.fn() }

vi.mock('@/hooks/usePipelines', () => ({
  usePipeline: () => PIPELINE_RESULT,
  useUpsertPipeline: () => UPSERT_RESULT,
}))
vi.mock('@/hooks/usePipelineDraft', () => ({
  usePipelineDraft: () => DRAFT_RESULT,
}))

function renderAt(path: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/m/data-sync/syncs/:id/edit/*" element={<SyncDetailEdit />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('SyncDetailEdit', () => {
  it('renders the breadcrumb header and the extraction section card', () => {
    renderAt('/m/data-sync/syncs/p1/edit/extraction')
    expect(screen.getByRole('navigation', { name: 'Breadcrumb' })).toHaveTextContent('Replicate › p1 › Edit')
    expect(screen.getByRole('heading', { level: 1, name: 'Edit p1' })).toBeInTheDocument()
    expect(screen.getByText('Changes save to the pipeline config and apply from the next run.')).toBeInTheDocument()
    // section card header = section name + aside; the active link is the same section
    expect(screen.getByRole('heading', { level: 3, name: 'Extraction' })).toBeInTheDocument()
    expect(screen.getByText('how each run reads the table')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Extraction' })).toHaveAttribute('aria-current', 'page')
  })

  it('lists the seven sections and keeps Discard / Save draft / Save changes in the card footer', async () => {
    renderAt('/m/data-sync/syncs/p1/edit/extraction')
    const nav = screen.getByRole('navigation', { name: 'Edit sections' })
    expect(within(nav).getAllByRole('link').map(a => a.textContent)).toEqual([
      'Identity', 'Extraction', 'Glue runtime', 'Silver', 'Quality', 'Reconcile', 'Raw JSON',
    ])
    expect(screen.getByRole('button', { name: 'Discard' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Save draft' })).toBeInTheDocument()
    await userEvent.setup().click(screen.getByRole('button', { name: 'Save changes' }))
    expect(UPSERT_RESULT.mutateAsync).toHaveBeenCalledWith({ id: 'p1', config: expect.objectContaining({ pipeline_id: 'p1' }) })
  })
})
