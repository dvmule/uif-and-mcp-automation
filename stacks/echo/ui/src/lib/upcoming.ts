// "Coming up" (UIFPE-681): the next expected run per table, projected from each
// table's own schedule. There is no scheduler endpoint to ask, so every time
// here is a projection and the page says so. Pure: no React.
import { formatDailyScheduleLabel, resolveDailySlot, slotUtcDate } from './dailySchedule'
import { minutesUntilNextGoldRun } from './goldDetail'
import {
  DEFAULT_WEEKLY_DAYS,
  formatWeekdays,
  isGoldWeekly,
  parseWeeklyDays,
} from './goldWeekly'
import {
  DEFAULT_GOLD_CADENCE,
  goldCadenceLabel,
  goldCadenceMinutes,
  goldEnabled,
} from './syncRow'
import type { GoldPipelineConfig, PipelineConfig } from './types'

const MINUTE_MS = 60_000
const HOUR_MS = 3_600_000
const DAY_MS = 86_400_000

export interface UpcomingRow {
  /** pipeline_id, or `gold:{gold_id}` - the key `lastRuns` is read with. */
  key: string
  object: string
  href: string
  layer: string
  cadence: string
  nextAt: Date | null
  disabled: boolean
  lastRunAt: string | null
  lastStatus: string | null
  note?: string
}

function floorToMinute(now: Date): Date {
  return new Date(Math.floor(now.getTime() / MINUTE_MS) * MINUTE_MS)
}

/** Hourly: the next whole hour. Daily: the sync's slot today, else tomorrow.
 *  Anything else (weekly is not a pipeline cadence today; reconcile runs on
 *  demand) has no projection. */
export function nextPipelineRun(p: PipelineConfig, now: Date): Date | null {
  const frequency = p.schedule?.frequency
  if (frequency === 'hourly') {
    return new Date((Math.floor(now.getTime() / HOUR_MS) + 1) * HOUR_MS)
  }
  if (frequency === 'daily') {
    const slot = resolveDailySlot(p.schedule)
    const today = slotUtcDate(slot, now)
    return today.getTime() > now.getTime()
      ? today
      : slotUtcDate(slot, new Date(now.getTime() + DAY_MS))
  }
  return null
}

/** The next tick of a gold table's start_time + cadence grid, UTC. Null when
 *  the cadence is not one the runtime knows or there is no usable start time:
 *  a guess would put a false time on the page. */
export function nextGoldRun(g: GoldPipelineConfig, now: Date): Date | null {
  const frequency = g.schedule?.frequency || DEFAULT_GOLD_CADENCE
  if (!isGoldWeekly(frequency) && goldCadenceMinutes(frequency) == null) return null
  const from = floorToMinute(now)
  const minutes = minutesUntilNextGoldRun(
    g.schedule?.start_time, frequency, from, g.schedule?.weekly_days,
  )
  return minutes == null ? null : new Date(from.getTime() + minutes * MINUTE_MS)
}

export function pipelineLayerLabel(p: PipelineConfig): string {
  return p.silver?.enabled ? 'Bronze + Silver' : 'Bronze'
}

export function pipelineCadence(p: PipelineConfig): string {
  return p.schedule?.frequency === 'daily'
    ? formatDailyScheduleLabel(p.schedule)
    : (p.schedule?.frequency ?? '—')
}

export function goldCadence(g: GoldPipelineConfig): string {
  const frequency = g.schedule?.frequency || DEFAULT_GOLD_CADENCE
  const label = goldCadenceLabel(frequency)
  if (!isGoldWeekly(frequency)) return label
  const days = parseWeeklyDays(g.schedule?.weekly_days) ?? DEFAULT_WEEKLY_DAYS
  return `${label} · ${formatWeekdays(days)} (UTC)`
}

export interface ObjectSchedule {
  cadence: string
  disabled: boolean
  /** Null when disabled or not projectable. */
  nextAt: Date | null
}

/** The schedule of one object key (pipeline_id or `gold:{gold_id}`); null when its config is not loaded. */
export function objectSchedule(
  key: string,
  pipelines: PipelineConfig[],
  golds: GoldPipelineConfig[],
  now: Date,
): ObjectSchedule | null {
  if (key.startsWith('gold:')) {
    const g = golds.find((x) => `gold:${x.gold_id}` === key)
    if (!g) return null
    const disabled = !goldEnabled(g)
    return { cadence: goldCadence(g), disabled, nextAt: disabled ? null : nextGoldRun(g, now) }
  }
  const p = pipelines.find((x) => x.pipeline_id === key)
  if (!p) return null
  const disabled = !p.enabled
  return { cadence: pipelineCadence(p), disabled, nextAt: disabled ? null : nextPipelineRun(p, now) }
}

export interface LastRun {
  at: string | null
  status: string | null
}

/** Every table with a projectable next run inside the horizon, soonest first.
 *  Disabled tables stay in, marked, so nobody is surprised when nothing runs. */
export function buildUpcoming(
  pipelines: PipelineConfig[],
  golds: GoldPipelineConfig[],
  lastRuns: Map<string, LastRun>,
  now: Date,
  horizonHours: 24 | 168,
): UpcomingRow[] {
  const end = now.getTime() + horizonHours * HOUR_MS
  const rows: UpcomingRow[] = []

  const push = (row: Omit<UpcomingRow, 'lastRunAt' | 'lastStatus' | 'note'>) => {
    if (row.nextAt === null || row.nextAt.getTime() > end) return
    const last = lastRuns.get(row.key)
    rows.push({
      ...row,
      lastRunAt: last?.at ?? null,
      lastStatus: last?.status ?? null,
      note: row.disabled ? 'skipped while disabled' : undefined,
    })
  }

  for (const p of pipelines) {
    push({
      key: p.pipeline_id,
      object: p.extract?.table ?? p.pipeline_id,
      href: `/m/data-sync/syncs/${p.pipeline_id}`,
      layer: pipelineLayerLabel(p),
      cadence: pipelineCadence(p),
      nextAt: nextPipelineRun(p, now),
      disabled: !p.enabled,
    })
  }
  for (const g of golds) {
    push({
      key: `gold:${g.gold_id}`,
      object: g.target?.table || g.gold_id,
      href: `/m/data-sync/gold/${g.gold_id}`,
      layer: 'Gold',
      cadence: goldCadence(g),
      nextAt: nextGoldRun(g, now),
      disabled: !goldEnabled(g),
    })
  }

  return rows.sort((a, b) => a.nextAt!.getTime() - b.nextAt!.getTime())
}

export interface NextAtLabel {
  utc: string
  local: string
  relative: string
}

const WEEKDAY_SHORT = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']

function utcDayKey(d: Date): number {
  return Math.floor(d.getTime() / DAY_MS)
}

/** "Tomorrow 05:30 UTC" / "11:00 GMT+5:30" / "in 15 h". `zone` defaults to the
 *  viewer's; pass one in tests. The day word is added only when the date is not
 *  today (UTC), so the common same-day case stays short. */
export function formatNextAt(d: Date, zone?: string, now: Date = new Date()): NextAtLabel {
  const clock = d.toISOString().slice(11, 16)
  const ahead = utcDayKey(d) - utcDayKey(now)
  const dayWord =
    ahead <= 0 ? '' : ahead === 1 ? 'Tomorrow ' : `${WEEKDAY_SHORT[d.getUTCDay()]} `
  const utc = `${dayWord}${clock} UTC`

  let local = utc
  try {
    local = new Intl.DateTimeFormat('en-GB', {
      timeZone: zone,
      hour: '2-digit',
      minute: '2-digit',
      hourCycle: 'h23',
      timeZoneName: 'short',
    }).format(d)
  } catch {
    // an unknown zone name: fall back to the UTC text rather than throw
  }

  const minutes = Math.max(0, Math.round((d.getTime() - now.getTime()) / MINUTE_MS))
  const relative =
    minutes < 1 ? 'in under a minute'
    : minutes < 60 ? `in ${minutes} min`
    : minutes < 2880 ? `in ${Math.round(minutes / 60)} h`
    : `in ${Math.round(minutes / 1440)} d`

  return { utc, local, relative }
}
