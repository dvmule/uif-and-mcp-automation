import { describe, it, expect, vi, beforeEach } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import { renderWithProviders } from '@/test/test-utils'
import { Runs } from '../Runs'
import { api } from '@/lib/api'

vi.mock('@/lib/api', () => ({
  api: { partnerExport: { listRuns: vi.fn() } },
}))

beforeEach(() => {
  vi.mocked(api.partnerExport.listRuns).mockReset()
})

describe('Runs page', () => {
  it('renders cross-export run rows', async () => {
    vi.mocked(api.partnerExport.listRuns).mockResolvedValue({
      items: [
        { run_id: 'r1', export_id: 'a1', status: 'SUCCEEDED', started_at: '2026-06-13T02:00:00Z', rows_written: 10 },
        { run_id: 'r2', export_id: 'b1', status: 'FAILED', started_at: '2026-06-13T01:00:00Z', error_message: 'sftp 530' },
      ],
      next_token: null,
    })
    renderWithProviders(<Runs />)
    await waitFor(() => expect(screen.getByText('r1')).toBeInTheDocument())
    expect(screen.getByText('r2')).toBeInTheDocument()
    expect(screen.getByText('SUCCEEDED')).toBeInTheDocument()
    expect(screen.getByText('FAILED')).toBeInTheDocument()
  })

  it('shows an empty-state message when no runs', async () => {
    vi.mocked(api.partnerExport.listRuns).mockResolvedValue({ items: [], next_token: null })
    renderWithProviders(<Runs />)
    expect(await screen.findByText(/no runs/i)).toBeInTheDocument()
  })
})
