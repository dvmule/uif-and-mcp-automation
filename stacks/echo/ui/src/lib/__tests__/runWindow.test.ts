import { describe, it, expect } from 'vitest'
import { RUN_WINDOW_LABEL, runWindowCaption, runsInWindow } from '../runWindow'

const NOW = new Date('2026-09-29T12:00:00Z')

describe('runWindow', () => {
  const runs = [
    { run_id: 'a', started_at: '2026-09-29T06:00:00Z' },
    { run_id: 'b', started_at: '2026-09-25T06:00:00Z' },
    { run_id: 'c', started_at: '2026-08-01T06:00:00Z' },
    { run_id: 'd', started_at: null },
  ]

  it('keeps the runs that started inside the window', () => {
    expect(runsInWindow(runs, '24h', NOW).map((r) => r.run_id)).toEqual(['a'])
    expect(runsInWindow(runs, '7d', NOW).map((r) => r.run_id)).toEqual(['a', 'b'])
    expect(runsInWindow(runs, '90d', NOW).map((r) => r.run_id)).toEqual(['a', 'b', 'c'])
  })

  it('names the windows in words and captions the count', () => {
    expect(Object.values(RUN_WINDOW_LABEL)).toEqual(['24 hours', '7 days', '30 days', '90 days'])
    expect(runWindowCaption('30d', 17)).toBe('Last 30 days · 17 runs')
    expect(runWindowCaption('24h', 1)).toBe('Last 24 hours · 1 run')
  })
})
