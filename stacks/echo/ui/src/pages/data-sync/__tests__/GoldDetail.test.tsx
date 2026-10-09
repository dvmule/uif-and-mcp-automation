import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { GoldDetail } from '@/pages/data-sync/GoldDetail'
import type { GoldPipelineConfig, GoldRunMetrics } from '@/lib/types'

vi.mock('@/lib/envConfig', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/envConfig')>()),
  getEnv: () => 'dev',
}))
// Clock labels follow the viewer's zone (UIFPE-532); pin it so the test does
// not depend on the machine running it.
vi.mock('@/lib/goldWeekly', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/goldWeekly')>()),
  browserTimeZone: () => 'UTC',
}))

const PRODUCT: GoldPipelineConfig = {
  gold_id: 'product', enabled: true, domain: 'product',
  target: { database: 'uif_echo_{env}_gold_product', table: 'product' },
  schedule: { frequency: 'gold-24h', start_time: '06:30', failure_cooldown_minutes: 30, recompute: { every_n_runs: 8 } },
  glue: { worker_type: 'G.1X', num_workers: 8, timeout_minutes: 180, full_load: { worker_type: 'G.2X', num_workers: 8, timeout_minutes: 180 } },
  sources: [
    { alias: 'items', silver_table: 'uif_echo_{env}_silver_mdm.mtl_system_items_b' },
    { alias: 'categories_b', silver_table: 'uif_echo_{env}_silver_mdm.mtl_categories_b' },
  ],
  metadata: { last_run_at: '2026-09-22T06:33:10+00:00' },
  watermark: {
    // ADR-0020: the runtime writes ONE bookmark (UIFPE-522) ...
    bookmark: '2026-09-22T06:31:13.947992+00:00',
    // ... and a row may still carry the retired per-source map until its
    // next run removes it; the page must NOT render it.
    last_processed_silver_updated_at: { items: '2026-09-22T05:55:00' },
    silver_snapshots: { items: 's3://uif-echo-dev-silver/mtl_system_items_b/metadata/00042.metadata.json' },
  },
  snapshot_gate: true,
}

let golds: GoldPipelineConfig[] = [PRODUCT]
vi.mock('@/hooks/useGoldPipelines', () => ({
  useGoldPipelines: () => ({ data: golds, isLoading: false }),
}))

// Each test decides whether /gold-runs is deployed (data) or not (bare 404).
let runsResult: { data?: { items: GoldRunMetrics[]; truncated: boolean }; error?: unknown } = { data: { items: [], truncated: false } }
vi.mock('@/hooks/useGoldRuns', () => ({
  useGoldRuns: () => runsResult,
}))

const triggerMutate = vi.fn(), toggleMutate = vi.fn(), setScheduleMutate = vi.fn()
let lockedRunId: string | null = null
vi.mock('@/hooks/useGoldPipelineMutations', () => ({
  useTriggerGoldRun:     () => ({ mutate: triggerMutate, isPending: false }),
  useToggleGoldPipeline: () => ({ mutate: toggleMutate, isPending: false }),
  useSetGoldSchedule:    () => ({ mutate: setScheduleMutate, isPending: false }),
  useGoldRunLock:        () => lockedRunId,
}))
// The per-table "notify me" card (UIFPE-543) has its own test; here it only has to mount.
vi.mock('@/hooks/useNotifications', () => ({
  useAlertSubscribers:      () => ({ data: { items: [], total: 0 }, isLoading: false }),
  useAddAlertSubscriber:    () => ({ mutate: vi.fn(), isPending: false }),
  useSetAlertSubscriber:    () => ({ mutate: vi.fn(), isPending: false }),
  useRemoveAlertSubscriber: () => ({ mutate: vi.fn(), isPending: false }),
}))

const gateway404 = Object.assign(new Error('Request failed with status code 404'), {
  isAxiosError: true, response: { status: 404, data: { message: 'Not Found' } },
})

function goldRun(over: Partial<GoldRunMetrics>): GoldRunMetrics {
  return {
    job_name: 'uif-echo-dev-gold-transform', run_id: 'jr_1', gold_id: 'product', status: 'SUCCEEDED',
    started_at: new Date(Date.now() - 3600_000).toISOString(), duration_sec: 95,
    rows_read: 2_142_292, rows_written: 1_320, affected_keys: 4_410, ...over,
  }
}

// Radix tabs activate on mouse down, so the Runs tab is opened the way a user does it.
async function openRunsTab() {
  await userEvent.setup().click(screen.getByRole('tab', { name: /^Runs/ }))
}

function renderAt(path = '/m/data-sync/gold/product') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/m/data-sync/gold/:goldId" element={<GoldDetail />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('GoldDetail (UIFPE-517)', () => {
  beforeEach(() => {
    golds = [PRODUCT]
    runsResult = { data: { items: [], truncated: false } }
    lockedRunId = null
    triggerMutate.mockClear(); toggleMutate.mockClear(); setScheduleMutate.mockClear()
  })

  it('names the entity in the header with its facts in the shared dialect, and links back and to the catalog', () => {
    renderAt()
    const headings = screen.getAllByRole('heading', { level: 1 })
    expect(headings).toHaveLength(1)
    const h1 = headings[0]
    expect(h1).toHaveTextContent(/^product/)
    expect(screen.getByRole('link', { name: 'Transform' })).toHaveAttribute('href', '/m/data-sync/gold')
    // the target table is a pill in the title, and opens the catalog's dataset page
    const fqn = within(h1).getByRole('link', { name: 'uif_echo_dev_gold_product.product' })
    expect(fqn).toHaveAttribute('href', '/m/catalog/datasets/gold/aws/uif_echo_dev_gold_product/product')
    // the domain is a pill too, so the meta line starts after it
    expect(within(h1).getAllByText('product').length).toBe(2)
    expect(screen.getByText(/^built from 2 silver tables · daily · 06:30 UTC · next run (today|tomorrow) 06:30 UTC$/)).toBeInTheDocument()
  })

  it('shows a freshness badge beside the health badge on an enabled entity', () => {
    renderAt()
    // last_run_at is days old against a daily cadence
    expect(within(screen.getByRole('heading', { level: 1 })).getByText('Overdue')).toBeInTheDocument()
  })

  // The config knows WHEN it ran, not HOW it ended: with nothing to read, say so.
  it('shows UNKNOWN with no runs, never an invented Healthy', () => {
    renderAt()
    expect(screen.getByText('Unknown')).toBeInTheDocument()
    expect(screen.queryByText('Healthy')).not.toBeInTheDocument()
  })

  it('tells the truth when /gold-runs is not deployed here, in the figures and both run sections', async () => {
    runsResult = { error: gateway404 }
    renderAt()
    const ids = ['kpi-runs', 'kpi-rows', 'kpi-failed', 'kpi-keys']
    for (const id of ids) {
      expect(within(screen.getByTestId(id)).getByText('not available in this environment yet')).toBeInTheDocument()
      expect(screen.getByTestId(id)).toHaveTextContent('—')
    }
    expect(screen.getAllByText('not available in this environment yet')).toHaveLength(4)
    expect(screen.queryByText(/^Last 30 days/)).not.toBeInTheDocument()
    await openRunsTab()
    expect(screen.getAllByText(/Run history is not available in this environment yet/).length).toBeGreaterThanOrEqual(2)
    expect(screen.queryByRole('link', { name: /All runs for this table/ })).not.toBeInTheDocument()
    // and never the sync page's misleading "click Run now to bootstrap"
    expect(screen.queryByText(/No runs yet/)).not.toBeInTheDocument()
  })

  it('renders runs in gold terms once /gold-runs answers: keys selected, rows written, no watermark, no retry', async () => {
    runsResult = { data: { items: [goldRun({})], truncated: false } }
    renderAt()
    expect(within(screen.getByRole('heading', { level: 1 })).getByText('Healthy')).toBeInTheDocument()

    expect(screen.getByTestId('kpi-rows')).toHaveTextContent(`Rows written${(1_320).toLocaleString()}`)
    expect(screen.getByTestId('kpi-keys')).toHaveTextContent(`Keys selected by CDC${(4_410).toLocaleString()}`)
    expect(screen.getByTestId('kpi-keys')).toHaveTextContent('affected keys in the last run · bookmark advanced')
    expect(screen.getByTestId('kpi-runs')).toHaveTextContent('Runs · 30 days1')
    expect(screen.getByTestId('kpi-runs')).toHaveTextContent('100% of finished runs succeeded · last success')
    expect(screen.getByTestId('kpi-failed')).toHaveTextContent('Failed · 30 days0')
    expect(screen.getByTestId('kpi-failed')).toHaveTextContent('nothing failed')
    expect(screen.getByText('Last 30 days · 1 run')).toBeInTheDocument()

    await openRunsTab()
    const recent = screen.getByTestId('recent-runs')
    const headers = within(recent).getAllByRole('columnheader').map(c => c.textContent)
    expect(headers).toEqual(['Started', 'Status', 'Duration', 'Keys selected', 'Rows written'])
    const row = within(recent).getAllByRole('row')[1]
    expect(row).toHaveTextContent((1_320).toLocaleString())
    expect(row).toHaveTextContent((4_410).toLocaleString())
    expect(within(recent).getByRole('link', { name: 'All runs for this table →' })).toHaveAttribute(
      'href', '/m/data-sync/runs?object=product&window=all')
    expect(screen.queryByText('Watermark', { selector: 'span' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Retry/ })).not.toBeInTheDocument()
    // a clean last run has no failure card
    expect(screen.queryByTestId('latest-failure')).not.toBeInTheDocument()
  })

  it('a recent-run click opens the shared drawer, whose Run now goes through the confirm dialog', async () => {
    runsResult = { data: { items: [goldRun({})], truncated: false } }
    renderAt()
    await openRunsTab()
    fireEvent.click(within(screen.getByTestId('recent-runs')).getByRole('button', { name: 'Open run jr_1' }))
    const drawer = screen.getByRole('dialog')
    expect(within(drawer).getByText('Affected keys')).toBeInTheDocument()
    expect(within(drawer).queryByRole('button', { name: 'Retry run' })).not.toBeInTheDocument()
    fireEvent.click(within(drawer).getByRole('button', { name: 'Run now' }))
    const confirm = screen.getAllByRole('dialog').find(d => d !== drawer)!
    expect(confirm).toHaveTextContent('uif_echo_dev_gold_product.product')
    fireEvent.click(within(confirm).getByRole('button', { name: 'Run now' }))
    expect(triggerMutate).toHaveBeenCalledWith('product', expect.anything())
  })

  it('opens the drawer from ?run=, with Run now held while the session lock is set', () => {
    runsResult = { data: { items: [goldRun({})], truncated: false } }
    lockedRunId = 'jr_1'
    renderAt('/m/data-sync/gold/product?run=jr_1')
    expect(within(screen.getByRole('dialog')).getByRole('button', { name: 'Run now' })).toBeDisabled()
  })

  it('explains the latest failure and flags the missing source it names', () => {
    runsResult = {
      data: {
        items: [goldRun({
          status: 'FAILED', rows_written: 0, affected_keys: 0,
          error_message: 'AnalysisException: Table or view not found: silver.mtl_system_items_b',
        })],
        truncated: false,
      },
    }
    renderAt()
    expect(within(screen.getByRole('heading', { level: 1 })).getByText('Failing')).toBeInTheDocument()
    expect(screen.getByTestId('latest-failure-explain')).toHaveTextContent('A table this run needs was not found.')
    expect(screen.getByTestId('latest-failure-explain')).toHaveTextContent('pattern: missing source')
    const items = within(screen.getByTestId('sources-card')).getAllByRole('listitem')
    expect(items[0]).toHaveTextContent('uif_echo_dev_silver_mdm.mtl_system_items_b')
    expect(within(items[0]).getByText('missing')).toBeInTheDocument()
    expect(within(items[1]).queryByText('missing')).not.toBeInTheDocument()
  })

  it('shows schedule & runtime in gold terms and opens the schedule dialog (cadence and time both editable)', () => {
    renderAt()
    const card = screen.getByTestId('details-schedule')
    expect(card).toHaveTextContent('Schedule daily (gold-24h) · 06:30 UTC')
    expect(card).toHaveTextContent('Failure cooldown 30 min')
    expect(card).toHaveTextContent('G.1X × 8 incremental · G.2X × 8 full · 180 min timeout · full load every 8 runs')
    expect(card).toHaveTextContent(/next in \d+ (min|h)/)
    // the button lives in the header only
    expect(within(card).queryByRole('button')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /Change run time/ }))
    const dialog = screen.getByRole('dialog')
    // UIFPE-564: the cadence is a control in the dialog, not a read-only note
    expect(within(dialog).getByLabelText(/Cadence/)).toHaveValue('gold-24h')
    fireEvent.change(within(dialog).getByLabelText(/Start time/), { target: { value: '07:00' } })
    fireEvent.click(within(dialog).getByRole('button', { name: /Save schedule/ }))
    expect(setScheduleMutate).toHaveBeenCalledWith(
      { id: 'product', change: { start_time: '07:00' } }, expect.anything())
  })

  // ADR-0020 decision 1: ONE bookmark, read from the config row - the start of
  // the last run that processed changes. Not the newest run's start (an idle
  // run advances nothing), not the run end, never a per-source watermark.
  it('shows the row bookmark and last run completed, never per-source watermarks', () => {
    // the newest run is a later, idle one: its start is NOT the bookmark
    runsResult = { data: { items: [goldRun({ started_at: '2026-09-23T06:30:12+00:00' })], truncated: false } }
    renderAt()
    const card = screen.getByTestId('details-bookmark')
    expect(card).toHaveTextContent('Snapshot gate on')
    const shown = within(card).getByTestId('gold-bookmark')
    expect(shown).toHaveTextContent('2026-09-22T06:31:13.947992+00:00')
    expect(shown).not.toHaveTextContent('2026-09-23T06:30:12+00:00')  // newest run start
    expect(shown).not.toHaveTextContent('2026-09-22T06:33:10+00:00')  // run end
    expect(card).toHaveTextContent(/Last run completed/)
    // the per-source map is on the fixture and must not leak onto the page
    expect(card).not.toHaveTextContent('items')
    expect(card).not.toHaveTextContent('2026-09-22T05:55:00')
    expect(card).not.toHaveTextContent(/snapshot 00042/)
    expect(card).not.toHaveTextContent(/not processed yet/)
  })

  it('says the bookmark is not written yet when the row has none, whatever /gold-runs says', () => {
    golds = [{ ...PRODUCT, watermark: { silver_snapshots: PRODUCT.watermark!.silver_snapshots } }]
    runsResult = { error: gateway404 }
    const first = renderAt()
    const card = screen.getByTestId('details-bookmark')
    expect(card).toHaveTextContent(/Not written yet/)
    expect(within(card).queryByTestId('gold-bookmark')).not.toBeInTheDocument()
    first.unmount()

    // a run exists but the row still has no bookmark: the run's start is NOT shown as one
    runsResult = { data: { items: [goldRun({ started_at: '2026-09-23T06:30:12+00:00' })], truncated: false } }
    renderAt()
    const card2 = screen.getByTestId('details-bookmark')
    expect(card2).toHaveTextContent(/Not written yet/)
    expect(card2).not.toHaveTextContent('2026-09-23T06:30:12+00:00')
  })

  it('Disable sends an explicit false; Run now confirms first, then triggers by gold_id', () => {
    renderAt()
    // Disable first: once the modal is open the rest of the page is aria-hidden.
    fireEvent.click(screen.getByRole('button', { name: 'Disable' }))
    expect(toggleMutate).toHaveBeenCalledWith({ id: 'product', enabled: false })

    fireEvent.click(screen.getByRole('button', { name: 'Run now' }))
    const dialog = screen.getByRole('dialog')
    expect(dialog).toHaveTextContent('uif_echo_dev_gold_product.product')
    fireEvent.click(within(dialog).getByRole('button', { name: 'Run now' }))
    expect(triggerMutate).toHaveBeenCalledWith('product', expect.anything())
  })

  it('offers Enable as the primary action on a disabled entity, and no Run', () => {
    golds = [{ ...PRODUCT, enabled: false }]
    renderAt()
    expect(screen.getByText('Disabled')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Enable' }))
    expect(toggleMutate).toHaveBeenCalledWith({ id: 'product', enabled: true })
    expect(screen.queryByRole('button', { name: 'Run now' })).not.toBeInTheDocument()
  })

  // Owner's decision: no identity or dependencies card on this page.
  it('has no Identity or Dependencies card', () => {
    renderAt()
    expect(screen.queryByText(/^Identity$/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/^Dependencies$/i)).not.toBeInTheDocument()
  })

  it('renders a not-found state for an unknown gold_id, not a spinner', () => {
    renderAt('/m/data-sync/gold/nope')
    expect(screen.getByText('Gold entity not found.')).toBeInTheDocument()
  })
})
