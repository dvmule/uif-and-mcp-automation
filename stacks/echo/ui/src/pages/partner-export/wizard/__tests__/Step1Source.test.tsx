import { describe, it, expect, vi, beforeEach } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderWithProviders } from '@/test/test-utils'
import { Step1Source } from '../Step1Source'
import { usePartnerExportWizardStore } from '@/store/partnerExportWizardStore'

vi.mock('@/hooks/useCatalogDatasets', () => ({
  useCatalogDatasets: (layer: 'silver' | 'gold') => ({
    data: (layer === 'silver'
      ? [
          { dataset: 'silver.s.t',  layer: 'silver', cloud: 'aws', database: 's', table: 't' },
          { dataset: 'silver.s.t2', layer: 'silver', cloud: 'aws', database: 's', table: 't2' },
        ]
      : [
          { dataset: 'gold.g.t3', layer: 'gold', cloud: 'aws', database: 'g', table: 't3' },
        ]),
    isLoading: false,
  }),
}))

beforeEach(() => {
  usePartnerExportWizardStore.getState().reset()
})

describe('Step1Source', () => {
  it('lists only silver datasets by default', () => {
    renderWithProviders(<Step1Source />)
    expect(screen.getByRole('option', { name: 'silver.s.t' })).toBeInTheDocument()
    expect(screen.queryByRole('option', { name: 'gold.g.t3' })).not.toBeInTheDocument()
  })

  it('switches to gold when the layer radio is changed', async () => {
    renderWithProviders(<Step1Source />)
    await userEvent.click(screen.getByLabelText(/gold/i))
    await waitFor(() => expect(screen.getByRole('option', { name: 'gold.g.t3' })).toBeInTheDocument())
  })

  it('writes the picked dataset to the store', async () => {
    renderWithProviders(<Step1Source />)
    await userEvent.selectOptions(screen.getByLabelText('Dataset'), 'silver.s.t2')
    expect(usePartnerExportWizardStore.getState().source).toEqual({ layer: 'silver', dataset: 'silver.s.t2' })
  })

  it('writes the export name', async () => {
    renderWithProviders(<Step1Source />)
    await userEvent.type(screen.getByLabelText(/export name/i), 'Partner A daily')
    expect(usePartnerExportWizardStore.getState().exportName).toBe('Partner A daily')
  })
})
