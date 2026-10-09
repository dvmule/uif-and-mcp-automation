import type { PipelineSchedule } from '@/lib/types'

export const DAILY_SLOT_COUNT = 12
/** 05:30 UTC — first daily Bronze slot */
export const DAILY_WINDOW_START_UTC_MINUTES = 5 * 60 + 30
/** 06:00 UTC — first Silver reconcile slot (+30 min after Bronze slot 0) */
export const RECONCILE_WINDOW_START_UTC_MINUTES = 6 * 60

export function resolveDailySlot(schedule?: PipelineSchedule | null): number {
  if (!schedule || schedule.frequency !== 'daily') return 0
  const raw = schedule.daily_slot
  if (raw == null || Number.isNaN(Number(raw))) return 0
  return Math.max(0, Math.min(DAILY_SLOT_COUNT - 1, Number(raw)))
}

export function slotUtcMinutes(slot: number): number {
  return DAILY_WINDOW_START_UTC_MINUTES + slot * 30
}

export function reconcileSlotUtcMinutes(slot: number): number {
  return RECONCILE_WINDOW_START_UTC_MINUTES + slot * 30
}

export function reconcileSlotUtcHhmm(slot: number): { hour: number; minute: number } {
  const total = reconcileSlotUtcMinutes(slot)
  return { hour: Math.floor(total / 60), minute: total % 60 }
}

export function slotUtcHhmm(slot: number): { hour: number; minute: number } {
  const total = slotUtcMinutes(slot)
  return { hour: Math.floor(total / 60), minute: total % 60 }
}

/** "HH:MM UTC" for a UTC minute-of-day. */
export function formatUtcClock(minutesOfDay: number): string {
  const hour = Math.floor(minutesOfDay / 60) % 24
  const minute = ((minutesOfDay % 60) + 60) % 60
  return `${String(hour).padStart(2, '0')}:${String(minute).padStart(2, '0')} UTC`
}

/** Eastern Time for the same UTC minute-of-day. Probes a January date, so this
 *  is standard time all year: consistent across every row, if not DST-exact. */
export function formatEasternClock(minutesOfDay: number): string {
  const hour = Math.floor(minutesOfDay / 60) % 24
  const minute = ((minutesOfDay % 60) + 60) % 60
  const probe = new Date(Date.UTC(2026, 0, 15, hour, minute, 0, 0))
  return new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/New_York',
    hour: 'numeric',
    minute: '2-digit',
    hour12: true,
  }).format(probe)
}

/** "07:00 UTC (2:00 AM ET)": the clock treatment every schedule cell shares. */
export function formatClockLabel(minutesOfDay: number): string {
  return `${formatUtcClock(minutesOfDay)} (${formatEasternClock(minutesOfDay)} ET)`
}

/** UTC clock time for a slot, e.g. "07:00 UTC" */
export function formatSlotUtc(slot: number): string {
  return formatUtcClock(slotUtcMinutes(slot))
}

/** UTC clock time for reconcile slot N (+30 min after Bronze slot N). */
export function formatReconcileSlotUtc(slot: number): string {
  const { hour, minute } = reconcileSlotUtcHhmm(slot)
  return `${String(hour).padStart(2, '0')}:${String(minute).padStart(2, '0')} UTC`
}

/** Eastern Time label for a slot. */
export function formatSlotEastern(slot: number): string {
  return formatEasternClock(slotUtcMinutes(slot))
}

export function formatReconcileSlotEastern(slot: number): string {
  return formatEasternClock(reconcileSlotUtcMinutes(slot))
}

export function formatDailyScheduleLabel(schedule?: PipelineSchedule | null): string {
  if (!schedule?.frequency) return '—'
  if (schedule.frequency !== 'daily') return schedule.frequency
  const slot = resolveDailySlot(schedule)
  return `daily · ${formatSlotUtc(slot)} (${formatSlotEastern(slot)} ET)`
}

export function formatReconcileScheduleLabel(
  schedule?: PipelineSchedule | null,
  reconcileEnabled?: boolean,
): string | null {
  if (!reconcileEnabled || schedule?.frequency !== 'daily') return null
  const slot = resolveDailySlot(schedule)
  return `${formatReconcileSlotUtc(slot)} (${formatReconcileSlotEastern(slot)} ET)`
}

export function slotUtcDate(slot: number, day: Date): Date {
  const { hour, minute } = slotUtcHhmm(slot)
  return new Date(
    Date.UTC(day.getUTCFullYear(), day.getUTCMonth(), day.getUTCDate(), hour, minute, 0, 0),
  )
}

/** Minutes until the next EventBridge tick for this daily slot. */
export function minutesUntilNextDailySlot(slot: number, now = new Date()): number {
  let next = slotUtcDate(slot, now)
  if (next.getTime() <= now.getTime()) {
    const tomorrow = new Date(now.getTime() + 86_400_000)
    next = slotUtcDate(slot, tomorrow)
  }
  return Math.max(0, Math.round((next.getTime() - now.getTime()) / 60_000))
}
