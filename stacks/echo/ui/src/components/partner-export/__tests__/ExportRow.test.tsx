import { describe, it, expect, beforeEach } from 'vitest'
import { screen } from '@testing-library/react'
import { renderWithProviders } from '@/test/test-utils'
import { ExportRow } from '../ExportRow'
import type { PartnerExport } from '@/lib/types'

const ex: PartnerExport = {
  export_id: 'partner_a_daily_csv', name: 'Partner A daily CSV', enabled: true,
  source: { layer: 'silver', dataset: 'silver.s.t' },
  format: { kind: 'csv' },
  mapping: [],
  delivery: { method: 'sftp', partner_id: 'partner_a', connection_arn: 'arn:x', path_template: '/{YYYYMMDD}.csv' },
  schedule: { frequency: 'daily', run_at: '02:00', empty_file_behavior: 'skip', retention_days: 30, alerts: { on_failure: true, on_partner_http_error: true, on_zero_rows: false, recipients: [] } },
  health: 'healthy',
  last_run: { run_id: 'r1', export_id: 'partner_a_daily_csv', status: 'SUCCEEDED', started_at: '2026-06-13T02:00:00Z', completed_at: '2026-06-13T02:04:32Z', rows_written: 12345 },
}

describe('ExportRow', () => {
  beforeEach(() => renderWithProviders(<ExportRow exportItem={ex} />))

  it('renders the export name + dataset + format chip + delivery badge', () => {
    expect(screen.getByText('Partner A daily CSV')).toBeInTheDocument()
    expect(screen.getByText('silver.s.t')).toBeInTheDocument()
    expect(screen.getByTestId('delivery-badge-sftp')).toBeInTheDocument()
    // format chip cell shows the format kind verbatim
    expect(screen.getAllByText(/csv/i).length).toBeGreaterThan(0)
  })

  it('renders the last-run row count', () => {
    expect(screen.getByText(/12,345/)).toBeInTheDocument()
  })

  it('the row links to /m/partner-export/exports/{id}', () => {
    expect(screen.getByRole('link', { name: /partner a daily csv/i }))
      .toHaveAttribute('href', '/m/partner-export/exports/partner_a_daily_csv')
  })
})
