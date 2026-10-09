import { differenceInHours, parseISO } from 'date-fns'
import { goldCadenceMinutes, goldEnabled, parseStartTime } from './syncRow'
import { DEFAULT_WEEKLY_DAYS, WEEKDAYS, isGoldWeekly, parseWeeklyDays } from './goldWeekly'
import { isInFlightStatus } from './runLifecycle'
import { MISSING_SOURCE_PATTERN, explainRun } from './runExplain'
import { statusBucket, type RunRow } from './runsTable'
import type { GoldPipelineConfig, GoldRunMetrics, GoldSource } from './types'

/** Where a gold row opens (UIFPE-517). Its own route: pipeline_id and gold_id
 *  share a namespace, so /syncs/{id} can never be taught to tell them apart. */
export function goldDetailPath(g: Pick<GoldPipelineConfig, 'gold_id'>): string {
  return `/m/data-sync/gold/${g.gold_id}`
}

/** Minutes until the next slot of a `start_time` + cadence grid, UTC. Mirrors
 *  gold_frequencies.slot_minutes / next_slot: slots are start + k*interval
 *  within the day. Null when the start time is unusable rather than guessing
 *  the cadence default the dispatcher would fall back to. */
export function minutesUntilNextGoldRun(
  startTime: string | null | undefined,
  frequency: string | null | undefined,
  now: Date = new Date(),
  weeklyDays?: unknown,
): number | null {
  const start = parseStartTime(startTime)
  if (start == null) return null
  if (isGoldWeekly(frequency)) {
    // UIFPE-532: the 7-day grid - mirrors gold_frequencies.next_weekly_slot
    const days = parseWeeklyDays(weeklyDays) ?? DEFAULT_WEEKLY_DAYS
    const midnight = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate())
    for (let ahead = 0; ahead <= 7; ahead++) {
      const cand = midnight + ahead * 86_400_000 + start * 60_000
      const day = WEEKDAYS[(new Date(cand).getUTCDay() + 6) % 7]
      if (days.includes(day) && cand > now.getTime()) return Math.round((cand - now.getTime()) / 60_000)
    }
    return null
  }
  const interval = goldCadenceMinutes(frequency) ?? 1440
  const slots = new Set<number>()
  for (let k = 0; k * interval < 1440; k++) slots.add((start + k * interval) % 1440)
  const sorted = [...slots].sort((a, b) => a - b)
  const nowMin = now.getUTCHours() * 60 + now.getUTCMinutes()
  const next = sorted.find(s => s > nowMin)
  return next != null ? next - nowMin : sorted[0] + 1440 - nowMin
}

export type GoldDetailStatus = 'disabled' | 'running' | 'failing' | 'degraded' | 'healthy' | 'unknown'

export interface GoldDetailState {
  status: GoldDetailStatus
  lastRun: GoldRunMetrics | null
  successRate30d: number
  trend: 'up' | 'down' | 'flat'
  nextRunInMin: number | null
}

const UNKNOWN: Omit<GoldDetailState, 'nextRunInMin'> = {
  status: 'unknown', lastRun: null, successRate30d: 0, trend: 'flat',
}

/** The hero's story, derived the way useSyncState derives a sync's - but
 *  honest about the gap: with no runs to read, status is UNKNOWN, never an
 *  invented healthy. /gold-runs already sorts newest first. */
export function goldDetailState(
  gold: GoldPipelineConfig | undefined,
  runs: GoldRunMetrics[],
  now: Date = new Date(),
): GoldDetailState {
  if (!gold) return { ...UNKNOWN, nextRunInMin: null }
  const nextRunInMin = minutesUntilNextGoldRun(
    gold.schedule?.start_time, gold.schedule?.frequency, now, gold.schedule?.weekly_days)
  const lastRun = runs[0] ?? null
  const rate = successRate30d(runs, now)

  if (!goldEnabled(gold)) {
    return { status: 'disabled', lastRun, successRate30d: rate, trend: 'flat', nextRunInMin: null }
  }
  if (!lastRun) return { ...UNKNOWN, nextRunInMin }
  if (isInFlightStatus(lastRun.status)) {
    return { status: 'running', lastRun, successRate30d: rate, trend: 'flat', nextRunInMin: null }
  }

  const leadingFails = countLeadingFails(runs)
  const failsInLast10 = runs.slice(0, 10).filter(r => r.status === 'FAILED').length
  let status: GoldDetailStatus = 'healthy'
  if (lastRun.status === 'FAILED' || leadingFails >= 3) status = 'failing'
  else if (lastRun.status === 'WARNING' || failsInLast10 >= 2) status = 'degraded'

  return { status, lastRun, successRate30d: rate, trend: trendOf(runs), nextRunInMin }
}

const escapeRegExp = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

/** True when `name` appears in `text` as a whole identifier (so `price` does not match `price_1`). */
function mentions(text: string, name: string): boolean {
  if (!name) return false
  return new RegExp(`(^|[^a-z0-9_])${escapeRegExp(name)}($|[^a-z0-9_])`).test(text)
}

/** The sources the latest failure names as missing: only when its explanation
 *  is the missing-source rule AND the raw error names that table. */
export function missingSourceTables(
  sources: GoldSource[] | undefined,
  latest: RunRow | null,
  env?: string,
): string[] {
  if (!latest || !sources?.length) return []
  const bucket = statusBucket(latest.status)
  if (bucket !== 'FAILED' && bucket !== 'WARNING') return []
  if (explainRun(latest).pattern !== MISSING_SOURCE_PATTERN) return []
  const text = (latest.run.error_message ?? '').toLowerCase()
  return sources
    .map((s) => s.silver_table)
    .filter((t) => {
      const name = (env ? t.replace('{env}', env) : t).toLowerCase()
      return mentions(text, name) || mentions(text, name.split('.').pop() ?? '')
    })
}

/** gold_ids whose sources read this silver table: the full name matches, or the table part does. */
export function goldReadersOfSilver(
  golds: GoldPipelineConfig[] | undefined,
  silverFqn: string,
  env?: string,
): string[] {
  const target = silverFqn.trim().toLowerCase()
  if (!target || !golds) return []
  const targetTable = target.split('.').pop()
  return golds
    .filter((g) =>
      (g.sources ?? []).some((s) => {
        const name = (env ? s.silver_table.replace('{env}', env) : s.silver_table).toLowerCase()
        return name === target || name.split('.').pop() === targetTable
      }),
    )
    .map((g) => g.gold_id)
}

function countLeadingFails(runs: GoldRunMetrics[]): number {
  let n = 0
  for (const r of runs) {
    if (r.status !== 'FAILED') break
    n++
  }
  return n
}

function successRate30d(runs: GoldRunMetrics[], now: Date): number {
  const recent = runs.filter(r => {
    if (!r.started_at) return false
    const d = parseISO(r.started_at)
    return !Number.isNaN(d.getTime()) && differenceInHours(now, d) <= 720 && !isInFlightStatus(r.status)
  })
  if (recent.length === 0) return 0
  return recent.filter(r => r.status === 'SUCCEEDED').length / recent.length
}

/** Faster or slower: the last three completed runs against the three before. */
function trendOf(runs: GoldRunMetrics[]): 'up' | 'down' | 'flat' {
  const done = runs.filter(r => r.duration_sec != null && !isInFlightStatus(r.status))
  if (done.length < 6) return 'flat'
  const avg = (xs: GoldRunMetrics[]) => xs.reduce((s, r) => s + (r.duration_sec ?? 0), 0) / xs.length
  const recent = avg(done.slice(0, 3)), older = avg(done.slice(3, 6))
  if (older === 0) return 'flat'
  if (recent < older * 0.9) return 'up'
  if (recent > older * 1.1) return 'down'
  return 'flat'
}
