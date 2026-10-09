import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, within } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { Overview } from '../Overview'

const hoursAgo = (h: number) => new Date(Date.now() - h * 3_600_000).toISOString()

const h = vi.hoisted(() => ({
  goldError: false,
  slow: false,
  statsLoading: false,
  fresh: 9,
  entities: 10,
  runsByPipeline: {} as Record<string, unknown>,
  goldRuns: [] as unknown[],
}))

vi.mock('@/hooks/usePipelines', () => ({
  usePipelines: () => ({
    data: [
      { pipeline_id: 'a', enabled: true, extract: { table: 'INV.A' }, schedule: { frequency: 'daily' } },
      { pipeline_id: 'b', enabled: true, extract: { table: 'INV.B' }, schedule: { frequency: 'daily' } },
    ],
    isLoading: false,
  }),
}))
vi.mock('@/hooks/useRuns', () => ({
  useRuns: (id: string) =>
    h.slow && id === 'b'
      ? { data: undefined, isLoading: true, isError: false }
      : { data: h.runsByPipeline[id], isLoading: false, isError: false },
}))
vi.mock('@/hooks/useGoldRuns', () => ({
  useGoldRuns: () =>
    h.goldError
      ? { data: undefined, isLoading: false, error: new Error('boom') }
      : { data: { items: h.goldRuns, truncated: false }, isLoading: false, error: null },
}))
vi.mock('@/hooks/useGoldPipelines', () => ({ useGoldPipelines: () => ({ data: [], isLoading: false }) }))
vi.mock('@/hooks/useGoldStats', () => ({
  useGoldStats: () =>
    h.statsLoading
      ? { data: undefined, isLoading: true, error: null }
      : {
          data: {
            trend_available: true,
            trend_generated_at: '2026-09-29T04:30:00Z',
            trend_age_minutes: 60,
            trend: {
              fleet: [{ day: '2026-09-28', entities: h.entities, runs: 4, failed: 0, rows_written: 10, fresh: h.fresh }],
            },
          },
          isLoading: false,
          error: null,
        },
}))

const run = (id: string, pid: string, status: string, ago: number, rows = 5) => ({
  run_id: id,
  pipeline_id: pid,
  job_name: 'uif-echo-dev-bronze-ingest',
  status,
  started_at: hoursAgo(ago),
  rows_read: 5,
  rows_written: rows,
})

function Probe() {
  const loc = useLocation()
  return <div data-testid="loc">{loc.pathname + loc.search}</div>
}

function renderAt(search = '') {
  return render(
    <MemoryRouter initialEntries={[`/m/data-sync${search}`]}>
      <Overview />
      <Probe />
    </MemoryRouter>,
  )
}

const FIGURES = ['figure-succeeded', 'figure-warning', 'figure-failed', 'figure-running', 'figure-upcoming']
const fig = (id: string) => screen.getByTestId(id)
/** The figure's number, which is a link to Jobs. */
const value = (id: string) => within(fig(id)).getByRole('link')
const jobsWindow = () => screen.getByRole('combobox', { name: 'Jobs window' })

describe('Insights › Overview', () => {
  beforeEach(() => {
    h.goldError = false
    h.slow = false
    h.statsLoading = false
    h.fresh = 9
    h.entities = 10
    h.runsByPipeline = {
      a: {
        items: [
          run('a-run', 'a', 'RUNNING', 0.1),
          run('a-ok', 'a', 'SUCCEEDED', 3),
          run('a-warn', 'a', 'WARNING', 4),
          run('a-fail', 'a', 'FAILED', 5),
          run('a-3d', 'a', 'SUCCEEDED', 72),
        ],
      },
      b: { items: [run('b-stuck', 'b', 'RUNNING', 40)] },
    }
    h.goldRuns = [
      { run_id: 'g-ok', gold_id: 'price', job_name: 'uif-echo-dev-gold-transform', status: 'SUCCEEDED', started_at: hoursAgo(1), rows_written: 0 },
      { run_id: 'g-fail', gold_id: 'price', job_name: 'uif-echo-dev-gold-transform', status: 'FAILED', started_at: hoursAgo(30) },
    ]
  })

  it('is Insights with its description behind the ⓘ, the Jobs card and five figures', () => {
    renderAt()
    expect(screen.getByRole('heading', { level: 1, name: 'Insights' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'About Insights' })).toBeInTheDocument()
    expect(screen.getByRole('tooltip')).toHaveTextContent(/computed from the runs loaded now/)
    expect(screen.queryByText(/Data Sync — Overview/)).not.toBeInTheDocument()
    expect(screen.queryByTestId('kpi-active')).not.toBeInTheDocument()
    expect(screen.queryByRole('group', { name: 'Window' })).not.toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 2, name: 'Jobs' })).toBeInTheDocument()
    for (const id of FIGURES) expect(fig(id)).toBeInTheDocument()
    for (const id of ['tile-succeeded', 'tile-warning', 'tile-failed', 'tile-running']) {
      expect(screen.queryByTestId(id)).not.toBeInTheDocument()
    }
  })

  it('counts the last 24 hours across replicate and gold, and says where each number comes from', () => {
    renderAt()
    expect(fig('figure-succeeded')).toHaveTextContent(/Succeeded24 hours/)
    expect(value('figure-succeeded')).toHaveTextContent(/^2$/)
    expect(fig('figure-succeeded')).toHaveTextContent('1 replicate · 1 gold')
    expect(fig('figure-warning')).toHaveTextContent('24 hours')
    expect(fig('figure-warning')).toHaveAttribute('title', 'A warning is a run that finished but tripped a quality gate, stopped early, or found an empty bronze.')
    expect(value('figure-warning')).toHaveTextContent(/^1$/)
    expect(value('figure-failed')).toHaveTextContent(/^1$/)
    expect(fig('figure-failed')).toHaveTextContent('1 replicate · 0 gold')
  })

  it('the Jobs window widens to 7 days, and keeps it in the URL', () => {
    renderAt()
    expect(jobsWindow()).toHaveValue('24h')
    fireEvent.change(jobsWindow(), { target: { value: '7d' } })
    expect(screen.getByTestId('loc').textContent).toBe('/m/data-sync?window=7d')
    expect(value('figure-succeeded')).toHaveTextContent(/^3$/)
    expect(value('figure-failed')).toHaveTextContent(/^2$/)
    expect(fig('figure-failed')).toHaveTextContent('1 replicate · 1 gold')
    expect(fig('figure-failed')).toHaveTextContent('7 days')
    fireEvent.change(jobsWindow(), { target: { value: '24h' } })
    expect(screen.getByTestId('loc').textContent).toBe('/m/data-sync')
  })

  it('?window=7d on load selects it', () => {
    renderAt('?window=7d')
    expect(jobsWindow()).toHaveValue('7d')
  })

  it('Running counts a run in flight even when it started before the window, and names up to two', () => {
    renderAt()
    expect(fig('figure-running')).toHaveTextContent(/Runningnow/)
    expect(value('figure-running')).toHaveTextContent(/^2$/)
    expect(fig('figure-running')).toHaveTextContent('INV.A, INV.B')
  })

  it('nothing running says so instead of showing a blank foot', () => {
    h.runsByPipeline = { a: { items: [run('a-ok', 'a', 'SUCCEEDED', 3)] }, b: { items: [] } }
    renderAt()
    expect(value('figure-running')).toHaveTextContent(/^0$/)
    expect(fig('figure-running')).toHaveTextContent('Nothing running')
  })

  it('Coming up counts the next 24 hours from the same projection as the Coming up tab', () => {
    renderAt()
    expect(fig('figure-upcoming')).toHaveTextContent('next 24 hours')
    expect(value('figure-upcoming')).toHaveTextContent(/^2$/)
    expect(value('figure-upcoming')).toHaveAttribute('href', '/m/data-sync/upcoming')
    expect(fig('figure-upcoming')).toHaveTextContent('2 syncs · 0 gold')
  })

  it('figures link to Jobs with the matching status and window', () => {
    renderAt('?window=7d')
    expect(value('figure-failed')).toHaveAttribute('href', '/m/data-sync/runs?status=FAILED&window=7d')
    expect(within(fig('figure-failed')).getByText('2').closest('a')).toHaveAttribute(
      'href',
      '/m/data-sync/runs?status=FAILED&window=7d',
    )
    expect(value('figure-running')).toHaveAttribute('href', '/m/data-sync/runs?status=RUNNING&window=all')
    expect(value('figure-succeeded')).toHaveAttribute('href', '/m/data-sync/runs?status=SUCCEEDED&window=7d')
  })

  it('24 hours links leave the window out', () => {
    renderAt()
    expect(value('figure-warning')).toHaveAttribute('href', '/m/data-sync/runs?status=WARNING')
  })

  it('shows skeletons, not zeros, while a sync is still loading', () => {
    h.slow = true
    renderAt()
    for (const id of ['figure-succeeded', 'figure-warning', 'figure-failed', 'figure-running']) {
      expect(fig(id).querySelector('[data-slot="skeleton"]')).not.toBeNull()
      expect(within(fig(id)).queryByRole('link')).toBeNull()
    }
    expect(fig('figure-failed')).not.toHaveTextContent(/replicate/)
  })

  it('says when gold runs are unavailable rather than counting gold as zero', () => {
    h.goldError = true
    renderAt()
    expect(screen.getByText(/Gold runs unavailable right now/)).toBeInTheDocument()
    expect(value('figure-succeeded')).toHaveTextContent(/^1$/)
  })

  it('Activity totals rows written and runs in the last 24 hours, one bar per hour', () => {
    renderAt()
    expect(screen.getByRole('heading', { level: 2, name: 'Activity' })).toBeInTheDocument()
    const rowsBlock = screen.getByTestId('activity-rows')
    expect(rowsBlock).toHaveTextContent(/Rows written20/)
    expect(rowsBlock).toHaveTextContent(/Peak 5 · \d\d:00/)
    expect(rowsBlock.querySelectorAll('rect')).toHaveLength(24)
    const runsBlock = screen.getByTestId('activity-runs')
    expect(runsBlock).toHaveTextContent(/Runs5/)
    expect(runsBlock).toHaveTextContent('2 succeeded · 1 warning · 1 failed')
    expect(within(runsBlock).getByText('1', { selector: 'b.text-signal-fail' })).toBeInTheDocument()
  })

  it('Activity has its own window: 7 days, seven daily bars', () => {
    renderAt()
    fireEvent.change(screen.getByRole('combobox', { name: 'Activity window' }), { target: { value: '7d' } })
    expect(screen.getByTestId('loc').textContent).toBe('/m/data-sync')
    const rowsBlock = screen.getByTestId('activity-rows')
    expect(rowsBlock.querySelectorAll('rect')).toHaveLength(7)
    expect(rowsBlock).toHaveTextContent(/per day/)
    expect(screen.getByTestId('activity-runs')).toHaveTextContent(/Runs8/)
  })

  it('Top tables puts the object with the most rows first', () => {
    h.goldRuns = [
      { run_id: 'g-ok', gold_id: 'price', job_name: 'uif-echo-dev-gold-transform', status: 'SUCCEEDED', started_at: hoursAgo(1), rows_written: 400 },
    ]
    renderAt()
    expect(screen.getByRole('heading', { level: 2, name: 'Top tables by rows' })).toBeInTheDocument()
    const links = within(screen.getByTestId('top-tables')).getAllByRole('link')
    expect(links.map((a) => a.textContent)).toEqual(['price', 'INV.A'])
    expect(links[0]).toHaveAttribute('href', '/m/data-sync/gold/price')
    expect(screen.getByText('400 rows')).toBeInTheDocument()
    expect(screen.getByText('15 rows')).toBeInTheDocument()
  })

  it('shows the gold trend card and the freshness share, with the honest line about silver', () => {
    renderAt()
    expect(screen.getByRole('heading', { name: /^Last \d+ days$/ })).toBeInTheDocument()
    expect(screen.getByTestId('freshness-share')).toHaveTextContent('90%')
    expect(screen.getByText(/of table-days inside their freshness target/)).toBeInTheDocument()
    expect(
      screen.getByText(/Silver and bronze trends need a fleet-level runs endpoint that does not exist yet/),
    ).toBeInTheDocument()
  })

  it('freshness share is loading, then never a fake 0%', () => {
    h.statsLoading = true
    renderAt()
    expect(screen.getByTestId('freshness-share-loading')).toBeInTheDocument()
    expect(screen.queryByText('0%')).not.toBeInTheDocument()
  })

  it('no entity-days means "not computed yet", not 0%', () => {
    h.entities = 0
    h.fresh = 0
    renderAt()
    expect(screen.getByTestId('freshness-share-missing')).toBeInTheDocument()
    expect(screen.queryByText('0%')).not.toBeInTheDocument()
  })
})
