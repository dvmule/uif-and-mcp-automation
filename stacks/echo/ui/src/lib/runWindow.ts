// The run-history window a detail page's chart and its Runs / Failed figures share. Pure: no React.
import { parseDateMs } from './utils'

export type RunWindow = '24h' | '7d' | '30d' | '90d'

export const RUN_WINDOWS: RunWindow[] = ['24h', '7d', '30d', '90d']
export const DEFAULT_RUN_WINDOW: RunWindow = '30d'

export const RUN_WINDOW_HOURS: Record<RunWindow, number> = { '24h': 24, '7d': 168, '30d': 720, '90d': 2160 }
export const RUN_WINDOW_LABEL: Record<RunWindow, string> = {
  '24h': '24 hours',
  '7d': '7 days',
  '30d': '30 days',
  '90d': '90 days',
}

/** Runs that started inside the window; a run with no readable start is left out. */
export function runsInWindow<T extends { started_at?: string | null }>(runs: T[], win: RunWindow, now: Date = new Date()): T[] {
  const cutoff = now.getTime() - RUN_WINDOW_HOURS[win] * 3600_000
  return runs.filter((r) => {
    const ms = parseDateMs(r.started_at)
    return ms !== null && ms >= cutoff
  })
}

/** "Last 30 days · 17 runs". */
export function runWindowCaption(win: RunWindow, count: number): string {
  return `Last ${RUN_WINDOW_LABEL[win]} · ${count} ${count === 1 ? 'run' : 'runs'}`
}
