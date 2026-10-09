import { describe, it, expect, vi, beforeEach } from 'vitest'
import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderWithProviders } from '@/test/test-utils'
import { NewExportWizard } from '../NewExportWizard'
import { usePartnerExportWizardStore } from '@/store/partnerExportWizardStore'

vi.mock('@/hooks/useCatalogDatasets', () => ({
  useCatalogDatasets: () => ({ data: [], isLoading: false }),
}))
vi.mock('@/hooks/usePartners', () => ({
  usePartners: () => ({ data: [], isLoading: false }),
  useCreatePartner: () => ({ mutateAsync: vi.fn(), isPending: false }),
}))
vi.mock('@/hooks/useConnections', () => ({ useConnections: () => ({ data: [], isLoading: false }) }))
vi.mock('@/hooks/usePartnerExports', () => ({
  useUpsertPartnerExport: () => ({ mutateAsync: vi.fn(), isPending: false, isError: false }),
}))

beforeEach(() => {
  usePartnerExportWizardStore.getState().reset()
})

describe('NewExportWizard shell', () => {
  it('renders the stepper with five steps', () => {
    renderWithProviders(<NewExportWizard />)
    expect(screen.getByText(/^Source$/)).toBeInTheDocument()
    expect(screen.getByText(/format & mapping/i)).toBeInTheDocument()
    expect(screen.getAllByText(/^Delivery$/).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/^Schedule$/).length).toBeGreaterThan(0)
    expect(screen.getByText(/^Review$/)).toBeInTheDocument()
  })

  it('Next / Back advance the step', async () => {
    renderWithProviders(<NewExportWizard />)
    expect(usePartnerExportWizardStore.getState().step).toBe(0)
    await userEvent.click(screen.getByRole('button', { name: /next/i }))
    expect(usePartnerExportWizardStore.getState().step).toBe(1)
    await userEvent.click(screen.getByRole('button', { name: /back/i }))
    expect(usePartnerExportWizardStore.getState().step).toBe(0)
  })

  it('renders the current step content', async () => {
    renderWithProviders(<NewExportWizard />)
    expect(screen.getByTestId('wizard-step-0')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /next/i }))
    expect(screen.getByTestId('wizard-step-1')).toBeInTheDocument()
  })
})
