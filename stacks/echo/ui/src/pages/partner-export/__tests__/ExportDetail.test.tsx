import { describe, it, expect, vi, beforeEach } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Routes, Route } from 'react-router-dom'
import { renderWithProviders } from '@/test/test-utils'
import { ExportDetail } from '../ExportDetail'
import { api } from '@/lib/api'

vi.mock('@/lib/api', () => ({
  api: { partnerExport: { getExport: vi.fn(), listExportRuns: vi.fn(), runExport: vi.fn(), deleteExport: vi.fn() } },
}))

const sample = {
  export_id: 'partner_a_csv', name: 'Partner A daily', enabled: true,
  source: { layer: 'silver' as const, dataset: 'silver.s.t' },
  format: { kind: 'csv' as const, csv: { delimiter: ',', quote: '"', header_row: true } },
  mapping: [{ source_col: 'ID', output_field: 'id', type: 'int' as const }],
  delivery: { method: 'sftp' as const, partner_id: 'partner_a', connection_arn: 'arn:x', path_template: '/{YYYYMMDD}.csv' },
  schedule: { frequency: 'daily' as const, run_at: '02:00', empty_file_behavior: 'skip' as const, retention_days: 30, alerts: { on_failure: true, on_partner_http_error: true, on_zero_rows: false, recipients: [] } },
  health: 'healthy' as const,
}

beforeEach(() => {
  Object.values(api.partnerExport).forEach(fn => (fn as ReturnType<typeof vi.fn>).mockReset())
  vi.mocked(api.partnerExport.getExport).mockResolvedValue(sample)
  vi.mocked(api.partnerExport.listExportRuns).mockResolvedValue({ items: [], next_token: null })
})

const renderAt = (path: string) =>
  renderWithProviders(
    <Routes><Route path="/m/partner-export/exports/:exportId" element={<ExportDetail />} /></Routes>,
    { route: path },
  )

describe('ExportDetail', () => {
  it('renders the export name and overview tab by default', async () => {
    renderAt('/m/partner-export/exports/partner_a_csv')
    expect(await screen.findByRole('heading', { name: /partner a daily/i })).toBeInTheDocument()
    expect(screen.getByText(/silver.s.t/i)).toBeInTheDocument()
  })

  it('switches to the Mapping tab', async () => {
    renderAt('/m/partner-export/exports/partner_a_csv')
    await screen.findByRole('heading', { name: /partner a daily/i })
    await userEvent.click(screen.getByRole('tab', { name: /mapping/i }))
    expect(screen.getByText('ID')).toBeInTheDocument()
    expect(screen.getByText('id')).toBeInTheDocument()
  })

  it('Run now triggers runExport', async () => {
    vi.mocked(api.partnerExport.runExport).mockResolvedValue({ run_id: 'r99' })
    renderAt('/m/partner-export/exports/partner_a_csv')
    await screen.findByRole('heading', { name: /partner a daily/i })
    await userEvent.click(screen.getByRole('button', { name: /run now/i }))
    await waitFor(() => expect(api.partnerExport.runExport).toHaveBeenCalledWith('partner_a_csv'))
  })
})
