import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Routes, Route, useLocation } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { SyncDetail } from '@/pages/data-sync/SyncDetail'
import type { DqCheckConfig, GoldPipelineConfig, RunMetrics } from '@/lib/types'

// Hoist mutation stubs to module scope so React 19 + jsdom doesn't re-render
// loop on a fresh stub identity per render (Task 21 lesson).
const upsertStub = { mutateAsync: vi.fn(), isPending: false }
const toggleStub = { mutate: vi.fn(), isPending: false }
const triggerStub = { mutate: vi.fn(), isPending: false }
const deleteStub = { mutate: vi.fn(), isPending: false }
const resetWmStub = { mutate: vi.fn(), isPending: false }
const reBootstrapStub = { mutate: vi.fn(), isPending: false }

const BASE = {
  pipeline_id: 'p1',
  enabled: true,
  schedule: { frequency: 'hourly' },
  extract: { table: 'X', mode: 'incremental' },
  target: { cloud: 'gcp', gcs_path: 'gs://b/p1/' },
  silver: {
    enabled: true,
    database: 'items',
    table: 'p1',
    primary_key_cols: ['id'],
  },
  metadata: { owner: 'raj.k@pearson.com' },
} as Record<string, unknown>

const HEALTHY_RUN = {
  run_id: 'r1',
  status: 'SUCCEEDED',
  started_at: new Date().toISOString(),
  rows_extracted: 1000,
  duration_sec: 60,
} as unknown as RunMetrics

let pipelineResult: { data: Record<string, unknown>; isLoading: boolean } = { data: BASE, isLoading: false }
let runItems: RunMetrics[] = [HEALTHY_RUN]
let dqResult: { data: DqCheckConfig[]; isLoading: boolean } = { data: [], isLoading: false }
let goldResult: { data: GoldPipelineConfig[] } = { data: [] }

vi.mock('@/hooks/usePipelines', () => ({
  usePipeline: () => pipelineResult,
  useUpsertPipeline: () => upsertStub,
  useTogglePipeline: () => toggleStub,
  useTriggerRun: () => triggerStub,
  useDeletePipeline: () => deleteStub,
}))

vi.mock('@/hooks/useRuns', () => ({
  // Every remaining export of @/hooks/useRuns, so the mock is
  // COMPLETE. vitest rejects a partial module mock, and each
  // missing one surfaced only as the next render-time throw --
  // three rounds of that before the list was filled from the
  // module's own exports rather than from the error messages.
  useActiveRunId: () => undefined,
  useRuns: () => ({ data: { items: [] } }),
  useRun: () => ({ data: undefined, isLoading: false, isError: false }),
  // Both are called for their side effect only -- SyncDetail.tsx:53 and
  // Syncs.tsx:96 assign neither return value, and useActiveRunPoll returns
  // nothing at all -- so a no-op is a faithful mock, not a stub that hides a
  // shape. Missing from this mock until UIFPE-515: vitest rejects a partial
  // module mock, so the component threw
  // `No "useActiveRunPoll" export is defined on the "@/hooks/useRuns" mock`
  // at render and the assertion never ran. Nothing caught it because this
  // stack ran no vitest at all before qe/ci.sh gained the gate.
  useActiveRunPoll: () => {},
  useSyncsActiveRunPolls: () => {},
  useRunsInfinite: () => ({
    data: { pages: [{ items: runItems, next_token: null }] },
    fetchNextPage: vi.fn(),
    hasNextPage: false,
  }),
}))

vi.mock('@/hooks/useDownstreamSyncs', () => ({
  useDownstreamSyncs: () => ({ data: [] }),
}))

vi.mock('@/hooks/useResetWatermark', () => ({
  useResetWatermark: () => resetWmStub,
}))

vi.mock('@/hooks/useReBootstrap', () => ({
  useReBootstrap: () => reBootstrapStub,
}))

vi.mock('@/hooks/useDqChecks', () => ({
  useDqChecks: () => dqResult,
}))

vi.mock('@/hooks/usePartnerExports', () => ({
  usePartnerExports: () => ({ data: [], isLoading: false }),
}))

vi.mock('@/hooks/useGoldPipelines', () => ({
  useGoldPipelines: () => goldResult,
}))

const ago = (h: number) => new Date(Date.now() - h * 3600_000).toISOString()

function ingest(over: Partial<RunMetrics>): RunMetrics {
  return {
    job_name: 'uif-echo-dev-bronze-ingest-eds',
    pipeline_id: 'p1',
    run_id: 'r',
    status: 'SUCCEEDED',
    started_at: ago(1),
    duration_sec: 60,
    rows_read: 10,
    rows_written: 10,
    ...over,
  }
}

function reconcileCheck(over: Partial<DqCheckConfig> = {}): DqCheckConfig {
  return {
    check_id: 'reconcile-items-p1', version: 'latest', kind: 'reconcile', enabled: true,
    subject: { layer: 'silver', database: 'items', table: 'p1' }, schedule: { frequency: 'daily' },
    parameters: {}, metadata: { created_at: '', owner: '' },
    last_run: { ts: '2026-09-28T08:55:00Z', status: 'SUCCEEDED', summary: 'variance 0' },
    ...over,
  }
}

function LocationProbe() {
  const loc = useLocation()
  return <div data-testid="loc">{loc.pathname + loc.search}</div>
}

function renderAt(path = '/m/data-sync/syncs/p1') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/m/data-sync/syncs/:id" element={<SyncDetail />} />
        </Routes>
        <LocationProbe />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

const kpi = (id: 'kpi-runs' | 'kpi-rows' | 'kpi-failed' | 'kpi-verified') => screen.getByTestId(id)

// Radix tabs activate on mouse down, so the Runs tab is opened the way a user does it.
async function openRunsTab() {
  await userEvent.setup().click(screen.getByRole('tab', { name: /^Runs/ }))
}

describe('SyncDetail', () => {
  beforeEach(() => {
    pipelineResult = { data: BASE, isLoading: false }
    runItems = [HEALTHY_RUN]
    dqResult = { data: [], isLoading: false }
    goldResult = { data: [] }
    triggerStub.mutate.mockClear()
    toggleStub.mutate.mockClear()
  })

  it('renders the header, the figures, run history, the section tabs and the aside in healthy state', () => {
    renderAt()
    // Title pulls from extract.table; the id, cloud, layers and health sit inside the h1.
    const headings = screen.getAllByRole('heading', { level: 1 })
    expect(headings).toHaveLength(1)
    const h1 = headings[0]
    expect(h1).toHaveTextContent(/^X/)
    expect(within(h1).getByText('Healthy')).toBeInTheDocument()
    expect(within(h1).getByText('p1')).toBeInTheDocument()
    expect(within(h1).getByText('AWS')).toBeInTheDocument()
    expect(within(h1).getByText('bronze · silver')).toBeInTheDocument()
    expect(screen.getByText(/Run history/i)).toBeInTheDocument()
    expect(screen.getByRole('tablist', { name: 'Sync sections' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'Details' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tab', { name: /^Runs/ })).toHaveTextContent('Runs1')
    expect(screen.getByText(/Dependencies/i)).toBeInTheDocument()
    expect(screen.queryByText(/^Identity$/)).not.toBeInTheDocument()
    expect(screen.queryByTestId('trust-card')).not.toBeInTheDocument()
  })

  it('has a back link to Replicate, one meta line and one primary action', () => {
    renderAt()
    expect(screen.getByRole('link', { name: 'Replicate' })).toHaveAttribute('href', '/m/data-sync/syncs')
    expect(screen.getByText(/^Oracle → bronze → silver · incremental · hourly/)).toBeInTheDocument()
    expect(screen.getByText('Last 30 days · 1 run')).toBeInTheDocument()
    expect(screen.queryByText('Behind schedule')).not.toBeInTheDocument()
    // Edit config and the More menu stay hidden per product request.
    expect(screen.queryByRole('button', { name: /Edit config/ })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Run now' }))
    expect(triggerStub.mutate).toHaveBeenCalledWith('p1')
  })

  it('a disabled sync offers Enable', () => {
    pipelineResult = { data: { ...BASE, enabled: false }, isLoading: false }
    renderAt()
    expect(within(screen.getByRole('heading', { level: 1 })).getByText('Disabled')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Enable' }))
    expect(toggleStub.mutate).toHaveBeenCalledWith({ id: 'p1', enabled: true })
  })

  it('a failing, overdue sync: Retry now, Behind schedule, the latest failure explained, and Retry on the newest failed row', async () => {
    runItems = [
      ingest({ run_id: 'r-fail', status: 'FAILED', started_at: ago(5), rows_read: 0, rows_written: 0, error_message: 'ORA-01017: invalid username/password; logon denied' }),
      ingest({ run_id: 'r-ok', started_at: ago(30), rows_read: 5120, rows_written: 5120 }),
    ]
    renderAt()
    const h1 = screen.getByRole('heading', { level: 1 })
    expect(within(h1).getByText('Failing')).toBeInTheDocument()
    expect(within(h1).getByText('Behind schedule')).toBeInTheDocument()

    const failure = screen.getByTestId('latest-failure')
    expect(within(failure).getByTestId('latest-failure-explain')).toHaveTextContent(/refused the sync's login/)
    expect(within(failure).getByText('ORA-01017: invalid username/password; logon denied')).toBeInTheDocument()

    expect(kpi('kpi-rows')).toHaveTextContent('Rows read → written')
    expect(kpi('kpi-rows')).toHaveTextContent('0 → 0')
    expect(kpi('kpi-rows')).toHaveTextContent('· failed')
    // Runs and Failed follow the run-history window (30 days by default).
    expect(kpi('kpi-runs')).toHaveTextContent('Runs · 30 days2')
    // 30 h ago is yesterday or the day before, by UTC calendar day
    expect(kpi('kpi-runs')).toHaveTextContent(/50% of finished runs succeeded · last success (yesterday|2 days ago)/)
    expect(within(kpi('kpi-runs')).getByTestId('success-ring')).toBeInTheDocument()
    expect(kpi('kpi-failed')).toHaveTextContent('Failed · 30 days1')
    expect(kpi('kpi-failed')).toHaveTextContent(/latest: \d\d:\d\d UTC · ORA-01017/)
    expect(screen.getByText('Last 30 days · 2 runs')).toBeInTheDocument()

    await openRunsTab()
    const recent = screen.getByTestId('recent-runs')
    // the newest success's row count, which the old trust card showed, is in the Runs tab
    expect(within(recent).getAllByText('5,120 → 5,120').length).toBeGreaterThan(0)
    expect(within(recent).getByRole('link', { name: 'All runs for this sync →' })).toHaveAttribute(
      'href',
      '/m/data-sync/runs?object=p1&window=all',
    )
    expect(within(recent).getAllByRole('button', { name: 'Retry' })).toHaveLength(1)
    fireEvent.click(within(recent).getByRole('button', { name: 'Retry' }))
    expect(triggerStub.mutate).toHaveBeenCalledWith('p1')
    // the Retry click does not also open the drawer
    expect(screen.getByTestId('loc').textContent).toBe('/m/data-sync/syncs/p1')

    fireEvent.click(screen.getByRole('button', { name: 'Retry now' }))
    expect(triggerStub.mutate).toHaveBeenCalledTimes(2)
  })

  it('open run on the failure note and a click on a recent run both open the drawer', async () => {
    runItems = [ingest({ run_id: 'r-fail', status: 'FAILED', error_message: 'boom' })]
    const first = renderAt()
    fireEvent.click(screen.getByRole('button', { name: 'open run' }))
    expect(screen.getByTestId('loc').textContent).toBe('/m/data-sync/syncs/p1?run=r-fail')
    first.unmount()

    renderAt()
    await openRunsTab()
    fireEvent.click(within(screen.getByTestId('recent-runs')).getByRole('button', { name: 'Open run r-fail' }))
    expect(screen.getByTestId('loc').textContent).toBe('/m/data-sync/syncs/p1?run=r-fail')
  })

  it('hides the latest failure note when the last run succeeded, and shows no Retry on the runs', async () => {
    runItems = [ingest({ run_id: 'r-ok' })]
    renderAt()
    expect(screen.queryByTestId('latest-failure')).not.toBeInTheDocument()
    expect(kpi('kpi-failed')).toHaveTextContent('nothing failed')
    await openRunsTab()
    expect(screen.getByTestId('recent-runs')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Retry' })).not.toBeInTheDocument()
  })

  it('the run-history window drives the Runs and Failed figures', () => {
    runItems = [
      ingest({ run_id: 'r-new', status: 'FAILED', started_at: ago(2), error_message: 'timed out' }),
      ingest({ run_id: 'r-old', started_at: ago(24 * 10) }),
    ]
    renderAt()
    expect(kpi('kpi-runs')).toHaveTextContent('Runs · 30 days2')
    fireEvent.click(screen.getByRole('button', { name: '24 hours' }))
    expect(kpi('kpi-runs')).toHaveTextContent('Runs · 24 hours1')
    expect(kpi('kpi-runs')).toHaveTextContent('0% of finished runs succeeded · last success 10 days ago')
    expect(kpi('kpi-failed')).toHaveTextContent('Failed · 24 hours1')
    expect(kpi('kpi-failed')).toHaveTextContent('timeout')
    expect(screen.getByText('Last 24 hours · 1 run')).toBeInTheDocument()
  })

  it('the Details tab holds the source, silver and schedule facts, with Reset… on the Load row', () => {
    pipelineResult = {
      data: {
        ...BASE,
        source: { secret_arn: 'arn:aws:secretsmanager:eu-west-1:1:secret:oracle-eds-AbC123' },
        extract: { table: 'X', mode: 'incremental', watermark_col: 'LAST_UPDATE_DATE' },
        glue: { worker_type: 'G.1X', num_workers: 5, timeout_minutes: 60 },
        metadata: { owner: 'raj.k@pearson.com', tags: ['production-critical'] },
      },
      isLoading: false,
    }
    runItems = [ingest({ new_watermark: '2026-09-28T13:14:59Z' })]
    renderAt()
    const source = screen.getByTestId('details-source')
    expect(source).toHaveTextContent('Connection oracle-eds · …secret:oracle-eds-AbC123')
    expect(source).toHaveTextContent('Load incremental · watermark LAST_UPDATE_DATE · 2026-09-28T13:14:59Z · Reset…')
    expect(source).toHaveTextContent('Bronze gs://b/p1/')
    const silver = screen.getByTestId('details-silver')
    expect(silver).toHaveTextContent('Table items.p1 · PK id')
    expect(silver).toHaveTextContent('raj.k@pearson.com')
    expect(silver).toHaveTextContent('production-critical')
    const schedule = screen.getByTestId('details-schedule')
    expect(schedule).toHaveTextContent('Schedule hourly')
    expect(schedule).toHaveTextContent('Glue G.1X × 5 · 60 min timeout')
    fireEvent.click(within(source).getByRole('button', { name: 'Reset…' }))
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })

  it('Last verified reads the newest reconcile check on the silver table and links to it', () => {
    dqResult = { data: [reconcileCheck()], isLoading: false }
    renderAt()
    const cell = kpi('kpi-verified')
    expect(within(cell).getByText('Passed')).toBeInTheDocument()
    expect(cell).toHaveTextContent('reconcile · 28 Sep 2026, 08:55 UTC · variance 0')
    expect(within(cell).getByRole('link', { name: 'open check' })).toHaveAttribute(
      'href',
      '/m/data-quality/checks/reconcile-items-p1',
    )
  })

  it('says Not verified, with a link to add a reconcile check for this silver table, when none exists', () => {
    renderAt()
    const cell = kpi('kpi-verified')
    expect(cell).toHaveTextContent('Not verified')
    expect(within(cell).getByRole('link', { name: 'Add reconcile check' })).toHaveAttribute(
      'href',
      '/m/data-quality/checks/new?subject=silver.items.p1',
    )
  })

  it('links to a plain New check when the sync has no silver table', () => {
    pipelineResult = { data: { ...BASE, silver: { enabled: false } }, isLoading: false }
    renderAt()
    expect(within(kpi('kpi-verified')).getByRole('link', { name: 'Add reconcile check' })).toHaveAttribute(
      'href',
      '/m/data-quality/checks/new',
    )
  })

  it('the quality card lists the configured rules and scores them from the newest run with a result', () => {
    pipelineResult = {
      data: { ...BASE, quality: { not_null_cols: ['ID'], min_rows_expected: 1000 } },
      isLoading: false,
    }
    runItems = [ingest({ quality_result: { not_null_checks: { ID: { passed: true, null_count: 0 } } } })]
    renderAt()
    const card = screen.getByTestId('quality-card')
    expect(within(card).getByText('ID')).toBeInTheDocument()
    expect(within(card).getByText('1 / 1')).toBeInTheDocument()
    expect(within(card).getByText(/≥ 1,000 expected/)).toBeInTheDocument()
    expect(within(card).getByText('not evaluated yet')).toBeInTheDocument()
  })

  it('the quality card says so when no rules are configured', () => {
    renderAt()
    expect(screen.getByText('No quality rules configured on this sync.')).toBeInTheDocument()
  })

  it('Dependencies names the gold tables that read this silver table', () => {
    goldResult = {
      data: [
        { gold_id: 'product', sources: [{ alias: 'p', silver_table: 'uif_echo_{env}_silver_items.p1' }] },
        { gold_id: 'stock', sources: [{ alias: 's', silver_table: 'x.stock' }] },
      ],
    }
    renderAt()
    expect(screen.getByText('1 gold table reads this silver table')).toBeInTheDocument()
    expect(screen.getByText('product')).toBeInTheDocument()
  })

  describe('run drawer (UIFPE-681)', () => {
    it('stays closed until a run is named in the URL', () => {
      renderAt()
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    })

    it('?run= opens the drawer for that run, in the new layout', () => {
      renderAt('/m/data-sync/syncs/p1?run=r1')
      const dialog = screen.getByRole('dialog')
      expect(within(dialog).getByRole('heading', { name: 'Properties' })).toBeInTheDocument()
      expect(within(dialog).getByTestId('run-explain')).toHaveTextContent(/Completed with/)
      expect(within(dialog).queryByText(/Worker profile/)).not.toBeInTheDocument()
      expect(within(dialog).queryByText(/View full log/)).not.toBeInTheDocument()
    })

    it('an unknown run id opens nothing', () => {
      renderAt('/m/data-sync/syncs/p1?run=nope')
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    })

    it('Retry run still triggers the sync', () => {
      renderAt('/m/data-sync/syncs/p1?run=r1')
      fireEvent.click(screen.getByRole('button', { name: 'Retry run' }))
      expect(triggerStub.mutate).toHaveBeenCalledWith('p1')
    })

    it('Close drops ?run= from the address', () => {
      renderAt('/m/data-sync/syncs/p1?run=r1')
      const dialog = screen.getByRole('dialog')
      fireEvent.click(within(dialog).getAllByRole('button', { name: /^Close$/ }).at(-1)!)
      expect(screen.getByTestId('loc').textContent).toBe('/m/data-sync/syncs/p1')
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    })
  })
})
