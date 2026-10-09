import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { ObjectsTable } from '../ObjectsTable'
import { RUNS_PAGE_SIZE } from '../RunsTable'
import { goldRunRow, groupRunsByObject, pipelineRunRow, type ObjectGroup, type RunRow } from '@/lib/runsTable'
import type { GoldPipelineConfig, GoldRunMetrics, PipelineConfig, RunMetrics } from '@/lib/types'

const NOW = new Date('2026-09-29T13:55:00Z')

const pipelines = [
  { pipeline_id: 'p1', enabled: true, extract: { table: 'EDS.PRICE_LIST' }, schedule: { frequency: 'hourly' } },
  { pipeline_id: 'off', enabled: false, extract: { table: 'SIS.STUDENT' }, schedule: { frequency: 'hourly' } },
] as unknown as PipelineConfig[]
const golds = [
  { gold_id: 'price', enabled: true, schedule: { frequency: 'gold-24h', start_time: '06:30' } },
] as unknown as GoldPipelineConfig[]

const bronze = (id: string, status: string, startedAt: string, pipelineId = 'p1', layer = 'bronze'): RunRow =>
  pipelineRunRow(pipelines.find((p) => p.pipeline_id === pipelineId)!, {
    job_name: `uif-echo-dev-${layer}-${layer === 'bronze' ? 'ingest' : 'transform'}`,
    run_id: id,
    pipeline_id: pipelineId,
    status,
    started_at: startedAt,
  } as RunMetrics)

const gold = (id: string, status: string, startedAt: string): RunRow =>
  goldRunRow({ job_name: 'uif-echo-dev-gold-transform', run_id: id, gold_id: 'price', status, started_at: startedAt } as GoldRunMetrics)

function setup(groups: ObjectGroup[], props: Partial<Parameters<typeof ObjectsTable>[0]> = {}) {
  const onPage = vi.fn()
  render(
    <MemoryRouter>
      <ObjectsTable
        groups={groups}
        pipelines={pipelines}
        golds={golds}
        now={NOW}
        pageIndex={0}
        onPageChange={onPage}
        empty="No objects match these filters"
        {...props}
      />
    </MemoryRouter>,
  )
  return { onPage }
}

const bodyRows = () => within(screen.getByRole('table', { name: 'Objects' })).getAllByRole('row').slice(1)

describe('ObjectsTable', () => {
  const groups = groupRunsByObject([
    bronze('b1', 'SUCCEEDED', '2026-09-29T11:00:00Z'),
    bronze('s1', 'FAILED', '2026-09-29T12:00:00Z', 'p1', 'silver'),
    bronze('b2', 'RUNNING', '2026-09-29T13:45:00Z'),
    gold('g1', 'SUCCEEDED', '2026-09-29T06:31:00Z'),
    bronze('x1', 'SKIPPED', '2026-09-29T05:00:00Z', 'off'),
  ])

  it('has the six columns, Last 5 runs explaining its order', () => {
    setup(groups)
    const headers = screen.getAllByRole('columnheader').map((h) => h.textContent)
    expect(headers).toEqual(['Object', 'Schedule', 'Last 5 runs', 'Success', 'Next run', 'Actions'])
    expect(screen.getByText('Last 5 runs')).toHaveAttribute('title', 'Oldest on the left, the latest run ringed on the right')
  })

  it('one row per object, most recently run first, linking to its page with its layers', () => {
    setup(groups)
    const rows = bodyRows()
    expect(rows).toHaveLength(3)
    const link = within(rows[0]).getByRole('link', { name: 'EDS.PRICE_LIST' })
    expect(link).toHaveAttribute('href', '/m/data-sync/syncs/p1')
    expect(link).toHaveClass('font-semibold', 'text-ink', 'hover:text-accent')
    expect(rows[0]).toHaveTextContent('bronze · silver')
    expect(within(rows[0]).getByRole('link', { name: 'Open EDS.PRICE_LIST' })).toHaveAttribute('href', '/m/data-sync/syncs/p1')
    expect(within(rows[1]).getByRole('link', { name: 'price' })).toHaveAttribute('href', '/m/data-sync/gold/price')
  })

  it('schedule pill, disabled marked, and no next run for a disabled object', () => {
    setup(groups)
    const [sync, goldRow, off] = bodyRows()
    expect(within(sync).getByText('hourly')).toHaveClass('bg-paper-head')
    expect(sync).toHaveTextContent('14:00 UTC · in 5 min')
    expect(goldRow).toHaveTextContent('Tomorrow 06:30 UTC')
    expect(within(off).getByText('disabled')).toBeInTheDocument()
    expect(within(off).getAllByText('—').length).toBeGreaterThan(0)
  })

  it('success share with its numbers in the title, a dash with no finished runs', () => {
    setup(groups)
    const [sync, goldRow, off] = bodyRows()
    expect(within(sync).getByText('50%')).toHaveAttribute('title', '1 of 2 finished runs')
    expect(within(goldRow).getByText('100%')).toHaveAttribute('title', '1 of 1 finished run')
    expect(within(off).getByTitle('no finished runs in range')).toHaveTextContent('—')
  })

  it('the run dots, and how long the latest run has been going', () => {
    setup(groups)
    const sync = bodyRows()[0]
    expect(within(sync).getByRole('img')).toHaveAttribute(
      'aria-label',
      'Last 3 runs, oldest first: Succeeded, Failed, Running',
    )
    expect(within(sync).getByText('In progress · 10 min')).toHaveClass('text-signal-live')
    expect(bodyRows()[1]).not.toHaveTextContent('In progress')
  })

  it('pages 25 objects at a time and shows the count line under the pager', () => {
    const many = groupRunsByObject(
      Array.from({ length: 30 }, (_, i) =>
        goldRunRow({ job_name: 'g', run_id: `r${i}`, gold_id: `g${i}`, status: 'SUCCEEDED', started_at: '2026-09-29T06:31:00Z' } as GoldRunMetrics),
      ),
    )
    const { onPage } = setup(many, { footnote: '30 objects' })
    expect(bodyRows()).toHaveLength(RUNS_PAGE_SIZE)
    expect(screen.getByText('1–25 of 30')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Next page' }))
    expect(onPage).toHaveBeenCalledWith(1)
    expect(screen.getByText('30 objects')).toBeInTheDocument()
  })

  it('no groups shows the empty message', () => {
    setup([])
    expect(screen.getByText('No objects match these filters')).toBeInTheDocument()
  })

  it('loading shows shimmer rows, not the empty message', () => {
    setup([], { loading: true })
    expect(screen.queryByText('No objects match these filters')).not.toBeInTheDocument()
  })
})
