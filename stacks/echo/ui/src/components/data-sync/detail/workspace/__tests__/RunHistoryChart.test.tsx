import { describe, it, expect, vi } from 'vitest'
import { render, act } from '@testing-library/react'
import { RunHistoryChart } from '@/components/data-sync/detail/workspace/RunHistoryChart'
import type { RunMetrics } from '@/lib/types'

const runs: RunMetrics[] = Array.from({ length: 8 }, (_, i) => ({
  job_name: 'job-x',
  pipeline_id: 'p-1',
  run_id: `r${i}`,
  status: i === 2 ? 'FAILED' : 'SUCCEEDED',
  started_at: new Date(Date.now() - i * 3600_000).toISOString(),
  duration_sec: 180 + i * 5,
  rows_extracted: 2_000_000 + i * 10_000,
  new_watermark: '2026-06-17T13:00:00',
}))

// Recharts renders each Bar Cell as a <path data-status="..."> (or <rect>
// when radius=0). The Cell forwards data-status onto whatever shape recharts
// chose. The selector below tolerates both.
const BAR_SELECTOR = '[data-status]'

describe('RunHistoryChart', () => {
  it('renders one bar per run with status-colored fill', () => {
    render(<RunHistoryChart runs={runs} onBarClick={vi.fn()} />)
    const bars = document.querySelectorAll(BAR_SELECTOR)
    expect(bars.length).toBe(runs.length)
    // data is reversed (oldest left → newest right). The FAILED run was index 2
    // in the original (newest-first) array; after .reverse() it lands at
    // index (length - 1 - 2) = 5 in the chart data.
    expect(bars[5].getAttribute('data-status')).toBe('FAILED')
  })

  it('calls onBarClick with the run id', () => {
    const onBarClick = vi.fn()
    render(<RunHistoryChart runs={runs} onBarClick={onBarClick} />)
    const bars = document.querySelectorAll(BAR_SELECTOR)
    act(() => {
      bars[0].dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })
    expect(onBarClick).toHaveBeenCalledTimes(1)
    expect(onBarClick.mock.calls[0][0]).toMatch(/^r\d$/)
  })
})
