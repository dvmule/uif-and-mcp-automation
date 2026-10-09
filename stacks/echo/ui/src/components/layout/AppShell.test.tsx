import { beforeEach, describe, it, expect } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import { PageHeader } from '@/components/relay'
import { AppShell } from './AppShell'
import { CONSOLE_RAIL, SETTINGS_RAIL, pickRail } from './railConfig'
import { RAIL_KEY } from '@/lib/railPreference'

// Stand-in page: real pages draw the section tabs through their PageHeader.
function StubPage({ name }: { name: string }) {
  return (
    <>
      <PageHeader title={name} />
      <div>{name}-outlet</div>
    </>
  )
}

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/" element={<AppShell />}>
          <Route index element={<div>home-outlet</div>} />
          <Route path="m/data-sync/*" element={<StubPage name="ds" />} />
          <Route path="m/data-quality/*" element={<StubPage name="dq" />} />
          <Route path="m/catalog/*" element={<div>cat-outlet</div>} />
          <Route path="settings/*" element={<div>settings-outlet</div>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  )
}

function railLinks() {
  return within(screen.getByRole('navigation', { name: 'Echo menu' })).getAllByRole('link')
}

describe('AppShell', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('shows the console rail at /', () => {
    renderAt('/')
    expect(screen.getByText('home-outlet')).toBeInTheDocument()
    for (const name of ['Home', 'Replicate', 'Transform', 'Insights', 'Quality', 'Catalog']) {
      expect(screen.getByRole('link', { name })).toBeInTheDocument()
    }
    expect(screen.getByRole('link', { name: 'New replication' })).toHaveAttribute(
      'href',
      '/m/data-sync/syncs/new',
    )
    // UIFPE-679: Transform has its own landing page now, not the Syncs list.
    expect(screen.getByRole('link', { name: 'Transform' })).toHaveAttribute('href', '/m/data-sync/gold')
    expect(screen.getByRole('link', { name: 'Export · Beacon' })).toHaveAttribute(
      'href',
      '/m/beacon/index.html#/m/beacon',
    )
  })

  it('shows the Settings rail with Back to console at /settings/*', () => {
    renderAt('/settings/connections')
    const nav = screen.getByRole('navigation', { name: 'Settings menu' })
    expect(within(nav).getByRole('link', { name: 'Back to console' })).toBeInTheDocument()
    expect(within(nav).getByRole('link', { name: 'Connections' })).toHaveAttribute('aria-current', 'page')
    expect(within(nav).getByRole('link', { name: 'Notifications' })).toBeInTheDocument()
    expect(within(nav).getByRole('link', { name: 'Environments' })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Replicate' })).not.toBeInTheDocument()
  })

  it('shows section tabs on the Syncs list and none on a sync detail page', () => {
    const { unmount } = renderAt('/m/data-sync/syncs')
    const tabs = screen.getByRole('navigation', { name: 'Section tabs' })
    expect(within(tabs).getByRole('link', { name: 'Syncs' })).toHaveAttribute('aria-current', 'page')
    expect(within(tabs).getByRole('link', { name: 'Watermarks' })).toBeInTheDocument()
    expect(within(tabs).queryByRole('link', { name: 'Job runs' })).not.toBeInTheDocument()
    unmount()

    renderAt('/m/data-sync/syncs/abc')
    expect(screen.queryByRole('navigation', { name: 'Section tabs' })).not.toBeInTheDocument()
  })

  it('starts with a Skip to content link that targets the main region', async () => {
    renderAt('/')
    await userEvent.tab()
    const skip = screen.getByRole('link', { name: 'Skip to content' })
    expect(skip).toHaveFocus()
    expect(skip).toHaveAttribute('href', '#main')
    expect(screen.getByRole('main')).toHaveAttribute('id', 'main')
  })

  it('renders the Settings button in the header', () => {
    renderAt('/')
    expect(screen.getByRole('button', { name: /^settings$/i })).toBeInTheDocument()
  })

  it('renders the wordmark and a single read-only environment badge', () => {
    renderAt('/')
    expect(within(screen.getByRole('banner')).getByText('Echo')).toBeInTheDocument()
    expect(screen.queryByText(/Universal Integration Fabric/i)).not.toBeInTheDocument()
    expect(screen.getAllByLabelText(/^environment:/i)).toHaveLength(1)
    expect(screen.getByLabelText('Environment: DEV')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^uat$/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^prod$/i })).not.toBeInTheDocument()
  })

  it('has no Sandbox tag', () => {
    renderAt('/')
    expect(screen.queryByText(/sandbox/i)).not.toBeInTheDocument()
  })

  it('renders the footer with Terms, Privacy and copyright', () => {
    renderAt('/')
    const terms = screen.getByRole('link', { name: 'Terms' })
    expect(terms).toHaveAttribute('target', '_blank')
    expect(terms).toHaveAttribute('rel', 'noreferrer')
    expect(screen.getByRole('link', { name: 'Privacy' })).toHaveAttribute('target', '_blank')
    expect(
      screen.getByText(/Copyright © \d{4} Pearson Education Inc\. or its affiliate\(s\)\. All rights reserved\./),
    ).toBeInTheDocument()
  })

  it('collapse button toggles aria-expanded and remembers the choice', async () => {
    const user = userEvent.setup()
    renderAt('/')
    const button = screen.getByRole('button', { name: 'Collapse menu' })
    expect(button).toHaveAttribute('aria-expanded', 'true')

    await user.click(button)
    const expand = screen.getByRole('button', { name: 'Expand menu' })
    expect(expand).toHaveAttribute('aria-expanded', 'false')
    expect(localStorage.getItem(RAIL_KEY)).toBe('1')

    await user.click(expand)
    expect(screen.getByRole('button', { name: 'Collapse menu' })).toHaveAttribute('aria-expanded', 'true')
    expect(localStorage.getItem(RAIL_KEY)).toBe('0')
  })

  it('starts collapsed when the choice was remembered', () => {
    localStorage.setItem(RAIL_KEY, '1')
    renderAt('/')
    expect(screen.getByRole('button', { name: 'Expand menu' })).toHaveAttribute('aria-expanded', 'false')
  })

  it('marks exactly one rail link current on Jobs (Insights)', () => {
    renderAt('/m/data-sync/runs')
    const current = railLinks().filter((a) => a.getAttribute('aria-current') === 'page')
    expect(current.map((a) => a.textContent)).toEqual(['Insights'])
  })

  it('marks Insights current on Coming up and shows the Insights tabs', () => {
    renderAt('/m/data-sync/upcoming')
    const current = railLinks().filter((a) => a.getAttribute('aria-current') === 'page')
    expect(current.map((a) => a.textContent)).toEqual(['Insights'])
    const tabs = screen.getByRole('navigation', { name: 'Section tabs' })
    expect(within(tabs).getAllByRole('link').map((a) => a.textContent)).toEqual(['Overview', 'Jobs', 'Coming up'])
  })

  it('marks exactly one rail link current on Watermarks (Replicate)', () => {
    renderAt('/m/data-sync/watermarks')
    const current = railLinks().filter((a) => a.getAttribute('aria-current') === 'page')
    expect(current.map((a) => a.textContent)).toEqual(['Replicate'])
  })

  it('marks exactly one rail link current on the Transform landing and on a gold detail page', () => {
    const { unmount } = renderAt('/m/data-sync/gold')
    let current = railLinks().filter((a) => a.getAttribute('aria-current') === 'page')
    expect(current.map((a) => a.textContent)).toEqual(['Transform'])
    unmount()

    renderAt('/m/data-sync/gold/product')
    current = railLinks().filter((a) => a.getAttribute('aria-current') === 'page')
    expect(current.map((a) => a.textContent)).toEqual(['Transform'])
  })

  it('shows no section tabs on the Transform landing', () => {
    renderAt('/m/data-sync/gold')
    expect(screen.queryByRole('navigation', { name: 'Section tabs' })).not.toBeInTheDocument()
  })

  it('marks Home current only at /', () => {
    renderAt('/')
    const current = railLinks().filter((a) => a.getAttribute('aria-current') === 'page')
    expect(current.map((a) => a.textContent)).toEqual(['Home'])
  })

  it('pickRail chooses the Settings rail only under /settings', () => {
    expect(pickRail('/settings')).toBe(SETTINGS_RAIL)
    expect(pickRail('/settings/connections')).toBe(SETTINGS_RAIL)
    expect(pickRail('/')).toBe(CONSOLE_RAIL)
    expect(pickRail('/m/data-sync/syncs')).toBe(CONSOLE_RAIL)
  })
})
