import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { Home } from './Home'
import type { ActivityItem } from '@/lib/types'

vi.mock('@/hooks/useHomeSummary', () => ({ useHomeSummary: vi.fn() }))
vi.mock('@/hooks/useRecentActivity', () => ({ useRecentActivity: vi.fn() }))
vi.mock('@/hooks/useGoldStats', () => ({ useGoldStats: vi.fn() }))

import { useHomeSummary } from '@/hooks/useHomeSummary'
import { useRecentActivity } from '@/hooks/useRecentActivity'
import { useGoldStats } from '@/hooks/useGoldStats'

const SUMMARY = {
  syncs: { ok: 3, fail: 1, in_24h: 4 },
  exports: { ok: 9, fail: 9, in_24h: 18 },
  dq: { ok: 2, fail: 1, in_24h: 3 },
  window_hours: 24,
  generated_at: '2026-09-29T14:25:00Z',
}

const GOLD = {
  live: {
    as_of: '2026-09-29T14:25:00Z',
    window_hours: 24,
    entities: 5,
    enabled: 5,
    runs: 4,
    succeeded: 3,
    failed: 1,
    skipped: 0,
    rows_written: 100,
    fresh: 3,
    stale: 1,
    unknown: 1,
    stale_entities: ['stock'],
  },
}

function item(over: Partial<ActivityItem>): ActivityItem {
  return {
    ts: '2026-09-29T11:59:00Z',
    module: 'sync',
    subject: 'EDS.PRICE_LIST',
    status: 'SUCCEEDED',
    message: '1284 rows',
    link_path: '/m/data-sync/syncs/eds_price_list',
    ...over,
  }
}

interface Setup {
  summary?: Record<string, unknown>
  activity?: Record<string, unknown>
  gold?: Record<string, unknown>
}

function setup({ summary, activity, gold }: Setup = {}) {
  const refetch = { summary: vi.fn(), activity: vi.fn(), gold: vi.fn() }
  vi.mocked(useHomeSummary).mockReturnValue({
    data: SUMMARY,
    isLoading: false,
    isError: false,
    refetch: refetch.summary,
    ...summary,
  } as unknown as ReturnType<typeof useHomeSummary>)
  vi.mocked(useRecentActivity).mockReturnValue({
    data: { items: [], count: 0 },
    isLoading: false,
    isError: false,
    refetch: refetch.activity,
    ...activity,
  } as unknown as ReturnType<typeof useRecentActivity>)
  vi.mocked(useGoldStats).mockReturnValue({
    data: GOLD,
    isLoading: false,
    isError: false,
    refetch: refetch.gold,
    ...gold,
  } as unknown as ReturnType<typeof useGoldStats>)
  render(
    <MemoryRouter>
      <Home />
    </MemoryRouter>,
  )
  return refetch
}

const tile = (id: string) => screen.getByTestId(id)

describe('Home', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('greets the signed-in user and states the window and update time', () => {
    setup()
    expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1)
    expect(screen.getByRole('heading', { level: 1, name: 'Welcome, test.user.' })).toBeInTheDocument()
    expect(screen.getByText('Health · last 24 hours · updated 14:25 UTC')).toBeInTheDocument()
    expect(screen.getByText('Health of every sync and gold table over the last 24 hours.')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'New transformation' })).toHaveAttribute(
      'href',
      '/m/transform/index.html#/transform/new',
    )
    expect(screen.getByRole('link', { name: 'New replication' })).toHaveAttribute(
      'href',
      '/m/data-sync/syncs/new',
    )
  })

  it('offers 24 hours as the only range and shows the pipeline drawing', () => {
    setup()
    const range = screen.getByRole('group', { name: 'Time range' })
    expect(within(range).getByRole('button', { name: '24 hours' })).toHaveAttribute('aria-pressed', 'true')
    for (const name of ['7 days', '30 days']) {
      const b = within(range).getByRole('button', { name })
      expect(b).toBeDisabled()
      expect(b).toHaveAttribute('title', 'Not available here yet')
    }
    expect(screen.getByRole('img', { name: /Source tables flow/ })).toBeInTheDocument()
  })

  it('shows the four figures with their numbers, hints and links', () => {
    setup()
    expect(tile('tile-syncs')).toHaveTextContent('Syncs healthy')
    expect(tile('tile-syncs')).toHaveTextContent('3 of 4')
    expect(tile('tile-syncs')).toHaveTextContent('1 failed in the window')
    expect(tile('tile-gold-fresh')).toHaveTextContent('Gold tables fresh')
    expect(tile('tile-gold-fresh')).toHaveTextContent('3 of 5')
    expect(tile('tile-gold-fresh')).toHaveTextContent('Behind: stock')
    expect(tile('tile-gold-failed')).toHaveTextContent('Gold runs failed')
    expect(tile('tile-gold-failed')).toHaveTextContent('1')
    expect(tile('tile-gold-failed')).toHaveTextContent('of 4 runs')
    expect(within(tile('tile-gold-failed')).getByText('1')).toHaveClass('text-signal-fail')
    expect(tile('tile-dq-failed')).toHaveTextContent('Quality checks failing')
    expect(tile('tile-dq-failed')).toHaveTextContent('3 ran')
    expect(within(tile('tile-dq-failed')).getByText('1')).toHaveClass('text-signal-fail')
    expect(tile('tile-syncs').closest('a')).toHaveAttribute('href', '/m/data-sync/syncs')
    expect(tile('tile-gold-fresh').closest('a')).toHaveAttribute('href', '/m/data-sync/gold')
    expect(tile('tile-gold-failed').closest('a')).toHaveAttribute('href', '/m/data-sync/gold')
    expect(tile('tile-dq-failed').closest('a')).toHaveAttribute('href', '/m/data-quality/checks')
  })

  it('says "none failed" and "all inside target" when nothing is wrong', () => {
    setup({
      summary: { data: { ...SUMMARY, syncs: { ok: 4, fail: 0, in_24h: 4 } } },
      gold: { data: { live: { ...GOLD.live, stale_entities: [], failed: 0 } } },
    })
    expect(tile('tile-syncs')).toHaveTextContent('none failed')
    expect(tile('tile-gold-fresh')).toHaveTextContent('all inside target')
  })

  it('does not show the legacy export numbers; it links to Beacon as a full page load', () => {
    setup()
    expect(screen.queryByText('18')).not.toBeInTheDocument()
    expect(screen.getByText(/Exports live in Beacon/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Open Beacon' })).toHaveAttribute(
      'href',
      '/m/beacon/index.html#/m/beacon',
    )
  })

  it('says "Not available here" on the two gold tiles when the gold call fails', () => {
    setup({ gold: { data: undefined, isError: true } })
    expect(tile('tile-gold-fresh')).toHaveTextContent('Not available here')
    expect(tile('tile-gold-failed')).toHaveTextContent('Not available here')
    expect(tile('tile-syncs')).toHaveTextContent('3 of 4')
    expect(screen.getByText('Gold freshness unavailable right now.')).toBeInTheDocument()
    expect(screen.queryByText("Couldn't reach the API.")).not.toBeInTheDocument()
  })

  it('shows skeletons, not numbers, while loading', () => {
    setup({
      summary: { data: undefined, isLoading: true },
      activity: { data: undefined, isLoading: true },
      gold: { data: undefined, isLoading: true },
    })
    for (const id of ['tile-syncs', 'tile-gold-fresh', 'tile-gold-failed', 'tile-dq-failed']) {
      expect(tile(id).querySelector('[data-slot="skeleton"]')).not.toBeNull()
      expect(tile(id)).not.toHaveTextContent('Not available here')
    }
    expect(screen.getByTestId('attention-loading')).toBeInTheDocument()
    expect(screen.queryByText('Nothing needs you right now')).not.toBeInTheDocument()
    expect(screen.queryByText(/updated/)).not.toBeInTheDocument()
  })

  it('shows one Retry note and unavailable tiles when the summary errors, and Retry refetches all three', async () => {
    const user = userEvent.setup()
    const refetch = setup({ summary: { data: undefined, isError: true } })
    expect(screen.getByText("Couldn't reach the API.")).toBeInTheDocument()
    expect(tile('tile-syncs')).toHaveTextContent('Not available here')
    expect(tile('tile-dq-failed')).toHaveTextContent('Not available here')
    expect(tile('tile-gold-fresh')).toHaveTextContent('3 of 5')
    await user.click(screen.getByRole('button', { name: 'Retry' }))
    expect(refetch.summary).toHaveBeenCalledTimes(1)
    expect(refetch.activity).toHaveBeenCalledTimes(1)
    expect(refetch.gold).toHaveBeenCalledTimes(1)
  })

  it('treats a summary without syncs or dq as unreachable', () => {
    setup({ summary: { data: { foo: 'bar' } } })
    expect(screen.getByText("Couldn't reach the API.")).toBeInTheDocument()
    expect(tile('tile-syncs')).toHaveTextContent('Not available here')
  })

  describe('Needs attention', () => {
    it('lists a failed subject once at its newest item, plus a stale gold table', () => {
      setup({
        activity: {
          data: {
            count: 3,
            items: [
              item({ status: 'FAILED', ts: '2026-09-29T11:59:00Z', message: 'ORA-01017 logon denied' }),
              item({ status: 'FAILED', ts: '2026-09-29T09:00:00Z', message: 'older failure' }),
              item({ subject: 'FIPM.X', status: 'SUCCEEDED', link_path: '/m/data-sync/syncs/fipm_x' }),
            ],
          },
        },
      })
      const card = screen.getByRole('region', { name: 'Needs attention' })
      expect(within(card).getByText('2 items')).toBeInTheDocument()
      expect(within(card).getAllByRole('link', { name: 'EDS.PRICE_LIST' })).toHaveLength(1)
      expect(within(card).getByText(/ORA-01017 logon denied/)).toBeInTheDocument()
      expect(within(card).queryByText(/older failure/)).not.toBeInTheDocument()
      expect(within(card).queryByText('FIPM.X')).not.toBeInTheDocument()
      expect(within(card).getByRole('link', { name: 'EDS.PRICE_LIST' })).toHaveAttribute(
        'href',
        '/m/data-sync/syncs/eds_price_list',
      )
      expect(within(card).getByText('Failed')).toBeInTheDocument()
      // The stale gold table
      expect(within(card).getByRole('link', { name: 'stock' })).toHaveAttribute('href', '/m/data-sync/gold')
      expect(within(card).getByText('Stale')).toBeInTheDocument()
      expect(within(card).getByText(/no successful run inside its cadence/)).toBeInTheDocument()
      // Each row is a note whose tone follows the object: fail for the job, warn for the stale table
      expect(within(card).getByRole('link', { name: 'EDS.PRICE_LIST' }).closest('.bg-signal-fail-bg')).not.toBeNull()
      expect(within(card).getByRole('link', { name: 'stock' }).closest('.bg-signal-warn-bg')).not.toBeNull()
      expect(within(card).getByRole('link', { name: 'Open stock' })).toHaveAttribute('href', '/m/data-sync/gold')
    })

    it('shows the empty state with the check time when nothing needs attention', () => {
      setup({
        gold: { data: { live: { ...GOLD.live, stale_entities: [] } } },
        activity: { data: { count: 1, items: [item({})] } },
      })
      expect(screen.getByText('Nothing needs you right now')).toBeInTheDocument()
      expect(
        screen.getByText(/All syncs and gold tables ran as expected in the last 24 hours · checked 14:25 UTC/),
      ).toBeInTheDocument()
    })

    it('does not claim all is well when the activity feed failed', () => {
      setup({ activity: { data: undefined, isError: true }, gold: { data: { live: { ...GOLD.live, stale_entities: [] } } } })
      expect(screen.queryByText('Nothing needs you right now')).not.toBeInTheDocument()
      expect(screen.getByText(/Couldn't read recent activity/)).toBeInTheDocument()
    })

    it('still lists failed jobs when only the gold call failed', () => {
      setup({
        activity: { data: { count: 1, items: [item({ status: 'FAILED' })] } },
        gold: { data: undefined, isError: true },
      })
      const card = screen.getByRole('region', { name: 'Needs attention' })
      expect(within(card).getByRole('link', { name: 'EDS.PRICE_LIST' })).toBeInTheDocument()
      expect(within(card).getByText('Gold freshness unavailable right now.')).toBeInTheDocument()
    })
  })

  describe('Recent jobs', () => {
    it('shows rows with area names, Passed for a quality success, and the All jobs link', () => {
      setup({
        activity: {
          data: {
            count: 3,
            items: [
              item({ subject: 'reconcile · FIPM.X', module: 'dq', status: 'SUCCEEDED', message: 'variance 0', link_path: '/m/data-quality/checks/x' }),
              item({ subject: 'FIPM.X', module: 'sync', status: 'SUCCEEDED' }),
              item({ subject: 'partner_a', module: 'export', status: 'FAILED', message: 'gcs denied', link_path: '/m/partner-export/x' }),
            ],
          },
        },
      })
      const card = screen.getByRole('region', { name: 'Recent jobs' })
      const rows = within(card).getAllByRole('row')
      // header + three items
      expect(rows).toHaveLength(4)
      const dq = rows[1]
      expect(within(dq).getByText('Quality')).toBeInTheDocument()
      expect(within(dq).getByText('Passed')).toBeInTheDocument()
      expect(within(dq).getByRole('link', { name: 'reconcile · FIPM.X' })).toHaveAttribute(
        'href',
        '/m/data-quality/checks/x',
      )
      const sync = rows[2]
      expect(within(sync).getByText('Replicate')).toBeInTheDocument()
      expect(within(sync).getByText('Succeeded')).toBeInTheDocument()
      const exp = rows[3]
      expect(within(exp).getByText('Export')).toBeInTheDocument()
      expect(within(exp).getByText('Failed')).toBeInTheDocument()
      expect(within(card).getByRole('link', { name: 'All jobs →' })).toHaveAttribute('href', '/m/data-sync/runs')
    })

    it('says so when there is no activity in the window', () => {
      setup()
      expect(screen.getByText('No activity in the last 24 hours')).toBeInTheDocument()
    })

    it('shows a note inside the card when the feed errors', () => {
      setup({ activity: { data: undefined, isError: true } })
      const card = screen.getByRole('region', { name: 'Recent jobs' })
      expect(within(card).getByText("Couldn't load recent jobs.")).toBeInTheDocument()
    })
  })
})
