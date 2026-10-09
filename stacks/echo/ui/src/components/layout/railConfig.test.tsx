import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { PageHeader, PageTabsContext } from '@/components/relay'
import { DATA_QUALITY_TABS, DATA_SYNC_TABS, INSIGHTS_TABS, pickTabs } from './railConfig'

describe('pickTabs', () => {
  const cases: [string, unknown][] = [
    ['/m/data-sync', INSIGHTS_TABS],
    ['/m/data-sync/', INSIGHTS_TABS],
    ['/m/data-sync/runs', INSIGHTS_TABS],
    ['/m/data-sync/runs/', INSIGHTS_TABS],
    ['/m/data-sync/upcoming', INSIGHTS_TABS],
    ['/m/data-sync/syncs', DATA_SYNC_TABS],
    ['/m/data-sync/syncs/', DATA_SYNC_TABS],
    ['/m/data-sync/watermarks', DATA_SYNC_TABS],
    ['/m/data-sync/sources', DATA_SYNC_TABS],
    ['/m/data-sync/upcoming/x', null],
    ['/m/data-sync/syncs/new', null],
    ['/m/data-sync/syncs/abc', null],
    ['/m/data-sync/syncs/abc/edit/schedule', null],
    ['/m/data-sync/gold/product', null],
    ['/m/data-quality', DATA_QUALITY_TABS],
    ['/m/data-quality/overview', DATA_QUALITY_TABS],
    ['/m/data-quality/checks', DATA_QUALITY_TABS],
    ['/m/data-quality/runs', DATA_QUALITY_TABS],
    ['/m/data-quality/kinds', DATA_QUALITY_TABS],
    ['/m/data-quality/checks/new', null],
    ['/m/data-quality/checks/abc', null],
    ['/', null],
    ['/m/catalog/browse', null],
    ['/m/partner-export/runs', null],
    ['/settings', null],
    ['/settings/connections', null],
  ]

  it.each(cases)('%s', (path, expected) => {
    expect(pickTabs(path)).toBe(expected)
  })

  it('lists the documented tab labels in order', () => {
    expect(DATA_SYNC_TABS.map((t) => t.label)).toEqual(['Syncs', 'Watermarks', 'Sources'])
    expect(INSIGHTS_TABS.map((t) => t.label)).toEqual(['Overview', 'Jobs', 'Coming up'])
    expect(INSIGHTS_TABS.map((t) => t.to)).toEqual(['/m/data-sync', '/m/data-sync/runs', '/m/data-sync/upcoming'])
    expect(DATA_QUALITY_TABS.map((t) => t.label)).toEqual(['Checks', 'Runs'])
  })
})

describe('section tabs in PageHeader', () => {
  function renderAt(path: string) {
    return render(
      <MemoryRouter initialEntries={[path]}>
        <PageTabsContext.Provider value={pickTabs(path)}>
          <PageHeader title="Page" />
        </PageTabsContext.Provider>
      </MemoryRouter>,
    )
  }

  it('marks only the tab for the current page', () => {
    renderAt('/m/data-sync/runs')
    expect(screen.getByRole('link', { name: 'Jobs' })).toHaveAttribute('aria-current', 'page')
    expect(screen.getByRole('link', { name: 'Overview' })).not.toHaveAttribute('aria-current')
    expect(screen.queryByRole('link', { name: 'Syncs' })).not.toBeInTheDocument()
  })

  it('Overview is current only at /m/data-sync itself, Coming up only at /upcoming', () => {
    const { unmount } = renderAt('/m/data-sync')
    expect(screen.getByRole('link', { name: 'Overview' })).toHaveAttribute('aria-current', 'page')
    expect(screen.getByRole('link', { name: 'Jobs' })).not.toHaveAttribute('aria-current')
    unmount()
    renderAt('/m/data-sync/upcoming')
    expect(screen.getByRole('link', { name: 'Coming up' })).toHaveAttribute('aria-current', 'page')
    expect(screen.getByRole('link', { name: 'Overview' })).not.toHaveAttribute('aria-current')
  })

  it('the Replicate pages carry no Job runs tab', () => {
    renderAt('/m/data-sync/syncs')
    expect(screen.getByRole('link', { name: 'Syncs' })).toHaveAttribute('aria-current', 'page')
    expect(screen.queryByRole('link', { name: /Job runs|Jobs/ })).not.toBeInTheDocument()
  })

  it('renders no tabs where the route has none', () => {
    renderAt('/m/catalog/browse')
    expect(screen.queryByRole('navigation', { name: 'Section tabs' })).not.toBeInTheDocument()
  })
})
