// Insights › Overview's Activity and Top tables (UIFPE-735), counted from the
// runs already loaded. Ported from Beacon's lib/insights.js. Pure: no React.
import { statusBucket, type RunRow, type Span } from './runsTable'
import { parseDateMs } from './utils'

export const HOUR_MS = 3_600_000
export const DAY_MS = 86_400_000

/** 24h = 24 hourly buckets, 7d = 7 daily buckets. */
export const ACTIVITY_SPAN: Record<Span, { n: number; spanMs: number }> = {
  '24h': { n: 24, spanMs: HOUR_MS },
  '7d': { n: 7, spanMs: DAY_MS },
}

/** The windows the Insights cards offer, as WindowSelect options. */
export const WINDOW_OPTIONS = [
  ['24h', 'Last 24 hours'],
  ['7d', 'Last 7 days'],
] as const satisfies ReadonlyArray<readonly [Span, string]>

/** The legend under every outcome chart. */
export const OUTCOME_LEGEND = [
  { label: 'Succeeded', tone: 'ok' },
  { label: 'Warning', tone: 'warn' },
  { label: 'Failed', tone: 'fail' },
] as const

export interface Bucket {
  from: number
  to: number
  runs: RunRow[]
}

/** Runs started in (since, until], both epoch ms. */
export function runsBetween(rows: RunRow[], sinceMs: number, untilMs: number): RunRow[] {
  return rows.filter((row) => {
    const at = parseDateMs(row.startedAt)
    return at !== null && at > sinceMs && at <= untilMs
  })
}

/** n equal buckets from `sinceMs`, each `spanMs` long, holding the runs started in (from, to]. */
export function buckets(rows: RunRow[], sinceMs: number, n: number, spanMs: number): Bucket[] {
  const out: Bucket[] = Array.from({ length: n }, (_, i) => ({
    from: sinceMs + i * spanMs,
    to: sinceMs + (i + 1) * spanMs,
    runs: [],
  }))
  for (const row of rows) {
    const at = parseDateMs(row.startedAt)
    if (at === null) continue
    const i = Math.ceil((at - sinceMs) / spanMs) - 1
    if (i >= 0 && i < n) out[i].runs.push(row)
  }
  return out
}

/** A run's rows: written, else read, else none. */
export function runRows(row: RunRow): number {
  return row.rowsWritten ?? row.rowsRead ?? 0
}

export function bucketRows(bucket: Bucket): number {
  return bucket.runs.reduce((sum, row) => sum + runRows(row), 0)
}

/** The bucket holding the most rows; index -1 when none has any. */
export function peakBucket(list: Bucket[]): { index: number; rows: number } {
  let best = { index: -1, rows: 0 }
  list.forEach((b, i) => {
    const rows = bucketRows(b)
    if (rows > best.rows) best = { index: i, rows }
  })
  return best
}

export interface OutcomeCounts {
  succeeded: number
  warning: number
  failed: number
  running: number
  other: number
  total: number
}

/** Counts by the same status buckets the Jobs filter uses; skipped runs are "other". */
export function countOutcomes(runs: RunRow[]): OutcomeCounts {
  const c: OutcomeCounts = { succeeded: 0, warning: 0, failed: 0, running: 0, other: 0, total: runs.length }
  for (const row of runs) {
    const b = statusBucket(row.status)
    if (b === 'SUCCEEDED') c.succeeded += 1
    else if (b === 'WARNING') c.warning += 1
    else if (b === 'FAILED') c.failed += 1
    else if (b === 'RUNNING') c.running += 1
    else c.other += 1
  }
  return c
}

const WEEKDAY = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']

/** "15:00" for the 24-hour window, "Mon 29" for 7 days, in the viewer's time. */
export function bucketLabel(fromMs: number, window: Span): string {
  const d = new Date(fromMs)
  if (window === '24h') return `${String(d.getHours()).padStart(2, '0')}:00`
  return `${WEEKDAY[d.getDay()]} ${d.getDate()}`
}

/** Up to five evenly spaced bucket indexes for the axis, first and last included. */
export function axisTicks(n: number): number[] {
  if (n <= 0) return []
  return [...new Set([0, 0.25, 0.5, 0.75, 1].map((f) => Math.round(f * (n - 1))))]
}

/** "1.3M", "431K", "1.3K", "88". */
export function compact(n: number): string {
  if (n >= 1e6) return `${(n / 1e6).toFixed(1).replace(/\.0$/, '')}M`
  if (n >= 1e3) return `${(n / 1e3).toFixed(1).replace(/\.0$/, '')}K`
  return String(Math.round(n))
}

export interface RankedObject {
  key: string
  label: string
  href: string | null
  rows: number
  succeeded: number
  warning: number
  failed: number
}

/** The objects with the most rows from finished runs, most first; ties by name. */
export function rankByRows(rows: RunRow[], limit = 5): RankedObject[] {
  const map = new Map<string, RankedObject>()
  for (const row of rows) {
    const b = statusBucket(row.status)
    if (b !== 'SUCCEEDED' && b !== 'WARNING' && b !== 'FAILED') continue
    const key = row.pipelineId ?? (row.goldId ? `gold:${row.goldId}` : row.object)
    const held = map.get(key) ?? {
      key,
      label: row.object,
      href: row.objectHref,
      rows: 0,
      succeeded: 0,
      warning: 0,
      failed: 0,
    }
    held.rows += runRows(row)
    if (b === 'SUCCEEDED') held.succeeded += 1
    else if (b === 'WARNING') held.warning += 1
    else held.failed += 1
    map.set(key, held)
  }
  return [...map.values()]
    .sort((a, b) => b.rows - a.rows || a.label.localeCompare(b.label))
    .slice(0, limit)
}
