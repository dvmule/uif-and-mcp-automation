import { describe, it, expect, vi, beforeEach } from 'vitest'
import { screen } from '@testing-library/react'
import { renderWithProviders } from '@/test/test-utils'
import { LinkedExportsList } from '../LinkedExportsList'
import { api } from '@/lib/api'

vi.mock('@/lib/api', () => ({
  api: { partnerExport: { listExports: vi.fn() } },
}))

beforeEach(() => {
  vi.mocked(api.partnerExport.listExports).mockReset()
})

const ex = {
  export_id: 'a1', name: 'A daily', enabled: true,
  source: { layer: 'silver' as const, dataset: 'silver.s.t' },
  format: { kind: 'csv' as const }, mapping: [],
  delivery: { method: 'sftp' as const, partner_id: 'partner_a', connection_arn: 'arn:x', path_template: '/x' },
  schedule: { frequency: 'daily' as const, empty_file_behavior: 'skip' as const, retention_days: 30, alerts: { on_failure: true, on_partner_http_error: true, on_zero_rows: false, recipients: [] } },
  health: 'healthy' as const,
}

describe('LinkedExportsList', () => {
  it('passes the source FQN as a filter to listExports', async () => {
    vi.mocked(api.partnerExport.listExports).mockResolvedValue([ex])
    renderWithProviders(<LinkedExportsList sourceFqn="silver.s.t" />)
    await screen.findByText('A daily')
    expect(api.partnerExport.listExports).toHaveBeenCalledWith({ source: 'silver.s.t' })
  })

  it('renders a + New Export link in the empty state', async () => {
    vi.mocked(api.partnerExport.listExports).mockResolvedValue([])
    renderWithProviders(<LinkedExportsList sourceFqn="silver.s.t" />)
    const link = await screen.findByRole('link', { name: /create export/i })
    expect(link).toHaveAttribute('href', '/m/partner-export/exports/new')
  })

  it('each export row links to its ExportDetail', async () => {
    vi.mocked(api.partnerExport.listExports).mockResolvedValue([ex])
    renderWithProviders(<LinkedExportsList sourceFqn="silver.s.t" />)
    const row = await screen.findByRole('link', { name: /a daily/i })
    expect(row).toHaveAttribute('href', '/m/partner-export/exports/a1')
  })
})
