import { describe, it, expect, vi, beforeEach } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderWithProviders } from '@/test/test-utils'
import { Step3Delivery } from '../Step3Delivery'
import { usePartnerExportWizardStore } from '@/store/partnerExportWizardStore'
import { api } from '@/lib/api'

vi.mock('@/lib/api', () => ({
  api: { partnerExport: { listPartners: vi.fn(), createPartner: vi.fn() }, getConnections: vi.fn() },
}))

beforeEach(() => {
  usePartnerExportWizardStore.getState().reset()
  vi.mocked(api.partnerExport.listPartners).mockReset()
  vi.mocked(api.partnerExport.createPartner).mockReset()
  vi.mocked(api.getConnections).mockReset()
  vi.mocked(api.partnerExport.listPartners).mockResolvedValue([{ partner_id: 'partner_a', name: 'Partner A' }])
  vi.mocked(api.getConnections).mockResolvedValue([
    { name: 'sftp-prod', kind: 'sftp', arn: 'arn:sftp:1' } as any,
    { name: 'https-prod', kind: 'https', arn: 'arn:https:1' } as any,
    { name: 's3-prod', kind: 's3', arn: 'arn:s3:1' } as any,
  ])
})

describe('Step3Delivery', () => {
  it('writes the partner selection into the store', async () => {
    renderWithProviders(<Step3Delivery />)
    await screen.findByRole('option', { name: /partner a/i })
    await userEvent.selectOptions(screen.getByLabelText(/partner/i), 'partner_a')
    expect(usePartnerExportWizardStore.getState().delivery.partner_id).toBe('partner_a')
  })

  it('connection picker filters by chosen method', async () => {
    renderWithProviders(<Step3Delivery />)
    await screen.findByRole('option', { name: /partner a/i })
    await waitFor(() => expect(screen.getByRole('option', { name: /sftp-prod/i })).toBeInTheDocument())
    expect(screen.queryByRole('option', { name: /https-prod/i })).not.toBeInTheDocument()
    await userEvent.click(screen.getByLabelText(/https/i))
    await waitFor(() => expect(screen.getByRole('option', { name: /https-prod/i })).toBeInTheDocument())
  })

  it('path template input writes to store', async () => {
    renderWithProviders(<Step3Delivery />)
    const input = await screen.findByLabelText(/path template/i)
    await userEvent.clear(input)
    // userEvent.type interprets `{...}` as key sequences — escape braces by doubling.
    await userEvent.type(input, '/incoming/{{export_id}/{{YYYYMMDD}.csv')
    expect(usePartnerExportWizardStore.getState().delivery.path_template).toBe('/incoming/{export_id}/{YYYYMMDD}.csv')
  })

  it('inline + New partner creates a partner and selects it', async () => {
    vi.mocked(api.partnerExport.createPartner).mockResolvedValue({ partner_id: 'partner_b', name: 'Partner B' })
    renderWithProviders(<Step3Delivery />)
    await screen.findByRole('option', { name: /partner a/i })
    await userEvent.click(screen.getByRole('button', { name: /new partner/i }))
    await userEvent.type(screen.getByLabelText(/new partner name/i), 'Partner B')
    await userEvent.click(screen.getByRole('button', { name: /create/i }))
    await waitFor(() => expect(api.partnerExport.createPartner).toHaveBeenCalled())
  })
})
