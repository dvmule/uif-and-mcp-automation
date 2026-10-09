import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { LineageCard } from '../LineageCard'

function renderWithRouter(ui: React.ReactNode) {
  return render(<MemoryRouter>{ui}</MemoryRouter>)
}

describe('LineageCard', () => {
  it('renders upstream sync link', () => {
    renderWithRouter(
      <LineageCard
        upstream={{ sync_id: 'smoke_author', sync_name: 'smoke_author' }}
        downstream_exports={[]}
        downstream_checks={[]}
      />,
    )
    expect(screen.getByText('smoke_author')).toBeInTheDocument()
    expect(
      screen.getByRole('link', { name: /smoke_author/i }),
    ).toHaveAttribute('href', '/m/data-sync/syncs/smoke_author')
  })

  it('renders downstream exports and checks lists', () => {
    renderWithRouter(
      <LineageCard
        upstream={null}
        downstream_exports={[
          { export_id: 'ex_a', name: 'Partner A daily CSV' },
          { export_id: 'ex_b', name: 'Partner B JSON' },
        ]}
        downstream_checks={[
          { check_id: 'chk_null', name: 'Author null-rate' },
        ]}
      />,
    )
    expect(screen.getByText('Partner A daily CSV')).toBeInTheDocument()
    expect(screen.getByText('Partner B JSON')).toBeInTheDocument()
    expect(screen.getByText('Author null-rate')).toBeInTheDocument()

    const exportLink = screen.getByRole('link', { name: /Partner A daily CSV/i })
    expect(exportLink).toHaveAttribute('href', '/m/partner-export/exports/ex_a')

    const checkLink = screen.getByRole('link', { name: /Author null-rate/i })
    expect(checkLink).toHaveAttribute('href', '/m/data-quality/checks/chk_null')
  })

  it('shows empty-state copy when no downstream and no upstream', () => {
    renderWithRouter(
      <LineageCard upstream={null} downstream_exports={[]} downstream_checks={[]} />,
    )
    expect(screen.getByText(/no upstream sync recorded/i)).toBeInTheDocument()
    expect(screen.getByText(/no downstream consumers yet/i)).toBeInTheDocument()
  })
})
