import type { ReactNode } from 'react'
import { describe, it, expect } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { PageHeader } from '../PageHeader'
import { PageTabsContext } from '../PageTabsContext'
import type { RouteTab } from '../RouteTabs'

const TABS: RouteTab[] = [
  { to: '/m/data-sync/syncs', label: 'Syncs', end: true },
  { to: '/m/data-sync/watermarks', label: 'Watermarks', end: true },
]

function renderWithTabs(ui: ReactNode, path = '/m/data-sync/syncs') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <PageTabsContext.Provider value={TABS}>{ui}</PageTabsContext.Provider>
    </MemoryRouter>,
  )
}

describe('PageHeader', () => {
  it('renders title, sub, actions and back', () => {
    render(
      <PageHeader
        title="Syncs"
        sub="Every sync in one place"
        actions={<button>New sync</button>}
        back={<a href="/">Back</a>}
      />,
    )
    expect(screen.getByRole('heading', { level: 1, name: 'Syncs' })).toBeInTheDocument()
    expect(screen.getByText('Every sync in one place')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'New sync' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Back' })).toBeInTheDocument()
  })

  it('shows skeleton bars and no heading while loading', () => {
    const { container } = render(<PageHeader loading />)
    expect(screen.queryByRole('heading')).toBeNull()
    expect(screen.getByRole('status')).toHaveTextContent('Loading')
    expect(container.querySelectorAll('[data-slot="skeleton"]')).toHaveLength(2)
  })

  it('omits the optional parts when not given', () => {
    const { container } = render(<PageHeader title="Only title" />)
    expect(container.querySelector('p')).toBeNull()
    expect(screen.queryByRole('button')).toBeNull()
    expect(screen.queryByRole('navigation')).toBeNull()
  })

  it('renders the section tabs from the context under the title, current tab marked', () => {
    renderWithTabs(<PageHeader title="Replicate" />)
    const nav = screen.getByRole('navigation', { name: 'Section tabs' })
    expect(within(nav).getByRole('link', { name: 'Syncs' })).toHaveAttribute('aria-current', 'page')
    expect(within(nav).getByRole('link', { name: 'Watermarks' })).not.toHaveAttribute('aria-current')
    const heading = screen.getByRole('heading', { level: 1, name: 'Replicate' })
    expect(heading.compareDocumentPosition(nav) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('renders the tabs in the loading state too', () => {
    renderWithTabs(<PageHeader loading />)
    expect(screen.getByRole('navigation', { name: 'Section tabs' })).toBeInTheDocument()
  })

  it('skips the tabs when tabs={false}', () => {
    renderWithTabs(<PageHeader title="Replicate" tabs={false} />)
    expect(screen.queryByRole('navigation', { name: 'Section tabs' })).toBeNull()
  })

  it('renders no tabs without a provider', () => {
    render(
      <MemoryRouter initialEntries={['/m/data-sync/syncs']}>
        <PageHeader title="Replicate" />
      </MemoryRouter>,
    )
    expect(screen.queryByRole('navigation', { name: 'Section tabs' })).toBeNull()
  })

  it('renders an InfoTip after the title when info is given', () => {
    render(<PageHeader title="Syncs" info="Oracle tables replicated into bronze and silver." />)
    expect(screen.getByRole('button', { name: 'About Syncs' })).toBeInTheDocument()
    expect(screen.getByRole('tooltip')).toHaveTextContent('Oracle tables replicated into bronze and silver.')
    expect(screen.getByRole('heading', { level: 1, name: 'Syncs' })).toBeInTheDocument()
  })

  it('labels the InfoTip generically when the title is not a string', () => {
    render(<PageHeader title={<span>Syncs</span>} info="x" />)
    expect(screen.getByRole('button', { name: 'About this page' })).toBeInTheDocument()
  })
})
