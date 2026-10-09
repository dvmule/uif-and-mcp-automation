import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, within } from '@testing-library/react'
import { GoldDetailsGroups, SyncDetailsGroups } from '@/components/data-sync/detail/workspace/DetailsGroups'
import type { GoldPipelineConfig, PipelineConfig } from '@/lib/types'

vi.mock('@/lib/goldWeekly', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/goldWeekly')>()),
  browserTimeZone: () => 'UTC',
}))

const PIPELINE = {
  pipeline_id: 'p1',
  source: { secret_arn: 'arn:aws:secretsmanager:eu-west-1:1:secret:oracle-fipm-AbC123' },
  extract: { table: 'MTL_SYSTEM_ITEMS_B', mode: 'incremental', watermark_col: 'last_update_date' },
  bronze: { path: 'gs://bucket/items/' },
  silver: { enabled: true, database: 'items', table: 'mtl_system_items_b', primary_key_cols: ['inventory_item_id'] },
  schedule: { frequency: 'daily', daily_slot: 3 },
  reconcile: { enabled: true },
  glue: { worker_type: 'G.2X', num_workers: 10, timeout_minutes: 120 },
  metadata: { owner: 'raj.k@pearson.com', tags: ['items', 'tier-1'] },
} as unknown as PipelineConfig

function renderSync(over: Partial<PipelineConfig> = {}, nextRunInMin: number | null = 23, onReset = vi.fn()) {
  render(
    <SyncDetailsGroups
      pipeline={{ ...PIPELINE, ...over }}
      watermarkValue="2026-06-16 14:02:00 UTC"
      nextRunInMin={nextRunInMin}
      onReset={onReset}
    />,
  )
  return onReset
}

describe('SyncDetailsGroups', () => {
  it('source and load: connection, mode, watermark column and value, bronze path', () => {
    renderSync()
    const g = screen.getByTestId('details-source')
    expect(g).toHaveTextContent('Connection oracle-fipm · …secret:oracle-fipm-AbC123')
    expect(g).toHaveTextContent('Load incremental · watermark last_update_date · 2026-06-16 14:02:00 UTC · Reset…')
    expect(within(g).getByText('gs://bucket/items/')).toBeInTheDocument()
    expect(within(g).getByRole('button', { name: 'Copy' })).toBeInTheDocument()
  })

  it('Reset… asks the page to open the reset dialog', () => {
    const onReset = renderSync()
    fireEvent.click(screen.getByRole('button', { name: 'Reset…' }))
    expect(onReset).toHaveBeenCalled()
  })

  it('silver: table and primary key, owner and tags', () => {
    renderSync()
    const g = screen.getByTestId('details-silver')
    expect(g).toHaveTextContent('Table items.mtl_system_items_b · PK inventory_item_id')
    expect(within(g).getByText('raj.k@pearson.com')).toBeInTheDocument()
    expect(within(g).getByText('items')).toBeInTheDocument()
    expect(within(g).getByText('tier-1')).toBeInTheDocument()
  })

  it('says so when silver is not enabled, and Unassigned with no owner', () => {
    renderSync({ silver: { enabled: false, primary_key_cols: [] }, metadata: {} })
    const g = screen.getByTestId('details-silver')
    expect(g).toHaveTextContent('Silver layer not enabled')
    expect(g).toHaveTextContent('Unassigned')
  })

  it('schedule and runtime: frequency, the bronze and reconcile windows, worker, timeout and the next run', () => {
    renderSync()
    const g = screen.getByTestId('details-schedule')
    expect(g).toHaveTextContent(/Schedule daily at \d\d:\d\d UTC \(.+ ET\) next in 23 min/)
    expect(g).toHaveTextContent(/Reconcile window \d\d:\d\d UTC \(.+ ET\)/)
    expect(g).toHaveTextContent('Glue G.2X × 10 · 120 min timeout')
  })

  it('reads On demand without a schedule, and no next-run pill without a next run', () => {
    renderSync({ schedule: undefined as unknown as PipelineConfig['schedule'] }, null)
    const g = screen.getByTestId('details-schedule')
    expect(g).toHaveTextContent('Schedule On demand')
    expect(g).not.toHaveTextContent('next in')
  })
})

const GOLD: GoldPipelineConfig = {
  gold_id: 'product', enabled: true,
  schedule: { frequency: 'gold-24h', start_time: '06:30', failure_cooldown_minutes: 30, recompute: { every_n_runs: 8 } },
  glue: { worker_type: 'G.1X', num_workers: 8, timeout_minutes: 180, full_load: { worker_type: 'G.2X', num_workers: 8, timeout_minutes: 180 } },
  metadata: { last_run_at: '2026-09-22T06:33:10+00:00' },
  watermark: { bookmark: '2026-09-22T06:31:13.947992+00:00' },
  snapshot_gate: false,
}

describe('GoldDetailsGroups', () => {
  it('schedule and runtime in gold terms', () => {
    render(<GoldDetailsGroups gold={GOLD} nextRunInMin={90} />)
    const g = screen.getByTestId('details-schedule')
    expect(g).toHaveTextContent('Schedule daily (gold-24h) · 06:30 UTC next in 2 h')
    expect(g).toHaveTextContent('Failure cooldown 30 min')
    expect(g).toHaveTextContent('Glue G.1X × 8 incremental · G.2X × 8 full · 180 min timeout · full load every 8 runs')
  })

  it('a weekly entity shows its run days', () => {
    render(<GoldDetailsGroups gold={{ ...GOLD, schedule: { ...GOLD.schedule, frequency: 'gold-weekly', weekly_days: ['MON', 'THU'] } }} nextRunInMin={null} />)
    expect(screen.getByTestId('details-schedule')).toHaveTextContent('Schedule weekly (gold-weekly) · Mon, Thu · 06:30 UTC')
  })

  it('the bookmark group: gate, the one bookmark and last run completed', () => {
    render(<GoldDetailsGroups gold={GOLD} nextRunInMin={null} />)
    const g = screen.getByTestId('details-bookmark')
    expect(g).toHaveTextContent('Snapshot gate off')
    expect(within(g).getByTestId('gold-bookmark')).toHaveTextContent('2026-09-22T06:31:13.947992+00:00')
    expect(g).toHaveTextContent(/Last run completed .+ ago/)
    expect(screen.getByText(/One bookmark per table/)).toBeInTheDocument()
  })
})
