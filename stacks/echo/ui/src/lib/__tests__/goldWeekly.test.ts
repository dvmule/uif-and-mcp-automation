import { describe, it, expect } from 'vitest'
import {
  WEEKDAYS,
  formatGoldClockLabel,
  formatWeekdays,
  localWeeklySchedule,
  parseWeeklyDays,
  utcToZoned,
  utcWeeklySchedule,
  zonedToUtc,
  type Weekday,
} from '../goldWeekly'

// 2026-09-23 is a Wednesday. Chicago: CDT (UTC-5) then, CST (UTC-6) on 2026-01-15.
const SEP = new Date('2026-09-23T12:00:00Z')
const JAN = new Date('2026-01-15T12:00:00Z')
const CHI = 'America/Chicago'
const IND = 'Asia/Kolkata'   // UTC+5:30, no DST

describe('parseWeeklyDays (mirror of gold_frequencies.parse_weekly_days)', () => {
  it('normalises case, drops duplicates, orders Mon..Sun', () => {
    expect(parseWeeklyDays(['thu', 'THU', 'tue'])).toEqual(['TUE', 'THU'])
  })
  it('is null for anything unusable, including the empty set', () => {
    for (const bad of [[], ['FUNDAY'], 'TUE', ['TUE', 3], null, undefined, 5]) {
      expect(parseWeeklyDays(bad)).toBeNull()
    }
  })
})

describe('utcToZoned / zonedToUtc move the day WITH the time', () => {
  it('Wednesday 03:00 UTC is Tuesday 22:00 in Chicago on CDT, 21:00 on CST', () => {
    expect(utcToZoned('WED', 180, CHI, SEP)).toEqual({ day: 'TUE', minutes: 22 * 60 })
    expect(utcToZoned('WED', 180, CHI, JAN)).toEqual({ day: 'TUE', minutes: 21 * 60 })
  })
  it('Tuesday 22:00 in Chicago is Wednesday 03:00 UTC on CDT, 04:00 on CST', () => {
    expect(zonedToUtc('TUE', 22 * 60, CHI, SEP)).toEqual({ day: 'WED', minutes: 180 })
    expect(zonedToUtc('TUE', 22 * 60, CHI, JAN)).toEqual({ day: 'WED', minutes: 240 })
  })
  it('handles a half-hour zone and wraps across the week boundary', () => {
    expect(utcToZoned('MON', 390, IND, SEP)).toEqual({ day: 'MON', minutes: 12 * 60 })
    expect(zonedToUtc('MON', 12 * 60, IND, SEP)).toEqual({ day: 'MON', minutes: 390 })
    // Sunday 23:00 UTC is already Monday 04:30 in India
    expect(utcToZoned('SUN', 23 * 60, IND, SEP)).toEqual({ day: 'MON', minutes: 270 })
    // and Monday 02:00 in India is still Sunday 20:30 UTC
    expect(zonedToUtc('MON', 120, IND, SEP)).toEqual({ day: 'SUN', minutes: 20 * 60 + 30 })
  })
  it('is the identity for a UTC viewer', () => {
    for (const d of WEEKDAYS) {
      expect(utcToZoned(d, 1439, 'UTC', SEP)).toEqual({ day: d, minutes: 1439 })
      expect(zonedToUtc(d, 0, 'UTC', SEP)).toEqual({ day: d, minutes: 0 })
    }
  })
  it('round-trips every day at both DST states', () => {
    for (const ref of [SEP, JAN]) {
      for (const d of WEEKDAYS) {
        for (const m of [0, 180, 12 * 60, 23 * 60 + 55]) {
          const local = utcToZoned(d, m, CHI, ref)
          expect(zonedToUtc(local.day, local.minutes, CHI, ref)).toEqual({ day: d, minutes: m })
        }
      }
    }
  })
})

describe('whole-schedule conversion', () => {
  it('stored Wed/Fri 03:00 UTC reads as Tue/Thu 22:00 in Chicago, and back', () => {
    expect(localWeeklySchedule(['WED', 'FRI'], 180, CHI, SEP)).toEqual({ days: ['TUE', 'THU'], minutes: 22 * 60 })
    expect(utcWeeklySchedule(['TUE', 'THU'], 22 * 60, CHI, SEP)).toEqual({ days: ['WED', 'FRI'], minutes: 180 })
  })
  it('keeps ISO order and dedupes after the flip (Sat + Sun evening both land on Sunday/Monday UTC)', () => {
    const days: Weekday[] = ['SAT', 'SUN']
    // Sat 22:00 CDT -> Sun 03:00 UTC, Sun 22:00 CDT -> Mon 03:00 UTC; ISO order puts Monday first
    expect(utcWeeklySchedule(days, 22 * 60, CHI, SEP)).toEqual({ days: ['MON', 'SUN'], minutes: 180 })
  })
})

describe('labels', () => {
  it('formats the viewer clock first and UTC beside it; UTC viewers see UTC once', () => {
    expect(formatGoldClockLabel(390, 'UTC')).toBe('06:30 UTC')
    expect(formatGoldClockLabel(390, CHI, SEP)).toBe('1:30 AM CDT · 06:30 UTC')
    expect(formatGoldClockLabel(390, CHI, JAN)).toBe('12:30 AM CST · 06:30 UTC')
  })
  it('names days as Tue, Thu and the whole week as every day', () => {
    expect(formatWeekdays(['TUE', 'THU'])).toBe('Tue, Thu')
    expect(formatWeekdays([...WEEKDAYS])).toBe('every day')
  })
})
