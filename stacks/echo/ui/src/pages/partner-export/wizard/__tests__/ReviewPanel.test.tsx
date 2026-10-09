import { describe, it, expect, vi, beforeEach } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Routes, Route } from 'react-router-dom'
import { renderWithProviders } from '@/test/test-utils'
import { ReviewPanel } from '../ReviewPanel'
import { usePartnerExportWizardStore } from '@/store/partnerExportWizardStore'
import { api } from '@/lib/api'

vi.mock('@/lib/api', () => ({
  api: { partnerExport: { upsertExport: vi.fn() } },
}))

beforeEach(() => {
  usePartnerExportWizardStore.getState().reset()
  vi.mocked(api.partnerExport.upsertExport).mockReset()
  const st = usePartnerExportWizardStore.getState()
  st.patch({ exportName: 'Partner A daily CSV', source: { layer: 'silver', dataset: 'silver.s.t' } })
  st.patchDelivery({
    method: 'sftp', partner_id: 'partner_a', connection_arn: 'arn:x',
    path_template: '/{YYYYMMDD}.csv',
  })
})

describe('ReviewPanel', () => {
  it('renders a summary of the wizard state', () => {
    renderWithProviders(<ReviewPanel />)
    expect(screen.getByText(/partner a daily csv/i)).toBeInTheDocument()
    expect(screen.getByText('silver.s.t')).toBeInTheDocument()
    expect(screen.getAllByText(/partner_a/i).length).toBeGreaterThan(0)
  })

  it('Save calls upsertExport with the slugified export_id', async () => {
    vi.mocked(api.partnerExport.upsertExport).mockResolvedValue(undefined)
    renderWithProviders(
      <Routes><Route path="/" element={<ReviewPanel />} /><Route path="/m/partner-export/exports/:exportId" element={<div>landed</div>} /></Routes>,
    )
    await userEvent.click(screen.getByRole('button', { name: /save/i }))
    await waitFor(() => expect(api.partnerExport.upsertExport).toHaveBeenCalled())
    const [exportId, body] = vi.mocked(api.partnerExport.upsertExport).mock.calls[0]
    expect(exportId).toBe('partner_a_daily_csv')
    expect(body.export_id).toBe('partner_a_daily_csv')
    expect(body.delivery.partner_id).toBe('partner_a')
  })

  it('disables Save when required fields are missing', () => {
    usePartnerExportWizardStore.getState().reset()
    renderWithProviders(<ReviewPanel />)
    expect(screen.getByRole('button', { name: /save/i })).toBeDisabled()
  })
})
