import { describe, it, expect } from 'vitest'
import { screen } from '@testing-library/react'
import { renderWithProviders } from '@/test/test-utils'
import { DeliveryMethodBadge } from '../DeliveryMethodBadge'

describe('DeliveryMethodBadge', () => {
  it.each([
    ['sftp', 'SFTP'],
    ['https', 'HTTPS'],
    ['s3', 'S3'],
    ['gcs', 'GCS'],
  ] as const)('renders %s as %s with the method-coded class', (method, label) => {
    renderWithProviders(<DeliveryMethodBadge method={method} />)
    const badge = screen.getByTestId(`delivery-badge-${method}`)
    expect(badge).toHaveTextContent(label)
    expect(badge.className).toMatch(new RegExp(`badge-${method}`))
  })

  it('falls back to the raw value for unknown methods', () => {
    // @ts-expect-error — exercising the defensive branch
    renderWithProviders(<DeliveryMethodBadge method="ftp" />)
    expect(screen.getByText(/FTP/i)).toBeInTheDocument()
  })
})
