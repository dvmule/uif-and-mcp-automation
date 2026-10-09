import { describe, it, expect, beforeEach } from 'vitest'
import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderWithProviders } from '@/test/test-utils'
import { Step4Schedule } from '../Step4Schedule'
import { usePartnerExportWizardStore } from '@/store/partnerExportWizardStore'

beforeEach(() => {
  usePartnerExportWizardStore.getState().reset()
})

describe('Step4Schedule', () => {
  it('changing frequency writes to the store', async () => {
    renderWithProviders(<Step4Schedule />)
    await userEvent.selectOptions(screen.getByLabelText(/frequency/i), 'hourly')
    expect(usePartnerExportWizardStore.getState().schedule.frequency).toBe('hourly')
  })

  it('cron frequency shows the cron expression field', async () => {
    renderWithProviders(<Step4Schedule />)
    await userEvent.selectOptions(screen.getByLabelText(/frequency/i), 'cron')
    expect(screen.getByLabelText(/cron expression/i)).toBeInTheDocument()
  })

  it('alert toggles flip individually', async () => {
    renderWithProviders(<Step4Schedule />)
    const zero = screen.getByLabelText(/zero[- ]row/i)
    expect(zero).not.toBeChecked()
    await userEvent.click(zero)
    expect(usePartnerExportWizardStore.getState().schedule.alerts.on_zero_rows).toBe(true)
  })

  it('adding a recipient writes to alerts.recipients', async () => {
    renderWithProviders(<Step4Schedule />)
    await userEvent.type(screen.getByLabelText(/add recipient/i), 'ops@x.example')
    await userEvent.click(screen.getByRole('button', { name: /add$/i }))
    expect(usePartnerExportWizardStore.getState().schedule.alerts.recipients).toContain('ops@x.example')
  })

  it('removing a recipient removes it from the store', async () => {
    usePartnerExportWizardStore.getState().patchAlerts({ recipients: ['a@x', 'b@x'] })
    renderWithProviders(<Step4Schedule />)
    await userEvent.click(screen.getByLabelText('Remove a@x'))
    expect(usePartnerExportWizardStore.getState().schedule.alerts.recipients).toEqual(['b@x'])
  })
})
