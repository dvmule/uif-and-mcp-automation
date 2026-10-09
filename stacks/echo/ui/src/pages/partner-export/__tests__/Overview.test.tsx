import { describe, it, expect, vi, beforeEach } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import { renderWithProviders } from '@/test/test-utils'
import { Overview } from '../Overview'
import { api } from '@/lib/api'

vi.mock('@/lib/api', () => ({
  api: { partnerExport: { listExports: vi.fn(), listRuns: vi.fn() } },
}))

beforeEach(() => {
  Object.values(api.partnerExport).forEach(fn => (fn as ReturnType<typeof vi.fn>).mockReset())
})

const base = {
  source: { layer: 'silver' as const, dataset: 'silver.s.t' },
  format: { kind: 'csv' as const },
  mapping: [],
  delivery: { method: 'sftp' as const, partner_id: 'p', connection_arn: 'arn', path_template: '/x' },
  schedule: { frequency: 'daily' as const, empty_file_behavior: 'skip' as const, retention_days: 30, alerts: { on_failure: true, on_partner_http_error: true, on_zero_rows: false, recipients: [] } },
}

describe('Overview', () => {
  it('counts active exports and pass/fail tiles', async () => {
    vi.mocked(api.partnerExport.listExports).mockResolvedValue([
      { ...base, export_id: 'a', name: 'a', enabled: true },
      { ...base, export_id: 'b', name: 'b', enabled: true },
      { ...base, export_id: 'c', name: 'c', enabled: false },
    ])
    vi.mocked(api.partnerExport.listRuns).mockResolvedValue({
      items: [
        { run_id: 'r1', export_id: 'a', status: 'SUCCEEDED', started_at: '2026-06-13T00:00:00Z' },
        { run_id: 'r2', export_id: 'a', status: 'SUCCEEDED', started_at: '2026-06-13T00:00:00Z' },
        { run_id: 'r3', export_id: 'b', status: 'FAILED',    started_at: '2026-06-13T00:00:00Z' },
      ],
      next_token: null,
    })
    renderWithProviders(<Overview />)
    await waitFor(() => expect(screen.getByTestId('kpi-active')).toHaveTextContent('2'))
    expect(screen.getByTestId('kpi-pass')).toHaveTextContent('2')
    expect(screen.getByTestId('kpi-fail')).toHaveTextContent('1')
  })
})
