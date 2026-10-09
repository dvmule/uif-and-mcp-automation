// Header facts and the one primary action of the sync and gold detail pages (UIFPE-682). Pure: no React.
import { pipelineCloud } from './cloud'
import { formatDailyScheduleLabel } from './dailySchedule'
import { minutesUntilNextGoldRun } from './goldDetail'
import { browserTimeZone } from './goldWeekly'
import { formatGoldScheduleLabel } from './syncRow'
import type { GoldPipelineConfig, PipelineConfig } from './types'

export type SyncHeaderStatus = 'healthy' | 'degraded' | 'failing' | 'running' | 'disabled'

export interface PrimaryAction {
  label: string
  kind: 'run' | 'enable' | 'open-drawer'
  disabled: boolean
}

/** Enable a disabled sync, open the running run, else run (or retry) unless a run is already in flight. */
export function syncPrimaryAction(status: SyncHeaderStatus, runInFlight: boolean): PrimaryAction {
  if (status === 'disabled') return { label: 'Enable', kind: 'enable', disabled: false }
  if (status === 'running') return { label: 'Open run drawer', kind: 'open-drawer', disabled: false }
  return { label: status === 'failing' ? 'Retry now' : 'Run now', kind: 'run', disabled: runInFlight }
}

/** The server runs a disabled entity and allows a second concurrent run
 *  (UIFPE-516 pending), so the button holds both lines. */
export function goldPrimaryAction(opts: {
  enabled: boolean
  running: boolean
  runLocked: boolean
  runPending: boolean
}): PrimaryAction {
  if (!opts.enabled) return { label: 'Enable', kind: 'enable', disabled: false }
  const label = opts.running ? 'Run in progress' : opts.runLocked ? 'Run started' : 'Run now'
  return { label, kind: 'run', disabled: opts.running || opts.runLocked || opts.runPending }
}

/** "45 min", "15 h". */
export function formatMinutesAhead(min: number): string {
  return min < 60 ? `${min} min` : `${Math.round(min / 60)} h`
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

/** "today 06:30 UTC", "tomorrow 06:30 UTC", "2 Oct 06:30 UTC". */
export function formatUtcAhead(at: Date, now: Date = new Date()): string {
  const day = (t: number) => Math.floor(t / 86_400_000)
  const diff = day(at.getTime()) - day(now.getTime())
  const clock = `${at.toISOString().slice(11, 16)} UTC`
  if (diff <= 0) return `today ${clock}`
  if (diff === 1) return `tomorrow ${clock}`
  return `${at.getUTCDate()} ${MONTHS[at.getUTCMonth()]} ${clock}`
}

/** pipeline_id · cloud · Oracle → bronze → silver · mode · schedule · next run in N. */
export function syncHeaderFacts(p: PipelineConfig, nextRunInMin: number | null): string[] {
  const schedule = formatDailyScheduleLabel(p.schedule)
  return [
    p.pipeline_id,
    pipelineCloud(p).toUpperCase(),
    p.silver?.enabled ? 'Oracle → bronze → silver' : 'Oracle → bronze',
    p.extract?.mode ?? 'incremental',
    ...(schedule !== '—' ? [schedule] : []),
    ...(nextRunInMin != null ? [`next run in ${formatMinutesAhead(nextRunInMin)}`] : []),
  ]
}

/** Everything after the target table: domain · built from N silver tables · cadence · next run. */
export function goldHeaderFacts(
  g: GoldPipelineConfig,
  opts: { showNextRun: boolean; now?: Date; zone?: string },
): string[] {
  const now = opts.now ?? new Date()
  const domain = g.domain ?? g.metadata?.domain
  const n = g.sources?.length
  const schedule = formatGoldScheduleLabel(g.schedule, opts.zone ?? browserTimeZone(), now)
  // Counted from the start of the minute, so the slot's clock time comes out exact.
  const minute = new Date(Math.floor(now.getTime() / 60_000) * 60_000)
  const ahead = opts.showNextRun
    ? minutesUntilNextGoldRun(g.schedule?.start_time, g.schedule?.frequency, minute, g.schedule?.weekly_days)
    : null
  return [
    ...(domain ? [domain] : []),
    ...(n != null ? [`built from ${n} silver ${n === 1 ? 'table' : 'tables'}`] : []),
    ...(schedule !== '—' ? [schedule] : []),
    ...(ahead != null ? [`next run ${formatUtcAhead(new Date(minute.getTime() + ahead * 60_000), now)}`] : []),
  ]
}
