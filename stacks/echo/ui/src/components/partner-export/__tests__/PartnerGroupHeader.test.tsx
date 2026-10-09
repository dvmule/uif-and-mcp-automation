import { describe, it, expect } from 'vitest'
import { screen } from '@testing-library/react'
import { renderWithProviders } from '@/test/test-utils'
import { PartnerGroupHeader } from '../PartnerGroupHeader'

const partner = {
  partner_id: 'partner_a',
  name: 'Partner A',
  contact_email: 'ops@partner-a.example',
  default_method: 'sftp' as const,
}

describe('PartnerGroupHeader', () => {
  it('renders name, export count, and contact', () => {
    renderWithProviders(<PartnerGroupHeader partner={partner} exportCount={3} aggregateHealth="healthy" />)
    expect(screen.getByText('Partner A')).toBeInTheDocument()
    expect(screen.getByText(/3 exports/i)).toBeInTheDocument()
    expect(screen.getByText(/ops@partner-a.example/i)).toBeInTheDocument()
  })

  it('uses the failing color class when aggregateHealth=failing', () => {
    renderWithProviders(<PartnerGroupHeader partner={partner} exportCount={2} aggregateHealth="failing" />)
    expect(screen.getByTestId('partner-group-header-partner_a').className).toMatch(/bg-red-50|border-red/)
  })

  it('uses the healthy color class when aggregateHealth=healthy', () => {
    renderWithProviders(<PartnerGroupHeader partner={partner} exportCount={2} aggregateHealth="healthy" />)
    expect(screen.getByTestId('partner-group-header-partner_a').className).not.toMatch(/bg-red-50/)
  })
})
