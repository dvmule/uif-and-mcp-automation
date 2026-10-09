import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import { ActivityChart } from '../ActivityChart'
import { DAY_MS, HOUR_MS, buckets } from '@/lib/activity'
import type { RunRow } from '@/lib/runsTable'

const NOW = Date.UTC(2026, 8, 29, 12, 0, 0)
const ago = (h: number) => new Date(NOW - h * HOUR_MS).toISOString()

function row(status: string, h: number, rowsWritten: number | null = 10): RunRow {
  return {
    key: `${status}-${h}`,
    runId: `${status}-${h}`,
    object: 'INV.A',
    objectHref: '/m/data-sync/syncs/a',
    layer: 'bronze',
    kind: 'Incremental',
    status,
    startedAt: ago(h),
    durationSec: 1,
    rowsRead: null,
    rowsWritten,
    run: {} as RunRow['run'],
    pipelineId: 'a',
  }
}

const day = (rows: RunRow[]) => buckets(rows, NOW - DAY_MS, 24, HOUR_MS)

describe('ActivityChart', () => {
  it('draws one accent bar per bucket, a faint stub for an empty one, and a hover title', () => {
    const { container } = render(<ActivityChart buckets={day([row('SUCCEEDED', 0.5, 412_000)])} window="24h" />)
    const rects = container.querySelectorAll('rect')
    expect(rects).toHaveLength(24)
    expect(rects[23]).toHaveAttribute('fill', 'var(--color-accent)')
    expect(rects[23]).toHaveAttribute('opacity', '1')
    expect(rects[23].querySelector('title')?.textContent).toMatch(/^\d\d:00 · 412K rows$/)
    expect(rects[0]).toHaveAttribute('height', '2')
    expect(rects[0]).toHaveAttribute('opacity', '0.25')
  })

  it('stacks runs by outcome in token colours, with the counts in the title', () => {
    const { container } = render(
      <ActivityChart
        buckets={day([row('SUCCEEDED', 0.5), row('SUCCEEDED', 0.4), row('WARNING', 0.3), row('FAILED', 0.2)])}
        window="24h"
        stacked
      />,
    )
    const last = [...container.querySelectorAll('rect')].filter((r) => r.getAttribute('opacity') !== '0.25')
    expect(last.map((r) => r.getAttribute('fill'))).toEqual([
      'var(--color-dot-ok)',
      'var(--color-signal-warn-line)',
      'var(--color-dot-fail)',
    ])
    expect(last[0].querySelector('title')?.textContent).toMatch(/· 2 succeeded · 1 warning · 1 failed$/)
    expect(container.querySelectorAll('rect[opacity="0.25"]')).toHaveLength(23)
  })

  it('uses only token colours and is hidden from assistive tech', () => {
    const { container } = render(<ActivityChart buckets={day([row('FAILED', 1)])} window="24h" stacked />)
    expect(container.querySelector('svg')).toHaveAttribute('aria-hidden', 'true')
    for (const el of container.querySelectorAll('rect, line')) {
      const colour = el.getAttribute('fill') ?? el.getAttribute('stroke') ?? ''
      expect(colour).toMatch(/^var\(--color-/)
    }
  })

  it('labels the axis with hours ending in "now", or days for 7d', () => {
    const { container, rerender } = render(<ActivityChart buckets={day([])} window="24h" />)
    const ticks = () => [...container.querySelectorAll('svg + div span')].map((s) => s.textContent)
    expect(ticks()).toHaveLength(5)
    expect(ticks()[4]).toBe('now')
    expect(ticks()[0]).toMatch(/^\d\d:00$/)
    rerender(<ActivityChart buckets={buckets([], NOW - 7 * DAY_MS, 7, DAY_MS)} window="7d" />)
    expect(ticks()).toHaveLength(5)
    for (const t of ticks()) expect(t).toMatch(/^(Sun|Mon|Tue|Wed|Thu|Fri|Sat) \d{1,2}$/)
  })
})
