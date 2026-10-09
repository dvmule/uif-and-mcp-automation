import { describe, it, expect } from 'vitest'
import { render, screen, fireEvent, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { TopTablesCard } from '../TopTablesCard'
import { HOUR_MS } from '@/lib/activity'
import type { RunRow } from '@/lib/runsTable'

const NOW = Date.UTC(2026, 8, 29, 12, 0, 0)
const ago = (h: number) => new Date(NOW - h * HOUR_MS).toISOString()

function row(object: string, status: string, h: number, rowsWritten: number, over: Partial<RunRow> = {}): RunRow {
  return {
    key: `${object}-${status}-${h}`,
    runId: `${object}-${h}`,
    object,
    objectHref: `/m/data-sync/syncs/${object}`,
    layer: 'bronze',
    kind: 'Incremental',
    status,
    startedAt: ago(h),
    durationSec: 1,
    rowsRead: null,
    rowsWritten,
    run: {} as RunRow['run'],
    pipelineId: object,
    ...over,
  }
}

const ROWS = [
  row('FIPM.X', 'SUCCEEDED', 2, 1300),
  row('OM.STOCK', 'SUCCEEDED', 1, 400_000),
  row('OM.STOCK', 'SUCCEEDED', 3, 12_000),
  row('EDS.PRICE', 'FAILED', 4, 0),
  row('OLD.ONE', 'SUCCEEDED', 72, 9_000_000),
  row('NOW.RUNNING', 'RUNNING', 0.1, 50),
]

function renderCard(rows = ROWS) {
  return render(
    <MemoryRouter>
      <TopTablesCard rows={rows} now={NOW} />
    </MemoryRouter>,
  )
}

const objectLinks = () => within(screen.getByTestId('top-tables')).getAllByRole('link').map((a) => a.textContent)

describe('TopTablesCard', () => {
  it('ranks finished runs in the last 24 hours by rows, linking each object', () => {
    renderCard()
    expect(screen.getByRole('heading', { name: 'Top tables by rows' })).toBeInTheDocument()
    expect(objectLinks()).toEqual(['OM.STOCK', 'FIPM.X', 'EDS.PRICE'])
    expect(screen.getByRole('link', { name: 'OM.STOCK' })).toHaveAttribute('href', '/m/data-sync/syncs/OM.STOCK')
    expect(screen.getByText('412K rows')).toBeInTheDocument()
    expect(screen.getByText('1.3K rows')).toBeInTheDocument()
    expect(screen.getByText('0 rows')).toBeInTheDocument()
  })

  it('splits each bar by outcome against the biggest, and a 0-row failure still shows', () => {
    renderCard()
    const bar = (name: string) => screen.getByRole('link', { name }).nextElementSibling as HTMLElement
    expect(bar('OM.STOCK')).toHaveAttribute('title', '2 succeeded · 0 warning · 0 failed')
    expect(bar('OM.STOCK').querySelector('.bg-dot-ok')).toHaveStyle({ width: '100%' })
    const fail = bar('EDS.PRICE')
    expect(fail).toHaveAttribute('title', '0 succeeded · 0 warning · 1 failed')
    expect(fail.querySelector('.bg-dot-fail')).toHaveStyle({ width: '3%' })
    expect(fail.querySelector('.bg-dot-ok')).toHaveStyle({ width: '0%' })
  })

  it('its own window widens to 7 days', () => {
    renderCard()
    fireEvent.change(screen.getByRole('combobox', { name: 'Top tables window' }), { target: { value: '7d' } })
    expect(objectLinks()[0]).toBe('OLD.ONE')
    expect(screen.getByText('9M rows')).toBeInTheDocument()
  })

  it('an object without a page is plain text', () => {
    renderCard([row('LOOSE', 'SUCCEEDED', 1, 5, { objectHref: null })])
    expect(screen.getByText('LOOSE').tagName).toBe('SPAN')
    expect(within(screen.getByTestId('top-tables')).queryByRole('link')).toBeNull()
  })

  it('says so when nothing finished in the window, and keeps the legend', () => {
    renderCard([row('NOW.RUNNING', 'RUNNING', 0.1, 50)])
    expect(screen.getByText('No finished runs in this window.')).toBeInTheDocument()
    expect(screen.getByText('Succeeded')).toBeInTheDocument()
    expect(screen.getByText('Failed')).toBeInTheDocument()
  })
})
