import type { ReactNode } from 'react'
import { describe, it, expect } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { ObjectHeader } from '../ObjectHeader'
import { Pill } from '../Pill'

function renderHeader(ui: ReactNode) {
  return render(<MemoryRouter>{ui}</MemoryRouter>)
}

describe('ObjectHeader', () => {
  it('renders the back link, one h1 with the pills inside it, the meta line, actions and caption', () => {
    renderHeader(
      <ObjectHeader
        back={{ to: '/m/data-sync/syncs', label: 'Replicate' }}
        title="EDS.PRICE_LIST"
        pills={<Pill tone="accent">AWS</Pill>}
        meta="Oracle → bronze → silver · incremental"
        actions={<button type="button">Run now</button>}
        caption="Last 30 days · 17 runs"
      />,
    )
    expect(screen.getByRole('link', { name: 'Replicate' })).toHaveAttribute('href', '/m/data-sync/syncs')
    const headings = screen.getAllByRole('heading', { level: 1 })
    expect(headings).toHaveLength(1)
    expect(headings[0]).toHaveTextContent(/^EDS\.PRICE_LIST/)
    expect(within(headings[0]).getByText('AWS')).toBeInTheDocument()
    expect(screen.getByText('Oracle → bronze → silver · incremental')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Run now' })).toBeInTheDocument()
    expect(screen.getByText('Last 30 days · 17 runs')).toBeInTheDocument()
  })

  it('shows the back link and a status, but no heading, while loading', () => {
    renderHeader(<ObjectHeader back={{ to: '/m/data-sync/gold', label: 'Transform' }} loading />)
    expect(screen.getByRole('link', { name: 'Transform' })).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('Loading…')
    expect(screen.queryByRole('heading')).toBeNull()
  })

  it('never renders section tabs', () => {
    renderHeader(<ObjectHeader back={{ to: '/', label: 'Back' }} title="x" />)
    expect(screen.queryByRole('tablist')).toBeNull()
    expect(screen.queryByRole('navigation')).toBeNull()
  })
})
