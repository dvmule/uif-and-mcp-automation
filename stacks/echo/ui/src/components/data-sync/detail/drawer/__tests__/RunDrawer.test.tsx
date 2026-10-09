import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { RunDrawer } from '@/components/data-sync/detail/drawer/RunDrawer'
import { useRunDrawerParam } from '@/components/data-sync/detail/drawer/useRunDrawerParam'
import { goldRunRow, pipelineRunRow, type RunRow } from '@/lib/runsTable'
import type { GoldPipelineConfig, GoldRunMetrics, PipelineConfig, RunMetrics } from '@/lib/types'

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const pipeline = { pipeline_id: 'p-1', extract: { table: 'EDS.PRICE_LIST' } } as unknown as PipelineConfig
const gold = { gold_id: 'price', target: { table: 'gold_price' } } as GoldPipelineConfig

const failedRun: RunMetrics = {
  job_name: 'uif-echo-dev-bronze-ingest-eds',
  pipeline_id: 'p-1',
  run_id: 'r-fail',
  status: 'FAILED',
  started_at: '2026-06-17T13:02:00Z',
  completed_at: '2026-06-17T13:19:03Z',
  duration_sec: 1023,
  rows_read: 0,
  rows_written: 0,
  new_watermark: null,
  phase: 'incremental',
  error_message: 'ORA-01017: invalid username/password',
}

const okGold: GoldRunMetrics = {
  job_name: 'uif-echo-dev-gold-transform',
  run_id: 'jr_gold',
  gold_id: 'price',
  status: 'SUCCEEDED',
  started_at: '2026-06-17T06:31:00Z',
  duration_sec: 540,
  rows_read: 500,
  rows_written: 0,
  affected_keys: 12,
}

const failedRow = () => pipelineRunRow(pipeline, failedRun)
const goldRow = () => goldRunRow(okGold, gold)

function Probe() {
  const loc = useLocation()
  return <div data-testid="search">{loc.search}</div>
}

/** Mirrors a page: the drawer opens for the run named in ?run=. */
function Host({
  rows,
  onRetry,
  retryDisabled,
  onRunNow,
  runNowDisabled,
}: {
  rows: RunRow[]
  onRetry?: (r: RunRow) => void
  retryDisabled?: boolean
  onRunNow?: (r: RunRow) => void
  runNowDisabled?: boolean
}) {
  const { runId } = useRunDrawerParam()
  const row = runId ? (rows.find((r) => r.runId === runId) ?? null) : null
  return (
    <>
      <RunDrawer
        row={row}
        onRetry={onRetry}
        retryDisabled={retryDisabled}
        onRunNow={onRunNow}
        runNowDisabled={runNowDisabled}
      />
      <Probe />
    </>
  )
}

function renderAt(search: string, props: Parameters<typeof Host>[0]) {
  return render(
    <MemoryRouter initialEntries={[`/m/data-sync/runs${search}`]}>
      <Host {...props} />
    </MemoryRouter>,
  )
}

describe('RunDrawer', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('stays closed with no ?run= and renders nothing', () => {
    renderAt('', { rows: [failedRow()] })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('opens for the run in the URL, with a linked object, the run id and a status badge', () => {
    renderAt('?run=r-fail', { rows: [failedRow()] })
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByRole('link', { name: 'EDS.PRICE_LIST' })).toHaveAttribute(
      'href',
      '/m/data-sync/syncs/p-1',
    )
    expect(within(dialog).getByText('r-fail')).toBeInTheDocument()
    expect(within(dialog).getAllByText('Failed').length).toBeGreaterThan(0)
  })

  it('explains a failure in plain words and keeps the raw error, open, under Technical detail', () => {
    renderAt('?run=r-fail', { rows: [failedRow()] })
    expect(screen.getByTestId('run-explain')).toHaveTextContent(/refused the sync's login/)
    expect(screen.getByTestId('run-explain')).toHaveTextContent(/pattern: ORA-01017/)
    expect(screen.getByText('Technical detail')).toBeInTheDocument()
    const raw = screen.getByText('ORA-01017: invalid username/password')
    expect(raw.closest('details')).toHaveAttribute('open')
  })

  it('shows the four sections and only fields the run carries', () => {
    renderAt('?run=r-fail', { rows: [failedRow()] })
    for (const heading of ['What happened', 'Properties', 'Results', 'Technical detail']) {
      expect(screen.getByRole('heading', { name: heading })).toBeInTheDocument()
    }
    expect(screen.getByText('17 Jun 2026, 13:02 UTC')).toBeInTheDocument()
    expect(screen.getByText('13:19 UTC · 17m 3s')).toBeInTheDocument()
    expect(screen.getByText('uif-echo-dev-bronze-ingest-eds')).toBeInTheDocument()
    expect(screen.getByText('unchanged')).toBeInTheDocument()
    // no delivery fields on this run, so no Delivery section
    expect(screen.queryByRole('heading', { name: 'Delivery' })).not.toBeInTheDocument()
  })

  it('has none of the old placeholders', () => {
    renderAt('?run=r-fail', { rows: [failedRow()] })
    expect(screen.queryByText(/Worker profile/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Watermark before/)).not.toBeInTheDocument()
    expect(screen.queryByText(/View full log/)).not.toBeInTheDocument()
  })

  it('adds a Delivery section when the run carries replication fields', () => {
    const row = pipelineRunRow(pipeline, {
      ...failedRun,
      run_id: 'r-rep',
      status: 'SUCCEEDED',
      error_message: null,
      replication_status: 'replicated',
      bq_status: 'loaded',
      files_replicated: 4,
      bytes_replicated: 2048,
    })
    renderAt('?run=r-rep', { rows: [row] })
    expect(screen.getByRole('heading', { name: 'Delivery' })).toBeInTheDocument()
    expect(screen.getByText('replicated')).toBeInTheDocument()
    expect(screen.getByText('2.0 KB')).toBeInTheDocument()
  })

  it('Retry run calls onRetry with the row, and is disabled when told to be', () => {
    const onRetry = vi.fn()
    const { unmount } = renderAt('?run=r-fail', { rows: [failedRow()], onRetry })
    fireEvent.click(screen.getByRole('button', { name: 'Retry run' }))
    expect(onRetry).toHaveBeenCalledTimes(1)
    expect(onRetry.mock.calls[0][0].runId).toBe('r-fail')
    unmount()

    const off = vi.fn()
    renderAt('?run=r-fail', { rows: [failedRow()], onRetry: off, retryDisabled: true })
    const button = screen.getByRole('button', { name: 'Retry run' })
    expect(button).toBeDisabled()
    fireEvent.click(button)
    expect(off).not.toHaveBeenCalled()
  })

  it('offers no Retry without an onRetry handler', () => {
    renderAt('?run=r-fail', { rows: [failedRow()] })
    expect(screen.queryByRole('button', { name: 'Retry run' })).not.toBeInTheDocument()
  })

  it('a gold run has no Retry, shows affected keys instead of a watermark, and opens the gold table', () => {
    renderAt('?run=jr_gold', { rows: [goldRow()], onRetry: vi.fn() })
    expect(screen.queryByRole('button', { name: 'Retry run' })).not.toBeInTheDocument()
    expect(screen.getByText('Affected keys')).toBeInTheDocument()
    expect(screen.queryByText('New watermark')).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Open gold table' })).toHaveAttribute('href', '/m/data-sync/gold/price')
    expect(screen.getByTestId('run-explain')).toHaveTextContent(/No rows changed/)
    // a clean gold run has nothing to show under Technical detail
    expect(screen.queryByText('Technical detail')).not.toBeInTheDocument()
  })

  it('a gold run offers Run now instead of Retry, calls onRunNow with the row, and honours runNowDisabled', () => {
    const onRunNow = vi.fn()
    const onRetry = vi.fn()
    const { unmount } = renderAt('?run=jr_gold', { rows: [goldRow()], onRetry, onRunNow })
    expect(screen.queryByRole('button', { name: 'Retry run' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Run now' }))
    expect(onRunNow).toHaveBeenCalledTimes(1)
    expect(onRunNow.mock.calls[0][0].goldId).toBe('price')
    expect(onRetry).not.toHaveBeenCalled()
    unmount()

    const off = vi.fn()
    renderAt('?run=jr_gold', { rows: [goldRow()], onRunNow: off, runNowDisabled: true })
    const button = screen.getByRole('button', { name: 'Run now' })
    expect(button).toBeDisabled()
    fireEvent.click(button)
    expect(off).not.toHaveBeenCalled()
  })

  it('a pipeline run never shows Run now, even when onRunNow is passed', () => {
    renderAt('?run=r-fail', { rows: [failedRow()], onRetry: vi.fn(), onRunNow: vi.fn() })
    expect(screen.getByRole('button', { name: 'Retry run' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Run now' })).not.toBeInTheDocument()
  })

  it('links to the sync from the footer', () => {
    renderAt('?run=r-fail', { rows: [failedRow()] })
    expect(screen.getByRole('link', { name: 'Open sync' })).toHaveAttribute('href', '/m/data-sync/syncs/p-1')
  })

  it('Copy link copies the page address', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.assign(navigator, { clipboard: { writeText } })
    renderAt('?run=r-fail', { rows: [failedRow()] })
    fireEvent.click(screen.getByRole('button', { name: 'Copy link' }))
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(window.location.href))
  })

  it('Copy on the run id copies the id', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.assign(navigator, { clipboard: { writeText } })
    renderAt('?run=r-fail', { rows: [failedRow()] })
    fireEvent.click(screen.getByRole('button', { name: 'Copy run id' }))
    await waitFor(() => expect(writeText).toHaveBeenCalledWith('r-fail'))
  })

  it('the footer Close removes ?run= and keeps every other query key', () => {
    renderAt('?status=FAILED&run=r-fail', { rows: [failedRow()] })
    const dialog = screen.getByRole('dialog')
    fireEvent.click(within(dialog).getAllByRole('button', { name: /^Close$/ }).at(-1)!)
    expect(screen.getByTestId('search').textContent).toBe('?status=FAILED')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('Escape closes it', async () => {
    const user = userEvent.setup()
    renderAt('?run=r-fail', { rows: [failedRow()] })
    await user.keyboard('{Escape}')
    await waitFor(() => expect(screen.getByTestId('search').textContent).toBe(''))
  })
})
