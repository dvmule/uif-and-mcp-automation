// The facts behind the Details tab of the sync and gold detail pages (UIFPE-734). Pure: no React.
import { formatReconcileScheduleLabel, formatSlotEastern, formatSlotUtc, resolveDailySlot } from './dailySchedule'
import {
  DEFAULT_WEEKLY_DAYS,
  formatGoldClockLabel,
  formatWeekdays,
  isGoldWeekly,
  localWeeklySchedule,
  parseWeeklyDays,
} from './goldWeekly'
import { goldCadenceLabel, parseStartTime } from './syncRow'
import type { GoldPipelineConfig, PipelineConfig } from './types'

/** Bronze path: the fixture's `bronze.path` shorthand first, then the shipped target paths. */
export function syncBronzePath(p: PipelineConfig): string | undefined {
  return (
    (p as PipelineConfig & { bronze?: { path?: string } }).bronze?.path ?? p.target?.gcs_path ?? p.target?.s3_path
  )
}

export interface SyncScheduleFacts {
  frequency: string
  /** "08:30 UTC (3:30 AM ET)" for a daily sync. */
  dailyWindow: string | null
  reconcileWindow: string | null
  worker: string
  timeoutMin: number
}

export function syncScheduleFacts(p: PipelineConfig): SyncScheduleFacts {
  const daily = p.schedule?.frequency === 'daily'
  return {
    frequency: p.schedule?.frequency ?? 'On demand',
    dailyWindow: daily
      ? `${formatSlotUtc(resolveDailySlot(p.schedule))} (${formatSlotEastern(resolveDailySlot(p.schedule))} ET)`
      : null,
    reconcileWindow: formatReconcileScheduleLabel(p.schedule, p.reconcile?.enabled),
    worker: `${p.glue?.worker_type ?? 'G.1X'} × ${p.glue?.num_workers ?? 5}`,
    timeoutMin: p.glue?.timeout_minutes ?? 60,
  }
}

/** "G.1X × 8"; an em dash when the config names neither. */
export function goldWorkerLabel(w?: { worker_type?: string; num_workers?: number }): string {
  return w?.worker_type || w?.num_workers ? `${w.worker_type ?? '—'} × ${w.num_workers ?? '—'}` : '—'
}

export interface GoldScheduleFacts {
  cadence: string
  /** The wire value when it reads differently from the cadence, e.g. "gold-24h". */
  cadenceRaw: string | null
  /** Weekly only: run days in the viewer's zone, and in UTC when they differ. */
  runDays: string | null
  runDaysUtc: string | null
  start: string
}

/** Cadence, weekly run days and start time, in the viewer's zone with UTC beside it (UIFPE-532). */
export function goldScheduleFacts(gold: GoldPipelineConfig, zone: string): GoldScheduleFacts {
  const s = gold.schedule ?? {}
  const start = parseStartTime(s.start_time)
  const weekly = isGoldWeekly(s.frequency)
  const utcDays = parseWeeklyDays(s.weekly_days) ?? DEFAULT_WEEKLY_DAYS
  const localDays = weekly && start != null ? localWeeklySchedule(utcDays, start, zone).days : utcDays
  const cadence = goldCadenceLabel(s.frequency)
  return {
    cadence,
    cadenceRaw: s.frequency && s.frequency !== cadence ? s.frequency : null,
    runDays: weekly ? formatWeekdays(localDays) : null,
    runDaysUtc: weekly && formatWeekdays(localDays) !== formatWeekdays(utcDays) ? `${formatWeekdays(utcDays)} UTC` : null,
    start: start != null ? formatGoldClockLabel(start, zone) : (s.start_time ?? '—'),
  }
}
