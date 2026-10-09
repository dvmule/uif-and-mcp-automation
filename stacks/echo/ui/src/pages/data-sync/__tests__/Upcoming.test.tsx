import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, within } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { Upcoming } from '../Upcoming'

const NOW = new Date('2026-09-29T13:55:00Z')

const h = vi.hoisted(() => ({
  pipelines: [] as unknown[],
  golds: [] as unknown[],
  runsByPipeline: {} as Record<string, unknown>,
  goldRuns: [] as unknown[],
  goldsLoading: false,
}))

vi.mock('@/hooks/usePipelines', () => ({
  usePipelines: () => ({ data: h.pipelines, isLoading: false }),
}))
vi.mock('@/hooks/useRuns', () => ({
  useRuns: (id: string) => ({ data: h.runsByPipeline[id], isLoading: false, isError: false }),
}))
vi.mock('@/hooks/useGoldRuns', () => ({
  useGoldRuns: () => ({ data: { items: h.goldRuns, truncated: false }, isLoading: false, error: null }),
}))
vi.mock('@/hooks/useGoldPipelines', () => ({
  useGoldPipelines: () => ({ data: h.golds, isLoading: h.goldsLoading }),
}))

const pipe = (id: string, table: string, schedule: Record<string, unknown>, extra: Record<string, unknown> = {}) => ({
  pipeline_id: id,
  enabled: true,
  extract: { table },
  schedule,
  ...extra,
})

function Probe() {
  const loc = useLocation()
  return <div data-testid="loc">{loc.pathname + loc.search}</div>
}

function renderAt(search = '') {
  return render(
    <MemoryRouter initialEntries={[`/m/data-sync/upcoming${search}`]}>
      <Upcoming />
      <Probe />
    </MemoryRouter>,
  )
}

const bodyRows = () => screen.getAllByRole('row').slice(1)

describe('Insights › Coming up', () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(NOW)
    h.goldsLoading = false
    h.pipelines = [
      pipe('hourly', 'OM.STOCK_POSITION', { frequency: 'hourly' }),
      pipe('off', 'SIS.STUDENT', { frequency: 'daily', daily_slot: 0 }, { enabled: false }),
      pipe('daily6', 'FIPM.X', { frequency: 'daily', daily_slot: 6 }, { silver: { enabled: true } }),
      pipe('rec', 'REC.ONLY', { frequency: 'reconcile' }),
    ]
    h.golds = [
      { gold_id: 'product', enabled: true, schedule: { frequency: 'gold-24h', start_time: '06:30' } },
      { gold_id: 'customer', enabled: true, schedule: { frequency: 'gold-weekly', start_time: '06:30', weekly_days: ['MON'] } },
    ]
    h.runsByPipeline = {
      hourly: { items: [{ run_id: 'h1', job_name: 'x-bronze-ingest', status: 'RUNNING', started_at: '2026-09-29T13:55:00Z' }] },
      daily6: { items: [{ run_id: 'd1', job_name: 'x-bronze-ingest', status: 'FAILED', started_at: '2026-09-29T08:31:00Z' }] },
    }
    h.goldRuns = [
      { run_id: 'g1', gold_id: 'product', job_name: 'uif-echo-dev-gold-transform', status: 'SUCCEEDED', started_at: '2026-09-29T06:31:00Z', rows_written: 3 },
    ]
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('has the title, the description behind the info tip, the zone in the toolbar, and the projection note', () => {
    renderAt()
    expect(screen.getByRole('heading', { level: 1, name: 'Coming up' })).toBeInTheDocument()
    expect(screen.getByRole('tooltip')).toHaveTextContent(/in your zone and UTC/)
    expect(screen.getByText(/^times in your zone \(.+\) and UTC$/)).toBeInTheDocument()
    expect(screen.queryByRole('group', { name: 'Window' })).not.toBeInTheDocument()
    expect(screen.getByText(/Projected from each table's schedule, not read from the scheduler/)).toBeInTheDocument()
    expect(screen.getByText(/Reconcile checks run on demand and are not listed/)).toBeInTheDocument()
  })

  it('lists tables soonest first; reconcile is not listed', () => {
    renderAt()
    const rows = bodyRows()
    const names = rows.map((r) => within(r).getAllByRole('link')[0].textContent)
    expect(names).toEqual(['OM.STOCK_POSITION', 'SIS.STUDENT', 'product', 'FIPM.X'])
    expect(screen.getByRole('table', { name: 'Upcoming runs' })).toBeInTheDocument()
    expect(screen.queryByText('REC.ONLY')).not.toBeInTheDocument()
    expect(rows[0].textContent).toContain('14:00 UTC')
    expect(rows[0].textContent).toContain('in 5 min')
    expect(rows[1].textContent).toContain('Tomorrow 05:30 UTC')
  })

  it('links to the sync or the gold table', () => {
    renderAt()
    expect(screen.getByRole('link', { name: 'OM.STOCK_POSITION' })).toHaveAttribute('href', '/m/data-sync/syncs/hourly')
    expect(screen.getByRole('link', { name: 'product' })).toHaveAttribute('href', '/m/data-sync/gold/product')
    expect(screen.getByRole('link', { name: 'Open OM.STOCK_POSITION' })).toHaveAttribute('href', '/m/data-sync/syncs/hourly')
    expect(screen.getByRole('link', { name: 'Open product' })).toHaveAttribute('href', '/m/data-sync/gold/product')
  })

  it('the count line says how many runs are expected, split into syncs and gold', () => {
    renderAt()
    expect(screen.getByTestId('upcoming-count')).toHaveTextContent('4 runs expected in the next 24 hours · 3 syncs · 1 gold')
    expect(screen.getByTestId('upcoming-count')).not.toHaveTextContent('filtered')
  })

  it('Find narrows by object in the browser and says what it was filtered from', () => {
    renderAt()
    fireEvent.change(screen.getByRole('searchbox', { name: 'Search objects' }), { target: { value: 'stock' } })
    expect(bodyRows()).toHaveLength(1)
    expect(bodyRows()[0].textContent).toContain('OM.STOCK_POSITION')
    expect(screen.getByTestId('upcoming-count')).toHaveTextContent('1 run expected in the next 24 hours · 1 sync · 0 gold · filtered from 4')
    expect(screen.getByTestId('loc').textContent).toBe('/m/data-sync/upcoming')
  })

  it('the Layer facet narrows to one layer', () => {
    renderAt()
    const layer = screen.getByRole('button', { name: 'Filter by layer' })
    expect(layer).toHaveTextContent('All layers (3)')
    fireEvent.click(layer)
    fireEvent.click(screen.getByRole('option', { name: /^Gold/ }))
    const rows = bodyRows()
    expect(rows).toHaveLength(1)
    expect(rows[0].textContent).toContain('product')
    expect(screen.getByTestId('upcoming-count')).toHaveTextContent('· 0 syncs · 1 gold · filtered from 4')
  })

  it('a search that matches nothing says so and offers Clear all', () => {
    renderAt()
    fireEvent.change(screen.getByRole('searchbox', { name: 'Search objects' }), { target: { value: 'nothing' } })
    expect(screen.getByText('No tables match')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Clear all' }))
    expect(bodyRows()).toHaveLength(4)
  })

  it('shows layer and cadence in words', () => {
    renderAt()
    const rows = bodyRows()
    expect(rows[0].textContent).toContain('Bronze')
    expect(rows[0].textContent).toContain('hourly')
    expect(rows[3].textContent).toContain('Bronze + Silver')
    expect(rows[3].textContent).toMatch(/daily · 08:30 UTC/)
    expect(rows[2].textContent).toContain('Gold')
  })

  it('shows the last run with its status, and a dash where there is none', () => {
    renderAt()
    const rows = bodyRows()
    expect(within(rows[0]).getByText('Running')).toBeInTheDocument()
    expect(within(rows[2]).getByText('Succeeded')).toBeInTheDocument()
    expect(within(rows[3]).getByText('Failed')).toBeInTheDocument()
    expect(within(rows[1]).getByText('—')).toBeInTheDocument()
  })

  it('a disabled table is listed and marked as skipped, not hidden', () => {
    renderAt()
    const row = bodyRows()[1]
    expect(within(row).getByText('Disabled')).toBeInTheDocument()
    expect(row.textContent).toContain('skipped while disabled')
  })

  it('7 d brings in the weekly gold table, with its day, and keeps the choice in the URL', () => {
    renderAt()
    expect(screen.queryByRole('link', { name: 'customer' })).not.toBeInTheDocument()
    const window = screen.getByRole('combobox', { name: 'Window' })
    expect(window).toHaveValue('24h')
    expect(screen.getByText('Window').tagName).toBe('LABEL')
    fireEvent.change(window, { target: { value: '7d' } })
    expect(screen.getByTestId('loc').textContent).toBe('/m/data-sync/upcoming?window=7d')
    const row = bodyRows().find((r) => r.textContent?.includes('customer'))!
    expect(row.textContent).toContain('weekly · Mon (UTC)')
    expect(row.textContent).toContain('Mon 06:30 UTC')
    expect(screen.getByTestId('upcoming-count')).toHaveTextContent(/in the next 7 days/)
    fireEvent.change(window, { target: { value: '24h' } })
    expect(screen.getByTestId('loc').textContent).toBe('/m/data-sync/upcoming')
  })

  it('?window=7d on load picks Next 7 days', () => {
    renderAt('?window=7d')
    expect(screen.getByRole('combobox', { name: 'Window' })).toHaveValue('7d')
  })

  it('nothing to show says so, with the window', () => {
    h.pipelines = []
    h.golds = []
    renderAt()
    expect(screen.getByText('Nothing expected in the next 24 hours')).toBeInTheDocument()
  })

  it('shows loading rows, not the empty message, while the gold list loads', () => {
    h.golds = []
    h.pipelines = []
    h.goldsLoading = true
    renderAt()
    expect(screen.queryByText(/Nothing expected/)).not.toBeInTheDocument()
  })
})
