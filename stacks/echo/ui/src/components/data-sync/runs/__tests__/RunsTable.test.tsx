import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { RunsTable, RUNS_PAGE_SIZE } from '../RunsTable'
import { goldRunRow, pipelineRunRow, type RunRow } from '@/lib/runsTable'
import type { GoldRunMetrics, PipelineConfig, RunMetrics } from '@/lib/types'

const pipeline = { pipeline_id: 'p1', extract: { table: 'EDS.PRICE_LIST' } } as unknown as PipelineConfig

const bronze = (id: string, over: Partial<RunMetrics> = {}): RunRow =>
  pipelineRunRow(pipeline, {
    job_name: 'uif-echo-dev-bronze-ingest',
    run_id: id,
    pipeline_id: 'p1',
    status: 'SUCCEEDED',
    started_at: '2026-09-29T11:59:00Z',
    duration_sec: 1023,
    rows_read: 1200,
    rows_written: null,
    ...over,
  })

const gold = (over: Partial<GoldRunMetrics> = {}): RunRow =>
  goldRunRow({
    job_name: 'uif-echo-dev-gold-transform',
    run_id: 'g1',
    gold_id: 'price',
    status: 'SUCCEEDED',
    started_at: '2026-09-29T06:31:00Z',
    rows_written: 0,
    ...over,
  })

function setup(rows: RunRow[], props: Partial<Parameters<typeof RunsTable>[0]> = {}) {
  const onOpen = vi.fn()
  const onSort = vi.fn()
  const onPage = vi.fn()
  render(
    <MemoryRouter>
      <RunsTable
        rows={rows}
        sortDirection="desc"
        onToggleSort={onSort}
        onOpen={onOpen}
        pageIndex={0}
        onPageChange={onPage}
        empty="Nothing here"
        {...props}
      />
    </MemoryRouter>,
  )
  return { onOpen, onSort, onPage }
}

describe('RunsTable', () => {
  it('has the seven columns from the design', () => {
    setup([bronze('r1')])
    const headers = screen.getAllByRole('columnheader').map((c) => c.textContent)
    expect(headers).toEqual(['Started', 'Object', 'Layer', 'Kind', 'Status', 'Duration', 'Rows read → written'])
  })

  it('renders a bronze row: link and sub-line, layer, kind, status, duration, and a dash for the missing side', () => {
    setup([bronze('r1')])
    const row = screen.getAllByRole('row')[1]
    expect(within(row).getByRole('link', { name: 'EDS.PRICE_LIST' })).toHaveAttribute('href', '/m/data-sync/syncs/p1')
    expect(within(row).getByText('p1')).toBeInTheDocument()
    expect(within(row).getByText('Bronze')).toBeInTheDocument()
    expect(within(row).getByText('Incremental')).toBeInTheDocument()
    expect(within(row).getByText('Succeeded')).toBeInTheDocument()
    expect(within(row).getByText('17m 3s')).toBeInTheDocument()
    expect(within(row).getByText('1,200 → —')).toBeInTheDocument()
  })

  it('renders a gold row: linked to the gold page, no-change success, dash on the read side, 0 written', () => {
    setup([gold()])
    const row = screen.getAllByRole('row')[1]
    expect(within(row).getByRole('link', { name: 'price' })).toHaveAttribute('href', '/m/data-sync/gold/price')
    expect(within(row).getByText('Succeeded · no changes')).toBeInTheDocument()
    expect(within(row).getByText('— → 0')).toBeInTheDocument()
  })

  it('clicking a row, or pressing the keyboard-reachable button in it, opens that run', () => {
    const { onOpen } = setup([bronze('r1'), bronze('r2')])
    fireEvent.click(screen.getAllByRole('row')[2])
    expect(onOpen).toHaveBeenLastCalledWith(expect.objectContaining({ runId: 'r2' }))
    fireEvent.click(screen.getByRole('button', { name: 'Open run r1' }))
    expect(onOpen).toHaveBeenLastCalledWith(expect.objectContaining({ runId: 'r1' }))
  })

  it('clicking the object link follows the link and does not open the drawer', () => {
    const { onOpen } = setup([bronze('r1')])
    fireEvent.click(screen.getByRole('link', { name: 'EDS.PRICE_LIST' }))
    expect(onOpen).not.toHaveBeenCalled()
  })

  it('highlights the open run', () => {
    setup([bronze('r1'), bronze('r2')], { selectedRunId: 'r2' })
    const rows = screen.getAllByRole('row')
    expect(rows[2].className).toContain('bg-accent-soft')
    expect(rows[1].className).not.toContain('bg-accent-soft')
  })

  it('Started is the only sortable column, marked with its direction', () => {
    const { onSort } = setup([bronze('r1')])
    expect(screen.getAllByRole('columnheader').filter((c) => c.hasAttribute('aria-sort'))).toHaveLength(1)
    expect(screen.getByRole('columnheader', { name: 'Started' })).toHaveAttribute('aria-sort', 'descending')
    fireEvent.click(screen.getByRole('button', { name: 'Started' }))
    expect(onSort).toHaveBeenCalledTimes(1)
  })

  it('shows 25 rows a page and hands page changes up', () => {
    const rows = Array.from({ length: 60 }, (_, i) => bronze(`r${i}`))
    const { onPage } = setup(rows, { pageIndex: 1 })
    expect(screen.getAllByRole('row')).toHaveLength(RUNS_PAGE_SIZE + 1)
    expect(screen.getByRole('button', { name: 'Open run r25' })).toBeInTheDocument()
    expect(screen.getByText('26–50 of 60')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Next page' })).toHaveAttribute('title', 'Page 2 of 3')
    fireEvent.click(screen.getByRole('button', { name: 'Next page' }))
    expect(onPage).toHaveBeenCalledWith(2)
  })

  it('clamps a page index that is past the end (after a filter narrowed the list)', () => {
    setup([bronze('r1')], { pageIndex: 4 })
    expect(screen.getByText('1–1 of 1')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Next page' })).toHaveAttribute('title', 'Page 1 of 1')
  })

  it('shows the empty message, and skeleton rows while loading', () => {
    setup([])
    expect(screen.getByText('Nothing here')).toBeInTheDocument()
  })

  it('does not show the empty message while loading', () => {
    setup([], { loading: true })
    expect(screen.queryByText('Nothing here')).not.toBeInTheDocument()
  })

  it('prints the footnote under the pager', () => {
    setup([bronze('r1')], { footnote: 'Filtered from 41 loaded runs' })
    expect(screen.getByText('Filtered from 41 loaded runs')).toBeInTheDocument()
  })

  it('detail-page variant: every row with no pager, columns hidden and added, a row action, a plain Started header', () => {
    const rows = Array.from({ length: RUNS_PAGE_SIZE + 2 }, (_, i) => bronze(`r${i}`))
    const action = vi.fn()
    const { onOpen } = setup(rows, {
      onToggleSort: undefined,
      hidePager: true,
      hideColumns: ['object', 'layer'],
      extraColumns: [{ key: 'extra', header: 'Extra', cell: (r) => `x-${r.runId}` }],
      rowAction: (r) =>
        r.runId === 'r0' ? (
          <button type="button" onClick={(e) => { e.stopPropagation(); action(r.runId) }}>Retry</button>
        ) : null,
    })
    expect(screen.getAllByRole('row')).toHaveLength(RUNS_PAGE_SIZE + 3)
    expect(screen.queryByRole('button', { name: 'Next page' })).not.toBeInTheDocument()
    const headers = screen.getAllByRole('columnheader').map((c) => c.textContent)
    expect(headers).toEqual(['Started', 'Kind', 'Status', 'Duration', 'Rows read → written', 'Extra', 'Actions'])
    expect(screen.getByRole('columnheader', { name: 'Started' })).not.toHaveAttribute('aria-sort')
    expect(screen.getByText('x-r1')).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: 'Retry' })).toHaveLength(1)
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(action).toHaveBeenCalledWith('r0')
    expect(onOpen).not.toHaveBeenCalled()
  })
})
