import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, within } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { GoldList } from '../GoldList'
import type { GoldStatsResponse } from '@/lib/goldStats'
import type { GoldPipelineConfig, GoldRunMetrics } from '@/lib/types'

// GET /gold-pipelines, /gold-runs and /gold-stats: each test decides what they return.
let goldResult: { data?: GoldPipelineConfig[]; isLoading?: boolean; isError?: boolean } = {}
let runsResult: { data?: { items: GoldRunMetrics[]; truncated: boolean }; isLoading?: boolean; error?: unknown } = {}
let statsResult: { data?: GoldStatsResponse; isLoading?: boolean; error?: unknown } = {}
const refetchGold = vi.fn()

vi.mock('@/hooks/useGoldPipelines', () => ({
  useGoldPipelines: () => ({ ...goldResult, refetch: refetchGold }),
}))
vi.mock('@/hooks/useGoldRuns', () => ({ useGoldRuns: () => runsResult }))
vi.mock('@/hooks/useGoldStats', () => ({ useGoldStats: () => statsResult }))

const triggerMutate = vi.fn()
const setScheduleMutate = vi.fn()
const toggleMutate = vi.fn()
vi.mock('@/hooks/useGoldPipelineMutations', () => ({
  useTriggerGoldRun:     () => ({ mutate: triggerMutate, isPending: false, variables: undefined }),
  useSetGoldSchedule:    () => ({ mutate: setScheduleMutate, isPending: false }),
  useToggleGoldPipeline: () => ({ mutate: toggleMutate, isPending: false }),
  useGoldRunLock:        () => null,
}))

// Pin the env so the {env} placeholder resolves to a known value.
vi.mock('@/lib/envConfig', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/envConfig')>()),
  getEnv: () => 'dev',
}))
// The schedule dialog works in the viewer's zone (UIFPE-532); pin UTC.
vi.mock('@/lib/goldWeekly', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/goldWeekly')>()),
  browserTimeZone: () => 'UTC',
}))

const hoursAgo = (h: number) => new Date(Date.now() - h * 3600_000).toISOString()

const GOLD: GoldPipelineConfig[] = [
  {
    gold_id: 'product', enabled: true, domain: 'product',
    sources: [{ silver_table: 'a.b' }, { silver_table: 'a.c' }, { silver_table: 'a.d' }],
    target: { database: 'uif_echo_{env}_gold_product', table: 'product' },
    schedule: { frequency: 'gold-24h', start_time: '06:30' },
    metadata: { last_run_at: hoursAgo(2) },
  },
  {
    gold_id: 'marketing_copy', enabled: true, domain: 'product',
    sources: [{ silver_table: 'a.b' }],
    target: { database: 'uif_echo_{env}_gold_product', table: 'marketing_copy' },
    schedule: { frequency: 'gold-24h', start_time: '07:15' },
    metadata: { last_run_at: hoursAgo(2) },
  },
  {
    gold_id: 'stock', enabled: false, domain: 'stock',
    target: { database: 'uif_echo_{env}_gold_stock', table: 'stock' },
    schedule: { frequency: 'gold-24h', start_time: '08:15' },
  },
] as GoldPipelineConfig[]

function goldRun(over: Partial<GoldRunMetrics> & { gold_id: string }): GoldRunMetrics {
  return {
    job_name: 'gold-transform', run_id: `r-${over.gold_id}`, status: 'SUCCEEDED',
    started_at: hoursAgo(2), completed_at: hoursAgo(2), rows_written: 10,
    ...over,
  } as GoldRunMetrics
}

function stats(over: Partial<GoldStatsResponse['live']> = {}): GoldStatsResponse {
  return {
    live: {
      as_of: '2026-09-24T12:00:00+00:00', window_hours: 24,
      entities: 3, enabled: 2, runs: 7, succeeded: 5, failed: 1, skipped: 3,
      rows_written: 1200, fresh: 2, stale: 1, unknown: 0, stale_entities: ['stock'],
      ...over,
    },
    trend: {
      schema_version: 1, generated_at: '2026-09-24T04:30:00+00:00', days: 1,
      fleet: [{ day: '2026-09-24', entities: 3, runs: 7, failed: 1, rows_written: 900, fresh: 2 }],
      entities: {},
    },
    trend_available: true, trend_unavailable_reason: null,
    trend_generated_at: '2026-09-24T04:30:00+00:00', trend_age_minutes: 90,
    generated_at: '2026-09-24T06:00:00+00:00',
  }
}

function LocationProbe() {
  const loc = useLocation()
  return <div data-testid="location">{loc.pathname}{loc.search}</div>
}

function renderPage(initialEntries?: string[]) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={initialEntries}>
        <GoldList />
        <LocationProbe />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

function rowOf(text: string | RegExp): HTMLElement {
  return screen.getByText(text).closest('tr') as HTMLElement
}

beforeEach(() => {
  goldResult = { data: GOLD }
  runsResult = {
    data: {
      truncated: false,
      items: [
        goldRun({ gold_id: 'product', rows_written: 1284 }),
        goldRun({ gold_id: 'marketing_copy', rows_written: 0 }),
      ],
    },
  }
  statsResult = { data: stats() }
  triggerMutate.mockClear(); setScheduleMutate.mockClear(); toggleMutate.mockClear(); refetchGold.mockClear()
  if (!window.matchMedia) {
    window.matchMedia = vi.fn().mockReturnValue({
      matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn(),
    }) as unknown as typeof window.matchMedia
  }
})

describe('Transform landing - header and count line', () => {
  it('titles the page Transform, with the description behind the info tip', () => {
    renderPage()
    expect(screen.getByRole('heading', { level: 1, name: 'Transform' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'About Transform' })).toBeInTheDocument()
    expect(screen.getByRole('tooltip')).toHaveTextContent('Gold tables built from silver.')
  })

  it('links My submissions and New transformation to the transform-ui bundle as plain anchors', () => {
    renderPage()
    expect(screen.getByRole('link', { name: 'My submissions' }).getAttribute('href'))
      .toBe('/m/transform/index.html#/transform/submissions')
    expect(screen.getByRole('link', { name: 'New transformation' }).getAttribute('href'))
      .toBe('/m/transform/index.html#/transform/new')
  })

  it('draws the count line from the live half of /gold-stats', () => {
    renderPage()
    expect(screen.getByTestId('gold-kpi-entities')).toHaveTextContent('3 gold tables · 2 enabled')
    expect(screen.getByTestId('gold-kpi-succeeded')).toHaveTextContent('5 succeeded in 24 h')
    expect(screen.getByTestId('gold-kpi-failed')).toHaveTextContent('1 failed in 24 h')
    expect(screen.getByTestId('gold-kpi-fresh')).toHaveTextContent('2 of 3 inside freshness · behind: stock')
    expect(screen.getByTestId('gold-kpi-entities').parentElement).toHaveTextContent(
      '3 gold tables · 2 enabled · 5 succeeded in 24 h · 1 failed in 24 h · 2 of 3 inside freshness · behind: stock',
    )
  })

  it('shows each segment as unavailable when the stats call fails', () => {
    statsResult = { error: new Error('boom') }
    renderPage()
    expect(screen.getByTestId('gold-kpi-entities')).toHaveTextContent('Not available here')
    expect(screen.getByTestId('gold-kpi-fresh')).toHaveTextContent('Not available here')
  })

  it('shimmers the count-line figures while stats load', () => {
    statsResult = { isLoading: true }
    renderPage()
    expect(screen.getByTestId('gold-kpi-entities').querySelector('[data-slot="skeleton"]')).not.toBeNull()
  })

  it('shows the 30-day trend card under the table', () => {
    renderPage()
    expect(screen.getByTestId('gold-trend-source')).toHaveTextContent('Computed nightly')
  })
})

describe('Transform landing - rows', () => {
  it('renders each gold table with its name, catalog name and domain beneath, and source count', () => {
    renderPage()
    const row = rowOf('marketing_copy')
    expect(within(row).getByText('uif_echo_dev_gold_product.marketing_copy')).toBeInTheDocument()
    expect(within(row).getByText('product')).toBeInTheDocument()          // domain
    expect(within(row).getByText('1 silver table')).toBeInTheDocument()
    expect(within(rowOf('uif_echo_dev_gold_product.product')).getByText('3 silver tables')).toBeInTheDocument()
  })

  it('orders the columns Gold table, Sources, Schedule, Last run, Status, Freshness, Rows written, Actions', () => {
    renderPage()
    expect(screen.getAllByRole('columnheader').map(th => th.textContent)).toEqual([
      'Gold table', 'Sources', 'Schedule', 'Last run', 'Status', 'Freshness', 'Rows written', 'Actions',
    ])
  })

  it('links the table to its own detail page, never to the sync detail route', () => {
    renderPage()
    expect(screen.getByRole('link', { name: 'product' }).getAttribute('href')).toBe('/m/data-sync/gold/product')
    expect(screen.getByRole('link', { name: 'Open product' }).getAttribute('href')).toBe('/m/data-sync/gold/product')
    screen.getAllByRole('link').forEach(a => {
      expect(a.getAttribute('href') ?? '').not.toMatch(/^\/m\/data-sync\/syncs\//)
    })
  })

  it('shows the real status of the latest run, not Unknown', () => {
    renderPage()
    expect(within(rowOf('uif_echo_dev_gold_product.product')).getByText('Succeeded')).toBeInTheDocument()
    expect(within(rowOf('uif_echo_dev_gold_product.product')).queryByText('Unknown')).not.toBeInTheDocument()
  })

  it('says "no changes" for a success that wrote 0 rows, and Disabled for a disabled table', () => {
    renderPage()
    expect(within(rowOf('uif_echo_dev_gold_product.marketing_copy')).getByText('Succeeded · no changes')).toBeInTheDocument()
    expect(within(rowOf('uif_echo_dev_gold_stock.stock')).getByText('Disabled')).toBeInTheDocument()
  })

  it('uses the newest run per table', () => {
    runsResult = {
      data: {
        truncated: false,
        items: [
          goldRun({ gold_id: 'product', status: 'SUCCEEDED', started_at: hoursAgo(30), run_id: 'old' }),
          goldRun({ gold_id: 'product', status: 'FAILED', started_at: hoursAgo(1), run_id: 'new', rows_written: 0 }),
        ],
      },
    }
    renderPage()
    expect(within(rowOf('uif_echo_dev_gold_product.product')).getByText('Failed')).toBeInTheDocument()
  })

  it('an enabled table with no run at all is Unknown', () => {
    runsResult = { data: { items: [], truncated: false } }
    renderPage()
    expect(within(rowOf('uif_echo_dev_gold_product.product')).getByText('Unknown')).toBeInTheDocument()
  })

  it('shows rows written from the latest run, and the freshness badge', () => {
    renderPage()
    const row = rowOf('uif_echo_dev_gold_product.product')
    expect(within(row).getByText('1,284')).toBeInTheDocument()
    expect(within(row).getByText('Fresh')).toBeInTheDocument()
    expect(within(row).getByText(/about 2 hours ago/)).toBeInTheDocument()
    // Same dialect as the sync rows' schedule cell, not the raw cadence token;
    // cadence on one line, the clock time beneath it.
    expect(within(row).getByText('daily')).toBeInTheDocument()
    expect(within(row).getByText('06:30 UTC')).toBeInTheDocument()
    expect(within(row).queryByText(/gold-24h/)).not.toBeInTheDocument()
  })

  it('reads Unknown for every enabled table, with a note, when the runs call fails', () => {
    runsResult = { error: new Error('boom') }
    renderPage()
    expect(screen.getByText('Run status unavailable right now.')).toBeInTheDocument()
    expect(within(rowOf('uif_echo_dev_gold_product.product')).getByText('Unknown')).toBeInTheDocument()
    expect(within(rowOf('uif_echo_dev_gold_product.marketing_copy')).getByText('Unknown')).toBeInTheDocument()
    // A disabled table is disabled by its config, whatever the runs call says.
    expect(within(rowOf('uif_echo_dev_gold_stock.stock')).getByText('Disabled')).toBeInTheDocument()
  })

  it('shimmers the run-derived cells of enabled rows while runs load', () => {
    runsResult = { isLoading: true }
    renderPage()
    expect(rowOf('uif_echo_dev_gold_product.product').querySelectorAll('[data-slot="skeleton"]').length).toBe(3)
    expect(within(rowOf('uif_echo_dev_gold_stock.stock')).getByText('Disabled')).toBeInTheDocument()
  })
})

describe('Transform landing - actions', () => {
  it('Run asks for confirmation naming the table, then triggers by gold_id', () => {
    renderPage()
    fireEvent.click(within(rowOf('uif_echo_dev_gold_product.product')).getByRole('button', { name: 'Run gold transform' }))
    expect(screen.getByRole('dialog')).toHaveTextContent('uif_echo_dev_gold_product.product')
    expect(triggerMutate).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Run now' }))
    expect(triggerMutate).toHaveBeenCalledWith('product', expect.anything())
  })

  it('does not offer Run on a disabled table', () => {
    renderPage()
    expect(within(rowOf('uif_echo_dev_gold_stock.stock')).getByRole('button', { name: 'Enable before running' })).toBeDisabled()
  })

  it('Disable and Enable toggle by gold_id with an explicit boolean', () => {
    renderPage()
    fireEvent.click(within(rowOf('uif_echo_dev_gold_product.product')).getByRole('button', { name: 'Disable' }))
    expect(toggleMutate).toHaveBeenCalledWith({ id: 'product', enabled: false })
    fireEvent.click(within(rowOf('uif_echo_dev_gold_stock.stock')).getByRole('button', { name: 'Enable' }))
    expect(toggleMutate).toHaveBeenCalledWith({ id: 'stock', enabled: true })
  })

  // UIFPE-513: start_time is console-owned; cadence is chosen in the dialog, and Save
  // sends only the change, by gold_id.
  it('changes a run time from the Schedule button', () => {
    renderPage()
    fireEvent.click(screen.getByRole('button', { name: /Change run time for uif_echo_dev_gold_product\.product/ }))
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByLabelText(/Cadence/)).toHaveValue('gold-24h')
    const save = within(dialog).getByRole('button', { name: /Save schedule/ })
    expect(save).toBeDisabled()
    fireEvent.change(within(dialog).getByLabelText(/Start time/), { target: { value: '07:00' } })
    fireEvent.click(save)
    expect(setScheduleMutate).toHaveBeenCalledWith(
      { id: 'product', change: { start_time: '07:00' } }, expect.anything())
  })
})

describe('Transform landing - filters, search, states', () => {
  it('reads domain, status and freshness from the URL into the MultiSelects, and unticking one lifts it', () => {
    renderPage(['/?domain=stock&status=DISABLED&freshness=stale'])
    expect(screen.getByRole('button', { name: 'Filter by domain' })).toHaveTextContent('stock')
    expect(screen.getByRole('button', { name: 'Filter by status' })).toHaveTextContent('Disabled')
    expect(screen.getByRole('button', { name: 'Filter by freshness' })).toHaveTextContent('Stale')
    fireEvent.click(screen.getByRole('button', { name: 'Filter by freshness' }))
    fireEvent.click(screen.getByRole('option', { name: /^Stale/ }))
    expect(screen.getByTestId('location').textContent).toBe('/?domain=stock&status=DISABLED')
  })

  it('shows every domain with none in the URL, and a pick writes that domain', () => {
    renderPage()
    const domain = screen.getByRole('button', { name: 'Filter by domain' })
    expect(domain).toHaveTextContent('All domains (2)')
    fireEvent.click(domain)
    fireEvent.click(screen.getByRole('option', { name: /^stock/ }))
    expect(screen.getByTestId('location').textContent).toBe('/?domain=stock')
    expect(screen.queryByText('uif_echo_dev_gold_product.product')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /Select all/ }))
    expect(screen.getByTestId('location').textContent).toBe('/')
  })

  it('gold tables answer the domain facet', () => {
    renderPage(['/?domain=stock'])
    expect(screen.getByText('uif_echo_dev_gold_stock.stock')).toBeInTheDocument()
    expect(screen.queryByText('uif_echo_dev_gold_product.product')).not.toBeInTheDocument()
  })

  it('the status facet matches the real latest-run status', () => {
    runsResult = {
      data: {
        truncated: false,
        items: [
          goldRun({ gold_id: 'product', status: 'FAILED', rows_written: 0 }),
          goldRun({ gold_id: 'marketing_copy' }),
        ],
      },
    }
    renderPage(['/?status=FAILED'])
    expect(screen.getByText('uif_echo_dev_gold_product.product')).toBeInTheDocument()
    expect(screen.queryByText('uif_echo_dev_gold_product.marketing_copy')).not.toBeInTheDocument()
  })

  it('ignores source and tag params, which gold has no answer to', () => {
    renderPage(['/?source=Oracle&tag=x'])
    expect(screen.getByText('uif_echo_dev_gold_product.product')).toBeInTheDocument()
  })

  it('search matches a gold table name and hides the rest', () => {
    renderPage()
    fireEvent.change(screen.getByRole('searchbox', { name: 'Search gold table' }), { target: { value: 'gold_stock' } })
    expect(screen.getByText('uif_echo_dev_gold_stock.stock')).toBeInTheDocument()
    expect(screen.queryByText('uif_echo_dev_gold_product.product')).not.toBeInTheDocument()
  })

  it('a miss gives the filtered-empty state, and Clear all brings the rows back', () => {
    renderPage(['/?domain=nothing'])
    expect(screen.getByText('No gold tables match these filters')).toBeInTheDocument()
    fireEvent.click(screen.getAllByRole('button', { name: 'Clear all' }).at(-1)!)
    expect(screen.getByTestId('location').textContent).toBe('/')
    expect(screen.getByText('uif_echo_dev_gold_product.product')).toBeInTheDocument()
  })

  it('clicking the Gold table header sorts descending', () => {
    renderPage()
    const names = () => screen.getAllByRole('row').slice(1).map(r => r.querySelector('td a')?.textContent)
    expect(names()).toEqual(['marketing_copy', 'product', 'stock'])
    fireEvent.click(within(screen.getByRole('columnheader', { name: /Gold table/ })).getByRole('button'))
    expect(names()).toEqual(['stock', 'product', 'marketing_copy'])
  })

  it('says "No gold tables yet" when there are none', () => {
    goldResult = { data: [] }
    renderPage()
    expect(screen.getByText('No gold tables yet')).toBeInTheDocument()
  })

  it('says it could not load, and Retry refetches', () => {
    goldResult = { data: undefined, isError: true }
    renderPage()
    expect(screen.getByText(/Couldn.t load gold tables\./)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(refetchGold).toHaveBeenCalledTimes(1)
  })
})
