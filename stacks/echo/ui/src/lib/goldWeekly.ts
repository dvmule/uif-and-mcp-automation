/** The gold weekly rung and the user's-timezone view of a gold schedule
 *  (UIFPE-532). The runtime speaks UTC only: `schedule.start_time` is a UTC
 *  time of day and `schedule.weekly_days` are UTC weekdays. The user picks both
 *  in their own zone, so day and time are converted TOGETHER - Tuesday 22:00 in
 *  Chicago is Wednesday 03:00 UTC, and the stored days may differ from the
 *  ticked ones. Mirrors gold_frequencies.py (WEEKDAYS, parse_weekly_days). */

export const GOLD_WEEKLY = 'gold-weekly'
export const WEEKDAYS = ['MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT', 'SUN'] as const
export type Weekday = (typeof WEEKDAYS)[number]
export const DEFAULT_WEEKLY_DAYS: Weekday[] = ['MON']
export const WEEKLY_MINUTES = 7 * 1440

const DAY_MS = 86_400_000

export function isGoldWeekly(freq?: string | null): boolean {
  return freq === GOLD_WEEKLY
}

/** Case-insensitive, deduplicated, ISO-ordered; null when unusable (empty
 *  included) - the same contract as the Python twin. */
export function parseWeeklyDays(value: unknown): Weekday[] | null {
  if (!Array.isArray(value)) return null
  const seen = new Set<Weekday>()
  for (const v of value) {
    if (typeof v !== 'string') return null
    const token = v.trim().toUpperCase() as Weekday
    if (!WEEKDAYS.includes(token)) return null
    seen.add(token)
  }
  if (seen.size === 0) return null
  return WEEKDAYS.filter(d => seen.has(d))
}

/** The viewer's IANA zone; UTC when the runtime cannot say. */
export function browserTimeZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
  } catch {
    return 'UTC'
  }
}

export function isUtcZone(zone: string): boolean {
  return zone === 'UTC' || zone === 'Etc/UTC' || zone === 'GMT' || zone === 'Etc/GMT'
}

export function formatMinutes(minutes: number): string {
  const m = ((minutes % 1440) + 1440) % 1440
  return `${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`
}

export interface DayTime {
  day: Weekday
  minutes: number
}

/** Monday 00:00 UTC of the week that contains `ref`. Conversions happen on a
 *  concrete week so the zone's offset (DST included) is the one in force. */
function weekStartUtc(ref: Date): number {
  const midnight = Date.UTC(ref.getUTCFullYear(), ref.getUTCMonth(), ref.getUTCDate())
  return midnight - ((ref.getUTCDay() + 6) % 7) * DAY_MS
}

/** The UTC instant of (utcDay, utcMinutes) in `ref`'s week. */
export function utcInstant(utcDay: Weekday, utcMinutes: number, ref: Date): Date {
  return new Date(weekStartUtc(ref) + WEEKDAYS.indexOf(utcDay) * DAY_MS + utcMinutes * 60_000)
}

/** Weekday and minute-of-day of `instant` as a clock in `zone` would show them. */
export function zonedParts(instant: Date, zone: string): DayTime {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: zone, weekday: 'short', hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
  }).formatToParts(instant)
  const get = (t: Intl.DateTimeFormatPartTypes) => parts.find(p => p.type === t)?.value ?? ''
  const day = get('weekday').slice(0, 3).toUpperCase() as Weekday
  return { day, minutes: (Number(get('hour')) % 24) * 60 + Number(get('minute')) }
}

/** Wall clock minus UTC, in minutes, for `zone` at `instant`. */
export function zoneOffsetMinutes(instant: Date, zone: string): number {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: zone, year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23',
  }).formatToParts(instant)
  const get = (t: Intl.DateTimeFormatPartTypes) => Number(parts.find(p => p.type === t)?.value)
  const wall = Date.UTC(get('year'), get('month') - 1, get('day'), get('hour') % 24, get('minute'), get('second'))
  return Math.round((wall - instant.getTime()) / 60_000)
}

/** UTC (day, time) -> the user's (day, time). */
export function utcToZoned(utcDay: Weekday, utcMinutes: number, zone: string, ref: Date = new Date()): DayTime {
  return zonedParts(utcInstant(utcDay, utcMinutes, ref), zone)
}

/** The user's (day, time) -> UTC (day, time). The wall clock is read as if it
 *  were UTC, then corrected by the zone's offset at that instant - twice, so a
 *  pick right at a DST edge lands on the offset actually in force. */
export function zonedToUtc(localDay: Weekday, localMinutes: number, zone: string, ref: Date = new Date()): DayTime {
  const wall = utcInstant(localDay, localMinutes, ref)
  let guess = new Date(wall.getTime() - zoneOffsetMinutes(wall, zone) * 60_000)
  guess = new Date(wall.getTime() - zoneOffsetMinutes(guess, zone) * 60_000)
  return zonedParts(guess, 'UTC')
}

/** A stored weekly schedule as the user sees it: every UTC day converted with
 *  the time, deduplicated into ISO order. All days share one start time, so
 *  the minutes come from the first conversion. */
export function localWeeklySchedule(
  utcDays: readonly Weekday[], utcMinutes: number, zone: string, ref: Date = new Date(),
): { days: Weekday[]; minutes: number } {
  const converted = utcDays.map(d => utcToZoned(d, utcMinutes, zone, ref))
  const seen = new Set(converted.map(c => c.day))
  return { days: WEEKDAYS.filter(d => seen.has(d)), minutes: converted[0]?.minutes ?? utcMinutes }
}

/** What the user ticked -> what the runtime stores. */
export function utcWeeklySchedule(
  localDays: readonly Weekday[], localMinutes: number, zone: string, ref: Date = new Date(),
): { days: Weekday[]; minutes: number } {
  const converted = localDays.map(d => zonedToUtc(d, localMinutes, zone, ref))
  const seen = new Set(converted.map(c => c.day))
  return { days: WEEKDAYS.filter(d => seen.has(d)), minutes: converted[0]?.minutes ?? localMinutes }
}

/** "Tue, Thu"; the whole week reads "every day". */
export function formatWeekdays(days: readonly Weekday[]): string {
  if (days.length === 7) return 'every day'
  return days.map(d => d.charAt(0) + d.slice(1).toLowerCase()).join(', ')
}

/** "10:00 PM CDT" for a UTC minute-of-day, on `ref`'s date in `zone`. */
export function formatZonedClock(utcMinutes: number, zone: string, ref: Date = new Date()): string {
  const instant = new Date(Date.UTC(ref.getUTCFullYear(), ref.getUTCMonth(), ref.getUTCDate()) + utcMinutes * 60_000)
  return new Intl.DateTimeFormat('en-US', {
    timeZone: zone, hour: 'numeric', minute: '2-digit', hour12: true, timeZoneName: 'short',
  }).format(instant)
}

/** The gold clock treatment (owner, 2026-09-23): the viewer's zone first, UTC
 *  beside it - "1:30 AM CDT · 06:30 UTC". A viewer already on UTC sees the UTC
 *  value once. */
export function formatGoldClockLabel(utcMinutes: number, zone: string = browserTimeZone(), ref: Date = new Date()): string {
  const utc = `${formatMinutes(utcMinutes)} UTC`
  return isUtcZone(zone) ? utc : `${formatZonedClock(utcMinutes, zone, ref)} · ${utc}`
}
