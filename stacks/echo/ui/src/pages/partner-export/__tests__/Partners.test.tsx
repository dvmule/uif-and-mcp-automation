import { describe, it, expect, vi, beforeEach } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import { Routes, Route } from 'react-router-dom'
import { renderWithProviders } from '@/test/test-utils'
import { Partners } from '../Partners'
import { PartnerDetail } from '../PartnerDetail'
import { api } from '@/lib/api'

vi.mock('@/lib/api', () => ({
  api: { partnerExport: { listPartners: vi.fn(), getPartner: vi.fn(), listExports: vi.fn() } },
}))

beforeEach(() => {
  Object.values(api.partnerExport).forEach(fn => (fn as ReturnType<typeof vi.fn>).mockReset())
})

describe('Partners directory', () => {
  it('lists partners with their export counts', async () => {
    vi.mocked(api.partnerExport.listPartners).mockResolvedValue([
      { partner_id: 'partner_a', name: 'Partner A', export_count: 3, health: 'healthy' },
      { partner_id: 'partner_b', name: 'Partner B', export_count: 1, health: 'failing' },
    ])
    renderWithProviders(<Partners />)
    await screen.findByText('Partner A')
    expect(screen.getByText('Partner B')).toBeInTheDocument()
    expect(screen.getByText(/3 exports/i)).toBeInTheDocument()
  })

  it('partner row links to detail', async () => {
    vi.mocked(api.partnerExport.listPartners).mockResolvedValue([
      { partner_id: 'partner_a', name: 'Partner A', export_count: 0 },
    ])
    renderWithProviders(<Partners />)
    const link = await screen.findByRole('link', { name: /partner a/i })
    expect(link).toHaveAttribute('href', '/m/partner-export/partners/partner_a')
  })
})

describe('PartnerDetail', () => {
  it('renders partner name + linked exports', async () => {
    vi.mocked(api.partnerExport.getPartner).mockResolvedValue({ partner_id: 'partner_a', name: 'Partner A', contact_email: 'ops@a.example' })
    vi.mocked(api.partnerExport.listExports).mockResolvedValue([{
      export_id: 'a1', name: 'A daily', enabled: true,
      source: { layer: 'silver', dataset: 'silver.s.t' },
      format: { kind: 'csv' }, mapping: [],
      delivery: { method: 'sftp', partner_id: 'partner_a', connection_arn: 'arn:x', path_template: '/x' },
      schedule: { frequency: 'daily', empty_file_behavior: 'skip', retention_days: 30, alerts: { on_failure: true, on_partner_http_error: true, on_zero_rows: false, recipients: [] } },
    }])
    renderWithProviders(
      <Routes><Route path="/m/partner-export/partners/:partnerId" element={<PartnerDetail />} /></Routes>,
      { route: '/m/partner-export/partners/partner_a' },
    )
    await waitFor(() => expect(screen.getByRole('heading', { name: /partner a/i })).toBeInTheDocument())
    expect(screen.getByText('A daily')).toBeInTheDocument()
    expect(api.partnerExport.listExports).toHaveBeenCalledWith({ partner: 'partner_a' })
  })
})
