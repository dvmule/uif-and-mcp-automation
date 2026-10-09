import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { OpenLink, RowActions, RowActionsCell } from '../RowActions'
import { CountLine, CountSegment } from '../CountLine'

describe('RowActions', () => {
  it('hides until the row is hovered or focused within', () => {
    render(
      <RowActions>
        <button type="button">Run now</button>
      </RowActions>,
    )
    const cls = screen.getByRole('button', { name: 'Run now' }).parentElement?.className ?? ''
    for (const c of ['opacity-0', 'group-hover:opacity-100', 'group-focus-within:opacity-100']) {
      expect(cls).toContain(c)
    }
  })

  it('OpenLink is labelled with the row name and links to the page', () => {
    render(
      <MemoryRouter>
        <OpenLink to="/m/data-sync/syncs/abc" name="orders" />
      </MemoryRouter>,
    )
    const link = screen.getByRole('link', { name: 'Open orders' })
    expect(link).toHaveAttribute('href', '/m/data-sync/syncs/abc')
    expect(link).toHaveAttribute('title', 'Open')
  })

  it('RowActionsCell puts the hover actions before the open icon', () => {
    render(
      <MemoryRouter>
        <RowActionsCell open={{ to: '/x', name: 'orders' }}>
          <button type="button">Run now</button>
        </RowActionsCell>
      </MemoryRouter>,
    )
    const button = screen.getByRole('button', { name: 'Run now' })
    const link = screen.getByRole('link', { name: 'Open orders' })
    expect(button.compareDocumentPosition(link) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(link.parentElement?.className).toContain('justify-end')
  })
})

describe('CountLine', () => {
  it('renders segments with test ids and a strong lead figure', () => {
    render(
      <CountLine>
        <CountSegment testid="stat-total" strong>
          13 syncs
        </CountSegment>
        {' · '}
        <CountSegment testid="stat-failing">1 failing</CountSegment>
      </CountLine>,
    )
    expect(screen.getByTestId('stat-total')).toHaveClass('font-semibold')
    expect(screen.getByTestId('stat-failing')).not.toHaveClass('font-semibold')
    expect(screen.getByTestId('stat-total').parentElement).toHaveTextContent('13 syncs · 1 failing')
  })
})
