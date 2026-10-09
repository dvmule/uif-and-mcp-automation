import { describe, it, expect, vi, beforeEach } from 'vitest'
import { screen } from '@testing-library/react'
import { renderWithProviders } from '@/test/test-utils'
import { Formats } from '../Formats'
import { api } from '@/lib/api'

vi.mock('@/lib/api', () => ({
  api: { partnerExport: { listFormats: vi.fn() } },
}))

beforeEach(() => {
  vi.mocked(api.partnerExport.listFormats).mockReset()
})

describe('Formats page', () => {
  it('renders saved format templates', async () => {
    vi.mocked(api.partnerExport.listFormats).mockResolvedValue([
      { format_id: 'csv_default', name: 'CSV default', description: 'Comma-separated, header on', config: { kind: 'csv' } },
      { format_id: 'ndjson', name: 'NDJSON', config: { kind: 'json', json: { lines: true } } },
    ])
    renderWithProviders(<Formats />)
    expect(await screen.findByText('CSV default')).toBeInTheDocument()
    expect(screen.getByText('NDJSON')).toBeInTheDocument()
  })

  it('renders an empty state', async () => {
    vi.mocked(api.partnerExport.listFormats).mockResolvedValue([])
    renderWithProviders(<Formats />)
    expect(await screen.findByText(/no format templates/i)).toBeInTheDocument()
  })
})
