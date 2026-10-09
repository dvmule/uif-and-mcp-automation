// The facts behind a detail page's KPI cards (UIFPE-682, UIFPE-734). Pure: no React.
import { explainRun } from './runExplain'
import { formatUtcClockTime, formatUtcStamp, runRowStatusView, statusBucket, type RunRow } from './runsTable'
import type { DqCheckConfig } from './types'
import { formatRows, parseDateMs } from './utils'

export interface TrustFact {
  value: string
  hint: string
}

const NO_RUNS: TrustFact = { value: '—', hint: 'no runs yet' }
const NO_SUCCESS: TrustFact = { value: 'none yet', hint: 'no successful run recorded' }

/** "45 min ago", "2 h ago"; an em dash when unreadable. */
export function hoursAgo(iso: string | null | undefined, now: Date = new Date()): string {
  const ms = parseDateMs(iso)
  if (ms === null) return '—'
  const mins = Math.max(0, Math.round((now.getTime() - ms) / 60_000))
  return mins < 60 ? `${mins} min ago` : `${Math.round(mins / 60)} h ago`
}

/** "today", "yesterday" or "N days ago", by UTC calendar day. */
export function relativeDayWord(iso: string | null | undefined, now: Date = new Date()): string {
  const ms = parseDateMs(iso)
  if (ms === null) return '—'
  const day = (t: number) => Math.floor(t / 86_400_000)
  const diff = day(now.getTime()) - day(ms)
  if (diff <= 0) return 'today'
  if (diff === 1) return 'yesterday'
  return `${diff} days ago`
}

/** "15:50 UTC · 2 h ago". */
export function clockAndAgo(iso: string | null | undefined, now: Date = new Date()): string {
  return `${formatUtcClockTime(iso)} · ${hoursAgo(iso, now)}`
}

function lastRunHint(row: RunRow): string {
  return `last run, ${formatUtcClockTime(row.startedAt)} · ${runRowStatusView(row).label.toLowerCase()}`
}

/** What the last run recorded, never an invented count. */
export function rowsReadWrittenFact(row: RunRow | null): TrustFact {
  if (!row) return NO_RUNS
  return { value: `${formatRows(row.rowsRead)} → ${formatRows(row.rowsWritten)}`, hint: lastRunHint(row) }
}

export function rowsWrittenFact(row: RunRow | null): TrustFact {
  if (!row) return NO_RUNS
  return { value: formatRows(row.rowsWritten), hint: lastRunHint(row) }
}

export function keysSelectedFact(row: RunRow | null): TrustFact {
  if (!row) return NO_RUNS
  const keys = 'affected_keys' in row.run ? row.run.affected_keys : null
  return { value: formatRows(keys), hint: 'affected keys in the last run' }
}

/** Newest run that succeeded; a run with no readable start never wins. */
export function newestSucceeded(rows: RunRow[]): RunRow | null {
  let best: RunRow | null = null
  let bestAt = -Infinity
  for (const row of rows) {
    if (statusBucket(row.status) !== 'SUCCEEDED') continue
    const at = parseDateMs(row.startedAt) ?? -Infinity
    if (!best || at > bestAt) {
      best = row
      bestAt = at
    }
  }
  return best
}

export function lastSuccessFact(rows: RunRow[], now: Date = new Date()): TrustFact {
  const row = newestSucceeded(rows)
  if (!row) return NO_SUCCESS
  const count = row.rowsWritten ?? row.rowsRead
  const parts = [
    ...(count != null ? [`${formatRows(count)} rows`] : []),
    formatUtcClockTime(row.startedAt),
    hoursAgo(row.startedAt, now),
  ]
  return { value: relativeDayWord(row.startedAt, now), hint: parts.join(' · ') }
}

/** Gold's last success: the bookmark only moves when the run wrote rows. */
export function goldLastSuccessFact(rows: RunRow[], now: Date = new Date()): TrustFact {
  const row = newestSucceeded(rows)
  if (!row) return NO_SUCCESS
  const time = formatUtcClockTime(row.startedAt)
  const hint =
    (row.rowsWritten ?? 0) > 0
      ? `${formatRows(row.rowsWritten)} rows · ${time} · bookmark advanced`
      : `${time} · no changes`
  return { value: relativeDayWord(row.startedAt, now), hint }
}

/** Whether gold's newest success moved the bookmark; null when nothing succeeded. */
export function goldBookmarkWord(rows: RunRow[]): 'bookmark advanced' | 'no changes' | null {
  const row = newestSucceeded(rows)
  if (!row) return null
  return (row.rowsWritten ?? 0) > 0 ? 'bookmark advanced' : 'no changes'
}

export interface WindowRunCounts {
  total: number
  /** Succeeded runs. */
  ok: number
  /** Failed or warned runs. */
  bad: number
}

export function windowRunCounts(rows: RunRow[]): WindowRunCounts {
  let ok = 0
  let bad = 0
  for (const row of rows) {
    const bucket = statusBucket(row.status)
    if (bucket === 'SUCCEEDED') ok += 1
    else if (bucket === 'FAILED' || bucket === 'WARNING') bad += 1
  }
  return { total: rows.length, ok, bad }
}

/** "88% of finished runs succeeded · last success yesterday"; the day is read from every loaded run. */
export function runsKpiHint(counts: WindowRunCounts, allRows: RunRow[], now: Date = new Date()): string {
  const finished = counts.ok + counts.bad
  const share = finished
    ? `${Math.round((counts.ok / finished) * 100)}% of finished runs succeeded`
    : 'no finished runs in this range'
  const success = newestSucceeded(allRows)
  return `${share} · ${success ? `last success ${relativeDayWord(success.startedAt, now)}` : NO_SUCCESS.hint}`
}

/** "latest: 13:14 UTC · ORA-01017" for the newest failed or warned run, else "nothing failed". */
export function failedKpiHint(rows: RunRow[]): string {
  let latest: RunRow | null = null
  let latestAt = -Infinity
  for (const row of rows) {
    const bucket = statusBucket(row.status)
    if (bucket !== 'FAILED' && bucket !== 'WARNING') continue
    const at = parseDateMs(row.startedAt) ?? -Infinity
    if (!latest || at > latestAt) {
      latest = row
      latestAt = at
    }
  }
  if (!latest) return 'nothing failed'
  const why = explainRun(latest)
  return `latest: ${formatUtcClockTime(latest.startedAt)} · ${why.pattern ?? why.headline}`
}

/** The newest reconcile check that has run at least once. */
export function latestReconcileCheck(checks: DqCheckConfig[]): DqCheckConfig | null {
  let best: DqCheckConfig | null = null
  let bestAt = -Infinity
  for (const check of checks) {
    if (check.kind !== 'reconcile' || !check.last_run) continue
    const at = parseDateMs(check.last_run.ts) ?? -Infinity
    if (!best || at > bestAt) {
      best = check
      bestAt = at
    }
  }
  return best
}

/** "reconcile · 28 Sep 2026, 08:55 UTC · <summary>"; the summary only when the check wrote one. */
export function reconcileHint(check: DqCheckConfig): string {
  const run = check.last_run
  return ['reconcile', formatUtcStamp(run?.ts), run?.summary?.trim()].filter(Boolean).join(' · ')
}
