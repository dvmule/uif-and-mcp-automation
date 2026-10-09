import {
  DEFAULT_WEEKLY_DAYS,
  GOLD_WEEKLY,
  WEEKLY_MINUTES,
  browserTimeZone,
  formatGoldClockLabel,
  formatWeekdays,
  isGoldWeekly,
  localWeeklySchedule,
  parseWeeklyDays,
} from './goldWeekly'
import type { SyncFilterState } from './filters'
import { formatDayLabel } from './goldStats'
import { normalizeRunStatus } from './runLifecycle'
import type { GoldPipelineConfig, GoldRunMetrics, PipelineConfig, PipelineStatus } from './types'
import { freshnessFrom, parseDateMs, type FreshnessLevel } from './utils'

/** One row of a sync-style list: a bronze/silver pipeline, or a gold entity.
 *  Gold is its own row because a gold table is built from MANY silver tables —
 *  it is not a property of any one sync (UIFPE-511). */
export type SyncRow =
  | { kind: 'pipeline'; id: string; pipeline: PipelineConfig }
  | { kind: 'gold'; id: string; gold: GoldPipelineConfig }

export function pipelineRow(p: PipelineConfig): SyncRow {
  return { kind: 'pipeline', id: p.pipeline_id, pipeline: p }
}

export function goldRow(g: GoldPipelineConfig): SyncRow {
  return { kind: 'gold', id: `gold:${g.gold_id}`, gold: g }
}

function goldTarget(g: GoldPipelineConfig, env: string): { database: string; table: string } | null {
  const database = (g.target?.database || '').replace('{env}', env).toLowerCase()
  const table = (g.target?.table || '').toLowerCase()
  return database && table ? { database, table } : null
}

/** `database.table` with `{env}` resolved. Null when the config cannot name it. */
export function goldTableFqn(g: GoldPipelineConfig, env: string): string | null {
  const t = goldTarget(g, env)
  return t ? `${t.database}.${t.table}` : null
}

/** Where a gold row links: the catalog's dataset page. Never /syncs/{gold_id},
 *  which resolves a pipeline_id and 404s on a gold id. */
export function goldCatalogPath(g: GoldPipelineConfig, env: string): string | null {
  const t = goldTarget(g, env)
  return t ? `/m/catalog/datasets/gold/aws/${t.database}/${t.table}` : null
}

export function goldEnabled(g: GoldPipelineConfig): boolean {
  return g.enabled === true
}

/** GET /gold-pipelines carries when the entity last ran but not how it ended.
 *  This answers from that alone: an enabled entity is UNKNOWN (undefined), not
 *  SUCCEEDED. /gold-runs is deployed, so lists should use goldRunStatus with
 *  the entity's latest run instead. */
export function goldStatus(g: GoldPipelineConfig): PipelineStatus | undefined {
  return goldEnabled(g) ? undefined : 'DISABLED'
}

/** The newest run per gold entity, from the fleet-wide GET /gold-runs page.
 *  Newest by started_at; on a tie, or when neither date parses, the run that
 *  comes first in the list wins. */
export function latestGoldRunByEntity(items: GoldRunMetrics[]): Map<string, GoldRunMetrics> {
  const latest = new Map<string, GoldRunMetrics>()
  for (const run of items) {
    const held = latest.get(run.gold_id)
    if (!held || (parseDateMs(run.started_at) ?? -Infinity) > (parseDateMs(held.started_at) ?? -Infinity)) {
      latest.set(run.gold_id, run)
    }
  }
  return latest
}

/** An entity's status from its latest run: DISABLED when off, undefined (shown
 *  as Unknown) when it has no run to judge by. */
export function goldRunStatus(
  g: GoldPipelineConfig,
  latest: GoldRunMetrics | undefined,
): PipelineStatus | undefined {
  if (!goldEnabled(g)) return 'DISABLED'
  return latest ? (normalizeRunStatus(latest.status) as PipelineStatus) : undefined
}

/** Clock time of a run in UTC, with the day added once it is not today:
 *  `06:31 UTC`, or `24 Sep, 06:31 UTC`. */
export function formatRunTime(iso?: string | null, now: Date = new Date()): string {
  const ms = parseDateMs(iso)
  if (ms == null) return '—'
  const at = new Date(ms).toISOString()
  const time = `${at.slice(11, 16)} UTC`
  return at.slice(0, 10) === now.toISOString().slice(0, 10)
    ? time
    : `${formatDayLabel(at.slice(0, 10))}, ${time}`
}

/** Pre-UIFPE-149 cadence names still present in un-resynced configs. Mirrors
 *  gold_frequencies.LEGACY_ALIASES so the row reports what actually runs. */
const LEGACY_GOLD_ALIASES: Record<string, string> = {
  'gold': 'gold-24h',
  'gold-hourly': 'gold-1h',
  'gold-60m': 'gold-1h',
}

/** Minutes between runs for a `gold-{N}{h|m}` cadence, or the weekly rung
 *  (UIFPE-532); null if not one. */
/** Every cadence a gold table may be put on, finest first - the console's
 *  picker (UIFPE-564). This is the TypeScript twin of gold_frequencies'
 *  TRANSFORM_FREQUENCIES and qe/gold/test_gold_frequency_parity.py pins the two
 *  together: a rung offered here that the handler refuses would be a dead
 *  option, and one missing here cannot be reached from the console at all. */
export const GOLD_CADENCE_RUNGS = [
  'gold-5m', 'gold-10m', 'gold-15m', 'gold-30m', 'gold-1h', 'gold-2h',
  'gold-4h', 'gold-6h', 'gold-8h', 'gold-12h', 'gold-24h', 'gold-weekly',
] as const

export type GoldCadence = (typeof GOLD_CADENCE_RUNGS)[number]

/** What a row with no `schedule.frequency` runs on - gold_frequencies'
 *  DEFAULT_FREQUENCY, pinned by the same parity test. */
export const DEFAULT_GOLD_CADENCE = 'gold-24h'

/** Scheduled runs a day on this rung; null for weekly (fewer than one). */
export function goldRunsPerDay(freq?: string | null): number | null {
  const mins = goldCadenceMinutes(freq)
  if (mins == null || mins >= WEEKLY_MINUTES) return null
  return Math.round(1440 / mins)
}

/** Under an hour: the rungs whose cost the dialog makes the operator confirm. */
export function isSubHourlyCadence(freq?: string | null): boolean {
  const mins = goldCadenceMinutes(freq)
  return mins != null && mins < 60
}

export function goldCadenceMinutes(freq?: string | null): number | null {
  const token = LEGACY_GOLD_ALIASES[freq ?? ''] ?? freq ?? ''
  if (token === GOLD_WEEKLY) return WEEKLY_MINUTES
  const m = /^gold-(\d+)(h|m)$/.exec(token)
  if (!m) return null
  return Number(m[1]) * (m[2] === 'h' ? 60 : 1)
}

/** Freshness threshold in hours; an unknown cadence is treated as a day. */
export function goldFrequencyHours(freq?: string | null): number {
  const mins = goldCadenceMinutes(freq)
  return mins == null ? 24 : mins / 60
}

/** The replicate layer's words for the same cadence: `daily`, `hourly`, else
 *  `every N hours/minutes`. An unknown token is shown as-is rather than guessed. */
export function goldCadenceLabel(freq?: string | null): string {
  const mins = goldCadenceMinutes(freq)
  if (mins == null) return freq || '—'
  if (mins === WEEKLY_MINUTES) return 'weekly'
  if (mins === 1440) return 'daily'
  if (mins === 60) return 'hourly'
  if (mins % 60 === 0) return `every ${mins / 60} hours`
  return `every ${mins} minutes`
}

/** "HH:MM" -> minutes since midnight UTC; null unless both parts are 1-2 digits
 *  (the strict rule gold_frequencies.py applies, so the two agree). */
export function parseStartTime(value?: string | null): number | null {
  if (typeof value !== 'string') return null
  const parts = value.trim().split(':')
  if (parts.length !== 2 || !parts.every(p => /^\d{1,2}$/.test(p))) return null
  const hh = Number(parts[0]), mm = Number(parts[1])
  if (hh > 23 || mm > 59) return null
  return hh * 60 + mm
}

export function goldFreshness(g: GoldPipelineConfig): FreshnessLevel {
  return freshnessFrom(
    goldEnabled(g),
    goldFrequencyHours(g.schedule?.frequency),
    g.metadata?.last_run_at,
  )
}

/** `daily · 1:30 AM CDT · 06:30 UTC` - cadence word, then the viewer's clock
 *  and UTC (owner, 2026-09-23). The weekly rung names its run days as the
 *  viewer sees them: `weekly on Tue, Thu · 10:00 PM CDT · 03:00 UTC`, where the
 *  stored UTC days may be Wed/Fri. */
export function formatGoldScheduleLabel(
  schedule?: GoldPipelineConfig['schedule'],
  zone: string = browserTimeZone(),
  now: Date = new Date(),
): string {
  if (!schedule?.frequency) return '—'
  const cadence = goldCadenceLabel(schedule.frequency)
  const start = parseStartTime(schedule.start_time)
  if (start == null) return cadence
  if (isGoldWeekly(schedule.frequency)) {
    const utcDays = parseWeeklyDays(schedule.weekly_days) ?? DEFAULT_WEEKLY_DAYS
    const local = localWeeklySchedule(utcDays, start, zone, now)
    return `weekly on ${formatWeekdays(local.days)} · ${formatGoldClockLabel(start, zone, now)}`
  }
  return `${cadence} · ${formatGoldClockLabel(start, zone, now)}`
}

function passes<T>(selected: T[], value: T): boolean {
  return selected.length === 0 || selected.includes(value)
}

/** The URL facets a pipeline row answers in matchesAll, for a gold row. Gold has
 *  no source system or tags, so those facets exclude it the same way they
 *  exclude a pipeline that lacks them. */
export function goldMatchesFilters(
  g: GoldPipelineConfig,
  state: SyncFilterState,
  /** The entity's status when the caller has its latest run (goldRunStatus);
   *  otherwise only DISABLED can be told apart. */
  status: PipelineStatus | undefined = goldStatus(g),
): boolean {
  if (!passes(state.domain, g.domain ?? g.metadata?.domain ?? 'Other')) return false
  if (!passes(state.source, '—')) return false
  if (state.status.length > 0) {
    const s = status
    if (!s || !state.status.includes(s)) return false
  }
  if (!passes(state.freshness, goldFreshness(g))) return false
  if (state.tag.length > 0) return false
  return true
}

/** What the Table column shows and sorts on. */
export function rowLabel(row: SyncRow, env: string): string {
  return row.kind === 'pipeline'
    ? row.pipeline.extract.table
    : (goldTableFqn(row.gold, env) ?? row.gold.gold_id)
}

/** What the search box matches against. */
export function rowSearchText(row: SyncRow, env: string): string {
  return row.kind === 'pipeline'
    ? `${row.pipeline.extract.table} ${row.pipeline.pipeline_id}`.toLowerCase()
    : `${goldTableFqn(row.gold, env) ?? ''} ${row.gold.gold_id}`.toLowerCase()
}
